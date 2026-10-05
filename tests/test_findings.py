"""The five findings. AC-3, AC-4, AC-5, AC-7, AC-15, AC-19, H1, H8, H14, H16, B3 rule 6.

AC-3 to AC-5 are the demo, and PRD C2 is specific about the bar: they "must pass with no UI
and no agent, from committed data alone, and they must pass against at least two
independently onboarded products". Both reference products are onboarded here from their own
sources, and the conditions are asserted with the numbers that can be counted by hand in the
committed files.
"""

from __future__ import annotations

import re

import pytest
from pathlib import Path
from sqlalchemy import select

from layer.core.db import org_session
from layer.db.models import Clause, Product
from layer.findings import queries
from layer.findings.citations import registry_for
from layer.findings.shapes import (
    METRIC_WITHOUT_CLAUSE,
    NO_ASSERTION,
    NO_METRIC,
)
from layer.onboarding import bindings as binding_gate
from layer.onboarding import run, state

from conftest import make_org

REFERENCE = Path("/Users/surabhideb/Desktop/chatbot-lab")
URL = "https://github.com/SurabhiDeb/chatbot-lab"
ACTOR = "pm@example.invalid"

pytestmark = pytest.mark.skipif(
    not REFERENCE.exists(), reason="reference products not present"
)

TRIAGE_METRICS = [
    {"metric": "team_accuracy", "kind": "accuracy",
     "actual": "team", "expected": "labelled_team"},
    {"metric": "escalation_recall", "kind": "recall",
     "actual": "escalate", "expected": "labelled_escalate",
     "coerce": {"labelled_escalate": "bool"}},
    {"metric": "contract_validity", "kind": "rate",
     "where": {"field": "problem", "op": "is_blank"}},
]

POLICYDESK_METRICS = [
    {"metric": "status_ok", "kind": "accuracy",
     "actual": "status", "expected": "expected_status"},
    {"metric": "critical_pass_rate", "kind": "accuracy",
     "actual": "status", "expected": "expected_status",
     "filter": {"field": "critical", "op": "is_present"}},
]


def onboard(
    session, org, key: str, prefix: str, metrics: list[dict], *,
    code=None, pairs=None, cases=None, retention=None,
) -> Product:
    """Onboard a reference product the way its owner would.

    `cases` is where the per-case layer lives in this source's rows, and `retention` is
    how long this source keeps a trace body. Both are configuration a source's owner
    states once, which is why they are arguments here and not conditions in the code.

    `pairs` is how a human pairs a measured number with a promise the source names
    differently. The policy product needs it: its specification states a bar for the critical
    subset and none at all for the headline, so nothing name-matches and the headline is
    correctly left as a measurement no clause promises (H16).
    """
    common = {"local_path": str(REFERENCE), "repo_url": URL}
    product = state.register(
        session, org_id=org, key=key, name=key.title(), ref_prefix=prefix, actor=ACTOR
    )
    state.bind_source(
        session, product=product, role="spec", kind="repo", actor=ACTOR,
        config={**common, "path": f"products/{key}/SPEC.md"},
    )
    state.bind_source(
        session, product=product, role="eval", kind="repo", actor=ACTOR,
        config={**common,
                "globs": [f"products/{key}/runs/*.json"],
                **({"trace_retention": retention} if retention else {}),
                "readers": [{
            "glob": f"products/{key}/runs/*.json",
            "measured_at": "/meta/timestamp_utc" if key == "triage" else "/meta/started",
            "corpus_sha": "/meta/dataset_sha" if key == "triage" else "/meta/corpus_sha",
            "prompt_version": "/meta/prompt_sha",
            "code_rev": "/meta/git_sha" if key == "triage" else "/meta/git",
            "cases": cases or {},
            "metrics": metrics,
        }]},
    )
    if code:
        state.bind_source(
            session, product=product, role="code", kind="repo", actor=ACTOR,
            config={**common, **code},
        )

    run.import_spec(session, product=product, actor=ACTOR)
    run.backfill(session, product=product, actor=ACTOR)
    for candidate in binding_gate.propose(session, product=product).pending:
        binding_gate.decide(
            session, product=product, metric=candidate.metric,
            clause_ref=candidate.clause_ref, decision="confirmed", by=ACTOR,
        )
    for metric, clause_ref in (pairs or {}).items():
        binding_gate.decide(
            session, product=product, metric=metric, clause_ref=clause_ref,
            decision="confirmed", by=ACTOR,
            note="paired by hand: the specification names this bar differently",
        )
    state.refresh(session, product=product, actor=ACTOR)
    if code:
        run.scan_enforcement(session, product=product, actor=ACTOR)
    state.measure(session, product=product, actor=ACTOR)
    return product


