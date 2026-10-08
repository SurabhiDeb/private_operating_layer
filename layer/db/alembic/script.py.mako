## WHAT THIS FILE IS. A blank form, not code. Nothing here ever runs.
## `alembic revision -m "..."` copies this file, fills in every ${...} slot,
## and saves the result into versions/. Every file in versions/ came from here.
##
## upgrade()   moves the database forward. Alembic usually writes this for you
##             by diffing the models against the live database.
## downgrade() puts it back. YOU write this by hand, and skipping it is how a
##             migration quietly becomes one-way.
##
## revision      this migration's own id
## down_revision the id of the one before it
## Those two are the chain Alembic walks to decide what order to run in.
##
## Edit the generated files in versions/, never this one. Change this file only
## when you want EVERY future migration to look different.
##
## These lines are Mako comments (##) so they are stripped at render time and
## never appear in a generated migration. A plain # comment would be copied
## into all of them.

"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """Upgrade schema."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Downgrade schema."""
    ${downgrades if downgrades else "pass"}
