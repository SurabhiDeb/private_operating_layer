"""The stdio MCP server. AC-31, AC-32, AC-20, AC-24, US-5, B11.

PRD A6's bar is that the Layer is "fully usable over stdio from a terminal with no Dust at
all, and every acceptance criterion in Part C must pass that way". Two of these tests are
about the boundary rather than the behaviour: AC-31 asserts against the **served tool
list**, because a security boundary that holds only while nobody adds a decorator is not a
boundary, and AC-32 sweeps the whole surface rather than sampling it.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from layer.answers import reads, writes
from layer.answers.shapes import CONFIDENCES, sentences
from layer.core.db import org_session
from layer.core.errors import Refusal, Unreadable
from layer.db.models import Link, Proposal
from layer.mcp import server as mcp_server
from layer.onboarding import state

from conftest import make_org
import test_findings as tf

REFERENCE = Path("/Users/surabhideb/Desktop/chatbot-lab")
ACTOR = "pm@example.invalid"
AGENT = "mcp:agent"


def tool_names(server) -> list[str]:
    return sorted(t.name for t in asyncio.run(server.list_tools()))


def tools(server) -> dict:
    return {t.name: t for t in asyncio.run(server.list_tools())}


@pytest.fixture(scope="module")
def served():
    """The tool list, which needs no database: the surface is static."""
    return tools(mcp_server.build(uuid.uuid4()))


# -- the boundary ----------------------------------------------------------------


class TestTheApprovalBoundary:
    """AC-31 and B11's third tier.

    The approval boundary lives inside the server, not in an agent's prompt or its tool
    allowlist, so it holds regardless of how an agent is configured, who wired it up, or
    what an ingested document tells that agent to do.
    """

    @pytest.mark.ac("AC-31")
    def test_no_human_only_tool_is_served(self, served):
        leaked = [name for name in mcp_server.HUMAN_ONLY if name in served]
        assert leaked == [], f"human-only tools reachable over MCP: {leaked}"

    @pytest.mark.ac("AC-31")
    def test_confirm_binding_and_accept_proposal_in_particular(self, served):
        """AC-31 names these two. `confirm_binding` is the one that is easy to miss: an
        agent able to confirm its own binding manufactures B3 rule 8's precondition and
        then proposes freely, which turns the gate into a formality."""
        assert "confirm_binding" not in served
        assert "accept_proposal" not in served
        assert "reject_proposal" not in served

    @pytest.mark.ac("AC-31")
    def test_the_human_only_tier_is_absent_from_the_module_not_merely_undecorated(self):
        """B11: "not merely unexposed but absent". A function sitting in the module
        undecorated is one decorator away from being served, and a reviewer reading the
        file would have to notice its absence from a list rather than its absence."""
        source = inspect.getsource(mcp_server)
        for name in mcp_server.HUMAN_ONLY:
            assert f"def {name}(" not in source, (
                f"{name} is defined in the server module. B11 requires the human-only "
                f"tier to be absent, not undecorated."
            )

    @pytest.mark.ac("AC-32")
    def test_no_tool_accepts_an_org_id(self, served):
        """B11 rule 4. The tenant comes from the process and RLS enforces it; a tool that
        took one from its caller would be a documented way across the boundary."""
        offenders = []
        for name, tool in served.items():
            # `input_schema` in mcp 2.2.0, not `inputSchema`. Read off the model
            # rather than recalled from the protocol's own JSON naming.
            properties = (tool.input_schema or {}).get("properties") or {}
            for argument in properties:
                if "org" in argument.lower():
                    offenders.append(f"{name}({argument})")
        assert offenders == [], offenders

    def test_the_whole_b11_surface_is_served(self, served):
        """Every tool B11 lists in its first two tiers, by name. A missing one is a
        capability a human can reach from the terminal and an agent cannot, which A6
        forbids."""
        expected = {
            "list_products", "get_product", "source_status", "list_clauses",
            "get_clause", "trace_chain", "find_unenforced", "find_uncovered",
            "find_drift", "find_stalled_decisions", "metric_history", "failing_cases",
            "blocked_tickets",
            "record_observation", "record_case_results", "propose_change",
            "propose_link", "propose_binding",
        }
        assert expected <= set(served), sorted(expected - set(served))

    def test_every_tool_describes_itself(self, served):
        """The description is the only thing an agent reads before choosing a tool."""
        undescribed = [n for n, t in served.items() if not (t.description or "").strip()]
        assert undescribed == []


# -- the shapes ------------------------------------------------------------------


@pytest.mark.skipif(not REFERENCE.exists(), reason="reference products not present")
class TestEveryToolReturnsAB1Shape:
    """AC-32, swept across the surface rather than sampled.

    Every tool returns an Answer, a Finding set, a Proposal or a Refusal. A tool that
    returns raw rows is a defect (B11 rule 1), and the only way to know is to call them
    all.
    """

    SHAPES = {"answer", "findings", "proposal", "refusal", "finding"}

    #: Tools that must answer rather than refuse against a fully onboarded product.
    #: Named because the sweep alone is satisfiable by refusing everything — a refusal is
    #: one of the four shapes, so a server that could see no data at all passed this file
    #: until the fixture stopped leaving its writes uncommitted.
    MUST_ANSWER = (
        "list_products", "get_product", "source_status", "list_clauses",
        "get_clause", "metric_history", "failing_cases", "trace_chain",
    )

    @pytest.fixture
    def live(self):
        """An onboarded product, **committed**.

        The server opens its own session, so anything left in an open transaction here is
        invisible to it. That is not a quirk to work around: it is how the server will
        really be run, one process per tenant reading what onboarding committed.
        """
        org = make_org("mcp-live")
        with org_session(org) as session:
            product = tf.onboard(
                session, org, "triage", "TRI", tf.TRIAGE_METRICS,
                cases={"input_field": "message"},
            )
            key = product.key
        yield org, key

    def _calls(self, product_key: str, clause_ref: str) -> dict:
        return {
            "list_products": {},
            "get_product": {"key": product_key},
            "source_status": {"product": product_key},
            "list_clauses": {"product": product_key},
            "get_clause": {"ref": clause_ref},
            "metric_history": {"clause_ref": clause_ref},
            "failing_cases": {"clause_ref": clause_ref},
            "trace_chain": {"entity": f"clause:{clause_ref}"},
            "blocked_tickets": {"product": product_key},
            "find_drift": {"product": product_key},
            "find_unenforced": {"product": product_key},
            "find_uncovered": {"product": product_key},
            "find_stalled_decisions": {"product": product_key},
            "find_underspecified": {"product": product_key},
        }

    @pytest.mark.ac("AC-32")
    def test_every_read_tool_returns_one_of_the_four_shapes(self, live):
        org, key = live
        server = mcp_server.build(org)
        served = tools(server)
        answered = []

        for name, arguments in self._calls(key, "TRI-11.2").items():
            result = asyncio.run(server.call_tool(name, arguments))
            payload = _payload(result)
            assert isinstance(payload, dict), f"{name} returned {type(payload)}"
            assert payload.get("shape") in self.SHAPES, f"{name}: {payload.get('shape')}"
            assert name in served
            if payload["shape"] != "refusal":
                answered.append(name)

        # Without this the sweep is satisfiable by refusing every call.
        refused = [n for n in self.MUST_ANSWER if n not in answered]
        assert refused == [], f"these refused against a live product: {refused}"

    @pytest.mark.ac("AC-32")
    def test_every_answer_states_a_confidence_and_qualifies_itself(self, live):
        org, key = live
        server = mcp_server.build(org)

        for name, arguments in self._calls(key, "TRI-11.2").items():
            payload = _payload(asyncio.run(server.call_tool(name, arguments)))
            if payload.get("shape") != "answer":
                continue
            assert payload["confidence"] in CONFIDENCES, name
            if payload["confidence"] != "high":
                assert payload["caveats"], f"{name} hedged without saying why"

    def test_every_statement_is_prose_a_human_can_read(self, live):
        """B1: "prose, no more than five sentences". Checked here rather than in the
        shape, because a runtime guard would turn a cosmetically long answer into a
        failed tool call."""
        org, key = live
        server = mcp_server.build(org)
        long_ones = []
        for name, arguments in self._calls(key, "TRI-11.2").items():
            payload = _payload(asyncio.run(server.call_tool(name, arguments)))
            if payload.get("shape") != "answer":
                continue
            if sentences(payload["statement"]) > 5:
                long_ones.append((name, sentences(payload["statement"])))
        assert long_ones == [], long_ones

    @pytest.mark.ac("AC-7")
    def test_no_answer_claims_resolution_it_never_attempted(self, live):
        """The trap step 8 found on findings, one line away from being repeated here: an
        answer with both `citation_links` and `unresolved` empty reads as "every citation
        resolved" when nothing was tried."""
        org, key = live
        server = mcp_server.build(org)
        for name in ("get_clause", "metric_history", "failing_cases"):
            payload = _payload(asyncio.run(server.call_tool(
                name, {"ref": "TRI-11.2"} if name == "get_clause"
                else {"clause_ref": "TRI-11.2"}
            )))
            assert payload.get("shape") == "answer", f"{name}: {payload}"
            assert payload["citations"], name
            assert payload["citation_links"] or payload["unresolved"], (
                f"{name} cited {len(payload['citations'])} records and resolved none of "
                f"them, reporting neither a link nor an unresolved ref"
            )


