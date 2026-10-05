"""The seven steps, as the only way content enters the Layer.

PRD B2: "The Layer knows nothing until a product is onboarded. There is no seeded content,
no demo product and no default clause set. Every record in every table traces back to a
source a human bound."

That is enforced here rather than remembered. Each step has a precondition, the status moves
only forward and only through a recorded event, and `require_live` is the single function
every proposal path must call — B3 rule 8 is then structural, and a future capability cannot
forget it by not knowing about it.

    1  register        human    status registering
    2  bind sources    human    >= 1 spec and >= 1 eval -> sources_bound
    3  import spec     Layer    clauses, all provisional / not_measured
    4  backfill evals  Layer    observations, every run, none skipped
    5  propose bindings, then STOP          -> assertions_confirmed
    6  measure         Layer    verdicts      -> live
    7  findings, then proposals

Step 5 is the gate. A product that stops at step 4 is still useful — it answers metric
history questions and surfaces coverage gaps — and it proposes nothing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from layer.core import audit, freshness
from layer.core.durations import format_duration
from layer.core.errors import NotLive, UnknownProduct
from layer.db.models import Binding, Clause, Observation, Product, Source
from layer.onboarding import bindings as binding_gate
from layer.verdicts.rules import Measurement, judge, state_for

REGISTERING = "registering"
SOURCES_BOUND = "sources_bound"
ASSERTIONS_CONFIRMED = "assertions_confirmed"
LIVE = "live"

#: Status to the step number it corresponds to, so a status change cannot leave the step
#: counter disagreeing with it.
_STEP = {REGISTERING: 1, SOURCES_BOUND: 2, ASSERTIONS_CONFIRMED: 5, LIVE: 6}


class StepNotReady(Exception):
    """A step attempted before its precondition holds, naming what is missing."""


@dataclass
class Status:
    product: str
    status: str
    step: int
    sources: dict[str, int]
    clauses: int
    measurable_clauses: int
    observations: int
    confirmed_bindings: int
    pending_candidates: int
    verdicts: dict[str, int]
    #: PRD B1's `stale_sources`. Empty is the required state; a non-empty list is shown in
    #: words, because AC-29 is explicit that a field alone does not count.
    stale_sources: list[dict] = dc_field(default_factory=list)

    def describe(self) -> str:
        roles = ", ".join(f"{r}×{n}" for r, n in sorted(self.sources.items())) or "none"
        lines = (
            f"{self.product}: {self.status} (step {self.step})\n"
            f"  sources       {roles}\n"
            f"  clauses       {self.clauses} ({self.measurable_clauses} with a numeric bar)\n"
            f"  observations  {self.observations}\n"
            f"  bindings      {self.confirmed_bindings} confirmed, "
            f"{self.pending_candidates} awaiting a decision\n"
            f"  verdicts      " + (", ".join(f"{k} {v}" for k, v in sorted(self.verdicts.items())) or "none")
        )
        for entry in self.stale_sources:
            lines += f"\n  OVERDUE       {entry['note']}"
        return lines


# -- step 1 ----------------------------------------------------------------------


def register(
    session: Session,
    *,
    org_id: uuid.UUID,
    key: str,
    name: str,
    pattern: str | None = None,
    ref_prefix: str | None = None,
    actor: str,
) -> Product:
    """Nothing exists in the Layer until a row is here.

    `pattern` is a hint and is never validated against a list. AC-18 requires an
    unrecognised or absent pattern to onboard with no defaults applied rather than be
    refused, so there is deliberately nothing here to refuse it.
    """
    product = Product(
        org_id=org_id, key=key, name=name, pattern=pattern,
        ref_prefix=ref_prefix, status=REGISTERING, onboarding_step=1,
    )
    session.add(product)
    session.flush()
    audit.record(
        session, org_id=org_id, actor=actor, action=audit.PRODUCT_REGISTERED,
        subject=f"product:{key}", detail={"name": name, "pattern": pattern},
    )
    return product


def get(session: Session, *, key: str) -> Product:
    product = session.execute(
        select(Product).where(Product.key == key)
    ).scalars().first()
    if product is None:
        # B3 rule 9: a record whose product does not resolve is rejected at write time
        # rather than cleaned up later.
        raise UnknownProduct(
            f"no product named {key!r} is registered. Nothing can be imported, measured or "
            f"proposed for a product that does not exist."
        )
    return product


# -- step 2 ----------------------------------------------------------------------


def bind_source(
    session: Session,
    *,
    product: Product,
    role: str,
    kind: str,
    config: dict,
    pinned_rev: str | None = None,
    freshness_window: timedelta | None = None,
    actor: str,
) -> Source:
    """PRD B11's `bind_source`, which is human-only and never an MCP tool.

    `freshness_window` is stated here because this is the only place a human says how
    often this source is expected to speak. It is optional: a source whose cadence
    nobody has stated is held to no window and its age is reported rather than used to
    degrade a verdict. Silence about the cadence is not a claim that the data is fresh.
    """
    source = Source(
        org_id=product.org_id, product_id=product.id, role=role, kind=kind,
        config=config, pinned_rev=pinned_rev, status="healthy",
        freshness_window=freshness_window,
    )
    session.add(source)
    session.flush()
    audit.record(
        session, org_id=product.org_id, actor=actor, action=audit.SOURCE_BOUND,
        subject=f"source:{source.id}",
        detail={
            "role": role, "kind": kind, "pinned_rev": pinned_rev,
            "product": product.key,
            "freshness_window": (
                format_duration(freshness_window) if freshness_window else None
            ),
        },
    )
    _advance(session, product, actor)
    return source


def sources_by_role(session: Session, *, product: Product) -> dict[str, list[Source]]:
    out: dict[str, list[Source]] = {}
    for source in session.execute(
        select(Source).where(Source.product_id == product.id)
    ).scalars():
        out.setdefault(source.role, []).append(source)
    return out


def require_source(session: Session, *, product: Product, role: str) -> list[Source]:
    found = sources_by_role(session, product=product).get(role, [])
    if not found:
        raise StepNotReady(
            f"{product.key} has no {role} source bound. Step 2 requires at least one spec "
            f"source and one eval source, because without them there is nothing to reason "
            f"about."
        )
    return found


# -- step 6 ----------------------------------------------------------------------


def measure(
    session: Session, *, product: Product, actor: str, now: datetime | None = None
) -> dict[str, int]:
    """Give every clause a verdict, and move the product to `live`.

    Every clause gets one, including `not_applicable` and `not_measured` — PRD B6 sets
    "clauses with a verdict" at 100%, so silence is not an option. Only confirmed bindings
    are joined through, so a pairing a human declined cannot produce a verdict.

    **This is also where staleness is applied.** A measurement outside its source's
    `freshness_window` degrades `met` and `missed` to `cannot_confirm` rather than holding
    its last good value (B3 rule 12, AC-28), and the pass brings every source's `status`
    and `overdue_since` in line with the clock on its way out.

    `now` is the seam a test needs to advance the clock past a window, and defaults to the
    database's own. It is not an override for operational use: passing a time that is not
    now would write verdicts nobody can reproduce.
    """
    if product.status not in (ASSERTIONS_CONFIRMED, LIVE):
        pending = len(binding_gate.propose(session, product=product).pending)
        raise StepNotReady(
            f"{product.key} is {product.status}. Step 6 runs after step 5, and "
            f"{pending} candidate binding(s) are still awaiting a decision. "
            f"Measuring now would give verdicts to pairings nobody confirmed."
        )

    bound = binding_gate.confirmed_metrics(session, product=product)
    by_ref: dict[str, str] = {ref: metric for metric, ref in bound.items()}

    at = now or freshness.clock(session)
    windows = freshness.windows_for(session, product.id)

    counts: dict[str, int] = {}
    degraded: list[str] = []
    for clause in session.execute(
        select(Clause).where(Clause.product_id == product.id, Clause.status == "active")
    ).scalars():
        metric = by_ref.get(clause.ref)
        latest = _latest(session, product.id, metric) if metric else None
        window = windows.get(latest.source_id) if latest is not None else None
        judgement = judge(
            kind=clause.kind,
            comparator=clause.comparator,
            value=clause.value,
            value_high=clause.value_high,
            latest=latest,
            staleness=freshness.staleness_of(
                latest.measured_at if latest else None,
                window=window.window if window else None,
                now=at,
                source=window.label if window else None,
            ),
        )
        clause.verdict = judgement.verdict
        clause.verdict_at = at
        clause.state = state_for(
            has_measurement=latest is not None, ratified=clause.state == "ratified"
        )
        counts[judgement.verdict] = counts.get(judgement.verdict, 0) + 1
        if judgement.degraded_from:
            degraded.append(f"{clause.ref} would have read {judgement.degraded_from}")

    # On the way out, not on the way in: the verdicts above were judged against `at`, and
    # a status refreshed first would be describing a different moment than they were.
    sources = freshness.refresh(session, product=product, now=at)

    detail = {"verdicts": counts, "bound_metrics": len(bound), "sources": sources}
    if degraded:
        # Named, not counted. A verdict that moved because its evidence aged is the one
        # thing an operator has to be able to find after the fact (B5 item 10).
        detail["degraded_for_staleness"] = degraded
    audit.record(
        session, org_id=product.org_id, actor=actor, action=audit.VERDICTS_COMPUTED,
        subject=f"product:{product.key}", detail=detail,
    )
    _set_status(session, product, LIVE, actor)
    return counts


# -- the gate --------------------------------------------------------------------


def require_live(session: Session, *, product: Product) -> None:
    """B3 rule 8, in one place.

    "Never propose anything for a product that is not `live`. A proposal requires at least
    one confirmed binding on that product." Called by every proposal path, so a capability
    added later cannot omit the check by not knowing it exists.
    """
    if product.status != LIVE:
        raise NotLive(
            f"{product.key} is {product.status}, not live. Findings and refusals are "
            f"available; proposals are not. Before step 5 the Layer does not know which "
            f"number answers which promise, so anything it proposed would be invented."
        )
    if not binding_gate.confirmed_metrics(session, product=product):
        raise NotLive(
            f"{product.key} is live but has no confirmed binding, so there is no assertion "
            f"for a proposal to rest on."
        )


# -- status ----------------------------------------------------------------------


def _advance(session: Session, product: Product, actor: str) -> None:
    """Move the status forward if its precondition now holds. Never backwards."""
    roles = sources_by_role(session, product=product)
    if product.status == REGISTERING and roles.get("spec") and roles.get("eval"):
        _set_status(session, product, SOURCES_BOUND, actor)


def refresh(session: Session, *, product: Product, actor: str) -> str:
    """Recompute the status after a step that may have satisfied a gate."""
    if product.status == SOURCES_BOUND:
        proposal = binding_gate.propose(session, product=product)
        confirmed = [d for d in proposal.already_decided if d[2] == binding_gate.CONFIRMED]
        if confirmed and not proposal.pending:
            _set_status(session, product, ASSERTIONS_CONFIRMED, actor)
    return product.status


def _set_status(session: Session, product: Product, status: str, actor: str) -> None:
    if status == product.status:
        return
    was = product.status
    product.status = status
    product.onboarding_step = _STEP[status]
    audit.record(
        session, org_id=product.org_id, actor=actor, action=audit.PRODUCT_STATUS_CHANGED,
        subject=f"product:{product.key}", detail={"from": was, "to": status},
    )


def status(session: Session, *, product: Product) -> Status:
    roles = {r: len(v) for r, v in sources_by_role(session, product=product).items()}
    clauses = session.execute(
        select(Clause).where(Clause.product_id == product.id, Clause.status == "active")
    ).scalars().all()
    observations = session.execute(
        select(func.count()).select_from(Observation).where(
            Observation.product_id == product.id
        )
    ).scalar_one()
    proposal = binding_gate.propose(session, product=product)
    verdicts: dict[str, int] = {}
    for clause in clauses:
        verdicts[clause.verdict] = verdicts.get(clause.verdict, 0) + 1

    return Status(
        product=product.key,
        status=product.status,
        step=product.onboarding_step,
        sources=roles,
        clauses=len(clauses),
        measurable_clauses=sum(
            1 for c in clauses if c.comparator is not None and c.value is not None
        ),
        observations=observations,
        confirmed_bindings=session.execute(
            select(func.count()).select_from(Binding).where(
                Binding.product_id == product.id, Binding.decision == binding_gate.CONFIRMED
            )
        ).scalar_one(),
        pending_candidates=len(proposal.pending),
        verdicts=verdicts,
        stale_sources=freshness.stale_sources(session, product=product),
    )


def _latest(session: Session, product_id: uuid.UUID, metric: str) -> Measurement | None:
    row = session.execute(
        select(Observation)
        .where(Observation.product_id == product_id, Observation.metric == metric)
        .order_by(Observation.measured_at.desc(), Observation.id.desc())
        .limit(1)
    ).scalars().first()
    if row is None:
        return None
    return Measurement(
        value=row.value, passed=row.passed, total=row.total, observation_id=row.id,
        measured_at=row.measured_at, source_id=row.source_id,
    )
