"""Who is acting, and what their role lets them decide.

**This is identity, not authentication.** PRD B11 rule 7: in this phase a write is
attributed to an actor the server cannot verify, and sessions and bearer tokens arrive
with the HTTP transport. So nothing here checks a credential. What it does check is that
the name on a decision resolves to somebody with a role, which is the precondition
AC-16's `decided_by` and the role rules both rest on.

**`--as` keeps two different strengths, deliberately.** Everywhere in onboarding it is
attribution: a string recorded on the audit event, unverified, exactly as B11 rule 7
describes. On the decide path it must *resolve*, because a role rule enforced against a
free-text string is not a rule — anyone who can type an email address could pick their own
permissions. Tightening it everywhere would buy nothing and break onboarding for operators
who are not registered people.

**`agent` is a role, not an absence of one.** It exists so that the refusal is explicit
and testable rather than a lookup that happens to fail. B3 rule 1 and AC-31 are this same
rule drawn at other layers: the MCP server does not serve `accept_proposal` at all, and
even if something reached this function as an agent, it would be refused here too. Two
independent barriers, because the one-barrier version is one mistake from gone.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer.core.errors import Unreadable
from layer.db.models import ACTOR_ROLES, ROLE_DECIDES, Actor


class UnknownActor(Unreadable):
    """The name on a decision does not resolve to a registered actor.

    An `Unreadable` rather than its own hierarchy: it is a refusal an operator is meant
    to read and correct, and it reaches them as `refused: ...` rather than a traceback.
    """


class RoleForbids(Unreadable):
    """The actor resolves, and their role does not decide this kind of proposal."""


def add(session: Session, *, org_id, email: str, role: str, name: str | None = None) -> Actor:
    """Register a person. Idempotent on the email, so a repeat is an update of the role.

    A second `actor add` for the same email is far more likely to be a correction than a
    mistake — someone changed team — and the alternative is an operator deleting a row to
    change a role, on a table whose rows the audit log refers to by email.
    """
    if role not in ACTOR_ROLES:
        raise Unreadable(
            f"unknown role {role!r}. One of: {', '.join(ACTOR_ROLES)}"
        )
    email = email.strip().lower()
    if not email:
        raise Unreadable("an actor needs an email; it is what a decision is attributed to")

    existing = session.execute(select(Actor).where(Actor.email == email)).scalar_one_or_none()
    if existing is not None:
        existing.role = role
        if name:
            existing.name = name
        return existing

    row = Actor(org_id=org_id, email=email, role=role, name=name)
    session.add(row)
    session.flush()
    return row


def resolve(session: Session, *, email: str) -> Actor:
    """The actor behind an `--as` argument, or a refusal naming what to do about it.

    No `org_id` filter: row level security scopes the query, which is why an actor from
    another tenant is not found rather than found and rejected. See CLAUDE.md's second
    hard constraint.
    """
    wanted = (email or "").strip().lower()
    if not wanted:
        raise UnknownActor("no actor given; a decision cannot be anonymous")
    row = session.execute(select(Actor).where(Actor.email == wanted)).scalar_one_or_none()
    if row is None:
        raise UnknownActor(
            f"{wanted} is not a registered actor in this org. "
            f"Register them with `layer actor add {wanted} --role <{'|'.join(ACTOR_ROLES)}>`. "
            "A decision has to be attributable to someone."
        )
    return row


def may_decide(actor: Actor, kind: str) -> bool:
    """Whether this role decides this kind of proposal. PRD A2's two users."""
    return kind in ROLE_DECIDES.get(actor.role, ())


def require_decider(session: Session, *, email: str, kind: str) -> Actor:
    """Resolve the actor and check the role, or refuse. The decide path's only gate.

    The two failures are told apart in the message on purpose. "You are not registered"
    and "your role does not cover this" ask for different actions from the reader, and
    collapsing them into one refusal sends half of them to the wrong fix.
    """
    actor = resolve(session, email=email)
    if not may_decide(actor, kind):
        allowed = ROLE_DECIDES.get(actor.role, ())
        if not allowed:
            raise RoleForbids(
                f"{actor.email} has role {actor.role!r}, which decides nothing. "
                "The Layer proposes and a human decides (PRD A3)."
            )
        raise RoleForbids(
            f"{actor.email} has role {actor.role!r}, which decides "
            f"{', '.join(allowed)} — not {kind}. "
            "A change to the definition of correct goes through the person accountable for it "
            "(US-10)."
        )
    return actor
