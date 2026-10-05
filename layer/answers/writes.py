"""The write tier of PRD B11. Five tools, none of which changes what correct means.

```
record_observation      a measurement arrives
record_case_results     the cases beneath it
propose_change          one field of one clause, for a human to decide
propose_link            one edge, for a human to decide
propose_binding         a metric paired with a clause, for a human to decide
```

**The line this module sits on.** An agent may record what happened and may propose what
should change. It may never decide. `accept_proposal`, `reject_proposal` and
`confirm_binding` are absent from the MCP server entirely (B11's third tier, AC-31), so
everything here ends either in a row describing the world or in an `open` proposal.

**Every proposal is checked against B4 before it exists, not after.** Evidence that does
not resolve is refused rather than stored, because B3 rule 7 is "never cite a record it
did not read" and a proposal whose citation is broken wastes the one minute of human
attention B4 budgets for it. A second open proposal against the same target and field is
refused by the database and reported as a refusal here (B4 item 4).

**`if_rejected` is required.** B4 item 5: a proposal states what happens if it is turned
down, which is what makes rejecting it a decision rather than a deferral.

**Nothing here runs for a product that is not `live` with a confirmed binding.** B3 rule
8, via `state.require_live`, which every proposal path calls. Recording an observation is
different and is allowed earlier: an observation is a fact about a run, not a suggestion.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from layer.answers.shapes import HIGH, Answer, ProposedChange
from layer.core import audit
from layer.core.errors import NotLive, Refusal
from layer.core.redact import redact
from layer.db.models import (
    KNOWN_LINK_TYPES,
    KNOWN_PROPOSAL_KINDS,
    CaseResult,
    Clause,
    Observation,
    Product,
    Proposal,
)
from layer.findings.citations import registry_for
from layer.metrics.engine import CASE_OUTCOMES, ERROR, FAIL
from layer.onboarding import bindings as binding_gate
from layer.onboarding import state

PROPOSAL_CREATED = "proposal_created"
OBSERVATION_RECORDED = "observation_recorded"
CASES_RECORDED = "case_results_recorded"


# -- recording what happened -----------------------------------------------------


def record_observation(
    session: Session,
    *,
    clause_ref: str,
    value: float,
    source_kind: str,
    measured_at: datetime,
    actor: str,
    run_url: str | None = None,
    run_id: str | None = None,
    passed: int | None = None,
    total: int | None = None,
    unit: str | None = None,
    prompt_version: str | None = None,
    corpus_sha: str | None = None,
    code_rev: str | None = None,
) -> Answer | Refusal:
    """One measurement, against the clause whose metric it answers.

    Keyed through the clause's confirmed binding rather than through a metric name the
    caller supplies, so an agent cannot invent a metric the product does not measure and
    then record history against it.
    """
    found = _clause(session, clause_ref)
    if isinstance(found, Refusal):
        return found
    clause, product = found
    metric = {ref: m for m, ref in
              binding_gate.confirmed_metrics(session, product=product).items()}.get(clause_ref)
    if metric is None:
        return Refusal(
            reason=(
                f"{clause_ref} has no confirmed binding, so the Layer does not know "
                f"which measurement answers it and will not guess. A human confirms "
                f"that pairing at step 5 of onboarding."
            ),
            missing=[f"a confirmed binding for clause:{clause_ref}"],
        )

    row = Observation(
        org_id=product.org_id,
        product_id=product.id,
        clause_ref=None,
        metric=metric,
        value=float(value),
        passed=passed,
        total=total,
        unit=unit or clause.unit,
        source_kind=source_kind,
        prompt_version=prompt_version,
        corpus_sha=corpus_sha,
        code_rev=code_rev,
        run_id=run_id,
        run_url=run_url,
        detail={},
        measured_at=measured_at,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        # The idempotency key did its job. Reported rather than raised: a scheduled pull
        # replaying a run is normal, and AC-2 wants the refusal counted, not hidden.
        return Refusal(
            reason=(
                f"an observation of {metric} for this run is already stored, so nothing "
                f"was written. History is append-only and never double counted."
            ),
            missing=[],
        )
    audit.record(
        session, org_id=product.org_id, actor=actor, action=OBSERVATION_RECORDED,
        subject=f"obs:{row.id}",
        detail={"metric": metric, "clause_ref": clause_ref, "value": float(value),
                "run_url": run_url, "source_kind": source_kind},
    )
    return Answer(
        statement=(
            f"Recorded {metric} at {float(value):g} for {clause_ref}, measured "
            f"{measured_at.date()}. The verdict is not recomputed here; the measure pass "
            f"does that."
        ),
        citations=[f"obs:{row.id}", f"clause:{clause_ref}"],
        detail={"observation_id": row.id, "metric": metric, "value": float(value)},
    )


def record_case_results(
    session: Session, *, observation_id: int, cases: list[dict], actor: str
) -> Answer | Refusal:
    """The per-case layer beneath an observation that already exists.

    **Redacted here, whatever the caller sent.** The caller is an agent and may pass raw
    customer text; PRD B6 sets unredacted PII at zero tolerance, so every input goes
    through the redactor on the way in rather than being trusted. An input on a case that
    is not `fail` or `error` is dropped, which the CHECK also enforces (AC-26).
    """
    observation = session.get(Observation, observation_id)
    if observation is None:
        return Refusal(
            reason=(
                f"no observation {observation_id} exists in this org, so there is "
                f"nothing for these cases to hang beneath."
            ),
            missing=[f"obs:{observation_id}"],
        )
    if not cases:
        return Refusal(
            reason="no cases were supplied, so nothing was written.",
            missing=["at least one case"],
        )

    rows, skipped, inputs = [], [], 0
    seen: set[str] = set()
    for case in cases:
        case_id = str(case.get("case_id") or "").strip()
        outcome = str(case.get("outcome") or "").strip()
        if not case_id or outcome not in CASE_OUTCOMES:
            skipped.append({"case_id": case_id or "(unnamed)", "reason": "bad_outcome"})
            continue
        if case_id in seen:
            skipped.append({"case_id": case_id, "reason": "duplicate_case_id"})
            continue
        seen.add(case_id)
        raw = case.get("input")
        stored = None
        if raw is not None and outcome in (FAIL, ERROR):
            stored = redact(str(raw))
            inputs += 1
        rows.append(CaseResult(
            org_id=observation.org_id,
            observation_id=observation.id,
            case_id=case_id,
            # The observation's own time, never the clock's: it is the partition key and
            # one run has one time (AC-27).
            measured_at=observation.measured_at,
            outcome=outcome,
            input_redacted=stored,
            trace_id=case.get("trace_id"),
            trace_url=case.get("trace_url"),
        ))
    if not rows:
        return Refusal(
            reason=(
                "no case in that list could be stored. Each needs a `case_id` and an "
                f"`outcome` from {', '.join(CASE_OUTCOMES)}."
            ),
            missing=["a case with a usable case_id and outcome"],
        )

    session.add_all(rows)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return Refusal(
            reason=(
                f"some of those cases are already stored beneath observation "
                f"{observation_id}. Case rows are append-only and are never rewritten."
            ),
            missing=[],
        )
    audit.record(
        session, org_id=observation.org_id, actor=actor, action=CASES_RECORDED,
        subject=f"obs:{observation_id}",
        detail={"stored": len(rows), "inputs_stored": inputs, "refused": skipped},
    )
    return Answer(
        statement=(
            f"Stored {len(rows)} case(s) beneath observation {observation_id}"
            + (f", refusing {len(skipped)}" if skipped else "")
            + f". {inputs} carried an input, redacted before it was written."
        ),
        citations=[f"obs:{observation_id}"]
        + [f"case:{observation_id}/{r.case_id}" for r in rows[:12]],
        detail={"stored": len(rows), "refused": skipped, "inputs_stored": inputs},
    )


# -- proposing what should change ------------------------------------------------


def propose_change(
    session: Session,
    *,
    target: str,
    field: str,
    new_value: str,
    reason: str,
    evidence: list[str],
    actor: str,
    if_rejected: str | None = None,
    kind: str = "clause_change",
    confidence: float | None = None,
) -> ProposedChange | Refusal:
    """One field of one clause, for a human to decide."""
    if kind not in KNOWN_PROPOSAL_KINDS:
        return Refusal(
            reason=(
                f"{kind!r} is not a proposal kind this Layer knows. Known: "
                f"{', '.join(KNOWN_PROPOSAL_KINDS)}."
            ),
            missing=[],
        )
    ref = target.split(":", 1)[-1] if target.startswith("clause:") else target
    found = _clause(session, ref)
    if isinstance(found, Refusal):
        return found
    clause, product = found
    old = getattr(clause, field, None)
    if not hasattr(clause, field):
        return Refusal(
            reason=(
                f"a clause has no field named {field!r}, so there is nothing to change. "
                f"A proposal names exactly one field that exists (B4 item 1)."
            ),
            missing=[f"a field named {field}"],
        )
    return _create(
        session, product=product, kind=kind, target=f"clause:{clause.ref}", field=field,
        old_value=None if old is None else str(old), new_value=str(new_value),
        reason=reason, evidence=evidence, actor=actor, if_rejected=if_rejected,
        confidence=confidence, target_version=clause.version,
    )


def propose_link(
    session: Session,
    *,
    from_ref: str,
    to_ref: str,
    link_type: str,
    reason: str,
    evidence: list[str],
    actor: str,
    product_key: str | None = None,
    if_rejected: str | None = None,
    confidence: float | None = None,
) -> ProposedChange | Refusal:
    """One edge between two records, for a human to decide."""
    if link_type not in KNOWN_LINK_TYPES:
        return Refusal(
            reason=(
                f"{link_type!r} is not a link type this Layer knows. Known: "
                f"{', '.join(KNOWN_LINK_TYPES)}. The vocabulary is open, and adding one "
                f"is a registry change rather than a guess at call time."
            ),
            missing=[],
        )
    product = _product_for(session, product_key, from_ref, to_ref)
    if isinstance(product, Refusal):
        return product
    return _create(
        session, product=product, kind="link", target=f"{from_ref}->{to_ref}",
        field="link_type", old_value=None, new_value=link_type, reason=reason,
        evidence=evidence, actor=actor, if_rejected=if_rejected, confidence=confidence,
    )


def propose_binding(
    session: Session,
    *,
    product_key: str,
    metric: str,
    clause_ref: str,
    reason: str,
    actor: str,
    evidence: list[str] | None = None,
    if_rejected: str | None = None,
    confidence: float | None = None,
) -> ProposedChange | Refusal:
    """A metric paired with a clause — proposed, never confirmed.

    This is the tool B11 singles out. An agent able to confirm its own binding would
    manufacture B3 rule 8's precondition and then propose freely, so `confirm_binding`
    is absent from the server and this stops at a proposal a human decides.
    """
    product = session.execute(
        select(Product).where(Product.key == product_key)
    ).scalars().first()
    if product is None:
        return Refusal(
            reason=f"no product named {product_key!r} is onboarded in this org.",
            missing=[f"product:{product_key}"],
        )
    clause = session.execute(
        select(Clause).where(
            Clause.product_id == product.id, Clause.ref == clause_ref,
            Clause.status == "active",
        )
    ).scalars().first()
    if clause is None:
        return Refusal(
            reason=f"{product_key} has no active clause named {clause_ref}.",
            missing=[f"clause:{clause_ref}"],
        )
    return _create(
        session, product=product, kind="link", target=f"binding:{product_key}/{metric}",
        field="clause_ref", old_value=None, new_value=clause_ref, reason=reason,
        evidence=evidence or [f"clause:{clause_ref}"], actor=actor,
        if_rejected=if_rejected, confidence=confidence,
    )


# -- the one path every proposal takes -------------------------------------------


def _create(
    session: Session,
    *,
    product: Product,
    kind: str,
    target: str,
    field: str,
    old_value: str | None,
    new_value: str | None,
    reason: str,
    evidence: list[str],
    actor: str,
    if_rejected: str | None,
    confidence: float | None,
    target_version: int | None = None,
) -> ProposedChange | Refusal:
    """Every B4 and B3 check in one place, so no proposal path can skip one."""
    try:
        state.require_live(session, product=product)
    except NotLive as exc:
        return Refusal(reason=str(exc), missing=["a confirmed binding on this product"])

    if not reason or not reason.strip():
        return Refusal(
            reason="a proposal needs a reason a human can agree or disagree with "
                   "(B4 item 3).",
            missing=["a one-sentence reason"],
        )
    if not evidence:
        return Refusal(
            reason=(
                "a proposal needs evidence that resolves to records a human can open "
                "(B4 item 2). Without it there is nothing to check it against."
            ),
            missing=["at least one evidence ref"],
        )
    if not if_rejected:
        return Refusal(
            reason=(
                "a proposal states what happens if it is rejected (B4 item 5), which is "
                "what makes turning it down a decision rather than a deferral."
            ),
            missing=["if_rejected"],
        )

    registry = registry_for(session, product=product)
    links, unresolved = registry.links(evidence)
    if unresolved:
        # B3 rule 7: never cite a record it did not read. Refused before it is stored,
        # because a proposal whose citation is broken costs a human the minute B4 budgets
        # for it and teaches them to distrust the rest.
        return Refusal(
            reason=(
                "this proposal cites evidence that does not resolve: "
                + ", ".join(unresolved)
                + ". A citation that cannot be opened is a defect, not a typo."
            ),
            missing=list(unresolved),
        )

    row = Proposal(
        org_id=product.org_id,
        product_id=product.id,
        kind=kind,
        target=target,
        field=field,
        old_value=old_value,
        new_value=new_value,
        reason=reason.strip(),
        evidence=[link["id"] for link in links],
        if_rejected=if_rejected,
        confidence=confidence,
        state="open",
        target_version=target_version,
        proposed_by=actor,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        # B4 item 4, enforced by a partial unique index on the open ones.
        return Refusal(
            reason=(
                f"a proposal against {target} / {field} is already open. Deciding that "
                f"one comes before making another."
            ),
            missing=[],
        )
    audit.record(
        session, org_id=product.org_id, actor=actor, action=PROPOSAL_CREATED,
        subject=f"proposal:{row.id}",
        detail={"kind": kind, "target": target, "field": field,
                "evidence": row.evidence, "confidence": confidence},
    )
    return ProposedChange(
        target=target,
        field_name=field,
        old_value=old_value,
        new_value=new_value,
        reason=row.reason,
        evidence=row.evidence,
        if_rejected=if_rejected,
        confidence=confidence,
        proposal_id=str(row.id),
        kind=kind,
    )


def _clause(session: Session, ref: str) -> tuple[Clause, Product] | Refusal:
    clause = session.execute(
        select(Clause).where(Clause.ref == ref, Clause.status == "active")
    ).scalars().first()
    if clause is None:
        return Refusal(
            reason=f"no active clause is named {ref} in this org.",
            missing=[f"clause:{ref}"],
        )
    return clause, session.get(Product, clause.product_id)


def _product_for(
    session: Session, key: str | None, *refs: str
) -> Product | Refusal:
    if key:
        row = session.execute(
            select(Product).where(Product.key == key)
        ).scalars().first()
        if row is None:
            return Refusal(
                reason=f"no product named {key!r} is onboarded in this org.",
                missing=[f"product:{key}"],
            )
        return row
    # Inferred from a clause ref among the endpoints, so a link between two clauses needs
    # no product argument. Where neither end names one, the caller is asked rather than
    # guessed at: a link stored against the wrong product is invisible afterwards.
    for ref in refs:
        if ref.startswith("clause:"):
            found = _clause(session, ref.split(":", 1)[1])
            if not isinstance(found, Refusal):
                return found[1]
    return Refusal(
        reason=(
            "neither end of that link names a clause, so the Layer cannot tell which "
            "product it belongs to. Name the product."
        ),
        missing=["product_key"],
    )


def _uuid(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError):
        return None