# -- a clean install -------------------------------------------------------------


@pytest.mark.ac("AC-20")
def test_a_clean_install_refuses_by_name_and_proposes_nothing():
    """AC-20: zero products onboarded answers every read tool with a refusal naming the
    missing product, and generates no proposals and no findings."""
    org = make_org("mcp-empty")
    server = mcp_server.build(org)

    for name, arguments in (
        ("list_products", {}),
        ("get_product", {"key": "anything"}),
        ("source_status", {"product": "anything"}),
        ("list_clauses", {"product": "anything"}),
        ("get_clause", {"ref": "X-1"}),
        ("failing_cases", {"clause_ref": "X-1"}),
        ("find_drift", {"product": "anything"}),
    ):
        payload = _payload(asyncio.run(server.call_tool(name, arguments)))
        assert payload["shape"] == "refusal", f"{name} did not refuse"
        assert payload["reason"].strip(), f"{name} refused without saying why"

    with org_session(org) as session:
        assert session.execute(select(Proposal)).scalars().all() == []


# -- the proof tool --------------------------------------------------------------


@pytest.mark.skipif(not REFERENCE.exists(), reason="reference products not present")
class TestTheProofTool:
    """AC-24: "answers 'which runs are the proof' for any clause in one call, with no log
    reading and no live call to the eval platform"."""

    @pytest.fixture
    def live(self):
        org = make_org("mcp-proof")
        with org_session(org) as session:
            product = tf.onboard(
                session, org, "triage", "TRI", tf.TRIAGE_METRICS,
                cases={"input_field": "message"},
            )
            yield org, session, product

    @pytest.mark.ac("AC-24")
    def test_one_call_names_the_runs_and_the_cases(self, live):
        org, session, _ = live
        answer = reads.failing_cases(session, "TRI-11.2")

        assert answer.detail["runs_missed"] == 7
        assert answer.detail["runs_total"] == 47
        assert answer.detail["failing_cases_state"] == "cited"
        cases = answer.detail["failing_cases"]
        assert cases and all(c["run_url"] for c in cases)
        assert {c["case_id"] for c in cases} == {"14"}

    @pytest.mark.ac("AC-24")
    def test_the_tool_and_the_finding_cite_the_same_cases(self, live):
        """Two paths to one fact. They share `failing_cases_for` precisely so they cannot
        drift apart and cite different evidence for the same claim."""
        from layer.findings import queries

        org, session, product = live
        tool = reads.failing_cases(session, "TRI-11.2")
        finding = next(
            f for f in queries.find_drift(session, product=product)
            if f.clause_ref == "TRI-11.2"
        )

        assert [c["ref"] for c in tool.detail["failing_cases"]] == [
            c["ref"] for c in finding.detail["failing_cases"]
        ]

    def test_a_window_narrows_the_runs_considered(self, live):
        org, session, _ = live
        narrow = reads.failing_cases(session, "TRI-11.2", window="1d")

        # The committed history is weeks old, so a one day window reaches no run at all
        # and the tool says so rather than reporting zero failures.
        assert isinstance(narrow, Refusal)
        assert "no run" in narrow.reason

    def test_an_unreadable_window_is_a_refusal_not_a_traceback(self, live):
        org, session, _ = live
        with pytest.raises(Unreadable):
            reads.metric_history(session, "TRI-11.2", window="30")


