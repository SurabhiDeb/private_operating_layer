"""
Second-pass backfill processor — batched version.

Instead of one API call per source, groups up to BATCH_SIZE sources
into a single extraction call. Cuts API costs by up to 90% on backfill.
"""

import time
from datetime import datetime, timezone
from core.database import SessionLocal
from models.sources import Source
from models.business_profile import BusinessProfile
from models.entities import Entity
from models.session_logs import SessionLog
from models.pending_entities import PendingEntity
from ingestion.extractor import extract_entities_batch
from ingestion.critic import critique_entity
from services.embeddings import get_embedding
from services.graph import init_graph_schema, write_entity_node, write_relationship
import uuid

BATCH_SIZE = 10          # sources per extraction call
SLEEP_BETWEEN_BATCHES = 3  # seconds between batches


def _store_results(org_id: str, sources: list, batch_results: list, db) -> None:
    """
    Takes extraction results for a batch of sources and stores
    approved entities, pending entities, and rejected logs.
    """
    org_uuid = uuid.UUID(org_id)
    init_graph_schema(org_id)

    for source, result in zip(sources, batch_results):
        for entity in result.get("entities", []):
            critique = critique_entity(source.raw_content, entity)
            score = critique.get("confidence_score", 0.5)
            entity["confidence_score"] = score

            if critique["approved"] and score >= 0.8:
                # Check deduplication
                if entity.get("name"):
                    existing = db.query(Entity).filter_by(
                        org_id=org_uuid,
                        entity_type=entity["entity_type"],
                        name=entity["name"],
                        status="active",
                    ).first()
                    if existing:
                        continue

                embedding = get_embedding(entity["content"])
                e = Entity(
                    org_id=org_uuid,
                    entity_type=entity["entity_type"],
                    name=entity.get("name"),
                    content=entity["content"],
                    source_id=source.id,
                    approved_by="coo_critic",
                    status="active",
                    embedding=embedding,
                )
                db.add(e)
                db.flush()
                write_entity_node(org_id, e.id, entity["entity_type"], entity.get("name", ""), entity["content"])

            elif score >= 0.5:
                p = PendingEntity(
                    org_id=org_uuid,
                    source_id=source.id,
                    entity_type=entity["entity_type"],
                    name=entity.get("name"),
                    content=entity["content"],
                    critic_reason=critique.get("reason", ""),
                    confidence_score=score,
                    review_type="standard",
                )
                db.add(p)
            else:
                log = SessionLog(
                    org_id=org_uuid,
                    log_type="rejected_entity",
                    content=f"REJECTED: {entity.get('name')} — {entity.get('content')}",
                )
                db.add(log)

        source.processed_at = datetime.now(timezone.utc)

    db.commit()


def process_backfill_batch() -> None:
    """Called by the scheduler every 2 minutes."""
    db = SessionLocal()
    try:
        profiles = db.query(BusinessProfile).all()

        for profile in profiles:
            org_id = str(profile.org_id)

            pending_sources = (
                db.query(Source)
                .filter(
                    Source.org_id == profile.org_id,
                    Source.source_type == "slack_history",
                    Source.processed_at == None,
                )
                .limit(BATCH_SIZE)
                .all()
            )

            if not pending_sources:
                continue

            # Extract from all sources in one API call
            contents = [s.raw_content for s in pending_sources]
            try:
                batch_results = extract_entities_batch(contents, org_id=org_id, db=db)
                _store_results(org_id, pending_sources, batch_results, db)
                print(f"[BackfillProcessor] Batch of {len(pending_sources)} processed for org {org_id}")
                time.sleep(SLEEP_BETWEEN_BATCHES)
            except Exception as e:
                print(f"[BackfillProcessor] Batch error for org {org_id}: {e}")
                db.rollback()

    finally:
        db.close()