from sqlalchemy.orm import Session
from models.entities import Entity
import uuid

def get_active_beliefs(org_id: str, db: Session) -> list:
    org_uuid = uuid.UUID(org_id)
    return (
        db.query(Entity)
        .filter_by(org_id=org_uuid, entity_type="Belief")
        .filter(Entity.superseded_at == None)
        .all()
    )

def get_active_decisions(org_id: str, db: Session) -> list:
    org_uuid = uuid.UUID(org_id)
    return (
        db.query(Entity)
        .filter_by(org_id=org_uuid, entity_type="Decision")
        .filter(Entity.superseded_at == None)
        .all()
    )

def check_against_beliefs(action_content: str, beliefs: list) -> dict:
    if not beliefs:
        return {"conflict": False}

    from openai import OpenAI
    from core.config import settings
    import json

    client = OpenAI(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
    )

    belief_text = "\n".join(f"- {b.content}" for b in beliefs)

    response = client.chat.completions.create(
        model=settings.ai_model,
        messages=[
            {
                "role": "system",
                "content": """You check if a proposed action violates any of the organisation's core beliefs.
A conflict only exists if the action directly contradicts or violates a belief.
Mere mention of the same topic is NOT a conflict.
Return JSON only: {"conflict": true/false, "belief": "the belief it conflicts with or empty string", "reason": "explanation"}"""
            },
            {
                "role": "user",
                "content": f"Proposed action: {action_content}\n\nBeliefs:\n{belief_text}"
            }
        ],
        temperature=0,
    )

    text = response.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    try:
        result = json.loads(text)
        return {
            "conflict": result.get("conflict", False),
            "belief": result.get("belief", ""),
            "reason": result.get("reason", ""),
        }
    except:
        return {"conflict": False}

def has_relevant_memory(org_id: str, entity_type: str, db: Session) -> bool:
    org_uuid = uuid.UUID(org_id)
    count = (
        db.query(Entity)
        .filter_by(org_id=org_uuid, entity_type=entity_type)
        .filter(Entity.superseded_at == None)
        .count()
    )
    return count > 0