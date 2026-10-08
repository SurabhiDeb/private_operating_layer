"""The actor slice: a role that bounds a decision, and an `--as` that resolves.

Phase 5 step 14. What is being tested is a precondition rather than a feature — AC-16
records `decided_by`, and the role rules cannot be enforced against a free-text string —
so the tests here are mostly about what is *refused*.

The role matrix is table-driven on purpose. Written as one test per role it would be
three tests that each pass while missing the same gap: a kind nobody thought to list.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from layer.core import actors
from layer.core.db import org_session
from layer.core.errors import Unreadable
from layer.db.models import ACTOR_ROLES, KNOWN_PROPOSAL_KINDS, ROLE_DECIDES, Actor

from conftest import make_org

pytestmark = pytest.mark.needs_db


def test_an_actor_is_registered_with_a_role():
    org = make_org("actor-basic")
    with org_session(org) as session:
        row = actors.add(session, org_id=org, email="pm@example.com", role="pm")
        assert row.role == "pm"
        assert row.email == "pm@example.com"


def test_email_is_normalised_so_as_matches_regardless_of_case():
    """`--as PM@Example.com` and `--as pm@example.com` are the same person.

    Otherwise one typist's capital letter creates a second actor with its own role, and
    `decided_by` stops being a reliable key for the acceptance rate.
    """
    org = make_org("actor-case")
    with org_session(org) as session:
        actors.add(session, org_id=org, email="  PM@Example.com ", role="pm")
    with org_session(org) as session:
        assert actors.resolve(session, email="pm@EXAMPLE.com").role == "pm"


def test_registering_again_changes_the_role_rather_than_failing():
    """Someone changes team. The alternative is an operator deleting the row that the
    audit log refers to by email."""
    org = make_org("actor-rerole")
    with org_session(org) as session:
        actors.add(session, org_id=org, email="x@example.com", role="engineer")
    with org_session(org) as session:
        actors.add(session, org_id=org, email="x@example.com", role="pm")
    with org_session(org) as session:
        rows = session.execute(select(Actor)).scalars().all()
        assert len(rows) == 1
        assert rows[0].role == "pm"


def test_an_unknown_role_is_refused_with_the_list():
    org = make_org("actor-badrole")
    with org_session(org) as session:
        with pytest.raises(Unreadable) as exc:
            actors.add(session, org_id=org, email="x@example.com", role="admin")
    assert "pm" in str(exc.value) and "engineer" in str(exc.value)


def test_the_database_refuses_a_role_it_does_not_know():
    """The CHECK is the real guard. R3: a closed set the Layer branches on gets one."""
    org = make_org("actor-check")
    with pytest.raises(Exception) as exc:
        with org_session(org) as session:
            session.add(Actor(org_id=org, email="y@example.com", role="superuser"))
    assert "ck_actor_role" in str(exc.value)


# -- the role matrix -------------------------------------------------------------


def test_every_role_has_a_decides_list():
    """A role the database accepts and `ROLE_DECIDES` omits would raise a KeyError at
    the moment somebody tried to decide, which is the worst possible time."""
    assert set(ROLE_DECIDES) == set(ACTOR_ROLES)


def test_every_decidable_kind_is_a_known_proposal_kind():
    """A typo in the matrix would silently grant nothing, or grant a kind that does not
    exist — both of which read as a working permission until used."""
    for role, kinds in ROLE_DECIDES.items():
        for kind in kinds:
            assert kind in KNOWN_PROPOSAL_KINDS, f"{role} decides unknown kind {kind}"


def test_a_pm_decides_every_kind_there_is():
    """PRD A2's primary user. If a kind is added and the pm cannot decide it, nobody
    can, and the queue silently stops being clearable."""
    assert set(ROLE_DECIDES["pm"]) == set(KNOWN_PROPOSAL_KINDS)


@pytest.mark.parametrize("role", sorted(ACTOR_ROLES))
@pytest.mark.parametrize("kind", sorted(KNOWN_PROPOSAL_KINDS))
def test_the_role_matrix_is_enforced_for_every_pair(role, kind):
    org = make_org(f"matrix-{role}-{kind}"[:60])
    with org_session(org) as session:
        actors.add(session, org_id=org, email=f"{role}@example.com", role=role)
        allowed = kind in ROLE_DECIDES[role]
        if allowed:
            actor = actors.require_decider(
                session, email=f"{role}@example.com", kind=kind
            )
            assert actor.role == role
        else:
            with pytest.raises(actors.RoleForbids):
                actors.require_decider(session, email=f"{role}@example.com", kind=kind)


def test_an_agent_decides_nothing_and_is_told_so_plainly():
    """B3 rule 1 at this layer. The MCP server does not serve the decide tools at all
    (AC-31); this is the second barrier, for anything that reaches the function."""
    org = make_org("actor-agent")
    with org_session(org) as session:
        actors.add(session, org_id=org, email="bot@example.com", role="agent")
        for kind in KNOWN_PROPOSAL_KINDS:
            with pytest.raises(actors.RoleForbids) as exc:
                actors.require_decider(session, email="bot@example.com", kind=kind)
            assert "decides nothing" in str(exc.value)


def test_an_engineer_cannot_decide_a_clause_change():
    """The spec is not edited through the side door. US-10: every change to the
    definition of correct passes through the person accountable for it."""
    org = make_org("actor-eng")
    with org_session(org) as session:
        actors.add(session, org_id=org, email="eng@example.com", role="engineer")
        with pytest.raises(actors.RoleForbids) as exc:
            actors.require_decider(session, email="eng@example.com", kind="clause_change")
        assert "ci_change" in str(exc.value)
        # And can decide the two that are theirs.
        for kind in ("ci_change", "eval_case"):
            assert actors.require_decider(session, email="eng@example.com", kind=kind)


# -- resolution and refusals -----------------------------------------------------


def test_an_unregistered_actor_is_refused_with_the_command_that_fixes_it():
    org = make_org("actor-unknown")
    with org_session(org) as session:
        with pytest.raises(actors.UnknownActor) as exc:
            actors.require_decider(session, email="ghost@example.com", kind="clause_change")
    message = str(exc.value)
    assert "layer actor add" in message
    assert "ghost@example.com" in message


def test_an_empty_actor_is_refused_rather_than_treated_as_a_name():
    org = make_org("actor-empty")
    with org_session(org) as session:
        with pytest.raises(actors.UnknownActor) as exc:
            actors.resolve(session, email="   ")
    assert "anonymous" in str(exc.value)


@pytest.mark.ac("AC-8")
def test_an_actor_from_another_tenant_does_not_resolve(two_tenants):
    """AC-8 over one more table. The actor is not *rejected*, it is not *found*: the
    filtering is row level security, not a hand-written org_id predicate."""
    a, b = two_tenants
    with org_session(a) as session:
        actors.add(session, org_id=a, email="shared@contractor.com", role="pm")
    with org_session(b) as session:
        with pytest.raises(actors.UnknownActor):
            actors.resolve(session, email="shared@contractor.com")


def test_the_same_contractor_may_exist_in_two_tenants():
    """H11's reasoning applied to people. Uniqueness is per org, and neither tenant
    learns about the other from a constraint violation."""
    a = make_org("tenant-one")
    b = make_org("tenant-two")
    for org in (a, b):
        with org_session(org) as session:
            actors.add(session, org_id=org, email="shared@contractor.com", role="pm")
    for org, role in ((a, "pm"), (b, "pm")):
        with org_session(org) as session:
            assert actors.resolve(session, email="shared@contractor.com").role == role


def test_actor_is_not_append_only_because_roles_change():
    """Unlike `audit_event` and `case_result`. What must be immutable is the record of
    what someone decided, which lives in the log. Freezing this table would make the
    log's integrity depend on nobody ever changing job."""
    org = make_org("actor-mutable")
    with org_session(org) as session:
        actors.add(session, org_id=org, email="z@example.com", role="engineer")
    with org_session(org) as session:
        row = actors.resolve(session, email="z@example.com")
        row.role = "pm"
    with org_session(org) as session:
        assert actors.resolve(session, email="z@example.com").role == "pm"