# -- the chain -------------------------------------------------------------------


@pytest.mark.skipif(not REFERENCE.exists(), reason="reference products not present")
class TestTraceChain:
    """US-5. The chain reachable in both directions, with absent links named."""

    @pytest.fixture
    def live(self):
        org = make_org("mcp-chain")
        with org_session(org) as session:
            product = tf.onboard(session, org, "triage", "TRI", tf.TRIAGE_METRICS)
            yield org, session, product

    def test_an_absent_link_is_named_rather_than_omitted(self, live):
        """US-5: "where a link in the expected chain is absent, the Layer shall name the
        gap rather than omitting it". Nothing binds the sources that carry these links
        until phase 6, so naming them is the required behaviour, not a shortfall."""
        org, session, _ = live
        answer = reads.trace_chain(session, "clause:TRI-11.2")

        assert answer.detail["gaps"], "no gap was named in a chain with no links at all"
        assert set(answer.detail["gaps"]) <= set(reads.EXPECTED_CHAIN)
        assert "missing" in " ".join(answer.caveats)
        assert answer.confidence != "high"

    def test_a_link_in_either_direction_is_walked(self, live):
        """Both directions from one ref, which is what distinguishes a chain from a
        lookup. Written by hand here because no source produces links yet."""
        org, session, product = live
        session.add_all([
            Link(org_id=product.org_id, product_id=product.id,
                 from_ref="requirement:R-1", to_ref="clause:TRI-11.2",
                 link_type="governs", created_by="test"),
            Link(org_id=product.org_id, product_id=product.id,
                 from_ref="clause:TRI-11.2", to_ref="metric:escalation_recall",
                 link_type="asserts", created_by="test"),
        ])
        session.flush()

        answer = reads.trace_chain(session, "clause:TRI-11.2")
        edges = {(e["from"], e["to"]) for e in answer.detail["links"]}

        assert ("requirement:R-1", "clause:TRI-11.2") in edges
        assert ("clause:TRI-11.2", "metric:escalation_recall") in edges
        assert "requirement:R-1" in answer.detail["nodes"]

    @pytest.mark.ac("AC-6")
    def test_a_six_link_chain_resolves_end_to_end(self, live):
        """AC-6, for the walk itself.

        The links are written by hand, because no source the Layer can bind yet produces
        a requirement, a decision or a ticket — those arrive in phase 6. So this proves
        the mechanism walks six hops in one call and keeps every link's type and source,
        and it does **not** prove that a real product's chain is complete. PROGRESS says
        which half is which rather than letting the marker imply both.
        """
        org, session, product = live
        # One link of each type `EXPECTED_CHAIN` declares, so "no gaps" means what it
        # says. The first version of this test used `decides`, which is a real link type
        # and not one of the six, and the gap it left was correctly reported.
        chain = [
            ("requirement:R-1", "clause:TRI-11.2", "governs"),
            ("decision:D-1", "clause:TRI-11.2", "sets"),
            ("ticket:T-1", "clause:TRI-11.2", "implements"),
            ("clause:TRI-11.2", "metric:escalation_recall", "asserts"),
            ("metric:escalation_recall", "file:products/triage/gate.py", "enforces"),
            ("metric:escalation_recall", "obs:1", "observes"),
        ]
        session.add_all([
            Link(org_id=product.org_id, product_id=product.id, from_ref=a, to_ref=b,
                 link_type=t, created_by="spec:test")
            for a, b, t in chain
        ])
        session.flush()

        answer = reads.trace_chain(session, "ticket:T-1")
        edges = {(e["from"], e["to"], e["link_type"]) for e in answer.detail["links"]}

        assert len(edges) == 6, edges
        assert set(chain) == edges
        assert answer.detail["gaps"] == [], answer.detail["gaps"]
        assert answer.confidence == "high"
        # Every node named, from either end of the chain.
        assert "obs:1" in answer.detail["nodes"]
        assert "ticket:T-1" in answer.detail["nodes"]
        assert all(e["source"] == "spec:test" for e in answer.detail["links"])

    def test_a_cycle_does_not_loop_forever(self, live):
        """The path is carried in the CTE and a ref already on it is not followed again.
        Without that, two records pointing at each other walk until the depth cap, and a
        longer cycle walks until something times out."""
        org, session, product = live
        session.add_all([
            Link(org_id=product.org_id, product_id=product.id,
                 from_ref="clause:TRI-11.2", to_ref="entity:A",
                 link_type="asserts", created_by="test"),
            Link(org_id=product.org_id, product_id=product.id,
                 from_ref="entity:A", to_ref="clause:TRI-11.2",
                 link_type="observes", created_by="test"),
        ])
        session.flush()

        answer = reads.trace_chain(session, "clause:TRI-11.2")
        assert answer.detail["depth_reached"] <= reads.MAX_CHAIN_DEPTH

    def test_a_ref_that_is_not_a_ref_is_refused(self, live):
        org, session, _ = live
        with pytest.raises(Unreadable):
            reads.trace_chain(session, "not a ref")


