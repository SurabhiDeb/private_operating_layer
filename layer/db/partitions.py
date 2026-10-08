"""Partitions for `case_result`, emitted as statements rather than executed.

PRD AC-27 requires the case store to be "partitioned by `org_id` and month", and a
tenant-scoped delete to remove that tenant's rows across every partition. Two levels
satisfy that without any DDL per tenant: a monthly `RANGE` partition on `measured_at`,
each one `HASH` split on `org_id`.

**Why that order, and not the other one.** `LIST (org_id)` at the top would give the
cheapest possible erasure — `DROP TABLE` on one tenant's partition — but it needs DDL
every time a tenant is registered, and the application role deliberately does not own
these tables, so registering a tenant would become a migration. Monthly ranges with a
hash beneath them keep the registration path pure DML, keep a tenant delete to one
statement, and still let an expired month be dropped whole.

**The default partition is not laziness.** The application role cannot create a
partition, so a run whose `measured_at` falls outside every declared month has nowhere
to go, and Postgres would reject the insert. Losing evidence because a maintenance job
did not run is worse than a partition with mixed months in it, and AC-2 forbids
omitting a run. So a `DEFAULT` month exists, is itself hash-split, and is where a
backfill of old history lands until someone declares those months.

Nothing here executes anything, for the same reason `layer/db/rls.py` does not: a
migration must stay declarative, and the same statements can then be asserted in a test.
"""

from __future__ import annotations

from datetime import date

__all__ = ["HASH_MODULUS", "month_statements", "default_month_statements",
           "months_between", "drop_month_statements", "partition_name"]

#: How many hash subpartitions each month is split into.
#:
#: Two, deliberately, and it is a tuning number rather than a correctness one: AC-27
#: asks that the store be partitioned by `org_id` and month, which any modulus above
#: one satisfies. It is kept small because every partition is a relation the test
#: harness takes a lock on when it truncates between tests — a modulus of four over a
#: two year window cost 126 relations and roughly 40 seconds across the suite, which
#: buys nothing: per-tenant volume follows eval suite size rather than traffic, so the
#: arithmetic in PRD B2 never needs the extra split. Raising it is one constant and a
#: migration that redeclares the months.
HASH_MODULUS = 2

_TABLE = "case_result"


def partition_name(year: int, month: int) -> str:
    return f"{_TABLE}_{year:04d}_{month:02d}"


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _hash_statements(parent: str) -> list[str]:
    return [
        f"CREATE TABLE {parent}_h{r} PARTITION OF {parent} "
        f"FOR VALUES WITH (MODULUS {HASH_MODULUS}, REMAINDER {r})"
        for r in range(HASH_MODULUS)
    ]


def month_statements(year: int, month: int) -> list[str]:
    """One month, hash-split on `org_id`. Idempotent at the migration level only:
    creating a month that exists is an error, which is correct — it means two
    migrations disagree about the window."""
    parent = partition_name(year, month)
    ny, nm = _next_month(year, month)
    return [
        f"CREATE TABLE {parent} PARTITION OF {_TABLE} "
        f"FOR VALUES FROM ('{year:04d}-{month:02d}-01') TO ('{ny:04d}-{nm:02d}-01') "
        f"PARTITION BY HASH (org_id)",
        *_hash_statements(parent),
    ]


def default_month_statements() -> list[str]:
    parent = f"{_TABLE}_default"
    return [
        f"CREATE TABLE {parent} PARTITION OF {_TABLE} DEFAULT PARTITION BY HASH (org_id)",
        *_hash_statements(parent),
    ]


def drop_month_statements(year: int, month: int) -> list[str]:
    """Dropping the month parent takes its hash children with it."""
    return [f"DROP TABLE IF EXISTS {partition_name(year, month)} CASCADE"]


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    """Every (year, month) from `start` to `end` inclusive, by month."""
    out: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append((year, month))
        year, month = _next_month(year, month)
    return out