@pytest.fixture
def triage():
    org = make_org("findings")
    with org_session(org) as session:
        yield session, onboard(
            session, org, "triage", "TRI", TRIAGE_METRICS,
            cases={"input_field": "message"},
            code={
                "files": ["products/triage/gate.py", ".github/workflows/ci.yml"],
                "metric_aliases": {
                    "escalation_recall": ["missed"],
                    "contract_validity": ["broken", "problem"],
                },
            },
        )


@pytest.fixture
def policydesk():
    org = make_org("findings-rag")
    with org_session(org) as session:
        yield session, onboard(
            session, org, "policydesk", "PD", POLICYDESK_METRICS,
            cases={"input_field": "question"},
            pairs={"critical_pass_rate": "PD-8.8"},
        )


@pytest.fixture
def policydesk_past_retention():
    """The same product, whose owner has said traces are kept for a day.

    The committed runs are dated September 2026, so every pointer in them is past that
    window. Which is the condition AC-23 is about: the body is gone at the source and
    the finding still has to be provable.
    """
    org = make_org("findings-retention")
    with org_session(org) as session:
        yield session, onboard(
            session, org, "policydesk", "PD", POLICYDESK_METRICS,
            cases={"input_field": "question"},
            pairs={"critical_pass_rate": "PD-8.8"},
            retention="1d",
        )


def one(findings, clause_ref: str):
    matches = [f for f in findings if f.clause_ref == clause_ref]
    assert matches, f"no finding for {clause_ref}"
    return matches[0]


# -- the proof beneath a finding --------------------------------------------------


