"""The decide path: accept, reject, and the number that says whether any of this works.

Phase 5 step 15. US-10, AC-9 extended to decisions, EC-5, EC-6, and the half of H2 that
belongs to accepting rather than to generating.

The fixtures are the onboarding suite's, imported rather than rebuilt. A second copy of
a hundred lines of onboarding setup would drift from the first, and the point of
onboarding the alien product here is that a proposal is being decided against a product
the Layer was not written around.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, update

from layer.answers import decisions, writes
from layer.core import actors
from layer.core.db import org_session
from layer.core.errors import Unreadable
from layer.db.models import AuditEvent, Clause, Link, Proposal

from conftest import make_org
from test_onboarding import (  # noqa: F401 - alien_repo is used as a fixture
    ACTOR,
    alien_repo,
    onboard_alien,
    pair_everything,
)
from layer.onboarding import run, state

pytestmark = pytest.mark.needs_db

PM = "pm@example.com"
ENGINEER = "eng@example.com"
AGENT = "bot@example.com"


def _live(session, org, repo):
    """The alien product, onboarded through every step to `live`."""
    product = onboard_alien(session, org, repo, with_code=False)
    run.import_spec(session, product=product, actor=ACTOR)
    run.backfill(session, product=product, actor=ACTOR)
    pair_everything(session, product)
    state.refresh(session, product=product, actor=ACTOR)
    state.measure(session, product=product, actor=ACTOR)
    for email, role in ((PM, "pm"), (ENGINEER, "engineer"), (AGENT, "agent")):
        actors.add(session, org_id=org, email=email, role=role)
    return product


def _a_clause(session, product, **where):
    stmt = select(Clause).where(
        Clause.product_id == product.id, Clause.status == "active",
        Clause.value.is_not(None),
    )
    return session.execute(stmt.limit(1)).scalars().one()


def _propose(session, product, clause, *, field="value", new_value="0.95", kind="clause_change"):
    result = writes.propose_change(
        session, target=f"clause:{clause.ref}", field=field, new_value=new_value,
        reason="the recorded runs sit below this bar across the sequence",
        evidence=[f"clause:{clause.ref}"], actor="generator",
        if_rejected="the clause stands and the gap stays open", kind=kind,
    )
    assert getattr(result, "shape", None) != "refusal", getattr(result, "reason", result)
    # The write tools return B1's response shape, not the row. The id is what the decide
    # path takes, so the helper hands back that rather than the shape.
    return result.proposal_id


# -- accepting -------------------------------------------------------------------


@pytest.mark.story("US-10")
def test_accepting_a_clause_change_versions_the_clause_and_keeps_its_ref(alien_repo):
    """H3's mechanism, reused. The ref survives so every link stays attached."""
    org = make_org("decide-accept")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        ref, before = clause.ref, clause.version
        proposal = _propose(session, product, clause)

        result = decisions.accept(session, proposal_id=proposal, by=PM)
        assert result["decision"] == "accepted"
        assert result["applied"]["version"] == before + 1
        assert result["applied"]["clause_ref"] == ref

        rows = session.execute(
            select(Clause).where(Clause.ref == ref, Clause.product_id == product.id)
            .order_by(Clause.version)
        ).scalars().all()
        assert [r.version for r in rows] == [before, before + 1]
        assert rows[0].status == "superseded"
        assert rows[0].superseded_by_id == rows[1].id
        assert rows[1].status == "active"
        assert rows[1].value == 0.95
        assert rows[1].approved_by == PM


@pytest.mark.story("US-10")
@pytest.mark.ac("AC-9")
def test_the_audit_event_records_the_evidence_that_was_displayed(alien_repo):
    """US-10's third requirement, and the one usually dropped. "They approved it" is not
    a defence; "they approved it while looking at these records" is."""
    org = make_org("decide-audit")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        proposal = _propose(session, product, clause)
        decisions.accept(session, proposal_id=proposal, by=PM, note="agreed")

        event = session.execute(
            select(AuditEvent).where(AuditEvent.action == decisions.PROPOSAL_ACCEPTED)
        ).scalars().one()
        assert event.actor == PM
        assert event.subject == f"proposal:{proposal}"
        displayed = event.detail["displayed"]
        assert displayed["evidence"] == [f"clause:{clause.ref}"]
        assert displayed["reason"]
        assert displayed["if_rejected"]
        assert event.detail["role"] == "pm"
        assert event.detail["applied"]["version"] == clause.version + 1


