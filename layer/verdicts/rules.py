"""What the answer is right now: a clause's verdict.

`state` and `verdict` answer different questions and PRD AC-13 requires both. `state` says
where the number came from; this module computes the other one.

**The verdict is read from the most recent observation**, because "right now" is what it
means. Whether a bar was ever missed in the past is a different question, and it is the
drift finding's question rather than this one. The prototype draws the same line: a clause
status carries the latest value and its interval, while the count of breaching runs sits in
the finding.

**A proportion is judged by its interval, not its point.** `met` requires the whole
interval to clear the bar, `missed` requires all of it to fall short, and anything else is
`cannot_confirm`. That is why `cannot_confirm` is expected to dominate — in the reference
implementation it was 365 of 540 verdicts — and PRD B2 calls that "the honest state rather
than a defect. A system that reports met or missed for everything is guessing."

**A measurement with no sample is a point estimate and says so.** A p95 latency or a mean
carries no passed/total, so there is no interval to bound it with. Such a clause is compared
directly and carries a caveat, because inventing a sample size would be worse than
admitting there is none.

**An old measurement is not a passing one.** Where the caller supplies a `Staleness` and it
is outside the source's window, `met` and `missed` degrade to `cannot_confirm` with the age
and the source named, and `degraded_from` records what the stored number would otherwise
have read (PRD B3 rule 12, AC-28). The window arithmetic is the caller's because it needs
the source; the rule is here because it is part of what a verdict means.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from layer.core.freshness import Staleness
from layer.verdicts.stats import wilson

MET = "met"
MISSED = "missed"
CANNOT_CONFIRM = "cannot_confirm"
NOT_APPLICABLE = "not_applicable"
NOT_MEASURED = "not_measured"

PROVISIONAL = "provisional"
MEASURED = "measured"
RATIFIED = "ratified"

#: Kinds that state something unmeasurable. PRD H6 and EC-7: held as written, and never
#: counted in drift or coverage, so a policy about ambiguity does not become a failing
#: number.
UNMEASURABLE_KINDS = frozenset({"rule", "contract", "non_goal", "hard_case"})


@dataclass(frozen=True)
class Judgement:
    verdict: str
    caveats: tuple[str, ...] = ()
    interval: tuple[float, float] | None = None
    #: The observation the verdict was read from, for the citation.
    observation_id: int | None = None
    #: The `measured_at` of that observation. PRD B1's `as_of`: what a reader needs to
    #: know how old the answer is, whether or not it is stale.
    as_of: datetime | None = None
    stale: bool = False
    #: What the number would have read inside its window. Recorded so a degradation is
    #: visible as one rather than looking like a clause that was never confident — a
    #: fact about the stored measurement, and not a claim about the product.
    degraded_from: str | None = None


@dataclass(frozen=True)
class Measurement:
    """The part of an observation a verdict depends on."""

    value: float
    passed: int | None = None
    total: int | None = None
    observation_id: int | None = None
    measured_at: datetime | None = None
    #: Which source it came from, so the caller can find that source's window.
    source_id: object | None = None


def judge(
    *,
    kind: str,
    comparator: str | None,
    value: float | None,
    value_high: float | None = None,
    latest: Measurement | None = None,
    staleness: Staleness | None = None,
) -> Judgement:
    """The verdict for one clause, given its most recent measurement.

    `staleness` is None where there is no stated cadence, and then nothing degrades: a
    window nobody set is not a window this clause failed to meet.
    """
    if kind in UNMEASURABLE_KINDS or comparator is None or value is None:
        return Judgement(NOT_APPLICABLE, (
            "this clause states no numeric bar, so it is held as written and never "
            "counted as drift",
        ))
    if latest is None:
        # Never measured and long overdue are different states and both are honest. A
        # clause with nothing bound to it is `not_measured`, which is not a degradation
        # of anything.
        return Judgement(NOT_MEASURED, ("no measurement is bound to this clause",))

    if latest.total:
        low, high = wilson(latest.passed or 0, latest.total)
        verdict = _against_interval(comparator, value, value_high, low, high)
        caveats: tuple[str, ...] = ()
        if verdict == CANNOT_CONFIRM:
            caveats = (
                f"the 95% interval for {latest.passed} of {latest.total} spans "
                f"{low:.3f} to {high:.3f}, which does not sit wholly on either side of "
                f"the bar",
            )
        return _aged(
            Judgement(verdict, caveats, (low, high), latest.observation_id), latest, staleness
        )

    # No sample behind the number.
    verdict = _against_point(comparator, value, value_high, latest.value)
    return _aged(
        Judgement(
            verdict,
            (
                "this measurement carries no sample size, so the verdict rests on a "
                "single point with no interval around it",
            ),
            None,
            latest.observation_id,
        ),
        latest,
        staleness,
    )


def _aged(
    judgement: Judgement, latest: Measurement, staleness: Staleness | None
) -> Judgement:
    """Apply the freshness rule to a verdict that has already been computed.

    Applied after rather than instead, because the Layer has to be able to say what the
    stored number would have read. A verdict computed as `cannot_confirm` on its interval
    stays `cannot_confirm` and simply gains the age: it was never claiming anything, so
    there is nothing to degrade and `degraded_from` would be misleading.
    """
    as_of = latest.measured_at
    if staleness is None:
        return replace(judgement, as_of=as_of)
    if not staleness.stale:
        # Fresh, and said so. The age goes nowhere near the caveats: a measurement
        # inside its window is the normal state and does not need qualifying.
        return replace(judgement, as_of=as_of)
    if judgement.verdict in (MET, MISSED):
        return replace(
            judgement,
            verdict=CANNOT_CONFIRM,
            caveats=(staleness.describe(),) + judgement.caveats,
            as_of=as_of,
            stale=True,
            degraded_from=judgement.verdict,
        )
    return replace(
        judgement,
        caveats=judgement.caveats + (staleness.describe(),),
        as_of=as_of,
        stale=True,
    )


def _against_interval(
    comparator: str, value: float, value_high: float | None, low: float, high: float
) -> str:
    if comparator == ">=":
        if low >= value:
            return MET
        return MISSED if high < value else CANNOT_CONFIRM
    if comparator == "<=":
        if high <= value:
            return MET
        return MISSED if low > value else CANNOT_CONFIRM
    if comparator == "==":
        if low >= value and high <= value:
            return MET
        return MISSED if (high < value or low > value) else CANNOT_CONFIRM
    if comparator == "between" and value_high is not None:
        if low >= value and high <= value_high:
            return MET
        return MISSED if (high < value or low > value_high) else CANNOT_CONFIRM
    return CANNOT_CONFIRM


def _against_point(
    comparator: str, value: float, value_high: float | None, observed: float
) -> str:
    if comparator == ">=":
        return MET if observed >= value else MISSED
    if comparator == "<=":
        return MET if observed <= value else MISSED
    if comparator == "==":
        return MET if abs(observed - value) < 1e-9 else MISSED
    if comparator == "between" and value_high is not None:
        return MET if value <= observed <= value_high else MISSED
    return CANNOT_CONFIRM


def state_for(*, has_measurement: bool, ratified: bool = False) -> str:
    """Where the number came from.

    Independent of the verdict (AC-13). A `ratified` clause can still be
    `cannot_confirm`, and a `met` clause can still be `provisional` — which means it is
    passing a bar nobody ever justified, and PRD H15 wants that reported rather than
    read as satisfaction.
    """
    if ratified:
        return RATIFIED
    return MEASURED if has_measurement else PROVISIONAL
