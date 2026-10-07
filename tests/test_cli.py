"""The operator surface. AC-18, AC-20, B3 rules 8 and 9.

The CLI is the whole of phases 1 and 2's interface, so what it prints is the product. These
tests check two things the state machine's own tests cannot: that a refusal reaches the
operator as a refusal rather than a traceback, and that an empty install says what is
missing instead of printing nothing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from layer.cli import main

from conftest import make_org

FIXTURES = Path(__file__).parent / "fixtures"
ACTOR = "pm@example.invalid"


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    """A small product in its own repository, so the CLI reads something real."""
    root = tmp_path_factory.mktemp("cli-product")
    (root / "docs").mkdir()
    (root / "runs").mkdir()
    shutil.copy(FIXTURES / "alien_spec.md", root / "docs" / "service.md")
    for path in sorted((FIXTURES / "alien_runs").glob("*.json")):
        shutil.copy(path, root / "runs" / path.name)
    for args in (
        ["init", "-b", "main"], ["config", "user.email", "t@example.invalid"],
        ["config", "user.name", "T"], ["add", "-A"], ["commit", "-m", "product"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    return root


@pytest.fixture
def org() -> str:
    return str(make_org("cli"))


def run_cli(capsys, *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def eval_config(repo: Path) -> str:
    return json.dumps({
        "local_path": str(repo), "repo_url": "https://host.example/p",
        "globs": ["runs/*.json"],
        "readers": [{
            "glob": "runs/*.json", "run_id": "/header/ticket",
            "measured_at": "/header/when",
            "prompt_version": "/header/prompt", "corpus_sha": "/header/corpus",
            "code_rev": "/header/build",
            "metrics": [{
                "metric": "accepted_suggestions", "kind": "rate", "rows": "/checks",
                "where": {"not": {"field": "suggested", "op": "eq", "value": "cannot_tell"}},
            }],
        }],
    })


def spec_config(repo: Path) -> str:
    return json.dumps({
        "local_path": str(repo), "repo_url": "https://host.example/p",
        "path": "docs/service.md", "target_columns": ["bar"],
    })


def onboard(capsys, org: str, repo: Path) -> None:
    common = ["--org", org, "--as", ACTOR]
    run_cli(capsys, "product", "register", *common, "wf", "--ref-prefix", "WF")
    run_cli(capsys, "source", "bind", *common, "wf", "--role", "spec",
            "--kind", "repo", "--config", spec_config(repo))
    run_cli(capsys, "source", "bind", *common, "wf", "--role", "eval",
            "--kind", "repo", "--config", eval_config(repo))
    run_cli(capsys, "spec", *common, "wf")
    run_cli(capsys, "backfill", *common, "wf")


class TestInvocation:
    def test_no_arguments_prints_help_rather_than_failing_obscurely(self, capsys):
        code, out, _ = run_cli(capsys)
        assert code == 2
        assert "usage: layer" in out

    def test_the_steps_are_discoverable_from_help(self, capsys):
        """A reader should be able to see the seven steps without the specification.

        `--help` exits rather than returning, which is argparse's contract and not worth
        fighting; the output is what matters.
        """
        with pytest.raises(SystemExit):
            main(["--help"])
        out = capsys.readouterr().out
        for marker in ("step 1", "step 2", "step 3", "step 5: the gate", "step 6"):
            assert marker in out, f"{marker!r} is not discoverable from --help"


class TestEmptyInstall:
    @pytest.mark.ac("AC-20")
    def test_an_org_with_no_product_says_what_is_missing(self, capsys, org):
        """AC-20: a clean install answers with a refusal naming the missing product, and
        generates nothing. Printing an empty list would read as "all clear"."""
        code, out, _ = run_cli(capsys, "product", "list", "--org", org, "--as", ACTOR)
        assert code == 0
        assert "no product is onboarded" in out
        assert "until one is registered" in out

    @pytest.mark.ac("AC-20")
    def test_acting_on_an_unregistered_product_is_a_refusal_not_a_traceback(self, capsys, org):
        """B3 rule 9 reaching the operator in a readable form."""
        code, out, err = run_cli(capsys, "status", "--org", org, "--as", ACTOR, "ghost")
        assert code == 1
        assert err.startswith("refused:")
        assert "ghost" in err
        assert "Traceback" not in err


class TestRegistration:
    @pytest.mark.ac("AC-18")
    def test_a_pattern_is_recorded_as_a_hint_and_said_to_be_one(self, capsys, org):
        """AC-18. The output says outright that no defaults were applied, so nobody infers
        that naming a pattern configured something."""
        code, out, _ = run_cli(
            capsys, "product", "register", "--org", org, "--as", ACTOR,
            "wf", "--pattern", "something-nobody-listed",
        )
        assert code == 0
        assert "registering (step 1)" in out
        assert "recorded as a hint; no defaults applied" in out

    def test_an_absent_pattern_is_accepted_in_silence(self, capsys, org):
        code, out, _ = run_cli(
            capsys, "product", "register", "--org", org, "--as", ACTOR, "wf"
        )
        assert code == 0
        assert "hint" not in out


class TestTheSteps:
    def test_binding_both_roles_moves_the_product_forward(self, capsys, org, repo):
        common = ["--org", org, "--as", ACTOR]
        run_cli(capsys, "product", "register", *common, "wf", "--ref-prefix", "WF")

        _, out, _ = run_cli(capsys, "source", "bind", *common, "wf", "--role", "spec",
                            "--kind", "repo", "--config", spec_config(repo))
        assert "wf: registering" in out

        _, out, _ = run_cli(capsys, "source", "bind", *common, "wf", "--role", "eval",
                            "--kind", "repo", "--config", eval_config(repo))
        assert "wf: sources_bound" in out

    def test_a_freshness_window_is_stated_at_the_point_a_human_binds_the_source(
        self, capsys, org, repo
    ):
        """PRD B11: `bind_source(product, role, kind, config, freshness_window)`. This is
        the only place anybody says how often a source is expected to speak, so the
        window is typed here and the consequence of it is printed back."""
        common = ["--org", org, "--as", ACTOR]
        run_cli(capsys, "product", "register", *common, "wf", "--ref-prefix", "WF")
        _, out, _ = run_cli(capsys, "source", "bind", *common, "wf", "--role", "spec",
                            "--kind", "repo", "--config", spec_config(repo),
                            "--freshness", "30d")
        assert "freshness window 30d" in out
        assert "cannot_confirm" in out

    def test_a_source_with_no_stated_cadence_says_so_rather_than_looking_fresh(
        self, capsys, org, repo
    ):
        """Silence about a cadence is not a claim that the data is current. B5 item 10
        ranks a verdict shown as current from a stale reading above a refusal in cost,
        so the absence of a window has to be visible."""
        common = ["--org", org, "--as", ACTOR]
        run_cli(capsys, "product", "register", *common, "wf", "--ref-prefix", "WF")
        _, out, _ = run_cli(capsys, "source", "bind", *common, "wf", "--role", "spec",
                            "--kind", "repo", "--config", spec_config(repo))
        assert "no freshness window stated" in out

    def test_an_unreadable_freshness_window_is_refused_rather_than_guessed(
        self, capsys, org, repo
    ):
        """A bare `30` could be minutes or days. Wrong by a factor of 1,440, a window
        either degrades every verdict at once or never degrades one, and both read as
        the Layer being broken rather than as a policy somebody set."""
        common = ["--org", org, "--as", ACTOR]
        run_cli(capsys, "product", "register", *common, "wf", "--ref-prefix", "WF")
        code, _, err = run_cli(capsys, "source", "bind", *common, "wf", "--role", "spec",
                               "--kind", "repo", "--config", spec_config(repo),
                               "--freshness", "30")
        assert code == 1
        assert err.startswith("refused:")
        assert "cannot read '30' as a duration" in err

    def test_import_and_backfill_report_their_arithmetic(self, capsys, org, repo):
        common = ["--org", org, "--as", ACTOR]
        run_cli(capsys, "product", "register", *common, "wf", "--ref-prefix", "WF")
        run_cli(capsys, "source", "bind", *common, "wf", "--role", "spec",
                "--kind", "repo", "--config", spec_config(repo))
        run_cli(capsys, "source", "bind", *common, "wf", "--role", "eval",
                "--kind", "repo", "--config", eval_config(repo))

        _, out, _ = run_cli(capsys, "spec", *common, "wf")
        assert "new" in out and "candidate(s) from" in out

        _, out, _ = run_cli(capsys, "backfill", *common, "wf")
        assert "observations stored" in out
        assert "of 3 documents imported" in out

    def test_importing_without_a_source_says_which_one_is_missing(self, capsys, org):
        common = ["--org", org, "--as", ACTOR]
        run_cli(capsys, "product", "register", *common, "wf")
        code, _, err = run_cli(capsys, "spec", *common, "wf")
        assert code == 1
        assert err.startswith("not ready:")
        assert "no spec source" in err

    def test_a_repeated_backfill_reports_the_duplicates_it_refused(self, capsys, org, repo):
        """The operator-visible half of AC-2: the proof that history is not double-counted
        is a number on screen, not an absence of change."""
        onboard(capsys, org, repo)
        _, out, _ = run_cli(capsys, "backfill", "--org", org, "--as", ACTOR, "wf")
        assert "0 observations stored" in out
        assert "duplicates refused" in out


class TestTheGate:
    def test_candidates_are_listed_with_their_evidence(self, capsys, org, repo):
        onboard(capsys, org, repo)
        code, out, _ = run_cli(capsys, "bindings", "list", "--org", org, "--as", ACTOR, "wf")
        assert code == 0
        assert "candidate binding(s) awaiting a decision" in out
        assert "accepted_suggestions" in out
        assert "obs, latest" in out
        assert "matched by exact metric name" in out

    def test_both_kinds_of_gap_are_named_as_findings(self, capsys, org, repo):
        """A clause nothing measures and a measurement nothing promises are findings, not
        omissions, so the gate lists them where a human will see them."""
        onboard(capsys, org, repo)
        _, out, _ = run_cli(capsys, "bindings", "list", "--org", org, "--as", ACTOR, "wf")
        assert "clauses with a bar that nothing measures" in out

    @pytest.mark.ac("AC-17")
    def test_measuring_before_the_gate_is_refused_with_what_is_pending(self, capsys, org, repo):
        onboard(capsys, org, repo)
        code, _, err = run_cli(capsys, "measure", "--org", org, "--as", ACTOR, "wf")
        assert code == 1
        assert "awaiting a decision" in err

    def test_confirming_a_candidate_advances_the_product(self, capsys, org, repo):
        onboard(capsys, org, repo)
        code, out, _ = run_cli(
            capsys, "bindings", "confirm", "--org", org, "--as", ACTOR, "wf",
            "--metric", "accepted_suggestions", "--clause", "WF-4.1",
            "--note", "the table states this bar",
        )
        assert code == 0
        assert "confirmed: accepted_suggestions -> WF-4.1" in out
        assert f"by {ACTOR}" in out
        assert "wf: assertions_confirmed" in out

    def test_rejecting_is_recorded_and_does_not_open_the_gate(self, capsys, org, repo):
        """All rejected is a complete review and an empty assertion set, so the product has
        nothing to propose about and must not advance."""
        onboard(capsys, org, repo)
        code, out, _ = run_cli(
            capsys, "bindings", "reject", "--org", org, "--as", ACTOR, "wf",
            "--metric", "accepted_suggestions", "--clause", "WF-4.1",
            "--note", "measures something else",
        )
        assert code == 0
        assert "rejected: accepted_suggestions -> WF-4.1" in out
        assert "wf: sources_bound" in out

    def test_a_binding_to_an_unmeasured_metric_is_refused(self, capsys, org, repo):
        onboard(capsys, org, repo)
        code, _, err = run_cli(
            capsys, "bindings", "confirm", "--org", org, "--as", ACTOR, "wf",
            "--metric", "invented", "--clause", "WF-4.1",
        )
        assert code == 1
        assert "has been measured" in err


class TestMeasureAndStatus:
    def _live(self, capsys, org, repo) -> None:
        onboard(capsys, org, repo)
        run_cli(capsys, "bindings", "confirm", "--org", org, "--as", ACTOR, "wf",
                "--metric", "accepted_suggestions", "--clause", "WF-4.1")
        run_cli(capsys, "measure", "--org", org, "--as", ACTOR, "wf")

    def test_measure_reports_every_verdict_it_assigned(self, capsys, org, repo):
        onboard(capsys, org, repo)
        run_cli(capsys, "bindings", "confirm", "--org", org, "--as", ACTOR, "wf",
                "--metric", "accepted_suggestions", "--clause", "WF-4.1")
        code, out, _ = run_cli(capsys, "measure", "--org", org, "--as", ACTOR, "wf")
        assert code == 0
        assert "wf: live" in out
        # Every clause carries one, including the ones that state nothing measurable.
        assert "not_applicable" in out

    def test_status_describes_the_product_without_the_database(self, capsys, org, repo):
        self._live(capsys, org, repo)
        code, out, _ = run_cli(capsys, "status", "--org", org, "--as", ACTOR, "wf")
        assert code == 0
        for line in ("wf: live (step 6)", "sources", "clauses", "observations",
                     "bindings", "verdicts"):
            assert line in out
        assert "1 confirmed" in out
        assert "0 awaiting a decision" in out

    def test_product_list_shows_the_step_each_product_reached(self, capsys, org, repo):
        self._live(capsys, org, repo)
        code, out, _ = run_cli(capsys, "product", "list", "--org", org, "--as", ACTOR)
        assert code == 0
        assert "wf" in out and "live" in out and "step 6" in out


class TestAttribution:
    def test_every_command_requires_an_actor(self, capsys, org):
        """`--as` is recorded on every write. It is attribution rather than authentication —
        trivially spoofable from a CLI used by one operator — and phase 4 replaces it with a
        real session, but a write with nobody's name on it is not something to allow even
        now."""
        with pytest.raises(SystemExit):
            main(["product", "register", "--org", org, "wf"])

    def test_every_command_requires_a_tenant(self, capsys):
        """There is no session to infer the org from, and row level security needs it
        named, so it cannot be defaulted."""
        with pytest.raises(SystemExit):
            main(["product", "register", "--as", ACTOR, "wf"])


# -- the actor slice, phase 5 step 14 --------------------------------------------


def test_actor_add_prints_the_role_it_recorded(org, capsys):
    assert main(["actor", "add", "pm@example.com", "--role", "pm",
                 "--org", org, "--as", ACTOR]) == 0
    assert "pm@example.com: pm" in capsys.readouterr().out


def test_actor_list_says_what_each_role_decides(org, capsys):
    """Printed rather than documented. The operator deciding who to register should not
    have to read `models.ROLE_DECIDES` to find out what they are granting."""
    main(["actor", "add", "a@example.com", "--role", "engineer",
          "--org", org, "--as", ACTOR])
    main(["actor", "add", "b@example.com", "--role", "agent",
          "--org", org, "--as", ACTOR])
    main(["actor", "list", "--org", org, "--as", ACTOR])
    out = capsys.readouterr().out
    assert "ci_change" in out and "eval_case" in out
    assert "decides: nothing" in out


def test_actor_list_on_an_empty_org_says_so_rather_than_printing_nothing(org, capsys):
    """AC-20's habit, applied to this table: an empty state names what is missing."""
    main(["actor", "list", "--org", org, "--as", ACTOR])
    assert "cannot be attributed" in capsys.readouterr().out


