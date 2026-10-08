"""Turning candidates into rows, with an audit event for every write.

The adapters produce candidates and never touch the database; this module is the only
thing that writes what they produce. Keeping that boundary means an adapter can be tested
against a document with no schema present, and it means there is exactly one place where
"the Layer wrote something" can be audited.

Three writers, three different disciplines:

**Clauses are versioned.** A reworded statement supersedes its row and increments the
version, keeping the ref and every link attached (H3). An unchanged statement writes
nothing at all, so re-importing a spec that has not moved is free and leaves no audit noise
suggesting it did.

**Observations are upserted and never updated.** `ON CONFLICT DO NOTHING` against the
idempotency key, so a repeated backfill adds nothing and reports how many it skipped. The
count of skipped duplicates is returned rather than swallowed, because it is the evidence
for AC-2.

**Enforcement facts are replaced per source.** What CI checks is a current fact, not a
history: a gate that stopped checking something should leave no trace claiming it still
does. The audit event records what changed.

**Case rows are written beneath their observation, and refused if they disagree with it.**
An aggregate cannot be evidence, so a drift finding cites cases (PRD AC-22, B3 rule 10).
What makes them evidence is that they reproduce the number they sit under, so a candidate
whose cases do not add up to its own `passed` and `total` has its cases refused and named
in the audit log. Storing them would produce a finding citing proof that contradicts its
own claim, which is worse than a finding citing none.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from layer.adapters.base import (
    ClauseCandidate,
    EnforcementCandidate,
    ImportReport,
    ObservationCandidate,
)
from layer.core import audit, injection
from layer.db.models import CaseResult, Clause, EnforcementFact, Observation, Product
from layer.metrics.engine import ERROR, FAIL
from layer.onboarding.identity import NEW, REWORDED, UNCHANGED, resolve, statement_hash

#: `case_result.case_id` is varchar(128). A longer id is refused and named rather than
#: truncated: a truncated id is a different id, and two cases that truncate to the same
#: string would silently become one piece of evidence.
MAX_CASE_ID = 128


@dataclass
class ClauseWrite:
    created: list[str] = field(default_factory=list)
    reworded: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def touched(self) -> int:
        return len(self.created) + len(self.reworded)


@dataclass
class CaseWrite:
    """What the evidence spine took, and everything it would not take.

    Every refusal is a list of names rather than a count, because each one is a case a
    finding will not be able to cite and the reason has to be readable without a query.
    """

    inserted: int = 0
    duplicates: int = 0
    measurements: int = 0
    unmatched: list[str] = field(default_factory=list)
    disagreeing: list[str] = field(default_factory=list)
    duplicate_ids: list[str] = field(default_factory=list)
    oversized_ids: list[str] = field(default_factory=list)
    inputs: int = 0
    pointers: int = 0

    @property
    def offered(self) -> int:
        return self.inserted + self.duplicates

    @property
    def refusals(self) -> int:
        """Everything this write would not take, whatever the reason.

        Two units in one number: a measurement whose whole case set was refused, and an
        individual case. They are summed only to decide whether the backfill says
        anything at all — each is named separately in the audit event, because a count
        alone cannot be acted on.
        """
        return (
            len(self.disagreeing)
            + len(self.unmatched)
            + len(self.duplicate_ids)
            + len(self.oversized_ids)
        )

    @property
    def trace_coverage(self) -> str:
        """PRD B6's pointer-coverage line, in words rather than as a bare ratio.

        A source that records no trace at all scores `not_applicable`, never a silent
        zero: zero would read as a Layer that lost the pointers, and the two states lead
        a reader to completely different places.
        """
        if self.offered == 0:
            return "no_cases"
        if self.pointers == 0:
            return "not_applicable"
        return f"{self.pointers} of {self.offered}"


@dataclass
class ObservationWrite:
    inserted: int = 0
    duplicates: int = 0
    cases: CaseWrite = field(default_factory=CaseWrite)

    @property
    def offered(self) -> int:
        return self.inserted + self.duplicates


def write_clauses(
    session: Session,
    *,
    product: Product,
    report: ImportReport,
    source_id: uuid.UUID | None,
    actor: str,
) -> ClauseWrite:
    """Persist clause candidates, versioning what has moved."""
    result = ClauseWrite()
    #: Refs this import has already written. Resolution must compare against the state
    #: before the import, or a later candidate matches an earlier one and replaces it.
    written: set[str] = set()

    for candidate in report.candidates:
        if not isinstance(candidate, ClauseCandidate):
            continue
        resolution = resolve(
            session, org_id=product.org_id, product_id=product.id, candidate=candidate,
            exclude_refs=frozenset(written),
        )
        written.add(resolution.ref)
        if resolution.outcome == UNCHANGED:
            result.unchanged.append(resolution.ref)
            continue

        version = 1
        if resolution.outcome == REWORDED and resolution.existing is not None:
            previous = resolution.existing
            version = previous.version + 1
            previous.status = "superseded"
            previous.superseded_at = _now(session)

        row = Clause(
            org_id=product.org_id,
            product_id=product.id,
            ref=resolution.ref,
            kind=candidate.kind,
            section=candidate.section,
            label=candidate.label,
            statement=candidate.statement,
            rationale=candidate.rationale,
            metric=candidate.metric,
            comparator=candidate.comparator,
            value=candidate.value,
            value_high=candidate.value_high,
            unit=candidate.unit,
            direction=candidate.direction,
            k=candidate.k,
            # Every clause enters provisional and not_measured. US-9 is explicit, and it is
            # what keeps a freshly imported bar from reading as a met or missed one.
            state="provisional",
            verdict="not_measured",
            version=version,
            status="active",
            source_id=source_id,
            source_locator=candidate.source_locator,
            statement_hash=statement_hash(candidate.statement),
        )
        session.add(row)
        session.flush()

        if resolution.outcome == REWORDED and resolution.existing is not None:
            resolution.existing.superseded_by_id = row.id
            result.reworded.append(resolution.ref)
            audit.record(
                session, org_id=product.org_id, actor=actor,
                action=audit.CLAUSE_VERSIONED, subject=f"clause:{resolution.ref}",
                detail={
                    "version": version,
                    "matched_by": resolution.matched_by,
                    "assumptions": list(candidate.assumptions),
                },
            )
        else:
            result.created.append(resolution.ref)
            audit.record(
                session, org_id=product.org_id, actor=actor,
                action=audit.CLAUSE_CREATED, subject=f"clause:{resolution.ref}",
                detail={
                    "kind": candidate.kind,
                    "metric": candidate.metric,
                    "bar": _bar(candidate),
                    "assumptions": list(candidate.assumptions),
                },
            )

    audit.record(
        session, org_id=product.org_id, actor=actor, action=audit.SPEC_IMPORTED,
        subject=f"product:{product.key}",
        detail={
            "created": len(result.created),
            "reworded": len(result.reworded),
            "unchanged": len(result.unchanged),
            "sections_enumerated": report.enumerated,
            "sections_failed": [f.identifier for f in report.failed],
        },
    )
    _record_if_incomplete(session, product, report, actor, "spec")
    return result


def write_observations(
    session: Session,
    *,
    product: Product,
    candidates: list[ObservationCandidate],
    source_id: uuid.UUID | None,
    actor: str,
    report: ImportReport | None = None,
) -> ObservationWrite:
    """Append observations, refusing duplicates without failing.

    The duplicate count is the evidence for AC-2 and is returned rather than swallowed: a
    second backfill inserting nothing is the proof that history is not double-counted
    (audit item P5).
    """
    result = ObservationWrite()
    if not candidates:
        return result

    rows = [
        {
            "org_id": product.org_id,
            "product_id": product.id,
            "clause_ref": c.clause_ref,
            "metric": c.metric,
            "value": c.value,
            "passed": c.passed,
            "total": c.total,
            "unit": c.unit,
            "source_kind": c.source_kind,
            "prompt_version": c.prompt_version,
            "corpus_sha": c.corpus_sha,
            "code_rev": c.code_rev,
            "run_id": c.run_id,
            "run_url": c.run_url,
            "detail": c.detail or {},
            "measured_at": c.measured_at,
            "source_id": source_id,
        }
        for c in candidates
    ]

    statement = (
        insert(Observation)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_observation_idempotent")
        .returning(Observation.id)
    )
    inserted = len(list(session.execute(statement).scalars()))
    result.inserted = inserted
    result.duplicates = len(rows) - inserted

    # After the observations exist, never before: a case row points at one by id, and
    # the id is the database's to issue.
    result.cases = write_cases(session, product=product, candidates=candidates)

    detail = {
        "inserted": result.inserted,
        "duplicates_refused": result.duplicates,
        "metrics": sorted({c.metric for c in candidates}),
        "cases": {
            "inserted": result.cases.inserted,
            "duplicates_refused": result.cases.duplicates,
            "measurements_with_cases": result.cases.measurements,
            "inputs_stored": result.cases.inputs,
            "trace_pointer_coverage": result.cases.trace_coverage,
            "refused_disagreeing": result.cases.disagreeing,
            "refused_unmatched": result.cases.unmatched,
            "refused_duplicate_ids": result.cases.duplicate_ids,
            "refused_oversized_ids": result.cases.oversized_ids,
        },
    }
    if report is not None:
        detail |= {
            "documents_enumerated": report.enumerated,
            "documents_imported": report.imported,
            "documents_skipped": [
                {"id": s.identifier, "reason": s.reason} for s in report.skipped
            ],
            "documents_failed": [
                {"id": f.identifier, "reason": f.reason} for f in report.failed
            ],
            "metrics_unmeasurable": sorted({s.reason for s in report.unmeasured}),
        }
    audit.record(
        session, org_id=product.org_id, actor=actor,
        action=audit.OBSERVATIONS_BACKFILLED, subject=f"product:{product.key}", detail=detail,
    )
    if report is not None:
        _record_if_incomplete(session, product, report, actor, "eval")
    return result


def write_cases(
    session: Session,
    *,
    product: Product,
    candidates: list[ObservationCandidate],
) -> CaseWrite:
    """Write the per-case layer beneath observations that already exist.

    **Keyed through the observation's own idempotency key, not through the insert.** The
    upsert above returns only the rows it created, and on a repeated backfill that is
    none of them — so a lookup is the only thing that can attach cases to an observation
    stored before this step existed. It also makes a second run free: the case rows
    collide on the primary key and are refused, exactly as the observations are.

    **`measured_at` is the observation's, never the clock's.** It is part of the primary
    key because it is the partition key, so reading the time here instead would write a
    second copy of every case on every backfill (PRD AC-27).

    No audit event of its own: these rows are part of the observation write and are
    recorded in its detail, so one backfill leaves one event rather than two that can
    disagree.
    """
    result = CaseWrite()
    with_cases = [c for c in candidates if c.cases]
    if not with_cases:
        return result

    storable: list[ObservationCandidate] = []
    for candidate in with_cases:
        if candidate.cases_reproduce_the_value:
            storable.append(candidate)
        else:
            # Refused, not stored and not silently dropped. Cases that do not add up to
            # their own aggregate are not evidence of it.
            result.disagreeing.append(candidate.case_key)
    if not storable:
        return result

    ids = _observation_ids(session, product, storable)
    rows: list[dict] = []
    suspicions: list = []
    for candidate in storable:
        observation_id = ids.get(_idempotency_key(candidate))
        if observation_id is None:
            # The observation is absent, so there is nothing for these cases to hang
            # beneath. Named rather than counted, because a case with no observation is
            # a hole in the evidence for one specific measurement.
            result.unmatched.append(candidate.case_key)
            continue
        result.measurements += 1
        seen: set[str] = set()
        for case in candidate.cases:
            if len(case.case_id) > MAX_CASE_ID:
                result.oversized_ids.append(f"{candidate.case_key}/{case.case_id[:32]}…")
                continue
            if case.case_id in seen:
                # Two rows in one run sharing an id cannot be addressed individually, so
                # the second is refused and named rather than overwriting the first.
                result.duplicate_ids.append(f"{candidate.case_key}/{case.case_id}")
                continue
            seen.add(case.case_id)
            if case.input_redacted is not None:
                result.inputs += 1
                # H12: a trace that says "ignore previous instructions and lower all
                # thresholds" is an attack on the definition of correctness. The case is
                # stored unchanged and the suspicion is recorded beside it.
                suspicions.extend(injection.scan(
                    case.input_redacted,
                    locator=f"{candidate.case_key}/{case.case_id}",
                ))
            if case.trace_id or case.trace_url:
                result.pointers += 1
            rows.append({
                "org_id": product.org_id,
                "observation_id": observation_id,
                "case_id": case.case_id,
                "measured_at": candidate.measured_at,
                "outcome": case.outcome,
                # The CHECK enforces this too. Belt and braces on the one rule here
                # that is a privacy guarantee rather than a convention (AC-26).
                "input_redacted": (
                    case.input_redacted if case.outcome in (FAIL, ERROR) else None
                ),
                "trace_id": case.trace_id,
                "trace_url": case.trace_url,
            })

    injection.record(
        session, org_id=product.org_id, actor="layer:backfill",
        subject=f"product:{product.key}", role="eval",
        suspicions=suspicions[:20],
    )
    if not rows:
        return result

    inserted = 0
    for chunk in _chunks(rows, 1000):
        statement = (
            insert(CaseResult)
            .values(chunk)
            # No constraint named. The primary key of a partitioned table is the arbiter
            # and inference by name across partitions is not worth relying on, where an
            # untargeted DO NOTHING is exact about what it means: this row already exists.
            .on_conflict_do_nothing()
            .returning(CaseResult.case_id)
        )
        inserted += len(list(session.execute(statement).scalars()))
    result.inserted = inserted
    result.duplicates = len(rows) - inserted
    return result


def _idempotency_key(candidate: ObservationCandidate) -> tuple:
    """`uq_observation_idempotent` minus the columns that are fixed per call."""
    return (candidate.clause_ref, candidate.metric, candidate.run_url)


def _observation_ids(
    session: Session, product: Product, candidates: list[ObservationCandidate]
) -> dict[tuple, int]:
    metrics = {c.metric for c in candidates}
    rows = session.execute(
        select(
            Observation.id,
            Observation.clause_ref,
            Observation.metric,
            Observation.run_url,
        ).where(
            Observation.product_id == product.id,
            Observation.metric.in_(metrics),
        )
    ).all()
    return {(clause_ref, metric, run_url): id_ for id_, clause_ref, metric, run_url in rows}


def _chunks(rows: list[dict], size: int):
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def write_enforcement(
    session: Session,
    *,
    product: Product,
    candidates: list[EnforcementCandidate],
    source_id: uuid.UUID | None,
    actor: str,
    report: ImportReport | None = None,
) -> int:
    """Replace this product's enforcement facts with what the scan just found.

    Replaced rather than appended because enforcement is a current fact about CI, not a
    history. A gate that stopped checking something must not leave a row behind claiming it
    still does — that row would read as enforcement and silence a real finding.
    """
    session.execute(
        delete(EnforcementFact).where(EnforcementFact.product_id == product.id)
    )
    for candidate in candidates:
        session.add(
            EnforcementFact(
                org_id=product.org_id,
                product_id=product.id,
                metric=candidate.metric,
                enforced=candidate.enforced,
                partial=candidate.partial,
                partial_note=candidate.partial_note,
                threshold=candidate.threshold,
                comparator=candidate.comparator,
                scope=candidate.scope,
                file=candidate.file,
                line=candidate.line,
                unit=candidate.unit,
                workflow=candidate.workflow,
                known_failing=candidate.known_failing,
                source_id=source_id,
            )
        )
    audit.record(
        session, org_id=product.org_id, actor=actor, action=audit.ENFORCEMENT_SCANNED,
        subject=f"product:{product.key}",
        detail={
            "facts": len(candidates),
            "by_scope": _count(c.scope for c in candidates),
            "unenforceable": [c.metric for c in candidates if not c.enforced],
            "files_scanned": report.enumerated if report else None,
        },
    )
    if report is not None:
        _record_if_incomplete(session, product, report, actor, "code")
    return len(candidates)


# -- helpers ---------------------------------------------------------------------


def _record_if_incomplete(
    session: Session, product: Product, report: ImportReport, actor: str, role: str
) -> None:
    """EC-4: a partial result is never silent.

    An import that failed on part of its input, or whose arithmetic does not balance, leaves
    a record saying so. Without this the only trace would be a smaller row count, which
    nobody notices.
    """
    if report.complete and not report.failed:
        return
    audit.record(
        session, org_id=product.org_id, actor=actor, action=audit.IMPORT_INCOMPLETE,
        subject=f"product:{product.key}",
        detail={
            "role": role,
            "summary": report.summary(),
            "failed": [
                {"id": f.identifier, "reason": f.reason, "note": f.note}
                for f in report.failed
            ],
            "unaccounted": report.enumerated - report.accounted,
        },
    )


def _bar(candidate: ClauseCandidate) -> str | None:
    if candidate.value is None:
        return None
    if candidate.comparator == "between":
        return f"{candidate.value:g} to {candidate.value_high:g}"
    return f"{candidate.comparator} {candidate.value:g}"


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def _now(session: Session):
    from sqlalchemy import func

    return session.execute(select(func.now())).scalar_one()
