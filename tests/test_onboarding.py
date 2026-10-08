"""Onboarding: the only way content enters the Layer.

AC-1, AC-9, AC-13, AC-17, AC-18, AC-19, AC-20, AC-21, EC-3, EC-6, H3, H6, H15, B3 rules 8
and 9.

Two things are being proven here. That the seven steps gate each other, so a product cannot
reach a state its evidence does not support. And that a product the Layer has never seen
onboards on configuration alone — which is why the third fixture, in a style nothing else in
the suite uses, gets the same treatment as the two real ones.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from layer.core.db import org_session
from layer.core.errors import NotLive, UnknownProduct
from layer.db.models import (
    AuditEvent,
    Binding,
    CaseResult,
    Clause,
    Observation,
    Product,
)
from layer.adapters.base import ObservationCandidate
from layer.metrics.engine import CaseOutcome
from layer.onboarding import bindings as binding_gate
from layer.onboarding import persist, run, state

from conftest import make_org

FIXTURES = Path(__file__).parent / "fixtures"
REFERENCE = Path("/Users/surabhideb/Desktop/chatbot-lab")
ACTOR = "pm@example.invalid"


# -- an unfamiliar product, in its own repository ---------------------------------


@pytest.fixture(scope="module")
def alien_repo(tmp_path_factory) -> Path:
    """A git repository holding the third fixture and nothing else.

    Built rather than committed into this repository so the test does not depend on the
    working tree being clean, and so the Layer is reading a product that is genuinely not
    its own.
    """
    root = tmp_path_factory.mktemp("wayfinder")
    (root / "docs").mkdir()
    (root / "runs").mkdir()
    (root / "ci").mkdir()
    shutil.copy(FIXTURES / "alien_spec.md", root / "docs" / "service.md")
    shutil.copy(FIXTURES / "alien_gate.py", root / "ci" / "verify.py")
    for path in sorted((FIXTURES / "alien_runs").glob("*.json")):
        shutil.copy(path, root / "runs" / path.name)

    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "T")
    git("add", "-A")
    git("commit", "-m", "wayfinder")
    return root


ALIEN_EVAL = {
    "globs": ["runs/*.json"],
    "readers": [{
        "glob": "runs/*.json",
        "run_id": "/header/ticket", "measured_at": "/header/when",
        "prompt_version": "/header/prompt", "corpus_sha": "/header/corpus",
        "code_rev": "/header/build",
        "metrics": [
            {"metric": "suggestion_correct", "kind": "accuracy", "rows": "/checks",
             "actual": "suggested", "expected": "wanted", "id_field": "ref"},
            {"metric": "p95_round_trip", "kind": "percentile", "rows": "/checks",
             "field": "ms", "p": 95, "unit": "duration_ms"},
            # Named the same as a row in this product's own signals table, so the suite
            # exercises both paths: a name match proposed by the Layer, and a prose bar a
            # human has to pair by hand.
            {"metric": "accepted_suggestions", "kind": "rate", "rows": "/checks",
             "where": {"not": {"field": "suggested", "op": "eq", "value": "cannot_tell"}}},
        ],
    }],
}


def pair_everything(session, product) -> int:
    """Decide every pairing the way a human at step 5 would.

    Name matches are confirmed as proposed. Beyond those this product states its bars in
    prose, so its clauses carry no metric name and the measured numbers are paired with the
    promises they answer by hand — the case step 5 exists for rather than an awkward
    exception. Paired by the stated value, which is how a reviewer would recognise them.
    """
    confirmed = 0
    for candidate in binding_gate.propose(session, product=product).pending:
        binding_gate.decide(
            session, product=product, metric=candidate.metric,
            clause_ref=candidate.clause_ref, decision="confirmed", by=ACTOR,
        )
        confirmed += 1

    wanted = {"suggestion_correct": 0.92}
    for metric, value in wanted.items():
        ref = session.execute(
            select(Clause.ref).where(
                Clause.product_id == product.id,
                Clause.status == "active",
                Clause.value == value,
                Clause.metric.is_(None),
            ).limit(1)
        ).scalar_one_or_none()
        if ref:
            binding_gate.decide(
                session, product=product, metric=metric, clause_ref=ref,
                decision="confirmed", by=ACTOR,
                note="paired by hand: the source states this bar in prose",
            )
            confirmed += 1
    return confirmed


def onboard_alien(session, org_id, repo: Path, *, with_code=True) -> Product:
    product = state.register(
        session, org_id=org_id, key="wayfinder", name="Wayfinder",
        ref_prefix="WF", actor=ACTOR,
    )
    common = {"local_path": str(repo), "repo_url": "https://host.example/w/wayfinder"}
    state.bind_source(
        session, product=product, role="spec", kind="repo", actor=ACTOR,
        config={**common, "path": "docs/service.md",
                "target_columns": ["bar"], "rationale_columns": ["commentary"]},
    )
    state.bind_source(
        session, product=product, role="eval", kind="repo", actor=ACTOR,
        config={**common, **ALIEN_EVAL},
    )
    if with_code:
        state.bind_source(
            session, product=product, role="code", kind="repo", actor=ACTOR,
            config={**common, "files": ["ci/verify.py"],
                    "metric_aliases": {"suggestion_correct": ["correct"]}},
        )
    return product


# -- the steps gate each other ---------------------------------------------------


class TestTheStepsGateEachOther:
    def test_a_product_starts_at_registering(self):
        org = make_org("steps")
        with org_session(org) as session:
            product = state.register(
                session, org_id=org, key="p", name="P", actor=ACTOR
            )
            assert (product.status, product.onboarding_step) == ("registering", 1)

    def test_importing_a_spec_without_a_spec_source_is_refused(self):
        org = make_org("steps")
        with org_session(org) as session:
            product = state.register(session, org_id=org, key="p", name="P", actor=ACTOR)
            with pytest.raises(state.StepNotReady) as caught:
                run.import_spec(session, product=product, actor=ACTOR)
            assert "no spec source" in str(caught.value)

    def test_one_source_is_not_enough_to_leave_step_one(self, alien_repo):
        """Step 2 needs a spec *and* an eval source: a promise with no measurement, or a
        measurement with no promise, cannot be reasoned about."""
        org = make_org("steps")
        with org_session(org) as session:
            product = state.register(session, org_id=org, key="p", name="P", actor=ACTOR)
            state.bind_source(
                session, product=product, role="spec", kind="repo", actor=ACTOR,
                config={"local_path": str(alien_repo), "repo_url": "u",
                        "path": "docs/service.md"},
            )
            assert product.status == "registering"

    @pytest.mark.ac("AC-17")
    def test_nothing_is_proposed_without_a_confirmed_binding(self, alien_repo):
        """AC-17 and B3 rule 8. Before step 5 the Layer does not know which number answers
        which promise, so anything it proposed would be invented."""
        org = make_org("gate")
        with org_session(org) as session:
            product = onboard_alien(session, org, alien_repo)
            run.import_spec(session, product=product, actor=ACTOR)
            run.backfill(session, product=product, actor=ACTOR)

            assert product.status == "sources_bound"
            with pytest.raises(NotLive):
                state.require_live(session, product=product)

    def test_measuring_before_the_gate_is_refused_and_says_what_is_pending(self, alien_repo):
        org = make_org("gate")
        with org_session(org) as session:
            product = onboard_alien(session, org, alien_repo)
            run.import_spec(session, product=product, actor=ACTOR)
            run.backfill(session, product=product, actor=ACTOR)
            with pytest.raises(state.StepNotReady) as caught:
                state.measure(session, product=product, actor=ACTOR)
            assert "awaiting a decision" in str(caught.value)

    @pytest.mark.ac("AC-20")
    def test_an_unregistered_product_is_refused_by_name(self):
        """AC-20: a clean install names what is missing rather than returning nothing.
        B3 rule 9: an unresolvable product is rejected at write time."""
        org = make_org("empty")
        with org_session(org) as session:
            with pytest.raises(UnknownProduct) as caught:
                state.get(session, key="never-registered")
            assert "never-registered" in str(caught.value)


# -- identity, which is AC-1 -----------------------------------------------------


class TestIdentity:
    def _import(self, session, org, repo) -> Product:
        product = onboard_alien(session, org, repo, with_code=False)
        run.import_spec(session, product=product, actor=ACTOR)
        return product

    @pytest.mark.ac("AC-1")
    def test_re_importing_an_unchanged_spec_writes_nothing(self, alien_repo):
        org = make_org("identity")
        with org_session(org) as session:
            product = self._import(session, org, alien_repo)
            first = session.execute(
                select(func.count()).select_from(Clause)
            ).scalar_one()

            again = run.import_spec(session, product=product, actor=ACTOR)

            assert "0 new, 0 reworded" in again.detail
            assert session.execute(
                select(func.count()).select_from(Clause)
            ).scalar_one() == first

    @pytest.mark.ac("AC-1")
    @pytest.mark.hard_case("H3")
    def test_rewording_a_statement_keeps_its_ref_and_bumps_its_version(self, alien_repo):
        """AC-1 as written, and H3. PRD B2 calls stable identity "the load-bearing
        requirement. Without it there is nothing to link to."""
        org = make_org("identity")
        with org_session(org) as session:
            product = self._import(session, org, alien_repo)
            clause = session.execute(
                select(Clause).where(Clause.metric.is_not(None)).limit(1)
            ).scalars().one()
            ref, metric, before = clause.ref, clause.metric, clause.version

            from layer.adapters.base import ClauseCandidate, ImportReport
            from layer.onboarding import persist

            report = ImportReport(source_role="spec", enumerated=1)
            report.candidates = [ClauseCandidate(
                identity_key="reworded-entirely", metric=metric,
                statement="Completely different words saying the very same thing.",
                kind=clause.kind, comparator=clause.comparator, value=clause.value,
            )]
            persist.write_clauses(
                session, product=product, report=report, source_id=None, actor=ACTOR
            )

            rows = session.execute(
                select(Clause).where(Clause.ref == ref).order_by(Clause.version)
            ).scalars().all()
            assert [r.version for r in rows] == [before, before + 1]
            assert [r.status for r in rows] == ["superseded", "active"]
            assert rows[0].superseded_by_id == rows[1].id

    @pytest.mark.ac("AC-1")
    def test_two_clauses_with_different_metrics_never_merge(self, alien_repo):
        """A regression test with teeth. "p50 latency under 1 second" and "p95 latency under
        3 seconds" score 0.906 on text similarity, and the second was stored as version 2 of
        the first — two distinct bars merged into one ref, with the p50 clause silently gone.
        A differing metric name now vetoes a similarity match outright."""
        org = make_org("identity")
        with org_session(org) as session:
            product = onboard_alien(session, org, alien_repo, with_code=False)

            from layer.adapters.base import ClauseCandidate, ImportReport
            from layer.onboarding import persist

            report = ImportReport(source_role="spec", enumerated=1)
            report.candidates = [
                ClauseCandidate(identity_key="a", metric="p50_latency", kind="threshold",
                                statement="p50 latency under 1 second.",
                                comparator="<=", value=1.0, unit="duration_s"),
                ClauseCandidate(identity_key="b", metric="p95_latency", kind="threshold",
                                statement="p95 latency under 3 seconds.",
                                comparator="<=", value=3.0, unit="duration_s"),
            ]
            written = persist.write_clauses(
                session, product=product, report=report, source_id=None, actor=ACTOR
            )

            assert len(written.created) == 2
            assert written.reworded == []
            assert len(set(written.created)) == 2

    def test_a_clause_whose_text_only_changes_punctuation_is_unchanged(self, alien_repo):
        """Otherwise a comma spends a version number and muddies the history."""
        org = make_org("identity")
        with org_session(org) as session:
            product = onboard_alien(session, org, alien_repo, with_code=False)
            from layer.adapters.base import ClauseCandidate, ImportReport
            from layer.onboarding import persist

            def once(statement):
                report = ImportReport(source_role="spec", enumerated=1)
                report.candidates = [ClauseCandidate(
                    identity_key="x", metric="m", kind="threshold",
                    statement=statement, comparator=">=", value=0.9,
                )]
                return persist.write_clauses(
                    session, product=product, report=report, source_id=None, actor=ACTOR
                )

            once("Accuracy must be at least 90%.")
            second = once("accuracy must be at least 90%")
            assert second.unchanged and not second.reworded


# -- the gate itself -------------------------------------------------------------


class TestTheBindingGate:
    def _ready(self, session, org, repo) -> Product:
        product = onboard_alien(session, org, repo, with_code=False)
        run.import_spec(session, product=product, actor=ACTOR)
        run.backfill(session, product=product, actor=ACTOR)
        return product

    def test_candidates_are_proposed_with_the_evidence_behind_them(self, alien_repo):
        org = make_org("bindings")
        with org_session(org) as session:
            product = self._ready(session, org, alien_repo)
            proposal = binding_gate.propose(session, product=product)

            assert proposal.pending
            for candidate in proposal.pending:
                assert candidate.observations > 0
                assert candidate.clause_statement
                assert candidate.matched_by in (
                    "exact metric name", "normalised metric name"
                )

    def test_a_binding_never_proposed_cannot_be_confirmed(self, alien_repo):
        """A binding invented at the point of confirmation has had no proposal, no evidence
        shown, and nothing for the audit record to point at."""
        org = make_org("bindings")
        with org_session(org) as session:
            product = self._ready(session, org, alien_repo)
            with pytest.raises(binding_gate.UnknownCandidate):
                binding_gate.decide(
                    session, product=product, metric="invented",
                    clause_ref="WF-9.9", decision="confirmed", by=ACTOR,
                )

    @pytest.mark.edge_case("EC-6")
    def test_the_first_decision_stands_and_the_second_is_told_by_whom(self, alien_repo):
        """EC-6 in miniature: first decision wins, and the second learns what happened
        rather than silently overwriting a named human's judgement."""
        org = make_org("bindings")
        with org_session(org) as session:
            product = self._ready(session, org, alien_repo)
            candidate = binding_gate.propose(session, product=product).pending[0]
            binding_gate.decide(
                session, product=product, metric=candidate.metric,
                clause_ref=candidate.clause_ref, decision="confirmed", by="first@x.invalid",
            )
            with pytest.raises(binding_gate.UnknownCandidate) as caught:
                binding_gate.decide(
                    session, product=product, metric=candidate.metric,
                    clause_ref=candidate.clause_ref, decision="rejected",
                    by="second@x.invalid",
                )
            assert "first@x.invalid" in str(caught.value)

    def test_an_unattributed_decision_is_refused(self):
        """A binding with no decider is not an assertion anyone made."""
        org = make_org("bindings")
        with org_session(org) as session:
            product = state.register(session, org_id=org, key="p", name="P", actor=ACTOR)
            with pytest.raises(ValueError):
                binding_gate.decide(
                    session, product=product, metric="m", clause_ref="r",
                    decision="confirmed", by="",
                )

    def test_a_rejected_binding_is_not_joined_through(self, alien_repo):
        """A pairing a human declined must not come back as a verdict."""
        org = make_org("bindings")
        with org_session(org) as session:
            product = self._ready(session, org, alien_repo)
            for candidate in binding_gate.propose(session, product=product).pending:
                binding_gate.decide(
                    session, product=product, metric=candidate.metric,
                    clause_ref=candidate.clause_ref, decision="rejected", by=ACTOR,
                    note="measures something else",
                )
            assert binding_gate.confirmed_metrics(session, product=product) == {}

    def test_the_gate_opens_only_when_something_was_confirmed(self, alien_repo):
        """All rejected is a complete review and an empty assertion set, so the product has
        nothing to propose about and must not advance."""
        org = make_org("bindings")
        with org_session(org) as session:
            product = self._ready(session, org, alien_repo)
            for candidate in binding_gate.propose(session, product=product).pending:
                binding_gate.decide(
                    session, product=product, metric=candidate.metric,
                    clause_ref=candidate.clause_ref, decision="rejected", by=ACTOR,
                )
            assert state.refresh(session, product=product, actor=ACTOR) == "sources_bound"


