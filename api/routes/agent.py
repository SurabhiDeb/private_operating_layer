from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from core.database import get_db
from agent.runner import run_agent

router = APIRouter(prefix="/agent", tags=["agent"])

class AgentInput(BaseModel):
    org_id: str
    task: str
    task_type: str

@router.post("/run")
def run(payload: AgentInput, db: Session = Depends(get_db)):
    try:
        result = run_agent(
            org_id=payload.org_id,
            task=payload.task,
            task_type=payload.task_type,
            db=db,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))