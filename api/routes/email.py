"""
Mailgun inbound email webhook.

Clients BCC their org's inbound address (e.g. 6592570d@inbound.earlyecho.com)
on any email they want captured. Mailgun receives it and POSTs here.

We verify the webhook signature, find the org from the recipient address,
store the email as a source, and run it through the pipeline.
"""

import hashlib
import hmac
from fastapi import APIRouter, Form, HTTPException, Depends
from sqlalchemy.orm import Session
from core.config import settings
from core.database import get_db
from models.business_profile import BusinessProfile
from models.sources import Source
from ingestion.pipeline import run_pipeline
from datetime import datetime, timezone
import uuid as uuid_lib

router = APIRouter(prefix="/email", tags=["email"])


def _verify_mailgun_signature(timestamp: str, token: str, signature: str) -> bool:
    """
    Mailgun signs every webhook with HMAC-SHA256.
    We verify it to make sure the request is actually from Mailgun.
    """
    value = f"{timestamp}{token}".encode("utf-8")
    key = settings.mailgun_signing_key.encode("utf-8")
    expected = hmac.new(key, value, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/inbound")
def receive_email(
    recipient: str = Form(...),
    sender: str = Form(...),
    subject: str = Form(default=""),
    body_plain: str = Form(default="", alias="body-plain"),
    stripped_text: str = Form(default="", alias="stripped-text"),
    timestamp: str = Form(default=""),
    token: str = Form(default=""),
    signature: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """
    Mailgun calls this endpoint when an email arrives at *@inbound.earlyecho.com.
    """
    # Verify the request is genuinely from Mailgun
    if settings.mailgun_signing_key:
        if not _verify_mailgun_signature(timestamp, token, signature):
            raise HTTPException(status_code=403, detail="Invalid Mailgun signature.")

    # Extract org identifier from the recipient address
    # Format: {org_id_prefix}@inbound.earlyecho.com
    local_part = recipient.split("@")[0].strip().lower()

    # Find the org — try matching org_id prefix or email_alias
    org = None
    profiles = db.query(BusinessProfile).all()
    for profile in profiles:
        org_id_short = str(profile.org_id).split("-")[0]  # first segment of UUID
        if local_part == org_id_short or local_part == str(profile.org_id):
            org = profile
            break

    if not org:
        # Unknown recipient — log and ignore rather than error
        print(f"[EmailWebhook] No org found for recipient: {recipient}")
        return {"status": "ignored", "reason": "unknown recipient"}

    org_id = str(org.org_id)
    org_uuid = org.org_id

    # Use stripped text (removes quoted replies) if available, otherwise full body
    content = stripped_text.strip() if stripped_text.strip() else body_plain.strip()

    if not content:
        return {"status": "ignored", "reason": "empty body"}

    # Format the email as a readable block for the pipeline
    raw_content = f"Email from: {sender}\nSubject: {subject}\n\n{content}"

    # Store as a source
    source = Source(
        org_id=org_uuid,
        source_type="email",
        raw_content=raw_content,
        timestamp=datetime.now(timezone.utc),
        source_metadata={
            "sender": sender,
            "recipient": recipient,
            "subject": subject,
        },
    )
    db.add(source)
    db.flush()

    # Run through the extraction pipeline
    result = run_pipeline(
        org_id=org_id,
        source_id=source.id,
        raw_content=raw_content,
        db=db,
    )

    db.commit()
    print(f"[EmailWebhook] Processed email for org {org_id}: {result}")
    return {"status": "processed", "pipeline_result": result}


@router.get("/address/{org_id}")
def get_inbound_address(org_id: str):
    """Returns the BCC address for this org."""
    short_id = org_id.split("-")[0]
    return {
        "bcc_address": f"{short_id}@{settings.mailgun_domain}",
        "instructions": f"BCC {short_id}@{settings.mailgun_domain} on any email you want captured in your AI memory."
    }