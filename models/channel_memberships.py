from sqlalchemy import Column, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from core.database import Base

class ChannelMembership(Base):
    __tablename__ = "channel_memberships"

    id = Column(Integer, primary_key=True)
    org_id = Column(UUID(as_uuid=True), nullable=False)
    channel_id = Column(String(255), nullable=False)
    user_id = Column(String(255), nullable=False)  # Slack user ID e.g. "U012AB3CD"

    __table_args__ = (
        UniqueConstraint("org_id", "channel_id", "user_id", name="uq_channel_member"),
    )

# What this does: creates a table that records which Slack users are members of which channels. 
# When someone queries the system, we check this table to decide what they're allowed to see.    