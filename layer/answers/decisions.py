"""The human-only tier: accept, reject, and the one number that says whether any of
this works.

**This module is deliberately not in `writes.py`.** `writes.py` is the tier
`layer/mcp/server.py` imports. A function that decides is not in it, so no decorator,
allowlist or configuration mistake can serve one. AC-31 tests the served tool list from
the outside; this makes the list hard to get wrong from the inside. PRD B11 is explicit
that the third tier is *absent* rather than merely unexposed, and absence is a property
of the import graph, not of a comment.

**US-10 is the story this implements**, and it asks for four things that are easy to
reduce to three: present the proposal with its evidence, record who decided and when,
record **what evidence was displayed**, and record the resulting clause version on
accept. The third is the one usually dropped, and it is the one that matters when a
decision is questioned a year later: "they approved it" is not a defence, "they approved
it while looking at these three runs" is.

**Accepting is where a proposal stops being a suggestion**, so every apply path here is
narrow and each is tested on its own. One kind — `ci_change` — stops short of its final
act, and says so rather than pretending: see `_apply_ci_change`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from layer.core import actors, audit
from layer.findings import traces
from layer.core.errors import Refusal, Unreadable
from layer.db.models import Clause, Link, Observation, Product, Proposal

PROPOSAL_ACCEPTED = "proposal_accepted"
PROPOSAL_REJECTED = "proposal_rejected"
PROPOSAL_INVALIDATED = "proposal_invalidated"
PROPOSAL_EVIDENCE_EXPIRED = "proposal_evidence_expired"

#: PRD B6. Both edges matter and the upper one is the unusual part: above it, either the
#: proposals are trivial or nobody is reading them, and a rubber stamp on changes to the
#: definition of correctness is worse than no tool.
BAND_LOW = 0.50
BAND_HIGH = 0.85


class AlreadyDecided(Unreadable):
    """EC-6. Someone decided this proposal first, and the second person is told by whom.

    Not a generic conflict: the message names the decider and the decision, because the
    second person's next action depends on which it was.
    """


class Stale(Unreadable):
    """H2. The target moved under the proposal, so applying it would overwrite a human.

    Raised by `accept` only. Rejecting a stale proposal is always allowed — clearing the
    queue of proposals that reality has overtaken is housekeeping, not a decision about
    the product.
    """


def _now(session: Session) -> datetime:
    """The database's clock, not the process's. Every other timestamp in the Layer
    comes from there and a decision ordered against a different clock is unorderable."""
    return session.execute(select(func.now())).scalar_one()


# -- the queue -------------------------------------------------------------------


def queue(
    session: Session, *, product: Product | None = None, sweep_first: bool = True
) -> list[dict]:
    """Open proposals, oldest first, in the shape B4 asks a human to read in a minute.

    Oldest first rather than newest: a queue that surfaces the newest first quietly
    buries whatever nobody got to, and the buried ones are exactly where an
    evidence-expiry (H7) is waiting.

    **The sweep runs first**, so nothing undecidable is ever presented as decidable. A
    proposal whose target a human has edited, or whose evidence retention has deleted, is
    closed before the queue is built rather than when somebody has already spent their
    minute on it. `sweep_first=False` exists for the tests that want to see an open
    proposal in the state the sweep would close.
    """
    if sweep_first and product is not None:
        sweep(session, product=product)
    stmt = select(Proposal).where(Proposal.state == "open")
    if product is not None:
        stmt = stmt.where(Proposal.product_id == product.id)
    rows = session.execute(stmt.order_by(Proposal.created_at)).scalars().all()
    return [_presented(row) for row in rows]


def _presented(row: Proposal) -> dict:
    """What a human is shown, and therefore what the audit event records as displayed."""
    return {
        "id": str(row.id),
        "kind": row.kind,
        "target": row.target,
        "field": row.field,
        "old_value": row.old_value,
        "new_value": row.new_value,
        "reason": row.reason,
        "evidence": list(row.evidence or []),
        "if_rejected": row.if_rejected,
        # Null is rendered by the caller as "unscored", never as zero. A proposal no
        # critic has seen is not a low-confidence proposal, and until the critic exists
        # every one of these is null.
        "confidence": row.confidence,
        "rank": getattr(row, "rank", None),
        "proposed_by": row.proposed_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def get(session: Session, proposal_id) -> Proposal | Refusal:
    row = session.execute(
        select(Proposal).where(Proposal.id == _as_uuid(proposal_id))
    ).scalars().first()
    if row is None:
        return Refusal(
            reason=f"no proposal with id {proposal_id} in this org.",
            missing=[f"proposal:{proposal_id}"],
        )
    return row


def _as_uuid(value) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise Unreadable(f"{value!r} is not a proposal id") from exc


# -- deciding --------------------------------------------------------------------


def accept(session: Session, *, proposal_id, by: str, note: str | None = None) -> dict:
    """Accept one proposal and apply it. The only path by which a proposal takes effect.

    Order matters here and is not arbitrary. The role is checked before the claim on the
    row, so an actor who may not decide learns that rather than racing for it; the claim
    is taken before the apply, so two people cannot both apply; and the apply happens
    before the audit event, so a failed apply records nothing and the whole thing is one
    transaction. B3 rule 1 is that no write happens without an approval record — the
    converse matters too, that no approval record is written for a write that did not
    happen.
    """
    row = get(session, proposal_id)
    if isinstance(row, Refusal):
        return {"shape": "refusal", **row.as_dict()}

    actor = actors.require_decider(session, email=by, kind=row.kind)
    _require_fresh(session, row)
    displayed = _presented(row)
    _claim(session, row, by=actor.email, state="accepted", note=note)

    applied = _apply(session, row, by=actor.email)

    audit.record(
        session,
        org_id=row.org_id,
        actor=actor.email,
        action=PROPOSAL_ACCEPTED,
        subject=f"proposal:{row.id}",
        # US-10: what evidence was displayed, not merely that a decision happened.
        detail={"displayed": displayed, "applied": applied, "role": actor.role,
                "note": note},
    )
    return {"shape": "answer", "decision": "accepted", "proposal": displayed,
            "applied": applied, "decided_by": actor.email}


def reject(session: Session, *, proposal_id, by: str, reason: str) -> dict:
    """Reject one proposal. A decision, which is why `reason` is required.

    B4 item 5 made the proposal state what happens if it is turned down; this is the
    other half. A rejection with no reason is indistinguishable from a proposal nobody
    got to, and the acceptance rate cannot tell the difference either.
    """
    if not reason or not reason.strip():
        raise Unreadable(
            "a rejection needs a reason. Turning a proposal down is a decision about the "
            "product (B4 item 5), and an unexplained one teaches the generator nothing."
        )
    row = get(session, proposal_id)
    if isinstance(row, Refusal):
        return {"shape": "refusal", **row.as_dict()}

    actor = actors.require_decider(session, email=by, kind=row.kind)
    displayed = _presented(row)
    _claim(session, row, by=actor.email, state="rejected", note=reason.strip())

    audit.record(
        session,
        org_id=row.org_id,
        actor=actor.email,
        action=PROPOSAL_REJECTED,
        subject=f"proposal:{row.id}",
        detail={"displayed": displayed, "role": actor.role, "reason": reason.strip(),
                "if_rejected": row.if_rejected},
    )
    return {"shape": "answer", "decision": "rejected", "proposal": displayed,
            "decided_by": actor.email}


def _claim(session: Session, row: Proposal, *, by: str, state: str, note: str | None) -> None:
    """Take the proposal, or find out who took it first. PRD EC-6.

    A conditional UPDATE rather than a row lock. `SELECT ... FOR UPDATE` would hold a
    lock across a human's deliberation, which is minutes, and the loser would block
    rather than be told. Here the loser's update matches no row and they are told what
    happened and by whom, which is what EC-6 asks for.
    """
    decided_at = _now(session)
    result = session.execute(
        update(Proposal)
        .where(Proposal.id == row.id, Proposal.state == "open")
        .values(state=state, decided_by=by, decided_at=decided_at, decision_note=note)
    )
    if result.rowcount == 0:
        session.expire(row)
        current = session.execute(
            select(Proposal).where(Proposal.id == row.id)
        ).scalars().first()
        if current is None:
            raise AlreadyDecided(f"proposal {row.id} no longer exists")
        raise AlreadyDecided(
            f"proposal {row.id} was already {current.state} by {current.decided_by} "
            f"at {current.decided_at.isoformat() if current.decided_at else 'unknown'}. "
            "The first decision stands; nothing has been changed by this one."
        )
    session.expire(row)


def _require_fresh(session: Session, row: Proposal) -> None:
    """H2. A human edited the target while this proposal was open, so it is invalidated
    rather than merged over. Human text wins.

    Only `clause_change` carries a `target_version` worth comparing. A `link` proposal's
    target is a pair of refs and a `ci_change`'s is a file, neither of which versions
    here.
    """
    if row.target_version is None or not row.target.startswith("clause:"):
        return
    ref = row.target.split(":", 1)[1]
    clause = _active_clause(session, row.product_id, ref)
    if clause is None:
        raise Stale(
            f"{row.target} no longer exists, so there is nothing to apply this to. "
            "Reject the proposal; the next generator run will reconsider it."
        )
    if clause.version != row.target_version:
        raise Stale(
            f"{row.target} was at version {row.target_version} when this was proposed "
            f"and is now at version {clause.version}. A human has edited it since, and "
            "human text wins (H2). Reject this proposal rather than applying it over "
            "their edit; the next generator run will propose against the new text."
        )


def _active_clause(session: Session, product_id, ref: str) -> Clause | None:
    return session.execute(
        select(Clause).where(
            Clause.product_id == product_id, Clause.ref == ref, Clause.status == "active"
        )
    ).scalars().first()


# -- the sweep: H2, H7 and EC-8 ---------------------------------------------------


def sweep(session: Session, *, product: Product, now: datetime | None = None) -> dict:
    """Close the open proposals reality has overtaken, before anybody decides one.

    Two conditions, both of which make a proposal undecidable rather than wrong:

    **H2, the target moved.** A human edited the clause an open proposal targets. Human
    text wins and the proposal is invalidated rather than merged over. `accept` refuses
    this case too, but refusing at the moment of decision is not enough on its own: the
    proposal sits in the queue until somebody spends their minute on it and is then told
    they cannot have it, which is the minute B4 budgets for a real decision.

    **H7 and EC-8, the evidence went.** Past the source's declared retention the trace
    body is gone, so a proposal resting on it cannot show its proof. It is marked
    evidence-expired, never displayed as live with a dead citation — which is B5 item 8,
    and the thing it ranks as unacceptable is *displaying* it as valid, not having it
    expire.

    **Neither is a decision and neither names a decider.** The database refuses a
    system-closed proposal that names one, so this cannot quietly become a way of
    deciding on a human's behalf.

    **Run before the queue is presented, not on a schedule.** A sweep nobody triggers is
    a sweep that has not run, and the queue is exactly where the answer matters.
    """
    moment = now or _now(session)
    retention = traces.retention_for(session, product.id)
    closed = {"invalidated": [], "evidence_expired": []}

    rows = session.execute(
        select(Proposal).where(
            Proposal.product_id == product.id, Proposal.state == "open"
        ).order_by(Proposal.created_at)
    ).scalars().all()

    for row in rows:
        stale = _stale_reason(session, row)
        if stale is not None:
            _close(session, row, state="invalidated", note=stale, at=moment,
                   action=PROPOSAL_INVALIDATED)
            closed["invalidated"].append(str(row.id))
            continue
        expired = _expired_reason(session, row, retention, moment)
        if expired is not None:
            _close(session, row, state="evidence_expired", note=expired, at=moment,
                   action=PROPOSAL_EVIDENCE_EXPIRED)
            closed["evidence_expired"].append(str(row.id))

    return closed


def _stale_reason(session: Session, row: Proposal) -> str | None:
    """H2's condition, as a sentence for the record rather than a boolean."""
    if row.target_version is None or not row.target.startswith("clause:"):
        return None
    ref = row.target.split(":", 1)[1]
    clause = _active_clause(session, row.product_id, ref)
    if clause is None:
        return (
            f"{row.target} is no longer an active clause, so this proposal has no target. "
            "Closed without a decision; the next generator run will reconsider the gap."
        )
    if clause.version != row.target_version:
        return (
            f"{row.target} was at version {row.target_version} when this was proposed and "
            f"is now at version {clause.version}. A human edited it, and human text wins "
            f"(H2). Closed without a decision rather than applied over their edit; the "
            f"next generator run will propose against the new text."
        )
    return None


