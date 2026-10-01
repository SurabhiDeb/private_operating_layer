"""Spec import. AC-1, AC-18, AC-21, EC-2, H6.

Three documents are read here, and the third is the one that matters. The two reference
specs were written by one author in one style, both using an identical
`| Metric | Target | Why |` table, so a parser tuned to them would look agnostic and not
be. `tests/fixtures/alien_spec.md` was written for this test in a deliberately different
style — prose thresholds, no metrics table, `2)` numbering, different column headers — and
it has to import on configuration alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from layer.adapters.parse import markdown as md
from layer.adapters.spec.file_spec import SpecAdapter
from layer.adapters.spec.values import normalise_metric_name, parse_target

ALIEN = Path(__file__).parent / "fixtures" / "alien_spec.md"
REFERENCE = Path("/Users/surabhideb/Desktop/chatbot-lab/products")


def thresholds(report) -> dict[str, object]:
    """Numeric clauses keyed by metric name where the source named one, otherwise by ref.

    A clause read from prose carries no metric name on purpose: a slug built from a
    sentence would never match what the eval source calls the number, so a human binds
    it at onboarding step 5.
    """
    return {
        (c.metric or c.ref_hint): c
        for c in report.candidates
        if c.value is not None
    }


class TestTargetParsing:
    @pytest.mark.parametrize(
        "text,comparator,value,unit",
        [
            ("85%", ">=", 0.85, "ratio"),
            ("100%", ">=", 1.0, "ratio"),
            ("at least 99%", ">=", 0.99, "ratio"),
            ("no more than 5%", "<=", 0.05, "ratio"),
            ("≥ 95%", ">=", 0.95, "ratio"),
            ("under 1 second", "<=", 1.0, "duration_s"),
            ("under 1500ms", "<=", 1500.0, "duration_ms"),
            ("under 3p", "<=", 0.03, "currency"),
            ("under £1,200", "<=", 1200.0, "currency"),
            ("0.85", ">=", 0.85, "ratio"),
        ],
    )
    def test_shapes_found_in_real_specifications(self, text, comparator, value, unit):
        target = parse_target(text)
        assert (target.comparator, target.value, target.unit) == (comparator, value, unit)

    def test_a_band_keeps_both_ends(self):
        """PRD B2's clause shape cannot hold this, which is why `value_high` exists."""
        target = parse_target("3% to 8%")
        assert (target.comparator, target.value, target.value_high) == ("between", 0.03, 0.08)
        assert target.direction == "within_band"

    def test_a_number_fused_to_an_identifier_is_not_a_target(self):
        """`p95 under 800ms` has two numbers and only one is the bar."""
        assert parse_target("p95 under 800ms").value == 800.0
        assert parse_target("recall@k at least 95%").value == 0.95

    def test_a_duration_or_cost_is_read_as_a_ceiling(self):
        """Nobody promises to be slow or expensive, and the assumption is recorded
        rather than hidden."""
        target = parse_target("1500ms")
        assert target.comparator == "<="
        assert any("ceiling" in a for a in target.assumptions)

    def test_an_assumed_floor_is_declared(self):
        """A bare `85%` in a Target column means a floor, but it does not say so. Every
        clause is created provisional and a human confirms, so the assumption has to
        travel with it."""
        assert any("floor" in a for a in parse_target("85%").assumptions)

    def test_text_with_no_number_yields_nothing(self):
        """H6: a clause with no measurable bar is held as a rule, never reported as
        unmeasured drift."""
        assert parse_target("It must abstain when the question is ambiguous.") is None
        assert parse_target("") is None

    def test_metric_names_normalise_towards_what_a_run_file_calls_them(self):
        assert normalise_metric_name("Overall team accuracy") == "team_accuracy"
        assert normalise_metric_name("Escalation recall") == "escalation_recall"


