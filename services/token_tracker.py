import uuid as uuid_lib
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import text
from models.token_usage import TokenUsage


def get_current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def record_token_usage(org_id: str, tokens_used: int, db: Session) -> None:
    """
    Add tokens_used to this org's running total for the current month.
    Creates the row if it doesn't exist yet.
    """
    org_uuid = uuid_lib.UUID(org_id)
    month = get_current_month()

    # Insert row if not exists, otherwise add to existing count
    stmt = pg_insert(TokenUsage).values(
        org_id=org_uuid,
        month=month,
        tokens_used=tokens_used,
        budget_limit=500000,
    ).on_conflict_do_update(
        constraint="uq_token_usage_org_month",
        set_={"tokens_used": TokenUsage.tokens_used + tokens_used}
    )
    db.execute(stmt)
    db.commit()


def is_over_budget(org_id: str, db: Session) -> bool:
    """
    Returns True if this org has exceeded their monthly token budget.
    Call this before running any LLM pipeline step.
    """
    org_uuid = uuid_lib.UUID(org_id)
    month = get_current_month()

    row = db.query(TokenUsage).filter_by(org_id=org_uuid, month=month).first()
    if not row:
        return False
    return row.tokens_used >= row.budget_limit


def get_usage_summary(org_id: str, db: Session) -> dict:
    """Returns current month's token usage and budget for an org."""
    org_uuid = uuid_lib.UUID(org_id)
    month = get_current_month()

    row = db.query(TokenUsage).filter_by(org_id=org_uuid, month=month).first()
    if not row:
        return {"month": month, "tokens_used": 0, "budget_limit": 500000, "over_budget": False}
    return {
        "month": month,
        "tokens_used": row.tokens_used,
        "budget_limit": row.budget_limit,
        "over_budget": row.tokens_used >= row.budget_limit,
    }