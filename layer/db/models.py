"""Tables for the Layer.

Two conventions worth stating, because they are load-bearing rather than stylistic.

**Closed sets get a CHECK, open vocabularies do not.** `product.status` and
`source.role` are fixed by PRD B2 and the Layer branches on them, so the database
enforces them. `pattern`, `source.kind`, `clause.kind` and `link_type` are
deliberately open — the handoff's build order adds a source "as a new `source` row
with a new `kind` and no new concepts", and that must not require a migration. Those
are validated in Python against a registry. No Postgres ENUM anywhere: altering one
is a migration per customer.

**`org_id` is on every tenant table and is never filtered by hand.** Row level
security does it. See `layer/core/db.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from layer.core.db import Base

# PRD B2. Fixed sets the Layer's own logic depends on.
PRODUCT_STATUSES = ("registering", "sources_bound", "assertions_confirmed", "live")
SOURCE_ROLES = ("spec", "eval", "code", "ticket", "production", "decision")

# Open vocabularies, validated in Python so a new one needs no migration.
KNOWN_SOURCE_KINDS = (
    "file", "repo", "langfuse", "braintrust", "promptfoo", "linear", "jira",
    "notion", "slack", "datadog", "prometheus", "custom_mcp",
)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Org(Base):
    """A tenant. The one table that is not itself tenant-scoped, being the registry
    of tenants. Nothing here is customer content."""

    __tablename__ = "org"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = _ts()

    products: Mapped[list[Product]] = relationship(back_populates="org")


class Product(Base):
    """An AI product under observation.

    Nothing exists in the Layer until a row is here (handoff section 6). `pattern`
    is a defaults hint and never a constraint: an unrecognised or absent pattern
    must onboard successfully with no defaults applied rather than be refused
    (AC-18), which is why it is nullable and unconstrained.
    """

    __tablename__ = "product"
    __table_args__ = (
        UniqueConstraint("org_id", "key", name="uq_product_org_key"),
        CheckConstraint(
            "status IN " + str(PRODUCT_STATUSES), name="ck_product_status"
        ),
        CheckConstraint(
            "onboarding_step BETWEEN 1 AND 7", name="ck_product_onboarding_step"
        ),
        Index("ix_product_org", "org_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    pattern: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ref_prefix: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="registering"
    )
    onboarding_step: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = _ts()

    org: Mapped[Org] = relationship(back_populates="products")
    sources: Mapped[list[Source]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class Source(Base):
    """Where every record in every other table came from.

    `config` carries the product-specific shape — a glob, a JSON pointer, a heading
    pattern, a metric definition. That is the whole agnosticism mechanism: shape in
    config, mechanism in the adapter, never a branch on a product key.

    `pinned_rev` is the immutable revision every citation from this source is built
    from. A branch name here is a defect (AC-14).
    """

    __tablename__ = "source"
    __table_args__ = (
        CheckConstraint("role IN " + str(SOURCE_ROLES), name="ck_source_role"),
        Index("ix_source_product_role", "org_id", "product_id", "role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="bound")
    pinned_rev: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts()

    product: Mapped[Product] = relationship(back_populates="sources")


class Binding(Base):
    """The metric-to-clause assertion, confirmed by a named human at a recorded time.

    This is the gate the whole design turns on (PRD B2 step 5, B3 rule 8). Before a
    binding exists the Layer has no idea which number answers which promise, so
    anything it proposes is invented.

    `metric_definition` holds how the number is computed when the source does not
    name one itself — tier 2 in the metric tiering. It is confirmed together with
    the binding because it is itself an assertion about what the number means.
    """

    __tablename__ = "binding"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "product_id", "metric", "clause_ref", name="uq_binding_metric_clause"
        ),
        Index("ix_binding_product", "org_id", "product_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(128), nullable=False)
    clause_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_definition: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confirmed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    confirmed_at: Mapped[datetime] = _ts()




# ===========================================================================
# Migration 2. The record itself: what was promised, what was measured, what is
# enforced, and what has been proposed about it.
# ===========================================================================

# PRD B2. Closed sets the Layer's own logic depends on.
CLAUSE_STATES = ("provisional", "measured", "ratified")
CLAUSE_VERDICTS = ("met", "missed", "cannot_confirm", "not_applicable", "not_measured")
COMPARATORS = (">=", "<=", "==", "between")
SOURCE_KINDS_OF_OBSERVATION = ("eval", "production")
#: `undetermined` is the honest fourth state and it earns its place the same way
#: `cannot_confirm` does for a verdict. A static scan that cannot tell which run set a
#: gate checks must not pick a side: claiming `all_runs` asserts full coverage and hides a
#: real gap, while claiming `latest_only` manufactures a finding that may not exist.
ENFORCEMENT_SCOPES = ("all_runs", "latest_only", "latest_shipped", "undetermined")
PROPOSAL_STATES = ("open", "accepted", "rejected")
ROW_STATUSES = ("active", "superseded")

# Open vocabularies, validated in Python so a new one needs no migration (rule R3).
KNOWN_CLAUSE_KINDS = ("threshold", "rule", "contract", "non_goal", "hard_case")
KNOWN_LINK_TYPES = (
    "governs", "sets", "implements", "asserts", "enforces", "observes", "promises",
    "decides", "supersedes",
)
#: A clause's unit decides how its value is read and rendered. Open, because the next
#: product will have one nobody listed. `ratio` means 0..1; `percent` means 0..100.
KNOWN_UNITS = ("ratio", "percent", "count", "duration_ms", "duration_s", "currency",
               "score", "tokens", "none")
KNOWN_DIRECTIONS = ("higher_is_better", "lower_is_better", "within_band", "none")
KNOWN_PROPOSAL_KINDS = ("clause_change", "new_clause", "link", "eval_case", "ci_change")


class Clause(Base):
    """One promise, with a stable ref that survives rewording.

    Three departures from PRD B2's clause shape, each forced by the fixtures rather
    than invented:

    `unit`, `direction` and `value_high`. Real spec targets include `3% to 8%`,
    `under 1 second`, `under £1,200` and `MRR 0.85`. A `comparator` and a single
    `value` cannot hold a band, and without a unit the Layer cannot tell `0.85` as a
    ratio from `85` as a percentage from `850` as milliseconds. B2 has neither.

    `verdict` is stored but timestamped. B2 lists it as a clause column and AC-13
    requires it to be independently settable, so it is a column — but it is the
    result of the last measurement pass, not a property of the text, so `verdict_at`
    records when it was computed. Without that, a verdict from before the latest
    backfill is indistinguishable from a current one.

    **`version` tracks the promise, not the answer.** Rewording a statement or moving
    a threshold supersedes the row and increments `version`, keeping `ref` and every
    link intact (H3). Recomputing a verdict does not: a measurement is not a change
    to what was promised.
    """

    __tablename__ = "clause"
    __table_args__ = (
        UniqueConstraint("org_id", "product_id", "ref", "version", name="uq_clause_ref_version"),
        CheckConstraint("state IN " + str(CLAUSE_STATES), name="ck_clause_state"),
        CheckConstraint("verdict IN " + str(CLAUSE_VERDICTS), name="ck_clause_verdict"),
        CheckConstraint(
            "comparator IS NULL OR comparator IN " + str(COMPARATORS),
            name="ck_clause_comparator",
        ),
        CheckConstraint("status IN " + str(ROW_STATUSES), name="ck_clause_status"),
        # A band needs both ends. A one-ended `between` is a parse that half failed and
        # would silently never match.
        CheckConstraint(
            "comparator IS DISTINCT FROM 'between' OR "
            "(value IS NOT NULL AND value_high IS NOT NULL)",
            name="ck_clause_band_has_both_ends",
        ),
        Index("ix_clause_product_active", "org_id", "product_id", "status"),
        Index("ix_clause_metric", "org_id", "product_id", "metric"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    ref: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="threshold")
    section: Mapped[str | None] = mapped_column(String(32), nullable=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    metric: Mapped[str | None] = mapped_column(String(128), nullable=True)
    comparator: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Double rather than Numeric: every consumer is statistical (a Wilson bound, a
    # band comparison), and Numeric would force a Decimal/float coercion at each one.
    value: Mapped[float | None] = mapped_column(Double, nullable=True)
    value_high: Mapped[float | None] = mapped_column(Double, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    k: Mapped[int | None] = mapped_column(nullable=True)

    state: Mapped[str] = mapped_column(String(32), nullable=False, default="provisional")
    verdict: Mapped[str] = mapped_column(String(32), nullable=False, default="not_measured")
    verdict_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    version: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clause.id", ondelete="SET NULL"), nullable=True
    )
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source.id", ondelete="SET NULL"), nullable=True
    )
    #: Where in the source this came from, e.g. `products/x/SPEC.md#L212-L219`. Carried
    #: so a citation points at the lines, not merely the file.
    source_locator: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: Hash of the normalised statement. Cheap change detection on re-import, before
    #: anything more expensive than a string comparison is attempted.
    statement_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    created_at: Mapped[datetime] = _ts()


class ClauseIdentity(Base):
    """What makes a ref stable when a human rewrites the sentence.

    AC-1 requires that re-importing after a reworded statement does not change the
    ref. Identity therefore cannot be the statement text. It is a derived key —
    normally the metric name, otherwise the section plus a slug of the label — which
    is recorded the first time a clause is seen and consulted on every later import.

    Keeping it in its own table rather than as a clause column matters because clause
    rows are versioned: identity must outlive any individual version of the text.
    """

    __tablename__ = "clause_identity"
    __table_args__ = (
        UniqueConstraint("org_id", "product_id", "identity_key", name="uq_identity_key"),
        UniqueConstraint("org_id", "product_id", "ref", name="uq_identity_ref"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    identity_key: Mapped[str] = mapped_column(String(255), nullable=False)
    ref: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _ts()


class Observation(Base):
    """One measured number at one point in time. Append-only, machine-written.

    Not an entity: no embedding, no version, no approval. This is the Waku
    `compare_history` lesson the handoff records — benchmark history belongs in its
    own append-only log, out of the store that holds reviewed content.

    `passed` and `total` are carried alongside `value`, which PRD B2 does not
    provide for. The verdict rule is a Wilson score interval and it needs n; a rate
    alone cannot supply one. 19/20 and 190/200 are both "95%" and they are very
    different claims, which is the whole reason the fixtures' own `shared/stats.py`
    exists. Where a source gives only a rate, `total` is null and the verdict can
    only ever be `cannot_confirm`.

    `clause_ref` is text rather than a foreign key on purpose. An observation may
    exist for a metric no clause mentions — that is PRD H16, reported as
    `uncovered / metric_without_clause`, where the gap is the absent clause rather
    than the measurement. A foreign key would make the interesting case unstorable.
    """

    __tablename__ = "observation"
    __table_args__ = (
        # Idempotency, audit item P5 and AC-2. Two departures from B2's four-column
        # version, both of which would otherwise let duplicates through:
        #
        # `product_id` is included. Without it, two products in one org that each have
        # an unbound metric of the same name collide on (org, NULL, metric, NULL).
        #
        # NULLS NOT DISTINCT. In Postgres a NULL is distinct from every other NULL, so
        # the plain constraint would never fire for an unbound metric or a production
        # reading with no run url — exactly the rows a repeated pull duplicates.
        UniqueConstraint(
            "org_id", "product_id", "clause_ref", "metric", "run_url",
            name="uq_observation_idempotent",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "source_kind IN " + str(SOURCE_KINDS_OF_OBSERVATION), name="ck_obs_source_kind"
        ),
        CheckConstraint(
            "total IS NULL OR (total >= 0 AND passed IS NOT NULL AND passed BETWEEN 0 AND total)",
            name="ck_obs_passed_within_total",
        ),
        Index("ix_obs_series", "org_id", "product_id", "metric", "measured_at"),
        Index("ix_obs_clause", "org_id", "clause_ref", "metric", "measured_at"),
    )

    #: A bigint identity rather than a uuid: this is the one table expected to grow
    #: without limit, and `obs:102` is the ref shape the output contract already uses.
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    clause_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metric: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[float] = mapped_column(Double, nullable=False)
    passed: Mapped[int | None] = mapped_column(nullable=True)
    total: Mapped[int | None] = mapped_column(nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)

    prompt_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    corpus_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The revision the run itself recorded, which may be `-dirty`. Provenance only:
    #: never used to build a citation, since nobody else can obtain a dirty tree.
    code_rev: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    run_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _ts()


class EnforcementFact(Base):
    """What CI actually checks, as opposed to what the spec says.

    PRD B2 has no table for this, and AC-4 and AC-15 cannot be answered without one.

    `scope` is the load-bearing column and the reason the whole product has something
    to find. A gate can check the right metric at the right threshold against the
    wrong set of runs, which is `enforced: true, scope: latest_only` — a breach
    sitting mid-sequence then never fails a build and never appears on a dashboard.
    A boolean `enforced` cannot express that (H14).
    """

    __tablename__ = "enforcement_fact"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "product_id", "metric", "file", "line", name="uq_enforcement_site"
        ),
        CheckConstraint("scope IN " + str(ENFORCEMENT_SCOPES), name="ck_enforcement_scope"),
        Index("ix_enforcement_metric", "org_id", "product_id", "metric"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(128), nullable=False)
    enforced: Mapped[bool] = mapped_column(nullable=False, default=False)
    #: The check exists but does not fully cover the clause, with a note saying why.
    partial: Mapped[bool] = mapped_column(nullable=False, default=False)
    partial_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Double, nullable=True)
    comparator: Mapped[str | None] = mapped_column(String(8), nullable=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="all_runs")
    file: Mapped[str] = mapped_column(String(512), nullable=False)
    line: Mapped[int | None] = mapped_column(nullable=True)
    #: The CI unit this lives in, e.g. a gate script or a workflow job name.
    unit: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workflow: Mapped[str | None] = mapped_column(String(512), nullable=True)
    known_failing: Mapped[bool] = mapped_column(nullable=False, default=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _ts()


class Entity(Base):
    """Requirements, decisions and config values: human-authored, versioned, approved.

    Distinct from `clause` because these are read from other people's systems and are
    sparse and text-shaped, where a clause is the Layer's own typed record. Filled in
    phase 6, when Notion, Slack and a production metrics source are bound; the table
    exists now so `link` has something to point at and so the migration is not split.
    """

    __tablename__ = "entity"
    __table_args__ = (
        UniqueConstraint("org_id", "entity_type", "ref", "version", name="uq_entity_ref_version"),
        CheckConstraint("status IN " + str(ROW_STATUSES), name="ck_entity_status"),
        Index("ix_entity_type", "org_id", "entity_type", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=True
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    ref: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity.id", ondelete="SET NULL"), nullable=True
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _ts()


class Link(Base):
    """A typed edge between two refs. What C5 traverses.

    Endpoints are refs rather than foreign keys, because a link routinely joins
    records the Layer does not hold — a Linear ticket to a Notion requirement. The
    Layer holds the statement that they are related, which is the product; it does
    not re-host either end.

    `link_type` carries no CHECK: adding a relationship kind must not be a migration
    (rule R3).
    """

    __tablename__ = "link"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "from_ref", "to_ref", "link_type", name="uq_link_edge"
        ),
        CheckConstraint("status IN ('active', 'retracted')", name="ck_link_status"),
        CheckConstraint("from_ref <> to_ref", name="ck_link_not_self"),
        Index("ix_link_from", "org_id", "from_ref"),
        Index("ix_link_to", "org_id", "to_ref"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=True
    )
    from_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    to_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    link_type: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Double, nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("proposal.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = _ts()


class Proposal(Base):
    """One change to one field, awaiting a human.

    Its own table rather than `pending_entities`, which has none of these columns.
    The shape follows PRD B1 and B4: exactly one field, evidence that resolves, a
    one-sentence reason, and `if_rejected` — because B4 item 5 requires a proposal to
    state what happens if it is turned down, which is what makes rejecting it a
    decision rather than a deferral.

    `confidence` and `critic` are the critic's score, and they are **display and
    routing only**. B3 rule 3 forbids auto-approving a spec edit or a CI change on
    confidence alone, because the critic is itself an LLM and is itself injectable.
    Nothing in this schema permits a state change without `decided_by`.
    """

    __tablename__ = "proposal"
    __table_args__ = (
        CheckConstraint("state IN " + str(PROPOSAL_STATES), name="ck_proposal_state"),
        # The approval record is structural, not a convention. A decided proposal
        # without a decider, or an open one with a decision, cannot be stored.
        CheckConstraint(
            "(state = 'open' AND decided_by IS NULL AND decided_at IS NULL) OR "
            "(state <> 'open' AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_proposal_decision_record",
        ),
        # B4 item 4: not already open against the same target and field.
        Index(
            "uq_proposal_one_open_per_target",
            "org_id", "target", "field",
            unique=True,
            postgresql_where=text("state = 'open'"),
        ),
        Index("ix_proposal_state", "org_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Which capability produced it, e.g. C1 or C3. Provenance for the acceptance rate.
    capability: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    field: Mapped[str] = mapped_column(String(128), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    if_rejected: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Double, nullable=True)
    critic: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    flags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    #: The target's version when the proposal was made. If the target has moved since,
    #: the proposal is stale and must not be applied over a human's edit (H2).
    target_version: Mapped[int | None] = mapped_column(nullable=True)
    proposed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts()


class AuditEvent(Base):
    """Who did what, when, and on what evidence. Append-only.

    PRD B5 ranks a write with no approval record as the first unacceptable failure,
    and audit item P6 notes that `approved_by` on a row is not an audit trail. This
    is enforced by a database trigger rather than by discipline: UPDATE is rejected
    outright, and DELETE only under an explicit erasure flag.

    That exception is deliberate. EC-9 and SEC-8 require a tenant-scoped hard delete
    reconciled with append-only history, and audit item P9 records the conflict. An
    absolute no-delete trigger would make erasure impossible and the right to it
    unimplementable, so the escape hatch exists, is narrow, and is itself auditable.

    No foreign key to `proposal` or `clause`: the log has to outlive its subjects.
    """

    __tablename__ = "audit_event"
    __table_args__ = (
        Index("ix_audit_subject", "org_id", "subject"),
        Index("ix_audit_actor", "org_id", "actor", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org.id", ondelete="CASCADE"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _ts()


#: Every tenant table, in dependency order. Row level security is applied to each.
#: `org` is absent deliberately: it is the registry of tenants, not tenant content.
TENANT_TABLES = (
    "product", "source", "binding", "clause", "clause_identity", "observation",
    "enforcement_fact", "entity", "proposal", "link", "audit_event",
)