# -- verdicts --------------------------------------------------------------------


class TestVerdicts:
    def _live(self, session, org, repo) -> Product:
        product = onboard_alien(session, org, repo, with_code=False)
        run.import_spec(session, product=product, actor=ACTOR)
        run.backfill(session, product=product, actor=ACTOR)
        pair_everything(session, product)
        state.refresh(session, product=product, actor=ACTOR)
        state.measure(session, product=product, actor=ACTOR)
        return product

    def test_every_clause_carries_a_verdict(self, alien_repo):
        """PRD B6 sets "clauses with a verdict" at 100%, so silence is not an option."""
        org = make_org("verdicts")
        with org_session(org) as session:
            product = self._live(session, org, alien_repo)
            missing = session.execute(
                select(func.count()).select_from(Clause).where(
                    Clause.product_id == product.id, Clause.verdict.is_(None)
                )
            ).scalar_one()
            assert missing == 0
            assert product.status == "live"

    @pytest.mark.hard_case("H6")
    def test_a_clause_with_no_bar_is_not_applicable_rather_than_failing(self, alien_repo):
        """H6 and EC-7: held as written, never counted as unmeasured drift."""
        org = make_org("verdicts")
        with org_session(org) as session:
            self._live(session, org, alien_repo)
            rows = session.execute(
                select(Clause.verdict).where(Clause.comparator.is_(None))
            ).scalars().all()
            assert rows and set(rows) == {"not_applicable"}

    def test_a_bound_clause_with_no_measurement_is_not_measured(self, alien_repo):
        org = make_org("verdicts")
        with org_session(org) as session:
            self._live(session, org, alien_repo)
            unbound = session.execute(
                select(Clause.verdict).where(
                    Clause.comparator.is_not(None),
                    Clause.ref.not_in(
                        select(Binding.clause_ref).where(Binding.decision == "confirmed")
                    ),
                )
            ).scalars().all()
            assert set(unbound) <= {"not_measured"}

    @pytest.mark.ac("AC-13")
    @pytest.mark.hard_case("H15")
    def test_state_and_verdict_move_independently(self, alien_repo):
        """AC-13. A measured clause can be cannot_confirm, and H15's case — met while
        provisional, passing a bar nobody justified — stays expressible."""
        org = make_org("verdicts")
        with org_session(org) as session:
            product = self._live(session, org, alien_repo)
            bound = binding_gate.confirmed_metrics(session, product=product)
            rows = session.execute(
                select(Clause.state, Clause.verdict).where(
                    Clause.ref.in_(list(bound.values()))
                )
            ).all()
            assert rows
            assert all(state_ == "measured" for state_, _ in rows)
            unbound_states = session.execute(
                select(Clause.state).where(
                    Clause.comparator.is_not(None),
                    Clause.ref.not_in(list(bound.values())),
                )
            ).scalars().all()
            assert set(unbound_states) <= {"provisional"}


