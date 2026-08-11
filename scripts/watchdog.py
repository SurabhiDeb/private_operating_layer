#!/usr/bin/env python3
"""
EarlyEcho Watchdog — dead man's switch.

Run every 5 minutes via systemd timer (see deploy/earlyecho-watchdog.timer).
Exits non-zero if any process is overdue, which systemd records as a failure
and can optionally trigger an OnFailure= alert unit.

To add real alerting: fill in the alert() function below.
Common options:
  - Slack webhook: requests.post(SLACK_WEBHOOK_URL, json={"text": message})
  - Email via Mailgun API
  - PagerDuty event
"""

import os
import sys

# Allow running from the scripts/ directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from services.heartbeat import check_all


# ─── Alert channel ─────────────────────────────────────────────────────────────
def alert(message: str) -> None:
    """
    Send an alert when a process goes overdue.
    Right now this just prints to stderr (systemd captures it in the journal).
    Replace the body with a real notification when you're ready.
    """
    print(f"[ALERT] {message}", file=sys.stderr)

    # Uncomment and fill in when ready:
    # import requests
    # webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    # if webhook_url:
    #     requests.post(webhook_url, json={"text": f":red_circle: EarlyEcho watchdog\n{message}"})


# ─── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    now = datetime.now(timezone.utc).isoformat()
    results = check_all()

    if not results:
        print(f"[Watchdog] {now} — no processes registered yet.")
        return

    overdue = [r for r in results if r["overdue"]]
    ok = [r for r in results if not r["overdue"]]

    print(f"[Watchdog] {now} — {len(ok)} ok, {len(overdue)} overdue")

    for r in overdue:
        by = f"{r['overdue_by_minutes']} min past deadline" if r["overdue_by_minutes"] else "never run"
        msg = (
            f"OVERDUE: {r['process']} | {by} | "
            f"expected every {r['expected_interval_minutes']} min | "
            f"last beat: {r['last_beat_at'] or 'never'} | "
            f"last status: {r['last_status']}"
        )
        if r["last_error"]:
            msg += f" | error: {r['last_error']}"
        alert(msg)

    # Non-zero exit so systemd marks this run as failed
    if overdue:
        sys.exit(1)


if __name__ == "__main__":
    main()