def test_an_unknown_role_is_rejected_by_the_parser_not_the_database(org, capsys):
    """`choices=` on the argument, so the operator gets the list back immediately
    rather than a constraint violation from Postgres."""
    with pytest.raises(SystemExit):
        main(["actor", "add", "x@example.com", "--role", "admin",
              "--org", org, "--as", ACTOR])
    assert "invalid choice" in capsys.readouterr().err


def test_registering_an_actor_is_itself_audited(org):
    """AC-9. Who may decide is a change worth a record of its own: granting somebody
    the pm role is granting them every future decision."""
    from sqlalchemy import select

    from layer.core import audit
    from layer.core.db import org_session
    from layer.db.models import AuditEvent

    main(["actor", "add", "pm@example.com", "--role", "pm", "--org", org, "--as", ACTOR])
    with org_session(org) as session:
        rows = session.execute(
            select(AuditEvent).where(AuditEvent.action == audit.ACTOR_REGISTERED)
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].actor == ACTOR
    assert rows[0].subject == "actor:pm@example.com"
    assert rows[0].detail["role"] == "pm"


# -- the decide path, phase 5 step 15 --------------------------------------------


class TestProposals:
    """The operator's half of US-10. What the terminal prints *is* the review surface in
    this phase, so the band and the unscored state are tested as output, not as fields."""

    def _with_a_proposal(self, capsys, org, repo) -> str:
        from layer.answers import writes
        from layer.core import actors
        from layer.core.db import org_session

        onboard(capsys, org, repo)
        run_cli(capsys, "bindings", "confirm", "--org", org, "--as", ACTOR, "wf",
                "--metric", "accepted_suggestions", "--clause", "WF-4.1")
        run_cli(capsys, "measure", "--org", org, "--as", ACTOR, "wf")
        run_cli(capsys, "actor", "add", "pm@example.com", "--role", "pm",
                "--org", org, "--as", ACTOR)
        with org_session(org) as session:
            result = writes.propose_change(
                session, target="clause:WF-4.1", field="value", new_value="0.95",
                reason="the recorded runs sit below this bar across the sequence",
                evidence=["clause:WF-4.1"], actor="generator",
                if_rejected="the clause stands and the gap stays open",
            )
            assert result.shape == "proposal", getattr(result, "reason", result)
            return result.proposal_id

    def test_the_listing_shows_what_a_reviewer_decides_on(self, capsys, org, repo):
        self._with_a_proposal(capsys, org, repo)
        code, out, _ = run_cli(capsys, "proposals", "list", "--org", org, "--as", ACTOR)
        assert code == 0
        assert "clause:WF-4.1.value" in out
        assert "->  0.95" in out
        assert "if rejected:" in out
        assert "evidence: clause:WF-4.1" in out
        # Null is unscored, never 0.00. Every proposal is unscored until the critic exists.
        assert "confidence: unscored" in out

    def test_an_empty_queue_says_so(self, capsys, org, repo):
        onboard(capsys, org, repo)
        code, out, _ = run_cli(capsys, "proposals", "list", "--org", org, "--as", ACTOR)
        assert code == 0
        assert "no open proposals" in out

    def test_accepting_prints_the_version_it_produced(self, capsys, org, repo):
        proposal = self._with_a_proposal(capsys, org, repo)
        code, out, _ = run_cli(capsys, "proposals", "accept", "--org", org,
                               "--as", "pm@example.com", proposal)
        assert code == 0
        assert "accepted: clause:WF-4.1.value" in out
        assert "WF-4.1 is now at version 2" in out

    def test_rejecting_without_a_reason_is_refused_by_the_parser(self, capsys, org, repo):
        proposal = self._with_a_proposal(capsys, org, repo)
        with pytest.raises(SystemExit):
            run_cli(capsys, "proposals", "reject", "--org", org,
                    "--as", "pm@example.com", proposal)

    def test_an_unregistered_decider_reaches_the_operator_as_a_refusal(
        self, capsys, org, repo
    ):
        """Not a traceback. `--as` is attribution everywhere else and has to resolve
        here, and the message names the command that fixes it."""
        proposal = self._with_a_proposal(capsys, org, repo)
        code, _, err = run_cli(capsys, "proposals", "accept", "--org", org,
                               "--as", "nobody@example.com", proposal)
        assert code == 1
        assert "refused:" in err
        assert "layer actor add" in err

    def test_deciding_twice_tells_the_second_person_who_was_first(self, capsys, org, repo):
        proposal = self._with_a_proposal(capsys, org, repo)
        run_cli(capsys, "proposals", "accept", "--org", org,
                "--as", "pm@example.com", proposal)
        code, _, err = run_cli(capsys, "proposals", "reject", "--org", org,
                               "--as", "pm@example.com", proposal, "--reason", "changed my mind")
        assert code == 1
        assert "already accepted by pm@example.com" in err

    def test_the_acceptance_rate_is_printed_with_its_band(self, capsys, org, repo):
        """B6's core health metric. Printed with the band because a bare percentage
        invites the reader to treat higher as better, and above 85% is a failure."""
        proposal = self._with_a_proposal(capsys, org, repo)
        code, out, _ = run_cli(capsys, "proposals", "acceptance", "--org", org,
                               "--as", ACTOR)
        assert code == 0
        assert "unmeasured" in out
        assert "band 50% to 85%" in out

        run_cli(capsys, "proposals", "accept", "--org", org,
                "--as", "pm@example.com", proposal)
        code, out, _ = run_cli(capsys, "proposals", "acceptance", "--org", org,
                               "--as", ACTOR)
        assert "acceptance: 100%" in out
        # One decision is arithmetic, not a measurement. AC-16 asks for twenty.
        assert "[provisional]" in out
        assert "AC-16" in out