# -- the audit trail -------------------------------------------------------------


@pytest.mark.ac("AC-9")
class TestEveryWriteIsAudited:
    def test_each_step_leaves_an_event_naming_its_actor(self, alien_repo):
        org = make_org("audit")
        with org_session(org) as session:
            product = onboard_alien(session, org, alien_repo, with_code=False)
            run.import_spec(session, product=product, actor=ACTOR)
            run.backfill(session, product=product, actor=ACTOR)
            for candidate in binding_gate.propose(session, product=product).pending:
                binding_gate.decide(
                    session, product=product, metric=candidate.metric,
                    clause_ref=candidate.clause_ref, decision="confirmed", by=ACTOR,
                )
            state.refresh(session, product=product, actor=ACTOR)
            state.measure(session, product=product, actor=ACTOR)

            actions = set(
                session.execute(select(AuditEvent.action)).scalars().all()
            )
            for required in (
                "product_registered", "source_bound", "spec_imported", "clause_created",
                "observations_backfilled", "binding_confirmed", "verdicts_computed",
                "product_status_changed",
            ):
                assert required in actions, f"{required} left no audit event"

            actors = set(session.execute(select(AuditEvent.actor)).scalars().all())
            assert actors == {ACTOR}

    def test_a_clause_write_records_the_assumptions_behind_it(self, alien_repo):
        """A bar read from prose carries assumptions — an inferred comparator, an unnamed
        metric. They travel into the audit record, so a reviewer can see what was guessed."""
        org = make_org("audit")
        with org_session(org) as session:
            product = onboard_alien(session, org, alien_repo, with_code=False)
            run.import_spec(session, product=product, actor=ACTOR)
            details = session.execute(
                select(AuditEvent.detail).where(AuditEvent.action == "clause_created")
            ).scalars().all()
            assert any(d.get("assumptions") for d in details)


