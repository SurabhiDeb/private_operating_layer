"""The edge cases that had no test. PRD A8, AC-12.

AC-12 requires every case EC-1 to EC-12 to have "a defined behaviour and a test". Four
already had one beside the behaviour they belong to — EC-2 and EC-4 in the adapters, EC-6
on the binding gate, EC-9 in the schema. This file holds the ones that did not.

**Three of them describe behaviour that belongs to a later phase**, and those are declared
in the matrix with their phase rather than given a test that proves something adjacent.
What is here is either implemented or implementable now, and two of the tests exist
because writing them found the behaviour missing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from layer.core.db import org_session
from layer.core.errors import Refusal
from layer.db.models import Clause
from layer.findings import queries
from layer.onboarding import bindings as binding_gate
from layer.onboarding import run, state
from layer.onboarding.state import StepNotReady

from conftest import make_org
import test_findings as tf

REFERENCE = Path("/Users/surabhideb/Desktop/chatbot-lab")
ACTOR = "pm@example.invalid"


# -- EC-1 -------------------------------------------------------------------------


@pytest.mark.edge_case("EC-1")
def test_a_product_with_no_spec_is_told_what_is_missing_not_shown_an_empty_state():
    """EC-1: "First run, no spec exists anywhere. Offer US-9's derivation. Never present
    an empty state as nothing to do."

    **The second half is tested here; the first is phase 6's.** Deriving clauses from an
    eval suite and the pattern defaults is US-9's second bullet and is not built. What is
    built, and what EC-1's real failure mode is, is the refusal: a product with no spec
    source names the source it needs instead of onboarding to a clean, empty, reassuring
    dashboard.
    """
    org = make_org("ec1")
    with org_session(org) as session:
        product = state.register(
            session, org_id=org, key="nospec", name="No Spec", ref_prefix="NS",
            actor=ACTOR,
        )
        with pytest.raises(StepNotReady) as raised:
            run.import_spec(session, product=product, actor=ACTOR)

        message = str(raised.value)
        assert "spec source" in message
        assert "nothing to reason about" in message
        # And the product has not quietly advanced past the step it cannot do.
        assert product.status == "registering"


# -- EC-3 -------------------------------------------------------------------------


@pytest.mark.edge_case("EC-3")
def test_a_cold_start_reports_one_measurement_and_never_a_trend(tmp_path):
    """EC-3: "Cold start, one eval run and no history. Every clause stays `provisional`.
    No drift claims from a single point."

    **The second sentence is implemented here. The first contradicts B2 and B2 wins.**
    B2 defines `measured` as "a baseline run exists", so a clause with one run behind it
    is `measured` by the specification's own definition, and holding it at `provisional`
    would make `state` mean something different in this one case. Recorded in PROGRESS as
    a criterion to amend, beside AC-25 and AC-26.

    The second sentence was a real gap. A single breaching run produced "missed in 1 of 1
    runs, worst 80%", which is a sentence shaped like a trend at the exact moment a reader
    is least able to tell. It now says there is no history.
    """
    root = tmp_path / "cold"
    (root / "runs").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    one_run = REFERENCE / "products/triage/runs/20260915-133223Z-v2.json"
    if not one_run.exists():
        pytest.skip("reference products not present")
    (root / "runs" / one_run.name).write_text(one_run.read_text())
    (root / "docs" / "spec.md").write_text(
        "# Service\n\n## Targets\n\n| Signal | Bar |\n|---|---|\n"
        "| Escalation recall | 99% |\n"
    )
    for args in (
        ["init", "-b", "main"], ["config", "user.email", "t@example.invalid"],
        ["config", "user.name", "T"], ["add", "-A"], ["commit", "-m", "cold"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    org = make_org("ec3")
    common = {"local_path": str(root), "repo_url": "https://host.example/r"}
    with org_session(org) as session:
        product = state.register(
            session, org_id=org, key="cold", name="Cold", ref_prefix="CO", actor=ACTOR
        )
        state.bind_source(
            session, product=product, role="spec", kind="repo", actor=ACTOR,
            config={**common, "path": "docs/spec.md", "target_columns": ["bar"]},
        )
        state.bind_source(
            session, product=product, role="eval", kind="repo", actor=ACTOR,
            config={**common, "globs": ["runs/*.json"], "readers": [{
                "glob": "runs/*.json", "measured_at": "/meta/timestamp_utc",
                "prompt_version": "/meta/prompt_sha",
                "metrics": [{"metric": "escalation_recall", "kind": "recall",
                             "actual": "escalate", "expected": "labelled_escalate",
                             "coerce": {"labelled_escalate": "bool"}}],
            }]},
        )
        run.import_spec(session, product=product, actor=ACTOR)
        run.backfill(session, product=product, actor=ACTOR)
        for candidate in binding_gate.propose(session, product=product).pending:
            binding_gate.decide(
                session, product=product, metric=candidate.metric,
                clause_ref=candidate.clause_ref, decision="confirmed", by=ACTOR,
            )
        state.refresh(session, product=product, actor=ACTOR)
        state.measure(session, product=product, actor=ACTOR)

        drift = list(queries.find_drift(session, product=product))
        assert len(drift) == 1
        finding = drift[0]

        assert finding.detail["runs_total"] == 1
        assert finding.detail["single_point"] is True
        assert "only run on record" in finding.summary
        assert "not a trend" in finding.summary
        assert "of 1 runs" not in finding.summary, (
            "the summary still reads like a sequence"
        )


# -- EC-5 is phase 5; EC-7 ---------------------------------------------------------


@pytest.mark.edge_case("EC-7")
@pytest.mark.skipif(not REFERENCE.exists(), reason="reference products not present")
def test_an_unmeasurable_clause_is_held_as_written_and_counted_in_nothing():
    """EC-7: "A clause that cannot be measured, for example an ambiguity policy. Held as
    `kind: rule`. Never counted in drift or coverage metrics."

    The same condition as H6, asserted from the other end: not that the verdict is right,
    but that the clause appears in neither query. A policy about ambiguity becoming a
    failing number is the failure to avoid.
    """
    org = make_org("ec7")
    with org_session(org) as session:
        product = tf.onboard(session, org, "triage", "TRI", tf.TRIAGE_METRICS)
        unmeasurable = session.execute(
            select(Clause.ref).where(
                Clause.product_id == product.id, Clause.status == "active",
                Clause.kind.in_(("rule", "contract", "non_goal", "hard_case")),
            )
        ).scalars().all()

        assert unmeasurable, "the fixture carries no unmeasurable clause"
        drifting = {f.clause_ref for f in queries.find_drift(session, product=product)}
        uncovered = {
            f.clause_ref for f in queries.find_uncovered(session, product=product)
        }
        assert drifting.isdisjoint(unmeasurable)
        assert uncovered.isdisjoint(unmeasurable)


# -- EC-8 -------------------------------------------------------------------------


@pytest.mark.edge_case("EC-8")
@pytest.mark.skipif(not REFERENCE.exists(), reason="reference products not present")
def test_evidence_past_retention_is_never_displayed_as_valid():
    """EC-8: "Retention deleted the evidence behind an open proposal. Mark it
    evidence-expired. Never display an unresolvable citation as valid."

    **The second sentence is tested here; the flag is phase 5's.** Marking a proposal
    `evidence-expired` needs the proposal lifecycle, which phase 5 builds. What exists is
    the rule the sentence turns on, and it is the part that would be a security defect if
    missing: a citation that cannot be resolved is displayed as unresolved, and a trace
    past its source's retention is never handed over as a live link.
    """
    org = make_org("ec8")
    with org_session(org) as session:
        product = tf.onboard(
            session, org, "policydesk", "PD", tf.POLICYDESK_METRICS,
            cases={"input_field": "question"},
            pairs={"critical_pass_rate": "PD-8.8"},
            retention="1d",
        )
        drift = queries.find_drift(session, product=product)
        cases = [c for f in drift for c in f.detail["failing_cases"]]

        assert cases
        assert all(c["trace_state"] == "past_retention" for c in cases)
        assert all(c["trace_url"] is None for c in cases)
        # The citation still resolves — to the run that holds the case, never to the
        # dead trace.
        links = [
            link for f in drift for link in f.evidence_links
            if link["id"].startswith("case:")
        ]
        assert links and not any("/traces/" in link["url"] for link in links)
        assert all(f.unresolved == [] for f in drift)


# -- EC-11 ------------------------------------------------------------------------


@pytest.mark.edge_case("EC-11")
@pytest.mark.skipif(not REFERENCE.exists(), reason="reference products not present")
def test_a_conflict_between_two_sources_is_reported_rather_than_resolved():
    """EC-11: "The spec and the ticket state different thresholds. Report the conflict.
    Do not pick a winner."

    **No ticket source can be bound, so the conflict this names cannot arise yet.** What
    is testable is the discipline it rests on, and it is the same discipline: where the
    Layer has two readings and no basis for choosing, it refuses rather than picking. The
    binding gate does exactly that for a unit mismatch, and `blocked_tickets` refuses by
    naming the source it does not have rather than reporting no conflicts.
    """
    org = make_org("ec11")
    with org_session(org) as session:
        product = tf.onboard(session, org, "triage", "TRI", tf.TRIAGE_METRICS)
        from layer.answers import reads

        result = reads.blocked_tickets(session, product.key)

        assert isinstance(result, Refusal)
        assert "ticket source" in result.reason
        assert "would be a claim about tickets the Layer has never read" in result.reason
