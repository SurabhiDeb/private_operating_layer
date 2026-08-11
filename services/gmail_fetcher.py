"""
Gmail fetcher — Internal Workspace Bypass.

Each client creates their own Internal OAuth app in their Google Cloud Console.
They paste their client_id and client_secret into EarlyEcho onboarding.
We use their credentials to read their emails — no Google verification required.
"""

import base64
import email as email_lib
from datetime import datetime, timezone, timedelta
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from sqlalchemy.orm import Session
from models.oauth_integrations import OAuthIntegration
import uuid as uuid_lib

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _get_credentials(org_id: str, db: Session) -> Credentials:
    """
    Loads stored OAuth tokens for this org and refreshes if expired.
    """
    org_uuid = uuid_lib.UUID(org_id)
    record = db.query(OAuthIntegration).filter_by(
        org_id=org_uuid,
        integration_type="gmail"
    ).first()

    if not record or not record.refresh_token:
        raise Exception("Gmail not connected for this org. Complete OAuth first.")

    creds = Credentials(
        token=record.access_token,
        refresh_token=record.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=record.client_id,
        client_secret=record.client_secret,
        scopes=SCOPES,
    )

    # Refresh if expired
    if creds.expired or not creds.valid:
        creds.refresh(Request())
        record.access_token = creds.token
        record.token_expiry = creds.expiry
        db.commit()

    return creds


def _extract_body(msg_payload: dict) -> str:
    """Extracts plain text body from a Gmail message payload."""
    body = ""

    if msg_payload.get("mimeType") == "text/plain":
        data = msg_payload.get("body", {}).get("data", "")
        if data:
            body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    elif "parts" in msg_payload:
        for part in msg_payload["parts"]:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                    break

    return body.strip()


def get_recent_emails(org_id: str, db: Session, since_hours: float = 24) -> list[dict]:
    """
    Fetches emails from the last N hours for this org's connected Gmail.
    Returns list of dicts with: subject, sender, body, timestamp.
    """
    creds = _get_credentials(org_id, db)
    service = build("gmail", "v1", credentials=creds)

    # Gmail query: emails newer than since_hours
    after_ts = int((datetime.now(timezone.utc) - timedelta(hours=since_hours)).timestamp())
    query = f"after:{after_ts} -in:spam -in:trash"

    results = service.users().messages().list(
        userId="me", q=query, maxResults=100
    ).execute()

    messages = results.get("messages", [])
    emails = []

    for msg_ref in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        subject = headers.get("Subject", "(no subject)")
        sender = headers.get("From", "")
        body = _extract_body(msg["payload"])

        if not body:
            continue

        ts_ms = int(msg.get("internalDate", 0))
        timestamp = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

        emails.append({
            "subject": subject,
            "sender": sender,
            "body": body,
            "timestamp": timestamp,
        })

    return emails