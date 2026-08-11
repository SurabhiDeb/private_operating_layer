"""
Slack workspace health check.

Calls auth.test to verify the bot token is still valid.
Logs the result to session_logs. If unhealthy, logs a warning so it
shows up in any monitoring dashboard.

Runs daily via the scheduler and is also available on-demand via the API.
"""

import httpx
from datetime import datetime, timezone
from models.session_logs import SessionLog

SLACK_API = "https://slack.com/api"


def check_slack_health(org_id: str, db) -> dict:
    """
    Pings Slack's auth.test endpoint and logs the result.
    Returns a dict with: healthy (bool), workspace, bot_user, error (if any).
    """
    from core.config import settings
    import uuid

    org_uuid = uuid.UUID(org_id)

    try:
        res = httpx.post(
            f"{SLACK_API}/auth.test",
            headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
            timeout=10,
        )
        data = res.json()

        if data.get("ok"):
            result = {
                "healthy": True,
                "workspace": data.get("team"),
                "bot_user": data.get("user"),
                "error": None,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            log_content = f"Slack health OK. Workspace: {result['workspace']}, Bot: {result['bot_user']}"
        else:
            result = {
                "healthy": False,
                "workspace": None,
                "bot_user": None,
                "error": data.get("error", "unknown_error"),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            log_content = f"Slack health FAILED. Error: {result['error']}. Token may be revoked — check Slack app settings."

    except Exception as e:
        result = {
            "healthy": False,
            "workspace": None,
            "bot_user": None,
            "error": str(e),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        log_content = f"Slack health check ERROR: {str(e)}"

    log = SessionLog(
        org_id=org_uuid,
        log_type="slack_health",
        content=log_content,
    )
    db.add(log)
    db.commit()

    print(f"[SlackHealth] Org {org_id}: {log_content}")
    return result


def run_all_health_checks():
    """Called by the daily scheduler job."""
    from core.database import SessionLocal
    from models.business_profile import BusinessProfile

    db = SessionLocal()
    try:
        profiles = db.query(BusinessProfile).all()
        for profile in profiles:
            check_slack_health(str(profile.org_id), db)
    finally:
        db.close()