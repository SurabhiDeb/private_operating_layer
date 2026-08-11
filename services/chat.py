from sqlalchemy.orm import Session
from sqlalchemy import text
from openai import OpenAI
from core.config import settings
from models.business_profile import BusinessProfile
from models.entities import Entity
from services.embeddings import get_embedding
from services.graph import query_graph, query_entity_relationships
import uuid

# Keywords that signal the user is asking about relationships between entities
# rather than recalling a specific fact — these route to the Kuzu graph layer
RELATIONSHIP_KEYWORDS = [
    "who owns", "who manages", "who works on", "who is assigned",
    "who leads", "who handles", "who is responsible", "who reports",
    "which projects", "which clients", "which teams",
    "what does", "tell me about", "everything about",
]


def _is_relationship_query(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in RELATIONSHIP_KEYWORDS)

client = OpenAI(
    api_key=settings.ai_api_key,
    base_url=settings.ai_base_url,
)

def search_memory(org_id: str, question: str, db: Session, limit: int = 5, user_id: str = None) -> list:
    question_embedding = get_embedding(question)
    org_uuid = uuid.UUID(org_id)

    if user_id:
        # Filter to sources the requesting user can access:
        # - public channels (channel_is_private = false)
        # - private channels where the user is a member
        results = db.execute(
            text("""
                SELECT e.id, e.entity_type, e.name, e.content, e.approved_by, e.source_id,
                       s.permalink, s.source_type
                FROM entities e
                LEFT JOIN sources s ON e.source_id = s.id
                WHERE e.org_id = :org_id
                AND e.superseded_at IS NULL
                AND e.embedding IS NOT NULL
                AND (
                    s.channel_is_private = false
                    OR s.channel_id IN (
                        SELECT channel_id FROM channel_memberships
                        WHERE org_id = :org_id AND user_id = :user_id
                    )
                    OR s.channel_id IS NULL
                )
                ORDER BY e.embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
            """),
            {
                "org_id": str(org_uuid),
                "embedding": str(question_embedding),
                "limit": limit,
                "user_id": user_id,
            }
        ).fetchall()
    else:
        # No user context — return all (used internally, not from user-facing endpoints)
        results = db.execute(
            text("""
                SELECT e.id, e.entity_type, e.name, e.content, e.approved_by, e.source_id,
                       s.permalink, s.source_type
                FROM entities e
                LEFT JOIN sources s ON e.source_id = s.id
                WHERE e.org_id = :org_id
                AND e.superseded_at IS NULL
                AND e.embedding IS NOT NULL
                ORDER BY e.embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
            """),
            {
                "org_id": str(org_uuid),
                "embedding": str(question_embedding),
                "limit": limit,
            }
        ).fetchall()

    return results

def chat(org_id: str, question: str, db: Session, user_id: str = None) -> dict:
    org_uuid = uuid.UUID(org_id)
    profile = db.query(BusinessProfile).filter_by(org_id=org_uuid).first()

    business_context = ""
    if profile:
        business_context = f"""Business: {profile.business_name}
Industry: {profile.industry}
Core services: {profile.core_services}
What they don't do: {profile.what_they_dont_do}
Team size: {profile.team_size}
"""

    # --- Dual-layer retrieval ---
    # Relationship queries go to the Kuzu graph layer first;
    # fact recall queries go to pgvector.
    # Both layers are always queried and combined so the LLM has the fullest context.

    # 1. pgvector — semantic fact recall
    relevant = search_memory(org_id, question, db, user_id=user_id)
    sources_used = []
    if relevant:
        vector_lines = "\n".join(
            f"[{r.entity_type}] {r.name or ''}: {r.content}"
            for r in relevant
        )
        sources_used = [
            {
                "source_id": r.source_id,
                "entity_type": r.entity_type,
                "name": r.name,
                "permalink": r.permalink,
                "source_type": r.source_type,
            }
            for r in relevant if r.source_id
        ]
    else:
        vector_lines = "No relevant facts found in memory."

    # 2. Kuzu — relationship/graph layer
    graph_lines = ""
    if _is_relationship_query(question):
        graph_results = query_graph(org_id, question)
        if graph_results:
            graph_lines = "\n".join(
                f"[{r['from_type']}] {r['from_name']} —{r['relationship']}→ [{r['to_type']}] {r['to_name']}: {r['to_content']}"
                for r in graph_results
            )
        else:
            graph_lines = "No relationship data found."

    memory_section = f"== Verified Facts ==\n{vector_lines}"
    if graph_lines:
        memory_section += f"\n\n== Entity Relationships ==\n{graph_lines}"

    system_prompt = f"""You are an AI operating layer for a small business team.
Answer only from the verified memory below. If the answer is not there, say so clearly.
Always cite which fact or relationship you are drawing from.

== Business Profile ==
{business_context}
{memory_section}
"""

    response = client.chat.completions.create(
        model=settings.ai_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
    )

    return {
        "answer": response.choices[0].message.content,
        "sources": sources_used,
    }