"""Reading a stated target out of human prose.

This is where `unit`, `direction` and `value_high` stop being schema decoration. Real
targets in real specifications look like this, all from the reference fixtures:

    85%            99%            100%           0.85
    3% to 8%       under 1 second under 3s       under £1,200
    under 3p       at least 99%   no more than 5%

A `comparator` and a single `value` can hold the first four and none of the rest. Worse,
without a unit there is no way to tell `0.85` as a score from `85` as a percentage from
`850` as milliseconds, so a threshold parsed from one spec cannot be compared to a number
measured from another.

Percentages become ratios, because that is what measured values are: `85%` is stored as
`0.85` with unit `ratio`. Durations and money are left in their own units and are never
scaled into a ratio, since 1200 pounds is not 12 of anything.

**On assumed comparators.** A bare `85%` in a column headed Target almost always means a
floor, but it does not say so. Where the parser assumes a direction it records the
assumption rather than hiding it, every clause is created `provisional`, and a human
confirms at onboarding step 5. The Layer proposes the reading; it does not decide it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Target:
    comparator: str
    value: float
    value_high: float | None = None
    unit: str | None = None
    direction: str | None = None
    #: Readings the parser inferred rather than read. Shown to the human who confirms.
    assumptions: tuple[str, ...] = field(default_factory=tuple)


# Phrases that state a direction outright.
_AT_LEAST = r"(?:at\s+least|minimum|min|no\s+less\s+than|not\s+below|≥|>=)"
# The lookbehinds matter: without them "no less than" and "not below" match here
# first and invert the comparator, turning a floor into a ceiling.
_AT_MOST = (
    r"(?:under|(?<!not\s)below|at\s+most|maximum|max|no\s+more\s+than|"
    r"(?<!no\s)less\s+than|within|≤|<=)"
)
_BAND = r"(?:to|and|–|—|-)"

# The lookbehind keeps `p95` and `recall@5` from donating their digits to a threshold:
# a number fused to an identifier is part of a name, not a target.
_NUMBER = r"(?<![a-z0-9@._])(\d+(?:[.,]\d+)?)"

# Unit suffixes. Order matters: `ms` before `s`, `seconds` before `s`.
_UNITS: tuple[tuple[str, str, float], ...] = (
    (r"%", "ratio", 0.01),
    (r"percent", "ratio", 0.01),
    (r"ms|milliseconds?", "duration_ms", 1.0),
    (r"seconds?|secs?|s\b", "duration_s", 1.0),
    (r"minutes?|mins?", "duration_s", 60.0),
    (r"hours?|hrs?", "duration_s", 3600.0),
    (r"pence|penny|p\b", "currency", 0.01),
    (r"tokens?", "tokens", 1.0),
)
_CURRENCY_PREFIX = r"[£$€]"


def _as_float(raw: str) -> float:
    return float(raw.replace(",", ""))


def _unit_of(tail: str) -> tuple[str | None, float]:
    """The unit named after a number, and the factor that normalises it."""
    tail = tail.strip().lower()
    for pattern, unit, factor in _UNITS:
        if re.match(rf"^\s*(?:{pattern})", tail):
            return unit, factor
    return None, 1.0


def _direction_for(comparator: str) -> str:
    if comparator == "between":
        return "within_band"
    return "higher_is_better" if comparator == ">=" else "lower_is_better"


def parse_target(text: str | None) -> Target | None:
    """A `Target`, or None when the text states no number.

    None is a legitimate outcome, not a failure: a clause may be a rule, a contract or a
    non-goal, and PRD H6 requires those to be held without a bar rather than reported as
    unmeasured drift.
    """
    if not text:
        return None
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return None
    lowered = cleaned.lower()

    # A band first, since "3% to 8%" also matches a single-number pattern.
    band = re.search(
        rf"{_NUMBER}\s*([%a-z]*)\s*{_BAND}\s*{_NUMBER}\s*([%a-z]*)", lowered
    )
    if band and _looks_like_band(lowered, band):
        low_raw, low_tail, high_raw, high_tail = band.groups()
        unit, factor = _unit_of(high_tail or low_tail)
        low = round(_as_float(low_raw) * factor, 12)
        high = round(_as_float(high_raw) * factor, 12)
        if low > high:
            low, high = high, low
        return Target("between", low, high, unit or "ratio", "within_band")

    # Anchor the search to the comparator where there is one. "p95 under 800ms" has two
    # numbers and only the one the comparator governs is the target; searching from the
    # start of the string picks the metric's own name instead.
    at_most = re.search(_AT_MOST, lowered)
    at_least = re.search(_AT_LEAST, lowered)
    anchor = max((m.end() for m in (at_most, at_least) if m), default=0)

    pattern = rf"({_CURRENCY_PREFIX})?\s*{_NUMBER}\s*([%a-z]*)"
    match = re.search(pattern, lowered[anchor:]) or re.search(pattern, lowered)
    if not match:
        return None
    currency, number, tail = match.groups()

    if currency:
        unit, factor = "currency", 1.0
    else:
        unit, factor = _unit_of(tail)

    # Rounded because 85 * 0.01 is 0.8500000000000001 in binary floating point. An
    # invisible discrepancy in a threshold is worse than an obvious one: it makes a
    # clause that should read `met` read `missed` for no visible reason.
    value = round(_as_float(number) * factor, 12)
    assumptions: tuple[str, ...] = ()

    if at_most:
        comparator = "<="
    elif at_least:
        comparator = ">="
    elif unit in ("duration_ms", "duration_s", "currency", "tokens"):
        # A latency or a cost target is a ceiling. Nobody promises to be slow.
        comparator = "<="
        assumptions = ("comparator assumed from the unit: a duration or cost target is a ceiling",)
    else:
        comparator = ">="
        assumptions = ("comparator assumed: a bare target is read as a floor",)

    if unit is None:
        # A bare decimal with no unit — a score such as a reciprocal rank. Left as a
        # score rather than guessed into a ratio, because 0.85 and 85% are only the same
        # thing if someone says so.
        unit = "ratio" if value <= 1 else "none"
        if unit == "ratio":
            assumptions += ("unit assumed ratio: a bare value at or below 1",)

    return Target(comparator, value, None, unit, _direction_for(comparator), assumptions)


def _looks_like_band(lowered: str, match: re.Match) -> bool:
    """Guard against reading "under 3 seconds" or a hyphenated word as a range.

    A band needs a separator that is actually separating two numbers, so the text
    between them must be a connector and nothing else.
    """
    between = lowered[match.end(1):match.start(3)]
    return bool(re.fullmatch(rf"\s*[%a-z]*\s*{_BAND}\s*", between))


def normalise_metric_name(label: str) -> str:
    """A stable, comparable metric name from a human label.

    `Overall team accuracy` and `team_accuracy` have to meet somewhere, since one is in
    the spec and the other is in the run file. Lowercase, non-alphanumerics to
    underscores, and a few leading adjectives dropped.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", str(label).strip().lower()).strip("_")
    slug = re.sub(r"^(overall|total|average|mean)_", "", slug)
    return re.sub(r"_+", "_", slug)
