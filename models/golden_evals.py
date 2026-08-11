"""
Golden evaluation dataset.

Each row is a known Q&A pair the system should always get right.
After each eval run, last_score is updated: 1.0 = correct, 0.0 = wrong.
"""

from sqlalchemy import Column, Integer, String, Text, Float, DateTime
from sqlalchemy.dialects.postgresql import UUID
from core.database import Base


class GoldenEval(Base):
    __tablename__ = "golden_evals"

    id = Column(Integer, primary_key=True)
    org_id = Column(UUID(as_uuid=True), nullable=False)
    question = Column(Text, nullable=False)
    expected_answer = Column(Text, nullable=False)  # what the correct answer should contain
    last_score = Column(Float, nullable=True)        # 1.0 = pass, 0.0 = fail
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    last_actual_answer = Column(Text, nullable=True) # what the system actually said last time