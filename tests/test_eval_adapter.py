"""Backfilling observations from committed run records. AC-2, EC-2, EC-4, H1, H5.

AC-2 is the criterion this file exists for: "no duplicates and **no run omitted**, proven
by counting source runs against stored observations". That proof is only worth anything if
"this document is not a run" and "this run would not import" are counted separately, so
most of what follows is about the difference between those two.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from layer.adapters.base import SourceDocument
from layer.adapters.eval.run_files import RunFilesAdapter
from layer.adapters.repo import RepoHandle

FIXTURE_REPO = Path("/Users/surabhideb/Desktop/chatbot-lab")

# Configurations a human would write at onboarding. They live here, in tests, because
# they are product-specific: in production they are rows in `source.config`.
TRIAGE_CONFIG = {
    "readers": [{
        "glob": "products/triage/runs/*.json",
        "measured_at": "/meta/timestamp_utc",
        "corpus_sha": "/meta/dataset_sha",
        "prompt_version": "/meta/prompt_sha",
        "metrics": [
            {"metric": "team_accuracy", "kind": "accuracy",
             "actual": "team", "expected": "labelled_team"},
            {"metric": "escalation_recall", "kind": "recall",
             "actual": "escalate", "expected": "labelled_escalate",
             "coerce": {"labelled_escalate": "bool"}},
            {"metric": "contract_validity", "kind": "rate",
             "where": {"field": "problem", "op": "is_blank"}},
        ],
    }]
}

POLICYDESK_CONFIG = {
    "readers": [
        {"glob": "products/policydesk/runs/*.json",
         "measured_at": "/meta/started", "corpus_sha": "/meta/corpus_sha",
         "prompt_version": "/meta/prompt_sha", "code_rev": "/meta/git",
         "metrics": [
             {"metric": "status_ok", "kind": "accuracy",
              "actual": "status", "expected": "expected_status"},
             {"metric": "critical_pass_rate", "kind": "accuracy",
              "actual": "status", "expected": "expected_status",
              "filter": {"field": "critical", "op": "is_present"}},
             {"metric": "p95_latency", "kind": "read",
              "pointer": "/meta/p95_seconds", "unit": "duration_s"},
         ]},
        {"glob": "products/policydesk/runs/*-groundedness.json",
         "join_on": "/run_id", "run_id": "/run_id",
         "requires": ["/run_id", "/grounded"],
         "metrics": [{"metric": "groundedness", "kind": "ratio_of",
                      "passed": "/grounded", "total": "/cases"}]},
    ]
}

SIMPLE = {
    "readers": [{
        "glob": "*.json",
        "run_id": "/run", "measured_at": "/at",
        "prompt_version": None, "corpus_sha": None, "code_rev": None,
        "metrics": [{"metric": "accuracy", "kind": "accuracy",
                     "rows": "/cases", "actual": "got", "expected": "want"}],
    }]
}


def doc(name: str, payload, url: str | None = None) -> SourceDocument:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return SourceDocument(name, body.encode(), url)


def a_run(run="r1", at="2026-09-15T13:00:00+00:00", cases=None):
    return {"run": run, "at": at,
            "cases": cases or [{"id": "1", "got": "a", "want": "a"},
                               {"id": "2", "got": "b", "want": "a"}]}


class TestClassification:
    """The distinction AC-2 depends on."""

    @pytest.mark.ac("AC-2")
    def test_a_document_that_is_not_a_run_is_skipped_not_failed(self):
        """A bare list from an earlier format. It was never a run, so calling it a
        failure would be as wrong as ignoring it."""
        report = RunFilesAdapter().read([doc("legacy.json", [1, 2, 3])], SIMPLE)
        assert [s.reason for s in report.skipped] == ["not_a_run_record"]
        assert report.failed == []
        assert report.complete

    @pytest.mark.ac("AC-2")
    def test_a_run_that_will_not_parse_is_failed_not_skipped(self):
        report = RunFilesAdapter().read([doc("broken.json", "{not json")], SIMPLE)
        assert [f.reason for f in report.failed] == ["unparsable_json"]
        assert report.skipped == []
        assert report.partial

    @pytest.mark.ac("AC-2")
    def test_every_document_is_accounted_for(self):
        report = RunFilesAdapter().read(
            [doc("a.json", a_run("a")), doc("legacy.json", []), doc("bad.json", "{")],
            SIMPLE,
        )
        assert report.enumerated == 3
        assert report.accounted == 3
        assert report.complete
        assert (report.imported, len(report.skipped), len(report.failed)) == (1, 1, 1)

    def test_a_document_no_reader_covers_is_reported(self):
        report = RunFilesAdapter().read([doc("notes.md", a_run())], SIMPLE)
        assert [s.reason for s in report.skipped] == ["no_reader_matched"]

    @pytest.mark.edge_case("EC-4")
    def test_a_partial_import_says_so(self):
        """EC-4: never a silent partial."""
        report = RunFilesAdapter().read(
            [doc("a.json", a_run("a")), doc("bad.json", "{")], SIMPLE
        )
        assert report.partial
        assert any("did not import" in n for n in report.notes)


class TestProvenance:
    def test_a_run_with_no_usable_time_is_not_stored(self):
        """An observation with no timestamp cannot sit in a series. Dating it `now` would
        put a two-week-old run at the end of the history and invent a trend."""
        report = RunFilesAdapter().read([doc("a.json", {"run": "r", "at": None,
                                                        "cases": []})], SIMPLE)
        assert report.skipped or report.failed
        assert not report.candidates

    @pytest.mark.parametrize(
        "raw", ["2026-09-15T13:00:00+00:00", "2026-09-15T13:00:00Z", "20260915-130000Z"]
    )
    def test_timestamp_shapes_found_in_real_runs(self, raw):
        report = RunFilesAdapter().read([doc("a.json", a_run(at=raw))], SIMPLE)
        assert report.candidates
        assert report.candidates[0].measured_at == datetime(2026, 9, 15, 13, 0, tzinfo=UTC)

    def test_a_nested_revision_is_flattened_and_keeps_its_dirty_marker(self):
        """`{"commit": "...", "dirty": true}` describes a tree nobody else can obtain, so
        the marker is kept as provenance and never becomes a citation."""
        config = json.loads(json.dumps(SIMPLE))
        config["readers"][0]["code_rev"] = "/git"
        payload = a_run() | {"git": {"commit": "abc1234", "dirty": True}}
        report = RunFilesAdapter().read([doc("a.json", payload)], config)
        assert report.candidates[0].code_rev == "abc1234-dirty"

    def test_the_citation_url_is_supplied_by_the_caller(self):
        """Only the caller knows the source's pinned revision, so the adapter never
        builds a URL itself."""
        url = "https://host/repo/blob/ef07ac9/runs/a.json"
        report = RunFilesAdapter().read([doc("a.json", a_run(), url)], SIMPLE)
        assert report.candidates[0].run_url == url


class TestSidecars:
    """A judge's verdict written beside a run, carrying no clock of its own."""

    @staticmethod
    def _config():
        return {"readers": [
            dict(SIMPLE["readers"][0]),
            {"glob": "*-judge.json", "join_on": "/run", "run_id": "/run",
             "requires": ["/run", "/passed"],
             "metrics": [{"metric": "groundedness", "kind": "ratio_of",
                          "passed": "/passed", "total": "/total"}]},
        ]}

    def test_a_sidecar_inherits_the_time_of_the_run_it_names(self):
        report = RunFilesAdapter().read(
            [doc("a.json", a_run("r1")),
             doc("a-judge.json", {"run": "r1", "passed": 29, "total": 35})],
            self._config(),
        )
        judged = [c for c in report.candidates if c.metric == "groundedness"]
        assert (judged[0].passed, judged[0].total) == (29, 35)
        assert judged[0].measured_at == datetime(2026, 9, 15, 13, 0, tzinfo=UTC)

    def test_the_more_specific_reader_wins(self):
        """`*-judge.json` also matches `*.json`. The broader pattern winning would read a
        verdict as a run."""
        report = RunFilesAdapter().read(
            [doc("a.json", a_run("r1")),
             doc("a-judge.json", {"run": "r1", "passed": 1, "total": 2})],
            self._config(),
        )
        assert {c.metric for c in report.candidates} == {"accuracy", "groundedness"}

    def test_a_sidecar_with_no_primary_run_is_a_failure(self):
        """It cannot be placed in time, and guessing a date would fabricate history."""
        report = RunFilesAdapter().read(
            [doc("orphan-judge.json", {"run": "missing", "passed": 1, "total": 2})],
            self._config(),
        )
        assert [f.reason for f in report.failed] == ["unjoined_sidecar"]

    def test_order_does_not_matter(self):
        """Primaries are read first regardless of the order they arrive in, so the result
        does not depend on how a filesystem happened to list them."""
        config = self._config()
        documents = [doc("a-judge.json", {"run": "r1", "passed": 1, "total": 2}),
                     doc("a.json", a_run("r1"))]
        report = RunFilesAdapter().read(documents, config)
        assert report.failed == []
        assert len(report.candidates) == 2


