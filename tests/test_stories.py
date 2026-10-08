"""One test per user-story criterion. PRD A7, AC-11.

AC-11 is "every US-1 to US-13 acceptance criterion has a test, and each passes". Until
step 13 **no test in the suite carried the `story` marker** — `pytest.ini` had declared it
from the start and nothing ever used it — so the criterion could not be answered by
running anything. This file answers it for the nine stories whose behaviour exists in
phases 1 to 3; US-1, US-2, US-10 and US-12 are the write half and are declared in
`test_matrix.py` with their phase.

**One test per criterion, not per story.** A story with four `shall` clauses needs four
tests, and the matrix counts them. Each test quotes the criterion it covers, so a reader
can check the mapping without holding the specification open beside it.

**Where a criterion's positive behaviour belongs to a later phase**, the test covers the
half that exists — almost always a refusal that names what is missing — and says so. That
is the required behaviour today: PRD B1 is explicit that the Layer never silently returns
nothing, and "no stalled decisions" from a Layer with no decision source would be a claim
about records it has never seen.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from layer.answers import reads, writes
from layer.core.db import org_session
from layer.core.errors import NotLive, Refusal
from layer.db.models import AuditEvent, Binding, Clause, EnforcementFact, Link, Proposal
from layer.findings import queries
from layer.onboarding import bindings as binding_gate
from layer.onboarding import run, state

from conftest import make_org
import test_findings as tf

REFERENCE = Path("/Users/surabhideb/Desktop/chatbot-lab")
LAYER = Path(__file__).resolve().parent.parent / "layer"
ACTOR = "pm@example.invalid"
AGENT = "mcp:agent"

pytestmark = pytest.mark.skipif(
    not REFERENCE.exists(), reason="reference products not present"
)


#: Onboarded per test, not per module. The harness truncates every tenant after each
#: test — deliberately, so no test can see another's rows — so a module-scoped product
#: exists for exactly one test and raises `UnknownProduct` for every one after it. Found
#: by writing one and watching 27 tests error identically.
@pytest.fixture
def triage():
    """A fully onboarded classifier, with a code source so enforcement is assessed."""
    org = make_org("stories-triage")
    with org_session(org) as session:
        product = tf.onboard(
            session, org, "triage", "TRI", tf.TRIAGE_METRICS,
            cases={"input_field": "message"},
            code={
                "files": ["products/triage/gate.py", ".github/workflows/ci.yml"],
                "metric_aliases": {
                    "escalation_recall": ["missed"],
                    "contract_validity": ["broken", "problem"],
                },
            },
        )
        yield session, product


@pytest.fixture
def policy():
    org = make_org("stories-policy")
    with org_session(org) as session:
        product = tf.onboard(
            session, org, "policydesk", "PD", tf.POLICYDESK_METRICS,
            cases={"input_field": "question"},
            pairs={"critical_pass_rate": "PD-8.8"},
        )
        yield session, product


# -- US-3 — promise versus enforcement -------------------------------------------


class TestUS3PromiseVersusEnforcement:
    @pytest.mark.story("US-3")
    def test_the_thresholds_enforced_in_ci_are_extracted(self, triage):
        """"When a push occurs, or nightly, the Layer shall extract the thresholds
        enforced in CI config." The scan is on demand here; the schedule is operations."""
        session, product = triage
        facts = session.execute(
            select(EnforcementFact).where(EnforcementFact.product_id == product.id)
        ).scalars().all()

        assert facts, "no enforcement fact was extracted from the code source"
        assert any(f.threshold is not None for f in facts)
        assert all(f.file for f in facts)

    @pytest.mark.story("US-3")
    def test_every_clause_stated_but_not_enforced_is_reported(self, triage):
        """"The Layer shall report every clause whose threshold is stated but not
        enforced"."""
        session, product = triage
        reported = {f.clause_ref for f in queries.find_unenforced(session, product=product)}

        assert reported
        enforced_fully = {
            f.metric for f in session.execute(
                select(EnforcementFact).where(
                    EnforcementFact.product_id == product.id,
                    EnforcementFact.enforced.is_(True),
                    EnforcementFact.scope == "all_runs",
                    EnforcementFact.partial.is_(False),
                )
            ).scalars()
        }
        bound = binding_gate.confirmed_metrics(session, product=product)
        for metric, ref in bound.items():
            if metric in enforced_fully:
                assert ref not in reported, (
                    f"{ref} is fully enforced and was reported as unenforced"
                )

    @pytest.mark.story("US-3")
    def test_a_missing_check_is_reported_rather_than_opened_as_a_pull_request(self, triage):
        """"Where a missing check can be expressed in the repository's existing gate, the
        Layer shall open a pull request adding it."

        **The Layer opens no pull requests yet**, which is phase 5's work, so the covered
        half is that the gap is reported with the file and line a human would edit. A
        test asserting a pull request would have to invent one.
        """
        session, product = triage
        findings = queries.find_unenforced(session, product=product)
        with_a_site = [
            f for f in findings
            if any(e.startswith("file:") for e in f.evidence)
        ]

        assert with_a_site, "no unenforced finding names a file a human could edit"
        assert all("file:" in " ".join(f.evidence) for f in with_a_site)

    @pytest.mark.story("US-3")
    def test_nothing_in_the_layer_can_push_to_a_branch(self):
        """"The Layer shall not push to a protected branch under any circumstance."

        Asserted against the source rather than against behaviour: the guarantee worth
        having is that no code path exists, not that the one we happened to exercise did
        not take it.
        """
        pushes = []
        for path in sorted(LAYER.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for number, line in enumerate(path.read_text().splitlines(), start=1):
                if re.search(r"""git['"]?\s*,\s*['"]push|git\s+push|\bgh\s+pr\s+create""", line):
                    pushes.append(f"{path.relative_to(LAYER.parent)}:{number}")
        assert pushes == [], f"a push path exists: {pushes}"


# -- US-4 — which tickets are blocked --------------------------------------------


class TestUS4BlockedTickets:
    @pytest.mark.story("US-4")
    def test_the_walk_from_a_ticket_refuses_by_naming_the_missing_source(self, triage):
        """"When asked, the Layer shall walk ticket to acceptance criterion to clause to
        latest observation."

        **No ticket source can be bound yet** (phase 6). The covered half is that the
        request is refused by name: answering "nothing is blocked" would be a claim about
        tickets the Layer has never read.
        """
        session, product = triage
        result = reads.blocked_tickets(session, product.key)

        assert isinstance(result, Refusal)
        assert "ticket source" in result.reason
        assert "a source with role 'ticket'" in result.missing

    @pytest.mark.story("US-4")
    def test_the_clause_the_bar_the_value_and_the_age_are_available_to_return(self, triage):
        """"The Layer shall return the ticket, the clause, the stated bar, the current
        value, and since when."

        The four the Layer owns are already in one answer; the ticket is the fifth and
        arrives with the source. `get_clause` is the shape US-4 will read from.
        """
        session, product = triage
        answer = reads.get_clause(session, "TRI-11.2")

        assert answer.detail["bar"] == ">= 0.99"
        assert answer.detail["latest_value"] is not None
        assert answer.detail["as_of"] is not None
        assert answer.detail["metric"]

    @pytest.mark.story("US-4")
    def test_a_clause_with_nothing_bound_to_it_is_listed_separately(self, triage):
        """"If a ticket has no clause bound to it, then the Layer shall list it
        separately as unlinked rather than as unblocked."

        The same distinction one level down, which is the one the Layer can make today: a
        clause with no binding is reported as a coverage gap, never as satisfied.
        """
        session, product = triage
        uncovered = queries.find_uncovered(session, product=product)
        unlinked = [f for f in uncovered if f.detail["reason"] == "no_assertion"]

        assert unlinked
        for finding in unlinked:
            assert finding.detail["metric"] is None
            assert "nothing in the connected sources answers this promise" in finding.summary

    @pytest.mark.story("US-4")
    def test_the_story_writes_nothing(self, triage):
        """"The Layer shall not write anything in service of this story"."""
        session, product = triage
        before = session.execute(select(AuditEvent.id)).scalars().all()

        reads.blocked_tickets(session, product.key)
        queries.find_uncovered(session, product=product)
        reads.get_clause(session, "TRI-11.2")

        after = session.execute(select(AuditEvent.id)).scalars().all()
        assert after == before, "a read path wrote an audit event"


# -- US-5 — why the product behaves this way -------------------------------------


class TestUS5TheChain:
    @pytest.fixture
    def chained(self, triage):
        session, product = triage
        session.add_all([
            Link(org_id=product.org_id, product_id=product.id,
                 from_ref="requirement:R-9", to_ref="clause:TRI-11.2",
                 link_type="governs", created_by="spec:handwritten"),
            Link(org_id=product.org_id, product_id=product.id,
                 from_ref="clause:TRI-11.2", to_ref="metric:escalation_recall",
                 link_type="asserts", created_by="binding:confirmed"),
        ])
        session.flush()
        return session, product

    @pytest.mark.story("US-5")
    def test_the_chain_is_returned_in_both_directions(self, chained):
        """"When given any entity, the Layer shall return the chain reachable from it in
        both directions"."""
        session, _ = chained
        answer = reads.trace_chain(session, "clause:TRI-11.2")
        edges = {(e["from"], e["to"]) for e in answer.detail["links"]}

        assert ("requirement:R-9", "clause:TRI-11.2") in edges
        assert ("clause:TRI-11.2", "metric:escalation_recall") in edges

    @pytest.mark.story("US-5")
    def test_every_link_carries_its_type_and_its_source_system(self, chained):
        """"The Layer shall label every link with its type and its source system"."""
        session, _ = chained
        answer = reads.trace_chain(session, "clause:TRI-11.2")

        assert answer.detail["links"]
        for edge in answer.detail["links"]:
            assert edge["link_type"]
            assert edge["source"], f"{edge} carries no source system"

    @pytest.mark.story("US-5")
    def test_an_absent_link_is_named(self, chained):
        """"Where a link in the expected chain is absent, the Layer shall name the gap
        rather than omitting it"."""
        session, _ = chained
        answer = reads.trace_chain(session, "clause:TRI-11.2")

        assert "sets" in answer.detail["gaps"]
        assert "sets" in " ".join(answer.caveats)
        assert answer.confidence != "high"

    @pytest.mark.story("US-5")
    def test_a_node_the_layer_stores_resolves_to_a_url(self, chained):
        """"Each node shall resolve to a URL a human can open."

        Where it cannot — a requirement ref from a source nobody has bound — the ref is
        reported in `unresolved` rather than dropped, which is what B1 requires and what
        makes the gap visible instead of invisible.
        """
        session, _ = chained
        answer = reads.trace_chain(session, "clause:TRI-11.2")

        resolved = {link["id"] for link in answer.citation_links}
        assert "clause:TRI-11.2" in resolved
        assert all(url.startswith("http") for url in
                   (link["url"] for link in answer.citation_links))
        assert set(answer.citations) == resolved | set(answer.unresolved)


# -- US-6 — under-specified requirement ------------------------------------------


class TestUS6UnderSpecified:
    @pytest.mark.story("US-6")
    def test_the_under_specified_query_refuses_without_a_production_source(self, policy):
        """"Where a production metric sits outside its band while every bound assertion
        passes, the Layer shall report the requirement as under-specified."

        **No production source can be bound yet** (phase 6), and this is the highest-value
        finding in the specification. Refused by name rather than answered empty, because
        an empty answer here reads as "the evals and production agree".
        """
        session, product = policy
        result = queries.find_underspecified(session, product=product)

        assert result.refusal is not None
        assert "production" in result.refusal.reason
        assert "a source with role 'production'" in result.refusal.missing

    @pytest.mark.story("US-6")
    def test_the_absent_assertion_is_named_not_merely_counted(self, policy):
        """"The Layer shall name the assertion that does not exist, not merely that one
        is missing"."""
        session, product = policy
        uncovered = queries.find_uncovered(session, product=product)

        assert uncovered.findings
        for finding in uncovered:
            assert finding.clause_ref or finding.detail.get("metric"), finding.summary
            assert finding.detail["reason"]
            # The summary names the thing, not a count of things.
            assert not re.match(r"^\d+ ", finding.summary)

    @pytest.mark.story("US-6")
    def test_a_measurement_no_clause_promises_is_a_coverage_gap(self, policy):
        """"Where a production metric has no clause bound to it at all, the Layer shall
        report that as a coverage gap." H16's direction: the gap is the absent clause."""
        session, product = policy
        uncovered = queries.find_uncovered(session, product=product)
        orphans = [
            f for f in uncovered
            if f.detail["reason"] == "metric_without_clause"
        ]

        assert orphans, "a measured metric with no clause was not reported"
        assert all(f.clause_ref is None for f in orphans)

    @pytest.mark.story("US-6")
    def test_the_condition_is_never_attributed_to_the_model(self, policy):
        """"The Layer shall not attribute the condition to the model"."""
        session, product = policy
        blamed = re.compile(
            r"\b(?:the model|model(?:'s)? (?:fault|failure|error)|because the model)\b",
            re.IGNORECASE,
        )
        for result in queries.find_all(session, product=product).values():
            for finding in result.findings:
                assert not blamed.search(finding.summary), finding.summary
            if result.refusal:
                assert not blamed.search(result.refusal.reason)


# -- US-7 — decisions that produced no action ------------------------------------


class TestUS7StalledDecisions:
    @pytest.mark.story("US-7")
    def test_the_sweep_refuses_by_naming_the_missing_source(self, triage):
        """"When the sweep runs, the Layer shall report decisions with no resulting pull
        request, ticket or spec change." No decision source exists yet (phase 6)."""
        session, product = triage
        result = queries.find_stalled_decisions(session, product=product)

        assert result.refusal is not None
        assert "decision" in result.refusal.reason
        assert len(result) == 0

    @pytest.mark.story("US-7")
    def test_the_refusal_names_what_it_would_need_to_state_an_age(self, triage):
        """"The Layer shall state how long each has been sitting." The elapsed time needs
        a decision with a date, which needs the source; the refusal names it."""
        session, product = triage
        result = queries.find_stalled_decisions(session, product=product)

        assert result.refusal.missing
        assert any("decision" in item for item in result.refusal.missing)

    @pytest.mark.story("US-7")
    def test_nothing_infers_what_the_action_should_have_been(self, triage):
        """"The Layer shall not infer what the action should have been."

        Nothing anywhere proposes an action for a decision, and the refusal says the
        Layer has not seen any decisions rather than guessing at them.
        """
        session, product = triage
        result = queries.find_stalled_decisions(session, product=product)

        assert "never seen" in result.refusal.reason or "has no" in result.refusal.reason
        assert session.execute(
            select(Proposal).where(Proposal.kind == "decision")
        ).scalars().all() == []


# -- US-8 — metric movements ------------------------------------------------------


class TestUS8MetricMovements:
    @pytest.mark.story("US-8")
    def test_every_observation_is_returned_in_order(self, policy):
        """"When asked for a clause's history, the Layer shall return every observation
        in order"."""
        session, product = policy
        answer = reads.metric_history(session, "PD-8.8")
        times = [o["measured_at"] for o in answer.detail["observations"]]

        assert len(times) == answer.detail["count"] == 7
        assert times == sorted(times)

    @pytest.mark.story("US-8")
    def test_the_points_where_an_input_changed_are_marked(self, policy):
        """"The Layer shall mark points where the prompt version or corpus changed."

        Marked rather than left for a reader to compare twelve rows by eye, which is what
        the history returned before step 13.
        """
        session, product = policy
        answer = reads.metric_history(session, "PD-8.8")
        observations = answer.detail["observations"]

        assert observations[0]["changed"] == [], "the first run has nothing to differ from"
        changed = [o for o in observations if o["changed"]]
        assert changed, "no change in any recorded input was marked across seven runs"
        assert all(
            set(o["changed"]) <= {"prompt_version", "corpus_sha", "code_rev"}
            for o in changed
        )

    @pytest.mark.story("US-8")
    @pytest.mark.hard_case("H1")
    def test_a_movement_with_no_change_in_any_version_says_so(self, policy):
        """"If a step occurs with no change in any recorded version, then the Layer shall
        state that versioning does not explain the movement."

        H1's required sentence, which existed in a docstring and in nothing the Layer ever
        said until step 13. The fixture's instance is three runs sharing one prompt sha
        and one corpus sha while scoring 92, 92 and 94 — a group, not an adjacent pair,
        which is why the first implementation found nothing.
        """
        session, product = policy
        binding_gate.decide(
            session, product=product, metric="status_ok", clause_ref="PD-8.8",
            decision="confirmed", by=ACTOR, note="paired by hand to reach H1's metric",
        )
        answer = reads.metric_history(session, "PD-8.8")
        unexplained = answer.detail["unexplained_steps"]

        assert unexplained, "the condition is in the committed history and was not reported"
        assert "versioning does not explain the movement" in answer.statement
        group = max(unexplained, key=lambda g: len(g["runs"]))
        assert len(group["runs"]) >= 2
        assert len(group["values"]) >= 2
        assert group["prompt_version"] or group["corpus_sha"]
        # And no cause is named anywhere in it.
        assert "caused" not in answer.statement
        assert all("caused" not in g["note"] for g in unexplained)


# -- US-9 — onboarding ------------------------------------------------------------


class TestUS9Onboarding:
    @pytest.mark.story("US-9")
    def test_clauses_are_extracted_from_a_spec_like_document(self, triage):
        """"Where a repository contains a spec-like document, the Layer shall propose
        clauses extracted from it for human confirmation"."""
        session, product = triage
        clauses = session.execute(
            select(Clause).where(
                Clause.product_id == product.id, Clause.status == "active"
            )
        ).scalars().all()

        assert len(clauses) > 10
        assert all(c.source_locator for c in clauses if c.comparator)

    @pytest.mark.story("US-9")
    def test_a_product_with_no_spec_is_told_rather_than_left_empty(self):
        """"Where no spec exists, the Layer shall propose clauses derived from the
        existing eval suite and the pattern default."

        **The derivation is phase 6's** and is the one US-9 criterion not built. The
        covered half is that the absence is named rather than presented as nothing to do,
        which is EC-1's rule and the failure mode that matters.
        """
        org = make_org("us9-nospec")
        with org_session(org) as session:
            product = state.register(
                session, org_id=org, key="bare", name="Bare", ref_prefix="BA",
                actor=ACTOR,
            )
            with pytest.raises(state.StepNotReady) as raised:
                run.import_spec(session, product=product, actor=ACTOR)
            assert "spec source" in str(raised.value)

    @pytest.mark.story("US-9")
    def test_every_clause_is_created_provisional(self, triage):
        """"Every clause produced this way shall be created in state `provisional`"."""
        session, product = triage
        events = session.execute(
            select(AuditEvent.detail).where(AuditEvent.action == "clause_created")
        ).scalars().all()

        assert events
        # The row's state moves to `measured` once something measures it, so the claim is
        # about creation: every clause enters provisional, which the import's own audit
        # events record one per clause.
        unmeasured = session.execute(
            select(Clause.state).where(
                Clause.product_id == product.id, Clause.status == "active",
                Clause.ref.not_in(
                    select(Binding.clause_ref).where(Binding.decision == "confirmed")
                ),
            )
        ).scalars().all()
        assert set(unmeasured) <= {"provisional"}

    @pytest.mark.story("US-9")
    def test_a_provisional_threshold_is_never_a_met_or_missed_bar(self, triage):
        """"The Layer shall not treat a provisional threshold as a met or missed bar"."""
        session, product = triage
        rows = session.execute(
            select(Clause.ref, Clause.verdict).where(
                Clause.product_id == product.id, Clause.status == "active",
                Clause.state == "provisional",
            )
        ).all()

        assert rows
        assert all(verdict not in ("met", "missed") for _, verdict in rows)

    @pytest.mark.story("US-9")
    def test_each_candidate_binding_is_presented_and_the_layer_waits(self):
        """"When clauses and observations both exist, the Layer shall present each
        candidate metric-to-clause binding for confirmation and shall wait."

        The waiting is the point. With clauses and observations both loaded and no
        decision taken, the candidates are listed and `measure` refuses — so a product
        cannot acquire verdicts for pairings nobody confirmed.
        """
        org = make_org("us9-wait")
        common = {"local_path": str(REFERENCE),
                  "repo_url": "https://github.com/SurabhiDeb/chatbot-lab"}
        with org_session(org) as session:
            product = state.register(
                session, org_id=org, key="triage", name="Triage", ref_prefix="TRI",
                actor=ACTOR,
            )
            state.bind_source(
                session, product=product, role="spec", kind="repo", actor=ACTOR,
                config={**common, "path": "products/triage/SPEC.md"},
            )
            state.bind_source(
                session, product=product, role="eval", kind="repo", actor=ACTOR,
                config={**common, "globs": ["products/triage/runs/*.json"], "readers": [{
                    "glob": "products/triage/runs/*.json",
                    "measured_at": "/meta/timestamp_utc",
                    "prompt_version": "/meta/prompt_sha",
                    "metrics": tf.TRIAGE_METRICS,
                }]},
            )
            run.import_spec(session, product=product, actor=ACTOR)
            run.backfill(session, product=product, actor=ACTOR)

            candidates = binding_gate.propose(session, product=product).pending
            assert candidates, "no candidate pairing was presented"
            assert all(c.metric and c.clause_ref for c in candidates)

            with pytest.raises(state.StepNotReady) as raised:
                state.measure(session, product=product, actor=ACTOR)
            assert "awaiting a decision" in str(raised.value)

    @pytest.mark.story("US-9")
    def test_nothing_is_proposed_until_a_binding_is_confirmed(self):
        """"The Layer shall not propose any change to any clause, eval suite or CI file
        until at least one binding on that product has been confirmed by a human."
        B3 rule 8, and AC-17."""
        org = make_org("us9-gate")
        with org_session(org) as session:
            product = state.register(
                session, org_id=org, key="gated", name="Gated", ref_prefix="GA",
                actor=ACTOR,
            )
            with pytest.raises(NotLive):
                state.require_live(session, product=product)
            result = writes.propose_binding(
                session, product_key=product.key, metric="m", clause_ref="GA-1",
                reason="r", actor=AGENT, if_rejected="nothing changes",
            )
            assert isinstance(result, Refusal)
            assert session.execute(select(Proposal)).scalars().all() == []

    @pytest.mark.story("US-9")
    def test_an_unrecognised_pattern_completes_onboarding_with_no_defaults(self):
        """"Where the product's pattern is unrecognised or absent, the Layer shall
        complete onboarding with no defaults applied rather than refuse." AC-18."""
        org = make_org("us9-pattern")
        with org_session(org) as session:
            product = state.register(
                session, org_id=org, key="odd", name="Odd", pattern="something-nobody-listed",
                ref_prefix="OD", actor=ACTOR,
            )
            assert product.pattern == "something-nobody-listed"
            assert product.status == "registering"
            assert session.execute(
                select(Clause).where(Clause.product_id == product.id)
            ).scalars().all() == [], "a default clause set was applied"


# -- US-11 — across several products ----------------------------------------------


class TestUS11AcrossProducts:
    @pytest.fixture
    def two_products(self):
        org = make_org("us11")
        with org_session(org) as session:
            tf.onboard(session, org, "triage", "TRI", tf.TRIAGE_METRICS)
            tf.onboard(
                session, org, "policydesk", "PD", tf.POLICYDESK_METRICS,
                pairs={"critical_pass_rate": "PD-8.8"},
            )
            yield session, org

    @pytest.mark.story("US-11")
    def test_a_question_with_no_product_named_answers_across_the_tenant(self, two_products):
        """"When asked without naming a product, the Layer shall answer across every
        product in the tenant." Added in step 13: the capability did not exist."""
        session, _ = two_products
        answer = reads.findings_across_products(session, "drift")

        assert set(answer.detail["by_product"]) == {"triage", "policydesk"}
        assert answer.detail["count"] >= 2
        assert "across 2 product(s)" in answer.statement

    @pytest.mark.story("US-11")
    def test_every_finding_is_attributed_to_its_product(self, two_products):
        """"The Layer shall attribute every finding to its product"."""
        session, _ = two_products
        answer = reads.findings_across_products(session, "drift")

        for key, findings in answer.detail["by_product"].items():
            for finding in findings:
                assert finding["product"] == key

    @pytest.mark.story("US-11")
    def test_no_record_from_another_tenant_is_returned(self, two_products):
        """"The Layer shall not return any record from another tenant." AC-19 and H11,
        from the cross-product surface rather than the single-product one."""
        session, org = two_products
        other = make_org("us11-other")
        with org_session(other) as elsewhere:
            tf.onboard(elsewhere, other, "triage", "TRI", tf.TRIAGE_METRICS)

        answer = reads.findings_across_products(session, "drift")
        assert set(answer.detail["by_product"]) == {"triage", "policydesk"}

        with org_session(other) as elsewhere:
            theirs = reads.findings_across_products(elsewhere, "drift")
            assert set(theirs.detail["by_product"]) == {"triage"}


# -- US-13 — which runs are the proof ---------------------------------------------


class TestUS13TheProof:
    @pytest.mark.story("US-13")
    def test_the_failing_runs_and_the_cases_within_them_are_returned(self, triage):
        """"When asked why a clause is in `missed`, the Layer shall return the failing
        runs, and within them the individual failing cases, each with its run url"."""
        session, product = triage
        answer = reads.failing_cases(session, "TRI-11.2")

        assert answer.detail["runs_missed"] == 7
        cases = answer.detail["failing_cases"]
        assert cases
        assert all(c["run_url"] and c["case_id"] and c["outcome"] for c in cases)

    @pytest.mark.story("US-13")
    def test_the_counts_are_stated_in_the_form_the_evidence_supports(self, triage):
        """"The Layer shall state the counts in the form the evidence supports, such as
        missed in 7 of 47 runs with the worst at 80%, rather than a single average"."""
        session, product = triage
        finding = next(
            f for f in queries.find_drift(session, product=product)
            if f.clause_ref == "TRI-11.2"
        )

        assert "missed in 7 of 47 runs" in finding.summary
        assert "worst 80" in finding.summary
        assert finding.detail["worst_run"]

    @pytest.mark.story("US-13")
    def test_a_deleted_trace_returns_the_outcome_and_is_marked_unavailable(self):
        """"Where a case's trace body has been deleted at the source, the Layer shall
        return the stored outcome and shall mark the trace unavailable, never omitting the
        case"."""
        org = make_org("us13-retention")
        with org_session(org) as session:
            tf.onboard(
                session, org, "policydesk", "PD", tf.POLICYDESK_METRICS,
                cases={"input_field": "question"},
                pairs={"critical_pass_rate": "PD-8.8"},
                retention="1d",
            )
            product = state.get(session, key="policydesk")
            answer = reads.failing_cases(session, "PD-8.8")
            cases = answer.detail["failing_cases"]

            assert cases, "cases were omitted rather than marked"
            assert all(c["trace_available"] is False for c in cases)
            assert all(c["trace_state"] == "past_retention" for c in cases)
            assert all(c["outcome"] in ("fail", "error") for c in cases)

    @pytest.mark.story("US-13")
    def test_the_history_shows_what_each_run_carried(self, policy):
        """"When asked about any clause's history across versions, the Layer shall show
        which `prompt_version` and `corpus_sha` each run carried, and shall mark where
        they did not change"."""
        session, product = policy
        answer = reads.metric_history(session, "PD-8.8")

        for observation in answer.detail["observations"]:
            assert "prompt_version" in observation
            assert "corpus_sha" in observation
            assert "changed" in observation
            assert "inputs_unchanged" in observation

    @pytest.mark.story("US-13")
    def test_no_answer_needs_a_live_call_to_the_eval_platform(self, triage):
        """"The Layer shall not require a live call to the eval platform to answer any of
        the above."

        Asserted by answering with the source's own repository made unreachable: the
        proof comes out of `case_result`, and the only thing that breaks is a citation,
        which is reported as unresolved rather than failing the call.
        """
        session, product = triage
        for source in state.sources_by_role(session, product=product)["eval"]:
            source.config = {**source.config, "local_path": "/nonexistent/path"}
        session.flush()

        answer = reads.failing_cases(session, "TRI-11.2")

        assert answer.detail["failing_cases"], "the proof needed the source to be present"
        assert answer.detail["runs_missed"] == 7

    @pytest.mark.story("US-13")
    def test_a_missed_bar_is_never_asserted_with_no_cases(self, triage, policy):
        """"The Layer shall not assert a missed bar while returning no cases. See B3 rule
        10." Over both products and every clause, not the one the condition was written
        for."""
        for session, product in (triage, policy):
            for finding in queries.find_drift(session, product=product):
                if not finding.detail["runs_missed"]:
                    continue
                state_ = finding.detail["failing_cases_state"]
                assert state_ in ("cited", "not_applicable", "no_failing_cases")
                if state_ == "cited":
                    assert finding.detail["failing_cases"]
                else:
                    assert state_ in finding.detail["failing_cases_state"]
                    assert len(finding.summary) > 80
