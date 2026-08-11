from sqlalchemy.orm import Session
from sqlalchemy import text
from openai import OpenAI
from core.config import settings
from services.embeddings import get_embedding
from models.entities import Entity
import uuid

client = OpenAI(
    api_key=settings.ai_api_key,
    base_url=settings.ai_base_url,
)

def search_memory_tool(org_id: str, query: str, db: Session) -> list:
    embedding = get_embedding(query)
    org_uuid = uuid.UUID(org_id)

    results = db.execute(
        text("""
            SELECT id, entity_type, name, content
            FROM entities
            WHERE org_id = :org_id
            AND superseded_at IS NULL
            AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT 5
        """),
        {
            "org_id": str(org_uuid),
            "embedding": str(embedding),
        }
    ).fetchall()

    return [
        {"entity_type": r.entity_type, "name": r.name, "content": r.content}
        for r in results
    ]

def draft_content_tool(task: str, memory_context: list) -> str:
    context = "\n".join(
        f"[{m['entity_type']}] {m['name'] or ''}: {m['content']}"
        for m in memory_context
    )

    response = client.chat.completions.create(
        model=settings.ai_model,
        messages=[
            {
                "role": "system",
                "content": f"""You are a business assistant drafting content for a small team.
Use only the verified memory below. Do not invent facts.

== Verified Memory ==
{context}
"""
            },
            {"role": "user", "content": task}
        ],
    )
    return response.choices[0].message.content

def flag_contradiction_tool(new_content: str, memory_context: list) -> dict:
    context = "\n".join(
        f"[{m['entity_type']}] {m['name'] or ''}: {m['content']}"
        for m in memory_context
    )

    response = client.chat.completions.create(
        model=settings.ai_model,
        messages=[
            {
                "role": "system",
                "content": """You check if new information contradicts existing verified memory.
Return JSON only: {"contradiction": true/false, "reason": "explanation"}"""
            },
            {
                "role": "user",
                "content": f"New information: {new_content}\n\nExisting memory:\n{context}"
            }
        ],
        temperature=0,
    )

    import json
    text = response.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    try:
        return json.loads(text)
    except:
        return {"contradiction": False, "reason": "Could not parse response."}