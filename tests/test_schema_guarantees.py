"""What the schema itself refuses. AC-2, AC-9, AC-13, EC-9, H2, H5, H16, P5, P6.

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
    Clause,
    EnforcementFact,
    Observation,
    Proposal,
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
        session.add(_obs(other_org, other_product, clause_ref="THEIRS-1"))
        session.add(AuditEvent(org_id=other_org, actor="them", action="spec_imported"))

    with org_session(org) as session:
        for model in (Clause, Observation, AuditEvent):
            count = session.execute(select(func.count()).select_from(model)).scalar_one()
            assert count == 0, f"{model.__tablename__} leaked across tenants"