# -- agnosticism -----------------------------------------------------------------


@pytest.mark.ac("AC-21")
def test_an_unfamiliar_product_reaches_live_with_no_code_change(alien_repo):
    """The criterion the whole build is arranged around.

    This product's spec states its bars in prose, its runs use a format neither reference
    product uses, and its gate is written in a different idiom. Nothing about it appears
    anywhere in `layer/`. Everything it needed was configuration.
    """
    org = make_org("agnostic")
    with org_session(org) as session:
        product = onboard_alien(session, org, alien_repo)

        spec = run.import_spec(session, product=product, actor=ACTOR)
        backfill = run.backfill(session, product=product, actor=ACTOR)

        assert spec.report.complete and not spec.report.failed
        assert backfill.report.complete and not backfill.report.failed
        assert backfill.report.imported == 3

        pair_everything(session, product)
        state.refresh(session, product=product, actor=ACTOR)
        state.measure(session, product=product, actor=ACTOR)

        # Scanned after step 5 on purpose. This product states its bars in prose, so its
        # clauses carry no metric name and the confirmed binding is what tells the scan
        # which number to look for in CI.
        scan = run.scan_enforcement(session, product=product, actor=ACTOR)

        assert product.status == "live"
        status = state.status(session, product=product)
        assert status.clauses > 0
        assert status.observations == 9  # three metrics across three runs
        assert status.confirmed_bindings >= 1
        assert scan.report.candidates, "the alien gate's bar was not found"


