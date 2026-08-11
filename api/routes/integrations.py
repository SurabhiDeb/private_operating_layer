from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from pydantic import BaseModel
from core.database import get_db
from services.entity_mapper import import_hubspot
from services.csv_import import import_csv
from services.slack_backfill import backfill_slack, ingest_new_slack_messages
from ingestion.pipeline import run_pipeline
from services.slack_health import check_slack_health
from fastapi.responses import RedirectResponse
from models.oauth_integrations import OAuthIntegration

router = APIRouter(prefix="/integrations", tags=["integrations"])

class SlackBackfillInput(BaseModel):
    org_id: str
    days_back: int = 180

@router.post("/slack/backfill")
def slack_backfill(payload: SlackBackfillInput, db: Session = Depends(get_db)):
    try:
        result = backfill_slack(payload.org_id, db, payload.days_back)
        return {
            "message": "Slack backfill complete.",
            "channels": result["channels"],
            "messages_indexed": result["messages_indexed"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/slack/ingest")
def slack_ingest(org_id: str, db: Session = Depends(get_db)):
    try:
        result = ingest_new_slack_messages(org_id, db)
        return {
            "message": "Slack daily ingestion complete.",
            "channels": result["channels"],
            "messages_processed": result["messages_processed"],
            "entities_proposed": result["entities_proposed"],
            "entities_approved": result["entities_approved"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class HubspotConnectInput(BaseModel):
    org_id: str
    connection_id: str

@router.post("/hubspot/import")
def hubspot_import(payload: HubspotConnectInput, db: Session = Depends(get_db)):
    try:
        result = import_hubspot(payload.org_id, payload.connection_id, db)
        return {
            "message": "HubSpot import complete.",
            "contacts_imported": result["contacts"],
            "deals_imported": result["deals"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/csv/import")
async def csv_import(
    org_id: str = Form(...),
    entity_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    try:
        content = await file.read()
        result = import_csv(org_id, content, entity_type, db)
        return {
            "message": f"CSV import complete.",
            "imported": result["imported"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/document/upload")
async def document_upload(
    org_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Accepts a PDF, Excel, or Word document, extracts its text,
    and runs it through the full ingestion pipeline (extract, critic, approval queue).
    """
    try:
        from ingestion.parsers.router import parse_document
        from models.sources import Source
        import uuid as uuid_lib

        file_bytes = await file.read()
        extracted_text = parse_document(file_bytes, file.filename)

        if not extracted_text.strip():
            raise HTTPException(status_code=400, detail="No text could be extracted from this file.")

        org_uuid = uuid_lib.UUID(org_id)
        source = Source(
            org_id=org_uuid,
            source_type="document",
            raw_content=extracted_text,
            source_metadata={"filename": file.filename},
        )
        db.add(source)
        db.flush()

        result = run_pipeline(
            org_id=org_id,
            source_id=source.id,
            raw_content=extracted_text,
            db=db,
        )

        return {
            "message": "Document processed successfully.",
            "filename": file.filename,
            "characters_extracted": len(extracted_text),
            "entities_proposed": result["proposed"],
            "entities_approved": result["approved"],
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/slack/backfill/status/{org_id}")
def backfill_status(org_id: str, db: Session = Depends(get_db)):
    """Shows how many backfilled sources are processed vs still pending."""
    import uuid as uuid_lib
    org_uuid = uuid_lib.UUID(org_id)

    from models.sources import Source
    total = db.query(Source).filter_by(org_id=org_uuid, source_type="slack_history").count()
    processed = db.query(Source).filter(
        Source.org_id == org_uuid,
        Source.source_type == "slack_history",
        Source.processed_at != None,
    ).count()

    return {
        "total_backfill_sources": total,
        "processed": processed,
        "remaining": total - processed,
        "percent_complete": round((processed / total * 100), 1) if total > 0 else 100,
    }

@router.get("/slack/health/{org_id}")
def slack_health(org_id: str, db: Session = Depends(get_db)):
    """On-demand Slack health check. Returns token status and workspace info."""
    return check_slack_health(org_id, db)

# ── Gmail (Internal Workspace Bypass) ──────────────────────────────────────

class GmailCredentialsInput(BaseModel):
    client_id: str
    client_secret: str


@router.post("/gmail/credentials/{org_id}")
def save_gmail_credentials(org_id: str, body: GmailCredentialsInput, db: Session = Depends(get_db)):
    """
    Step 1 — Client pastes their Google Cloud OAuth app credentials here.
    They create an Internal app at console.cloud.google.com.
    """
    import uuid as uuid_lib
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    org_uuid = uuid_lib.UUID(org_id)

    stmt = pg_insert(OAuthIntegration).values(
        org_id=org_uuid,
        integration_type="gmail",
        client_id=body.client_id,
        client_secret=body.client_secret,
    ).on_conflict_do_update(
        constraint="uq_oauth_org_type",
        set_={"client_id": body.client_id, "client_secret": body.client_secret}
    )
    db.execute(stmt)
    db.commit()
    return {"message": "Gmail credentials saved. Now visit /integrations/gmail/connect/{org_id} to authorise."}


@router.get("/gmail/connect/{org_id}")
def gmail_connect(org_id: str, db: Session = Depends(get_db)):
    """
    Step 2 — Redirects client to Google's OAuth consent screen using their own credentials.
    """
    import uuid as uuid_lib
    from google_auth_oauthlib.flow import Flow

    org_uuid = uuid_lib.UUID(org_id)
    record = db.query(OAuthIntegration).filter_by(org_id=org_uuid, integration_type="gmail").first()
    if not record:
        raise HTTPException(status_code=404, detail="No Gmail credentials found. POST to /gmail/credentials first.")

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": record.client_id,
                "client_secret": record.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost:8000/integrations/gmail/callback"],
            }
        },
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    flow.redirect_uri = "http://localhost:8000/integrations/gmail/callback"

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=org_id,
        prompt="consent",
    )
    return RedirectResponse(auth_url)


@router.get("/gmail/callback")
def gmail_callback(code: str, state: str, db: Session = Depends(get_db)):
    """
    Step 3 — Google redirects here after the client authorises.
    Exchanges the code for tokens and stores them.
    """
    import uuid as uuid_lib
    from google_auth_oauthlib.flow import Flow

    org_id = state
    org_uuid = uuid_lib.UUID(org_id)
    record = db.query(OAuthIntegration).filter_by(org_id=org_uuid, integration_type="gmail").first()

    if not record:
        raise HTTPException(status_code=404, detail="No Gmail credentials on file for this org.")

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": record.client_id,
                "client_secret": record.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost:8000/integrations/gmail/callback"],
            }
        },
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        state=org_id,
    )
    flow.redirect_uri = "http://localhost:8000/integrations/gmail/callback"
    flow.fetch_token(code=code)

    creds = flow.credentials
    record.access_token = creds.token
    record.refresh_token = creds.token if creds.refresh_token is None else creds.refresh_token
    record.refresh_token = creds.refresh_token
    record.token_expiry = creds.expiry
    db.commit()

    return {"message": "Gmail connected successfully. You can now trigger a backfill."}


@router.post("/gmail/backfill/{org_id}")
def gmail_backfill(org_id: str, since_hours: float = 720, db: Session = Depends(get_db)):
    """
    Step 4 — Fetches emails and runs them through the pipeline.
    Default: last 720 hours (30 days). Pass since_hours to adjust.
    """
    from services.gmail_fetcher import get_recent_emails
    from models.sources import Source
    import uuid as uuid_lib

    org_uuid = uuid_lib.UUID(org_id)
    emails = get_recent_emails(org_id, db, since_hours=since_hours)

    processed = 0
    for em in emails:
        raw_content = f"Email from: {em['sender']}\nSubject: {em['subject']}\n\n{em['body']}"
        source = Source(
            org_id=org_uuid,
            source_type="email",
            raw_content=raw_content,
            timestamp=em["timestamp"],
            source_metadata={"sender": em["sender"], "subject": em["subject"]},
        )
        db.add(source)
        db.flush()

        run_pipeline(org_id=org_id, source_id=source.id, raw_content=raw_content, db=db)
        processed += 1

    db.commit()
    return {"message": f"Gmail backfill complete.", "emails_processed": processed}


@router.get("/status/{org_id}")
def get_org_status(org_id: str, db: Session = Depends(get_db)):
    """
    Full status summary for the dashboard status card.
    Returns Gmail connection, last sync time, memory count, and pending queue size.
    """
    import uuid
    from models.entities import Entity
    from models.pending_entities import PendingEntity
    from models.process_heartbeat import ProcessHeartbeat

    org_uuid = uuid.UUID(org_id)

    # Gmail — connected if a record exists with an access token
    gmail = db.query(OAuthIntegration).filter_by(
        org_id=org_uuid, integration_type="gmail"
    ).first()
    gmail_connected = gmail is not None and gmail.access_token is not None

    # Last sync — when the micro_batch job last ran successfully
    heartbeat = db.query(ProcessHeartbeat).filter_by(process_name="micro_batch").first()
    last_sync_at = (
        heartbeat.last_beat_at.isoformat()
        if heartbeat and heartbeat.last_beat_at
        else None
    )

    # Total active memories
    total_memories = (
        db.query(Entity)
        .filter_by(org_id=org_uuid)
        .filter(Entity.superseded_at == None)
        .count()
    )

    # Pending review count
    pending_count = db.query(PendingEntity).filter_by(org_id=org_uuid).count()

    return {
        "gmail_connected": gmail_connected,
        "last_sync_at": last_sync_at,
        "total_memories": total_memories,
        "pending_count": pending_count,
    }