class TestMetricOutcomes:
    def test_one_uncomputable_metric_does_not_lose_the_run(self):
        """The run is still imported and its other metrics stored. The uncomputable one
        is recorded so onboarding can say which clause nothing measures."""
        config = json.loads(json.dumps(SIMPLE))
        config["readers"][0]["metrics"].append({"metric": "mrr", "kind": "reciprocal_rank"})
        report = RunFilesAdapter().read([doc("a.json", a_run())], config)

        assert report.imported == 1
        assert {c.metric for c in report.candidates} == {"accuracy"}
        assert [s.identifier.endswith("#mrr") for s in report.unmeasured] == [True]
        assert any("not computable" in n for n in report.notes)

    def test_unmeasured_metrics_stay_out_of_the_document_arithmetic(self):
        """The unit of enumeration is a document. A document whose third metric is
        uncomputable is still one imported document."""
        config = json.loads(json.dumps(SIMPLE))
        config["readers"][0]["metrics"].append({"metric": "mrr", "kind": "reciprocal_rank"})
        report = RunFilesAdapter().read([doc("a.json", a_run())], config)
        assert report.complete

    def test_a_run_whose_every_metric_fails_is_a_failure(self):
        """Shaped like a run, measured nothing. Silently importing it would make the
        reconciliation count a run that contributed no history."""
        config = json.loads(json.dumps(SIMPLE))
        config["readers"][0]["metrics"] = [{"metric": "mrr", "kind": "reciprocal_rank"}]
        report = RunFilesAdapter().read([doc("a.json", a_run())], config)
        assert [f.reason for f in report.failed] == ["no_metric_computed"]


