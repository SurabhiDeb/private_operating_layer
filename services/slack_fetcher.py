import httpx
from datetime import datetime, timezone, timedelta
from core.config import settings

SLACK_API = "https://slack.com/api"

def get_headers():
    return {"Authorization": f"Bearer {settings.slack_bot_token}"}

def build_slack_permalink(workspace_domain: str, channel_id: str, ts: str) -> str:
    """
    Construct a Slack message permalink without an API call.
    ts looks like "1683456789.123456" — remove the dot to get the URL format.
    workspace_domain can be "mycompany" or "mycompany.slack.com" — both work.
    """
    domain = workspace_domain.replace(".slack.com", "")
    ts_clean = ts.replace(".", "")
    return f"https://{domain}.slack.com/archives/{channel_id}/p{ts_clean}"

def get_channels() -> list:
    res = httpx.get(f"{SLACK_API}/conversations.list", headers=get_headers(), params={
        "types": "public_channel,private_channel",
        "limit": 100,
    })
    data = res.json()
    if not data.get("ok"):
        raise Exception(f"Slack error: {data.get('error')}")
    return [
        {
            "id": ch["id"],
            "name": ch["name"],
            "is_private": ch.get("is_private", False),
        }
        for ch in data.get("channels", [])
    ]

def get_messages(channel_id: str, days_back: int = 180, workspace_domain: str = None) -> list:
    oldest = (datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp()
    messages = []
    cursor = None

    while True:
        params = {
            "channel": channel_id,
            "oldest": oldest,
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor

        res = httpx.get(f"{SLACK_API}/conversations.history", headers=get_headers(), params=params)
        data = res.json()

        if not data.get("ok"):
            break

        for msg in data.get("messages", []):
            if msg.get("type") == "message" and msg.get("text"):
                messages.append({
                    "content": msg["text"],
                    "timestamp": datetime.fromtimestamp(float(msg["ts"]), tz=timezone.utc),
                    "permalink": build_slack_permalink(workspace_domain, channel_id, msg["ts"]) if workspace_domain else msg.get("ts"),
                    "metadata": {
                        "channel_id": channel_id,
                        "user": msg.get("user", ""),
                        "ts": msg.get("ts"),
                    }
                })

        next_cursor = data.get("response_metadata", {}).get("next_cursor")
        if not next_cursor:
            break
        cursor = next_cursor

    return messages

def get_new_messages(channel_id: str, since_hours: int = 24) -> list:
    return get_messages(channel_id, days_back=since_hours / 24)

def get_channel_members(channel_id: str) -> list:
    """Fetch all member user IDs for a private channel."""
    members = []
    cursor = None

    while True:
        params = {"channel": channel_id, "limit": 200}
        if cursor:
            params["cursor"] = cursor

        res = httpx.get(f"{SLACK_API}/conversations.members", headers=get_headers(), params=params)
        data = res.json()

        if not data.get("ok"):
            break

        members.extend(data.get("members", []))

        next_cursor = data.get("response_metadata", {}).get("next_cursor")
        if not next_cursor:
            break
        cursor = next_cursor

    return members