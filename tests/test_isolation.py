"""Tenant isolation. PRD AC-8, SEC-2, hard case H11.

B5 ranks a cross-tenant read second among unacceptable failures, and B6 sets the
bar at zero "tested, not assumed". So these assertions are about the database's
behaviour, not the application's intentions: none of them filters `org_id` by hand,
because the point is that hand filtering is no longer what protects anything.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError

from layer.core.db import current_org, org_session, unscoped_session
from layer.core.errors import TenantContextMissing
from layer.db.models import Product

from conftest import make_org, make_product


@pytest.mark.ac("AC-8")
def test_a_tenant_reads_only_its_own_rows(two_tenants):
    a, b = two_tenants
    with org_session(a) as session:
        keys = set(session.execute(select(Product.key)).scalars())
    assert keys == {"alpha"}

    with org_session(b) as session:
        keys = set(session.execute(select(Product.key)).scalars())
    assert keys == {"beta"}


@pytest.mark.ac("AC-8")
def test_a_cross_tenant_read_returns_zero_rows(two_tenants):
    """The criterion as written: zero rows, not an error, not a filtered subset."""
    a, _ = two_tenants
    with org_session(a) as session:
        count = session.execute(
            select(func.count()).select_from(Product).where(Product.key == "beta")
        ).scalar_one()
    assert count == 0


@pytest.mark.ac("AC-8")
def test_a_session_with_no_tenant_reads_nothing(two_tenants):
    """`current_setting(..., true)` is NULL when unset, so the policy matches no row.

    This is the case that makes a forgotten org fail closed. If it ever returns
    rows, every unscoped query in the codebase becomes a leak.
    """
    with unscoped_session() as session:
        assert current_org(session) is None
        count = session.execute(select(func.count()).select_from(Product)).scalar_one()
    assert count == 0


@pytest.mark.ac("AC-8")
def test_writing_another_tenants_row_is_refused(two_tenants):
    """WITH CHECK, not just USING: isolation has to stop writes as well as reads."""
    a, b = two_tenants
    with pytest.raises(ProgrammingError) as caught:
        with org_session(a) as session:
            session.add(Product(id=uuid.uuid4(), org_id=b, key="smuggled", name="Smuggled"))
    assert "row-level security" in str(caught.value).lower()


@pytest.mark.ac("AC-8")
def test_an_unusable_org_id_is_refused_before_any_query(two_tenants):
    """A malformed tenant must not degrade into an unscoped session."""
    with pytest.raises(TenantContextMissing):
        with org_session("not-a-uuid"):
            pass


@pytest.mark.hard_case("H11")
def test_two_tenants_may_hold_the_same_product_key(two_tenants):
    """H11: refs and keys are scoped to the org, never globally unique."""
    a, b = two_tenants
    make_product(a, "shared-key")
    make_product(b, "shared-key")

    for org in (a, b):
        with org_session(org) as session:
            count = session.execute(
                select(func.count()).select_from(Product).where(Product.key == "shared-key")
            ).scalar_one()
        assert count == 1


def test_the_tenant_registry_itself_is_not_tenant_scoped():
    """`org` has no `org_id`, so it carries no policy. Asserted so that adding one
    later is a deliberate decision rather than an accident."""
    make_org("visible-without-context")
    with unscoped_session() as session:
        from layer.db.models import Org

        slugs = set(session.execute(select(Org.slug)).scalars())
    assert "visible-without-context" in slugs


@pytest.mark.ac("AC-8")
def test_every_tenant_table_has_row_level_security_and_a_policy():
    """A guard on the guard, and the one that scales.

    Every test above names a table. Adding a tenant table without a policy therefore
    breaks nothing, and the window between a table existing and its policy existing is
    a window in which a cross-tenant read is legal — which B5 ranks second among the
    failures this project may not have. This asserts the property of the whole set, so
    the next table is covered before anyone writes a test for it.

    Read from the live `TENANT_TABLES` on purpose, unlike the migrations, which name
    their own: here the point is that today's model file has nothing uncovered in it.
    """
    from sqlalchemy import text

    from layer.core.config import settings
    from layer.db.models import TENANT_TABLES

    engine = __import__("sqlalchemy").create_engine(settings.migration_url, future=True)
    with engine.connect() as conn:
        enabled = dict(conn.execute(text(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE relname = ANY(:names)"
        ), {"names": list(TENANT_TABLES)}).all())
        policed = {
            row[0]: row[1] for row in conn.execute(text(
                "SELECT tablename, qual FROM pg_policies WHERE tablename = ANY(:names)"
            ), {"names": list(TENANT_TABLES)}).all()
        }

    missing_table = sorted(set(TENANT_TABLES) - set(enabled))
    assert missing_table == [], f"declared tenant tables that do not exist: {missing_table}"

    unprotected = sorted(t for t in TENANT_TABLES if not enabled.get(t))
    assert unprotected == [], f"tenant tables without row level security: {unprotected}"

    unpoliced = sorted(set(TENANT_TABLES) - set(policed))
    assert unpoliced == [], f"tenant tables with no policy: {unpoliced}"

    # And the predicate itself, because the `nullif` is what makes a tenant-less session
    # match no rows instead of raising on an empty string left by a pooled connection.
    # See layer/db/rls.py; this is the gotcha that cost a session to find.
    for table, qual in sorted(policed.items()):
        assert "org_id" in qual, f"{table}: policy does not mention org_id: {qual}"
        assert "nullif" in qual.lower(), f"{table}: policy omits the nullif: {qual}"