class TestTheCaseLayerReachesTheCandidate:
    """The evidence spine's first link. AC-22, AC-25, AC-26.

    The engine produces the cases and the database stores them; this is the adapter in
    between, whose only job is to carry them without recomputing anything. A second pass
    over the rows here could only make the cases and the number disagree.
    """

    CASES = {
        "readers": [{
            **SIMPLE["readers"][0],
            "cases": {"input_field": "said"},
        }]
    }
    ROWS = [
        {"id": "1", "got": "a", "want": "a", "said": "fine, thanks"},
        {"id": "2", "got": "b", "want": "a", "said": "call me on 07700 900123"},
    ]

    @pytest.mark.ac("AC-22")
    def test_a_candidate_carries_the_cases_behind_its_number(self):
        report = RunFilesAdapter().read([doc("a.json", a_run(cases=self.ROWS))], SIMPLE)
        candidate = report.candidates[0]

        assert [(c.case_id, c.outcome) for c in candidate.cases] == [
            ("1", "pass"), ("2", "fail")
        ]
        assert candidate.cases_reproduce_the_value

    @pytest.mark.ac("AC-25")
    def test_the_readers_case_shape_reaches_every_metric_it_computes(self):
        """A run file's rows have one shape. Repeating `input_field` on each of a
        source's metrics is how one of them comes to be forgotten, and a metric that
        lost it stores no evidence text and says nothing about having done so."""
        report = RunFilesAdapter().read([doc("a.json", a_run(cases=self.ROWS))], self.CASES)
        stored = {c.case_id: c.input_redacted for c in report.candidates[0].cases}

        assert stored == {"1": None, "2": "call me on [phone]"}

    def test_a_metric_may_override_the_readers_shape(self):
        """The shape is a property of the rows; an exception is a property of one
        metric, such as a judge's sidecar that names its fields differently."""
        config = json.loads(json.dumps(self.CASES))
        config["readers"][0]["metrics"][0]["input_field"] = "nowhere"
        report = RunFilesAdapter().read([doc("a.json", a_run(cases=self.ROWS))], config)

        assert all(c.input_redacted is None for c in report.candidates[0].cases)

    @pytest.mark.ac("AC-26")
    def test_a_misspelled_case_key_is_refused_rather_than_ignored(self):
        """The failure mode of a typo here is silence: every failing case would carry no
        input and nothing anywhere would say why. So the set is closed."""
        config = json.loads(json.dumps(self.CASES))
        config["readers"][0]["cases"] = {"input_fields": "said"}
        with pytest.raises(ValueError, match="input_fields"):
            RunFilesAdapter().read([doc("a.json", a_run(cases=self.ROWS))], config)

    def test_a_tier_one_metric_carries_no_cases_and_that_is_not_a_disagreement(self):
        """`read` is a number the source already counted. There are no rows beneath it
        that this Layer ever saw, which is an absence of evidence rather than evidence
        that contradicts itself."""
        config = {"readers": [{
            "glob": "*.json", "run_id": "/run", "measured_at": "/at",
            "prompt_version": None, "corpus_sha": None, "code_rev": None,
            "metrics": [{"metric": "cases_seen", "kind": "read", "pointer": "/seen"}],
        }]}
        payload = a_run() | {"seen": 14}
        report = RunFilesAdapter().read([doc("a.json", payload)], config)

        assert report.candidates[0].cases == ()
        assert report.candidates[0].cases_reproduce_the_value
        assert report.cases == 0
        assert report.cases_disagreeing == []