@pytest.mark.ac("AC-19")
def test_two_products_in_one_org_stay_separate(alien_repo):
    """AC-19: queryable separately, and no record of one attributed to the other."""
    org = make_org("multi")
    with org_session(org) as session:
        first = onboard_alien(session, org, alien_repo, with_code=False)
        run.import_spec(session, product=first, actor=ACTOR)
        run.backfill(session, product=first, actor=ACTOR)

        second = state.register(
            session, org_id=org, key="wayfinder-eu", name="Wayfinder EU",
            ref_prefix="WFE", actor=ACTOR,
        )
        common = {"local_path": str(alien_repo), "repo_url": "https://host.example/w/eu"}
        state.bind_source(session, product=second, role="spec", kind="repo", actor=ACTOR,
                          config={**common, "path": "docs/service.md",
                                  "target_columns": ["bar"]})
        state.bind_source(session, product=second, role="eval", kind="repo", actor=ACTOR,
                          config={**common, **ALIEN_EVAL})
        run.import_spec(session, product=second, actor=ACTOR)
        run.backfill(session, product=second, actor=ACTOR)

        for product, prefix in ((first, "WF-"), (second, "WFE-")):
            refs = session.execute(
                select(Clause.ref).where(Clause.product_id == product.id)
            ).scalars().all()
            assert refs
            assert all(r.startswith(prefix) for r in refs), (
                f"{product.key} holds a ref belonging to the other product"
            )

        for product in (first, second):
            rows = session.execute(
                select(func.count()).select_from(Observation).where(
                    Observation.product_id == product.id
                )
            ).scalar_one()
            assert rows == 9


@pytest.mark.ac("AC-18")
def test_an_unrecognised_pattern_onboards_with_no_defaults(alien_repo):
    """AC-18: an unrecognised or absent pattern must complete onboarding rather than be
    refused, so there is deliberately nothing that validates it."""
    org = make_org("pattern")
    for pattern in (None, "something-nobody-listed"):
        with org_session(org) as session:
            product = state.register(
                session, org_id=org, key=f"p-{pattern}", name="P",
                pattern=pattern, ref_prefix="PX", actor=ACTOR,
            )
            assert product.pattern == pattern
            assert product.status == "registering"


# -- against the reference products ----------------------------------------------


