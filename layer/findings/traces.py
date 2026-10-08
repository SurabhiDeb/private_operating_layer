"""Whether the conversation behind a case can still be opened.

Three answers, and the third is why this is not a boolean.

**`not_applicable`.** The source records no trace at all. PRD B6 scores that
`not_applicable` and never a silent zero, because a false would read as a Layer that lost
the pointer, and the two states send a reader to completely different places.

**`past_retention`.** The pointer exists and the body behind it is gone. Eval platforms
delete traces on lower tiers, often at 30 to 90 days, and B3 rule 11 forbids presenting a
dead link as live — while dropping the case would lose the outcome the Layer copied
precisely so the finding stays provable (AC-23).

**`available`.** Nothing says the body is gone.

**None of these calls the eval platform.** AC-24 requires the proof "with no log reading
and no live call to the eval platform", so `available` is a claim about the pointer and
the declared window, never about a fetch that just succeeded. The live fetch, and the
sentence it has to print when the body has gone, belong to the tool a human reaches for
when they want to read the conversation itself.

**Retention is per source and lives in `source.config`.** It is a fact about somebody
else's platform and tier, so the Layer is told it rather than knowing it, and adding it
needs no migration (agnosticism rule R3).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer.core.durations import parse_duration
from layer.db.models import Source

AVAILABLE = "available"
PAST_RETENTION = "past_retention"
NOT_APPLICABLE = "not_applicable"

#: The key a source declares its trace retention under, as a duration: `30d`, `12h`.
TRACE_RETENTION = "trace_retention"


def retention_for(session: Session, product_id: uuid.UUID) -> dict[uuid.UUID, timedelta]:
    """Per source, how long that source keeps a trace body.

    Absent means nothing is asserted, so a pointer is reported as available because
    nothing says otherwise. That is the honest default: the alternative is a Layer that
    declares every old trace dead without having been told.

    **Unreadable is treated as absent, not as zero.** A window nobody can parse is
    dropped, because silently reading a typo as "every trace is gone" would retire the
    evidence behind every old finding at once.
    """
    out: dict[uuid.UUID, timedelta] = {}
    for source_id, config in session.execute(
        select(Source.id, Source.config).where(Source.product_id == product_id)
    ).all():
        raw = (config or {}).get(TRACE_RETENTION)
        if not raw:
            continue
        try:
            out[source_id] = parse_duration(str(raw))
        except ValueError:
            continue
    return out


def state_of(
    *,
    trace_id: str | None,
    trace_url: str | None,
    measured_at: datetime,
    retention: timedelta | None,
    now: datetime | None,
) -> str:
    """One of the three states above, for one case."""
    if not (trace_id or trace_url):
        return NOT_APPLICABLE
    if retention is not None and now is not None and now - measured_at > retention:
        return PAST_RETENTION
    return AVAILABLE