class TestMarkdownStructure:
    def test_an_unnumbered_subsection_inherits_its_numbered_ancestor(self):
        """A metrics table under `### Retrieval` inside `## 8. Success metrics` is still
        section 8's promise. A ref built from the subsection title would move the moment
        someone renamed the heading."""
        sections = md.parse("## 8. Success metrics\n\n### Retrieval\n\ntext\n")
        assert [s.ref for s in sections] == ["8", "8"]

    def test_a_table_is_found_by_its_divider_not_its_pipes(self):
        sections = md.parse("## 1. M\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        table = sections[-1].tables[0]
        assert table.headers == ["A", "B"]
        assert table.rows[0].cells == {"A": "1", "B": "2"}

    def test_a_row_knows_its_line(self):
        """A citation that resolves to a file is weaker than one resolving to the lines
        that said it."""
        sections = md.parse("# T\n\n## 1. M\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        assert sections[-1].tables[0].rows[0].line == 7

    def test_a_pipe_inside_a_fence_is_not_a_table(self):
        sections = md.parse("## 1. M\n\n```\n| not | a table |\n|---|---|\n```\n")
        assert sections[-1].tables == []

    def test_headers_are_matched_by_any_of_several_names(self):
        """Target, Bar and Threshold all mean the same column, and which one an author
        used is not worth a code change."""
        sections = md.parse("## 1. M\n\n| Signal | Bar |\n|---|---|\n| x | 60% |\n")
        assert sections[-1].tables[0].rows[0].get("target", "bar") == "60%"


@pytest.mark.ac("AC-21")
class TestAnUnfamiliarSpecification:
    """The agnosticism test. A document in a style the parser was not built against,
    imported on configuration alone."""

    @pytest.fixture
    def report(self):
        return SpecAdapter().read(
            ALIEN.read_text(),
            {"path": "docs/service.md", "ref_prefix": "WF",
             "target_columns": ["bar"], "rationale_columns": ["commentary"]},
        )

    def test_it_imports_without_a_code_change(self, report):
        assert report.complete, report.summary()
        assert not report.failed, report.summary()
        assert report.candidates

    def test_prose_thresholds_are_found(self, report):
        """No metrics table anywhere in this document. Every bar is in a sentence."""
        found = thresholds(report)
        assert found, "no numeric threshold was extracted from prose"
        values = {c.value for c in found.values()}
        assert 0.92 in values, "the 92% correctness bar was missed"
        assert 1500.0 in values, "the 1500ms median was missed"
        assert 4.0 in values, "the second bar in a two-bar sentence was missed"
        assert 900.0 in values, "the monthly spend ceiling was missed"

    def test_a_floor_written_as_no_less_than_stays_a_floor(self, report):
        """An inverted comparator is the most damaging parse error available: every
        value above the bar would then read as a breach."""
        floors = [c for c in thresholds(report).values() if c.value == 0.95]
        assert floors and floors[0].comparator == ">="

    def test_a_clause_read_from_prose_names_no_metric_and_says_why(self):
        """The source did not name the measurement, so the Layer does not invent one. A
        human binds it, which is what onboarding step 5 exists for."""
        report = SpecAdapter().read(ALIEN.read_text(), {"path": "x.md", "ref_prefix": "WF"})
        prose = [c for c in report.candidates if c.value is not None and c.metric is None]
        assert prose
        assert all(
            any("human must bind" in a for a in c.assumptions) for c in prose
        )

    def test_a_band_in_prose_is_found(self, report):
        bands = [c for c in report.candidates if c.comparator == "between"]
        assert bands, "the 4% to 11% band was missed"
        assert (bands[0].value, bands[0].value_high) == (0.04, 0.11)

    def test_a_differently_named_column_is_read_from_config(self, report):
        """`| Signal | Bar | Commentary |` instead of `| Metric | Target | Why |`."""
        values = {c.value for c in thresholds(report).values()}
        assert 0.6 in values and 0.25 in values

    def test_currency_and_duration_keep_their_units(self, report):
        units = {c.unit for c in thresholds(report).values()}
        assert "currency" in units and "duration_ms" in units

    def test_non_goals_are_kept_without_a_bar(self, report):
        """H6: held as stated, never counted in drift or coverage."""
        non_goals = [c for c in report.candidates if c.kind == "non_goal"]
        assert len(non_goals) == 3
        assert all(c.value is None for c in non_goals)

    def test_refs_follow_the_configured_prefix(self, report):
        assert all(c.ref_hint.startswith("WF-") for c in report.candidates)

    def test_every_candidate_carries_a_line_locator(self, report):
        assert all(c.source_locator for c in report.candidates)


class TestFailureIsolation:
    @pytest.mark.edge_case("EC-2")
    def test_an_unreadable_section_does_not_cost_the_others(self, monkeypatch):
        """EC-2: say which part failed and import the rest. Never fail the whole import."""
        adapter = SpecAdapter()
        original = adapter._from_section
        calls = {"n": 0}

        def explode(section, spec, ordinals):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ValueError("deliberate")
            return original(section, spec, ordinals)

        monkeypatch.setattr(adapter, "_from_section", explode)
        report = adapter.read(ALIEN.read_text(), {"path": "x.md", "ref_prefix": "WF"})

        assert report.failed, "the failure was swallowed"
        assert report.candidates, "one bad section aborted the whole import"
        assert report.complete, report.summary()
        assert any("did not parse" in n for n in report.notes)

    def test_a_document_with_no_promises_reports_rather_than_raising(self):
        report = SpecAdapter().read("# Title\n\nJust prose.\n", {"path": "x.md"})
        assert report.candidates == []
        assert report.complete

    @pytest.mark.ac("AC-18")
    def test_no_pattern_is_required(self):
        """AC-18: an absent pattern must onboard with no defaults applied, not refuse."""
        report = SpecAdapter().read(ALIEN.read_text(), {"path": "x.md"})
        assert report.candidates and report.complete


@pytest.mark.skipif(not REFERENCE.exists(), reason="reference specs not present")
class TestAgainstTheReferenceSpecifications:
    """Two documents whose numbers can be checked by hand against the handoff's tables."""

    def _report(self, product: str, prefix: str):
        return SpecAdapter().read(
            (REFERENCE / product / "SPEC.md").read_text(),
            {"path": f"products/{product}/SPEC.md", "ref_prefix": prefix},
        )

    def test_the_classifier_spec_yields_its_stated_bars(self):
        found = thresholds(self._report("triage", "TRI"))
        assert found["team_accuracy"].value == 0.85
        assert found["escalation_recall"].value == 0.99
        assert found["escalation_precision"].value == 0.70
        assert found["contract_validity"].value == 1.0
        band = found["needs_clarification_rate"]
        assert (band.comparator, band.value, band.value_high) == ("between", 0.03, 0.08)

    def test_budget_clauses_are_thresholds_too(self):
        """Latency and cost carry bars like anything else, and are unrepresentable
        without `unit`."""
        found = thresholds(self._report("triage", "TRI"))
        assert (found["p95_latency"].comparator, found["p95_latency"].unit) == ("<=", "duration_s")
        assert found["cost_per_month"].value == 1200.0
        assert found["cost_per_month"].unit == "currency"

    def test_the_rag_spec_yields_its_stated_bars(self):
        found = thresholds(self._report("policydesk", "PD"))
        assert found["recall_5"].value == 0.95
        assert found["mrr"].value == 0.85
        assert found["groundedness"].value == 0.98
        assert found["abstention_recall"].value == 0.95

    def test_refs_are_built_from_section_numbers_and_are_unique(self):
        """Independently landing on the same refs the prototype used — `TRI-11.2` for
        escalation recall — is a useful check that the scheme is the obvious one."""
        report = self._report("triage", "TRI")
        refs = [c.ref_hint for c in report.candidates]
        assert len(refs) == len(set(refs)), "a ref was issued twice"
        found = thresholds(report)
        assert found["escalation_recall"].ref_hint == "TRI-11.2"

    def test_both_specifications_account_for_every_section(self):
        for product, prefix in (("triage", "TRI"), ("policydesk", "PD")):
            report = self._report(product, prefix)
            assert report.complete, f"{product}: {report.summary()}"
            assert not report.failed, f"{product}: {report.summary()}"

    def test_a_clause_without_a_number_is_not_a_threshold(self):
        """H6 again, on real text: an ambiguity policy has no bar and must never appear
        as unmeasured drift."""
        report = self._report("triage", "TRI")
        unmeasurable = [c for c in report.candidates if c.value is None]
        assert unmeasurable
        assert all(c.kind != "threshold" for c in unmeasurable)
