"""Step 5: the gate the whole design turns on.

PRD B2: "Before it the Layer has no idea which number answers which promise, so anything it
proposes is invented. After it, every proposal rests on an assertion a named human confirmed
at a recorded time."

So this module proposes and **stops**. It never writes a binding on its own, at any
confidence, because the thing being asserted — this measured number answers that written
promise — is the product. Getting it wrong does not produce a slightly worse suggestion; it
produces a confident finding about a promise the number has nothing to do with.

What is proposed is paired by name, and the pairing is deliberately shallow: an exact metric
match, then a normalised match, then nothing. There is no fuzzy scoring, because a candidate
a human has to check anyway gains nothing from a plausible-looking number attached to it, and
loses something if that number encourages waving it through.

The two kinds of gap are surfaced alongside, because they are findings rather than
omissions: a clause nothing measures (`no_assertion`), and a measurement nothing promises
(`metric_without_clause`, PRD H16, where "the gap is the absent clause, not the metric").
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from layer.adapters.spec.values import normalise_metric_name
from layer.core import audit
from layer.core.errors import LayerError
from layer.db.models import Binding, Clause, Observation, Product

CONFIRMED = "confirmed"
REJECTED = "rejected"


class UnknownCandidate(LayerError):
    """A decision about a pairing the Layer never proposed.

    Refused rather than accepted, because a binding invented at the point of confirmation
    has had no proposal, no evidence shown, and nothing for the audit record to point at.
    """


@dataclass(frozen=True)
class Candidate:
    """One proposed assertion, with why it was proposed."""

    metric: str
    clause_ref: str
    clause_statement: str
    clause_bar: str | None
    matched_by: str
    observations: int
    latest_value: float | None


@dataclass
class Proposal:
    candidates: list[Candidate] = field(default_factory=list)
    #: Clauses stating a bar that no observed metric answers.
    clauses_without_measurement: list[str] = field(default_factory=list)
    #: Metrics measured that no clause mentions. H16.
    metrics_without_clause: list[str] = field(default_factory=list)
    already_decided: list[tuple[str, str, str]] = field(default_factory=list)
    #: Available for a human to pair explicitly, where no name matched either way.
    unpaired_clauses: list[tuple[str, str, str | None]] = field(default_factory=list)
    unpaired_metrics: list[str] = field(default_factory=list)

    @property
    def pending(self) -> list[Candidate]:
        decided = {(m, c) for m, c, _ in self.already_decided}
        return [c for c in self.candidates if (c.metric, c.clause_ref) not in decided]


def propose(session: Session, *, product: Product) -> Proposal:
    """Candidate pairings, the gaps either side of them, and what is already decided."""
    clauses = session.execute(
        select(Clause).where(Clause.product_id == product.id, Clause.status == "active")
    ).scalars().all()
    measurable = [c for c in clauses if c.comparator is not None and c.value is not None]

    observed = session.execute(
        select(Observation.metric).where(Observation.product_id == product.id).distinct()
    ).scalars().all()

    decided = [
        (b.metric, b.clause_ref, b.decision)
        for b in session.execute(
            select(Binding).where(Binding.product_id == product.id)
        ).scalars()
    ]

    proposal = Proposal(already_decided=decided)
    by_exact = {c.metric: c for c in measurable if c.metric}
    by_normalised = {
        normalise_metric_name(c.metric or c.label or ""): c for c in measurable
    }

    matched_clauses: set[str] = set()
    for metric in sorted(observed):
        clause = by_exact.get(metric)
        matched_by = "exact metric name" if clause else None
        if clause is None:
            clause = by_normalised.get(normalise_metric_name(metric))
            matched_by = "normalised metric name" if clause else None
        if clause is None:
            proposal.metrics_without_clause.append(metric)
            continue

        matched_clauses.add(clause.ref)
        count, latest = _series(session, product.id, metric)
        proposal.candidates.append(
            Candidate(
                metric=metric,
                clause_ref=clause.ref,
                clause_statement=clause.statement,
                clause_bar=_bar(clause),
                matched_by=matched_by or "unknown",
                observations=count,
                latest_value=latest,
            )
        )

    proposal.clauses_without_measurement = sorted(
        c.ref for c in measurable if c.ref not in matched_clauses
    )
    # What a human can pair by hand. A clause read from prose carries no metric name — the
    # spec adapter refuses to invent one — so it will never appear as a name match, and
    # without these two lists there would be nothing on screen to pair it with.
    proposal.unpaired_clauses = [
        (c.ref, c.statement, _bar(c)) for c in measurable if c.ref not in matched_clauses
    ]
    proposal.unpaired_metrics = list(proposal.metrics_without_clause)
    return proposal


def decide(
    session: Session,
    *,
    product: Product,
    metric: str,
    clause_ref: str,
    decision: str,
    by: str,
    note: str | None = None,
    metric_definition: dict | None = None,
) -> Binding:
    """Record a human's decision about one proposed pairing.

    `by` is required and unvalidated here, which is a deliberate limit of phases 1 to 3:
    this is attribution, not authentication. It records who says they decided, so the audit
    trail names someone, and it is trivially spoofable from a CLI used by one operator. Real
    identity arrives with HTTP in phase 4, where the surface makes it load-bearing.
    """
    if decision not in (CONFIRMED, REJECTED):
        raise ValueError(f"a decision is confirmed or rejected, not {decision!r}")
    if not by:
        raise ValueError("a decision needs a decider: an unattributed binding is not an "
                         "assertion anyone made")

    # The components are validated, not the pairing. An earlier version required the exact
    # pair to appear in `propose`, which made a product whose bars are all stated in prose
    # permanently unable to leave the gate: such clauses carry no metric name, so nothing
    # name-matched and nothing could be confirmed. Pairing a measured number with a promise
    # the source never named is the main thing step 5 is *for*, so what has to be true is
    # that the number exists, the promise exists, and the promise has a bar.
    measured = session.execute(
        select(func.count())
        .select_from(Observation)
        .where(Observation.product_id == product.id, Observation.metric == metric)
    ).scalar_one()
    if not measured:
        raise UnknownCandidate(
            f"nothing named {metric!r} has been measured for {product.key}. A binding to an "
            f"unmeasured metric asserts something no run supports."
        )

    clause = session.execute(
        select(Clause).where(
            Clause.product_id == product.id,
            Clause.ref == clause_ref,
            Clause.status == "active",
        )
    ).scalars().first()
    if clause is None:
        raise UnknownCandidate(
            f"{product.key} has no active clause {clause_ref!r}. B3 rule 9: a record whose "
            f"target does not resolve is rejected at write time."
        )
    if decision == CONFIRMED and (clause.comparator is None or clause.value is None):
        raise UnknownCandidate(
            f"{clause_ref} states no numeric bar, so a measurement cannot answer it. It is "
            f"held as written and never counted as drift (H6)."
        )

    if decision == CONFIRMED:
        observed_unit = session.execute(
            select(Observation.unit).where(
                Observation.product_id == product.id,
                Observation.metric == metric,
                Observation.unit.is_not(None),
            ).limit(1)
        ).scalar_one_or_none()
        if observed_unit and clause.unit and observed_unit != clause.unit:
            # The gate is the right place to catch this. A p95 measured in milliseconds
            # bound to a bar stated in seconds compares 1200 against 4 and reports a
            # confident breach that is purely a unit error — a wrong finding, which B6 puts
            # at zero tolerance. Refused rather than silently converted, because guessing
            # which of the two is authoritative is how a bar gets quietly rescaled.
            raise UnknownCandidate(
                f"{metric} is measured in {observed_unit} and {clause_ref} states its bar in "
                f"{clause.unit}. Confirming this would compare numbers in different units. "
                f"Fix the metric definition or the clause, then propose again."
            )

    existing = session.execute(
        select(Binding).where(
            Binding.product_id == product.id,
            Binding.metric == metric,
            Binding.clause_ref == clause_ref,
        )
    ).scalars().first()

    if existing is not None:
        # EC-6 in miniature: the first decision stands, and the second is told so rather
        # than silently overwriting a named human's judgement.
        raise UnknownCandidate(
            f"{metric} against {clause_ref} was already {existing.decision} by "
            f"{existing.confirmed_by}. Reverse it explicitly rather than deciding twice."
        )

    row = Binding(
        org_id=product.org_id,
        product_id=product.id,
        metric=metric,
        clause_ref=clause_ref,
        metric_definition=metric_definition,
        decision=decision,
        decision_note=note,
        confirmed_by=by,
    )
    session.add(row)
    # Flushed deliberately. The session runs with autoflush off, so without this the
    # status check that follows queries the table and does not see the decision just
    # made — which left a product sitting at the gate with nothing left to decide.
    session.flush()
    audit.record(
        session,
        org_id=product.org_id,
        actor=by,
        action=audit.BINDING_CONFIRMED if decision == CONFIRMED else audit.BINDING_REJECTED,
        subject=f"clause:{clause_ref}",
        detail={"metric": metric, "note": note, "product": product.key},
    )
    return row


def confirmed_metrics(session: Session, *, product: Product) -> dict[str, str]:
    """Metric to clause ref, for confirmed bindings only.

    What every downstream query joins through. A rejected binding is absent, so a pairing a
    human declined cannot come back as a verdict or a finding.
    """
    rows = session.execute(
        select(Binding.metric, Binding.clause_ref).where(
            Binding.product_id == product.id, Binding.decision == CONFIRMED
        )
    ).all()
    return {metric: ref for metric, ref in rows}


def _series(session: Session, product_id: uuid.UUID, metric: str) -> tuple[int, float | None]:
    rows = session.execute(
        select(Observation.value)
        .where(Observation.product_id == product_id, Observation.metric == metric)
        .order_by(Observation.measured_at.desc())
    ).scalars().all()
    return len(rows), (rows[0] if rows else None)


def _bar(clause: Clause) -> str | None:
    if clause.value is None:
        return None
    if clause.comparator == "between":
        return f"{clause.value:g} to {clause.value_high:g}"
    return f"{clause.comparator} {clause.value:g}"
