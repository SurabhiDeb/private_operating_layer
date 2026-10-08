"""An actor with a role, so a decision can be attributed and bounded

AC-16 records who decided, and the role rules in `models.ROLE_DECIDES` cannot be
enforced against a free-text `--as` string. This table is the precondition for both.

**It is identity, not authentication**, which is PRD B11 rule 7's line and the same line
the phase ordering draws: no password, no session, no token. A write is attributed to an
actor the server cannot verify; verifying it arrives with the HTTP transport in phase 4.

**The table is `actor`, not `user`.** `user` is reserved in SQL. A table by that name
needs quoting at every call site for the rest of the project's life, and the first
unquoted reference is a runtime error rather than a test failure.

**Not append-only**, unlike `audit_event` and `case_result`. A role changes when someone
changes job and an actor is removed when they leave; what must be immutable is the record
of what they decided, which lives in the audit log and already is. Freezing this table
would make the audit log's integrity depend on nobody ever changing teams.

Revision ID: d3f9c25a84e1
Revises: c4e8b1a07f55
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from layer.core.config import settings
from layer.db import rls
from layer.db.models import ACTOR_ROLES

revision: str = "d3f9c25a84e1"
down_revision: Union[str, Sequence[str], None] = "c4e8b1a07f55"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Named explicitly rather than read from the live `TENANT_TABLES`. The note in
#: `layer/db/rls.py` records why: a migration that reads the current tuple describes
#: whatever the model file says today, not what that migration created, and migration
#: 1's downgrade broke exactly that way when migration 2 landed.
NEW_TABLES: tuple[str, ...] = ("actor",)


def upgrade() -> None:
    op.create_table(
        "actor",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("org.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Unique per org and never globally. H11's reasoning about clause refs applies
        # to people: two tenants may employ the same contractor, and neither should
        # learn that from a uniqueness violation.
        sa.UniqueConstraint("org_id", "email", name="uq_actor_org_email"),
        sa.CheckConstraint(f"role IN {ACTOR_ROLES}", name="ck_actor_role"),
    )
    op.create_index("ix_actor_org", "actor", ["org_id"])

    # Isolation and privileges land in the same migration as the table. A window in
    # which a tenant table exists without a policy is a window in which a leak is legal.
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
    op.drop_index("ix_actor_org", table_name="actor")
    op.drop_table("actor")
