"""Whether a stated bar is one a build may fail on

The first AC-16 sitting produced 21 proposals and a 47.6% acceptance rate, under B6's
floor. The rate was not the finding. Fifteen of the twenty-one were the same shape — a
stated bar with no CI gate — and the generator drew no distinction between a safety bar
at 99% and a monthly budget that no single CI run can observe at all. Eight of the eleven
rejections were that one gap, and the operator's rule on the day was a single sentence:
gate the bars that are contracts or safety, do not gate diagnostics, bands, harness
latencies or costs. Nothing in the Layer carried that distinction.

**Why it cannot be inferred, which is the part worth recording.** `unit`, `direction` and
`value_high` already separate a band from a threshold, so the band could have been
excluded by rule and that would have answered one of the eight. The other seven are not
reachable that way: a latency bar at `<= 3` and a recall bar at `>= 0.99` are identical
in every column the Layer has, and one is a promise to a customer while the other is a
property of whichever machine ran the suite. The product's owner knows which; the Layer
does not, and the guess is what produced the noise.

**So it is declared, and keyed like a binding rather than stored on a clause.** Clause
rows are versioned — rewording supersedes the row and increments `version` — and a
declaration about a promise has to outlive a rewording of its text. A column on `clause`
would be silently reset to undeclared by the next spec import, which is how the noise
would come back without anyone touching the generator. This is the same reasoning that
put `clause_identity` in its own table.

**There is no `undeclared` value.** An undeclared bar is the absence of a row, exactly as
an unconfirmed binding is. A third enum member would have to be written by the spec
importer onto every clause it reads, which would make the Layer's own default
indistinguishable from the owner's declaration, and the default is the thing that was
wrong.

**`report_only` requires a reason, in the database.** That reason is most of the value:
"a harness number, not a product number" is what stops the same proposal arriving next
month to be re-argued from scratch. A CHECK rather than a convention, because a
convention is what the first sitting had.

Revision ID: b2a845cf1e07
Revises: a9e4c71b2f08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from layer.core.config import settings
from layer.db import rls
from layer.db.models import GATE_INTENTS

revision: str = "b2a845cf1e07"
down_revision: Union[str, Sequence[str], None] = "a9e4c71b2f08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Named explicitly rather than read from the live `TENANT_TABLES`, for the reason
#: recorded in `layer/db/rls.py`: a migration that reads the current tuple describes
#: whatever the model file says today instead of what that migration created.
NEW_TABLES: tuple[str, ...] = ("gate_intent",)


def upgrade() -> None:
    op.create_table(
        "gate_intent",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("org.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("product.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The ref, not the clause id: a declaration outlives any one version of the text.
        sa.Column("clause_ref", sa.String(64), nullable=False),
        sa.Column("intent", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("declared_by", sa.String(255), nullable=False),
        sa.Column(
            "declared_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "org_id", "product_id", "clause_ref", name="uq_gate_intent_clause"
        ),
        sa.CheckConstraint(f"intent IN {GATE_INTENTS}", name="ck_gate_intent"),
        sa.CheckConstraint(
            "intent <> 'report_only' OR reason IS NOT NULL",
            name="ck_gate_intent_report_only_has_reason",
        ),
    )
    op.create_index("ix_gate_intent_product", "gate_intent", ["org_id", "product_id"])

    # Isolation and privileges land with the table. A window in which a tenant table
    # exists without a policy is a window in which a leak is legal.
    role = rls.app_role_from_url(settings.database_url)
    for statement in rls.enable_statements(NEW_TABLES):
        op.execute(statement)
    for statement in rls.grant_statements(role, NEW_TABLES):
        op.execute(statement)


def downgrade() -> None:
    role = rls.app_role_from_url(settings.database_url)
    for statement in rls.revoke_statements(role, NEW_TABLES):
        op.execute(statement)
    for statement in rls.disable_statements(NEW_TABLES):
        op.execute(statement)
    op.drop_index("ix_gate_intent_product", table_name="gate_intent")
    op.drop_table("gate_intent")