def _expired_reason(
    session: Session, row: Proposal, retention: dict, now: datetime
) -> str | None:
    """H7 and EC-8's condition.

    Answered from the source's declared retention window and never from a fetch, which
    is AC-24's rule: the path that answers "which cases are the proof" makes no live call
    to the eval platform. An undeclared or unparseable window counts as absent, so
    nothing expires on a typo (`traces.retention_for`).

    **Only case citations expire.** A `clause:` ref resolves to a specification document
    at a pinned revision and an `obs:` ref to a stored run record; neither is a trace
    body that a source deletes. Treating them as expirable would retire proposals whose
    evidence is perfectly readable.
    """
    if not retention:
        return None
    cases = [ref for ref in (row.evidence or []) if ref.startswith("case:")]
    if not cases:
        return None

    gone: list[str] = []
    for ref in cases:
        body = ref.split(":", 1)[1]
        obs_id = body.split("/", 1)[0]
        if not obs_id.isdigit():
            continue
        found = session.execute(
            select(Observation.source_id, Observation.measured_at).where(
                Observation.product_id == row.product_id, Observation.id == int(obs_id)
            )
        ).first()
        if found is None:
            continue
        source_id, measured_at = found
        window = retention.get(source_id)
        if window is not None and now - measured_at > window:
            gone.append(ref)

    if not gone or len(gone) < len(cases):
        # Some proof survives, so the proposal can still show its evidence. B5 item 8 is
        # about displaying a dead citation as live, not about any citation having aged.
        return None
    return (
        f"every case this proposal cites is past its source's declared trace retention: "
        f"{', '.join(gone[:4])}"
        + (f" and {len(gone) - 4} more" if len(gone) > 4 else "")
        + ". The recorded outcomes survive and the citations point at their runs, but the "
        "proof a reviewer would open is gone, so this is closed rather than shown as "
        "live (H7, EC-8, B5 item 8)."
    )