@pytest.mark.story("US-10")
def test_a_decision_cannot_be_recorded_without_a_decider(alien_repo):
    """US-10's fourth criterion: no proposal is applied without an approval record.

    B5 item 1 is structural here rather than a convention — the CHECK refuses the row,
    so the rule holds against code that has not been written yet."""
    org = make_org("decide-nodecider")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        proposal = _propose(session, product, clause)
        # In a savepoint: the violation poisons the transaction, and the point of the
        # test is the constraint, not what a poisoned session then does.
        with pytest.raises(Exception) as exc:
            with session.begin_nested():
                session.execute(
                    update(Proposal)
                    .where(Proposal.id == uuid.UUID(proposal))
                    .values(state="accepted")
                )
    assert "ck_proposal_decision_record" in str(exc.value)


# -- rejecting -------------------------------------------------------------------


def test_rejecting_requires_a_reason(alien_repo):
    """An unexplained rejection is indistinguishable from a proposal nobody got to, and
    the acceptance rate cannot tell them apart either."""
    org = make_org("decide-noreason")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        proposal = _propose(session, product, clause)
        with pytest.raises(Unreadable) as exc:
            decisions.reject(session, proposal_id=proposal, by=PM, reason="  ")
    assert "decision" in str(exc.value)


def test_rejecting_changes_nothing_about_the_clause(alien_repo):
    org = make_org("decide-reject")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        ref, before, value = clause.ref, clause.version, clause.value
        proposal = _propose(session, product, clause)
        result = decisions.reject(
            session, proposal_id=proposal, by=PM, reason="the bar is right; the product is not"
        )
        assert result["decision"] == "rejected"
        after = session.execute(
            select(Clause).where(Clause.ref == ref, Clause.status == "active")
        ).scalars().one()
        assert (after.version, after.value) == (before, value)

        event = session.execute(
            select(AuditEvent).where(AuditEvent.action == decisions.PROPOSAL_REJECTED)
        ).scalars().one()
        assert event.detail["reason"].startswith("the bar is right")
        assert event.detail["if_rejected"]


# -- the role rules on the decide path -------------------------------------------


def test_an_engineer_cannot_accept_a_clause_change(alien_repo):
    """US-10: a change to the definition of correct passes through the person
    accountable for it, and no further."""
    org = make_org("decide-role")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        proposal = _propose(session, product, clause)
        with pytest.raises(actors.RoleForbids):
            decisions.accept(session, proposal_id=proposal, by=ENGINEER)
        assert session.execute(
            select(Proposal.state).where(Proposal.id == uuid.UUID(proposal))
        ).scalar_one() == "open"


def test_an_agent_cannot_decide_even_through_this_function(alien_repo):
    """The MCP server does not serve this at all (AC-31). This is the second barrier."""
    org = make_org("decide-agent")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        proposal = _propose(session, product, clause)
        with pytest.raises(actors.RoleForbids):
            decisions.accept(session, proposal_id=proposal, by=AGENT)


def test_an_unregistered_decider_is_refused(alien_repo):
    org = make_org("decide-ghost")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        proposal = _propose(session, product, clause)
        with pytest.raises(actors.UnknownActor):
            decisions.accept(session, proposal_id=proposal, by="ghost@example.com")


def test_a_missing_proposal_is_a_refusal_not_an_exception(alien_repo):
    org = make_org("decide-missing")
    with org_session(org) as session:
        _live(session, org, alien_repo)
        result = decisions.accept(session, proposal_id=uuid.uuid4(), by=PM)
    assert result["shape"] == "refusal"


# -- EC-6, two people at once ----------------------------------------------------


@pytest.mark.edge_case("EC-6")
def test_the_first_decision_wins_and_the_second_is_told_by_whom(alien_repo):
    """EC-6. The mechanism is a conditional update rather than a row lock: a lock held
    across a human's deliberation is a lock held for minutes."""
    org = make_org("decide-race")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        proposal = _propose(session, product, clause)
        actors.add(session, org_id=org, email="other@example.com", role="pm")

        decisions.accept(session, proposal_id=proposal, by=PM)
        with pytest.raises(decisions.AlreadyDecided) as exc:
            decisions.reject(
                session, proposal_id=proposal, by="other@example.com", reason="no"
            )
    message = str(exc.value)
    assert "already accepted" in message
    assert PM in message
    assert "first decision stands" in message


