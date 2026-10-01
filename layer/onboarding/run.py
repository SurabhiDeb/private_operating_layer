"""Wiring the adapters to the state machine.

The steps that read a source live here rather than in `state.py`, so the state machine
depends on no adapter and an adapter depends on no database. What this module knows is which
adapter serves which `(role, kind)` pair — a registry, so a new source kind is one entry and
no change anywhere else (agnosticism rule R3).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from layer.adapters.base import Expectation, ImportReport, SourceDocument
from layer.adapters.code.enforcement import EnforcementAdapter
from layer.adapters.eval.run_files import RunFilesAdapter
from layer.adapters.repo import RepoHandle
from layer.adapters.spec.file_spec import SpecAdapter
from layer.db.models import Clause, Product, Source
from layer.onboarding import persist, state
from layer.refs.registry import Registry
from sqlalchemy import select


class UnsupportedSource(Exception):
    """A (role, kind) pair with no adapter, named so the gap is obvious."""


@dataclass
class StepResult:
    report: ImportReport
    detail: str


def handle_for(source: Source) -> RepoHandle:
    """A repository pinned to the revision this source recorded.

    `pinned_rev` is used when the source has one, so a re-import reads exactly what the
    first import read. Without it the source pins HEAD now and records what that was, which
    is the only moment a revision is allowed to be chosen (AC-14).
    """
    config = dict(source.config)
    if source.pinned_rev:
        config["rev"] = source.pinned_rev
    handle = RepoHandle.from_config(config)
    source.pinned_rev = handle.rev
    return handle


def documents_for(source: Source, handle: RepoHandle, glob: str | None = None) -> list[SourceDocument]:
    patterns = [glob] if glob else list(source.config.get("globs") or [])
    paths: list[str] = []
    for pattern in patterns:
        paths.extend(handle.list_files(pattern))
    for explicit in source.config.get("files") or []:
        if explicit not in paths:
            paths.append(explicit)
    return [
        SourceDocument(p, handle.read(p), handle.blob_url(p)) for p in dict.fromkeys(paths)
    ]


# -- step 3 ----------------------------------------------------------------------


def import_spec(session: Session, *, product: Product, actor: str) -> StepResult:
    sources = state.require_source(session, product=product, role="spec")
    total = persist.ClauseWrite()
    reports: list[ImportReport] = []

    for source in sources:
        if source.kind not in ("repo", "file"):
            raise UnsupportedSource(f"no spec adapter for kind {source.kind!r}")
        handle = handle_for(source)
        path = source.config["path"]
        report = SpecAdapter().read(
            handle.read(path),
            {**source.config, "ref_prefix": source.config.get("ref_prefix")
             or product.ref_prefix or product.key[:3].upper()},
        )
        written = persist.write_clauses(
            session, product=product, report=report, source_id=source.id, actor=actor
        )
        total.created += written.created
        total.reworded += written.reworded
        total.unchanged += written.unchanged
        reports.append(report)

    merged = _merge(reports, "spec")
    return StepResult(
        merged,
        f"{len(total.created)} new, {len(total.reworded)} reworded, "
        f"{len(total.unchanged)} unchanged",
    )


# -- step 4 ----------------------------------------------------------------------


def backfill(session: Session, *, product: Product, actor: str) -> StepResult:
    sources = state.require_source(session, product=product, role="eval")
    reports: list[ImportReport] = []
    inserted = duplicates = 0

    for source in sources:
        if source.kind in ("repo", "file", "promptfoo"):
            handle = handle_for(source)
            documents = documents_for(source, handle)
        elif source.kind == "langfuse":
            documents = _langfuse_documents(source)
        else:
            raise UnsupportedSource(f"no eval adapter for kind {source.kind!r}")

        report = RunFilesAdapter().read(documents, source.config)
        written = persist.write_observations(
            session, product=product, candidates=report.candidates,
            source_id=source.id, actor=actor, report=report,
        )
        inserted += written.inserted
        duplicates += written.duplicates
        reports.append(report)

    merged = _merge(reports, "eval")
    return StepResult(
        merged,
        f"{inserted} observations stored, {duplicates} duplicates refused, "
        f"{merged.imported} of {merged.enumerated} documents imported",
    )


def _langfuse_documents(source: Source) -> list[SourceDocument]:
    """Built here rather than in the adapter so the client is constructed once, late, and
    only when a Langfuse source actually exists — the package is not a dependency."""
    from langfuse import Langfuse  # noqa: PLC0415

    from layer.adapters.eval.langfuse import LangfuseSource

    return LangfuseSource(Langfuse()).documents(source.config)


# -- step 4b ---------------------------------------------------------------------


def scan_enforcement(session: Session, *, product: Product, actor: str) -> StepResult:
    """What CI checks. Optional: a product with no code source is simply unassessed.

    Reported as unassessed rather than as "CI enforces nothing", which would be a finding
    the Layer has no evidence for.
    """
    sources = state.sources_by_role(session, product=product).get("code", [])
    if not sources:
        empty = ImportReport(source_role="code")
        empty.notes.append(
            "no code source is bound, so enforcement was not assessed. This is not the "
            "same as finding that CI enforces nothing."
        )
        return StepResult(empty, "no code source bound")

    # A clause read from prose carries no metric name — the spec adapter refuses to invent
    # one — so on its own it could never be looked for in CI, and the bar in the alien
    # fixture's gate was invisible for exactly that reason. The confirmed binding is the
    # bridge: it is a human saying which measured number answers that promise, which is also
    # the name the gate is likely to use. Before step 5 such a clause is simply not scanned,
    # which is honest rather than silent: the scan reports what it did not check.
    from layer.onboarding import bindings as binding_gate  # noqa: PLC0415

    bound = {ref: metric for metric, ref in
             binding_gate.confirmed_metrics(session, product=product).items()}

    expectations = []
    unnamed = 0
    for clause in session.execute(
        select(Clause).where(
            Clause.product_id == product.id, Clause.status == "active",
            Clause.value.is_not(None),
        )
    ).scalars():
        metric = clause.metric or bound.get(clause.ref)
        if not metric:
            unnamed += 1
            continue
        expectations.append(Expectation(
            metric=metric, comparator=clause.comparator, value=clause.value,
            value_high=clause.value_high, unit=clause.unit, label=clause.label,
        ))

    reports: list[ImportReport] = []
    found = 0
    for source in sources:
        handle = handle_for(source)
        documents = documents_for(source, handle)
        report = EnforcementAdapter().read(documents, source.config, expectations)
        found += persist.write_enforcement(
            session, product=product, candidates=report.candidates,
            source_id=source.id, actor=actor, report=report,
        )
        reports.append(report)

    merged = _merge(reports, "code")
    unchecked = sorted({e.metric for e in expectations} - {c.metric for c in merged.candidates})
    detail = (
        f"{found} enforcement fact(s); {len(unchecked)} stated metric(s) not checked anywhere"
        + (f": {', '.join(unchecked)}" if unchecked else "")
    )
    if unnamed:
        detail += (
            f"; {unnamed} clause(s) with a bar but no bound metric were not looked for, "
            f"because nothing names the number that answers them yet"
        )
        merged.notes.append(
            f"{unnamed} clause(s) state a bar whose metric is unnamed. Confirm a binding for "
            f"them and re-run the scan, or their enforcement cannot be assessed."
        )
    return StepResult(merged, detail)


# -- citations -------------------------------------------------------------------


def registry_for(session: Session, *, product: Product) -> Registry:
    """A resolver registry for this product's sources.

    Built per product and per request, never shared, because a resolver closes over one
    tenant's repository and a shared registry would be a route for it to answer another
    tenant's ref.
    """
    from layer.adapters.repo import repo_resolvers

    registry = Registry()
    for role in ("spec", "code", "eval"):
        for source in state.sources_by_role(session, product=product).get(role, []):
            if source.kind == "repo" and source.pinned_rev:
                registry.register_all(repo_resolvers(handle_for(source)))
    return registry


def _merge(reports: list[ImportReport], role: str) -> ImportReport:
    merged = ImportReport(source_role=role)
    for report in reports:
        merged.candidates.extend(report.candidates)
        merged.skipped.extend(report.skipped)
        merged.failed.extend(report.failed)
        merged.unmeasured.extend(report.unmeasured)
        merged.notes.extend(report.notes)
        merged.enumerated += report.enumerated
        merged.imported += report.imported
    return merged
