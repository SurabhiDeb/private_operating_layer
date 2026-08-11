from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from core.database import Base
from datetime import datetime, timezone
import uuid

class BusinessProfile(Base):
    __tablename__ = "business_profile"

    id = Column(Integer, primary_key=True)
    org_id = Column(UUID(as_uuid=True), unique=True, nullable=False, default=uuid.uuid4)
    business_name = Column(String(255), nullable=False)
    industry = Column(String(255))
    core_services = Column(Text)
    what_they_dont_do = Column(Text)
    team_size = Column(Integer)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # --- Procedural memory layer ---

    # List of team members: [{name, role, email, expertise: []}]
    team_members = Column(JSONB, nullable=True, default=list)

    # Key workflows: [{name, description, steps: [], owner}]
    workflows = Column(JSONB, nullable=True, default=list)

    # Per-entity-type approval config:
    # {entity_type: {auto_approve_above: 90, soft_flag_below: 70}}
    approval_rules = Column(JSONB, nullable=True, default=dict)

    # Slack workspace domain (e.g. "acme") — used for permalink construction
    # Full URL: https://{slack_workspace_domain}.slack.com/archives/{channel_id}/p{ts}
    slack_workspace_domain = Column(String(255), nullable=True)

    # Client-defined briefing focus — injected into the daily briefing prompt.
    # e.g. "Focus on open client decisions, sales pipeline, and any team blockers."
    # If null, the briefing falls back to generic instructions.
    briefing_focus = Column(Text, nullable=True)