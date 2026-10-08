"""What the schema itself refuses. AC-2, AC-9, AC-13, AC-26, AC-27, EC-9, H2, H5, H16, P5, P6.

These are deliberately database tests rather than application tests. Each one asserts
a rule the Layer must not be able to break even by mistake, and a rule enforced only
in Python is a rule that holds until someone writes a second code path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DataError, IntegrityError, InternalError, ProgrammingError

from layer.core.db import org_session
from layer.db.models import (
    AuditEvent,
    CaseResult,
    Clause,
    EnforcementFact,
    Observation,
    Proposal,
    Source,
)

from conftest import make_org, make_product

NOW = datetime(2026, 9, 15, 13, 32, 23, tzinfo=UTC)


@pytest.fixture
def tenant() -> tuple[uuid.UUID, uuid.UUID]:
    org = make_org("schema-tenant")
    product = make_product(org, "subject", name="Subject")
    return org, product


def _obs(org, product, **kw) -> Observation:
    defaults = dict(
        org_id=org, product_id=product, clause_ref="X-1.1", metric="accuracy",
        value=0.9, source_kind="eval", measured_at=NOW,
        run_url="https://runs.example/r1",
    )
    return Observation(**{**defaults, **kw})


def _clause(org, product, **kw) -> Clause:
    defaults = dict(
        org_id=org, product_id=product, ref="X-1.1", statement="Accuracy at least 90%.",
        metric="accuracy", comparator=">=", value=0.9, unit="ratio",
    )
    return Clause(**{**defaults, **kw})


class TestObservationIdempotency:
    """Audit item P5 and AC-2: a repeated pull must not double-count history."""

    @pytest.mark.ac("AC-2")
    def test_the_same_measurement_twice_is_refused(self, tenant):
        org, product = tenant
        with org_session(org) as session:
            session.add(_obs(org, product))
        with pytest.raises(IntegrityError):
            with org_session(org) as session:
                session.add(_obs(org, product))

    @pytest.mark.ac("AC-2")
    def test_idempotency_holds_when_the_clause_ref_is_unknown(self, tenant):
        """The case B2's four-column constraint misses.

        In Postgres a NULL is distinct from every other NULL, so a plain unique
        constraint never fires for a metric no clause mentions — which is exactly the
        row a repeated pull duplicates, and exactly the row H16 is about.
        """
        org, product = tenant
        with org_session(org) as session:
            session.add(_obs(org, product, clause_ref=None, run_url=None))
        with pytest.raises(IntegrityError):
            with org_session(org) as session:
                session.add(_obs(org, product, clause_ref=None, run_url=None))

    @pytest.mark.ac("AC-2")
    def test_two_products_may_each_have_an_unbound_metric_of_the_same_name(self, tenant):
        """Why `product_id` is in the key. Without it these two collide."""
        org, product = tenant
        other = make_product(org, "other", name="Other")
        with org_session(org) as session:
            session.add(_obs(org, product, clause_ref=None, run_url=None))
            session.add(_obs(org, other, clause_ref=None, run_url=None))
        with org_session(org) as session:
            assert session.execute(select(func.count()).select_from(Observation)).scalar_one() == 2

    @pytest.mark.hard_case("H5")
    def test_one_metric_may_serve_two_clauses(self, tenant):
        """H5: both links exist and drift surfaces against both, so the same run
        legitimately produces one observation per clause."""
        org, product = tenant
        with org_session(org) as session:
            session.add(_obs(org, product, clause_ref="X-1.1"))
            session.add(_obs(org, product, clause_ref="X-2.4"))
        with org_session(org) as session:
            assert session.execute(select(func.count()).select_from(Observation)).scalar_one() == 2

    def test_a_sample_count_must_be_coherent(self, tenant):
        """`passed` and `total` feed a Wilson interval. 8 of 5 would produce a
        confidence bound for a sample that cannot exist."""
        org, product = tenant
        with pytest.raises(IntegrityError):
            with org_session(org) as session:
                session.add(_obs(org, product, passed=8, total=5))


class TestClause:
    @pytest.mark.ac("AC-13")
    def test_state_and_verdict_are_independently_settable(self, tenant):
        """AC-13. A ratified clause can still be unconfirmable, and a met clause can
        still be provisional — passing a bar nobody justified (H15)."""
        org, product = tenant
        with org_session(org) as session:
            session.add(_clause(org, product, ref="A-1", state="ratified",
                                verdict="cannot_confirm"))
            session.add(_clause(org, product, ref="A-2", state="provisional",
                                verdict="met"))
        with org_session(org) as session:
            got = dict(session.execute(select(Clause.ref, Clause.state)).all())
            verdicts = dict(session.execute(select(Clause.ref, Clause.verdict)).all())
        assert got == {"A-1": "ratified", "A-2": "provisional"}
        assert verdicts == {"A-1": "cannot_confirm", "A-2": "met"}

    def test_a_verdict_outside_the_vocabulary_is_refused(self, tenant):
        org, product = tenant
        with pytest.raises((IntegrityError, DataError)):
            with org_session(org) as session:
                session.add(_clause(org, product, verdict="probably_fine"))

    def test_a_band_must_have_both_ends(self, tenant):
        """A one-ended `between` is a parse that half failed, and it would silently
        never match anything."""
        org, product = tenant
        with pytest.raises(IntegrityError):
            with org_session(org) as session:
                session.add(_clause(org, product, comparator="between", value=0.05,
                                    value_high=None))

    def test_a_band_with_both_ends_is_accepted(self, tenant):
        """`3% to 8%` is a real target in a real spec, and PRD B2's clause shape
        cannot hold it."""
        org, product = tenant
        with org_session(org) as session:
            session.add(_clause(org, product, comparator="between", value=0.03,
                                value_high=0.08, direction="within_band"))
        with org_session(org) as session:
            row = session.execute(select(Clause.value, Clause.value_high)).one()
        assert row == (0.03, 0.08)

    @pytest.mark.hard_case("H3")
    def test_versions_of_one_ref_coexist(self, tenant):
        """H3: rewording increments the version and keeps the ref, so both rows exist
        and nothing that pointed at the ref breaks."""
        org, product = tenant
        with org_session(org) as session:
            session.add(_clause(org, product, version=1, status="superseded",
                                statement="Accuracy at least 90%."))
            session.add(_clause(org, product, version=2, status="active",
                                statement="Accuracy must be 90% or better."))
        with org_session(org) as session:
            active = session.execute(
                select(Clause.version).where(Clause.status == "active")
            ).scalar_one()
        assert active == 2

    def test_the_same_ref_and_version_cannot_be_stored_twice(self, tenant):
        org, product = tenant
        with pytest.raises(IntegrityError):
            with org_session(org) as session:
                session.add(_clause(org, product, version=1))
                session.add(_clause(org, product, version=1))


class TestProposalApprovalRecord:
    """PRD B5 item 1 and AC-9: a write with no approval record is a release blocker,
    so the schema must make the unapproved state unstorable rather than merely
    discouraged."""

    @pytest.mark.ac("AC-9")
    def test_a_decided_proposal_without_a_decider_is_refused(self, tenant):
        org, product = tenant
        with pytest.raises(IntegrityError):
            with org_session(org) as session:
                session.add(Proposal(
                    org_id=org, product_id=product, kind="clause_change",
                    target="clause:X-1.1", field="value", reason="r",
                    proposed_by="agent:layer", state="accepted",
                ))

    @pytest.mark.ac("AC-9")
    def test_an_open_proposal_carrying_a_decision_is_refused(self, tenant):
        org, product = tenant
        with pytest.raises(IntegrityError):
            with org_session(org) as session:
                session.add(Proposal(
                    org_id=org, product_id=product, kind="clause_change",
                    target="clause:X-1.1", field="value", reason="r",
                    proposed_by="agent:layer", state="open",
                    decided_by="someone@example.invalid", decided_at=NOW,
                ))

    def test_a_properly_decided_proposal_is_accepted(self, tenant):
        org, product = tenant
        with org_session(org) as session:
            session.add(Proposal(
                org_id=org, product_id=product, kind="clause_change",
                target="clause:X-1.1", field="value", reason="r",
                proposed_by="agent:layer", state="accepted",
                decided_by="pm@example.invalid", decided_at=NOW,
            ))
        with org_session(org) as session:
            assert session.execute(select(func.count()).select_from(Proposal)).scalar_one() == 1

    def test_only_one_proposal_may_be_open_against_a_target_and_field(self, tenant):
        """B4 item 4. Two open proposals on one field make the second undecidable:
        accepting both would apply conflicting values."""
        org, product = tenant

        def proposal(**kw):
            return Proposal(
                org_id=org, product_id=product, kind="clause_change",
                target="clause:X-1.1", field="value", reason="r",
                proposed_by="agent:layer", **kw,
            )

        with org_session(org) as session:
            session.add(proposal())
        with pytest.raises(IntegrityError):
            with org_session(org) as session:
                session.add(proposal())

    def test_a_decided_proposal_does_not_block_a_new_one(self, tenant):
        """The constraint is partial, on open rows only — otherwise a field could be
        changed once and never again."""
        org, product = tenant
        with org_session(org) as session:
            session.add(Proposal(
                org_id=org, product_id=product, kind="clause_change",
                target="clause:X-1.1", field="value", reason="first",
                proposed_by="agent:layer", state="rejected",
                decided_by="pm@example.invalid", decided_at=NOW,
                decision_note="not now",
            ))
        with org_session(org) as session:
            session.add(Proposal(
                org_id=org, product_id=product, kind="clause_change",
                target="clause:X-1.1", field="value", reason="second",
                proposed_by="agent:layer",
            ))
        with org_session(org) as session:
            assert session.execute(select(func.count()).select_from(Proposal)).scalar_one() == 2


class TestAuditIsAppendOnly:
    """Audit item P6: `approved_by` on a row is not an audit trail. The log is what
    makes the tool defensible, so it is enforced by the database."""

    def _event(self, org: uuid.UUID) -> AuditEvent:
        return AuditEvent(org_id=org, actor="agent:layer", action="proposal_created",
                          subject="proposal:1", detail={"field": "value"})

    @pytest.mark.ac("AC-9")
    def test_an_event_can_be_written(self, tenant):
        org, _ = tenant
        with org_session(org) as session:
            session.add(self._event(org))
        with org_session(org) as session:
            assert session.execute(select(func.count()).select_from(AuditEvent)).scalar_one() == 1

    @pytest.mark.ac("AC-9")
    def test_an_event_cannot_be_updated(self, tenant):
        org, _ = tenant
        with org_session(org) as session:
            session.add(self._event(org))
        with pytest.raises((InternalError, ProgrammingError)) as caught:
            with org_session(org) as session:
                session.execute(text("UPDATE audit_event SET actor = 'someone else'"))
        assert "append-only" in str(caught.value)

    @pytest.mark.ac("AC-9")
    def test_an_event_cannot_be_deleted_without_an_erasure(self, tenant):
        org, _ = tenant
        with org_session(org) as session:
            session.add(self._event(org))
        with pytest.raises((InternalError, ProgrammingError)) as caught:
            with org_session(org) as session:
                session.execute(text("DELETE FROM audit_event"))
        assert "append-only" in str(caught.value)

    @pytest.mark.edge_case("EC-9")
    def test_an_explicit_erasure_may_delete(self, tenant):
        """EC-9 and SEC-8: erasure must be reconciled with append-only history. An
        absolute refusal would make the right to erasure unimplementable, so the hatch
        exists, has to be asked for by name, and is transaction-local."""
        org, _ = tenant
        with org_session(org) as session:
            session.add(self._event(org))
        with org_session(org) as session:
            session.execute(text("SELECT set_config('app.erasure', 'on', true)"))
            session.execute(text("DELETE FROM audit_event"))
        with org_session(org) as session:
            assert session.execute(select(func.count()).select_from(AuditEvent)).scalar_one() == 0

    @pytest.mark.edge_case("EC-9")
    def test_the_erasure_flag_does_not_leak_into_the_next_transaction(self, tenant):
        """It is set transaction-locally, so a later session must not inherit it from a
        pooled connection. This is the same reuse that broke the isolation predicate."""
        org, _ = tenant
        with org_session(org) as session:
            session.execute(text("SELECT set_config('app.erasure', 'on', true)"))
        with org_session(org) as session:
            session.add(self._event(org))
        with pytest.raises((InternalError, ProgrammingError)):
            with org_session(org) as session:
                session.execute(text("DELETE FROM audit_event"))


class TestEnforcementFact:
    @pytest.mark.ac("AC-15")
    @pytest.mark.hard_case("H14")
    def test_a_narrowed_scope_is_recordable_alongside_enforced(self, tenant):
        """AC-15 and H14. The gate checks the right metric at the right threshold and
        still misses a mid-sequence breach, because it reads one run. A boolean
        `enforced` cannot say that, which is why `scope` exists."""
        org, product = tenant
        with org_session(org) as session:
            session.add(EnforcementFact(
                org_id=org, product_id=product, metric="team_accuracy",
                enforced=True, threshold=0.85, comparator=">=",
                scope="latest_only", file="products/x/gate.py", line=25,
            ))
        with org_session(org) as session:
            row = session.execute(
                select(EnforcementFact.enforced, EnforcementFact.scope)
            ).one()
        assert row == (True, "latest_only")

    def test_an_unknown_scope_is_refused(self, tenant):
        org, product = tenant
        with pytest.raises((IntegrityError, DataError)):
            with org_session(org) as session:
                session.add(EnforcementFact(
                    org_id=org, product_id=product, metric="m", enforced=True,
                    scope="sort_of", file="f",
                ))




class TestCaseResultIsTheProof:
    """PRD AC-26 and AC-27, and the evidence spine's own rules.

    An aggregate cannot be evidence, so these rows are what a drift finding cites. The
    schema has to refuse the two ways they could stop being proof: text stored for a
    case nobody will ask about, and a row that can be edited after it was cited.
    """

    def _case(self, org, observation_id, **kw) -> CaseResult:
        defaults = dict(
            org_id=org, observation_id=observation_id, case_id="14",
            measured_at=NOW, outcome="fail", input_redacted="[redacted]",
        )
        return CaseResult(**{**defaults, **kw})

    def _observation(self, org, product, **kw) -> int:
        with org_session(org) as session:
            row = _obs(org, product, **kw)
            session.add(row)
            session.flush()
            return row.id

    @pytest.mark.ac("AC-26")
    def test_a_passing_case_may_not_carry_an_input(self, tenant):
        """The privacy half of AC-26, and the only half a CHECK can honestly carry.
        Nobody asks to see the input of a case that passed, so storing it is retained
        personal data with no reader. PRD B6 sets unredacted PII at zero tolerance."""
        org, product = tenant
        observation_id = self._observation(org, product)
        with pytest.raises((IntegrityError, DataError)):
            with org_session(org) as session:
                session.add(self._case(
                    org, observation_id, case_id="1",
                    outcome="pass", input_redacted="the customer's actual question",
                ))

    @pytest.mark.ac("AC-26")
    def test_a_passing_case_with_no_input_is_the_normal_row(self, tenant):
        """Null on a pass is the correct state, not missing data."""
        org, product = tenant
        observation_id = self._observation(org, product)
        with org_session(org) as session:
            session.add(self._case(
                org, observation_id, case_id="1", outcome="pass", input_redacted=None
            ))
        with org_session(org) as session:
            row = session.execute(select(CaseResult)).scalar_one()
        assert (row.outcome, row.input_redacted) == ("pass", None)

    @pytest.mark.ac("AC-26")
    def test_every_outcome_in_the_vocabulary_is_storable_and_others_are_not(self, tenant):
        org, product = tenant
        observation_id = self._observation(org, product)
        with org_session(org) as session:
            for i, outcome in enumerate(("pass", "fail", "error", "skipped")):
                session.add(self._case(
                    org, observation_id, case_id=f"c{i}", outcome=outcome,
                    input_redacted="[redacted]" if outcome in ("fail", "error") else None,
                ))
        with pytest.raises((IntegrityError, DataError)):
            with org_session(org) as session:
                session.add(self._case(org, observation_id, case_id="flaky", outcome="flaky"))

    @pytest.mark.ac("AC-26")
    def test_a_skipped_case_may_not_carry_an_input_either(self, tenant):
        """`skipped` means this metric did not measure the case. Nobody asks to see the
        input of a case nobody measured, so storing it is retained personal data with no
        reader — the same argument AC-26 makes about a passing case.

        This is here because the first version of the rule was written as
        `outcome <> 'pass'`, which let the text of every true negative through: nine of
        fourteen cases per run for one fixture's recall metric."""
        org, product = tenant
        observation_id = self._observation(org, product)
        with pytest.raises((IntegrityError, DataError)):
            with org_session(org) as session:
                session.add(self._case(
                    org, observation_id, case_id="tn",
                    outcome="skipped", input_redacted="the customer's actual question",
                ))

    @pytest.mark.ac("AC-2")
    def test_the_same_case_in_the_same_run_twice_is_refused(self, tenant):
        """The idempotency key, which carries `measured_at` only because Postgres
        requires a partitioned table's unique constraint to contain its partition keys.
        It stays idempotent because the time is the observation's, not the clock's."""
        org, product = tenant
        observation_id = self._observation(org, product)
        with org_session(org) as session:
            session.add(self._case(org, observation_id))
        with pytest.raises(IntegrityError):
            with org_session(org) as session:
                session.add(self._case(org, observation_id))

    @pytest.mark.ac("AC-22")
    def test_a_case_cannot_be_edited_after_it_has_been_cited(self, tenant):
        """Append-only, in the database. A row a finding cites as its proof must not be
        rewritable, and the one thing that legitimately changes — whether the trace body
        still exists at the source — is resolved live and reported (B3 rule 11)."""
        org, product = tenant
        observation_id = self._observation(org, product)
        with org_session(org) as session:
            session.add(self._case(org, observation_id))
        with pytest.raises((InternalError, ProgrammingError)) as caught:
            with org_session(org) as session:
                session.execute(text("UPDATE case_result SET outcome = 'pass'"))
        assert "append-only" in str(caught.value)

    @pytest.mark.ac("AC-23")
    def test_a_case_whose_trace_is_gone_still_holds_its_outcome(self, tenant):
        """AC-23's storage half. The pointer and the outcome survive the source's
        retention window; whether the body behind it still exists is resolved later."""
        org, product = tenant
        observation_id = self._observation(org, product)
        with org_session(org) as session:
            session.add(self._case(
                org, observation_id, trace_id="t-14",
                trace_url="https://traces.example/t-14",
            ))
        with org_session(org) as session:
            row = session.execute(select(CaseResult)).scalar_one()
        assert (row.outcome, row.trace_id, row.trace_url) == (
            "fail", "t-14", "https://traces.example/t-14",
        )

    @pytest.mark.ac("AC-27")
    def test_rows_route_to_a_partition_by_month(self, tenant):
        """AC-27's month half, asserted against where the row actually landed rather
        than against the DDL that was issued."""
        org, product = tenant
        september = self._observation(org, product, run_url="https://runs.example/sep")
        november = self._observation(
            org, product, run_url="https://runs.example/nov",
            measured_at=datetime(2026, 11, 20, 9, 0, tzinfo=UTC),
        )
        with org_session(org) as session:
            session.add(self._case(org, september, case_id="s1"))
            session.add(self._case(
                org, november, case_id="n1",
                measured_at=datetime(2026, 11, 20, 9, 0, tzinfo=UTC),
            ))
        with org_session(org) as session:
            landed = dict(session.execute(text(
                "SELECT tableoid::regclass::text, count(*) FROM case_result "
                "GROUP BY 1 ORDER BY 1"
            )).all())
        assert len(landed) == 2, f"both rows in one partition: {landed}"
        assert any("2026_09" in name for name in landed)
        assert any("2026_11" in name for name in landed)

    @pytest.mark.ac("AC-27")
    def test_a_month_nobody_declared_still_stores_the_row(self, tenant):
        """The DEFAULT partition. The application role cannot create a partition, so a
        run outside the declared window would otherwise be rejected — and AC-2 forbids
        omitting a run. Losing evidence to a maintenance job that did not run is worse
        than a partition holding mixed months."""
        org, product = tenant
        far_future = datetime(2031, 5, 5, 9, 0, tzinfo=UTC)
        observation_id = self._observation(org, product, measured_at=far_future)
        with org_session(org) as session:
            session.add(self._case(org, observation_id, measured_at=far_future))
        with org_session(org) as session:
            where = session.execute(text(
                "SELECT tableoid::regclass::text FROM case_result"
            )).scalar_one()
        assert "default" in where

    @pytest.mark.ac("AC-27")
    @pytest.mark.edge_case("EC-9")
    def test_a_tenant_scoped_delete_clears_every_partition_and_spares_the_other_tenant(
        self, tenant
    ):
        """AC-27's delete half, which is audit item P9. One statement, across months,
        and only the tenant that asked for it."""
        org, product = tenant
        other_org = make_org("erasure-other")
        other_product = make_product(other_org, "theirs", name="Theirs")
        november = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)

        for an_org, a_product in ((org, product), (other_org, other_product)):
            for when, run in ((NOW, "sep"), (november, "nov")):
                observation_id = self._observation(
                    an_org, a_product, measured_at=when,
                    run_url=f"https://runs.example/{run}",
                )
                with org_session(an_org) as session:
                    session.add(self._case(
                        an_org, observation_id, case_id=run, measured_at=when
                    ))

        with org_session(org) as session:
            session.execute(text("SELECT set_config('app.erasure', 'on', true)"))
            session.execute(text("DELETE FROM case_result"))

        with org_session(org) as session:
            assert session.execute(
                select(func.count()).select_from(CaseResult)
            ).scalar_one() == 0
        with org_session(other_org) as session:
            assert session.execute(
                select(func.count()).select_from(CaseResult)
            ).scalar_one() == 2, "an erasure crossed a tenant boundary"

    @pytest.mark.ac("AC-27")
    def test_a_case_cannot_be_deleted_without_an_erasure(self, tenant):
        org, product = tenant
        observation_id = self._observation(org, product)
        with org_session(org) as session:
            session.add(self._case(org, observation_id))
        with pytest.raises((InternalError, ProgrammingError)) as caught:
            with org_session(org) as session:
                session.execute(text("DELETE FROM case_result"))
        assert "append-only" in str(caught.value)