@pytest.mark.edge_case("EC-6")
def test_a_rejected_proposal_cannot_then_be_accepted(alien_repo):
    org = make_org("decide-race2")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        proposal = _propose(session, product, clause)
        decisions.reject(session, proposal_id=proposal, by=PM, reason="not this one")
        with pytest.raises(decisions.AlreadyDecided):
            decisions.accept(session, proposal_id=proposal, by=PM)


# -- H2, the target moved --------------------------------------------------------


@pytest.mark.hard_case("H2")
def test_a_clause_edited_since_the_proposal_cannot_be_overwritten(alien_repo):
    """H2. Human text wins. The proposal is refused rather than merged over the edit."""
    org = make_org("decide-stale")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        proposal = _propose(session, product, clause)

        # A human rewords the clause: the row is superseded and a new version is active.
        edited = Clause(
            org_id=clause.org_id, product_id=clause.product_id, ref=clause.ref,
            kind=clause.kind, statement=clause.statement + " (reworded by hand)",
            metric=clause.metric, comparator=clause.comparator, value=clause.value,
            unit=clause.unit, direction=clause.direction, state=clause.state,
            verdict=clause.verdict, version=clause.version + 1, status="active",
        )
        clause.status = "superseded"
        session.add(edited)
        session.flush()

        with pytest.raises(decisions.Stale) as exc:
            decisions.accept(session, proposal_id=proposal, by=PM)
    assert "human text wins" in str(exc.value)


@pytest.mark.hard_case("H2")
def test_a_stale_proposal_can_still_be_rejected(alien_repo):
    """Clearing the queue of proposals reality has overtaken is housekeeping. Blocking
    the rejection too would leave them stuck open, holding the one-open-per-target index
    against their own replacements."""
    org = make_org("decide-stale2")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        proposal = _propose(session, product, clause)
        clause.version = clause.version + 1
        session.flush()
        result = decisions.reject(
            session, proposal_id=proposal, by=PM, reason="overtaken by a human edit"
        )
    assert result["decision"] == "rejected"


# -- the kinds that stop short ---------------------------------------------------


def test_accepting_a_ci_change_records_the_approval_and_says_the_pr_is_not_opened(alien_repo):
    """SEC-3 needs a scoped app installation this deployment does not have. The seam is
    named in the output rather than discovered by someone waiting for a pull request."""
    org = make_org("decide-ci")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        proposal = _propose(session, product, clause, kind="ci_change")
        result = decisions.accept(session, proposal_id=proposal, by=ENGINEER)
        assert result["applied"]["pull_request"] is None
        assert "SEC-3" in result["applied"]["pending"]
        assert session.execute(
            select(Proposal.state).where(Proposal.id == uuid.UUID(proposal))
        ).scalar_one() == "accepted"


def test_accepting_a_link_creates_the_edge_with_its_provenance(alien_repo):
    org = make_org("decide-link")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        result = writes.propose_link(
            session, from_ref="ticket:ABC-1", to_ref=f"clause:{clause.ref}",
            link_type="implements", reason="the ticket names this clause",
            evidence=[f"clause:{clause.ref}"], actor="generator",
            product_key=product.key, if_rejected="the clause stays unlinked",
        )
        assert getattr(result, "shape", None) != "refusal", getattr(result, "reason", "")
        decisions.accept(session, proposal_id=result.proposal_id, by=PM)
        link = session.execute(select(Link)).scalars().one()
        assert link.link_type == "implements"
        assert str(link.proposal_id) == result.proposal_id
        assert link.created_by == PM


def test_accepting_a_proposed_binding_is_refused_and_names_the_right_command(alien_repo):
    """B11's rule. If accepting a proposal could confirm a binding, an agent that may
    propose a binding would have manufactured B3 rule 8's precondition."""
    org = make_org("decide-binding")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        result = writes.propose_binding(
            session, product_key=product.key, metric="some_metric",
            clause_ref=clause.ref, reason="the names match",
            actor="generator", if_rejected="the metric stays unpaired",
        )
        assert getattr(result, "shape", None) != "refusal", getattr(result, "reason", "")
        with pytest.raises(Unreadable) as exc:
            decisions.accept(session, proposal_id=result.proposal_id, by=PM)
    assert "bindings confirm" in str(exc.value)


