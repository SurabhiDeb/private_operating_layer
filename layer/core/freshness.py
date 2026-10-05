"""How old is too old, and what to say when it is.

A scheduled pull that dies raises nothing. Observations simply stop arriving, and a Layer
that reads "the latest observation" keeps answering `met` with full confidence from a
three week old number. Nothing is broken, nothing is logged as wrong, and every answer
over that window is confidently false. PRD B5 item 10 ranks that the worst output this
system can produce, because it is indistinguishable from good news.

**Two different ages are measured here, and conflating them is the easy mistake.**

*A measurement's age* is `now - observation.measured_at`. It decides a verdict: outside
the window a `met` or `missed` degrades to `cannot_confirm` (B3 rule 12, AC-28). The
question is "how old is the number this answer rests on".

*A source's age* is `now - source.last_sync_at`. It decides `source.status`, and a source
that has never delivered is judged from when it was bound instead, because it has owed
data since then. The question is "has the pipe stopped".

They are usually close and they are not the same: a source that synced ten minutes ago and
found nothing new leaves a fresh source and a stale measurement. Both are reported.

**A window nobody set degrades nothing.** `freshness_window` is null until a human states
a cadence, and PRD B2 is explicit that staleness is then reported without a verdict being
degraded by a window nobody set. The alternative — a global default — is how every product
on a quarterly review cycle would wake up to a wall of `cannot_confirm`, and the Layer
would have invented the policy rather than been told it.

**Degrade, never freeze.** An absent measurement is not a passing one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from layer.core.durations import approximate_duration, format_duration
from layer.db.models import Product, Source

HEALTHY = "healthy"
OVERDUE = "overdue"
FAILING = "failing"
PAUSED = "paused"

#: Statuses a human or a failure set deliberately, which this module never overwrites. A
#: paused source is paused on purpose and a failing one has an error recorded against it;
#: silently relabelling either as `overdue` would lose the reason somebody can act on.
NOT_OURS = frozenset({FAILING, PAUSED})


@dataclass(frozen=True)
class SourceWindow:
    """One source's cadence, and when it last delivered."""

    source_id: uuid.UUID
    role: str
    kind: str
    window: timedelta | None
    last_sync_at: datetime | None
    created_at: datetime
    status: str = HEALTHY

    @property
    def label(self) -> str:
        """How the source is named in prose. AC-29 wants the source, not an id."""
        return f"{self.role} source ({self.kind})"

    @property
    def owing_since(self) -> datetime:
        """The point data was last known to arrive, or when the source was bound.

        A source that has never synced is judged from `created_at`: it has owed data
        since somebody bound it, and treating "never" as "just now" would make a dead
        pipe look healthy for one whole window.
        """
        return self.last_sync_at or self.created_at

    def overdue_by(self, now: datetime) -> timedelta | None:
        if self.window is None:
            return None
        late = (now - self.owing_since) - self.window
        return late if late > timedelta(0) else None

    def overdue_since(self, now: datetime) -> datetime | None:
        """The moment it became overdue, which is not the moment it was noticed."""
        if self.overdue_by(now) is None:
            return None
        return self.owing_since + (self.window or timedelta(0))

    def as_stale_source(self, now: datetime) -> dict:
        """PRD B1's `stale_sources` entry, with the age in words as well as in a field."""
        late = self.overdue_by(now)
        return {
            "source_id": str(self.source_id),
            "role": self.role,
            "kind": self.kind,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "overdue_by": approximate_duration(late) if late else None,
            "window": format_duration(self.window) if self.window else None,
            "note": self.describe(now),
        }

    def describe(self, now: datetime) -> str:
        late = self.overdue_by(now)
        if late is None:
            return f"the {self.label} is inside its window"
        # The window is exact because a human typed it and will check it; the ages are
        # approximate because they never land on a round number and an exact one reads
        # as `2591999s`. See `approximate_duration`.
        if self.last_sync_at is None:
            return (
                f"the {self.label} has never reported since it was bound, which is "
                f"{approximate_duration(late)} past its "
                f"{format_duration(self.window)} window"
            )
        return (
            f"the {self.label} last synced {approximate_duration(now - self.last_sync_at)} "
            f"ago, {approximate_duration(late)} past its "
            f"{format_duration(self.window)} window"
        )


