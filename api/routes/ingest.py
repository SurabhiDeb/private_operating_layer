from models.pending_entities import PendingEntity
from ingestion.pipeline import run_pipeline
from models.session_logs import SessionLog
from services.embeddings import get_embedding
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from core.database import get_db
from models.sources import Source
from models.entities import Entity
import uuid

router = APIRouter(prefix="/memory", tags=["memory"])

class IngestPayload(BaseModel):
    org_id: str
    source_type: str
    raw_content: str
    entity_type: str
    entity_name: Optional[str] = None
    approved_by: Optional[str] = None
    permalink: Optional[str] = None

class PromotePayload(BaseModel):
    org_id: str
    approved_by: str

@router.post("/ingest")
def ingest_memory(payload: IngestPayload, db: Session = Depends(get_db)):
    org_uuid = uuid.UUID(payload.org_id)

    source = Source(
        org_id=org_uuid,
        source_type=payload.source_type,
        raw_content=payload.raw_content,
        permalink=payload.permalink,
    )
    db.add(source)
    db.flush()

    embedding = get_embedding(payload.raw_content)

    entity = Entity(
        org_id=org_uuid,
        entity_type=payload.entity_type,
        name=payload.entity_name,
        content=payload.raw_content,
        source_id=source.id,
        approved_by=payload.approved_by,
        embedding=embedding,
    )
    db.add(entity)
    db.commit()
    db.refresh(entity)

    return {
        "message": "Memory ingested successfully",
        "entity_id": entity.id,
        "source_id": source.id,
    }

@router.get("/list/{org_id}")
def list_memory(
    org_id: str,
    entity_type: str = None,
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    org_uuid = uuid.UUID(org_id)
    query = (
        db.query(Entity)
        .filter_by(org_id=org_uuid)
        .filter(Entity.superseded_at == None)
        .order_by(Entity.created_at.desc())
    )
    if entity_type:
        query = query.filter(Entity.entity_type == entity_type)
    total = query.count()
    entities = query.offset(offset).limit(limit).all()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "entities": [
            {
                "id": e.id,
                "entity_type": e.entity_type,
                "name": e.name,
                "content": e.content,
                "approved_by": e.approved_by,
                "created_at": str(e.created_at),
            }
            for e in entities
        ]
    }

@router.get("/briefing/{org_id}")
def get_briefing(org_id: str, db: Session = Depends(get_db)):
    org_uuid = uuid.UUID(org_id)
    log = (
        db.query(SessionLog)
        .filter_by(org_id=org_uuid, log_type="daily_briefing")
        .order_by(SessionLog.created_at.desc())
        .first()
    )
    if not log:
        return {"briefing": None, "message": "No briefing yet."}
    return {"briefing": log.content, "created_at": str(log.created_at)}

@router.post("/process/{source_id}")
def process_source(source_id: int, org_id: str, db: Session = Depends(get_db)):
    source = db.query(Source).filter_by(id=source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found.")
    
    result = run_pipeline(
        org_id=org_id,
        source_id=source_id,
        raw_content=source.raw_content,
        db=db,
    )
    return {
        "message": "Pipeline complete.",
        "proposed": result["proposed"],
        "approved": result["approved"],
        "rejected": result["rejected"],
    }
@router.get("/queue/{org_id}")
def get_queue(org_id: str, db: Session = Depends(get_db)):
    org_uuid = uuid.UUID(org_id)
    pending = (
        db.query(PendingEntity)
        .filter_by(org_id=org_uuid)
        .order_by(PendingEntity.created_at.desc())
        .all()
    )
    return {
        "pending": [
            {
                "id": p.id,
                "entity_type": p.entity_type,
                "name": p.name,
                "content": p.content,
                "critic_reason": p.critic_reason,
                "created_at": str(p.created_at),
            }
            for p in pending
        ]
    }


@router.post("/queue/{pending_id}/approve")
def approve_entity(pending_id: int, approved_by: str, db: Session = Depends(get_db)):
    pending = db.query(PendingEntity).filter_by(id=pending_id).first()
    if not pending:
        raise HTTPException(status_code=404, detail="Pending entity not found.")

    embedding = get_embedding(pending.content)
    entity = Entity(
        org_id=pending.org_id,
        entity_type=pending.entity_type,
        name=pending.name,
        content=pending.content,
        source_id=pending.source_id,
        approved_by=approved_by,
        embedding=embedding,
    )
    db.add(entity)
    db.delete(pending)
    db.commit()
    return {"message": "Entity approved and saved to memory."}


@router.delete("/queue/{pending_id}/reject")
def reject_entity(pending_id: int, db: Session = Depends(get_db)):
    pending = db.query(PendingEntity).filter_by(id=pending_id).first()
    if not pending:
        raise HTTPException(status_code=404, detail="Pending entity not found.")
    db.delete(pending)
    db.commit()
    return {"message": "Entity rejected and removed from queue."}

@router.post("/queue/bulk-approve/{org_id}")
def bulk_approve(org_id: str, approved_by: str = "bulk", db: Session = Depends(get_db)):
    """Approve all pending items in the queue for this org in one shot."""
    import uuid
    org_uuid = uuid.UUID(org_id)
    pending_items = db.query(PendingEntity).filter_by(org_id=org_uuid).all()
    count = 0
    for pending in pending_items:
        embedding = get_embedding(pending.content)
        entity = Entity(
            org_id=pending.org_id,
            entity_type=pending.entity_type,
            name=pending.name,
            content=pending.content,
            source_id=pending.source_id,
            approved_by=approved_by,
            embedding=embedding,
        )
        db.add(entity)
        db.delete(pending)
        count += 1
    db.commit()
    return {"message": f"{count} items approved and saved to memory."}


@router.delete("/queue/bulk-reject/{org_id}")
def bulk_reject(org_id: str, db: Session = Depends(get_db)):
    """Reject and delete all pending items in the queue for this org."""
    import uuid
    org_uuid = uuid.UUID(org_id)
    pending_items = db.query(PendingEntity).filter_by(org_id=org_uuid).all()
    count = len(pending_items)
    for pending in pending_items:
        db.delete(pending)
    db.commit()
    return {"message": f"{count} items rejected and removed from queue."}


@router.post("/promote/{source_id}")
def promote_source(source_id: int, payload: PromotePayload, db: Session = Depends(get_db)):
    source = db.query(Source).filter_by(id=source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found.")

    org_uuid = uuid.UUID(payload.org_id)
    embedding = get_embedding(source.raw_content)

    entity = Entity(
        org_id=org_uuid,
        entity_type="Decision",
        name="Promoted from history",
        content=source.raw_content,
        source_id=source.id,
        approved_by=payload.approved_by,
        embedding=embedding,
    )
    db.add(entity)
    db.commit()

    return {"message": "Source promoted to memory successfully."}

@router.get("/contradictions/{org_id}")
def get_contradictions(org_id: str, db: Session = Depends(get_db)):
    org_uuid = uuid.UUID(org_id)
    logs = (
        db.query(SessionLog)
        .filter_by(org_id=org_uuid, log_type="contradiction_flag")
        .order_by(SessionLog.created_at.desc())
        .limit(20)
        .all()
    )
    return {
        "contradictions": [
            {
                "id": l.id,
                "content": l.content,
                "created_at": str(l.created_at),
            }
            for l in logs
        ]
    }