@pytest.mark.hard_case("H5")
def test_observations_are_keyed_by_metric_not_by_clause():
    """H5: one metric may serve two clauses. Storing an observation per clause would
    double every row and make binding a second clause a rewrite of history, so the
    clause relationship lives in `binding` and these arrive unbound."""
    report = RunFilesAdapter().read([doc("a.json", a_run())], SIMPLE)
    assert all(c.clause_ref is None for c in report.candidates)


def test_reading_the_same_documents_twice_produces_the_same_candidates():
    """The precondition for idempotency. The database refuses the duplicate, but only if
    the adapter presents the same identity each time."""
    documents = [doc("a.json", a_run(), "https://host/blob/ef07ac9/a.json")]
    first = RunFilesAdapter().read(documents, SIMPLE)
    second = RunFilesAdapter().read(documents, SIMPLE)
    key = lambda c: (c.metric, c.run_url, c.value, c.measured_at)
    assert [key(c) for c in first.candidates] == [key(c) for c in second.candidates]


# -- against the committed reference history -------------------------------------

@pytest.mark.skipif(not FIXTURE_REPO.exists(), reason="fixture repository not present")
class TestAgainstCommittedRuns:

    @staticmethod
    @pytest.fixture(scope="class")
    def handle():
        return RepoHandle.pin(FIXTURE_REPO, "https://github.com/SurabhiDeb/chatbot-lab")

    def _read(self, handle, glob, config):
        paths = handle.list_files(glob)
        documents = [
            SourceDocument(p, handle.read(p), handle.blob_url(p)) for p in paths
        ]
        return RunFilesAdapter().read(documents, config)

    @pytest.mark.ac("AC-2")
    def test_the_reconciliation_that_proves_nothing_was_omitted(self, handle):
        """48 files enumerated, 47 run records imported, one classified as not a run,
        nothing failed, and the arithmetic balances. A quiet skip would have made this
        read 47 of 47 and look correct."""
        report = self._read(handle, "products/triage/runs/*.json", TRIAGE_CONFIG)

        assert report.enumerated == 48
        assert report.imported == 47
        assert len(report.skipped) == 1
        assert report.skipped[0].identifier.endswith("v1.json")
        assert report.skipped[0].reason == "not_a_run_record"
        assert report.failed == []
        assert report.complete, report.summary()
        assert len(report.candidates) == 47 * 3

    @pytest.mark.ac("AC-14")
    def test_every_observation_cites_a_pinned_revision(self, handle):
        report = self._read(handle, "products/triage/runs/*.json", TRIAGE_CONFIG)
        assert all(handle.rev in (c.run_url or "") for c in report.candidates)
        assert not any("/blob/main/" in (c.run_url or "") for c in report.candidates)

    def test_the_breach_a_latest_only_gate_never_sees(self, handle):
        """Condition 1, from committed data and without being told where to look. The
        numbers here are the prototype's: seven breaching runs, worst 80% at four of
        five, in one named run, missing one named case."""
        report = self._read(handle, "products/triage/runs/*.json", TRIAGE_CONFIG)
        recall = [c for c in report.candidates if c.metric == "escalation_recall"]

        breaching = [c for c in recall if c.value < 0.99]
        worst = min(recall, key=lambda c: c.value)
        latest = max(recall, key=lambda c: c.measured_at)

        assert len(recall) == 47
        assert len(breaching) == 7
        assert (worst.passed, worst.total) == (4, 5)
        assert worst.value == pytest.approx(0.8)
        assert worst.run_id == "20260915-133223Z-v2"
        assert worst.detail["missed_ids"] == ["14"]
        # The whole point: the newest run is clean, so anything reading only the latest
        # file sees nothing wrong.
        assert latest.value == 1.0

    def test_a_subset_that_never_cleared_its_bar_while_the_headline_climbed(self, handle):
        """Condition 2. Every figure below is checkable by hand in the committed files."""
        report = self._read(handle, "products/policydesk/runs/*.json", POLICYDESK_CONFIG)

        def series(metric):
            rows = sorted(
                (c for c in report.candidates if c.metric == metric),
                key=lambda c: c.measured_at,
            )
            return [round(c.value, 4) for c in rows]

        assert series("status_ok") == [0.74, 0.84, 0.88, 0.88, 0.92, 0.92, 0.94]
        critical = series("critical_pass_rate")
        assert critical == [0.4545, 0.9091, 0.8182, 0.8182, 0.9091, 0.9091, 0.9091]
        assert max(critical) < 1.0, "the subset is supposed never to clear its 100% bar"
        assert series("status_ok")[-1] > critical[-1]

    @pytest.mark.hard_case("H1")
    def test_versions_sharing_their_inputs_but_not_their_scores(self, handle):
        """H1. The Layer records that the inputs match and the scores differ. Saying why
        is forbidden, and nothing here attempts it."""
        report = self._read(handle, "products/policydesk/runs/*.json", POLICYDESK_CONFIG)
        rows = sorted(
            (c for c in report.candidates if c.metric == "status_ok"),
            key=lambda c: c.measured_at,
        )
        by_inputs: dict[tuple, set] = {}
        for row in rows:
            by_inputs.setdefault((row.prompt_version, row.corpus_sha), set()).add(row.value)

        assert any(len(values) > 1 for values in by_inputs.values()), (
            "no group of runs shares its recorded inputs while disagreeing on the score, "
            "so H1 could not be detected from this history"
        )

    def test_a_judge_sidecar_joins_to_the_run_it_scored(self, handle):
        report = self._read(handle, "products/policydesk/runs/*.json", POLICYDESK_CONFIG)
        judged = [c for c in report.candidates if c.metric == "groundedness"]

        assert len(judged) == 2
        assert all(c.measured_at is not None and c.run_id for c in judged)
        assert (judged[0].passed, judged[0].total) == (29, 35)

    @pytest.mark.ac("AC-22")
    def test_every_case_row_reproduces_the_number_it_sits_under(self, handle):
        """The load-bearing property of the whole spine, over both committed histories.
        If `passed` and `total` could disagree with the stored outcomes, a finding would
        cite cases that do not add up to its own claim."""
        for glob, config in (
            ("products/triage/runs/*.json", TRIAGE_CONFIG),
            ("products/policydesk/runs/*.json", POLICYDESK_CONFIG),
        ):
            report = self._read(handle, glob, config)
            assert report.cases_disagreeing == [], report.summary()
            assert report.cases > 0, f"{glob} carried no case rows at all"

    def test_the_report_counts_the_cases_it_carries(self, handle):
        """506 cases across the 47 committed runs, three metrics over each of them.

        Not 47 times 14: the run files hold between 1 and 20 cases each, which is worth
        knowing here because an assertion written from the headline `cases` field in one
        run's metadata would pass on the wrong arithmetic.
        """
        report = self._read(handle, "products/triage/runs/*.json", TRIAGE_CONFIG)
        assert report.cases == 506 * 3
        assert f"{506 * 3} case row(s)" in report.summary()

    @pytest.mark.ac("AC-24")
    def test_one_products_rows_carry_a_trace_pointer_and_the_others_do_not(self, handle):
        """A source without tracing is `not_applicable`, never a silent zero, so the two
        states have to be distinguishable at the point the rows are produced."""
        triage = self._read(handle, "products/triage/runs/*.json", TRIAGE_CONFIG)
        policy = self._read(handle, "products/policydesk/runs/*.json", POLICYDESK_CONFIG)

        assert all(
            case.trace_url is None and case.trace_id is None
            for c in triage.candidates for case in c.cases
        )
        traced = [case for c in policy.candidates for case in c.cases if case.trace_url]
        assert traced, "the policy fixture's rows carry a trace_url and none arrived"
        assert all("/traces/" in case.trace_url for case in traced)

    def test_both_products_account_for_every_document(self, handle):
        for glob, config in (
            ("products/triage/runs/*.json", TRIAGE_CONFIG),
            ("products/policydesk/runs/*.json", POLICYDESK_CONFIG),
        ):
            report = self._read(handle, glob, config)
            assert report.complete, f"{glob}: {report.summary()}"
            assert not report.failed, f"{glob}: {report.summary()}"