class TestSourceFreshness:
    """PRD B2, B3 rule 12 and B5 item 10. The columns that let an answer say how old
    its evidence is. The behaviour they drive is step 11's; this is what the schema
    refuses."""

    def _source(self, org, product, **kw) -> Source:
        defaults = dict(
            org_id=org, product_id=product, role="eval", kind="file", config={},
        )
        return Source(**{**defaults, **kw})

    def test_a_window_and_a_sync_time_are_storable(self, tenant):
        org, product = tenant
        with org_session(org) as session:
            session.add(self._source(
                org, product, freshness_window=timedelta(days=1),
                last_sync_at=NOW, status="healthy",
            ))
        with org_session(org) as session:
            row = session.execute(select(Source)).scalar_one()
        assert (row.status, row.freshness_window) == ("healthy", timedelta(days=1))

    def test_the_default_status_is_healthy(self, tenant):
        """`bound` was the old free-text default and is not a value in B2's set."""
        org, product = tenant
        with org_session(org) as session:
            session.add(self._source(org, product))
        with org_session(org) as session:
            assert session.execute(select(Source.status)).scalar_one() == "healthy"

    def test_an_unknown_status_is_refused(self, tenant):
        org, product = tenant
        with pytest.raises((IntegrityError, DataError)):
            with org_session(org) as session:
                session.add(self._source(org, product, status="bound"))

    def test_overdue_without_a_window_is_refused(self, tenant):
        """A source cannot be overdue against a window nobody set. Without this, a
        `status` of `overdue` could degrade verdicts on no stated basis at all."""
        org, product = tenant
        with pytest.raises((IntegrityError, DataError)):
            with org_session(org) as session:
                session.add(self._source(
                    org, product, status="overdue", overdue_since=NOW,
                ))

    def test_overdue_without_a_timestamp_is_refused(self, tenant):
        org, product = tenant
        with pytest.raises((IntegrityError, DataError)):
            with org_session(org) as session:
                session.add(self._source(
                    org, product, status="overdue", freshness_window=timedelta(days=1),
                ))

    def test_a_zero_or_negative_window_is_refused(self, tenant):
        """A window of zero would make every measurement stale the instant it was
        taken, which reads as the Layer being broken rather than as a policy."""
        org, product = tenant
        with pytest.raises((IntegrityError, DataError)):
            with org_session(org) as session:
                session.add(self._source(
                    org, product, freshness_window=timedelta(0)
                ))