@pytest.mark.skipif(not REFERENCE.exists(), reason="reference products not present")
@pytest.mark.ac("AC-2")
def test_a_reference_product_reaches_live_and_its_history_is_not_double_counted():
    """The second half of AC-2, at the database rather than the adapter: a repeated backfill
    stores nothing and says how many it refused."""
    org = make_org("reference")
    common = {"local_path": str(REFERENCE), "repo_url": "https://github.com/SurabhiDeb/chatbot-lab"}
    eval_config = {
        **common,
        "globs": ["products/triage/runs/*.json"],
        "readers": [{
            "glob": "products/triage/runs/*.json",
            "measured_at": "/meta/timestamp_utc", "corpus_sha": "/meta/dataset_sha",
            "prompt_version": "/meta/prompt_sha",
            "metrics": [
                {"metric": "team_accuracy", "kind": "accuracy",
                 "actual": "team", "expected": "labelled_team"},
                {"metric": "escalation_recall", "kind": "recall",
                 "actual": "escalate", "expected": "labelled_escalate",
                 "coerce": {"labelled_escalate": "bool"}},
            ],
        }],
    }
    with org_session(org) as session:
        product = state.register(
            session, org_id=org, key="triage", name="Triage", ref_prefix="TRI", actor=ACTOR
        )
        state.bind_source(session, product=product, role="spec", kind="repo", actor=ACTOR,
                          config={**common, "path": "products/triage/SPEC.md"})
        state.bind_source(session, product=product, role="eval", kind="repo", actor=ACTOR,
                          config=eval_config)

        run.import_spec(session, product=product, actor=ACTOR)
        first = run.backfill(session, product=product, actor=ACTOR)
        again = run.backfill(session, product=product, actor=ACTOR)

        assert "94 observations stored, 0 duplicates refused" in first.detail
        assert "0 observations stored, 94 duplicates refused" in again.detail

        pair_everything(session, product)
        state.refresh(session, product=product, actor=ACTOR)
        counts = state.measure(session, product=product, actor=ACTOR)

        assert product.status == "live"
        # cannot_confirm dominating is the honest state, not a defect (PRD B2).
        assert counts.get("cannot_confirm", 0) >= 1
        assert sum(counts.values()) == state.status(session, product=product).clauses


# -- the evidence spine, at the database ------------------------------------------


TRIAGE_WITH_CASES = {
    "globs": ["products/triage/runs/*.json"],
    "readers": [{
        "glob": "products/triage/runs/*.json",
        "measured_at": "/meta/timestamp_utc", "corpus_sha": "/meta/dataset_sha",
        "prompt_version": "/meta/prompt_sha",
        # The case shape, stated once for every metric this reader computes. The rows
        # carry no trace at all, which is `not_applicable` and not a missing value.
        "cases": {"input_field": "message"},
        "metrics": [
            {"metric": "team_accuracy", "kind": "accuracy",
             "actual": "team", "expected": "labelled_team"},
            {"metric": "escalation_recall", "kind": "recall",
             "actual": "escalate", "expected": "labelled_escalate",
             "coerce": {"labelled_escalate": "bool"}},
        ],
    }],
}