# -- Langfuse as a second transport over the same measurement path ----------------

class FakeLangfuse:
    """A stand-in shaped like the surface read from langfuse 4.15.4.

    Dicts rather than the SDK's pydantic models, which the adapter supports on purpose:
    testing the normalisation must not require the SDK, or the one seam that cannot be
    verified without a network would also be the one that cannot be tested without it.
    """

    def __init__(self, runs, items, scores, limit=50):
        self._runs, self._items, self._scores, self._limit = runs, items, scores, limit
        self.calls: list[str] = []

    @property
    def api(self):
        return self

    @property
    def datasets(self):
        return self

    @property
    def scores(self):
        return self

    def get_runs(self, dataset_name, page=1, limit=50):
        self.calls.append(f"get_runs:{page}")
        start = (page - 1) * limit
        return {"data": self._runs[start:start + limit]}

    def get_run(self, dataset_name, run_name):
        self.calls.append(f"get_run:{run_name}")
        run = next(r for r in self._runs if r["name"] == run_name)
        return run | {"dataset_run_items": self._items.get(run_name, [])}

    def get_many(self, dataset_run_id=None, page=1, limit=100):
        self.calls.append(f"scores:{dataset_run_id}:{page}")
        rows = self._scores.get(dataset_run_id, [])
        start = (page - 1) * limit
        return {"data": rows[start:start + limit]}


