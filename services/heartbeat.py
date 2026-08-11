"""
Dead man's switch — heartbeat service.

Every background job calls beat() when it finishes a cycle.
The watchdog (scripts/watchdog.py) calls check_all() every 5 minutes
to detect processes that have gone silent.
"""

from datetime import datetime, timedelta, timezone
from core.database import SessionLocal
from models.process_heartbeat import ProcessHeartbeat


# ─── Process registry ──────────────────────────────────────────────────────────
# Each entry defines how often (in minutes) that process is expected to report in.
# Overdue grace = 1.5× the interval, so a 30-min job can be up to 45 min late
# before it triggers an alert.
PROCESS_INTERVALS = {
    "daily_briefings":   1440,   # once a day
    "daily_ingestion":   1440,   # once a day
    "micro_batch":         30,   # every 30 min
    "backfill_processor":   2,   # every 2 min
    "evals":            43200,   # monthly (30 days)
    "slack_health":      1440,   # once a day
}


def beat(process_name: str, status: str = "ok", error: str = None) -> None:
    """
    Record a heartbeat for a background process.

    Call this at the END of every job cycle — success or failure.
    Pass status="error" and the exception message if the job failed.

    Example:
        try:
            do_work()
            beat("micro_batch")
        except Exception as e:
            beat("micro_batch", status="error", error=str(e))
            raise
    """
    interval = PROCESS_INTERVALS.get(process_name)
    if interval is None:
        raise ValueError(
            f"Unknown process '{process_name}'. "
            f"Register it in services/heartbeat.PROCESS_INTERVALS first."
        )

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        existing = db.query(ProcessHeartbeat).filter_by(process_name=process_name).first()
        if existing:
            existing.last_beat_at = now
            existing.last_status = status
            existing.last_error = error
            existing.expected_interval_minutes = interval
            existing.updated_at = now
        else:
            db.add(ProcessHeartbeat(
                process_name=process_name,
                last_beat_at=now,
                last_status=status,
                last_error=error,
                expected_interval_minutes=interval,
            ))
        db.commit()
    finally:
        db.close()


def check_all() -> list[dict]:
    """
    Return the current health status of every registered process.
    Used by /health and scripts/watchdog.py.

    A process is 'overdue' if it hasn't reported within 1.5× its expected interval.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        rows = {r.process_name: r for r in db.query(ProcessHeartbeat).all()}
        results = []

        for name, interval in PROCESS_INTERVALS.items():
            row = rows.get(name)
            if row is None or row.last_beat_at is None:
                results.append({
                    "process": name,
                    "last_beat_at": None,
                    "last_status": "never_run",
                    "last_error": None,
                    "expected_interval_minutes": interval,
                    "overdue": True,
                    "overdue_by_minutes": None,
                })
                continue

            deadline = row.last_beat_at + timedelta(minutes=interval * 1.5)
            overdue = now > deadline
            overdue_by = int((now - deadline).total_seconds() / 60) if overdue else None

            results.append({
                "process": name,
                "last_beat_at": row.last_beat_at.isoformat(),
                "last_status": row.last_status,
                "last_error": row.last_error,
                "expected_interval_minutes": interval,
                "overdue": overdue,
                "overdue_by_minutes": overdue_by,
            })

        return results
    finally:
        db.close()
