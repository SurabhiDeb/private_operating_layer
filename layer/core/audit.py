"""The append-only record of who did what, on what evidence.

PRD B5 ranks a write with no approval record as the first unacceptable failure, and audit
item P6 notes that `approved_by` on a row is not an audit trail. The table refuses UPDATE
and DELETE in the database; this module is the only thing that writes to it, so there is
one place to look for what the Layer considers a write.

`record` takes the session rather than opening its own, so the event and the change it
describes commit or roll back together. An audit log that can survive a failed transaction
is a log of things that did not happen.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from layer.db.models import AuditEvent

#: Actions used by onboarding. Free-form strings rather than an enum, for the same reason
#: `source.kind` is: a new capability should not need a migration to be auditable.
PRODUCT_REGISTERED = "product_registered"
SOURCE_BOUND = "source_bound"
SPEC_IMPORTED = "spec_imported"
CLAUSE_CREATED = "clause_created"
CLAUSE_VERSIONED = "clause_versioned"
OBSERVATIONS_BACKFILLED = "observations_backfilled"
ENFORCEMENT_SCANNED = "enforcement_scanned"
BINDING_CONFIRMED = "binding_confirmed"
BINDING_REJECTED = "binding_rejected"
VERDICTS_COMPUTED = "verdicts_computed"
PRODUCT_STATUS_CHANGED = "product_status_changed"
IMPORT_INCOMPLETE = "import_incomplete"


def record(
    session: Session,
    *,
    org_id: uuid.UUID,
    actor: str,
    action: str,
    subject: str | None = None,
    detail: dict | None = None,
) -> AuditEvent:
    """Append one event. Never updates, never deletes."""
    event = AuditEvent(
        org_id=org_id, actor=actor, action=action, subject=subject, detail=detail or {}
    )
    session.add(event)
    return event
