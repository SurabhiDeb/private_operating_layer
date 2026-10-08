"""A binding records the decision, not only the confirmations

Adds `decision` and `decision_note` to `binding`.

Onboarding step 5 presents candidate metric-to-clause bindings and stops until "every
candidate has been confirmed or rejected". A table that could only hold confirmations made
that condition unreachable: a product with one candidate a human declined would wait at the
gate forever, and the declined candidate would be re-proposed at every subsequent import,
turning review into a treadmill. A rejection is a decision and is stored as one.

Revision ID: b7d2f4a16c38
Revises: a1c3e7f90b22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2f4a16c38"
down_revision: Union[str, Sequence[str], None] = "a1c3e7f90b22"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "binding",
        sa.Column(
            "decision", sa.String(length=16), nullable=False, server_default="confirmed"
        ),
    )
    op.add_column("binding", sa.Column("decision_note", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_binding_decision", "binding", "decision IN ('confirmed', 'rejected')"
    )


def downgrade() -> None:
    # A rejection cannot be represented without the column, and keeping the row would turn
    # it into a confirmation — an assertion no human made.
    op.execute("DELETE FROM binding WHERE decision = 'rejected'")
    op.drop_constraint("ck_binding_decision", "binding", type_="check")
    op.drop_column("binding", "decision_note")
    op.drop_column("binding", "decision")