# -- the write tier --------------------------------------------------------------


@pytest.mark.skipif(not REFERENCE.exists(), reason="reference products not present")
class TestTheWriteTier:
    """What an agent may write, and every B4 check it cannot skip."""

    @pytest.fixture
    def live(self):
        org = make_org("mcp-writes")
        with org_session(org) as session:
            product = tf.onboard(
                session, org, "triage", "TRI", tf.TRIAGE_METRICS,
                cases={"input_field": "message"},
            )
            yield org, session, product

    def _propose(self, session, **kw):
        defaults = dict(
            target="clause:TRI-11.2", field="value", new_value="0.995",
            reason="the recorded history has never reached the stated bar",
            evidence=["clause:TRI-11.2"], actor=AGENT,
            if_rejected="the clause stands and the gap stays open",
        )
        return writes.propose_change(session, **{**defaults, **kw})

    def test_a_proposal_is_created_open_and_undecided(self, live):
        org, session, _ = live
        result = self._propose(session)

        assert result.as_dict()["shape"] == "proposal"
        assert result.state == "open"
        row = session.execute(select(Proposal)).scalars().one()
        assert (row.state, row.decided_by, row.decided_at) == ("open", None, None)
        assert row.proposed_by == AGENT

    @pytest.mark.ac("AC-9")
    def test_a_proposal_leaves_an_audit_event(self, live):
        from layer.db.models import AuditEvent

        org, session, _ = live
        self._propose(session)
        actions = session.execute(select(AuditEvent.action)).scalars().all()
        assert "proposal_created" in actions

    def test_evidence_that_does_not_resolve_is_refused_before_it_is_stored(self, live):
        """B3 rule 7: never cite a record it did not read. A proposal whose citation is
        broken costs a human the minute B4 budgets for it."""
        org, session, _ = live
        result = self._propose(session, evidence=["clause:NOPE-1"])

        assert isinstance(result, Refusal)
        assert "does not resolve" in result.reason
        assert session.execute(select(Proposal)).scalars().all() == []

    def test_a_proposal_with_no_evidence_is_refused(self, live):
        org, session, _ = live
        assert isinstance(self._propose(session, evidence=[]), Refusal)

    def test_a_proposal_that_does_not_say_what_happens_if_rejected_is_refused(self, live):
        """B4 item 5. Without it, rejecting is a deferral rather than a decision."""
        org, session, _ = live
        result = self._propose(session, if_rejected=None)

        assert isinstance(result, Refusal)
        assert "rejected" in result.reason

    def test_a_second_open_proposal_against_the_same_field_is_refused(self, live):
        """B4 item 4, enforced by a partial unique index rather than by a check here."""
        org, session, _ = live
        assert self._propose(session).as_dict()["shape"] == "proposal"
        second = self._propose(session, new_value="0.999")

        assert isinstance(second, Refusal)
        assert "already open" in second.reason

    def test_a_field_a_clause_does_not_have_is_refused(self, live):
        org, session, _ = live
        result = self._propose(session, field="vlaue")
        assert isinstance(result, Refusal)

    @pytest.mark.ac("AC-17")
    def test_nothing_is_proposed_for_a_product_with_no_confirmed_binding(self):
        """B3 rule 8 and AC-17, through the write tier rather than through the CLI."""
        org = make_org("mcp-notlive")
        with org_session(org) as session:
            product = state.register(
                session, org_id=org, key="bare", name="Bare", ref_prefix="B",
                actor=ACTOR,
            )
            result = writes.propose_binding(
                session, product_key=product.key, metric="m", clause_ref="B-1",
                reason="r", actor=AGENT, if_rejected="nothing changes",
            )
            assert isinstance(result, Refusal)
            assert session.execute(select(Proposal)).scalars().all() == []

    def test_proposing_a_binding_stops_at_a_proposal(self, live):
        """The tool B11 singles out. It may propose; it may not confirm, and there is no
        tool that would let it."""
        from layer.db.models import Binding

        org, session, product = live
        before = session.execute(select(Binding)).scalars().all()
        result = writes.propose_binding(
            session, product_key=product.key, metric="contract_validity",
            clause_ref="TRI-11.1", reason="this number answers that promise",
            actor=AGENT, if_rejected="the metric stays unpaired",
        )
        after = session.execute(select(Binding)).scalars().all()

        assert result.as_dict()["shape"] == "proposal"
        assert len(after) == len(before), "a proposal changed the bindings"

    def test_an_unknown_link_type_is_refused_rather_than_stored(self, live):
        org, session, _ = live
        result = writes.propose_link(
            session, from_ref="clause:TRI-11.2", to_ref="entity:A",
            link_type="causes", reason="r", evidence=["clause:TRI-11.2"],
            actor=AGENT, if_rejected="no link",
        )
        assert isinstance(result, Refusal)
        assert "not a link type" in result.reason

    @pytest.mark.ac("AC-25")
    def test_a_recorded_case_input_is_redacted_whatever_the_caller_sent(self, live):
        """The caller is an agent and may pass raw customer text. PRD B6 sets unredacted
        PII at zero tolerance, so the redactor runs on the way in rather than trusting
        anyone."""
        from layer.db.models import CaseResult, Observation

        org, session, product = live
        observation = session.execute(
            select(Observation).where(Observation.product_id == product.id).limit(1)
        ).scalars().one()

        result = writes.record_case_results(
            session, observation_id=observation.id,
            cases=[
                {"case_id": "agent-1", "outcome": "fail",
                 "input": "call me on 07700 900123 or a@b.com"},
                {"case_id": "agent-2", "outcome": "pass", "input": "should be dropped"},
            ],
            actor=AGENT,
        )
        rows = {
            r.case_id: r for r in session.execute(
                select(CaseResult).where(CaseResult.case_id.like("agent-%"))
            ).scalars()
        }

        assert result.as_dict()["shape"] == "answer"
        assert "07700 900123" not in (rows["agent-1"].input_redacted or "")
        assert "[phone]" in rows["agent-1"].input_redacted
        assert "[email]" in rows["agent-1"].input_redacted
        assert rows["agent-2"].input_redacted is None, "a passing case kept its input"

    def test_a_case_with_an_unknown_outcome_is_refused_and_named(self, live):
        from layer.db.models import Observation

        org, session, product = live
        observation = session.execute(
            select(Observation).where(Observation.product_id == product.id).limit(1)
        ).scalars().one()
        result = writes.record_case_results(
            session, observation_id=observation.id,
            cases=[{"case_id": "x", "outcome": "flaky"}], actor=AGENT,
        )
        assert isinstance(result, Refusal)

    def test_recording_an_observation_for_an_unbound_clause_is_refused(self, live):
        from datetime import UTC, datetime

        org, session, _ = live
        result = writes.record_observation(
            session, clause_ref="TRI-11.4", value=0.9, source_kind="production",
            measured_at=datetime(2026, 10, 1, tzinfo=UTC), actor=AGENT,
        )
        assert isinstance(result, Refusal)
        assert "no confirmed binding" in result.reason


