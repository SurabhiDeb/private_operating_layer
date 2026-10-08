"""An enforcement scan may be unable to tell which run set a gate checks

Adds `undetermined` to `enforcement_fact.scope`.

A static scan reads the obvious selectors — a `[-1]`, a `sorted(...)[-1]`, an iteration
over every run — and sometimes reads none of them. Before this, such a file had to be
recorded as one of the three confident states: `all_runs`, which asserts full coverage and
hides exactly the gap condition 1 is about, or `latest_only`, which manufactures a finding
that may not exist. Neither is true, so there is now a fourth value that is.

Revision ID: a1c3e7f90b22
Revises: e56fae9886ab
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a1c3e7f90b22"
down_revision: Union[str, Sequence[str], None] = "e56fae9886ab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = "('all_runs', 'latest_only', 'latest_shipped')"
_NEW = "('all_runs', 'latest_only', 'latest_shipped', 'undetermined')"


def upgrade() -> None:
    op.drop_constraint("ck_enforcement_scope", "enforcement_fact", type_="check")
    op.create_check_constraint("ck_enforcement_scope", "enforcement_fact", f"scope IN {_NEW}")


def downgrade() -> None:
    # Anything recorded as undetermined cannot be represented by the narrower vocabulary,
    # and guessing a value for it on the way down would invent an enforcement claim.
    op.execute("DELETE FROM enforcement_fact WHERE scope = 'undetermined'")
    op.drop_constraint("ck_enforcement_scope", "enforcement_fact", type_="check")
    op.create_check_constraint("ck_enforcement_scope", "enforcement_fact", f"scope IN {_OLD}")
