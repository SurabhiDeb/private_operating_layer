"""Freshness: a verdict degrades rather than freezing. AC-28, AC-29, AC-30, B3 rule 12.

The failure this file exists to prevent has no error in it. A scheduled pull dies,
observations stop arriving, and a Layer that reads "the latest observation" keeps
answering `met` with full confidence from a three week old number. PRD B5 item 10 calls
that the worst output this system can produce, because it is indistinguishable from good
news.

So the tests are about silence rather than about failure, and most of them assert that
something was *said*.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from layer.core import freshness
from layer.core.db import org_session
from layer.db.models import Source
from layer.findings import queries
from layer.onboarding import bindings as binding_gate
from layer.onboarding import run, state
from layer.verdicts.rules import Measurement, judge

from conftest import make_org

REFERENCE = Path("/Users/surabhideb/Desktop/chatbot-lab")
URL = "https://github.com/SurabhiDeb/chatbot-lab"
ACTOR = "pm@example.invalid"

#: The committed history is dated September 2026, so every run in it is already weeks old
#: against a nightly window. Where a test needs a specific age it advances `now` instead,
#: which is AC-28's own wording: "proven by test with a clock advanced past the window".
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


# -- the rule itself, with no database at all ------------------------------------


class TestTheFreshnessRule:
    """`judge` applying the window. Pure, so the rule can be read in one place."""

    def _measurement(self, days_old: int) -> Measurement:
        return Measurement(
            value=1.0, passed=50, total=50, observation_id=1,
            measured_at=NOW - timedelta(days=days_old),
        )

    def _judge(self, days_old: int, window: str | None):
        latest = self._measurement(days_old)
        return judge(
            kind="threshold", comparator=">=", value=0.9, latest=latest,
            staleness=freshness.staleness_of(
                latest.measured_at,
                window=None if window is None else _window(window),
                now=NOW,
                source="eval source (repo)",
            ),
        )

    @pytest.mark.ac("AC-28")
    def test_a_measurement_outside_its_window_degrades_rather_than_freezing(self):
        """The whole point. The number still says 50 of 50 and the verdict no longer
        says met, because an absent measurement is not a passing one."""
        fresh, stale = self._judge(1, "7d"), self._judge(23, "7d")

        assert fresh.verdict == "met"
        assert stale.verdict == "cannot_confirm"
        assert stale.degraded_from == "met"
        assert stale.stale is True

    @pytest.mark.ac("AC-28")
    def test_a_missed_verdict_degrades_too(self):
        """Not only the comfortable direction. A stale `missed` is as unfounded as a
        stale `met`, and leaving it would have the Layer asserting a breach from
        evidence it has said is too old to trust."""
        latest = Measurement(
            value=0.5, passed=5, total=10, observation_id=1,
            measured_at=NOW - timedelta(days=23),
        )
        verdict = judge(
            kind="threshold", comparator=">=", value=0.9, latest=latest,
            staleness=freshness.staleness_of(
                latest.measured_at, window=_window("7d"), now=NOW, source="eval source",
            ),
        )
        assert verdict.verdict == "cannot_confirm"
        assert verdict.degraded_from == "missed"

    @pytest.mark.ac("AC-29")
    def test_the_degradation_names_the_source_and_the_age_in_words(self):
        caveat = " ".join(self._judge(23, "7d").caveats)

        assert "eval source (repo)" in caveat
        assert "23d" in caveat
        assert "1w window" in caveat

    def test_an_age_is_rounded_and_a_window_is_exact(self):
        """Two renderings, because one of them read as `1491958s` in real output.

        A window is policy a human typed and will check, so it stays exact. An age is
        prose read once and never lands on a round number, so it is rounded down — never
        up, because an overstated age is a claim the record does not support.
        """
        latest = Measurement(
            value=1.0, passed=50, total=50, observation_id=1,
            measured_at=NOW - timedelta(days=17, hours=6, minutes=25),
        )
        caveat = " ".join(judge(
            kind="threshold", comparator=">=", value=0.9, latest=latest,
            staleness=freshness.staleness_of(
                latest.measured_at, window=_window("90m"), now=NOW, source="eval source",
            ),
        ).caveats)

        assert "17d" in caveat
        assert "90m window" in caveat
        assert "s," not in caveat, "an age rendered in seconds reached the output"


    def test_a_window_nobody_set_degrades_nothing(self):
        """PRD B2: null means no stated cadence, so staleness is reported without a
        verdict being degraded by a window nobody set. A global default would have every
        product on a quarterly cycle wake to a wall of cannot_confirm the Layer invented."""
        verdict = self._judge(400, None)

        assert verdict.verdict == "met"
        assert verdict.stale is False
        assert verdict.caveats == ()

    def test_a_fresh_measurement_carries_as_of_and_no_caveat(self):
        """`as_of` is on every judgement, not only the stale ones: a reader needs to know
        how old an answer is whether or not it crossed a line."""
        verdict = self._judge(1, "7d")

        assert verdict.as_of == NOW - timedelta(days=1)
        assert verdict.caveats == ()

    def test_cannot_confirm_stays_cannot_confirm_and_gains_the_age(self):
        """There is nothing to degrade from: it was never claiming anything. Recording a
        `degraded_from` here would invent a verdict the Layer never held."""
        latest = Measurement(
            value=0.9, passed=9, total=10, observation_id=1,
            measured_at=NOW - timedelta(days=23),
        )
        verdict = judge(
            kind="threshold", comparator=">=", value=0.9, latest=latest,
            staleness=freshness.staleness_of(
                latest.measured_at, window=_window("7d"), now=NOW, source="eval source",
            ),
        )
        assert verdict.verdict == "cannot_confirm"
        assert verdict.degraded_from is None
        assert verdict.stale is True
        assert any("23d" in c for c in verdict.caveats)

    def test_never_measured_is_not_a_degradation(self):
        """`not_measured` and "overdue" are different states and both are honest. A
        clause with nothing bound to it has not gone stale; it never started."""
        verdict = judge(kind="threshold", comparator=">=", value=0.9, latest=None)

        assert verdict.verdict == "not_measured"
        assert verdict.stale is False
        assert verdict.as_of is None


# -- a source's own age, which is a different question ---------------------------


class TestASourcesOwnAge:
    """`now - last_sync_at`, which decides `source.status`.

    Not the same as a measurement's age, and conflating them is the easy mistake: a
    source that synced ten minutes ago and found nothing new leaves a fresh source and a
    stale measurement. Both are reported.
    """

    def _window(self, **kw) -> freshness.SourceWindow:
        defaults = dict(
            source_id="s1", role="eval", kind="repo", window=_window("7d"),
            last_sync_at=NOW - timedelta(days=2), created_at=NOW - timedelta(days=60),
        )
        return freshness.SourceWindow(**{**defaults, **kw})

    def test_a_source_inside_its_window_is_not_overdue(self):
        assert self._window().overdue_by(NOW) is None

    def test_a_source_past_its_window_reports_how_late_it_is(self):
        window = self._window(last_sync_at=NOW - timedelta(days=10))

        assert window.overdue_by(NOW) == timedelta(days=3)
        assert "3d past its 1w window" in window.describe(NOW)

    def test_overdue_since_is_when_it_happened_not_when_it_was_noticed(self):
        """An operator asking how long this has been true needs the date it became true.
        The moment the Layer looked is not information about the source."""
        window = self._window(last_sync_at=NOW - timedelta(days=10))

        assert window.overdue_since(NOW) == NOW - timedelta(days=3)

    def test_a_source_that_never_reported_is_judged_from_when_it_was_bound(self):
        """Treating "never" as "just now" would make a dead pipe look healthy for one
        whole window, which is exactly the silence this module exists to break."""
        window = self._window(last_sync_at=None)

        assert window.overdue_by(NOW) == timedelta(days=53)
        assert "has never reported since it was bound" in window.describe(NOW)

    def test_a_source_bound_moments_ago_is_not_overdue_for_never_reporting(self):
        window = self._window(last_sync_at=None, created_at=NOW - timedelta(hours=1))

        assert window.overdue_by(NOW) is None

    def test_a_source_with_no_window_is_never_overdue(self):
        window = self._window(window=None, last_sync_at=NOW - timedelta(days=400))

        assert window.overdue_by(NOW) is None
        assert window.as_stale_source(NOW)["overdue_by"] is None


# -- end to end, over a reference product ----------------------------------------


@pytest.mark.skipif(not REFERENCE.exists(), reason="reference products not present")
class TestAProductWhoseSourcesWentQuiet:
    """AC-28, AC-29 and AC-30 against a real onboarded product.

    **Why the policy product and not the triage one.** A degradation can only be shown
    where there is a verdict to degrade, and triage has none: its committed runs hold
    between 1 and 20 cases, so every Wilson interval is too wide to clear or fall below a
    bar and all three of its measured clauses read `cannot_confirm` while perfectly
    fresh. The policy product's critical subset is 11 cases against a 100% bar and reads
    `missed`, which is a verdict with something to lose. Probed rather than assumed — the
    first version of this file asserted `met` on triage and failed for that reason.

    **Why the clock moves and the window mostly does not.** Staleness is relative to the
    database's own clock, so a test that hard-coded a date would start failing on a
    future Tuesday. The fresh case uses a window wider than the fixture history can be,
    and the stale case advances `now` past a narrow one. Both hold whatever today is.
    """

    METRICS = [
        {"metric": "status_ok", "kind": "accuracy",
         "actual": "status", "expected": "expected_status"},
        {"metric": "critical_pass_rate", "kind": "accuracy",
         "actual": "status", "expected": "expected_status",
         "filter": {"field": "critical", "op": "is_present"}},
    ]
    #: Wider than the committed history can ever be, so "fresh" does not depend on the
    #: date the suite happens to run on.
    WIDE = "9999d"

    def _onboard(self, session, org, *, window: str | None):
        common = {"local_path": str(REFERENCE), "repo_url": URL}
        product = state.register(
            session, org_id=org, key="policydesk", name="Policydesk",
            ref_prefix="PD", actor=ACTOR,
        )
        state.bind_source(
            session, product=product, role="spec", kind="repo", actor=ACTOR,
            config={**common, "path": "products/policydesk/SPEC.md"},
        )
        state.bind_source(
            session, product=product, role="eval", kind="repo", actor=ACTOR,
            freshness_window=_window(window) if window else None,
            config={**common, "globs": ["products/policydesk/runs/*.json"], "readers": [{
                "glob": "products/policydesk/runs/*.json",
                "measured_at": "/meta/started",
                "corpus_sha": "/meta/corpus_sha",
                "prompt_version": "/meta/prompt_sha",
                "code_rev": "/meta/git",
                "metrics": self.METRICS,
            }]},
        )
        run.import_spec(session, product=product, actor=ACTOR)
        run.backfill(session, product=product, actor=ACTOR)
        for candidate in binding_gate.propose(session, product=product).pending:
            binding_gate.decide(
                session, product=product, metric=candidate.metric,
                clause_ref=candidate.clause_ref, decision="confirmed", by=ACTOR,
            )
        # The specification states this bar for the critical subset and names it
        # differently, so a human pairs it. Same as the findings suite does.
        binding_gate.decide(
            session, product=product, metric="critical_pass_rate", clause_ref="PD-8.8",
            decision="confirmed", by=ACTOR,
            note="paired by hand: the specification names this bar differently",
        )
        state.refresh(session, product=product, actor=ACTOR)
        return product

    def _live(self, session, org, *, window: str | None, quiet_for=None, after=None):
        """Onboard, optionally let the pull die, then measure.

        `quiet_for` backdates `last_sync_at`, which is what a pull that stopped actually
        leaves behind. Earlier versions of these tests advanced a clock inside `measure`
        instead and produced a state the world cannot reach: a row saying it synced
        seconds ago while the answer claimed it was overdue. The two ages this module
        separates have to be made stale the way each really goes stale.
        """
        product = self._onboard(session, org, window=window)
        if quiet_for is not None:
            _went_quiet(session, product, quiet_for)
        at = freshness.clock(session) + after if after else None
        counts = state.measure(session, product=product, actor=ACTOR, now=at)
        return product, counts, at

    @pytest.mark.ac("AC-28")
    def test_a_verdict_holds_while_its_measurement_is_inside_the_window(self):
        """The control, and it is load-bearing. Without it a suite in which nothing is
        ever `missed` would pass whether the freshness rule worked or the verdict engine
        had simply stopped answering."""
        org = make_org("freshness-fresh")
        with org_session(org) as session:
            _, counts, _ = self._live(session, org, window=self.WIDE)

            assert counts.get("missed", 0) >= 1, counts

    @pytest.mark.ac("AC-28")
    @pytest.mark.ac("AC-30")
    def test_the_same_product_degrades_once_the_clock_passes_the_window(self):
        """AC-28 and AC-30 together: the clock advanced past the window, nothing else
        changed — same rows, same bindings, same product — and the verdict that said
        `missed` says `cannot_confirm` instead of holding its last value."""
        org = make_org("freshness-overdue")
        with org_session(org) as session:
            product, counts, _ = self._live(
                session, org, window="1d", quiet_for=timedelta(days=30)
            )

            assert counts.get("met", 0) == 0, counts
            assert counts.get("missed", 0) == 0, counts
            assert counts["cannot_confirm"] >= 1, counts
            assert product.status == "live"

            status = state.status(session, product=product)
            assert status.stale_sources, "no source was reported stale"

    @pytest.mark.ac("AC-30")
    def test_a_product_with_every_source_overdue_says_why_in_words(self):
        """AC-30. A product can be healthy and uninformative at once, and the correct
        output is a high count of cannot_confirm rather than a reassuring dashboard."""
        org = make_org("freshness-why")
        with org_session(org) as session:
            product, _, _ = self._live(
                session, org, window="1d", quiet_for=timedelta(days=30)
            )
            overdue = freshness.stale_sources(session, product=product)

            assert overdue, "every source is past its window and none was named"
            assert all(e["overdue_by"] for e in overdue)
            assert any("eval source (repo)" in e["note"] for e in overdue)
            assert "OVERDUE" in state.status(session, product=product).describe()

    @pytest.mark.ac("AC-28")
    def test_the_clock_alone_moves_the_verdict_between_two_passes(self):
        """AC-28 as written: "proven by test with a clock advanced past the window".

        Nothing changes between the two passes but the time: same rows, same bindings,
        same window. This is the seam `measure(now=...)` exists for, and the only thing
        in the suite that uses it.
        """
        org = make_org("freshness-clock")
        with org_session(org) as session:
            product, before, _ = self._live(session, org, window=self.WIDE)
            after = state.measure(
                session, product=product, actor=ACTOR,
                now=freshness.clock(session) + timedelta(days=20000),
            )

            assert before.get("missed", 0) >= 1, before
            assert after.get("missed", 0) == 0, after
            assert after["cannot_confirm"] > before.get("cannot_confirm", 0)

    @pytest.mark.ac("AC-29")
    def test_the_audit_event_names_the_verdict_that_moved(self):
        """A count of cannot_confirm is not an explanation. B5 item 10 is the failure
        being guarded against, so which clause moved and what it would otherwise have
        read has to be findable after the fact."""
        from layer.db.models import AuditEvent

        org = make_org("freshness-audit")
        with org_session(org) as session:
            self._live(session, org, window="1d", quiet_for=timedelta(days=30))
            detail = session.execute(
                select(AuditEvent.detail)
                .where(AuditEvent.action == "verdicts_computed")
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .limit(1)
            ).scalar_one()

            degraded = detail.get("degraded_for_staleness") or []
            assert degraded, detail
            assert any("would have read missed" in line for line in degraded)
            assert detail["sources"].get("overdue", 0) >= 1

    @pytest.mark.ac("AC-29")
    def test_every_finding_resting_on_a_stale_source_says_so_in_prose(self):
        """AC-29 as written: "names that source and its age in prose, not only in a
        field". So the field is checked, and then the words are."""
        org = make_org("freshness-findings")
        with org_session(org) as session:
            product, _, _ = self._live(
                session, org, window="1d", quiet_for=timedelta(days=30)
            )
            drift = queries.find_drift(session, product=product)

            assert drift.stale_sources, "the set did not carry stale_sources"
            assert any("eval source (repo)" in e["note"] for e in drift.stale_sources)
            assert len(drift) >= 1
            for finding in drift:
                assert finding.as_of is not None
                assert finding.stale is True
                assert "not current" in finding.summary
                assert "outside its" in finding.summary

    def test_a_stale_drift_summary_states_the_record_not_the_present(self):
        """Found by reading output rather than by a test. "The latest run is at 90.9%
        and still misses the bar" sat one sentence away from "this measurement is not
        current", so the summary asserted and withdrew the same thing in one paragraph.
        A stale finding states what the record holds and leaves the present alone."""
        org = make_org("freshness-tense")
        with org_session(org) as session:
            product, _, _ = self._live(
                session, org, window="1d", quiet_for=timedelta(days=30)
            )
            finding = next(iter(queries.find_drift(session, product=product)))

            assert finding.stale and finding.current
            assert "still misses" not in finding.summary
            assert "newest run on record" in finding.summary
            assert "below the bar" in finding.summary

    def test_a_fresh_drift_summary_keeps_the_present_tense(self):
        """The control. The present tense is correct when the measurement is current,
        and losing it everywhere would be a worse summary for the common case."""
        org = make_org("freshness-tense-ok")
        with org_session(org) as session:
            product, _, _ = self._live(session, org, window=self.WIDE)
            finding = next(iter(queries.find_drift(session, product=product)))

            assert not finding.stale
            assert "still misses the bar" in finding.summary

    @pytest.mark.ac("AC-29")
    def test_a_fresh_product_carries_no_staleness_anywhere(self):
        """Empty is the required state, and a caveat that shows up when nothing is wrong
        is a caveat nobody reads when something is."""
        org = make_org("freshness-quiet")
        with org_session(org) as session:
            product, _, _ = self._live(session, org, window=self.WIDE)
            drift = queries.find_drift(session, product=product)

            assert drift.stale_sources == []
            assert all(not f.stale for f in drift)
            assert all("not current" not in f.summary for f in drift)

    def test_a_clause_whose_source_states_no_cadence_is_never_degraded(self):
        """The default. Most sources arrive with no window, and the Layer must not
        invent one on their behalf — a product on a quarterly review cycle would wake up
        to a wall of cannot_confirm nobody asked for."""
        org = make_org("freshness-none")
        with org_session(org) as session:
            _, counts, _ = self._live(
                session, org, window=None, quiet_for=timedelta(days=4000)
            )

            assert counts.get("missed", 0) >= 1, counts

    def test_an_overdue_source_is_recorded_on_the_row_as_well(self):
        """`status` and `overdue_since` exist for the surfaces that read a source rather
        than a finding, and the CHECK requires the two together."""
        org = make_org("freshness-row")
        with org_session(org) as session:
            product, _, _ = self._live(
                session, org, window="1d", quiet_for=timedelta(days=30)
            )
            rows = session.execute(
                select(Source).where(Source.product_id == product.id)
            ).scalars().all()
            overdue = [r for r in rows if r.status == "overdue"]

            assert overdue
            assert all(r.overdue_since is not None for r in overdue)
            assert all(r.last_sync_at is not None for r in rows), (
                "an import that read a source left no last_sync_at, so nothing can tell "
                "a dead pull from a quiet one"
            )

    def test_a_backfill_stamps_the_source_it_read(self):
        """The stamp is about the pipe, not about what came down it. A source that only
        counted as synced when a new run appeared would report a dead pull every quiet
        night."""
        org = make_org("freshness-stamp")
        with org_session(org) as session:
            product, _, _ = self._live(session, org, window=self.WIDE)
            before = session.execute(
                select(Source.last_sync_at).where(
                    Source.product_id == product.id, Source.role == "eval"
                )
            ).scalar_one()

            again = run.backfill(session, product=product, actor=ACTOR)
            after = session.execute(
                select(Source.last_sync_at).where(
                    Source.product_id == product.id, Source.role == "eval"
                )
            ).scalar_one()

            assert "0 observations stored" in again.detail, "expected a no-op backfill"
            assert after >= before


def _went_quiet(session, product, ago: timedelta) -> None:
    """Backdate every source's last sync, which is what a dead pull leaves behind.

    Written directly rather than through a module function on purpose: nothing in the
    Layer may move a sync stamp backwards, so there is no API for this and a test that
    wanted one would be asking for a way to lie about when data arrived.
    """
    at = freshness.clock(session) - ago
    for source in session.execute(
        select(Source).where(Source.product_id == product.id)
    ).scalars():
        source.last_sync_at = at
    session.flush()


def _window(text: str) -> timedelta:
    from layer.core.durations import parse_duration

    return parse_duration(text)
