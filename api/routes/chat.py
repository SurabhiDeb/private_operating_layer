from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from core.database import get_db
from services.chat import chat

router = APIRouter(prefix="/chat", tags=["chat"])

class ChatInput(BaseModel):
    org_id: str
    question: str
    user_id: Optional[str] = None  # Slack user ID of the person asking

@router.post("/ask")
def ask(payload: ChatInput, db: Session = Depends(get_db)):
    try:
        result = chat(payload.org_id, payload.question, db, user_id=payload.user_id)
        return {
            "answer": result["answer"],
            "sources": result["sources"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))