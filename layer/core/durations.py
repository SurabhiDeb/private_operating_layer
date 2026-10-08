"""Durations written the way a human writes them.

`freshness_window` is a per-source policy a human states at onboarding — "this eval runs
nightly", "this review happens quarterly" — so it has to be typeable on a command line.
Postgres stores an interval and Python holds a `timedelta`; this is the translation
between those and `30d`, `12h`, `90m`.

Deliberately not `dateutil` or an ISO 8601 duration parser. A window is a round number of
minutes, hours, days or weeks in every case the specification gives, and a parser that
accepts `P1Y2M3DT4H` would then have to decide how long a month is — which is exactly the
kind of invented precision the rest of this codebase refuses.
"""

from __future__ import annotations

import re
from datetime import timedelta

__all__ = ["parse_duration", "format_duration", "approximate_duration"]

_UNITS = {
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}

_PATTERN = re.compile(r"^\s*(\d+)\s*([mhdw])\s*$", re.IGNORECASE)


def parse_duration(text: str) -> timedelta:
    """`30d` to a timedelta. Raises rather than guessing.

    A bare number is refused on purpose. `30` could be minutes or days, and a window
    wrong by a factor of 1,440 would either degrade every verdict at once or never
    degrade one at all — both of which read as the Layer being broken rather than as a
    policy somebody set.
    """
    match = _PATTERN.match(text)
    if not match:
        raise ValueError(
            f"cannot read {text!r} as a duration. Use a number and a unit: "
            f"90m, 12h, 30d, 2w"
        )
    amount, unit = int(match.group(1)), match.group(2).lower()
    if amount <= 0:
        raise ValueError(
            f"a duration of {text!r} is not usable: a window of zero would make every "
            f"measurement stale the instant it was taken"
        )
    return timedelta(**{_UNITS[unit]: amount})


def format_duration(window: timedelta) -> str:
    """The shortest exact rendering, for a window a human typed and will read back.

    Exact on purpose: a window is policy, and `30d` shown as "about a month" would stop
    an operator being able to check it against what they set.
    """
    seconds = int(window.total_seconds())
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0 and seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def approximate_duration(age: timedelta) -> str:
    """An age, rounded down to one unit, for prose a human reads once.

    An age is not policy and almost never lands on a round number, so the exact form is
    the wrong tool: a measurement 17 days and 6 hours old came out of `format_duration`
    as `1491958s`, which is accurate and tells a reader nothing. Found by printing a
    finding rather than by a test, which is also why the two functions are now distinct.

    Rounded down rather than to nearest, so the age is never overstated: "17d" for
    anything from 17 days to 17 days and 23 hours.
    """
    seconds = int(age.total_seconds())
    if seconds < 0:
        # A measurement dated in the future. Reported rather than rendered as a negative
        # age: the clocks disagree, and that is the thing worth saying.
        return "a time in the future"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return "under a minute"