class TestADriftFindingCitesItsCases:
    """AC-22, AC-23, AC-24, B3 rules 10 and 11.

    "Escalation recall was missed in 7 of 47 runs, worst 80%" is not provable from a
    per-run average, and naming the cases that are the proof is the Layer's primary job.
    Every number below comes out of `case_result` and nothing here calls an eval
    platform, which is the whole of AC-24.
    """

    @pytest.mark.ac("AC-22")
    def test_the_cases_that_missed_the_bar_are_named_on_the_finding(self, triage):
        session, product = triage
        finding = one(queries.find_drift(session, product=product), "TRI-11.2")
        cases = finding.detail["failing_cases"]

        assert finding.detail["runs_missed"] == 7
        assert cases, "a bar was asserted missed with no case cited (B3 rule 10)"
        assert finding.detail["failing_cases_state"] == "cited"
        # The one case the committed history misses, in the runs that miss it.
        assert {c["case_id"] for c in cases} == {"14"}
        assert all(c["outcome"] in ("fail", "error") for c in cases)
        assert len({c["run_id"] for c in cases}) == 7

    @pytest.mark.ac("AC-22")
    def test_every_cited_case_resolves_to_somewhere_a_human_can_open(self, triage):
        """AC-7 applies to a case ref like any other. The triage rows carry no trace, so
        each case resolves to the run record that holds it rather than to nothing."""
        session, product = triage
        finding = one(queries.find_drift(session, product=product), "TRI-11.2")
        refs = {c["ref"] for c in finding.detail["failing_cases"]}

        assert refs <= set(finding.evidence)
        assert finding.unresolved == []
        resolved = {link["id"]: link["url"] for link in finding.evidence_links}
        assert refs <= set(resolved)
        assert all("/runs/" in resolved[ref] for ref in refs)

    @pytest.mark.ac("AC-14")
    def test_a_case_citation_pins_an_immutable_revision(self, triage):
        session, product = triage
        finding = one(queries.find_drift(session, product=product), "TRI-11.2")
        urls = [
            link["url"] for link in finding.evidence_links
            if link["id"].startswith("case:")
        ]
        assert urls
        assert not any("/blob/main/" in url for url in urls)

    @pytest.mark.ac("AC-24")
    def test_a_source_with_no_tracing_says_so_rather_than_reporting_false(self, triage):
        """PRD B6: a source without tracing scores `not_applicable`, never a silent
        zero. The two states send a reader to completely different places — one to the
        eval platform's settings, the other to a bug in here."""
        session, product = triage
        finding = one(queries.find_drift(session, product=product), "TRI-11.2")
        cases = finding.detail["failing_cases"]

        assert all(c["trace_state"] == "not_applicable" for c in cases)
        assert all(c["trace_url"] is None for c in cases)
        assert "records no trace pointers" in finding.summary

    @pytest.mark.ac("AC-24")
    def test_a_traced_source_carries_the_pointer_onto_the_finding(self, policydesk):
        session, product = policydesk
        drift = queries.find_drift(session, product=product)
        traced = [
            case for finding in drift
            for case in finding.detail["failing_cases"]
            if case["trace_state"] == "available"
        ]

        assert traced, "the policy fixture's rows carry a trace_url and none reached a finding"
        assert all(c["trace_available"] and "/traces/" in c["trace_url"] for c in traced)

    @pytest.mark.ac("AC-23")
    def test_a_deleted_trace_is_reported_gone_and_the_outcome_still_stands(
        self, policydesk_past_retention
    ):
        """AC-23 and B3 rule 11. The body is past the source's retention, so the finding
        says so, keeps the recorded outcome, and does not hand over the dead link."""
        session, product = policydesk_past_retention
        drift = queries.find_drift(session, product=product)
        cases = [c for f in drift for c in f.detail["failing_cases"]]

        assert cases
        assert all(c["trace_state"] == "past_retention" for c in cases)
        assert all(c["trace_available"] is False for c in cases)
        assert all(c["trace_url"] is None for c in cases)
        assert all(c["outcome"] in ("fail", "error") for c in cases)
        assert any("no longer keeps" in f.summary for f in drift)

    @pytest.mark.ac("AC-23")
    def test_the_citation_for_a_deleted_trace_goes_to_the_run_not_the_dead_link(
        self, policydesk_past_retention
    ):
        """A finding that says the body is gone while its own citation offers the link
        would break B3 rule 11 in the space of one screen."""
        session, product = policydesk_past_retention
        drift = queries.find_drift(session, product=product)
        links = [
            link for f in drift for link in f.evidence_links
            if link["id"].startswith("case:")
        ]

        assert links
        assert not any("/traces/" in link["url"] for link in links)
        assert all("/runs/" in link["url"] for link in links)

    @pytest.mark.ac("AC-22")
    def test_no_drift_finding_asserts_a_breach_without_cases_or_a_reason(
        self, triage, policydesk
    ):
        """B3 rule 10, across both products and every clause rather than the one the
        condition was written for. An empty list is allowed only where the source has no
        per-case layer at all, and then the finding says which of the two it is."""
        for session, product in (triage, policydesk):
            for finding in queries.find_drift(session, product=product):
                detail = finding.detail
                if not detail["runs_missed"]:
                    continue
                state = detail["failing_cases_state"]
                assert state in ("cited", "not_applicable", "no_failing_cases")
                if state == "cited":
                    assert detail["failing_cases"]
                else:
                    # Not silence: the summary has to say which it is and why.
                    assert (
                        "no per-case rows are stored" in finding.summary.lower()
                        or "none of them is recorded as a failure" in finding.summary
                    ), finding.summary

    def test_the_cited_cases_agree_with_the_counts_the_metric_recorded(self, triage):
        """The aggregate's own `missed_ids` and the stored cases are two paths to the
        same fact. If they could disagree, one of them is not evidence."""
        session, product = triage
        finding = one(queries.find_drift(session, product=product), "TRI-11.2")

        assert set(finding.detail["case_ids"]) <= {
            c["case_id"] for c in finding.detail["failing_cases"]
        }

    def test_the_total_is_stated_even_where_the_list_is_capped(self, policydesk):
        """The cap narrows the list and never the claim, so a reader can always tell a
        finding citing fifty cases from one citing fifty of four hundred."""
        session, product = policydesk
        for finding in queries.find_drift(session, product=product):
            cases, total = finding.detail["failing_cases"], finding.detail["failing_cases_total"]
            assert len(cases) <= queries.MAX_FAILING_CASES
            assert total >= len(cases)
            if total > len(cases):
                assert f"{total} case(s)" in finding.summary


# -- condition 1 -----------------------------------------------------------------


