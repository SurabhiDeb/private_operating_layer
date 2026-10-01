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

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
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


#: Tenant tables, in dependency order. Row level security is applied to each of
#: these and to nothing else. `org` is absent deliberately: it is the registry.
TENANT_TABLES: tuple[str, ...] = ("product", "source", "binding")
