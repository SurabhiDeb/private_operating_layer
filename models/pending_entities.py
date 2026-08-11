from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Float
from sqlalchemy.dialects.postgresql import UUID
from core.database import Base
from datetime import datetime, timezone

class PendingEntity(Base):
    __tablename__ = "pending_entities"
    id = Column(Integer, primary_key=True)
    org_id = Column(UUID(as_uuid=True), nullable=False)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=True)
    entity_type = Column(String(50), nullable=False)
    name = Column(String(255))
    content = Column(Text, nullable=False)
    critic_reason = Column(Text)
    confidence_score = Column(Float, nullable=True)        # NEW: 0.0–1.0
    review_type = Column(String(50), default="standard")   # NEW: "standard" or "contradiction"
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))