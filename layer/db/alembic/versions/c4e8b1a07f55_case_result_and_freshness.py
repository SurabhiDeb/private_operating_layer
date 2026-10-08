"""The evidence spine, and the freshness columns

Two additions from the 3 October revision, in one migration because they are one claim:
an answer is only trustworthy if it can show its proof and can say how old that proof is.

`case_result` is the per-case layer beneath an observation. An aggregate cannot be
evidence — "missed in 7 of 47 runs, worst 80%" is not derivable from a per-run average —
and naming the cases that are the proof is the Layer's primary job (PRD US-13, AC-22 to
AC-27). It is partitioned by month and hashed on `org_id` per AC-27, append-only like the
audit log, and tenant-isolated by the same policy as every other table.

`source.freshness_window`, `overdue_since` and a closed `status` are the other half. A
scheduled pull that dies raises no error: observations simply stop arriving and a Layer
reading "the latest observation" keeps answering `met` with full confidence from a three
week old number (PRD B3 rule 12, B5 item 10, AC-28 to AC-30). The existing free-text
`status` of `bound` is not a value in the new set, so it is mapped to `healthy` before the
CHECK is added rather than after, which would fail on every existing row.

`product.harvest_cap` arrives with them because it is one column and US-1 is specified
now; nothing reads it until the write half exists.

Revision ID: c4e8b1a07f55
Revises: b7d2f4a16c38
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from layer.core.config import settings
from layer.db import partitions as pt
from layer.db import rls

revision: str = "c4e8b1a07f55"
down_revision: Union[str, Sequence[str], None] = "b7d2f4a16c38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: This migration names its own tables rather than reading the live constant. A
#: migration has to describe the schema as it was when it was written — see the note in
#: `layer/db/rls.py` about migration 1's downgrade breaking when migration 2 landed.
NEW_TABLES: tuple[str, ...] = ("case_result",)

#: The months declared up front. The fixtures' committed history is September and
#: October 2026; the rest is headroom. Anything outside this window lands in the DEFAULT
#: partition rather than being rejected, because losing a run is worse than a partition
#: holding mixed months (AC-2).
#:
#: The window is short on purpose. Declaring months is maintenance by the table owner,
#: not a code change, and every partition is one more relation to lock — two years at a
#: hash modulus of four came to 126 relations and roughly doubled the test suite's
#: runtime, while changing nothing about what the store can answer.
FIRST_MONTH = (2026, 9)
LAST_MONTH = (2027, 2)


def _declared_months() -> list[tuple[int, int]]:
    from datetime import date

    return pt.months_between(date(*FIRST_MONTH, 1), date(*LAST_MONTH, 1))


def upgrade() -> None:
    # -- the freshness half ------------------------------------------------------
    op.add_column("source", sa.Column("freshness_window", sa.Interval(), nullable=True))
    op.add_column(
        "source", sa.Column("overdue_since", sa.DateTime(timezone=True), nullable=True)
    )
    # Before the CHECK, not after: every existing row says `bound`, which is not a value
    # in the new set, and `bound` meant exactly what `healthy` means now.
    op.execute("UPDATE source SET status = 'healthy' WHERE status = 'bound'")
    op.alter_column("source", "status", server_default=sa.text("'healthy'"))
    op.create_check_constraint(
        "ck_source_status",
        "source",
        "status IN ('healthy', 'overdue', 'failing', 'paused')",
    )
    op.create_check_constraint(
        "ck_source_overdue_coherent",
        "source",
        "(status <> 'overdue') OR "
        "(overdue_since IS NOT NULL AND freshness_window IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_source_freshness_positive",
        "source",
        "freshness_window IS NULL OR freshness_window > interval '0'",
    )

    op.add_column(
        "product",
        sa.Column(
            "harvest_cap", sa.Integer(), nullable=False, server_default=sa.text("20")
        ),
    )
    op.create_check_constraint("ck_product_harvest_cap", "product", "harvest_cap > 0")

    # -- the evidence spine -----------------------------------------------------
    # Written as DDL rather than through `op.create_table` because the partitioning
    # clause, the two partition levels and the composite key have to arrive together.
    op.execute(
        """
        CREATE TABLE case_result (
            org_id          uuid        NOT NULL REFERENCES org (id) ON DELETE CASCADE,
            observation_id  bigint      NOT NULL REFERENCES observation (id) ON DELETE CASCADE,
            case_id         varchar(128) NOT NULL,
            measured_at     timestamptz NOT NULL,
            outcome         varchar(16) NOT NULL,
            input_redacted  text,
            trace_id        varchar(128),
            trace_url       text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_case_result
                PRIMARY KEY (org_id, observation_id, case_id, measured_at),
            CONSTRAINT ck_case_outcome
                CHECK (outcome IN ('pass', 'fail', 'error', 'skipped')),
            CONSTRAINT ck_case_input_only_for_failures
                CHECK (input_redacted IS NULL OR outcome IN ('fail', 'error'))
        ) PARTITION BY RANGE (measured_at)
        """
    )
    for year, month in _declared_months():
        for statement in pt.month_statements(year, month):
            op.execute(statement)
    for statement in pt.default_month_statements():
        op.execute(statement)

    op.create_index("ix_case_observation", "case_result", ["org_id", "observation_id"])
    op.create_index("ix_case_id", "case_result", ["org_id", "case_id", "measured_at"])

    # Isolation, privileges and immutability land in the same migration as the table.
    # A window in which a tenant table exists without a policy is a window in which a
    # leak is legal.
    role = rls.app_role_from_url(settings.database_url)
    for statement in rls.enable_statements(NEW_TABLES):
        op.execute(statement)
    for statement in rls.grant_statements(role, NEW_TABLES):
        op.execute(statement)
    for statement in rls.append_only_statements(NEW_TABLES):
        op.execute(statement)


def downgrade() -> None:
    role = rls.app_role_from_url(settings.database_url)
    # `drop_function=False`: `audit_event`'s triggers still use it, and migration 2
    # owns it. Dropping it here fails and rolls the whole downgrade back.
    for statement in rls.drop_append_only_statements(NEW_TABLES, drop_function=False):
        op.execute(statement)
    for statement in rls.revoke_statements(role, NEW_TABLES):
        op.execute(statement)
    for statement in rls.disable_statements(NEW_TABLES):
        op.execute(statement)
    # Dropping the parent takes every month and every hash child with it.
    op.execute("DROP TABLE IF EXISTS case_result CASCADE")

    op.drop_constraint("ck_product_harvest_cap", "product", type_="check")
    op.drop_column("product", "harvest_cap")

    op.drop_constraint("ck_source_freshness_positive", "source", type_="check")
    op.drop_constraint("ck_source_overdue_coherent", "source", type_="check")
    op.drop_constraint("ck_source_status", "source", type_="check")
    # Back to the free-text default this replaced. An `overdue` or `failing` source
    # cannot be represented without the columns, and leaving it saying so while the
    # window it was judged against is gone would be a claim with no basis.
    op.execute("UPDATE source SET status = 'bound' WHERE status IN ('healthy', 'overdue', 'failing')")
    op.alter_column("source", "status", server_default=sa.text("'bound'"))
    op.drop_column("source", "overdue_since")
    op.drop_column("source", "freshness_window")