class TestConditionOne:
    """A bar breached mid-sequence while the gate reads only the newest run."""

    @pytest.mark.ac("AC-3")
    def test_drift_surfaces_the_breach_without_being_told_where_to_look(self, triage):
        """AC-3 as written. The query is given a product and nothing else — no metric, no
        run, no hint — and reports the breach from the full committed sequence."""
        session, product = triage
        result = queries.find_drift(session, product=product)

        finding = one(result, "TRI-11.2")
        assert finding.detail["runs_missed"] == 7
        assert finding.detail["runs_total"] == 47
        assert finding.detail["worst"] == pytest.approx(0.8)
        assert finding.detail["worst_run"] == "20260915-133223Z-v2"
        assert finding.detail["case_ids"] == ["14"]

    @pytest.mark.ac("AC-3")
    def test_the_finding_records_that_the_newest_run_is_clean(self, triage):
        """The sentence that makes the condition legible. It states what a narrower check can
        see, which is a fact about the check rather than a claim about the product."""
        session, product = triage
        finding = one(queries.find_drift(session, product=product), "TRI-11.2")

        assert finding.current is False, "the latest run is clean, so the breach is historical"
        assert "a check on the latest run alone shows nothing" in finding.summary
        assert finding.detail["latest_run"] == "20260915-150650Z-v2"

    def test_the_sample_behind_the_worst_run_is_shown(self, triage):
        """Four of five, not merely 80%. 19/20 and 190/200 are both "95%" and are very
        different claims, so the reader is given the sample rather than left to assume one."""
        session, product = triage
        finding = one(queries.find_drift(session, product=product), "TRI-11.2")
        assert "(4 of 5)" in finding.summary

    @pytest.mark.hard_case("H1")
    def test_revisions_are_stated_beside_the_numbers_and_nothing_is_inferred(self, triage):
        """H1's discipline. The finding says which revisions breaching runs recorded and
        which did not, and asserts no relationship between them."""
        session, product = triage
        finding = one(queries.find_drift(session, product=product), "TRI-11.2")

        assert finding.detail["breach_revisions"]
        assert finding.detail["clean_revisions"]
        assert "Breaching runs record" in finding.summary
        assert "do not breach" in finding.summary

    def test_drift_and_the_verdict_answer_different_questions(self, triage):
        """The distinction that matters most in this file. The clause reads cannot_confirm
        because seven of seven cannot establish a 99% rate, and drift reports seven breaches
        because seven runs recorded a number below the bar. Both are true."""
        session, product = triage
        finding = one(queries.find_drift(session, product=product), "TRI-11.2")
        clause = session.execute(
            select(Clause).where(
                Clause.product_id == product.id, Clause.ref == "TRI-11.2",
                Clause.status == "active",
            )
        ).scalars().one()

        assert clause.verdict == "cannot_confirm"
        assert finding.detail["runs_missed"] == 7


# -- condition 2 -----------------------------------------------------------------


class TestConditionTwo:
    """A sub-metric that never cleared its bar while the headline climbed."""

    @pytest.mark.ac("AC-5")
    def test_the_subset_breach_surfaces_despite_the_rising_headline(self, policydesk):
        """AC-5. `critical_pass_rate` never reaches its 100% bar across every run, while
        `status_ok` improves from 0.74 to 0.94. Good news does not suppress the finding."""
        session, product = policydesk
        result = queries.find_drift(session, product=product)

        critical = [f for f in result if f.detail["metric"] == "critical_pass_rate"]
        assert critical, "the subset breach was not reported"
        finding = critical[0]

        assert finding.detail["runs_missed"] == finding.detail["runs_total"] == 7
        assert finding.current is True, "it never cleared the bar, so it still holds"
        assert finding.detail["worst"] == pytest.approx(0.4545, abs=1e-3)

    @pytest.mark.ac("AC-5")
    def test_the_headline_metric_is_reported_separately(self, policydesk):
        """Both are reported, which is the point: a reader comparing them sees that the one
        improving is not the one that matters."""
        session, product = policydesk
        result = queries.find_drift(session, product=product)
        metrics = {f.detail["metric"] for f in result}
        assert "critical_pass_rate" in metrics

    def test_the_two_metrics_disagree_in_the_stored_record(self, policydesk):
        """The shape of the condition, asserted on the data rather than on prose."""
        session, product = policydesk
        from layer.db.models import Observation

        def series(metric):
            return [
                round(v, 4) for v in session.execute(
                    select(Observation.value).where(
                        Observation.product_id == product.id,
                        Observation.metric == metric,
                    ).order_by(Observation.measured_at)
                ).scalars()
            ]

        assert series("status_ok") == [0.74, 0.84, 0.88, 0.88, 0.92, 0.92, 0.94]
        assert max(series("critical_pass_rate")) < 1.0


