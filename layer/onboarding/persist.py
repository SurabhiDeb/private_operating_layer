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
from layer.core import audit
from layer.db.models import Clause, EnforcementFact, Observation, Product
from layer.onboarding.identity import NEW, REWORDED, UNCHANGED, resolve, statement_hash


@dataclass
class ClauseWrite:
    created: list[str] = field(default_factory=list)
    reworded: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def touched(self) -> int:
        return len(self.created) + len(self.reworded)


@dataclass
class ObservationWrite:
    inserted: int = 0
    duplicates: int = 0

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

    detail = {
        "inserted": result.inserted,
        "duplicates_refused": result.duplicates,
        "metrics": sorted({c.metric for c in candidates}),
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
