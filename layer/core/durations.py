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

__all__ = ["parse_duration", "format_duration"]

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
    """The shortest exact rendering, for output a human reads back."""
    seconds = int(window.total_seconds())
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0 and seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"
