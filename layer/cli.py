"""The operator surface for phases 1 and 2.

Every command takes `--org` and `--as`. The first is the tenant, because there is no session
to infer it from and RLS needs it named; the second is attribution, recorded on every write.
Neither is authentication — see `bindings.decide` — and both become a real session in phase 4.

The commands follow PRD B2's seven steps in order, and the gate at step 5 is a real stop: a
`--yes-to-all` flag does not exist, because the review it would skip is the product.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

from sqlalchemy import select

from layer.core import actors, audit, freshness
from layer.core.db import org_session, unscoped_session
from layer.core.durations import format_duration, parse_duration
from layer.core.errors import LayerError, Unreadable
from layer.db import models
from layer.db.models import ACTOR_ROLES, Actor, Org, Product
from layer.answers import decisions
from layer.findings import queries
from layer.onboarding import bindings as binding_gate
from layer.onboarding import run, state


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2
    try:
        return args.handler(args)
    except LayerError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    except state.StepNotReady as exc:
        print(f"not ready: {exc}", file=sys.stderr)
        return 1


# -- commands --------------------------------------------------------------------


def _org_create(args) -> int:
    with unscoped_session() as session:
        org = Org(slug=args.slug, name=args.name or args.slug.title())
        session.add(org)
        session.flush()
        print(f"{org.slug}  {org.id}")
    return 0


def _org_list(args) -> int:
    with unscoped_session() as session:
        for org in session.execute(select(Org).order_by(Org.slug)).scalars():
            print(f"{org.slug:24} {org.id}")
    return 0


def _actor_add(args) -> int:
    with org_session(args.org) as session:
        row = actors.add(
            session, org_id=uuid.UUID(args.org), email=args.email,
            role=args.role, name=args.name,
        )
        audit.record(
            session, org_id=row.org_id, actor=args.actor,
            action=audit.ACTOR_REGISTERED, subject=f"actor:{row.email}",
            detail={"role": row.role},
        )
        print(f"{row.email}: {row.role}")
    return 0


def _actor_list(args) -> int:
    with org_session(args.org) as session:
        rows = session.execute(select(Actor).order_by(Actor.email)).scalars().all()
        if not rows:
            print("no actors registered. A decision cannot be attributed until one is.")
            return 0
        for row in rows:
            decides = ", ".join(models.ROLE_DECIDES[row.role]) or "nothing"
            print(f"{row.email:<40} {row.role:<10} decides: {decides}")
    return 0


def _register(args) -> int:
    with org_session(args.org) as session:
        product = state.register(
            session, org_id=uuid.UUID(args.org), key=args.key, name=args.name or args.key,
            pattern=args.pattern, ref_prefix=args.ref_prefix, actor=args.actor,
        )
        print(f"{product.key}: {product.status} (step {product.onboarding_step})")
        if args.pattern:
            print(f"  pattern {args.pattern!r} recorded as a hint; no defaults applied")
    return 0


def _bind(args) -> int:
    config = json.loads(args.config)
    try:
        window = parse_duration(args.freshness) if args.freshness else None
    except ValueError as exc:
        # A refusal the operator reads and corrects, not a traceback.
        raise Unreadable(str(exc)) from exc
    with org_session(args.org) as session:
        product = state.get(session, key=args.product)
        source = state.bind_source(
            session, product=product, role=args.role, kind=args.kind,
            config=config, freshness_window=window, actor=args.actor,
        )
        print(f"bound {args.role}/{args.kind} to {product.key}  ({source.id})")
        if window:
            print(f"  freshness window {format_duration(window)}; outside it, any "
                  f"verdict resting on this source degrades to cannot_confirm")
        else:
            # Said out loud rather than left as a blank field. A source with no stated
            # cadence is not a fresh one, and a reader has to know the difference.
            print("  no freshness window stated, so this source's age is reported "
                  "and never used to degrade a verdict")
        print(f"  {product.key}: {product.status}")
    return 0


def _import_spec(args) -> int:
    with org_session(args.org) as session:
        product = state.get(session, key=args.product)
        result = run.import_spec(session, product=product, actor=args.actor)
        print(f"spec: {result.detail}")
        _print_report(result.report)
    return 0


def _backfill(args) -> int:
    with org_session(args.org) as session:
        product = state.get(session, key=args.product)
        result = run.backfill(session, product=product, actor=args.actor)
        print(f"eval: {result.detail}")
        _print_report(result.report)
    return 0


def _scan(args) -> int:
    with org_session(args.org) as session:
        product = state.get(session, key=args.product)
        result = run.scan_enforcement(session, product=product, actor=args.actor)
        print(f"code: {result.detail}")
        _print_report(result.report)
    return 0


def _bindings_list(args) -> int:
    with org_session(args.org) as session:
        product = state.get(session, key=args.product)
        proposal = binding_gate.propose(session, product=product)

        pending = proposal.pending
        print(f"{len(pending)} candidate binding(s) awaiting a decision:")
        for candidate in pending:
            print(
                f"  {candidate.metric:28} -> {candidate.clause_ref:12} "
                f"{candidate.clause_bar or '':12} "
                f"({candidate.observations} obs, latest "
                f"{'n/a' if candidate.latest_value is None else f'{candidate.latest_value:g}'}, "
                f"matched by {candidate.matched_by})"
            )
            print(f"      {candidate.clause_statement[:88]}")
        if proposal.clauses_without_measurement:
            print("\nclauses with a bar that nothing measures (an uncovered finding):")
            print("  " + ", ".join(proposal.clauses_without_measurement))
        if proposal.metrics_without_clause:
            print("\nmetrics measured that no clause states (H16, the gap is the clause):")
            print("  " + ", ".join(proposal.metrics_without_clause))
    return 0


def _bindings_decide(args, decision: str) -> int:
    with org_session(args.org) as session:
        product = state.get(session, key=args.product)
        binding_gate.decide(
            session, product=product, metric=args.metric, clause_ref=args.clause,
            decision=decision, by=args.actor, note=args.note,
            metric_definition=json.loads(args.definition) if args.definition else None,
        )
        status = state.refresh(session, product=product, actor=args.actor)
        print(f"{decision}: {args.metric} -> {args.clause}  (by {args.actor})")
        print(f"  {product.key}: {status}")
    return 0


def _measure(args) -> int:
    with org_session(args.org) as session:
        product = state.get(session, key=args.product)
        counts = state.measure(session, product=product, actor=args.actor)
        print(f"{product.key}: {product.status}")
        for verdict, count in sorted(counts.items()):
            print(f"  {verdict:16} {count}")
        # Said here as well as on `status`, because this is the command that just moved
        # those verdicts and an operator reading a wall of cannot_confirm is owed the
        # reason in the same output (AC-29, AC-30).
        for entry in freshness.stale_sources(session, product=product):
            print(f"  OVERDUE          {entry['note']}")
    return 0


def _findings(args) -> int:
    with org_session(args.org) as session:
        product = state.get(session, key=args.product)
        found = queries.find_all(session, product=product)

        wanted = [args.kind] if args.kind else list(found)
        for kind in wanted:
            result = found.get(kind)
            if result is None:
                print(f"no such finding kind: {kind}")
                continue
            if result.refusal is not None:
                # A refusal is printed as prominently as a finding. An empty section would
                # read as "nothing to see here", which is the one thing it does not mean.
                print(f"\n{kind}: cannot answer")
                print(f"  {result.refusal.reason}")
                for missing in result.refusal.missing:
                    print(f"  needs: {missing}")
                continue

            if result.stale_sources:
                # Before the findings, not after. PRD B1 caps confidence at medium while
                # anything is overdue, and a caveat printed under the answer it qualifies
                # is read second or not at all.
                for entry in result.stale_sources:
                    print(f"\n{kind}: STALE — {entry['note']}")
            print(f"\n{kind}: {len(result)} finding(s)")
            for finding in result.findings:
                marker = "now" if finding.current else "historical"
                if finding.stale:
                    marker += ", stale"
                print(f"  [{finding.clause_ref or '-'}] {marker}")
                print(f"    {finding.summary}")
                if args.citations:
                    for link in finding.evidence_links:
                        print(f"      {link['id']:34} {link['url']}")
                if finding.unresolved:
                    # Displayed, never hidden (PRD B1).
                    print(f"      UNRESOLVED: {', '.join(finding.unresolved)}")
    return 0


def _proposals_list(args) -> int:
    with org_session(args.org) as session:
        product = state.get(session, key=args.product) if args.product else None
        rows = decisions.queue(session, product=product)
        if not rows:
            print("no open proposals.")
        for row in rows:
            print(f"{row['id']}  {row['kind']}  {row['target']}.{row['field']}")
            old_value = "—" if row["old_value"] is None else row["old_value"]
            print(f"    {old_value}  ->  {row['new_value']}")
            print(f"    {row['reason']}")
            print(f"    if rejected: {row['if_rejected']}")
            print(f"    evidence: {', '.join(row['evidence']) or 'none'}")
            # A proposal no critic has scored is unscored, never zero. Until the critic
            # exists every one of these is null, and rendering that as 0.0 would read as
            # a confident judgement that the proposal is worthless.
            score = "unscored" if row["confidence"] is None else f"{row['confidence']:.2f}"
            print(f"    confidence: {score}    proposed by {row['proposed_by']}")
            print()
        _print_acceptance(decisions.acceptance(session, product=product))
    return 0


def _proposal_decide(args, decision: str) -> int:
    with org_session(args.org) as session:
        if decision == "accept":
            result = decisions.accept(
                session, proposal_id=args.id, by=args.actor, note=args.note
            )
        else:
            result = decisions.reject(
                session, proposal_id=args.id, by=args.actor, reason=args.reason
            )
        if result["shape"] == "refusal":
            print(f"refused: {result['reason']}", file=sys.stderr)
            return 1
        print(f"{result['decision']}: {result['proposal']['target']}"
              f".{result['proposal']['field']}  (by {result['decided_by']})")
        applied = result.get("applied") or {}
        if applied.get("version"):
            print(f"  {applied['clause_ref']} is now at version {applied['version']}")
        if applied.get("link"):
            print(f"  {applied['link']}")
        if applied.get("pending"):
            print(f"  not done: {applied['pending']}")
    return 0


def _acceptance(args) -> int:
    with org_session(args.org) as session:
        product = state.get(session, key=args.product) if args.product else None
        _print_acceptance(decisions.acceptance(session, product=product))
    return 0


def _print_acceptance(report: dict) -> None:
    """The one number B6 calls the core product health metric, with its band in view.

    Printed with the band rather than alone, because a bare percentage invites the reader
    to treat higher as better — and above 85% is a failure, not a win.
    """
    low, high = report["band"]
    rate = "unmeasured" if report["rate"] is None else f"{report['rate'] * 100:.0f}%"
    print(f"acceptance: {rate}  (band {low:.0%} to {high:.0%})  [{report['verdict']}]")
    print(f"  {report['summary']}")
    if report["invalidated"] or report["evidence_expired"]:
        print(f"  excluded from the rate: {report['invalidated']} invalidated, "
              f"{report['evidence_expired']} evidence-expired — neither was decided "
              f"by anyone.")


def _serve(args) -> int:
    """Serve the MCP tool surface over stdio.

    Nothing is printed on success: stdout is the protocol's own channel, and a banner
    written to it is a parse error at the other end.
    """
    from layer.mcp import server as mcp_server  # noqa: PLC0415

    # `--as` is the same argument every other command takes, and it means the same
    # thing here: who the writes are attributed to. Identity, not authentication — the
    # server cannot verify the claim and does not pretend to (phase 4's job).
    mcp_server.run(org_id=args.org, transport=args.transport, actor=args.actor)
    return 0


def _status(args) -> int:
    with org_session(args.org) as session:
        product = state.get(session, key=args.product)
        print(state.status(session, product=product).describe())
    return 0


def _products(args) -> int:
    with org_session(args.org) as session:
        rows = session.execute(select(Product).order_by(Product.key)).scalars().all()
        if not rows:
            # AC-20: a clean install says what is missing rather than printing nothing.
            print("no product is onboarded in this org. Nothing can be found or proposed "
                  "until one is registered and its sources bound.")
            return 0
        for product in rows:
            print(f"{product.key:20} {product.status:22} step {product.onboarding_step}")
    return 0


def _print_report(report) -> None:
    print(f"  {report.summary()}")
    for note in report.notes:
        print(f"  note: {note}")
    for failure in report.failed[:10]:
        print(f"  FAILED {failure.identifier}: {failure.reason} — {failure.note or ''}")


# -- parser ----------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="layer", description="The AI product operating layer."
    )
    sub = parser.add_subparsers(dest="command")

    def with_common(p, *, product=True):
        p.add_argument("--org", required=True, help="tenant id")
        p.add_argument("--as", dest="actor", required=True,
                       help="who is acting; recorded on every write")
        if product:
            p.add_argument("product", help="product key")
        return p

    org = sub.add_parser("org", help="tenants").add_subparsers(dest="org_command")
    create = org.add_parser("create")
    create.add_argument("slug")
    create.add_argument("--name")
    create.set_defaults(handler=_org_create)
    org.add_parser("list").set_defaults(handler=_org_list)

    actor = sub.add_parser("actor", help="who may decide").add_subparsers(dest="actor_command")
    add_actor = with_common(actor.add_parser("add"), product=False)
    add_actor.add_argument("email")
    add_actor.add_argument("--role", required=True, choices=list(ACTOR_ROLES),
                           help="pm decides everything; engineer decides ci_change and "
                                "eval_case; agent decides nothing")
    add_actor.add_argument("--name")
    add_actor.set_defaults(handler=_actor_add)
    with_common(actor.add_parser("list"), product=False).set_defaults(handler=_actor_list)

    product = sub.add_parser("product", help="step 1").add_subparsers(dest="product_command")
    register = with_common(product.add_parser("register"), product=False)
    register.add_argument("key")
    register.add_argument("--name")
    register.add_argument("--pattern", help="a hint only; never a constraint")
    register.add_argument("--ref-prefix")
    register.set_defaults(handler=_register)
    with_common(product.add_parser("list"), product=False).set_defaults(handler=_products)

    source = sub.add_parser("source", help="step 2").add_subparsers(dest="source_command")
    bind = with_common(source.add_parser("bind"))
    bind.add_argument("--role", required=True,
                      choices=["spec", "eval", "code", "ticket", "production", "decision"])
    bind.add_argument("--kind", required=True)
    bind.add_argument("--config", required=True, help="JSON; the product-specific shape")
    bind.add_argument("--freshness", metavar="30d",
                      help="how long a measurement from this source stays usable "
                           "(90m, 12h, 30d, 2w). Omitted means no stated cadence")
    bind.set_defaults(handler=_bind)

    with_common(sub.add_parser("spec", help="step 3: import")).set_defaults(handler=_import_spec)
    with_common(sub.add_parser("backfill", help="step 4: observations")).set_defaults(handler=_backfill)
    with_common(sub.add_parser("scan", help="step 4b: enforcement")).set_defaults(handler=_scan)

    bindings = sub.add_parser("bindings", help="step 5: the gate").add_subparsers(dest="bindings_command")
    with_common(bindings.add_parser("list")).set_defaults(handler=_bindings_list)
    for name, decision in (("confirm", "confirmed"), ("reject", "rejected")):
        p = with_common(bindings.add_parser(name))
        p.add_argument("--metric", required=True)
        p.add_argument("--clause", required=True)
        p.add_argument("--note")
        p.add_argument("--definition", help="JSON metric definition, confirmed with it")
        p.set_defaults(handler=lambda a, d=decision: _bindings_decide(a, d))

    with_common(sub.add_parser("measure", help="step 6: verdicts")).set_defaults(handler=_measure)

    findings = with_common(sub.add_parser("findings", help="step 7: what the record says"))
    findings.add_argument("--kind", choices=[
        "drift", "unenforced", "uncovered", "stalled_decision", "underspecified",
    ])
    findings.add_argument("--citations", action="store_true",
                          help="print the resolved url for every piece of evidence")
    findings.set_defaults(handler=_findings)
    proposals = sub.add_parser(
        "proposals", help="the write half: what is waiting on a human"
    ).add_subparsers(dest="proposals_command")
    listing = with_common(proposals.add_parser("list"), product=False)
    listing.add_argument("--product", help="one product, or every product in the org")
    listing.set_defaults(handler=_proposals_list)

    accept = with_common(proposals.add_parser("accept"), product=False)
    accept.add_argument("id")
    accept.add_argument("--note")
    accept.set_defaults(handler=lambda a: _proposal_decide(a, "accept"))

    reject = with_common(proposals.add_parser("reject"), product=False)
    reject.add_argument("id")
    reject.add_argument("--reason", required=True,
                        help="required: rejecting is a decision, not a deferral")
    reject.set_defaults(handler=lambda a: _proposal_decide(a, "reject"))

    rate = with_common(proposals.add_parser("acceptance"), product=False)
    rate.add_argument("--product")
    rate.set_defaults(handler=_acceptance)

    with_common(sub.add_parser("status")).set_defaults(handler=_status)

    serve = with_common(sub.add_parser("serve", help="step 7: the MCP server"), product=False)
    serve.add_argument("--transport", default="stdio",
                       choices=["stdio", "sse", "streamable-http"],
                       help="stdio is the only one phases 1 to 3 use; the others arrive "
                            "with phase 4 and change nothing else")
    serve.set_defaults(handler=_serve)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
