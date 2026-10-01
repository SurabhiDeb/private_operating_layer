"""Database access, with tenant isolation that cannot be forgotten.

EarlyEcho filters `org_id` by hand in roughly sixty places and misses it in six
(`api/routes/ingest.py:156,178` look a row up by id alone). PRD SEC-2 and AC-8
require isolation enforced "in the data access layer, not per query", so here the
only way to obtain a session for tenant data is `org_session`, which sets the
Postgres session variable that row level security reads.

The consequence worth stating: a query with no `org_id` filter is now correct, and
a session opened without an org sees nothing at all.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from layer.core.config import settings
from layer.core.errors import TenantContextMissing

# The transaction-local setting every row level security policy compares against.
ORG_SETTING = "app.org_id"


class Base(DeclarativeBase):
    """Declarative base for every Layer table."""


engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _as_uuid(org_id: uuid.UUID | str) -> uuid.UUID:
    if isinstance(org_id, uuid.UUID):
        return org_id
    try:
        return uuid.UUID(str(org_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise TenantContextMissing(f"not a usable org id: {org_id!r}") from exc


def _set_org(session: Session, org_id: uuid.UUID) -> None:
    """Bind the tenant for the rest of this transaction.

    `set_config(..., is_local => true)` is used rather than `SET LOCAL` because it
    accepts a bind parameter. `SET LOCAL app.org_id = :org` is not valid Postgres,
    so the alternative would be interpolating a value into DDL-ish SQL.
    """
    session.execute(
        text("SELECT set_config(:k, :v, true)"),
        {"k": ORG_SETTING, "v": str(org_id)},
    )


@contextmanager
def org_session(org_id: uuid.UUID | str) -> Iterator[Session]:
    """A transaction scoped to one tenant. The only door to tenant data."""
    org = _as_uuid(org_id)
    session = SessionFactory()
    try:
        session.begin()
        _set_org(session, org)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def unscoped_session() -> Iterator[Session]:
    """A session with no tenant bound.

    For the registry tables that precede any tenant (orgs themselves) and for
    migrations. Reading a tenant table through this returns zero rows by design,
    which `tests/test_isolation.py` asserts rather than assumes.
    """
    session = SessionFactory()
    try:
        session.begin()
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_org(session: Session) -> uuid.UUID | None:
    """Whatever org this transaction is bound to, for assertions and logging."""
    raw = session.execute(
        text("SELECT current_setting(:k, true)"), {"k": ORG_SETTING}
    ).scalar()
    return uuid.UUID(raw) if raw else None