@dataclass(frozen=True)
class Staleness:
    """How old one measurement is against the window it had to arrive in.

    Built even when the measurement is fresh, so a caller can state the age either way
    rather than only when something is wrong.
    """

    age: timedelta
    window: timedelta
    measured_at: datetime
    source: str | None = None

    @property
    def stale(self) -> bool:
        return self.age > self.window

    def describe(self) -> str:
        """The staleness as a sentence of its own, which is what AC-29 asks for.

        Names the source, the age and the window. Carefully not a cause: it says the
        measurement is old, never that anything happened to the product (B3 rule 6).
        """
        where = f" from the {self.source}" if self.source else ""
        if not self.stale:
            return (
                f"this rests on a measurement{where} taken "
                f"{approximate_duration(self.age)} ago, inside its "
                f"{format_duration(self.window)} window"
            )
        return (
            f"this rests on a measurement{where} taken "
            f"{approximate_duration(self.age)} ago, outside its "
            f"{format_duration(self.window)} window, so it is shown with its age rather "
            f"than as current"
        )

    def phrase(self) -> str:
        """The same fact as a clause that fits inside somebody else's sentence.

        Two forms because one of them read badly in real output: "PD-8.8 was last
        measured in run X, and this rests on a measurement taken 17d ago…" is two
        sentences wearing one. Found by printing a finding.
        """
        where = f" on the {self.source}" if self.source else ""
        return (
            f"that run is {approximate_duration(self.age)} old against a "
            f"{format_duration(self.window)} window{where}"
        )

    def as_dict(self) -> dict:
        return {
            "age": approximate_duration(self.age),
            "window": format_duration(self.window),
            "as_of": self.measured_at.isoformat(),
            "source": self.source,
            "stale": self.stale,
        }


def staleness_of(
    measured_at: datetime | None,
    *,
    window: timedelta | None,
    now: datetime,
    source: str | None = None,
) -> Staleness | None:
    """None where there is nothing to judge: no measurement, or no stated cadence."""
    if measured_at is None or window is None:
        return None
    return Staleness(
        age=now - measured_at, window=window, measured_at=measured_at, source=source
    )


def windows_for(session: Session, product_id: uuid.UUID) -> dict[uuid.UUID, SourceWindow]:
    """Every source of one product, keyed by id so an observation can find its own."""
    return {
        row.id: SourceWindow(
            source_id=row.id,
            role=row.role,
            kind=row.kind,
            window=row.freshness_window,
            last_sync_at=row.last_sync_at,
            created_at=row.created_at,
            status=row.status,
        )
        for row in session.execute(
            select(Source).where(Source.product_id == product_id)
        ).scalars()
    }


def stale_sources(
    session: Session, *, product: Product, now: datetime | None = None
) -> list[dict]:
    """PRD B1's `stale_sources`, derived rather than read off `status`.

    Derived on purpose. The column is a cache that something has to refresh, and an
    answer that trusted it would read "nothing is stale" for as long as nobody ran the
    refresh — which is the same silence this whole module exists to break.
    """
    at = now or clock(session)
    return [
        window.as_stale_source(at)
        for window in sorted(
            windows_for(session, product.id).values(), key=lambda w: (w.role, w.kind)
        )
        if window.overdue_by(at) is not None
    ]


def refresh(
    session: Session, *, product: Product, now: datetime | None = None
) -> dict[str, int]:
    """Bring `source.status` and `overdue_since` in line with the clock.

    Called where the Layer reassesses what it knows — the measure pass — and from the
    operator's own command, never from a query. A read path that wrote would make
    `layer findings` a mutation, and two of them racing would each think it was the one
    that noticed.

    `failing` and `paused` are left alone: see `NOT_OURS`.
    """
    at = now or clock(session)
    counts: dict[str, int] = {}
    for row in session.execute(
        select(Source).where(Source.product_id == product.id)
    ).scalars():
        if row.status in NOT_OURS:
            counts[row.status] = counts.get(row.status, 0) + 1
            continue
        window = SourceWindow(
            source_id=row.id, role=row.role, kind=row.kind,
            window=row.freshness_window, last_sync_at=row.last_sync_at,
            created_at=row.created_at, status=row.status,
        )
        since = window.overdue_since(at)
        if since is None:
            row.status, row.overdue_since = HEALTHY, None
        else:
            # The CHECK requires both of these together, and so does the output: an
            # overdue source with no date cannot say how long it has been overdue.
            row.status, row.overdue_since = OVERDUE, since
        counts[row.status] = counts.get(row.status, 0) + 1
    return counts


def mark_synced(
    session: Session, *, source: Source, now: datetime | None = None
) -> None:
    """Record that this source just delivered.

    Called after an import that read the source, whatever it found. Finding nothing new
    is still the pipe working, and it is the pipe this stamp is about — a source that
    only counted as synced when a run appeared would report a dead pipe every quiet
    night.
    """
    source.last_sync_at = now or clock(session)
    source.overdue_since = None
    if source.status not in NOT_OURS:
        source.status = HEALTHY


def clock(session: Session) -> datetime:
    """The database's clock, which is where every timestamp compared against it came from."""
    return session.execute(select(func.now())).scalar_one()