def _close(
    session: Session, row: Proposal, *, state: str, note: str, at: datetime, action: str
) -> None:
    """Close a proposal without a decider, and record that the system did it.

    The same conditional update `_claim` uses, so a sweep racing a human loses to the
    human rather than overwriting their decision (EC-6 again, from the other side).
    """
    result = session.execute(
        update(Proposal)
        .where(Proposal.id == row.id, Proposal.state == "open")
        .values(state=state, decided_by=None, decided_at=at, decision_note=note)
    )
    if result.rowcount == 0:
        return
    audit.record(
        session,
        org_id=row.org_id,
        # The actor is the Layer itself, which is the honest answer and is why
        # `decided_by` on the row stays null: the log records who acted, the column
        # records who decided, and here those are not the same thing.
        actor="layer:sweep",
        action=action,
        subject=f"proposal:{row.id}",
        detail={"state": state, "reason": note, "target": row.target},
    )
    session.expire(row)


# -- applying --------------------------------------------------------------------


def _apply(session: Session, row: Proposal, *, by: str) -> dict:
    handler = {
        "clause_change": _apply_clause_change,
        "new_clause": _apply_new_clause,
        "link": _apply_link,
        "eval_case": _apply_deferred,
        "ci_change": _apply_ci_change,
        "ticket": _apply_ticket,
    }.get(row.kind)
    if handler is None:
        raise Unreadable(
            f"accepting a {row.kind!r} proposal is not implemented, so it has not been "
            "applied. Nothing is half-applied: this is one transaction."
        )
    return handler(session, row, by=by)


