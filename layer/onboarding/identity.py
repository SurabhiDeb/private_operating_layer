"""Deciding whether a clause read from a source is one the Layer already holds.

AC-1: "re-importing after a human rewords a statement does not change its ref". That is
the whole of this module, and it is load-bearing for everything downstream — PRD B2 calls
stable identity "the load-bearing requirement. Without stable identity there is nothing to
link to, and nothing else in this spec works."

Identity therefore cannot be the statement text. Three signals are tried in falling order
of confidence:

1. **The metric name.** Far more stable than a sentence, and it is also what the eval
   source calls the number, so two systems already agree on it.
2. **The recorded identity key**, held in `clause_identity` — normally section plus metric,
   otherwise section plus a hash of the normalised text. Kept in its own table because
   clause rows are versioned and identity has to outlive any single version of the text.
3. **Similarity of the statement.** Catches a genuine rewrite that moved sections and
   dropped the metric name. Deterministic, using difflib rather than embeddings, so AC-1
   holds without a model provider being reachable — EC-10 requires failing closed, and an
   identity decision that depends on a network call is a bad place to be. The `embedding`
   column exists and pgvector is installed, so this can be swapped for a vector search
   later without changing what the function promises.

Where nothing matches, the clause is new. Where something matches and the text has changed,
the ref survives, the version increments and every link stays attached (H3).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer.adapters.base import ClauseCandidate
from layer.db.models import Clause, ClauseIdentity

#: Above this, two statements are the same promise differently worded. Chosen to sit well
#: clear of the reference specs' distinct clauses, whose nearest pair scores far below it.
SIMILARITY = 0.82

NEW = "new"
UNCHANGED = "unchanged"
REWORDED = "reworded"


@dataclass(frozen=True)
class Resolution:
    ref: str
    outcome: str
    existing: Clause | None = None
    matched_by: str | None = None


def statement_hash(statement: str) -> str:
    return hashlib.sha256(normalise(statement).encode()).hexdigest()


def normalise(text: str) -> str:
    """Lowercase, collapse punctuation and whitespace.

    So that a statement differing only in a comma or a capital is recognised as unchanged
    and does not spend a version number.
    """
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def resolve(
    session: Session,
    *,
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    candidate: ClauseCandidate,
    exclude_refs: frozenset[str] = frozenset(),
) -> Resolution:
    """Which clause this candidate is, and whether its text has moved.

    `exclude_refs` holds clauses written by the import currently running. Resolution
    compares against the state *before* this import, because a candidate matching a row its
    own run just inserted is not a rewrite — it is two different promises being merged, and
    the second one silently replaces the first.
    """
    existing = _by_metric(session, product_id, candidate, exclude_refs)
    matched_by = "metric" if existing else None

    if existing is None:
        existing = _by_identity_key(session, product_id, candidate, exclude_refs)
        matched_by = "identity_key"
    if existing is None:
        existing = _by_similarity(session, product_id, candidate, exclude_refs)
        matched_by = "similarity"

    if existing is None:
        return Resolution(
            ref=_allocate_ref(session, org_id, product_id, candidate), outcome=NEW
        )

    same = existing.statement_hash == statement_hash(candidate.statement)
    return Resolution(
        ref=existing.ref,
        outcome=UNCHANGED if same else REWORDED,
        existing=existing,
        matched_by=matched_by,
    )


# -- the three signals -----------------------------------------------------------


def _active(product_id: uuid.UUID):
    return (Clause.product_id == product_id) & (Clause.status == "active")


def _by_metric(
    session: Session, product_id: uuid.UUID, candidate: ClauseCandidate,
    exclude_refs: frozenset[str],
):
    if not candidate.metric:
        return None
    row = session.execute(
        select(Clause).where(_active(product_id), Clause.metric == candidate.metric)
    ).scalars().first()
    return None if row is not None and row.ref in exclude_refs else row


def _by_identity_key(
    session: Session, product_id: uuid.UUID, candidate: ClauseCandidate,
    exclude_refs: frozenset[str],
):
    ref = session.execute(
        select(ClauseIdentity.ref).where(
            ClauseIdentity.product_id == product_id,
            ClauseIdentity.identity_key == candidate.identity_key,
        )
    ).scalar_one_or_none()
    if ref is None or ref in exclude_refs:
        return None
    return session.execute(
        select(Clause).where(_active(product_id), Clause.ref == ref)
    ).scalars().first()


def _by_similarity(
    session: Session, product_id: uuid.UUID, candidate: ClauseCandidate,
    exclude_refs: frozenset[str],
):
    """A rewrite that moved section and dropped the metric name.

    Two guards, both learned the hard way. Restricted to clauses of the same kind, because a
    threshold and a non-goal that happen to read alike are not the same promise. And **a
    differing metric name vetoes a match outright**, however similar the text: "p50 latency
    under 1 second" and "p95 latency under 3 seconds" score 0.906, and without the veto the
    second was stored as version 2 of the first — two distinct bars merged into one ref, with
    the p50 clause silently gone. Text similarity is a weak signal and must never outrank an
    explicit disagreement.
    """
    rows = session.execute(
        select(Clause).where(_active(product_id), Clause.kind == candidate.kind)
    ).scalars().all()
    target = normalise(candidate.statement)
    best, score = None, 0.0
    for row in rows:
        if row.ref in exclude_refs:
            continue
        if candidate.metric and row.metric and candidate.metric != row.metric:
            continue
        ratio = SequenceMatcher(None, target, normalise(row.statement)).ratio()
        if ratio > score:
            best, score = row, ratio
    return best if score >= SIMILARITY else None


# -- allocation ------------------------------------------------------------------


def _allocate_ref(
    session: Session, org_id: uuid.UUID, product_id: uuid.UUID, candidate: ClauseCandidate
) -> str:
    """The ref a new clause keeps for life.

    The adapter's hint is used when it is free. A collision is suffixed rather than
    overwritten, because two different promises sharing a ref would make every citation to
    it ambiguous.
    """
    base = candidate.ref_hint or f"C-{candidate.identity_key[:12]}"
    taken = set(
        session.execute(
            select(ClauseIdentity.ref).where(ClauseIdentity.product_id == product_id)
        ).scalars()
    ) | set(
        session.execute(
            select(Clause.ref).where(Clause.product_id == product_id)
        ).scalars()
    )
    ref = base
    suffix = 1
    while ref in taken:
        suffix += 1
        ref = f"{base}-{suffix}"

    session.add(
        ClauseIdentity(
            org_id=org_id,
            product_id=product_id,
            identity_key=candidate.identity_key,
            ref=ref,
        )
    )
    return ref
