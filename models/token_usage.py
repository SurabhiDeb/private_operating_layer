from sqlalchemy import Column, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from core.database import Base


class TokenUsage(Base):
    __tablename__ = "token_usage"

    id = Column(Integer, primary_key=True)
    org_id = Column(UUID(as_uuid=True), nullable=False)
    month = Column(String(7), nullable=False)  # Format: "2026-08"
    tokens_used = Column(Integer, default=0, nullable=False)
    budget_limit = Column(Integer, default=500000, nullable=False)  # 500k tokens/month default

    __table_args__ = (
        UniqueConstraint("org_id", "month", name="uq_token_usage_org_month"),
    )