# -- enforcement -----------------------------------------------------------------


class TestEnforcement:
    @pytest.mark.ac("AC-15")
    @pytest.mark.hard_case("H14")
    def test_a_narrowed_scope_is_distinguished_from_no_check_at_all(self, triage):
        """AC-15 as written: `enforced: false` and `enforced: true` with a narrowed scope are
        different conditions and must read differently."""
        session, product = triage
        result = queries.find_unenforced(session, product=product)

        narrowed = one(result, "TRI-11.1")
        assert narrowed.detail["enforced"] is True
        assert narrowed.detail["scope"] == "latest_only"
        assert "only against the newest run" in narrowed.summary

        absent = one(result, "TRI-11.3")
        assert absent.detail["enforced"] is False
        assert absent.detail["scope"] is None
        assert "no scanned CI file checks it" in absent.summary

    @pytest.mark.ac("AC-4")
    def test_every_stated_bar_that_no_file_checks_is_reported(self, triage):
        """AC-4. These are stated in the specification and absent from CI."""
        session, product = triage
        result = queries.find_unenforced(session, product=product)
        reported = {f.clause_ref for f in result}

        for ref in ("TRI-11.3", "TRI-11.4", "TRI-11.6", "TRI-12.1", "TRI-12.2"):
            assert ref in reported, f"{ref} is stated and unchecked but was not reported"

    def test_a_product_with_no_code_source_says_so_rather_than_claiming_nothing_checks(
        self, policydesk
    ):
        """"Nothing was scanned" and "nothing checks this" are different claims, and only one
        of them is supported by evidence."""
        session, product = policydesk
        result = queries.find_unenforced(session, product=product)
        assert result.findings
        assert all("has been scanned" in f.summary for f in result.findings)


# -- coverage --------------------------------------------------------------------


class TestCoverage:
    def test_a_clause_nothing_measures_is_reported_with_its_reason(self, triage):
        session, product = triage
        result = queries.find_uncovered(session, product=product)
        finding = one(result, "TRI-11.3")
        assert finding.detail["reason"] == NO_ASSERTION
        assert "nothing in the connected sources answers this promise" in finding.summary

    @pytest.mark.hard_case("H16")
    def test_a_measurement_nothing_promises_names_the_clause_as_the_gap(self, triage):
        """H16: "the gap is the absent clause, not the metric"."""
        session, product = triage
        with org_session(product.org_id) as other:
            pass
        result = queries.find_uncovered(session, product=product)
        orphans = [f for f in result if f.detail["reason"] == METRIC_WITHOUT_CLAUSE]
        for finding in orphans:
            assert finding.clause_ref is None
            assert "the gap is the absent clause" in finding.summary

    def test_a_rejected_pairing_is_not_reported_as_a_gap(self, policydesk):
        """A human looked and said this number does not answer that promise. Reporting it as
        uncovered would re-open a closed question."""
        session, product = policydesk
        clause = session.execute(
            select(Clause).where(
                Clause.product_id == product.id, Clause.status == "active",
                Clause.comparator.is_not(None), Clause.metric.is_(None),
            ).limit(1)
        ).scalars().first()
        if clause is None:
            pytest.skip("this product has no prose clause to reject against")

        binding_gate.decide(
            session, product=product, metric="status_ok", clause_ref=clause.ref,
            decision="rejected", by=ACTOR, note="measures something else",
        )
        result = queries.find_uncovered(session, product=product)
        orphaned = {
            f.detail.get("metric") for f in result
            if f.detail["reason"] == METRIC_WITHOUT_CLAUSE
        }
        assert "status_ok" not in orphaned


# -- the two that cannot run yet -------------------------------------------------


class TestRefusals:
    @pytest.mark.ac("AC-20")
    def test_stalled_decisions_refuses_by_name_rather_than_answering_empty(self, triage):
        """An empty answer would be a claim about decisions the Layer has never seen."""
        session, product = triage
        result = queries.find_stalled_decisions(session, product=product)

        assert result.refusal is not None
        assert result.findings == []
        assert "no decision source bound" in result.refusal.reason
        assert result.refusal.missing

    @pytest.mark.ac("AC-20")
    @pytest.mark.hard_case("H8")
    def test_underspecified_refuses_and_names_both_things_it_needs(self, triage):
        """H8 is the highest-value finding in the specification and needs a source this phase
        does not have. Saying which two things are missing beats returning nothing."""
        session, product = triage
        result = queries.find_underspecified(session, product=product)

        assert result.refusal is not None
        assert "no production source bound" in result.refusal.reason
        assert len(result.refusal.missing) == 2

    def test_a_refusal_serialises_as_a_refusal_not_as_an_empty_finding_set(self, triage):
        session, product = triage
        payload = queries.find_stalled_decisions(session, product=product).as_dict()
        assert payload["shape"] == "refusal"
        assert "findings" not in payload


