from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector
from core.database import Base
from datetime import datetime, timezone
import uuid

class Entity(Base):
    __tablename__ = "entities"

    id = Column(Integer, primary_key=True)
    org_id = Column(UUID(as_uuid=True), nullable=False)
    entity_type = Column(String(50), nullable=False)
    name = Column(String(255))
    content = Column(Text, nullable=False)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=True)
    approved_by = Column(String(255))
    version = Column(Integer, default=1)
    status = Column(String(20), default="active", nullable=False)          # NEW
    superseded_at = Column(DateTime(timezone=True), nullable=True)
    superseded_by_id = Column(Integer, ForeignKey("entities.id"), nullable=True)  # NEW
    embedding = Column(Vector(1536), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))