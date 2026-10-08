"""A proposal carries its rank, the signals behind it, and the record it describes

AC-34 and AC-35. A queue a human cannot argue with is a queue they will stop reading:
AC-35 requires every candidate to show "its rank and the signals behind it, so a human
can disagree with the ordering rather than only with the cases".

**`rank_signals` is JSONB and not a score breakdown with fixed columns**, because the
signals differ by what produced the candidate. A drift-derived proposal ranks on how much
of the history breaches and by how far; a harvest candidate ranks on US-1's four — cluster
size, clause proximity, novelty, severity. Columns for both would leave half of them null
in every row and imply the two are comparable, which they are not.

**`payload` holds the record a proposal describes but that does not yet exist.** A
`new_clause` proposal's `new_value` cannot hold a whole clause — one text column against
a statement, a metric, a comparator, a value, a unit and a direction — and B4 item 1 is
about one *change* to one field, not about cramming a record into one. It is its own
column rather than a corner of `critic`, because `critic` is the critic's score and a
reader who finds a clause definition in it will mistrust both.

**Nothing here is called importance or severity of impact.** AC-35 forbids presenting the
score as a measure of either, and the column name is the place that mistake gets made once
and then repeated by everything reading it. It is a queue order.

Revision ID: f1a6b43c0d97
Revises: d3f9c25a84e1
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f1a6b43c0d97"
down_revision: Union[str, Sequence[str], None] = "d3f9c25a84e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, and null means unranked rather than last. A proposal an agent created
    # over MCP has no rank: it was not produced by a generator run and did not compete
    # with anything.
    op.add_column("proposal", sa.Column("rank", sa.Integer(), nullable=True))
    op.add_column("proposal", sa.Column("rank_signals", JSONB(), nullable=True))
    op.add_column("proposal", sa.Column("payload", JSONB(), nullable=True))
    op.create_index(
        "ix_proposal_rank", "proposal", ["org_id", "product_id", "rank"],
        postgresql_where=sa.text("state = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("ix_proposal_rank", table_name="proposal")
    op.drop_column("proposal", "payload")
    op.drop_column("proposal", "rank_signals")
    op.drop_column("proposal", "rank")