class TestHarvestCap:
    @pytest.mark.ac("AC-33")
    def test_a_product_carries_a_cap_defaulting_to_twenty(self, tenant):
        """AC-33's storage half. US-1's twenty was a hardcoded `provisional` number,
        which is the exact defect this product exists to find in other people's specs,
        so it is a column. Nothing reads it until the write half exists."""
        org, product = tenant
        with org_session(org) as session:
            from layer.db.models import Product

            assert session.get(Product, product).harvest_cap == 20

    @pytest.mark.ac("AC-33")
    def test_a_cap_of_zero_is_refused(self, tenant):
        org, _ = tenant
        with pytest.raises((IntegrityError, DataError)):
            make_product(org, "capless", harvest_cap=0)


@pytest.mark.ac("AC-19")
def test_a_second_tenants_records_are_invisible_across_every_new_table(tenant):
    """AC-19 asks that products be queryable separately and that no finding cite
    another's records. The second half starts here: the new tables must be isolated
    too, not only the three from migration 1."""
    org, product = tenant
    other_org = make_org("other-tenant")
    other_product = make_product(other_org, "theirs", name="Theirs")

    with org_session(other_org) as session:
        session.add(_clause(other_org, other_product, ref="THEIRS-1"))
        theirs = _obs(other_org, other_product, clause_ref="THEIRS-1")
        session.add(theirs)
        session.flush()
        session.add(CaseResult(
            org_id=other_org, observation_id=theirs.id, case_id="14",
            measured_at=NOW, outcome="fail", input_redacted="[redacted]",
        ))
        session.add(AuditEvent(org_id=other_org, actor="them", action="spec_imported"))

    with org_session(org) as session:
        for model in (Clause, Observation, CaseResult, AuditEvent):
            count = session.execute(select(func.count()).select_from(model)).scalar_one()
            assert count == 0, f"{model.__tablename__} leaked across tenants"
