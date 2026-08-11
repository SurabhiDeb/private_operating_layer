from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from core.database import get_db
from services.compile_context import compile_context
from models.golden_evals import GoldenEval
from services.evaluator import run_evals_for_org
from pydantic import BaseModel
from models.entities import Entity as EntityModel

router = APIRouter(prefix="/context", tags=["context"])

@router.get("/{org_id}")
def get_context(org_id: str, db: Session = Depends(get_db)):
    try:
        snapshot = compile_context(org_id, db)
        return {"org_id": org_id, "context": snapshot}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/usage/{org_id}")
def get_token_usage(org_id: str, db: Session = Depends(get_db)):
    from services.token_tracker import get_usage_summary
    return get_usage_summary(org_id, db)

class GoldenEvalInput(BaseModel):
    question: str
    expected_answer: str


@router.post("/evals/{org_id}")
def add_golden_eval(org_id: str, body: GoldenEvalInput, db: Session = Depends(get_db)):
    """Add a known Q&A pair to the golden eval dataset for this org."""
    import uuid
    ev = GoldenEval(
        org_id=uuid.UUID(org_id),
        question=body.question,
        expected_answer=body.expected_answer,
    )
    db.add(ev)
    db.commit()
    return {"message": "Golden eval added.", "id": ev.id}


@router.get("/evals/{org_id}")
def get_evals(org_id: str, db: Session = Depends(get_db)):
    """View all golden evals and their last scores for this org."""
    import uuid
    evals = db.query(GoldenEval).filter_by(org_id=uuid.UUID(org_id)).all()
    return [
        {
            "id": e.id,
            "question": e.question,
            "expected_answer": e.expected_answer,
            "last_score": e.last_score,
            "last_run_at": str(e.last_run_at) if e.last_run_at else None,
            "last_actual_answer": e.last_actual_answer,
        }
        for e in evals
    ]


@router.post("/evals/{org_id}/run")
def run_evals(org_id: str, db: Session = Depends(get_db)):
    """Manually trigger an eval run for this org."""
    result = run_evals_for_org(org_id, db)
    return result

@router.get("/pending/{org_id}")
def get_pending(org_id: str, review_type: str = None, db: Session = Depends(get_db)):
    """View pending entities. Pass ?review_type=contradiction to see only conflicts."""
    import uuid
    from models.pending_entities import PendingEntity
    org_uuid = uuid.UUID(org_id)
    query = db.query(PendingEntity).filter_by(org_id=org_uuid)
    if review_type:
        query = query.filter_by(review_type=review_type)
    items = query.order_by(PendingEntity.created_at.desc()).all()
    return [
        {
            "id": i.id,
            "entity_type": i.entity_type,
            "name": i.name,
            "content": i.content,
            "critic_reason": i.critic_reason,
            "confidence_score": i.confidence_score,
            "review_type": i.review_type,
            "created_at": str(i.created_at),
        }
        for i in items
    ]

@router.post("/entities/{org_id}/supersede")
def supersede_entity(org_id: str, old_id: int, new_id: int, db: Session = Depends(get_db)):
    """
    Mark old_id as superseded by new_id.
    Use this when resolving a contradiction from the pending queue.
    """
    import uuid
    from datetime import datetime, timezone

    org_uuid = uuid.UUID(org_id)
    old = db.query(EntityModel).filter_by(id=old_id, org_id=org_uuid).first()
    new = db.query(EntityModel).filter_by(id=new_id, org_id=org_uuid).first()

    if not old or not new:
        return {"error": "One or both entity IDs not found."}

    old.status = "superseded"
    old.superseded_at = datetime.now(timezone.utc)
    old.superseded_by_id = new_id
    new.status = "active"

    db.commit()
    return {
        "message": f"Entity {old_id} superseded by {new_id}.",
        "old_status": old.status,
        "new_status": new.status,
    }

@router.get("/entities/{org_id}")
def get_entities(org_id: str, status: str = "active", db: Session = Depends(get_db)):
    """List entities for this org. Pass ?status=superseded to see old versions."""
    import uuid
    org_uuid = uuid.UUID(org_id)
    entities = db.query(EntityModel).filter_by(org_id=org_uuid, status=status).all()
    return [
        {
            "id": e.id,
            "entity_type": e.entity_type,
            "name": e.name,
            "content": e.content,
            "status": e.status,
            "superseded_by_id": e.superseded_by_id,
            "created_at": str(e.created_at),
        }
        for e in entities
    ]