LANGFUSE_CONFIG = {
    "langfuse": {"dataset": "policy-questions", "host": "https://cloud.langfuse.com",
                 "project_id": "p1", "limit": 50},
    "readers": [{
        "glob": "langfuse/**",
        "run_id": "/meta/run_id", "measured_at": "/meta/started",
        "prompt_version": "/meta/prompt_sha", "corpus_sha": "/meta/corpus_sha",
        "code_rev": "/meta/git_sha",
        "metrics": [
            {"metric": "groundedness", "kind": "rate", "rows": "/results",
             "where": {"field": "/scores/groundedness", "op": "gte", "value": 1}},
            {"metric": "status_ok", "kind": "accuracy", "rows": "/results",
             "actual": "/scores/status", "expected": "/scores/expected_status"},
        ],
    }],
}


def _langfuse_client():
    runs = [{"id": "run-uuid-1", "name": "v6", "dataset_name": "policy-questions",
             "created_at": "2026-09-18T08:01:01+00:00",
             "metadata": {"prompt_sha": "ddae9036", "corpus_sha": "557c9914"}}]
    items = {"v6": [{"id": "i1", "dataset_item_id": "q1", "trace_id": "t1"},
                    {"id": "i2", "dataset_item_id": "q2", "trace_id": "t2"},
                    {"id": "i3", "dataset_item_id": "q3", "trace_id": "t3"}]}
    scores = {"run-uuid-1": [
        {"trace_id": "t1", "name": "groundedness", "value": 1, "data_type": "NUMERIC"},
        {"trace_id": "t1", "name": "status", "value": "answered", "data_type": "CATEGORICAL"},
        {"trace_id": "t1", "name": "expected_status", "value": "answered", "data_type": "CATEGORICAL"},
        {"trace_id": "t2", "name": "groundedness", "value": 0, "data_type": "NUMERIC"},
        {"trace_id": "t2", "name": "status", "value": "answered", "data_type": "CATEGORICAL"},
        {"trace_id": "t2", "name": "expected_status", "value": "no_answer", "data_type": "CATEGORICAL"},
        {"trace_id": "t3", "name": "groundedness", "value": 1, "data_type": "NUMERIC"},
        {"trace_id": "t3", "name": "status", "value": "no_answer", "data_type": "CATEGORICAL"},
        {"trace_id": "t3", "name": "expected_status", "value": "no_answer", "data_type": "CATEGORICAL"},
    ]}
    return FakeLangfuse(runs, items, scores)