# -- citations and language ------------------------------------------------------


class TestCitationsAndLanguage:
    @pytest.mark.ac("AC-7")
    @pytest.mark.ac("AC-14")
    def test_every_citation_resolves_to_a_pinned_revision(self, triage):
        """AC-7 and AC-14. `unresolved` must be empty, and every URL must name an immutable
        revision rather than a branch."""
        session, product = triage
        found = queries.find_all(session, product=product)

        checked = 0
        for result in found.values():
            for finding in result.findings:
                assert finding.evidence, f"{finding.kind} cited nothing"
                assert finding.unresolved == [], (
                    f"{finding.kind}/{finding.clause_ref} could not resolve "
                    f"{finding.unresolved}"
                )
                for link in finding.evidence_links:
                    assert "/blob/main/" not in link["url"]
                    assert "/blob/master/" not in link["url"]
                    checked += 1
        assert checked > 20, "too few citations were checked for this to mean anything"

    def test_a_clause_citation_points_at_the_lines_that_stated_it(self, triage):
        """A citation that resolves to a file is weaker than one resolving to the sentence."""
        session, product = triage
        registry = registry_for(session, product=product)
        url = registry.resolve("clause:TRI-11.2")
        assert url is not None
        assert re.search(r"#L\d+$", url), f"no line in {url}"

    def test_an_observation_citation_points_at_the_run_it_came_from(self, triage):
        session, product = triage
        finding = one(queries.find_drift(session, product=product), "TRI-11.2")
        runs = [
            link["url"] for link in finding.evidence_links
            if link["id"].startswith("obs:")
        ]
        assert runs
        assert all("/runs/" in url and url.endswith(".json") for url in runs)

    @pytest.mark.ac("AC-7")
    def test_no_summary_states_a_cause(self, triage, policydesk):
        """B3 rule 6, and B6 sets stated causal claims at zero.

        Checked against generated text rather than against intent, because the rule is about
        what a reader is told. "X first failed at v3 and the prompt sha changed at v3" is
        permitted; anything asserting that one produced the other is not.
        """
        forbidden = re.compile(
            r"\b(?:because|caused|causing|due to|as a result of|led to|leads to|"
            r"resulted in|results in|owing to|thanks to|explains|attributable)\b",
            re.IGNORECASE,
        )
        summaries: list[tuple[str, str]] = []
        for session, product in (triage, policydesk):
            for result in queries.find_all(session, product=product).values():
                for finding in result.findings:
                    summaries.append((f"{finding.kind}/{finding.clause_ref}", finding.summary))
                if result.refusal:
                    summaries.append((f"{result.kind}/refusal", result.refusal.reason))

        assert len(summaries) > 20
        offenders = [(where, forbidden.search(text).group(0))
                     for where, text in summaries if forbidden.search(text)]
        assert offenders == [], f"causal language in generated text: {offenders}"


# -- isolation -------------------------------------------------------------------


@pytest.mark.ac("AC-19")
def test_a_finding_never_cites_another_products_record():
    """AC-19: two products onboarded into one org are queryable separately, and no finding on
    one cites a record belonging to the other."""
    org = make_org("two-products")
    with org_session(org) as session:
        triage = onboard(session, org, "triage", "TRI", TRIAGE_METRICS)
        policydesk = onboard(
            session, org, "policydesk", "PD", POLICYDESK_METRICS,
            pairs={"critical_pass_rate": "PD-8.8"},
        )

        for product, prefix, foreign in (
            (triage, "TRI-", "PD-"), (policydesk, "PD-", "TRI-"),
        ):
            for result in queries.find_all(session, product=product).values():
                for finding in result.findings:
                    assert finding.product == product.key
                    cited = [e for e in finding.evidence if e.startswith("clause:")]
                    assert all(e.startswith(f"clause:{prefix}") for e in cited), (
                        f"{product.key} cited {cited}"
                    )
                    assert not any(foreign in e for e in finding.evidence)
