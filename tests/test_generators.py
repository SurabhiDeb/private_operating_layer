"""Findings into proposals. Phase 5 step 16.

US-2, US-3, EC-12, AC-17, AC-20, AC-33, AC-34, AC-35, and the fail-closed half of EC-10.

**Both reference products, not one.** The generators are run against `triage` and
`policydesk` separately, because a generator fitted to one product's findings is the
failure mode this whole specification is about. Running against the second product is
what found the three defects recorded in PROGRESS for this step.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from layer.answers import decisions
from layer.core.db import org_session
from layer.core.errors import Refusal
from layer.db.models import Clause, Proposal
from layer.findings import queries
from layer.onboarding import bindings as binding_gate
from layer.onboarding import state
from layer.proposals import generators

from conftest import make_org
from test_findings import (  # noqa: F401 - fixtures
    ACTOR,
    POLICYDESK_METRICS,
    TRIAGE_METRICS,
    onboard,
    policydesk,
    triage,
)

pytestmark = pytest.mark.needs_db


def _run(session, product):
    result = generators.generate(session, product=product, actor="generator")
    assert not isinstance(result, Refusal), getattr(result, "reason", result)
    return result


def _kinds(result) -> set[str]:
    return {p.kind for p in result.proposals}


# -- it runs at all, on both products --------------------------------------------


@pytest.mark.story("US-2")
def test_the_generators_produce_proposals_for_the_classifier_product(triage):
    session, product = triage
    result = _run(session, product)
    assert result.proposals, "a product with findings produced no proposals"
    # Every one is open, decidable, and carries what B4 requires.
    rows = session.execute(
        select(Proposal).where(Proposal.product_id == product.id)
    ).scalars().all()
    for row in rows:
        assert row.state == "open"
        assert row.reason and row.if_rejected and row.evidence
        assert row.capability, "a generated proposal records which generator made it"


@pytest.mark.story("US-2")
def test_the_generators_produce_proposals_for_the_rag_product(policydesk):
    """The second product, whose findings are a different shape: its specification
    states a bar for a subset and none for the headline."""
    session, product = policydesk
    result = _run(session, product)
    assert result.proposals


def test_the_two_products_do_not_borrow_each_other_s_findings(triage, policydesk):
    """AC-19 applied to the write half: no proposal on one product cites the other."""
    triage_session, triage_product = triage
    result = _run(triage_session, triage_product)
    for proposal in result.proposals:
        assert "policydesk" not in proposal.target
        for ref in proposal.evidence:
            assert "policydesk" not in ref


# -- US-2: a threshold change or a ticket, never both ----------------------------


@pytest.mark.story("US-2")
def test_a_drift_finding_yields_a_threshold_change_or_a_ticket_and_never_both(triage):
    """US-2's rule. The two are opposite claims about who is wrong, and offering both
    would be hedging and leaving the judgement unmade."""
    session, product = triage
    result = _run(session, product)
    by_clause: dict[str, set[str]] = {}
    for proposal in result.proposals:
        row = session.execute(
            select(Proposal).where(Proposal.id == proposal.proposal_id)
        ).scalars().one()
        if row.capability != generators.FROM_DRIFT:
            continue
        by_clause.setdefault(row.target, set()).add(row.kind)
    for target, kinds in by_clause.items():
        assert kinds <= {"clause_change", "ticket"}, (target, kinds)
        assert len(kinds) == 1, f"{target} got both a threshold change and a ticket"


@pytest.mark.story("US-2")
def test_the_branch_and_its_reason_agree(triage, policydesk):
    """The defect this test exists for: a bar missed in every run took the ticket branch
    and asserted "the history clears the bar elsewhere". A sentence a reader can check
    against the finding has to be true in the branch that wrote it."""
    for fixture in (triage, policydesk):
        session, product = fixture
        result = _run(session, product)
        for proposal in result.proposals:
            row = session.execute(
                select(Proposal).where(Proposal.id == proposal.proposal_id)
            ).scalars().one()
            if row.capability != generators.FROM_DRIFT:
                continue
            signals = row.rank_signals
            cleared = signals["ever_cleared"]
            if "clears the bar in other runs" in row.reason:
                assert cleared, f"{row.target} claims the history cleared it and it did not"
            if "No run on record has reached it" in row.reason:
                assert not cleared, f"{row.target} claims no run reached it and one did"


@pytest.mark.parametrize("state, expected", [("ratified", "ticket"),
                                             ("measured", "clause_change"),
                                             ("provisional", "clause_change")])
def test_a_bar_no_run_ever_cleared_turns_on_whether_it_was_ratified(
    triage, state, expected
):
    """The third case, and the two it sits between.

    A bar nothing ever reached is either an unjustified number or a promise the product
    has never kept, and `state` is what tells them apart: `ratified` means somebody
    signed it off, so the Layer does not propose lowering it on its own initiative.

    The condition is **constructed rather than waited for**: the bar is raised above
    every observed value so that no run ever cleared it. Neither reference product
    happens to contain this shape, and a test that skips on both leaves the branch it was
    written for uncovered — which is the state this test was in when it was first written.
    """
    session, product = triage
    bound = binding_gate.confirmed_metrics(session, product=product)
    metric, ref = next(iter(bound.items()))
    rows = queries.series_for(session, product.id, metric)
    assert rows, "the fixture carries no observations for this metric"

    clause = session.execute(
        select(Clause).where(
            Clause.product_id == product.id, Clause.ref == ref, Clause.status == "active"
        )
    ).scalars().one()
    clause.comparator = ">="
    clause.direction = "higher_is_better"
    clause.value_high = None
    clause.kind = "threshold"
    # Above every run on record, so nothing ever cleared it.
    clause.value = max(r.value for r in rows) * 1.5
    clause.state = state
    session.flush()

    result = _run(session, product)
    mine = [p for p in result.proposals if p.target == f"clause:{ref}"]
    assert mine, "a bar every run missed produced no proposal"
    assert mine[0].kind == expected
    if expected == "ticket":
        assert "not the Layer's to propose lowering" in mine[0].reason
    else:
        assert "no run on record has reached it" in mine[0].reason


@pytest.mark.story("US-2")
@pytest.mark.ac("AC-7")
def test_no_generated_proposal_states_a_cause(triage, policydesk):
    """US-2's fourth criterion and B3 rule 6, over proposal text rather than findings.

    The findings already have this test. Proposals need their own: a proposal's reason is
    the sentence a human agrees or disagrees with, and it is written by different code.
    "First breached at this run, and the prompt sha changed at that run" is permitted;
    anything asserting that one produced the other is not.
    """
    forbidden = re.compile(
        r"\b(?:because|caused|causing|due to|as a result of|led to|leads to|"
        r"resulted in|results in|owing to|thanks to|explains|attributable)\b",
        re.IGNORECASE,
    )
    texts: list[tuple[str, str]] = []
    for fixture in (triage, policydesk):
        session, product = fixture
        result = _run(session, product)
        for proposal in result.proposals:
            texts.append((proposal.target, proposal.reason))
            texts.append((proposal.target, proposal.if_rejected or ""))
        for item in result.declined:
            texts.append((str(item.get("clause_ref")), item["reason"]))

    assert len(texts) > 10, "too little generated text for this to mean anything"
    offenders = [
        (where, forbidden.search(text).group(0))
        for where, text in texts if forbidden.search(text)
    ]
    assert offenders == [], f"causal language in a proposal: {offenders}"


# -- US-3 and H14: the gate ------------------------------------------------------


@pytest.mark.story("US-3")
@pytest.mark.hard_case("H14")
def test_a_gate_reading_one_run_is_a_change_to_the_run_set_not_the_threshold(triage):
    """H14, which is condition 1's root cause. The threshold is already right; what is
    wrong is which runs the gate reads. Proposing a threshold change here would be
    proposing to fix a correct number."""
    session, product = triage
    unenforced = queries.find_unenforced(session, product=product)
    narrowed = [
        f for f in unenforced
        if f.detail["enforced"] and f.detail["scope"] in ("latest_only", "latest_shipped")
    ]
    if not narrowed:
        pytest.skip("this product's gate does not narrow its run set")

    result = _run(session, product)
    scope_changes = [
        p for p in result.proposals
        if p.kind == "ci_change" and p.field_name == "scope"
    ]
    assert scope_changes, "a narrowed gate produced no proposal to widen it"
    for proposal in scope_changes:
        assert proposal.new_value == "all_runs"
        assert proposal.target.startswith("file:")
        # Not a threshold change. The number in the gate is correct.
        assert "threshold" not in proposal.field_name


def test_nothing_is_proposed_about_a_gate_the_scan_never_read(policydesk):
    """The second defect this step found. `find_unenforced` is right to report a clause
    with no fact covering it; a proposal to *add* a gate asserts no gate exists, and
    with nothing scanned the Layer has not established that."""
    session, product = policydesk  # bound with no code source
    result = _run(session, product)
    assert not [p for p in result.proposals if p.kind == "ci_change"]
    reasons = [d["reason"] for d in result.declined]
    assert any("no code source is bound" in r for r in reasons), reasons


# -- H16: a metric nothing promises ----------------------------------------------


@pytest.mark.hard_case("H16")
def test_a_measured_metric_with_no_clause_yields_a_new_clause(policydesk):
    """H16: "the gap is the absent clause, not the metric". The policy product's
    headline metric is measured and its specification states no bar for it."""
    session, product = policydesk
    result = _run(session, product)
    new_clauses = [p for p in result.proposals if p.kind == "new_clause"]
    assert new_clauses, "a metric nothing promises produced no clause proposal"


@pytest.mark.hard_case("H16")
def test_the_new_clause_proposes_no_bar_of_its_own(policydesk):
    """B10 rules out automatic threshold setting: "the Layer proposes the derived default
    and a human ratifies it". A clause invented here with a number in it would be the
    Layer setting the definition of correct and calling it a proposal."""
    session, product = policydesk
    result = _run(session, product)
    for proposal in result.proposals:
        if proposal.kind != "new_clause":
            continue
        row = session.execute(
            select(Proposal).where(Proposal.id == proposal.proposal_id)
        ).scalars().one()
        payload = row.payload or {}
        assert payload.get("value") is None
        assert payload.get("comparator") is None


@pytest.mark.ac("AC-14")
def test_every_generated_proposal_cites_evidence_that_resolves(triage, policydesk):
    """B4 item 2 and B3 rule 7. `_create` refuses a proposal whose citation does not
    resolve, so a refusal here means a finding is citing something unresolvable — which
    is how the missing `eval_metric` resolver was found."""
    for fixture in (triage, policydesk):
        session, product = fixture
        result = _run(session, product)
        assert result.refusals == [], [r.reason for r in result.refusals]


# -- AC-33, AC-34, AC-35: the cap and the rank -----------------------------------


@pytest.mark.ac("AC-33")
def test_the_cap_is_read_from_the_product_and_changes_what_is_presented(triage):
    """AC-33: read from the product, default twenty, and a changed value changes the
    number presented."""
    session, product = triage
    assert product.harvest_cap == 20, "the default is twenty and is not derived"

    full = _run(session, product)
    total = len(full.proposals) + full.beyond_cap
    if total < 2:
        pytest.skip("this product justifies too few proposals to cap meaningfully")

    # Clear the queue and run again under a cap of one.
    session.execute(
        Proposal.__table__.delete().where(Proposal.product_id == product.id)
    )
    product.harvest_cap = 1
    session.flush()
    capped = _run(session, product)
    assert len(capped.proposals) == 1
    assert capped.cap == 1
    assert capped.beyond_cap == total - 1


@pytest.mark.ac("AC-34")
def test_candidates_beyond_the_cap_are_retained_with_their_rank(triage):
    """AC-34 and US-1: "the cap is a priority order, never a deletion"."""
    session, product = triage
    product.harvest_cap = 1
    session.flush()
    result = _run(session, product)
    if not result.beyond_cap:
        pytest.skip("this product justifies only one proposal")

    rows = session.execute(
        select(Proposal).where(Proposal.product_id == product.id)
        .order_by(Proposal.rank)
    ).scalars().all()
    assert len(rows) == len(result.proposals) + result.beyond_cap
    # Queryable after the run, each with its rank, and the ranks are a dense order.
    assert [r.rank for r in rows] == list(range(1, len(rows) + 1))
    for row in rows:
        assert row.rank_signals, f"rank {row.rank} carries no signals"


@pytest.mark.ac("AC-35")
def test_every_candidate_carries_the_signals_behind_its_rank(triage):
    """AC-35: so a human can disagree with the ordering rather than only with the
    proposals."""
    session, product = triage
    result = _run(session, product)
    for proposal in result.proposals:
        row = session.execute(
            select(Proposal).where(Proposal.id == proposal.proposal_id)
        ).scalars().one()
        assert row.rank is not None
        assert row.rank_signals
        assert row.rank_signals.get("rule"), "a signal set names the rule that applied"


@pytest.mark.ac("AC-35")
def test_no_signal_is_named_importance_or_severity(triage, policydesk):
    """AC-35 is explicit: a ranking score is never presented as a measure of importance
    or of severity of impact. It is a queue order, and the name is where that mistake
    would be made once and then copied by everything that reads it."""
    forbidden = ("importance", "severity", "impact", "priority", "urgency", "criticality")
    for fixture in (triage, policydesk):
        session, product = fixture
        result = _run(session, product)
        for proposal in result.proposals:
            row = session.execute(
                select(Proposal).where(Proposal.id == proposal.proposal_id)
            ).scalars().one()
            for key in row.rank_signals:
                assert not any(word in key.lower() for word in forbidden), key


def test_the_rank_is_dense_and_stable_across_the_whole_run(triage):
    """Ranked across every generator rather than within each, so the cap applies to the
    run and not to whichever generator happened to go first."""
    session, product = triage
    result = _run(session, product)
    rows = session.execute(
        select(Proposal.rank, Proposal.capability)
        .where(Proposal.product_id == product.id).order_by(Proposal.rank)
    ).all()
    ranks = [r.rank for r in rows]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks), "two candidates share a rank"


# -- B3 rule 8: nothing for a product that is not live ---------------------------


@pytest.mark.ac("AC-17")
def test_a_product_with_no_confirmed_binding_generates_nothing(triage):
    """AC-17 and B3 rule 8, on the generator rather than on one write path."""
    org = make_org("gen-notlive")
    with org_session(org) as session:
        product = state.register(
            session, org_id=org, key="bare", name="Bare", actor=ACTOR
        )
        result = generators.generate(session, product=product, actor="generator")
    assert isinstance(result, Refusal)
    assert "binding" in " ".join(result.missing + [result.reason])


@pytest.mark.ac("AC-20")
def test_the_refusal_names_the_product_once_rather_than_per_candidate(triage):
    """A refusal repeated per finding is a wall of identical text, which is how a
    reader learns to skip refusals."""
    org = make_org("gen-bare")
    with org_session(org) as session:
        product = state.register(
            session, org_id=org, key="bare2", name="Bare", actor=ACTOR
        )
        result = generators.generate(session, product=product, actor="generator")
    assert isinstance(result, Refusal)


# -- EC-10: fail closed -----------------------------------------------------------


@pytest.mark.edge_case("EC-10")
def test_a_generator_that_raises_part_way_writes_nothing(triage, monkeypatch):
    """EC-10: "the model provider is unavailable mid-proposal — fail closed. No partial
    proposal is written".

    Nothing here calls a model, so the guarantee is structural rather than handled: the
    run is one transaction. This test breaks a generator in the middle to prove the
    structure holds, which is the version of EC-10 that can be tested today.
    """
    session, product = triage
    before = session.execute(
        select(Proposal).where(Proposal.product_id == product.id)
    ).scalars().all()
    assert before == []

    def explode(*args, **kwargs):
        raise RuntimeError("the provider is unavailable")

    monkeypatch.setattr(generators, "_from_uncovered", explode)
    with pytest.raises(RuntimeError):
        with session.begin_nested():
            generators.generate(session, product=product, actor="generator")

    after = session.execute(
        select(Proposal).where(Proposal.product_id == product.id)
    ).scalars().all()
    assert after == [], "a partial run left proposals behind"


# -- EC-12: the bar that stopped being a constraint -------------------------------


@pytest.mark.edge_case("EC-12")
def test_a_bar_comfortably_cleared_for_months_is_a_candidate_for_raising(triage):
    """EC-12, "the one teams never build": a permanently easy threshold measures nothing.

    The bar is lowered far below every observed value so that the condition exists in
    this product's record, rather than waiting for a fixture that happens to contain it.
    """
    session, product = triage
    bound = binding_gate.confirmed_metrics(session, product=product)
    metric, ref = next(iter(bound.items()))
    rows = queries.series_for(session, product.id, metric)
    if len(rows) < generators.EASY_BAR_MIN_RUNS:
        pytest.skip(f"{metric} has too few runs for this condition to be real")

    clause = session.execute(
        select(Clause).where(
            Clause.product_id == product.id, Clause.ref == ref, Clause.status == "active"
        )
    ).scalars().one()
    clause.comparator = ">="
    clause.direction = "higher_is_better"
    clause.value_high = None
    clause.kind = "threshold"
    clause.value = min(r.value for r in rows) * 0.5
    session.flush()

    result = _run(session, product)
    raised = [
        p for p in result.proposals
        if p.target == f"clause:{ref}" and p.field_name == "value"
    ]
    assert raised, "a bar nothing has approached produced no proposal to raise it"
    assert "not measuring anything" in raised[0].reason
    assert float(raised[0].new_value) > clause.value


@pytest.mark.edge_case("EC-12")
def test_a_bar_cleared_by_a_hair_is_not_an_easy_bar(triage):
    """Both numbers in the rule are conservative and named. A bar cleared by a hair is
    not a bar that stopped constraining anything."""
    session, product = triage
    bound = binding_gate.confirmed_metrics(session, product=product)
    metric, ref = next(iter(bound.items()))
    rows = queries.series_for(session, product.id, metric)
    clause = session.execute(
        select(Clause).where(
            Clause.product_id == product.id, Clause.ref == ref, Clause.status == "active"
        )
    ).scalars().one()
    clause.comparator = ">="
    clause.direction = "higher_is_better"
    clause.value_high = None
    # Just inside the margin: cleared by every run, but only barely.
    clause.value = min(r.value for r in rows) * (1 - generators.EASY_BAR_MARGIN / 2)
    session.flush()

    result = _run(session, product)
    from_easy = [
        p for p in result.proposals
        if p.target == f"clause:{ref}" and p.field_name == "value"
    ]
    assert not from_easy, "a bar cleared by a hair was proposed for raising"


def test_a_band_is_never_proposed_for_raising(triage):
    """A band has two edges, and clearing one of them by a margin means approaching the
    other. "Comfortably exceeded" is not a thing a band can be."""
    session, product = triage
    bound = binding_gate.confirmed_metrics(session, product=product)
    metric, ref = next(iter(bound.items()))
    rows = queries.series_for(session, product.id, metric)
    clause = session.execute(
        select(Clause).where(
            Clause.product_id == product.id, Clause.ref == ref, Clause.status == "active"
        )
    ).scalars().one()
    clause.value = min(r.value for r in rows) * 0.5
    clause.value_high = max(r.value for r in rows) * 2
    clause.direction = "within_band"
    session.flush()

    result = _run(session, product)
    assert not [
        p for p in result.proposals
        if p.target == f"clause:{ref}" and p.field_name == "value"
    ]


# -- what is deliberately not proposed -------------------------------------------


def test_an_unmeasurable_promise_is_declined_with_its_reason(policydesk):
    """Read and deliberately not proposed for, with the reason — which is a different
    answer from "not looked at", the distinction `failing_cases_state` also exists for."""
    session, product = policydesk
    result = _run(session, product)
    declined = [d for d in result.declined if d.get("detail") == "no_assertion"]
    if not declined:
        pytest.skip("this product has no promise nothing measures")
    assert "would be a guess" in declined[0]["reason"]


def test_an_underspecified_finding_produces_no_proposal(triage):
    """H8 asks for the condition to be reported. What proposal it justifies is specified
    nowhere, and choosing one here would be the Layer deciding what the spec should say."""
    session, product = triage
    result = _run(session, product)
    for proposal in result.proposals:
        row = session.execute(
            select(Proposal).where(Proposal.id == proposal.proposal_id)
        ).scalars().one()
        assert row.capability in (
            generators.FROM_DRIFT, generators.FROM_UNENFORCED,
            generators.FROM_UNCOVERED, generators.FROM_EASY_BAR,
        )


# -- the generated queue is decidable --------------------------------------------


@pytest.mark.ac("AC-16")
def test_every_generated_proposal_can_be_decided(triage):
    """The point of the whole step: AC-16 needs proposals a person can actually decide,
    not rows that refuse when someone tries."""
    session, product = triage
    from layer.core import actors

    actors.add(session, org_id=product.org_id, email="pm@example.com", role="pm")
    result = _run(session, product)
    assert result.proposals

    for proposal in result.proposals:
        outcome = decisions.accept(
            session, proposal_id=proposal.proposal_id, by="pm@example.com"
        )
        assert outcome["decision"] == "accepted", outcome
    report = decisions.acceptance(session, product=product)
    assert report["accepted"] == len(result.proposals)
    assert report["rate"] == 1.0
