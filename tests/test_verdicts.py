"""The verdict rule and the interval behind it. AC-39, AC-13, H15, B2.

AC-39 had no test of its own. The Wilson interval was exercised only through onboarding
verdicts, where a wrong bound would have shifted a count nobody was asserting exactly, and
`layer/verdicts/stats.py` was not tested anywhere at all. That is the thinnest coverage in
the suite over the most load-bearing arithmetic in it: every `met` and every `missed` the
Layer has ever produced came out of these forty lines.

**The strongest test here compares two independent implementations.** The reference
fixtures carry their own `shared/stats.py`, and the Layer reimplements it rather than
importing it, because a fixture may never be a dependency (Appendix F). Reimplementation is
the right call and it is also the risk: two copies can drift. So the bounds are compared
against the fixtures' own across a sweep, which is the only check that would catch a
transcription error in either.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

from layer.verdicts.rules import judge
from layer.verdicts.stats import Z_95, wilson

FIXTURE_STATS = Path("/Users/surabhideb/Desktop/chatbot-lab/shared/stats.py")


def _bar(passed: int, total: int, comparator: str = ">=", value: float = 0.85):
    from layer.verdicts.rules import Measurement

    return judge(
        kind="threshold", comparator=comparator, value=value,
        latest=Measurement(value=passed / total, passed=passed, total=total,
                           observation_id=1),
    )


class TestTheInterval:
    def test_the_z_is_pinned_and_is_the_fixtures_own(self):
        """Pinned on purpose, so a number the Layer reports and a number the product
        reports agree to the last digit."""
        assert Z_95 == 1.959963984540054

    def test_a_proportion_is_bounded_by_zero_and_one(self):
        for passed, total in ((0, 1), (0, 20), (20, 20), (1, 2), (7, 7)):
            low, high = wilson(passed, total)
            assert 0.0 <= low <= high <= 1.0, (passed, total, low, high)

    def test_the_interval_narrows_as_the_sample_grows(self):
        """The property the whole rule exists for: 19/20 and 190/200 are both 95% and
        they are not the same claim."""
        widths = [wilson(round(0.95 * n), n) for n in (20, 200, 2000)]
        spans = [high - low for low, high in widths]
        assert spans[0] > spans[1] > spans[2]

    def test_seven_of_seven_is_not_certainty(self):
        """The prototype's `[0.6456695649333126, 1.0]` for seven of seven is what
        identified this rule as Wilson rather than something else. The textbook normal
        interval returns zero width here and claims certainty from seven samples."""
        low, high = wilson(7, 7)
        assert high == 1.0
        assert low == pytest.approx(0.6456695649333126, abs=1e-12)

    def test_a_total_of_zero_is_refused_rather_than_guessed(self):
        with pytest.raises(ValueError):
            wilson(0, 0)

    def test_more_passes_than_cases_is_refused(self):
        with pytest.raises(ValueError):
            wilson(5, 4)

    @pytest.mark.skipif(not FIXTURE_STATS.exists(), reason="fixture stats not present")
    def test_the_bounds_match_the_fixtures_own_implementation(self):
        """Two implementations, one constant, swept across the range where they could
        disagree. This is the test that would catch a transcription error in either, and
        it is the reason reimplementing rather than importing is safe to do."""
        specification = importlib.util.spec_from_file_location("fixture_stats", FIXTURE_STATS)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)

        compared = 0
        for total in (1, 2, 5, 7, 11, 14, 20, 47, 50, 200, 1000):
            for passed in range(0, total + 1):
                theirs = module.wilson(passed, total)
                low, high = wilson(passed, total)
                their_low = theirs[0] if isinstance(theirs, tuple) else theirs.low
                their_high = theirs[1] if isinstance(theirs, tuple) else theirs.high
                assert low == pytest.approx(their_low, abs=1e-12), (passed, total)
                assert high == pytest.approx(their_high, abs=1e-12), (passed, total)
                compared += 1
        assert compared > 1300, compared


class TestTheVerdictRule:
    """AC-39's own sentence: "a clause with few cases returns `cannot_confirm` rather
    than `met` on a favourable point estimate"."""

    @pytest.mark.ac("AC-39")
    def test_a_favourable_point_estimate_on_few_cases_cannot_confirm(self):
        """9 of 10 is 90% against an 85% bar, and the interval runs from 0.60 to 0.98.
        Reporting `met` would be the single most expensive wrong answer this rule can
        give, because it reads as good news."""
        few = _bar(9, 10)
        low, high = wilson(9, 10)

        assert few.verdict == "cannot_confirm"
        assert low < 0.85 < high
        assert few.caveats and "interval" in few.caveats[0]

    @pytest.mark.ac("AC-39")
    def test_the_same_point_estimate_on_many_cases_is_met(self):
        """900 of 1000 is the same 90% and a different claim. Without this the suite
        would pass with a rule that never says `met` at all."""
        many = _bar(900, 1000)
        assert many.verdict == "met"
        assert wilson(900, 1000)[0] >= 0.85

    @pytest.mark.ac("AC-39")
    def test_missed_requires_the_whole_interval_below_the_bar(self):
        assert _bar(1, 10).verdict == "missed"
        assert wilson(1, 10)[1] < 0.85

    def test_a_cap_is_judged_from_the_upper_bound(self):
        """`<=` inverts which end of the interval has to clear the bar, and getting it
        backwards would make every latency and cost clause confidently wrong."""
        assert _bar(1, 100, comparator="<=", value=0.1).verdict == "met"
        assert _bar(50, 100, comparator="<=", value=0.1).verdict == "missed"
        assert _bar(9, 100, comparator="<=", value=0.1).verdict == "cannot_confirm"

    def test_a_band_requires_the_whole_interval_inside_it(self):
        from layer.verdicts.rules import Measurement

        inside = judge(
            kind="threshold", comparator="between", value=0.03, value_high=0.08,
            latest=Measurement(value=0.05, passed=50, total=1000, observation_id=1),
        )
        assert inside.verdict == "met"
        wide = judge(
            kind="threshold", comparator="between", value=0.03, value_high=0.08,
            latest=Measurement(value=0.05, passed=1, total=20, observation_id=1),
        )
        assert wide.verdict == "cannot_confirm"

    def test_a_measurement_with_no_sample_says_it_has_none(self):
        """A p95 or a mean carries no passed/total, so there is no interval. Inventing a
        sample size would be worse than admitting there is none."""
        from layer.verdicts.rules import Measurement

        point = judge(
            kind="threshold", comparator="<=", value=1.0,
            latest=Measurement(value=0.9, observation_id=1),
        )
        assert point.verdict == "met"
        assert point.interval is None
        assert "no sample size" in " ".join(point.caveats)

    @pytest.mark.ac("AC-13")
    @pytest.mark.hard_case("H15")
    def test_state_and_verdict_are_independent(self):
        """H15: a clause can be `met` while `provisional`, which means it is passing a
        bar nobody justified. The two axes are set by different functions precisely so
        that one cannot quietly imply the other."""
        from layer.verdicts.rules import state_for

        assert state_for(has_measurement=True) == "measured"
        assert state_for(has_measurement=False) == "provisional"
        assert state_for(has_measurement=True, ratified=True) == "ratified"
        # And the verdict function has no access to state at all: nothing in `judge`'s
        # signature carries it.
        assert _bar(900, 1000).verdict == "met"


@pytest.mark.ac("AC-38")
def test_a_band_a_duration_and_a_currency_amount_survive_the_round_trip():
    """AC-38: "A clause stating a band, a duration or a currency amount round-trips
    through `comparator`, `value`, `value_high`, `unit` and `direction` without loss,
    proven by test over at least one of each."

    The behaviour and its tests existed in the spec adapter and the schema; no marker
    connected either to this criterion, so the matrix could not see it. One of each, read
    through the parser the importer uses.
    """
    from layer.adapters.spec.values import parse_target

    band = parse_target("3% to 8%")
    assert (band.comparator, band.value, band.value_high) == ("between", 0.03, 0.08)
    assert band.direction == "within_band"

    duration = parse_target("under 1 second")
    assert duration.comparator == "<="
    assert duration.value == 1.0
    assert duration.unit == "duration_s"
    assert duration.direction == "lower_is_better"

    money = parse_target("under £1,200")
    assert money.comparator == "<="
    assert money.value == 1200.0
    assert money.unit == "currency"
    assert money.direction == "lower_is_better"
