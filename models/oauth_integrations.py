"""
Stores per-org OAuth credentials for external integrations (Gmail, etc).
Each client using Internal Workspace Bypass brings their own Google OAuth app,
so we store their client_id, client_secret, and the resulting tokens here.
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from core.database import Base
from datetime import datetime, timezone


class OAuthIntegration(Base):
    __tablename__ = "oauth_integrations"

    id = Column(Integer, primary_key=True)
    org_id = Column(UUID(as_uuid=True), nullable=False)
    integration_type = Column(String(50), nullable=False)   # "gmail"
    client_id = Column(Text, nullable=False)
    client_secret = Column(Text, nullable=False)
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_expiry = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("org_id", "integration_type", name="uq_oauth_org_type"),
    )