class TestLangfuseSource:
    """The second transport. It adds no measurement code, which is the design."""

    def test_a_dataset_run_becomes_the_same_document_shape_as_a_run_file(self):
        from layer.adapters.eval.langfuse import LangfuseSource

        documents = LangfuseSource(_langfuse_client()).documents(LANGFUSE_CONFIG)

        assert len(documents) == 1
        payload = json.loads(documents[0].content)
        assert documents[0].identifier == "langfuse/policy-questions/v6"
        assert payload["meta"]["run_id"] == "v6"
        assert payload["meta"]["prompt_sha"] == "ddae9036"
        assert len(payload["results"]) == 3
        assert payload["results"][0]["scores"]["groundedness"] == 1

    def test_the_same_engine_measures_it(self):
        """A metric definition written for a run file works here unchanged, because the
        document shape is the same. That is the whole reason this file holds no
        aggregation code."""
        from layer.adapters.eval.langfuse import LangfuseSource

        documents = LangfuseSource(_langfuse_client()).documents(LANGFUSE_CONFIG)
        report = RunFilesAdapter().read(documents, LANGFUSE_CONFIG)

        assert report.complete, report.summary()
        values = {c.metric: c for c in report.candidates}
        assert (values["groundedness"].passed, values["groundedness"].total) == (2, 3)
        assert (values["status_ok"].passed, values["status_ok"].total) == (2, 3)

    def test_provenance_survives_the_transport(self):
        from layer.adapters.eval.langfuse import LangfuseSource

        documents = LangfuseSource(_langfuse_client()).documents(LANGFUSE_CONFIG)
        report = RunFilesAdapter().read(documents, LANGFUSE_CONFIG)
        candidate = report.candidates[0]

        assert candidate.prompt_version == "ddae9036"
        assert candidate.corpus_sha == "557c9914"
        assert candidate.run_id == "v6"
        assert candidate.measured_at == datetime(2026, 9, 18, 8, 1, 1, tzinfo=UTC)

    def test_a_trace_url_is_built_per_row_when_the_project_is_known(self):
        from layer.adapters.eval.langfuse import LangfuseSource

        payload = json.loads(
            LangfuseSource(_langfuse_client()).documents(LANGFUSE_CONFIG)[0].content
        )
        assert payload["results"][0]["trace_url"].endswith("/project/p1/traces/t1")

    def test_pagination_stops_on_a_short_page_rather_than_a_reported_total(self):
        """A total that disagrees with the data would loop forever or truncate history,
        and AC-2 forbids omitting a run."""
        from layer.adapters.eval.langfuse import LangfuseSource

        client = _langfuse_client()
        documents = LangfuseSource(client).documents(LANGFUSE_CONFIG)
        assert len(documents) == 1
        assert client.calls.count("get_runs:1") == 1
        assert not any(c.startswith("get_runs:2") for c in client.calls)

    def test_absent_provenance_is_left_absent_rather_than_invented(self):
        """H1 reports that versioning does not explain a movement. It can only do that if
        a missing sha stays missing."""
        from layer.adapters.eval.langfuse import LangfuseSource

        client = _langfuse_client()
        client._runs[0]["metadata"] = {}
        documents = LangfuseSource(client).documents(LANGFUSE_CONFIG)
        report = RunFilesAdapter().read(documents, LANGFUSE_CONFIG)

        assert report.candidates[0].prompt_version is None
        assert report.candidates[0].corpus_sha is None

    @pytest.mark.ac("AC-24")
    def test_the_trace_pointers_langfuse_already_carries_reach_the_cases(self):
        """The one thing this transport has that a run file does not.

        Langfuse knows the trace id for every dataset item and the host and project to
        build its URL from, so the normalisation writes both onto each row and the
        engine's own defaults pick them up. Nothing in the measurement path knows which
        transport it is reading, which is the design.
        """
        from layer.adapters.eval.langfuse import LangfuseSource

        documents = LangfuseSource(_langfuse_client()).documents(LANGFUSE_CONFIG)
        report = RunFilesAdapter().read(documents, LANGFUSE_CONFIG)
        cases = {
            case.case_id: case
            for c in report.candidates if c.metric == "groundedness"
            for case in c.cases
        }

        assert set(cases) == {"q1", "q2", "q3"}
        assert cases["q2"].outcome == "fail"
        assert cases["q2"].trace_id == "t2"
        assert cases["q2"].trace_url.endswith("/project/p1/traces/t2")

    def test_only_named_runs_are_fetched_when_the_config_names_some(self):
        from layer.adapters.eval.langfuse import LangfuseSource

        config = json.loads(json.dumps(LANGFUSE_CONFIG))
        config["langfuse"]["runs"] = ["v5"]
        assert LangfuseSource(_langfuse_client()).documents(config) == []