def _apply_clause_change(session: Session, row: Proposal, *, by: str) -> dict:
    """Supersede the clause and write the new version. H3's mechanism, reused.

    The ref survives and every link stays attached, which is the whole reason clauses
    are versioned rather than updated in place. A link that pointed at the old row would
    otherwise have to be rewritten, and a missed one is a chain that breaks silently.
    """
    ref = row.target.split(":", 1)[1]
    previous = _active_clause(session, row.product_id, ref)
    if previous is None:  # pragma: no cover - _require_fresh refuses this first
        raise Stale(f"{row.target} is not an active clause")

    columns = {c.key for c in Clause.__table__.columns}
    carried = {
        key: getattr(previous, key)
        for key in columns
        - {"id", "version", "status", "superseded_at", "superseded_by_id", "created_at",
           "approved_by", "approved_at"}
    }
    carried[row.field] = _coerce(previous, row.field, row.new_value)

    new = Clause(
        **carried,
        version=previous.version + 1,
        status="active",
        approved_by=by,
    )
    previous.status = "superseded"
    previous.superseded_at = _now(session)
    session.add(new)
    session.flush()
    previous.superseded_by_id = new.id

    # US-10's third bullet: the resulting clause version is recorded, not inferred.
    return {"clause_ref": ref, "field": row.field, "version": new.version,
            "clause_id": str(new.id)}