# -- over a real pipe ------------------------------------------------------------


@pytest.mark.skipif(not REFERENCE.exists(), reason="reference products not present")
@pytest.mark.ac("AC-32")
def test_the_server_answers_over_a_real_stdio_pipe():
    """PRD A6: "fully usable over stdio from a terminal with no Dust at all".

    The one test that proves it rather than assuming it. Everything else in this file
    calls the server in-process, which would pass just as well if the transport were
    broken, the CLI entry point missing, or stdout polluted by a banner — that last one
    being the easy mistake, since stdout is the protocol's own channel.
    """
    import os
    import sys

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    org = make_org("mcp-stdio")
    with org_session(org) as session:
        product = tf.onboard(session, org, "triage", "TRI", tf.TRIAGE_METRICS)
        key = product.key

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "layer", "serve", "--org", str(org), "--as", AGENT],
        env={**os.environ},
        cwd=str(Path(__file__).resolve().parent.parent),
    )

    async def exercise():
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                listed = await client.list_tools()
                answer = await client.call_tool("find_drift", {"product": key})
                return [t.name for t in listed.tools], answer

    names, answer = asyncio.run(asyncio.wait_for(exercise(), timeout=120))

    assert "failing_cases" in names
    assert [n for n in mcp_server.HUMAN_ONLY if n in names] == []
    payload = _payload(answer)
    assert payload["shape"] == "findings"
    assert payload["count"] >= 1


def _payload(result):
    """The dict a tool returned, out of whatever the SDK wrapped it in."""
    for attribute in ("structured_content", "structuredContent"):
        value = getattr(result, attribute, None)
        if isinstance(value, dict):
            return value.get("result", value)
    if isinstance(result, tuple) and result:
        return _payload(result[0])
    if isinstance(result, dict):
        return result.get("result", result)
    content = getattr(result, "content", None)
    if content:
        import json

        return json.loads(content[0].text)
    raise AssertionError(f"cannot read a payload out of {type(result)}")
