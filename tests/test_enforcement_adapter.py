"""What CI actually checks. AC-4, AC-15, H14.

The condition this adapter exists for: a gate checks the right metric at the right
threshold against the wrong set of runs, so a mid-sequence breach never fails a build.
`enforced: true, scope: latest_only` is that condition, and a boolean cannot express it.

Most of these tests are about the scan declining to claim things. A false "enforced"
leaves a real gap hidden, which is the failure the product exists to prevent; a false
"could not tell" costs a human one file read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from layer.adapters.base import Expectation, SourceDocument
from layer.adapters.code.enforcement import EnforcementAdapter
from layer.adapters.repo import RepoHandle

FIXTURE_REPO = Path("/Users/surabhideb/Desktop/chatbot-lab")

ACCURACY = Expectation("team_accuracy", ">=", 0.85, label="Overall team accuracy")
RECALL = Expectation("escalation_recall", ">=", 0.99, label="Escalation recall")


def scan(source: str, expectations=(ACCURACY,), config=None, name="gate.py"):
    return EnforcementAdapter().read(
        [SourceDocument(name, source.encode())], config or {}, expectations
    )


def only(report):
    assert len(report.candidates) == 1, report.summary()
    return report.candidates[0]


class TestScope:
    """The load-bearing column."""

    @pytest.mark.ac("AC-15")
    @pytest.mark.hard_case("H14")
    def test_a_gate_that_reads_only_the_newest_run_is_enforced_but_narrowed(self):
        source = """
        files = sorted(RUNS_DIR.glob("*.json"))
        record = json.loads(files[-1].read_text())
        MIN_TEAM_ACCURACY = 0.85
        if team_ok / n < MIN_TEAM_ACCURACY:
            fail()
        """
        fact = only(scan(source))
        assert fact.enforced is True
        assert fact.scope == "latest_only"
        assert fact.threshold == 0.85
        assert "earlier run is never seen" in fact.partial_note

    def test_a_gate_that_walks_every_run_claims_the_wider_scope(self):
        source = """
        MIN_TEAM_ACCURACY = 0.85
        for record in sorted(RUNS_DIR.glob("*.json")):
            if record.team_accuracy < MIN_TEAM_ACCURACY:
                fail()
        """
        fact = only(scan(source))
        assert fact.scope == "all_runs"
        assert fact.partial is False

    @pytest.mark.parametrize(
        "selector",
        ["runs[-1]", "runs.pop()", "max(runs)", "latest_run = pick(runs)",
         "cat runs/*.json | tail -1"],
    )
    def test_narrowing_selectors_are_recognised(self, selector):
        source = f"MIN_TEAM_ACCURACY = 0.85\n{selector}\nassert acc >= MIN_TEAM_ACCURACY"
        assert only(scan(source)).scope == "latest_only"

    def test_narrowing_wins_over_iteration(self):
        """A file may glob every run and then index the last one, which is precisely the
        shape that hides a mid-sequence breach."""
        source = """
        MIN_TEAM_ACCURACY = 0.85
        for f in RUNS_DIR.glob("*.json"):
            pass
        record = sorted(RUNS_DIR.glob("*.json"))[-1]
        assert record.accuracy >= MIN_TEAM_ACCURACY
        """
        assert only(scan(source)).scope == "latest_only"

    def test_an_unreadable_run_selection_is_undetermined_rather_than_assumed(self):
        """Neither of the confident states is true here. Claiming `all_runs` asserts full
        coverage and hides a gap; claiming `latest_only` manufactures a finding."""
        source = "MIN_TEAM_ACCURACY = 0.85\nassert report.team_accuracy >= MIN_TEAM_ACCURACY"
        fact = only(scan(source))
        assert fact.scope == "undetermined"
        assert fact.partial is True
        assert "could not be determined" in fact.partial_note

    def test_a_narrowing_token_unrelated_to_runs_is_not_read_as_run_selection(self):
        """`[-1]` on something that is not a run collection says nothing about scope."""
        source = """
        MIN_TEAM_ACCURACY = 0.85
        last_char = name[-1]
        assert report.accuracy >= MIN_TEAM_ACCURACY
        """
        assert only(scan(source)).scope == "undetermined"


class TestThresholdReading:
    def test_a_value_on_the_same_line_as_the_name_is_taken_as_the_bar(self):
        fact = only(scan("MIN_TEAM_ACCURACY = 0.85\nfor r in runs: check(r)"))
        assert fact.threshold == 0.85

    def test_a_percentage_and_a_ratio_are_the_same_bar(self):
        fact = only(scan("TEAM_ACCURACY_PCT = 85\nfor r in runs: check(r)"))
        assert fact.threshold == 0.85

    def test_a_gate_checking_a_different_number_is_reported_as_such(self):
        """The spec moved and the gate did not. A real and common condition, and the
        reason a same-line number is accepted even when it is not the stated one."""
        fact = only(scan("MIN_TEAM_ACCURACY = 0.80\nfor r in runs: check(r)"))
        assert fact.threshold == 0.80
        assert fact.partial is True
        assert "while the clause states 0.85" in fact.partial_note

    def test_an_unreadable_value_is_reported_unread_rather_than_guessed(self):
        """An earlier version fell back to "the first ratio between 0 and 1", which would
        report a threshold of 0.5 for a metric whose gate sat beside a sampling constant.
        A fabricated threshold is worse than an unread one: it reads as knowledge."""
        source = """
        SAMPLE_RATE = 0.5
        for r in runs:
            if not team_accuracy_ok(r):
                fail()
        """
        fact = only(scan(source))
        assert fact.threshold is None
        assert "could not be read" in fact.partial_note

    def test_a_hundred_percent_bar_is_not_confirmed_by_a_stray_digit(self):
        """`sum(1 for r in results ...)` is enough to make a neighbourhood match for a
        1.0 bar. This reported a gate as enforcing contract validity at 1.0 when the digit
        found was a loop accumulator."""
        source = """
        for r in runs:
            broken = sum(1 for r in results if r["problem"])
            if broken:
                fail()
        """
        fact = only(scan(source, (Expectation("contract_validity", ">=", 1.0),),
                         {"metric_aliases": {"contract_validity": ["broken"]}}))
        assert fact.threshold is None


class TestNaming:
    @pytest.mark.parametrize(
        "spelling",
        ["MIN_TEAM_ACCURACY = 0.85", "teamAccuracy = 0.85", "team-accuracy: 0.85",
         '"team accuracy" >= 0.85', "TEAM.ACCURACY = 0.85"],
    )
    def test_a_metric_is_found_however_the_code_spells_it(self, spelling):
        assert scan(f"for r in runs: pass\n{spelling}").candidates

    def test_word_order_matters(self):
        """`accuracy_team` is a different thing and must not match."""
        assert not scan("for r in runs: pass\naccuracy_team = 0.85").candidates

    def test_an_alias_finds_a_check_that_shares_no_vocabulary_with_the_clause(self):
        """The case that made aliases necessary. A gate can enforce escalation recall by
        counting missed escalations and requiring zero, never writing the words anywhere.
        Without an alias the clause is reported as unenforced, which is simply wrong."""
        source = """
        for r in runs:
            missed = count_missed(r)
            if missed:
                fail()
        """
        assert not scan(source, (RECALL,)).candidates
        with_alias = scan(source, (RECALL,), {"metric_aliases": {"escalation_recall": ["missed"]}})
        assert only(with_alias).metric == "escalation_recall"


class TestChecksWithoutTeeth:
    """A check that runs and cannot fail the build looks enforced on every dashboard."""

    @pytest.mark.parametrize(
        "marker",
        ["continue-on-error: true", "pytest products || true", "set +e",
         "@pytest.mark.xfail", "# TODO re-enable this"],
    )
    def test_a_check_that_cannot_fail_a_build_is_not_enforcement(self, marker):
        source = f"for r in runs: pass\nMIN_TEAM_ACCURACY = 0.85\n{marker}"
        fact = only(scan(source))
        assert fact.enforced is False
        assert fact.known_failing is True
        assert "cannot fail a build" in fact.partial_note


class TestReporting:
    @pytest.mark.ac("AC-4")
    def test_a_metric_no_file_mentions_yields_no_fact(self):
        """Absence is reported by the finding query as an unenforced clause. A row saying
        `enforced: false` here would duplicate that, and would also be a claim the scan
        cannot support: it knows it found nothing, not that nothing exists."""
        report = scan("for r in runs: pass\nprint('hello')")
        assert report.candidates == []
        assert [s.reason for s in report.skipped] == ["no_threshold_found"]

    def test_no_expectations_is_not_a_finding_that_ci_checks_nothing(self):
        report = EnforcementAdapter().read(
            [SourceDocument("gate.py", b"x = 1")], {}, ()
        )
        assert report.candidates == []
        assert any("not the same as finding" in n for n in report.notes)

    def test_every_document_is_accounted_for(self):
        report = EnforcementAdapter().read(
            [SourceDocument("a.py", b"MIN_TEAM_ACCURACY = 0.85\nfor r in runs: pass"),
             SourceDocument("b.py", b"print('nothing here')")],
            {}, (ACCURACY,),
        )
        assert report.enumerated == 2
        assert report.complete
        assert (report.imported, len(report.skipped)) == (1, 1)


@pytest.mark.skipif(not FIXTURE_REPO.exists(), reason="fixture repository not present")
class TestAgainstTheRealGate:
    """The instance of condition 1's root cause, read out of committed code."""

    @staticmethod
    @pytest.fixture(scope="class")
    def report():
        handle = RepoHandle.pin(FIXTURE_REPO, "https://github.com/SurabhiDeb/chatbot-lab")
        documents = [
            SourceDocument(p, handle.read(p), handle.blob_url(p))
            for p in ("products/triage/gate.py", ".github/workflows/ci.yml")
        ]
        expectations = (
            ACCURACY,
            RECALL,
            Expectation("contract_validity", ">=", 1.0, label="Contract validity"),
            Expectation("escalation_precision", ">=", 0.70, label="Escalation precision"),
            Expectation("needs_clarification_rate", "between", 0.03, 0.08),
            Expectation("p95_latency", "<=", 3.0, unit="duration_s"),
        )
        config = {"metric_aliases": {
            "escalation_recall": ["missed"],
            "contract_validity": ["broken", "problem"],
        }}
        return EnforcementAdapter().read(documents, config, expectations)

    @pytest.mark.ac("AC-15")
    @pytest.mark.hard_case("H14")
    def test_the_gate_checks_the_right_bar_against_the_wrong_run_set(self, report):
        """Condition 1's root cause, from committed code: the threshold is right, the
        value is right, and the run set is not."""
        facts = {f.metric: f for f in report.candidates}
        accuracy = facts["team_accuracy"]

        assert accuracy.enforced is True
        assert accuracy.threshold == 0.85
        assert accuracy.scope == "latest_only"
        assert accuracy.partial is True
        assert "earlier run is never seen" in accuracy.partial_note
        assert accuracy.file == "products/triage/gate.py"
        assert accuracy.line

    @pytest.mark.ac("AC-4")
    def test_the_metrics_the_gate_does_not_check_at_all(self, report):
        """AC-4: every clause metric that no file in the code source checks. These four
        are stated in the specification and absent from CI."""
        checked = {f.metric for f in report.candidates}
        assert "escalation_precision" not in checked
        assert "needs_clarification_rate" not in checked
        assert "p95_latency" not in checked

    def test_a_check_sharing_no_vocabulary_is_found_by_alias(self, report):
        """The gate enforces escalation recall by requiring zero missed escalations and
        never writes the words. Its value is reported unread, which is accurate: the gate
        checks the thing and never writes the number down."""
        facts = {f.metric: f for f in report.candidates}
        assert "escalation_recall" in facts
        assert facts["escalation_recall"].threshold is None
        assert "could not be read" in facts["escalation_recall"].partial_note

    def test_the_workflow_file_states_no_thresholds_of_its_own(self, report):
        """It runs the gate rather than checking anything, which is the usual shape and
        must not be mistaken for enforcement."""
        assert all(f.file != ".github/workflows/ci.yml" for f in report.candidates)
        assert any(
            s.identifier == ".github/workflows/ci.yml" and s.reason == "no_threshold_found"
            for s in report.skipped
        )

    def test_everything_is_accounted_for(self, report):
        assert report.complete, report.summary()
        assert not report.failed, report.summary()
