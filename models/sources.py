from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from core.database import Base
from datetime import datetime, timezone

class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True)
    org_id = Column(UUID(as_uuid=True), nullable=False)
    source_type = Column(String(50), nullable=False)  # slack, gmail, calendar
    raw_content = Column(Text, nullable=False)
    source_metadata = Column(JSONB, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=True)
    permalink = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    channel_id = Column(String(255), nullable=True)
    channel_is_private = Column(Boolean, default=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)