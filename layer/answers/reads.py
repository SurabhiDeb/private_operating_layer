"""The read tier of PRD B11, as functions over a session.

Every one returns an `Answer`, a `FindingSet` or a `Refusal` — B1's shapes, never rows
(B11 rule 1). None of them takes an `org_id`: the tenant comes from the session, which is
where RLS enforces it, and a tool that accepted one from its caller would be a way across
the boundary (B11 rule 4, AC-32).

**Nothing here knows what MCP is.** The transport wraps these; it does not contain them.
That is what makes PRD D2's claim about phase 4 true — that it "adds no capability (one
`run(transport=...)` argument)" — and it is why the same functions already serve the CLI.

**A refusal is the answer to a question the store cannot support.** AC-20: a clean install
answers every read tool with a refusal naming what is missing. So an unknown product, an
unbound source role and a clause nobody has is each a stated refusal rather than an empty
list, which would read as "all clear".
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from layer.answers.shapes import CANNOT_DETERMINE, HIGH, MEDIUM, Answer
from layer.core import freshness
from layer.core.durations import approximate_duration, format_duration, parse_duration
from layer.core.errors import Refusal, Unreadable
from layer.db.models import Clause, Link, Observation, Product, Source
from layer.refs.registry import Registry
from layer.findings import queries
from layer.findings.citations import registry_for
from layer.onboarding import bindings as binding_gate
from layer.onboarding import state

#: How far `trace_chain` walks. AC-6 asks for a six-link chain end to end, so the cap is
#: above it rather than at it: a chain that stopped exactly at the criterion's length
#: would answer AC-6 and silently truncate anything longer.
MAX_CHAIN_DEPTH = 10

#: The chain the Layer expects to find, as data. US-5 requires an absent link to be named
#: rather than omitted, which is only possible against a stated expectation. Link types,
#: not product concepts — no product name or metric name appears here (rule R1).
EXPECTED_CHAIN = ("governs", "sets", "implements", "asserts", "enforces", "observes")


# -- products and sources --------------------------------------------------------


def list_products(session: Session) -> Answer | Refusal:
    """What is onboarded at all, with status."""
    rows = session.execute(select(Product).order_by(Product.key)).scalars().all()
    if not rows:
        # AC-20. The one answer a bare install must give, and the reason it is a refusal
        # rather than an empty list: "no products" and "nothing wrong" look identical in
        # a list and lead a reader to opposite conclusions.
        return Refusal(
            reason=(
                "no product is onboarded in this org, so there is nothing to report on. "
                "The Layer knows nothing until a product is registered and its sources "
                "are bound."
            ),
            missing=["a registered product", "a spec source", "an eval source"],
        )
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
    # No citations: a product key is an identifier, not a record with a URL behind it.
    # Listing it as a citation would guarantee a non-empty `unresolved` on every call and
    # train a reader to ignore the field that exists to be read (B1).
    return Answer(
        statement=(
            f"{len(rows)} product(s) onboarded: "
            + ", ".join(f"{r.key} ({r.status})" for r in rows)
            + "."
        ),
        detail={
            "products": [
                {
                    "key": r.key, "name": r.name, "status": r.status,
                    "pattern": r.pattern, "step": r.onboarding_step,
                }
                for r in rows
            ],
            "by_status": by_status,
        },
    )


def get_product(session: Session, key: str) -> Answer | Refusal:
    """Sources, their status and staleness."""
    product = _product(session, key)
    if isinstance(product, Refusal):
        return product
    status = state.status(session, product=product)
    sources = freshness.windows_for(session, product.id)
    now = freshness.clock(session)
    return Answer(
        statement=(
            f"{product.key} is {product.status} at step {status.step}, with "
            f"{status.clauses} clause(s), {status.observations} observation(s) and "
            f"{status.confirmed_bindings} confirmed binding(s). "
            f"Verdicts: "
            + (", ".join(f"{k} {v}" for k, v in sorted(status.verdicts.items())) or "none")
            + "."
        ),
        stale_sources=status.stale_sources,
        detail={
            "key": product.key,
            "status": product.status,
            "step": status.step,
            "clauses": status.clauses,
            "measurable_clauses": status.measurable_clauses,
            "observations": status.observations,
            "confirmed_bindings": status.confirmed_bindings,
            "pending_candidates": status.pending_candidates,
            "verdicts": status.verdicts,
            "sources": [_source_dict(w, now) for w in sources.values()],
        },
    )


def source_status(session: Session, key: str) -> Answer | Refusal:
    """`last_sync_at` and `overdue_since`, per source."""
    product = _product(session, key)
    if isinstance(product, Refusal):
        return product
    windows = list(freshness.windows_for(session, product.id).values())
    if not windows:
        return Refusal(
            reason=f"{product.key} has no source bound, so there is no sync to report.",
            missing=["a spec source", "an eval source"],
        )
    now = freshness.clock(session)
    stale = freshness.stale_sources(session, product=product, now=now)
    return Answer(
        statement=(
            f"{product.key} has {len(windows)} source(s), "
            f"{len(stale)} of them past a stated window. "
            + " ".join(w.describe(now) + "." for w in sorted(windows, key=lambda w: w.role))
        ),
        stale_sources=stale,
        detail={"sources": [_source_dict(w, now) for w in windows]},
    )


def _source_dict(window: freshness.SourceWindow, now) -> dict:
    return {
        "source_id": str(window.source_id),
        "role": window.role,
        "kind": window.kind,
        "status": window.status,
        "last_sync_at": window.last_sync_at.isoformat() if window.last_sync_at else None,
        "freshness_window": format_duration(window.window) if window.window else None,
        "overdue_since": (
            window.overdue_since(now).isoformat() if window.overdue_since(now) else None
        ),
        "note": window.describe(now),
    }


# -- clauses ---------------------------------------------------------------------


def list_clauses(
    session: Session,
    key: str,
    *,
    failing: bool | None = None,
    clause_state: str | None = None,
    verdict: str | None = None,
) -> Answer | Refusal:
    """Every active clause, narrowed by the filters B11 names.

    `failing` means `verdict = missed`. Deliberately not "missed or cannot_confirm": a
    clause nobody can confirm is not a clause that failed, and collapsing them would
    report an evidence problem as a product problem.
    """
    product = _product(session, key)
    if isinstance(product, Refusal):
        return product
    statement = select(Clause).where(
        Clause.product_id == product.id, Clause.status == "active"
    )
    if failing is True:
        statement = statement.where(Clause.verdict == "missed")
    if failing is False:
        statement = statement.where(Clause.verdict != "missed")
    if clause_state:
        statement = statement.where(Clause.state == clause_state)
    if verdict:
        statement = statement.where(Clause.verdict == verdict)
    rows = session.execute(statement.order_by(Clause.ref)).scalars().all()

    if not rows:
        return Refusal(
            reason=(
                f"{product.key} has no active clause matching that filter. "
                f"An empty list here would not say whether the filter excluded "
                f"everything or the product has no clauses at all."
            ),
            missing=["a clause matching the filter"],
        )
    stale = freshness.stale_sources(session, product=product)
    return _resolved(session, product, Answer(
        statement=(
            f"{len(rows)} clause(s) in {product.key}"
            + (f" with verdict {verdict}" if verdict else "")
            + (" currently missed" if failing is True else "")
            + ": "
            + ", ".join(f"{r.ref} {r.verdict}" for r in rows[:12])
            + ("…" if len(rows) > 12 else "")
            + "."
        ),
        citations=[f"clause:{r.ref}" for r in rows[:12]],
        stale_sources=stale,
        detail={
            "clauses": [
                {
                    "ref": r.ref, "kind": r.kind, "label": r.label,
                    "statement": r.statement, "state": r.state, "verdict": r.verdict,
                    "bar": _bar(r), "unit": r.unit, "version": r.version,
                }
                for r in rows
            ],
            "count": len(rows),
        },
    ))


def get_clause(session: Session, ref: str) -> Answer | Refusal:
    """Statement, threshold, latest value, links, history and `as_of`."""
    clause = session.execute(
        select(Clause).where(Clause.ref == ref, Clause.status == "active")
    ).scalars().first()
    if clause is None:
        return Refusal(
            reason=(
                f"no active clause is named {ref} in this org. A ref is scoped to an "
                f"org and to a product, so the same ref may exist elsewhere (H11)."
            ),
            missing=[f"clause:{ref}"],
        )
    product = session.get(Product, clause.product_id)
    metric = {ref_: m for m, ref_ in
              binding_gate.confirmed_metrics(session, product=product).items()}.get(ref)
    series = _series(session, product, metric) if metric else []
    latest = series[-1] if series else None
    staleness = _staleness(session, product, latest)
    links = _links_touching(session, product, f"clause:{ref}")

    caveats: list[str] = []
    confidence = HIGH
    if metric is None:
        confidence = CANNOT_DETERMINE
        caveats.append(
            "no confirmed binding pairs a measurement with this clause, so the Layer "
            "does not know which number answers it"
        )
    elif latest is None:
        confidence = CANNOT_DETERMINE
        caveats.append("nothing in the connected sources carries a value for this metric")
    return _resolved(session, product, Answer(
        statement=(
            f"{ref} {clause.label or ''} states {_bar(clause)}. "
            f"It is {clause.state} and reads {clause.verdict}"
            + (
                f", latest {latest.value:g} in run {latest.run_id or 'unknown'}"
                if latest else ", with no measurement"
            )
            + f". {len(links)} link(s) touch it."
        ).replace("  ", " "),
        citations=[f"clause:{ref}"] + ([f"obs:{latest.id}"] if latest else []),
        confidence=confidence,
        caveats=caveats,
        stale_sources=freshness.stale_sources(session, product=product),
        detail={
            "ref": ref,
            "product": product.key,
            "statement": clause.statement,
            "rationale": clause.rationale,
            "kind": clause.kind,
            "bar": _bar(clause),
            "comparator": clause.comparator,
            "value": clause.value,
            "value_high": clause.value_high,
            "unit": clause.unit,
            "direction": clause.direction,
            "state": clause.state,
            "verdict": clause.verdict,
            "version": clause.version,
            "metric": metric,
            "latest_value": latest.value if latest else None,
            "as_of": latest.measured_at.isoformat() if latest else None,
            "stale": bool(staleness and staleness.stale),
            "staleness": staleness.as_dict() if staleness else None,
            "history": _mark_changes([_observation_dict(o) for o in series[-12:]]),
            "links": links,
        },
    ))


# -- measurements ----------------------------------------------------------------


def metric_history(
    session: Session, clause_ref: str, window: str | None = None
) -> Answer | Refusal:
    """The full series behind a clause, newest last."""
    found = _clause_and_metric(session, clause_ref)
    if isinstance(found, Refusal):
        return found
    clause, product, metric = found
    series = _series(session, product, metric, window=window)
    if not series:
        return Refusal(
            reason=(
                f"{clause_ref} is bound to {metric} and no run "
                + (f"inside {window} " if window else "")
                + "carries a value for it."
            ),
            missing=[f"an observation of {metric}"],
        )
    staleness = _staleness(session, product, series[-1])
    first, last = series[0], series[-1]
    history = _mark_changes([_observation_dict(o) for o in series])
    unexplained = _unexplained_steps(history)
    return _resolved(session, product, Answer(
        statement=(
            f"{metric} has {len(series)} observation(s)"
            + (f" inside {window}" if window else "")
            + f", from {first.value:g} on {first.measured_at.date()} to {last.value:g} "
            f"on {last.measured_at.date()}, against {_bar(clause)}."
            + (
                f" {len(unexplained)} group(s) of runs record identical inputs and "
                f"differ in value, so versioning does not explain the movement."
                if unexplained else ""
            )
        ),
        citations=[f"clause:{clause_ref}"] + [f"obs:{o.id}" for o in series[-12:]],
        stale_sources=freshness.stale_sources(session, product=product),
        detail={
            "clause_ref": clause_ref,
            "metric": metric,
            "bar": _bar(clause),
            "window": window,
            "count": len(series),
            "as_of": last.measured_at.isoformat(),
            "stale": bool(staleness and staleness.stale),
            "observations": history,
            "unexplained_steps": unexplained,
        },
    ))


def failing_cases(
    session: Session, clause_ref: str, window: str | None = None
) -> Answer | Refusal:
    """**The proof tool.** The individual cases that failed, with run and trace. AC-24.

    Answered from `case_result` alone. No log is read and no call is made to the eval
    platform, which is the whole of AC-24 — and it is why the rows are copied at
    observation time, since the platform will delete the trace bodies long before anyone
    asks this question.
    """
    found = _clause_and_metric(session, clause_ref)
    if isinstance(found, Refusal):
        return found
    clause, product, metric = found
    series = _series(session, product, metric, window=window)
    if not series:
        return Refusal(
            reason=(
                f"{clause_ref} is bound to {metric} and no run "
                + (f"inside {window} " if window else "")
                + "carries a value for it, so there are no cases to show."
            ),
            missing=[f"an observation of {metric}"],
        )

    breaching = [o for o in series if queries.violates(clause, o.value)]
    scope = breaching or series
    cases, total, state_ = queries.failing_cases_for(session, product, scope)
    staleness = _staleness(session, product, series[-1])

    caveats: list[str] = []
    confidence = HIGH
    if state_ == queries.NOT_APPLICABLE:
        confidence = CANNOT_DETERMINE
        caveats.append(
            "this source records no per-case rows beneath its numbers, so the runs are "
            "the finest evidence it has"
        )
    elif state_ == queries.NO_FAILING_CASES:
        confidence = MEDIUM
        caveats.append(
            "per-case rows are stored beneath these runs and none is recorded as a "
            "failure, so the number and the cases beneath it disagree about what to show"
        )
    return _resolved(session, product, Answer(
        statement=(
            f"{clause_ref} {_bar(clause)}: {len(breaching)} of {len(series)} run(s)"
            + (f" inside {window}" if window else "")
            + f" miss the bar, and {total} case(s) across them are recorded as failing"
            + (f", {len(cases)} cited here" if len(cases) < total else "")
            + "."
        ),
        citations=[f"clause:{clause_ref}"] + [c["ref"] for c in cases],
        confidence=confidence,
        caveats=caveats,
        stale_sources=freshness.stale_sources(session, product=product),
        detail={
            "clause_ref": clause_ref,
            "metric": metric,
            "window": window,
            "runs_total": len(series),
            "runs_missed": len(breaching),
            "failing_cases": cases,
            "failing_cases_total": total,
            "failing_cases_state": state_,
            "as_of": series[-1].measured_at.isoformat(),
            "stale": bool(staleness and staleness.stale),
        },
    ))


# -- across every product --------------------------------------------------------


def findings_across_products(session: Session, kind: str = "drift") -> Answer | Refusal:
    """US-11: "When asked without naming a product, the Layer shall answer across every
    product in the tenant", with every finding attributed to its product.

    One answer rather than a merged list of findings, because a merged list loses the
    thing US-11 is about: which product each one belongs to. The findings themselves
    already carry `product`, and they are returned beneath the statement rather than
    flattened into it.
    """
    from layer.findings import queries as q

    products = session.execute(select(Product).order_by(Product.key)).scalars().all()
    if not products:
        return Refusal(
            reason=(
                "no product is onboarded in this org, so there is nothing to answer "
                "across."
            ),
            missing=["a registered product"],
        )
    query = {
        "drift": q.find_drift,
        "unenforced": q.find_unenforced,
        "uncovered": q.find_uncovered,
    }.get(kind)
    if query is None:
        return Refusal(
            reason=f"{kind!r} is not a finding kind that can be asked across products.",
            missing=[],
        )

    per_product: dict[str, list[dict]] = {}
    stale: list[dict] = []
    total = 0
    for product in products:
        result = query(session, product=product)
        per_product[product.key] = [f.as_dict() for f in result]
        total += len(result)
        stale.extend(result.stale_sources)
    return Answer(
        statement=(
            f"{total} {kind} finding(s) across {len(products)} product(s): "
            + ", ".join(f"{key} {len(v)}" for key, v in per_product.items())
            + "."
        ),
        stale_sources=stale,
        detail={"kind": kind, "by_product": per_product, "count": total},
    )


# -- the chain -------------------------------------------------------------------


def trace_chain(session: Session, entity: str, depth: int = MAX_CHAIN_DEPTH) -> Answer | Refusal:
    """Walk `link` in both directions from one ref, naming the gaps. US-5, AC-6.

    Recursive in SQL rather than in Python, so one round trip walks the whole chain and
    a cycle cannot loop forever: the path is carried and a ref already on it is not
    followed again. `link` is tenant-scoped by RLS like every other table, so the walk
    cannot cross an org boundary however deep it goes.
    """
    if not entity or ":" not in entity:
        raise Unreadable(
            f"{entity!r} is not a ref. A ref is `kind:id`, such as `clause:ABC-1.2`."
        )
    depth = max(1, min(int(depth), MAX_CHAIN_DEPTH))
    rows = session.execute(
        text(
            """
            WITH RECURSIVE walk(from_ref, to_ref, link_type, created_by, depth, path) AS (
                SELECT l.from_ref, l.to_ref, l.link_type, l.created_by, 1,
                       -- Cast required: `from_ref` is varchar(255) and the recursive
                       -- term appends an unsized varchar, which Postgres refuses to
                       -- unify. Named here because the error message points at the
                       -- wrong half of the query.
                       ARRAY[l.from_ref, l.to_ref]::varchar[]
                  FROM link l
                 WHERE l.status = 'active' AND (l.from_ref = :ref OR l.to_ref = :ref)
                UNION ALL
                SELECT l.from_ref, l.to_ref, l.link_type, l.created_by, w.depth + 1,
                       w.path || CASE WHEN l.from_ref = ANY(w.path)
                                      THEN l.to_ref ELSE l.from_ref END
                  FROM link l
                  JOIN walk w
                    ON (l.from_ref = ANY(w.path) OR l.to_ref = ANY(w.path))
                 WHERE l.status = 'active'
                   AND w.depth < :depth
                   AND NOT (l.from_ref = ANY(w.path) AND l.to_ref = ANY(w.path))
            )
            SELECT DISTINCT from_ref, to_ref, link_type, created_by, min(depth) AS depth
              FROM walk
             GROUP BY from_ref, to_ref, link_type, created_by
             ORDER BY depth
            """
        ),
        {"ref": entity, "depth": depth},
    ).all()

    edges = [
        {
            "from": r.from_ref, "to": r.to_ref, "link_type": r.link_type,
            "source": r.created_by, "depth": r.depth,
        }
        for r in rows
    ]
    present = {e["link_type"] for e in edges}
    gaps = [t for t in EXPECTED_CHAIN if t not in present]
    nodes = sorted({e["from"] for e in edges} | {e["to"] for e in edges} | {entity})

    caveats: list[str] = []
    confidence = HIGH
    if gaps:
        # US-5: name the gap rather than omitting it. Most of these will be absent until
        # phase 6 binds the sources that fill them, and saying so is the behaviour the
        # story asks for rather than a shortfall to hide.
        confidence = MEDIUM if edges else CANNOT_DETERMINE
        caveats.append(
            "the expected chain is missing "
            + ", ".join(gaps)
            + ". No source bound to this product carries those links, so the chain is "
            "incomplete rather than broken"
        )
    return _resolved(session, _product_of(session, entity), Answer(
        statement=(
            f"{entity} reaches {len(nodes) - 1} other record(s) over {len(edges)} "
            f"link(s), to a depth of {max((e['depth'] for e in edges), default=0)}. "
            + (f"{len(gaps)} expected link type(s) are absent." if gaps else
               "Every expected link type is present.")
        ),
        # The entity first, then what it reaches. Deduplicated: `nodes` contains the
        # entity itself, and citing it twice made a chain of nothing report two
        # citations.
        citations=[entity] + [n for n in nodes if n != entity][:12],
        confidence=confidence,
        caveats=caveats,
        detail={
            "entity": entity,
            "nodes": nodes,
            "links": edges,
            "expected": list(EXPECTED_CHAIN),
            "gaps": gaps,
            "depth_reached": max((e["depth"] for e in edges), default=0),
            "depth_cap": depth,
        },
    ))


# -- the tool that needs a source nothing has bound ------------------------------


def blocked_tickets(session: Session, key: str) -> Answer | Refusal:
    """US-4. Refused by name, because no `ticket` source exists to read."""
    product = _product(session, key)
    if isinstance(product, Refusal):
        return product
    if not state.sources_by_role(session, product=product).get("ticket"):
        return Refusal(
            reason=(
                f"{product.key} has no ticket source bound. A blocked ticket is one "
                f"whose clause is failing, and without tickets neither half of that can "
                f"be seen. Answering 'none' would be a claim about tickets the Layer has "
                f"never read."
            ),
            missing=["a source with role 'ticket'", "a link from a ticket to a clause"],
        )
    # Unreachable until phase 6 binds one; left as a refusal rather than a guess at the
    # shape of a source nobody has described yet.
    return Refusal(
        reason=(
            f"{product.key} has a ticket source bound and the Layer has no adapter for "
            f"its kind yet, so it cannot say which tickets are blocked."
        ),
        missing=["a ticket adapter"],
    )


# -- helpers ---------------------------------------------------------------------


def _resolved(session: Session, product: Product | None, answer: Answer) -> Answer:
    """Attach the URLs before the answer leaves this module.

    Never left to the caller. Step 8 learned this on findings: a query used on its own
    returned `citation_links` empty and `unresolved` empty too, which reads as "every
    citation resolved" when in truth none had been tried — AC-7 and AC-14 broken by
    omission rather than by a wrong URL. The same trap is one line away here, so every
    `Answer` with citations goes through this.

    With no product there is no registry, and every citation lands in `unresolved`, which
    is the honest state rather than a silent empty pair.
    """
    if not answer.citations:
        return answer
    registry = registry_for(session, product=product) if product is not None else Registry()
    return answer.resolve(registry)


def _product(session: Session, key: str) -> Product | Refusal:
    row = session.execute(select(Product).where(Product.key == key)).scalars().first()
    if row is not None:
        return row
    known = session.execute(select(Product.key).order_by(Product.key)).scalars().all()
    return Refusal(
        reason=(
            f"no product named {key!r} is onboarded in this org. "
            + (f"Onboarded here: {', '.join(known)}." if known else
               "Nothing is onboarded here at all.")
        ),
        missing=[f"product:{key}"],
    )


def _product_of(session: Session, ref: str) -> Product | None:
    """The product a ref belongs to, where the ref names one the Layer stores.

    Only a clause ref can say, which is enough: a chain walked from a clause resolves its
    clause and observation nodes, and one walked from a ticket nobody has bound resolves
    nothing and says so in `unresolved`.
    """
    if not ref.startswith("clause:"):
        return None
    clause = session.execute(
        select(Clause).where(
            Clause.ref == ref.split(":", 1)[1], Clause.status == "active"
        )
    ).scalars().first()
    return None if clause is None else session.get(Product, clause.product_id)


def _clause_and_metric(
    session: Session, clause_ref: str
) -> tuple[Clause, Product, str] | Refusal:
    clause = session.execute(
        select(Clause).where(Clause.ref == clause_ref, Clause.status == "active")
    ).scalars().first()
    if clause is None:
        return Refusal(
            reason=f"no active clause is named {clause_ref} in this org.",
            missing=[f"clause:{clause_ref}"],
        )
    product = session.get(Product, clause.product_id)
    bound = {ref: m for m, ref in
             binding_gate.confirmed_metrics(session, product=product).items()}
    metric = bound.get(clause_ref)
    if metric is None:
        return Refusal(
            reason=(
                f"{clause_ref} has no confirmed binding, so the Layer does not know "
                f"which measurement answers it. A human confirms that pairing at step 5 "
                f"of onboarding, and nothing may assume it."
            ),
            missing=[f"a confirmed binding for clause:{clause_ref}"],
        )
    return clause, product, metric


def _series(
    session: Session, product: Product, metric: str, window: str | None = None
) -> list[Observation]:
    statement = select(Observation).where(
        Observation.product_id == product.id, Observation.metric == metric
    )
    if window:
        statement = statement.where(
            Observation.measured_at >= freshness.clock(session) - _duration(window)
        )
    return list(
        session.execute(
            statement.order_by(Observation.measured_at, Observation.id)
        ).scalars()
    )


def _duration(window: str) -> timedelta:
    try:
        return parse_duration(window)
    except ValueError as exc:
        # An operator's mistake, reaching them as a refusal rather than a traceback.
        raise Unreadable(str(exc)) from exc


def _staleness(session: Session, product: Product, observation: Any | None):
    if observation is None:
        return None
    windows = freshness.windows_for(session, product.id)
    window = windows.get(observation.source_id)
    if window is None or window.window is None:
        return None
    return freshness.staleness_of(
        observation.measured_at,
        window=window.window,
        now=freshness.clock(session),
        source=window.label,
    )


def _links_touching(session: Session, product: Product, ref: str) -> list[dict]:
    rows = session.execute(
        select(Link).where(
            Link.product_id == product.id,
            Link.status == "active",
            (Link.from_ref == ref) | (Link.to_ref == ref),
        )
    ).scalars().all()
    return [
        {"from": r.from_ref, "to": r.to_ref, "link_type": r.link_type,
         "source": r.created_by}
        for r in rows
    ]


def _mark_changes(rows: list[dict]) -> list[dict]:
    """Flag each observation where a recorded input differs from the one before it.

    US-8: "The Layer shall mark points where the prompt version or corpus changed." A
    reader comparing twelve rows by eye is the thing this avoids, and it is also what
    makes H1 legible — a step with no change in any recorded version is the case where
    versioning does not explain the movement, and it can only be seen once the changes
    are marked.
    """
    previous: dict | None = None
    for row in rows:
        changed = sorted(
            field for field in ("prompt_version", "corpus_sha", "code_rev")
            if previous is not None and row.get(field) != previous.get(field)
        )
        row["changed"] = changed
        row["inputs_unchanged"] = previous is not None and not changed
        previous = row
    return rows


def _unexplained_steps(history: list[dict]) -> list[dict]:
    """Groups of runs that record the same inputs and disagree on the number.

    H1 and US-8's third criterion: "the Layer shall state that versioning does not
    explain the movement". The sentence existed in a docstring and in nothing the Layer
    ever said, which the story matrix found — H1 had a test that the *condition* exists
    in the fixture history and none that the Layer reports it.

    **Grouped, not compared pairwise.** The first version walked adjacent pairs and found
    nothing, because the fixture's instance is v4, v5 and v6 sharing one prompt sha and
    one corpus sha while scoring 92, 92 and 94 — the disagreement is across a group, and
    two of the three runs agree. Comparing neighbours would have reported this condition
    as absent from a history built to contain it.

    States the coincidence and stops. Naming a cause is forbidden (B3 rule 6), and the
    honest content here is precisely that the recorded inputs do not account for it.
    """
    groups: dict[tuple, list[dict]] = {}
    for row in history:
        if row["prompt_version"] is None and row["corpus_sha"] is None:
            # Nothing recorded to hold constant, so nothing can be said about it.
            continue
        groups.setdefault((row["prompt_version"], row["corpus_sha"]), []).append(row)

    out = []
    for (prompt_version, corpus_sha), rows in groups.items():
        values = {row["value"] for row in rows}
        if len(rows) < 2 or len(values) < 2:
            continue
        out.append({
            "runs": [row["run_id"] for row in rows],
            "values": sorted(values),
            "prompt_version": prompt_version,
            "corpus_sha": corpus_sha,
            "note": (
                f"{len(rows)} runs record the same prompt version and corpus and the "
                f"value differs across them. Versioning does not explain the movement."
            ),
        })
    return out


def _observation_dict(row: Observation) -> dict:
    return {
        "id": row.id,
        "value": row.value,
        "passed": row.passed,
        "total": row.total,
        "unit": row.unit,
        "measured_at": row.measured_at.isoformat(),
        "run_id": row.run_id,
        "run_url": row.run_url,
        "prompt_version": row.prompt_version,
        "corpus_sha": row.corpus_sha,
        "code_rev": row.code_rev,
    }


def _bar(clause: Clause) -> str:
    if clause.value is None:
        return "no numeric bar"
    if clause.comparator == "between":
        return f"between {clause.value:g} and {clause.value_high:g}"
    unit = f" {clause.unit}" if clause.unit and clause.unit != "ratio" else ""
    return f"{clause.comparator} {clause.value:g}{unit}"


def _age(window, now) -> str:
    return approximate_duration(now - window.last_sync_at) if window.last_sync_at else "never"


def count_products(session: Session) -> int:
    return session.execute(select(func.count()).select_from(Product)).scalar_one()
