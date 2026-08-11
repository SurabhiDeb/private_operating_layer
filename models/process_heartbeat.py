from sqlalchemy import Column, Integer, String, Text, DateTime
from core.database import Base
from datetime import datetime, timezone


class ProcessHeartbeat(Base):
    __tablename__ = "process_heartbeats"

    id = Column(Integer, primary_key=True)
    process_name = Column(String(100), unique=True, nullable=False, index=True)
    last_beat_at = Column(DateTime(timezone=True), nullable=True)
    last_status = Column(String(20), default="ok")   # ok | error
    last_error = Column(Text, nullable=True)
    expected_interval_minutes = Column(Integer, nullable=False)

    # Updated automatically on every beat
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