@pytest.mark.skipif(not REFERENCE.exists(), reason="reference products not present")
class TestTheEvidenceSpineIsStored:
    """AC-22, AC-26, AC-27. The rows a drift finding will cite.

    The engine produces the cases and the adapter carries them; this is where they
    become evidence. Everything here is counted against the committed run files: 47
    runs holding 506 cases between them, between 1 and 20 each. Not 47 times the 14 a
    run's own metadata reports, which is the arithmetic a reader checking this by hand
    would reach for first and is wrong.
    """

    COMMON = {
        "local_path": str(REFERENCE),
        "repo_url": "https://github.com/SurabhiDeb/chatbot-lab",
    }

    @pytest.fixture
    def backfilled(self):
        org = make_org("spine")
        with org_session(org) as session:
            product = state.register(
                session, org_id=org, key="triage", name="Triage",
                ref_prefix="TRI", actor=ACTOR,
            )
            state.bind_source(
                session, product=product, role="spec", kind="repo", actor=ACTOR,
                config={**self.COMMON, "path": "products/triage/SPEC.md"},
            )
            state.bind_source(
                session, product=product, role="eval", kind="repo", actor=ACTOR,
                config={**self.COMMON, **TRIAGE_WITH_CASES},
            )
            run.import_spec(session, product=product, actor=ACTOR)
            yield session, product, run.backfill(session, product=product, actor=ACTOR)

    @pytest.mark.ac("AC-26")
    def test_every_case_in_every_run_gets_a_row(self, backfilled):
        """Not only the failures. The counts are what drift detection reads, so a table
        of failures alone would make every rate unrecomputable from this table."""
        session, product, result = backfilled
        stored = session.execute(
            select(func.count()).select_from(CaseResult)
            .join(Observation, Observation.id == CaseResult.observation_id)
            .where(Observation.product_id == product.id)
        ).scalar_one()

        assert stored == 506 * 2
        assert f"{506 * 2} case rows stored" in result.detail

    @pytest.mark.ac("AC-22")
    def test_the_stored_cases_reproduce_the_number_they_sit_under(self, backfilled):
        """The property that makes them evidence rather than decoration, asserted over
        every stored observation rather than a sampled one."""
        session, product, _ = backfilled
        rows = session.execute(
            select(
                Observation.id, Observation.passed, Observation.total,
                func.count().filter(CaseResult.outcome == "pass"),
                func.count().filter(CaseResult.outcome != "skipped"),
            )
            .join(CaseResult, CaseResult.observation_id == Observation.id)
            .where(Observation.product_id == product.id)
            .group_by(Observation.id, Observation.passed, Observation.total)
        ).all()

        assert len(rows) == 47 * 2
        disagreeing = [
            r for r in rows if (r[1], r[2]) != (r[3], r[4])
        ]
        assert disagreeing == [], "stored cases do not add up to their own observation"

    @pytest.mark.ac("AC-27")
    def test_a_case_carries_its_observations_time_and_not_the_clock(self, backfilled):
        """`measured_at` is in the primary key because it is the partition key. Reading
        the clock here instead would write a second copy of every case on every
        backfill, and one run has one time."""
        session, product, _ = backfilled
        mismatched = session.execute(
            select(func.count())
            .select_from(CaseResult)
            .join(Observation, Observation.id == CaseResult.observation_id)
            .where(
                Observation.product_id == product.id,
                CaseResult.measured_at != Observation.measured_at,
            )
        ).scalar_one()
        assert mismatched == 0

    @pytest.mark.ac("AC-2")
    def test_a_second_backfill_stores_no_cases_either(self, backfilled):
        session, product, _ = backfilled
        again = run.backfill(session, product=product, actor=ACTOR)

        assert "0 case rows stored" in again.detail
        assert f"{506 * 2} refused as duplicates" in again.detail

    @pytest.mark.ac("AC-26")
    def test_an_input_is_stored_for_a_failing_case_and_for_no_other(self, backfilled):
        """The half of AC-26 the CHECK cannot carry: non-null on a failing row, where
        the source carries an input at all. This source does."""
        session, product, _ = backfilled
        rows = session.execute(
            select(CaseResult.outcome, CaseResult.input_redacted)
            .join(Observation, Observation.id == CaseResult.observation_id)
            .where(Observation.product_id == product.id)
        ).all()

        failures = [r for r in rows if r[0] in ("fail", "error")]
        assert failures, "no failing case was stored, so AC-26 proves nothing here"
        assert all(r[1] for r in failures), "a failing case stored no input"
        assert all(r[1] is None for r in rows if r[0] not in ("fail", "error"))

    @pytest.mark.ac("AC-26")
    def test_one_case_is_a_failure_under_one_metric_and_skipped_by_another(self, backfilled):
        """AC-26 asks for this explicitly, and it is the clearest statement of what an
        outcome is: a property of the metric, not of the row. The same case in the same
        run is a failure of one metric and outside what the other measures."""
        session, product, _ = backfilled
        rows = session.execute(
            select(
                Observation.run_id, CaseResult.case_id,
                Observation.metric, CaseResult.outcome,
            )
            .join(CaseResult, CaseResult.observation_id == Observation.id)
            .where(Observation.product_id == product.id)
        ).all()

        by_case: dict[tuple, dict] = {}
        for run_id, case_id, metric, outcome in rows:
            by_case.setdefault((run_id, case_id), {})[metric] = outcome

        both = [
            key for key, outcomes in by_case.items()
            if "fail" in outcomes.values() and "skipped" in outcomes.values()
        ]
        assert both, (
            "no case is a failure under one metric and skipped by another, so the "
            "distinction is not proven against real data"
        )

    @pytest.mark.ac("AC-24")
    def test_a_source_with_no_tracing_stores_no_pointer_rather_than_a_dead_one(
        self, backfilled
    ):
        session, product, _ = backfilled
        pointers = session.execute(
            select(func.count())
            .select_from(CaseResult)
            .join(Observation, Observation.id == CaseResult.observation_id)
            .where(
                Observation.product_id == product.id,
                (CaseResult.trace_id.is_not(None)) | (CaseResult.trace_url.is_not(None)),
            )
        ).scalar_one()
        assert pointers == 0

    @pytest.mark.ac("AC-9")
    def test_the_backfills_audit_event_records_what_the_spine_took(self, backfilled):
        """A count of rows is not an audit trail. The event names the coverage and every
        refusal, so a human can tell proof that was not stored from proof that does not
        exist."""
        session, product, _ = backfilled
        detail = session.execute(
            select(AuditEvent.detail).where(
                AuditEvent.action == "observations_backfilled"
            ).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(1)
        ).scalar_one()

        assert detail["cases"]["inserted"] == 506 * 2
        assert detail["cases"]["measurements_with_cases"] == 47 * 2
        assert detail["cases"]["trace_pointer_coverage"] == "not_applicable"
        assert detail["cases"]["refused_disagreeing"] == []
        assert detail["cases"]["refused_unmatched"] == []
        assert detail["cases"]["inputs_stored"] > 0