def _apply_new_clause(session: Session, row: Proposal, *, by: str) -> dict:
    """Create the clause the proposal describes, provisional and not measured.

    `provisional` is not a formality. H9: a threshold that was never measured must never
    read as a met or missed bar, and a clause born from an `uncovered` finding has by
    definition never been measured against anything.
    """
    fields = dict(row.payload or {})
    ref = row.target.split(":", 1)[-1]
    new = Clause(
        org_id=row.org_id,
        product_id=row.product_id,
        ref=ref,
        kind=fields.get("kind", "threshold"),
        statement=fields.get("statement") or row.reason,
        metric=fields.get("metric"),
        comparator=fields.get("comparator"),
        value=fields.get("value"),
        value_high=fields.get("value_high"),
        unit=fields.get("unit"),
        direction=fields.get("direction"),
        state="provisional",
        verdict="not_measured",
        version=1,
        status="active",
        approved_by=by,
    )
    session.add(new)
    session.flush()
    return {"clause_ref": ref, "version": 1, "clause_id": str(new.id),
            "state": "provisional"}


def _apply_link(session: Session, row: Proposal, *, by: str) -> dict:
    """Create the edge, carrying its proposal id as provenance.

    A binding proposal is stored as a `link` whose target names a binding, and it does
    **not** become a binding here. `confirm_binding` is human-only for the reason B11
    gives, and routing it through this function would make accepting a proposal a way of
    confirming a binding — which is the same power an agent must not have. It is refused and
    named instead.
    """
    if row.target.startswith("binding:"):
        raise Unreadable(
            "this is a proposed binding, and a binding is confirmed rather than accepted: "
            "`layer bindings confirm --metric ... --clause ...`. Keeping the two apart is "
            "B11's rule, because a binding is what B3 rule 8 requires before anything may "
            "be proposed at all."
        )
    from_ref, _, to_ref = row.target.partition("->")
    link = Link(
        org_id=row.org_id,
        product_id=row.product_id,
        from_ref=from_ref,
        to_ref=to_ref,
        link_type=row.new_value,
        created_by=by,
        proposal_id=row.id,
        status="active",
    )
    session.add(link)
    session.flush()
    return {"link": f"{from_ref} -{row.new_value}-> {to_ref}", "link_id": str(link.id)}


def _apply_ci_change(session: Session, row: Proposal, *, by: str) -> dict:
    """Record the approval and the intended change. **It does not open the pull request.**

    SEC-3 requires a scoped app installation — `contents:write` and
    `pull_requests:write`, a per-tenant repository allowlist, pull requests only, never a
    push to a protected branch (B3 rule 4) — and no such credential exists in this
    project. The honest options were to stop here and say so, or to open the PR by some
    other means and quietly break SEC-3 and B3 rule 4 together.

    So the decision and its approval record are real, the diff is recorded, and the act
    of raising the PR is a named seam rather than a surprise. US-12 and US-3 close for
    everything except that act.
    """
    return {
        "recorded": True,
        "pull_request": None,
        "pending": (
            "the pull request is not opened: SEC-3 requires a scoped app installation "
            "with a per-tenant repository allowlist, which this deployment does not have. "
            "The approval is recorded and the intended change is stored."
        ),
        "intended": {"target": row.target, "field": row.field,
                     "new_value": row.new_value},
    }


def _apply_ticket(session: Session, row: Proposal, *, by: str) -> dict:
    """Records that the work was accepted. **It does not file the ticket.**

    US-2's other branch: where the history shows the bar is reachable, the gap is work
    rather than a specification error. Filing it means writing into Linear or Jira, which
    needs a `ticket` role source bound — phase 6, and open question 2's neighbour. The
    decision and its approval record are real now; the act of filing is a named seam.

    This handler arrived after the decide path did, because the `ticket` kind did. The
    path refuses an unknown kind rather than silently recording an approval for a change
    nothing applies, which is why its absence showed up as a failing test rather than as
    a proposal that looked accepted and did nothing.
    """
    return {
        "recorded": True,
        "ticket": None,
        "pending": (
            "the ticket is not filed: that needs a ticket source bound to this product, "
            "which no phase before 6 provides. The approval and the work it describes "
            "are recorded."
        ),
        "intended": {"target": row.target, "summary": row.new_value},
    }


def _apply_deferred(session: Session, row: Proposal, *, by: str) -> dict:
    """An accepted `eval_case` records its approval and its provenance.

    Writing the case into the customer's suite is a repository write and lands behind the
    same app installation `_apply_ci_change` names. US-1's rule — never write a case into
    the suite until a human has ticked it — is satisfied by the tick being recorded here.
    """
    return {
        "recorded": True,
        "written_to_suite": False,
        "pending": (
            "the case is approved and not yet written into the suite: that is a repository "
            "write and needs the scoped app installation SEC-3 describes."
        ),
        "intended": {"target": row.target, "new_value": row.new_value},
    }


