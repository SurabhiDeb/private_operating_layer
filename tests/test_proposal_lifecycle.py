"""The hard cases that make the write half trustworthy. Phase 5 step 17.

H2, H7, EC-6 and EC-8 — a proposal ending without anybody having decided it, which is
the state the first three states could not express.

EC-6 and the accept-time half of H2 landed with the decide path in step 15 and are tested
in `tests/test_decisions.py`: a conditional update is simply the right way to write
`accept`, so the mechanism arrived with it. What is here is the rest — the sweep that
closes an undecidable proposal *before* a human spends their minute on it, and the two
terminal states that let it be closed without forging an approval record.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from layer.answers import decisions, writes
from layer.core import actors
from layer.core.db import org_session
from layer.db.models import (
    DECIDED_STATES,
    PROPOSAL_STATES,
    SYSTEM_CLOSED_STATES,
    AuditEvent,
    Clause,
    Proposal,
    Source,
)
from layer.findings import queries

from conftest import make_org
from test_findings import ACTOR, onboard, policydesk, triage  # noqa: F401

pytestmark = pytest.mark.needs_db

PM = "pm@example.com"


def _with_actor(session, product):
    actors.add(session, org_id=product.org_id, email=PM, role="pm")


def _a_clause(session, product):
    return session.execute(
        select(Clause).where(
            Clause.product_id == product.id, Clause.status == "active",
            Clause.value.is_not(None),
        ).limit(1)
    ).scalars().one()


def _propose(session, product, clause, *, evidence=None, field="value"):
    result = writes.propose_change(
        session, target=f"clause:{clause.ref}", field=field, new_value="0.99",
        reason="the recorded runs sit below this bar across the sequence",
        evidence=evidence or [f"clause:{clause.ref}"], actor="generator",
        if_rejected="the clause stands and the gap stays open",
    )
    assert getattr(result, "shape", None) != "refusal", getattr(result, "reason", result)
    return uuid.UUID(result.proposal_id)


def _state(session, proposal_id) -> str:
    return session.execute(
        select(Proposal.state).where(Proposal.id == proposal_id)
    ).scalar_one()


# -- the vocabulary itself -------------------------------------------------------


def test_the_two_halves_of_the_state_set_do_not_overlap():
    """A state in both would be counted as a decision and as not one, and whichever
    read happened last would win."""
    assert set(DECIDED_STATES) & set(SYSTEM_CLOSED_STATES) == set()
    assert {"open", *DECIDED_STATES, *SYSTEM_CLOSED_STATES} == set(PROPOSAL_STATES)


@pytest.mark.parametrize("state", sorted(SYSTEM_CLOSED_STATES))
def test_the_database_refuses_a_system_closed_proposal_that_names_a_decider(
    triage, state
):
    """The property worth having. Naming a decider here would forge an approval record,
    which is the first item in PRD B5, and this is refused by the database rather than
    by whichever code path happens to be writing."""
    session, product = triage
    clause = _a_clause(session, product)
    proposal = _propose(session, product, clause)
    with pytest.raises(Exception) as exc:
        with session.begin_nested():
            session.execute(
                update(Proposal).where(Proposal.id == proposal).values(
                    state=state, decided_by=PM, decided_at=func.now(),
                    decision_note="a reason",
                )
            )
    assert "ck_proposal_decision_record" in str(exc.value)


@pytest.mark.parametrize("state", sorted(SYSTEM_CLOSED_STATES))
def test_a_system_closed_proposal_must_state_its_reason(triage, state):
    """"Closed by the system" with no reason is indistinguishable from a bug, and the
    person who finds it a month later cannot tell which it was."""
    session, product = triage
    clause = _a_clause(session, product)
    proposal = _propose(session, product, clause)
    with pytest.raises(Exception) as exc:
        with session.begin_nested():
            session.execute(
                update(Proposal).where(Proposal.id == proposal).values(
                    state=state, decided_by=None, decided_at=func.now(),
                    decision_note=None,
                )
            )
    assert "ck_proposal_decision_record" in str(exc.value)


def test_a_decided_proposal_still_requires_its_approval_record(triage):
    """The case the constraint already enforced, re-asserted after it grew a third
    branch: an amended constraint that loosened the original would be worse than the
    thing it was amended for."""
    session, product = triage
    clause = _a_clause(session, product)
    proposal = _propose(session, product, clause)
    for state in DECIDED_STATES:
        with pytest.raises(Exception) as exc:
            with session.begin_nested():
                session.execute(
                    update(Proposal).where(Proposal.id == proposal).values(
                        state=state, decided_by=None, decided_at=func.now(),
                    )
                )
        assert "ck_proposal_decision_record" in str(exc.value)


# -- H2, a human edits the target ------------------------------------------------


@pytest.mark.hard_case("H2")
def test_the_sweep_invalidates_a_proposal_whose_clause_a_human_edited(triage):
    """H2. Human text wins, and the proposal is closed before anybody spends their
    minute on it rather than refused at the moment they try to accept."""
    session, product = triage
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

    closed = decisions.sweep(session, product=product)
    assert str(proposal) in closed["invalidated"]
    assert _state(session, proposal) == "invalidated"

    row = session.execute(
        select(Proposal).where(Proposal.id == proposal)
    ).scalars().one()
    assert row.decided_by is None, "the system closed this; nobody decided it"
    assert "human text wins" in row.decision_note


@pytest.mark.hard_case("H2")
def test_an_invalidated_proposal_stops_holding_its_target(triage):
    """The reason `open` was not an option. An invalidated proposal left open would hold
    `uq_proposal_one_open_per_target` against its own replacement, so the queue would
    stop healing after the first human edit."""
    session, product = triage
    clause = _a_clause(session, product)
    _propose(session, product, clause)

    clause.version = clause.version + 1
    session.flush()
    decisions.sweep(session, product=product)

    # The same target and field can now be proposed again.
    again = _propose(session, product, clause)
    assert _state(session, again) == "open"


@pytest.mark.hard_case("H2")
def test_an_invalidated_proposal_is_never_presented_in_the_queue(triage):
    """The point of sweeping before presenting. B4 budgets a human one minute per
    proposal, and spending it on one that cannot be applied is spending it twice."""
    session, product = triage
    clause = _a_clause(session, product)
    proposal = _propose(session, product, clause)
    clause.version = clause.version + 1
    session.flush()

    rows = decisions.queue(session, product=product)
    assert str(proposal) not in [row["id"] for row in rows]


@pytest.mark.hard_case("H2")
def test_closing_a_proposal_is_audited_as_the_layer_and_not_as_a_person(triage):
    """The log records who acted; `decided_by` records who decided. Here those are not
    the same thing, and collapsing them would make the column unable to answer the one
    question it exists for."""
    session, product = triage
    clause = _a_clause(session, product)
    proposal = _propose(session, product, clause)
    clause.version = clause.version + 1
    session.flush()
    decisions.sweep(session, product=product)

    event = session.execute(
        select(AuditEvent).where(AuditEvent.action == decisions.PROPOSAL_INVALIDATED)
    ).scalars().one()
    assert event.actor == "layer:sweep"
    assert event.subject == f"proposal:{proposal}"
    assert event.detail["reason"]


# -- H7 and EC-8, retention deletes the evidence ---------------------------------


@pytest.fixture
def expiring(policydesk):
    """The policy product, whose owner has declared a one-day trace retention."""
    session, product = policydesk
    session.execute(
        update(Source)
        .where(Source.product_id == product.id, Source.role == "eval")
        .values(config=Source.config.op("||")({"trace_retention": "1d"}))
    )
    session.expire_all()
    return session, session.execute(
        select(type(product)).where(type(product).id == product.id)
    ).scalars().one()


@pytest.mark.hard_case("H7")
@pytest.mark.edge_case("EC-8")
def test_a_proposal_whose_cases_are_past_retention_is_marked_evidence_expired(expiring):
    """H7 and EC-8. B5 item 8 ranks displaying a dead citation as live among the
    unacceptable failures; expiring is the honest alternative."""
    session, product = expiring
    drift = [f for f in queries.find_drift(session, product=product)
             if f.detail["failing_cases"]]
    if not drift:
        pytest.skip("this product has no drift finding carrying case evidence")
    finding = drift[0]
    clause = session.execute(
        select(Clause).where(
            Clause.product_id == product.id, Clause.ref == finding.clause_ref,
            Clause.status == "active",
        )
    ).scalars().one()
    cases = [ref for ref in finding.evidence if ref.startswith("case:")]
    assert cases, "the finding cites no cases"
    proposal = _propose(session, product, clause, evidence=cases)

    closed = decisions.sweep(session, product=product)
    assert str(proposal) in closed["evidence_expired"], closed
    row = session.execute(select(Proposal).where(Proposal.id == proposal)).scalars().one()
    assert row.decided_by is None
    assert "past its source's declared trace retention" in row.decision_note


@pytest.mark.hard_case("H7")
def test_nothing_expires_where_the_source_declares_no_retention(policydesk):
    """`retention_for` treats absent and unparseable alike as "nothing is asserted".
    Reading silence as "every trace is gone" would retire every old finding's evidence
    at once."""
    session, product = policydesk
    drift = [f for f in queries.find_drift(session, product=product)
             if f.detail["failing_cases"]]
    if not drift:
        pytest.skip("this product has no drift finding carrying case evidence")
    finding = drift[0]
    clause = session.execute(
        select(Clause).where(
            Clause.product_id == product.id, Clause.ref == finding.clause_ref,
            Clause.status == "active",
        )
    ).scalars().one()
    cases = [ref for ref in finding.evidence if ref.startswith("case:")]
    proposal = _propose(session, product, clause, evidence=cases)
    closed = decisions.sweep(session, product=product)
    assert closed["evidence_expired"] == []
    assert _state(session, proposal) == "open"


@pytest.mark.hard_case("H7")
def test_a_proposal_citing_only_a_clause_does_not_expire(expiring):
    """Only a case citation is a trace body a source deletes. A `clause:` ref resolves
    to a specification document at a pinned revision, which no retention window touches,
    and expiring on it would retire proposals whose evidence is perfectly readable."""
    session, product = expiring
    clause = _a_clause(session, product)
    proposal = _propose(session, product, clause, evidence=[f"clause:{clause.ref}"])
    closed = decisions.sweep(session, product=product)
    assert closed["evidence_expired"] == []
    assert _state(session, proposal) == "open"


# -- the rate's denominator ------------------------------------------------------


@pytest.mark.ac("AC-16")
@pytest.mark.edge_case("EC-5")
def test_a_system_closed_proposal_is_in_neither_half_of_the_rate(triage):
    """The arithmetic this step exists to get right before the number is ever reported.

    A proposal nobody decided is not a rejection. Counting it as one would make B6's
    single most important metric depend on how much evidence had expired, which is a
    dependency nobody reading the number would suspect.
    """
    session, product = triage
    _with_actor(session, product)
    clauses = session.execute(
        select(Clause).where(
            Clause.product_id == product.id, Clause.status == "active",
            Clause.value.is_not(None),
        ).limit(2)
    ).scalars().all()
    if len(clauses) < 2:
        pytest.skip("this product has too few clauses with a bar")

    decided = _propose(session, product, clauses[0])
    decisions.accept(session, proposal_id=str(decided), by=PM)

    doomed = _propose(session, product, clauses[1])
    clauses[1].version = clauses[1].version + 1
    session.flush()
    decisions.sweep(session, product=product)
    assert _state(session, doomed) == "invalidated"

    report = decisions.acceptance(session, product=product)
    assert report["accepted"] == 1
    assert report["rejected"] == 0
    assert report["invalidated"] == 1
    assert report["decided"] == 1, "a system-closed proposal entered the denominator"
    assert report["rate"] == 1.0


def test_the_excluded_counts_are_reported_rather_than_dropped(triage):
    """Excluded from the rate is not the same as hidden. A reader who sees 100% over one
    decision should be able to see that four others were closed without one."""
    session, product = triage
    report = decisions.acceptance(session, product=product)
    assert "invalidated" in report
    assert "evidence_expired" in report


# -- the sweep against a human ---------------------------------------------------


@pytest.mark.edge_case("EC-6")
def test_the_sweep_loses_to_a_human_who_already_decided(triage):
    """EC-6 from the other side. The sweep uses the same conditional update `accept`
    does, so it cannot overwrite a decision that was already made — a sweep that closed
    an accepted proposal would be erasing an approval record."""
    session, product = triage
    _with_actor(session, product)
    clause = _a_clause(session, product)
    proposal = _propose(session, product, clause)
    decisions.accept(session, proposal_id=str(proposal), by=PM)

    # The target then moves, which would have invalidated it had it still been open.
    clause.version = clause.version + 10
    session.flush()
    closed = decisions.sweep(session, product=product)

    assert str(proposal) not in closed["invalidated"]
    row = session.execute(select(Proposal).where(Proposal.id == proposal)).scalars().one()
    assert row.state == "accepted"
    assert row.decided_by == PM


def test_the_sweep_is_idempotent(triage):
    """Run before every queue, so running twice must not double-count or re-close."""
    session, product = triage
    clause = _a_clause(session, product)
    _propose(session, product, clause)
    clause.version = clause.version + 1
    session.flush()

    first = decisions.sweep(session, product=product)
    second = decisions.sweep(session, product=product)
    assert len(first["invalidated"]) == 1
    assert second["invalidated"] == []
    events = session.execute(
        select(func.count()).select_from(AuditEvent)
        .where(AuditEvent.action == decisions.PROPOSAL_INVALIDATED)
    ).scalar_one()
    assert events == 1, "a second sweep logged the same closure again"