class TestTheSpineRefusesWhatIsNotEvidence:
    """What `write_cases` will not take, and what it says instead.

    Every refusal here is a case a finding will not be able to cite, so none of them is
    allowed to be silent: the rows are not written, the reason is named in the audit
    event, and the backfill's own line says a refusal happened.
    """

    def _candidate(self, *, passed, total, cases, metric="m", run="r1"):
        return ObservationCandidate(
            metric=metric,
            value=passed / total,
            measured_at=datetime(2026, 9, 15, 13, 0, tzinfo=UTC),
            passed=passed,
            total=total,
            run_id=run,
            run_url=f"https://host.example/blob/ef07ac9/{run}.json",
            cases=cases,
        )

    def _write(self, session, product, candidate):
        return persist.write_observations(
            session, product=product, candidates=[candidate],
            source_id=None, actor=ACTOR,
        )

    @pytest.fixture
    def product(self):
        org = make_org("refusals")
        with org_session(org) as session:
            yield session, state.register(
                session, org_id=org, key="k", name="K", ref_prefix="K", actor=ACTOR
            )

    @pytest.mark.ac("AC-22")
    def test_cases_that_do_not_add_up_to_their_number_are_refused(self, product):
        """The number says two of two passed and the cases say one of them failed. One
        of the two is wrong and the Layer cannot tell which, so it stores the number and
        refuses the cases: a finding citing evidence that contradicts its own claim is
        worse than one citing none."""
        session, row = product
        written = self._write(session, row, self._candidate(
            passed=2, total=2,
            cases=(CaseOutcome("1", "pass"), CaseOutcome("2", "fail", "[redacted]")),
        ))

        assert written.inserted == 1, "the observation itself should still be stored"
        assert written.cases.inserted == 0
        assert written.cases.disagreeing == ["m@r1"]
        stored = session.execute(select(func.count()).select_from(CaseResult)).scalar_one()
        assert stored == 0

    def test_two_cases_sharing_an_id_keep_the_first_and_name_the_second(self, product):
        """Two rows in one run under one id cannot be addressed individually, so the
        second is refused rather than overwriting the first — and `case:102/14` would
        otherwise resolve to whichever row happened to be written last."""
        session, row = product
        written = self._write(session, row, self._candidate(
            passed=1, total=2,
            cases=(CaseOutcome("14", "pass"), CaseOutcome("14", "fail", "[redacted]")),
        ))

        assert written.cases.inserted == 1
        assert written.cases.duplicate_ids == ["m@r1/14"]

    def test_an_oversized_case_id_is_refused_rather_than_truncated(self, product):
        """A truncated id is a different id, and two cases truncating to the same string
        would silently become one piece of evidence."""
        session, row = product
        written = self._write(session, row, self._candidate(
            passed=1, total=2,
            cases=(CaseOutcome("1", "pass"), CaseOutcome("x" * 200, "fail", "[r]")),
        ))

        assert written.cases.inserted == 1
        assert len(written.cases.oversized_ids) == 1

    def test_a_refusal_reaches_the_line_the_backfill_prints(self, product):
        """A smaller number of rows is not a report. Somebody reading the step's output
        has to see that proof was refused without going to the audit log for it."""
        session, row = product
        written = self._write(session, row, self._candidate(
            passed=2, total=2,
            cases=(CaseOutcome("1", "pass"), CaseOutcome("2", "fail", "[redacted]")),
        ))
        assert written.cases.refusals == 1


@pytest.mark.ac("AC-26")
def test_a_source_whose_rows_carry_no_text_stores_no_input_at_all(alien_repo):
    """The third fixture's per-case rows hold a reference, a prediction, a label and a
    duration, and no text whatsoever. That is AC-26's `not_applicable` rather than a
    missing value, and it is why the CHECK does not demand the converse."""
    org = make_org("spine-alien")
    with org_session(org) as session:
        product = onboard_alien(session, org, alien_repo, with_code=False)
        run.import_spec(session, product=product, actor=ACTOR)
        run.backfill(session, product=product, actor=ACTOR)

        rows = session.execute(
            select(CaseResult.outcome, CaseResult.input_redacted)
            .join(Observation, Observation.id == CaseResult.observation_id)
            .where(Observation.product_id == product.id)
        ).all()

        assert rows, "the third fixture stored no cases at all"
        assert any(outcome == "fail" for outcome, _ in rows)
        assert all(text is None for _, text in rows)


def test_a_binding_across_different_units_is_refused(alien_repo):
    """A p95 measured in milliseconds bound to a bar stated in seconds would compare 1200
    against 4 and report a confident breach that is purely a unit error. The gate is the
    right place to catch it, and refusing beats converting: guessing which side is
    authoritative is how a bar gets quietly rescaled."""
    org = make_org("units")
    with org_session(org) as session:
        product = onboard_alien(session, org, alien_repo, with_code=False)
        run.import_spec(session, product=product, actor=ACTOR)
        run.backfill(session, product=product, actor=ACTOR)

        seconds_clause = session.execute(
            select(Clause.ref).where(
                Clause.product_id == product.id,
                Clause.unit == "duration_s",
                Clause.value.is_not(None),
            ).limit(1)
        ).scalar_one_or_none()
        assert seconds_clause, "the fixture should state a bar in seconds"

        with pytest.raises(binding_gate.UnknownCandidate) as caught:
            binding_gate.decide(
                session, product=product, metric="p95_round_trip",
                clause_ref=seconds_clause, decision="confirmed", by=ACTOR,
            )
        assert "different units" in str(caught.value)