def _coerce(clause: Clause, field: str, value: str | None):
    """A proposal's `new_value` is text, because one column holds every field's value.

    Converted against the column it is going into rather than guessed at from the
    string's shape: "0.9" is a float on `value` and a perfectly good string on
    `statement`, and sniffing would eventually put one in the wrong place.
    """
    if value is None:
        return None
    column = Clause.__table__.columns.get(field)
    if column is None:
        raise Unreadable(f"a clause has no field named {field!r}")
    python_type = getattr(column.type, "python_type", str)
    try:
        if python_type is float:
            return float(value)
        if python_type is int:
            return int(value)
        if python_type is bool:
            return value.strip().lower() in {"1", "true", "yes"}
    except ValueError as exc:
        raise Unreadable(
            f"{value!r} is not a valid {field} for a clause: {exc}"
        ) from exc
    return value


# -- the number ------------------------------------------------------------------


def acceptance(session: Session, *, product: Product | None = None) -> dict:
    """PRD B6's single most important metric, reported against its band in both
    directions. AC-16, EC-5.

    **The denominator is decisions, not proposals.** An open proposal has not been
    decided and a system-closed one — invalidated by a human's edit (H2), or
    evidence-expired by retention (H7) — was never decided by anybody. Counting either
    would make the number depend on how much evidence had expired, which is the kind of
    quiet dependency that makes a metric useless precisely when it is being relied on.

    Below 50% the proposals are noise. Above 85% they are trivial or unread, and a rubber
    stamp on changes to the definition of correctness is worse than no tool. A result
    outside the band is reported, never tuned away (EC-5).
    """
    stmt = select(Proposal.state, func.count()).group_by(Proposal.state)
    if product is not None:
        stmt = stmt.where(Proposal.product_id == product.id)
    counts = dict(session.execute(stmt).all())

    accepted = counts.get("accepted", 0)
    rejected = counts.get("rejected", 0)
    decided = accepted + rejected
    rate = (accepted / decided) if decided else None

    return {
        "accepted": accepted,
        "rejected": rejected,
        "open": counts.get("open", 0),
        "invalidated": counts.get("invalidated", 0),
        "evidence_expired": counts.get("evidence_expired", 0),
        "decided": decided,
        "rate": rate,
        "band": [BAND_LOW, BAND_HIGH],
        "verdict": _band_verdict(rate, decided),
        "summary": _acceptance_sentence(rate, decided, accepted, rejected),
    }


def _band_verdict(rate: float | None, decided: int) -> str:
    """`unmeasured` is a distinct answer from a rate at either edge of the band.

    AC-16 asks for a real number rather than null, so the state of having no number must
    be nameable. Returning `noise` for a product with nothing decided would be a verdict
    on evidence that does not exist.
    """
    if rate is None:
        return "unmeasured"
    if decided < 20:
        # AC-16's own bar. A rate over six decisions is arithmetic, not a measurement,
        # and reporting it as `healthy` would be the rubber stamp B6 warns about wearing
        # the costume of a passing metric.
        return "provisional"
    if rate < BAND_LOW:
        return "noise"
    if rate > BAND_HIGH:
        return "rubber_stamp"
    return "healthy"


def _acceptance_sentence(rate, decided, accepted, rejected) -> str:
    if rate is None:
        return (
            "No proposal has been decided, so the acceptance rate is unmeasured. "
            "AC-16 needs at least twenty decisions before the number means anything."
        )
    pct = f"{rate * 100:.0f}%"
    base = f"{accepted} accepted and {rejected} rejected of {decided} decided, {pct}."
    if decided < 20:
        return (
            f"{base} Below AC-16's twenty decisions, so this is provisional: the band of "
            f"50% to 85% is not yet a judgement on it."
        )
    if rate < BAND_LOW:
        return (
            f"{base} Below the 50% floor: more time is spent rejecting these than the work "
            f"would have taken, and that is a product failure to report rather than hide "
            f"(EC-5)."
        )
    if rate > BAND_HIGH:
        return (
            f"{base} Above the 85% ceiling, which B6 reads as either trivial proposals or "
            f"a rubber stamp. A rubber stamp on changes to the definition of correctness "
            f"launders an unreviewed change as an approved one."
        )
    return f"{base} Inside the 50% to 85% band."
