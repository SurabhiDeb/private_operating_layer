"""A proposal the system closed, which is not a proposal anybody decided

H2 and H7/EC-8 both need a proposal to end without a human having decided it: a human
edited the target under it, or retention deleted the evidence behind it. Before this,
`proposal.state` held `open`, `accepted` and `rejected`, and neither case fits.

**Why neither of the two obvious shortcuts works.**

Leaving such a proposal `open` keeps it in the acceptance rate's denominator and holds
`uq_proposal_one_open_per_target` against its own replacement, so the next generator run
cannot propose against the clause a human just edited — the queue silently stops healing.

Marking it `rejected` requires a `decided_by`, because `ck_proposal_decision_record`
requires one for anything not `open`. Putting a name there would forge an approval
record, which is the first item in PRD B5. Putting the system's name there would make
"who decided this" unanswerable by reading the column, which is the one thing the column
is for.

So there are two new states and the constraint grows a third case: `open` has no decider,
`accepted` and `rejected` require one, and `invalidated` and `evidence_expired` require
`decided_by` to be **null** and a reason to be stated. The database now refuses a
system-closed proposal that names a decider, which is the property worth having.

**And it fixes the acceptance rate before it is ever reported.** The rate is
`accepted / (accepted + rejected)`. A proposal nobody decided belongs in neither, so
B6's single most important number does not quietly depend on how much evidence expired.

Revision ID: a9e4c71b2f08
Revises: f1a6b43c0d97
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9e4c71b2f08"
down_revision: Union[str, Sequence[str], None] = "f1a6b43c0d97"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_STATES = "('open', 'accepted', 'rejected')"
_NEW_STATES = "('open', 'accepted', 'rejected', 'invalidated', 'evidence_expired')"

_OLD_RECORD = (
    "(state = 'open' AND decided_by IS NULL AND decided_at IS NULL) OR "
    "(state <> 'open' AND decided_by IS NOT NULL AND decided_at IS NOT NULL)"
)
_NEW_RECORD = (
    # Open: nobody has decided, so no decision record exists.
    "(state = 'open' AND decided_by IS NULL AND decided_at IS NULL) OR "
    # Decided by a person: the approval record is mandatory. B5 item 1.
    "(state IN ('accepted', 'rejected') "
    " AND decided_by IS NOT NULL AND decided_at IS NOT NULL) OR "
    # Closed by the system: no decider, and the reason is stated rather than implied.
    "(state IN ('invalidated', 'evidence_expired') "
    " AND decided_by IS NULL AND decided_at IS NOT NULL AND decision_note IS NOT NULL)"
)


def upgrade() -> None:
    op.drop_constraint("ck_proposal_state", "proposal", type_="check")
    op.create_check_constraint("ck_proposal_state", "proposal", f"state IN {_NEW_STATES}")
    op.drop_constraint("ck_proposal_decision_record", "proposal", type_="check")
    op.create_check_constraint(
        "ck_proposal_decision_record", "proposal", _NEW_RECORD
    )
    # The partial unique index already keys on `state = 'open'`, so a system-closed
    # proposal stops holding its target and the next generator run can propose again.
    # Nothing to change there, and it is named here so a reader does not go looking.


def downgrade() -> None:
    # A system-closed proposal cannot be represented by the narrower vocabulary, and
    # choosing `rejected` for it on the way down would invent a decision and a decider.
    op.execute(
        "DELETE FROM proposal WHERE state IN ('invalidated', 'evidence_expired')"
    )
    op.drop_constraint("ck_proposal_decision_record", "proposal", type_="check")
    op.create_check_constraint(
        "ck_proposal_decision_record", "proposal", _OLD_RECORD
    )
    op.drop_constraint("ck_proposal_state", "proposal", type_="check")
    op.create_check_constraint("ck_proposal_state", "proposal", f"state IN {_OLD_STATES}")
