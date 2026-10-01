"""Shared fixtures.

Every test runs against a real Postgres with the real migrations applied, because
the thing most worth testing here — row level security — does not exist in SQLite
and cannot be faked.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text

from layer.core.config import settings
from layer.core.db import org_session, unscoped_session
from layer.db.models import Org, Product

#: Truncated between tests. `org` cascades to everything tenant-scoped.
_ADMIN = create_engine(settings.migration_url, future=True)


@pytest.fixture(scope="session", autouse=True)
def schema_is_current() -> None:
    """Fail loudly if the database is not migrated, rather than erroring per test."""
    with _ADMIN.connect() as conn:
        tables = set(
            conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        )
    missing = {"org", "product", "source", "binding"} - tables
    if missing:
        pytest.fail(
            f"database {settings.migration_url.rsplit('/', 1)[-1]} is missing "
            f"{sorted(missing)}. Run: alembic upgrade head"
        )


@pytest.fixture(autouse=True)
def clean_tenants() -> Iterator[None]:
    yield
    with _ADMIN.begin() as conn:
        conn.execute(text("TRUNCATE org CASCADE"))


def make_org(slug: str, name: str | None = None) -> uuid.UUID:
    """Register a tenant. Not tenant-scoped, so it needs no org context."""
    org_id = uuid.uuid4()
    with unscoped_session() as session:
        session.add(Org(id=org_id, slug=slug, name=name or slug.title()))
    return org_id


def make_product(org_id: uuid.UUID, key: str, **kw) -> uuid.UUID:
    """Register a product inside its own tenant context, as the Layer always does."""
    product_id = uuid.uuid4()
    with org_session(org_id) as session:
        session.add(
            Product(id=product_id, org_id=org_id, key=key, name=kw.pop("name", key), **kw)
        )
    return product_id


@pytest.fixture
def two_tenants() -> tuple[uuid.UUID, uuid.UUID]:
    """Two tenants, each with one product, for the isolation and AC-19 tests."""
    a = make_org("tenant-a")
    b = make_org("tenant-b")
    make_product(a, "alpha")
    make_product(b, "beta")
    return a, b
