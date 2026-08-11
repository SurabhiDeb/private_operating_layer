from typing import TypedDict, Annotated
import operator
import uuid
from datetime import datetime, timezone
from langgraph.graph import StateGraph, END
from sqlalchemy.orm import Session
from models.sources import Source
from models.entities import Entity
from models.session_logs import SessionLog
from models.pending_entities import PendingEntity
from ingestion.extractor import extract_entities
from ingestion.critic import critique_entity
from services.embeddings import get_embedding


class PipelineState(TypedDict):
    org_id: str
    source_id: int
    raw_content: str
    proposed_entities: Annotated[list, operator.add]
    proposed_relationships: Annotated[list, operator.add]
    approved_entities: Annotated[list, operator.add]
    rejected_entities: Annotated[list, operator.add]
    pending_entities: Annotated[list, operator.add]


def extract_node(state: PipelineState) -> dict:
    result = extract_entities(state["raw_content"], org_id=state["org_id"])
    return {
        "proposed_entities": result["entities"],
        "proposed_relationships": result["relationships"],
    }


def critic_node(state: PipelineState) -> dict:
    """
    Three-tier routing based on numeric confidence score:
    - score >= 0.7: auto-approve (high confidence)
    - 0.5 <= score < 0.7: send to human review queue
    - score < 0.5: auto-reject
    """
    approved = []
    rejected = []
    pending = []

    for entity in state["proposed_entities"]:
        result = critique_entity(state["raw_content"], entity)
        score = result.get("confidence_score", 0.5)
        entity["critic_approved"] = result["approved"]
        entity["critic_reason"] = result["reason"]
        entity["confidence_score"] = score

        if result["approved"] and score >= 0.7:
            approved.append(entity)
        elif score >= 0.5:
            pending.append(entity)
        else:
            rejected.append(entity)

    return {
        "approved_entities": approved,
        "rejected_entities": rejected,
        "pending_entities": pending,
    }


def store_node(state: PipelineState, db: Session) -> dict:
    org_uuid = uuid.UUID(state["org_id"])

    # Cap check — pause pipeline if approval queue is full
    pending_count = db.query(PendingEntity).filter_by(org_id=org_uuid).count()
    if pending_count >= 20:
        log = SessionLog(
            org_id=org_uuid,
            log_type="pipeline_paused",
            content=f"Pipeline paused: approval queue has reached 20 pending items. Review and clear the queue to resume.",
        )
        db.add(log)
        db.commit()
        print(f"[Pipeline] Paused for org {state['org_id']} — approval queue full (20 items).")
        return {}
    
    from services.graph import init_graph_schema, write_entity_node, write_relationship
    init_graph_schema(state["org_id"])

    # Map entity name -> postgres ID for relationship wiring
    name_to_entity_id: dict[str, int] = {}

    for entity in state["approved_entities"]:
        embedding = get_embedding(entity["content"])

        # Deduplication — skip if same name + type already exists and is active
        if entity.get("name"):
            existing_entity = db.query(Entity).filter_by(
                org_id=org_uuid,
                entity_type=entity["entity_type"],
                name=entity.get("name"),
                status="active",
            ).first()
            if existing_entity:
                # Still wire up relationships using the existing entity's ID
                name_to_entity_id[entity["name"]] = existing_entity.id
                continue  # skip storing the duplicate

        e = Entity(
            org_id=org_uuid,
            entity_type=entity["entity_type"],
            name=entity.get("name"),
            content=entity["content"],
            source_id=state["source_id"],
            approved_by="coo_critic",
            status="active",
            embedding=embedding,
        )
        db.add(e)
        db.flush()

        # Write to Kuzu semantic layer
        write_entity_node(
            org_id=state["org_id"],
            entity_id=e.id,
            entity_type=entity["entity_type"],
            name=entity.get("name", ""),
            content=entity["content"],
        )
        if entity.get("name"):
            name_to_entity_id[entity["name"]] = e.id

        # auto contradiction check against existing memory
        from agent.tools import flag_contradiction_tool, search_memory_tool
        existing = search_memory_tool(state["org_id"], entity["content"], db)
        if existing:
            check = flag_contradiction_tool(entity["content"], existing)
            if check.get("contradiction"):
                # Surface contradiction as a reviewable item, not a silent log
                p = PendingEntity(
                    org_id=org_uuid,
                    source_id=state["source_id"],
                    entity_type=entity["entity_type"],
                    name=entity.get("name"),
                    content=f"NEW: {entity['content']}\n\nCONFLICTS WITH: {existing}\n\nREASON: {check.get('reason')}",
                    critic_reason=check.get("reason", "Contradiction detected."),
                    confidence_score=0.5,
                    review_type="contradiction",
                )
                db.add(p)

    # Write relationships to Kuzu
    for rel in state.get("proposed_relationships", []):
        from_id = name_to_entity_id.get(rel.get("from_name", ""))
        to_id = name_to_entity_id.get(rel.get("to_name", ""))
        if from_id and to_id:
            write_relationship(
                org_id=state["org_id"],
                from_entity_id=from_id,
                to_entity_id=to_id,
                relationship_type=rel.get("relationship_type", "related_to"),
            )

    for entity in state["pending_entities"]:
        p = PendingEntity(
            org_id=org_uuid,
            source_id=state["source_id"],
            entity_type=entity["entity_type"],
            name=entity.get("name"),
            content=entity["content"],
            critic_reason=entity.get("critic_reason", ""),
            confidence_score=entity.get("confidence_score"),
            review_type="standard",
        )
        db.add(p)

    for entity in state["rejected_entities"]:
        log = SessionLog(
            org_id=org_uuid,
            log_type="rejected_entity",
            content=f"REJECTED: {entity.get('name')} — {entity.get('content')} — Reason: {entity.get('critic_reason')}",
        )
        db.add(log)

    db.commit()
    return {}


def build_pipeline(db: Session):
    graph = StateGraph(PipelineState)

    graph.add_node("extract", extract_node)
    graph.add_node("critic", critic_node)
    graph.add_node("store", lambda state: store_node(state, db))

    graph.set_entry_point("extract")
    graph.add_edge("extract", "critic")
    graph.add_edge("critic", "store")
    graph.add_edge("store", END)

    return graph.compile()


def run_pipeline(org_id: str, source_id: int, raw_content: str, db: Session):
    pipeline = build_pipeline(db)
    result = pipeline.invoke({
        "org_id": org_id,
        "source_id": source_id,
        "raw_content": raw_content,
        "proposed_entities": [],
        "proposed_relationships": [],
        "approved_entities": [],
        "rejected_entities": [],
        "pending_entities": [],
    })
    return {
        "proposed": len(result["proposed_entities"]),
        "approved": len(result["approved_entities"]),
        "rejected": len(result["rejected_entities"]),
        "pending": len(result["pending_entities"]),
    }