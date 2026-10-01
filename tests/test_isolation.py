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