# -- the number ------------------------------------------------------------------


@pytest.mark.ac("AC-16")
def test_the_rate_is_unmeasured_rather_than_zero_when_nothing_is_decided(alien_repo):
    """AC-16 asks for a real number rather than null, so the state of having no number
    has to be nameable. Zero would read as a measured failure."""
    org = make_org("rate-empty")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        report = decisions.acceptance(session, product=product)
    assert report["rate"] is None
    assert report["verdict"] == "unmeasured"
    assert "AC-16" in report["summary"]


@pytest.mark.ac("AC-16")
@pytest.mark.edge_case("EC-5")
@pytest.mark.parametrize(
    "accepted, rejected, verdict",
    [
        (3, 1, "provisional"),      # under AC-16's twenty, so the band says nothing yet
        (5, 20, "noise"),           # 20%, below the floor
        (24, 1, "rubber_stamp"),    # 96%, above the ceiling
        (15, 10, "healthy"),        # 60%, inside the band
    ],
)
def test_the_rate_is_reported_against_its_band_in_both_directions(
    alien_repo, accepted, rejected, verdict
):
    """EC-5: falling outside the band is a product failure to report, not to hide — and
    B6's upper edge is a failure too, which is the half that is easy to drop."""
    org = make_org("rate-band")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        for index in range(accepted + rejected):
            session.add(Proposal(
                org_id=org, product_id=product.id, kind="clause_change",
                target=f"clause:X-{index}", field="value", reason="r",
                evidence=[], state="accepted" if index < accepted else "rejected",
                proposed_by="generator", decided_by=PM,
                decided_at=decisions._now(session),
            ))
        session.flush()
        report = decisions.acceptance(session, product=product)
    assert report["verdict"] == verdict
    assert report["decided"] == accepted + rejected
    assert abs(report["rate"] - accepted / (accepted + rejected)) < 1e-9


def test_open_proposals_are_not_in_the_denominator(alien_repo):
    """An open proposal has not been decided. Counting it would make the rate drift
    downwards simply because a queue exists."""
    org = make_org("rate-open")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        _propose(session, product, clause)
        report = decisions.acceptance(session, product=product)
    assert report["open"] == 1
    assert report["decided"] == 0
    assert report["rate"] is None


def test_the_queue_is_oldest_first(alien_repo):
    """Newest-first would bury whatever nobody got to, and the buried ones are where an
    evidence expiry is waiting."""
    org = make_org("queue-order")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clauses = session.execute(
            select(Clause).where(
                Clause.product_id == product.id, Clause.status == "active",
                Clause.value.is_not(None),
            ).limit(2)
        ).scalars().all()
        first = _propose(session, product, clauses[0])
        second = _propose(session, product, clauses[1])
        rows = decisions.queue(session, product=product)
    assert [row["id"] for row in rows] == [first, second]


@pytest.mark.story("US-10")
def test_the_queue_presents_every_field_a_reviewer_decides_on(alien_repo):
    """US-10's first criterion: target, old value, new value, reason and evidence.

    All five, because a reviewer missing the old value cannot tell a tightening from a
    loosening, and that is the whole decision."""
    org = make_org("queue-shape")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        _propose(session, product, clause)
        row = decisions.queue(session, product=product)[0]
    for field in ("target", "field", "old_value", "new_value", "reason", "evidence"):
        assert field in row, field
    assert row["old_value"] is not None
    assert row["new_value"] == "0.95"
    assert row["evidence"]


def test_an_unscored_proposal_shows_a_null_confidence_rather_than_zero(alien_repo):
    """Until the critic exists every proposal is unscored, and a zero would read as a
    confident judgement that it is worthless."""
    org = make_org("queue-unscored")
    with org_session(org) as session:
        product = _live(session, org, alien_repo)
        clause = _a_clause(session, product)
        _propose(session, product, clause)
        assert decisions.queue(session, product=product)[0]["confidence"] is None
