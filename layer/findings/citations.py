"""Resolvers for the refs a finding cites.

Step 3 built the registry and the repository resolvers. These are the database-backed ones:
an observation resolves to the run it came from, a clause to the lines of the document that
stated it, and a case to the conversation behind it where the source recorded one.

Each is built per product and per request, never shared, because a resolver closes over one
tenant's sources. A process-wide registry would be a route for one tenant's repository to
answer another tenant's ref, and refs are not globally unique (H11).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from sqlalchemy import func

from layer.adapters.repo import RepoHandle, repo_resolvers
from layer.db.models import CaseResult, Clause, Observation, Source
from layer.findings import traces
from layer.refs.ref import Ref
from layer.refs.registry import Registry


def registry_for(session: Session, *, product) -> Registry:
    """Every resolver this product's findings might need."""
    registry = Registry()
    sources = {
        source.role: source
        for source in session.execute(
            select(Source).where(Source.product_id == product.id)
        ).scalars()
    }

    # File and commit refs, from whichever source pins a revision. The code source is
    # preferred for files because that is where an enforcement finding points.
    for role in ("code", "spec", "eval"):
        source = sources.get(role)
        if source is None or source.kind != "repo" or not source.pinned_rev:
            continue
        handle = RepoHandle.from_config({**source.config, "rev": source.pinned_rev})
        registry.register_all(repo_resolvers(handle))
        break

    registry.register("obs", _observation_resolver(session, product.id))
    registry.register(
        "case",
        _case_resolver(session, product.id, traces.retention_for(session, product.id)),
    )

    spec = sources.get("spec")
    if spec is not None and spec.kind == "repo" and spec.pinned_rev:
        handle = RepoHandle.from_config({**spec.config, "rev": spec.pinned_rev})
        registry.register(
            "clause", _clause_resolver(session, product.id, handle, spec.config.get("path"))
        )
    return registry


def _observation_resolver(session: Session, product_id: uuid.UUID):
    def resolve(ref: Ref) -> str | None:
        if not ref.id.isdigit():
            return None
        return session.execute(
            select(Observation.run_url).where(
                Observation.product_id == product_id, Observation.id == int(ref.id)
            )
        ).scalar_one_or_none()

    return resolve


def _case_resolver(session: Session, product_id: uuid.UUID, retention: dict):
    """`case:<observation_id>/<case_id>` to the trace, or to the run that holds it.

    Three things make this resolver shaped the way it is.

    **It falls back to the run.** The trace is the best place to land and most sources
    do not record one, so a case with no pointer resolves to the run record that
    contains it rather than landing in `unresolved`. The case is stored, the outcome is
    stored, and the run document names it — a citation a human can follow, which is what
    AC-7 asks for.

    **It will not hand over a trace the source no longer keeps.** Past the source's
    declared retention the link goes to the run instead. A finding that says the body is
    gone while its own citation offers the dead link breaks B3 rule 11 in the space of
    one screen, so the rule is applied in both places from the same module.

    **It reads the stored row rather than trusting the ref.** A ref naming a case that
    was never stored resolves to nothing and is displayed in `unresolved`, because a
    citation that does not resolve is reported, never invented (B3 rule 7, AC-7).
    """

    def resolve(ref: Ref) -> str | None:
        observation_id, _, case_id = ref.id.partition("/")
        if not observation_id.isdigit() or not case_id:
            return None
        row = session.execute(
            select(
                CaseResult.trace_id,
                CaseResult.trace_url,
                CaseResult.measured_at,
                Observation.run_url,
                Observation.source_id,
            )
            .join(Observation, Observation.id == CaseResult.observation_id)
            .where(
                Observation.product_id == product_id,
                CaseResult.observation_id == int(observation_id),
                CaseResult.case_id == case_id,
            )
        ).first()
        if row is None:
            return None
        state = traces.state_of(
            trace_id=row.trace_id,
            trace_url=row.trace_url,
            measured_at=row.measured_at,
            retention=retention.get(row.source_id),
            now=session.execute(select(func.now())).scalar_one() if retention else None,
        )
        return row.trace_url if state == traces.AVAILABLE else row.run_url

    return resolve


def _clause_resolver(session: Session, product_id: uuid.UUID, handle: RepoHandle, path):
    """A clause resolves to the lines that stated it, not merely to the file.

    `source_locator` holds the line the importer read it from, so a reader lands on the
    sentence rather than on a document and a search box.
    """

    def resolve(ref: Ref) -> str | None:
        row = session.execute(
            select(Clause.source_locator).where(
                Clause.product_id == product_id,
                Clause.ref == ref.id,
                Clause.status == "active",
            )
        ).scalar_one_or_none()
        if path is None:
            return None
        line = None
        if row and row.startswith("L") and row[1:].split("-")[0].isdigit():
            line = int(row[1:].split("-")[0])
        return handle.blob_url(path, line)

    return resolve
