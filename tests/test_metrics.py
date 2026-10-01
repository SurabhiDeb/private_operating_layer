"""The metric engine. Tiers 1 and 2 compute; tier 3 declines.

The mechanism tests use synthetic documents, because an engine that needed a
particular product's run format to be tested would already be fitted to it. The
fixture tests at the end check that the definitions a human would actually write
reproduce numbers countable by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from layer.metrics import (
    BAD_DEF,
    MISSING_DATA,
    NO_METRIC,
    MetricValue,
    Unmeasurable,
    compute,
    supports,
)
from layer.metrics.pointer import MISSING, resolve
from layer.metrics.predicates import BadDefinition, evaluate, to_bool, truthy

FIXTURE_RUNS = Path("/Users/surabhideb/Desktop/chatbot-lab/products")


def rows(*records) -> dict:
    return {"results": list(records)}


class TestPointer:
    def test_it_addresses_nested_values(self):
        assert resolve({"meta": {"p95": 2.4}}, "/meta/p95") == 2.4

    def test_an_empty_pointer_is_the_whole_document(self):
        doc = {"a": 1}
        assert resolve(doc, "") is doc

    def test_absent_and_null_are_different(self):
        """A source that does not carry a metric and one that measured nothing lead to
        different findings, so they must not collapse into the same value."""
        assert resolve({"a": None}, "/a") is None
        assert resolve({}, "/a") is MISSING

    def test_it_indexes_lists(self):
        assert resolve({"r": [{"id": "x"}]}, "/r/0/id") == "x"

    def test_escapes(self):
        assert resolve({"a/b": 1}, "/a~1b") == 1

    def test_a_relative_pointer_is_refused(self):
        with pytest.raises(ValueError):
            resolve({}, "meta/p95")


class TestBooleanHandling:
    @pytest.mark.parametrize("raw", ["true", "True", " TRUE ", "yes", "1", 1, True])
    def test_truthy_encodings(self, raw):
        assert to_bool(raw) is True

    @pytest.mark.parametrize("raw", ["false", "False", "no", "0", 0, False, "", None])
    def test_falsy_encodings(self, raw):
        assert to_bool(raw) is False

    def test_the_string_false_is_not_true(self):
        """The single most expensive mistake available here. A bare `bool("false")` is
        True, which would mark every correctly-labelled negative as positive and invert
        a recall metric."""
        assert to_bool("false") is False
        assert bool("false") is True

    def test_an_uninterpretable_value_raises_rather_than_being_guessed(self):
        with pytest.raises(BadDefinition):
            to_bool("bad json")

    def test_presence_is_a_separate_question_from_truth(self):
        """`is_present` asks whether there is content; `is_true` interprets an encoded
        boolean. One operator doing both is how a message string gets asked whether it
        is true."""
        assert truthy("false") is True
        assert to_bool("false") is False


class TestPredicates:
    def test_an_absent_predicate_matches_everything(self):
        assert evaluate(None, {"a": 1}) is True

    def test_comparison_between_two_fields(self):
        row = {"team": "fraud", "labelled_team": "fraud"}
        assert evaluate({"field": "team", "op": "eq", "other_field": "labelled_team"}, row)

    def test_coercion_applies_before_comparison(self):
        """The asymmetry in real data: one field a JSON boolean, the other the string
        "true". Comparing them uncoerced reports every case as a mismatch."""
        row = {"escalate": True, "labelled_escalate": "true"}
        predicate = {"field": "escalate", "op": "eq", "other_field": "labelled_escalate"}
        assert evaluate(predicate, row) is False
        assert evaluate(predicate, row, {"labelled_escalate": "bool"}) is True

    def test_boolean_combinators(self):
        row = {"critical": True, "status": "answered"}
        assert evaluate({"all": [
            {"field": "critical", "op": "is_true"},
            {"field": "status", "op": "eq", "value": "answered"},
        ]}, row)
        assert evaluate({"not": {"field": "critical", "op": "is_false"}}, row)
        assert evaluate({"any": [
            {"field": "critical", "op": "is_false"},
            {"field": "status", "op": "eq", "value": "answered"},
        ]}, row)

    def test_an_ordering_comparison_against_an_absent_value_excludes_the_row(self):
        """False rather than an exception: the row is excluded instead of a result being
        invented for it."""
        assert evaluate({"field": "confidence", "op": "gte", "value": 0.8}, {}) is False

    def test_contains_any_over_a_list(self):
        row = {"retrieved": ["NIM-1", "NIM-2"], "expected": ["NIM-2"]}
        assert evaluate(
            {"field": "retrieved", "op": "contains_any", "other_field": "expected"}, row
        )

    def test_a_predicate_is_never_executed_as_code(self):
        """SEC-4 and B3 rule 2: a definition arrives in tenant-supplied config, so a
        string in it must never reach an evaluator. An unknown operator is refused
        rather than interpreted."""
        with pytest.raises(BadDefinition):
            evaluate({"field": "a", "op": "__import__('os').system"}, {"a": 1})


class TestTierOne:
    def test_a_number_the_source_already_names(self):
        got = compute({"meta": {"p95_seconds": 2.4}},
                      {"metric": "p95", "kind": "read", "pointer": "/meta/p95_seconds",
                       "unit": "duration_s"})
        assert isinstance(got, MetricValue)
        assert (got.value, got.unit, got.total) == (2.4, "duration_s", None)

    def test_a_scale_converts_units_without_code(self):
        got = compute({"meta": {"p95_seconds": 2.4}},
                      {"metric": "p95", "kind": "read", "pointer": "/meta/p95_seconds",
                       "scale": 1000, "unit": "duration_ms"})
        assert got.value == pytest.approx(2400)

    def test_a_pass_total_pair_carries_its_sample(self):
        """The shape a judge sidecar uses. Carrying n is what lets the verdict rule say
        anything at all."""
        got = compute({"cases": 35, "grounded": 29},
                      {"metric": "groundedness", "kind": "ratio_of",
                       "passed": "/grounded", "total": "/cases"})
        assert (got.passed, got.total) == (29, 35)
        assert got.value == pytest.approx(29 / 35)

    def test_a_missing_pointer_declines_rather_than_defaulting(self):
        got = compute({}, {"metric": "p95", "kind": "read", "pointer": "/meta/p95"})
        assert isinstance(got, Unmeasurable) and got.reason == MISSING_DATA

    def test_a_zero_total_is_not_a_zero_rate(self):
        got = compute({"cases": 0, "grounded": 0},
                      {"metric": "g", "kind": "ratio_of", "passed": "/grounded",
                       "total": "/cases"})
        assert isinstance(got, Unmeasurable) and got.reason == MISSING_DATA


class TestTierTwo:
    def test_accuracy_between_two_fields(self):
        doc = rows({"id": "1", "team": "a", "labelled_team": "a"},
                   {"id": "2", "team": "b", "labelled_team": "a"})
        got = compute(doc, {"metric": "team_accuracy", "kind": "accuracy",
                            "actual": "team", "expected": "labelled_team"})
        assert (got.passed, got.total) == (1, 2)
        assert got.detail["failing_ids"] == ["2"]

    def test_recall_over_a_stated_positive_class(self):
        doc = rows({"id": "1", "esc": True, "lab": "true"},
                   {"id": "2", "esc": False, "lab": "true"},
                   {"id": "3", "esc": False, "lab": "false"})
        got = compute(doc, {"metric": "escalation_recall", "kind": "recall",
                            "actual": "esc", "expected": "lab",
                            "coerce": {"lab": "bool"}})
        assert (got.passed, got.total) == (1, 2)
        assert got.detail == {"tp": 1, "fp": 0, "fn": 1, "missed_ids": ["2"]}

    def test_precision_uses_a_different_denominator(self):
        doc = rows({"id": "1", "esc": True, "lab": "true"},
                   {"id": "2", "esc": True, "lab": "false"})
        got = compute(doc, {"metric": "escalation_precision", "kind": "precision",
                            "actual": "esc", "expected": "lab", "coerce": {"lab": "bool"}})
        assert (got.passed, got.total) == (1, 2)

    def test_the_positive_class_must_be_stated_not_inferred(self):
        """One product treats abstention as the positive class and another treats
        escalation as it. Guessing from field names would yield a confidently inverted
        metric."""
        doc = rows({"id": "1", "status": "no_answer", "expected_status": "no_answer"},
                   {"id": "2", "status": "answered", "expected_status": "no_answer"})
        got = compute(doc, {"metric": "abstention_recall", "kind": "recall",
                            "actual": "status", "expected": "expected_status",
                            "positive": "no_answer", "positive_is_bool": False})
        assert (got.passed, got.total) == (1, 2)

    def test_a_filter_expresses_a_subset_metric(self):
        """The shape that makes a subset breach visible while the headline climbs: the
        engine never learns what critical means."""
        doc = rows({"id": "1", "critical": True, "ok": True},
                   {"id": "2", "critical": True, "ok": False},
                   {"id": "3", "critical": False, "ok": False})
        got = compute(doc, {"metric": "critical_pass_rate", "kind": "rate",
                            "filter": {"field": "critical", "op": "is_true"},
                            "where": {"field": "ok", "op": "is_true"}})
        assert (got.passed, got.total) == (1, 2)

    def test_a_filter_that_matches_nothing_declines(self):
        """Reporting zero would assert a failure never observed."""
        doc = rows({"id": "1", "critical": False})
        got = compute(doc, {"metric": "m", "kind": "rate",
                            "filter": {"field": "critical", "op": "is_true"},
                            "where": {"field": "critical", "op": "is_true"}})
        assert isinstance(got, Unmeasurable) and got.reason == MISSING_DATA

    def test_recall_with_no_relevant_case_declines(self):
        """0.0 would read as total failure of something the run never exercised."""
        doc = rows({"id": "1", "esc": False, "lab": "false"})
        got = compute(doc, {"metric": "r", "kind": "recall", "actual": "esc",
                            "expected": "lab", "coerce": {"lab": "bool"}})
        assert isinstance(got, Unmeasurable) and got.reason == MISSING_DATA

    def test_a_count_is_not_a_proportion(self):
        doc = rows({"id": "1", "esc": False, "lab": "true"},
                   {"id": "2", "esc": False, "lab": "true"})
        got = compute(doc, {"metric": "missed_escalations", "kind": "count",
                            "where": {"all": [
                                {"field": "lab", "op": "is_true"},
                                {"field": "esc", "op": "is_false"},
                            ]}, "coerce": {"lab": "bool"}})
        assert (got.value, got.unit, got.total) == (2.0, "count", None)

    def test_a_percentile_uses_nearest_rank(self):
        """No interpolation: on a small sample it would invent a value between two
        observations that never occurred."""
        doc = rows(*({"id": str(i), "s": float(i)} for i in range(1, 11)))
        got = compute(doc, {"metric": "p95", "kind": "percentile", "field": "s", "p": 95,
                            "unit": "duration_s"})
        assert got.value == 10.0
        assert got.total is None

    def test_a_mean_carries_no_sample_for_the_verdict_rule(self):
        doc = rows({"id": "1", "s": 1.0}, {"id": "2", "s": 3.0})
        got = compute(doc, {"metric": "mean_latency", "kind": "mean", "field": "s"})
        assert got.value == 2.0 and got.passed is None


class TestTierThreeIsARefusal:
    def test_an_unsupported_aggregation_declines_with_no_metric(self):
        """Mean reciprocal rank needs rank semantics over retrieved lists. Computing it
        would mean reimplementing the customer's scorer, which is building the eval
        half. Declining is the designed behaviour, and it becomes an `uncovered`
        finding rather than an error in a log."""
        got = compute(rows({"id": "1"}), {"metric": "mrr", "kind": "reciprocal_rank"})
        assert isinstance(got, Unmeasurable) and got.reason == NO_METRIC

    def test_support_can_be_checked_before_a_binding_is_confirmed(self):
        assert supports({"kind": "recall"}) is True
        assert supports({"kind": "reciprocal_rank"}) is False

    def test_a_malformed_definition_is_reported_not_approximated(self):
        got = compute(rows({"id": "1"}), {"metric": "m", "kind": "recall"})
        assert isinstance(got, Unmeasurable) and got.reason == BAD_DEF

    def test_an_unreadable_document_declines_rather_than_aborting(self):
        """EC-4: one unreadable run must not abort a backfill of ninety."""
        got = compute({"not_results": []}, {"metric": "m", "kind": "rate",
                                            "where": {"field": "a", "op": "is_true"}})
        assert isinstance(got, Unmeasurable) and got.reason == BAD_DEF


# -- against real run records ---------------------------------------------------

@pytest.mark.skipif(not FIXTURE_RUNS.exists(), reason="fixture repository not present")
class TestAgainstFixtureRuns:
    """The definitions a human would write at onboarding, checked against numbers that
    can be counted by hand in the committed files."""

    def _run(self, relative: str) -> dict:
        return json.loads((FIXTURE_RUNS / relative).read_text())

    def test_a_classifier_run_yields_its_stated_metrics(self):
        doc = self._run("triage/runs/20260915-150650Z-v2.json")

        accuracy = compute(doc, {"metric": "team_accuracy", "kind": "accuracy",
                                 "actual": "team", "expected": "labelled_team"})
        recall = compute(doc, {"metric": "escalation_recall", "kind": "recall",
                               "actual": "escalate", "expected": "labelled_escalate",
                               "coerce": {"labelled_escalate": "bool"}})
        contract = compute(doc, {"metric": "contract_validity", "kind": "rate",
                                 "where": {"field": "problem", "op": "is_blank"}})

        assert accuracy.total == 20
        assert recall.total and recall.passed == recall.total  # the clean latest run
        assert contract.value == 1.0

    def test_the_mid_sequence_run_that_a_latest_only_gate_never_sees(self):
        """Condition 1's instance. This run breaches while the newest file is clean, so
        the number here is the one the whole product exists to surface."""
        doc = self._run("triage/runs/20260915-133223Z-v2.json")

        recall = compute(doc, {"metric": "escalation_recall", "kind": "recall",
                               "actual": "escalate", "expected": "labelled_escalate",
                               "coerce": {"labelled_escalate": "bool"}})

        assert isinstance(recall, MetricValue)
        assert recall.value < 0.99, "this run is supposed to breach a 99% bar"
        assert recall.detail["fn"] >= 1
        assert recall.detail["missed_ids"]

    def test_a_rag_run_yields_a_subset_metric_distinct_from_its_headline(self):
        """Condition 2's shape: a headline and a subset measured from one file, where
        the subset is the one that never clears its bar."""
        doc = self._run("policydesk/runs/20260918-080101Z-v6.json")

        headline = compute(doc, {"metric": "status_ok", "kind": "accuracy",
                                 "actual": "status", "expected": "expected_status"})
        # `critical` holds a case label — 'C1', 'C3' — or an empty string, not a
        # boolean. So the subset is selected with `is_present` rather than `is_true`,
        # which is exactly why those are separate operators: asking whether 'C3' is
        # true is not a question with an answer.
        critical = compute(doc, {"metric": "critical_pass_rate", "kind": "accuracy",
                                 "actual": "status", "expected": "expected_status",
                                 "filter": {"field": "critical", "op": "is_present"}})

        assert isinstance(headline, MetricValue) and isinstance(critical, MetricValue)
        # Both numbers are the product's own, reproduced from a declarative definition
        # with no knowledge of this product in the engine.
        assert (headline.passed, headline.total) == (47, 50)
        assert headline.value == pytest.approx(0.94)
        assert (critical.passed, critical.total) == (10, 11)
        assert critical.value == pytest.approx(0.9091, abs=1e-4)
        assert critical.total < headline.total, "the subset must be smaller"
        assert critical.value < 1.0, "the subset is supposed to miss its 100% bar"
        assert headline.value > critical.value, (
            "the shape of condition 2: the headline reads better than the subset that "
            "never cleared its bar"
        )

    def test_a_judge_sidecar_is_read_rather_than_recomputed(self):
        """Tier 1 over a file the product already counted. The Layer does not re-judge."""
        doc = self._run("policydesk/runs/20260917-071445Z-v1-groundedness.json")
        got = compute(doc, {"metric": "groundedness", "kind": "ratio_of",
                            "passed": "/grounded", "total": "/cases"})
        assert (got.passed, got.total) == (29, 35)

    def test_latency_comes_from_run_metadata(self):
        doc = self._run("policydesk/runs/20260918-080101Z-v6.json")
        got = compute(doc, {"metric": "p95_latency", "kind": "read",
                            "pointer": "/meta/p95_seconds", "unit": "duration_s"})
        assert isinstance(got, MetricValue) and got.value > 0

    def test_the_file_that_is_not_a_run_declines_rather_than_crashing(self):
        """`v1.json` is a bare list from an earlier format. The adapter must tell this
        apart from a run that failed to import, and neither may be silently skipped."""
        doc = self._run("triage/runs/v1.json")
        got = compute(doc, {"metric": "team_accuracy", "kind": "accuracy",
                            "actual": "team", "expected": "labelled_team"})
        assert isinstance(got, Unmeasurable) and got.reason == BAD_DEF
