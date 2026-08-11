from sqlalchemy.orm import Session
from models.sources import Source
import uuid
from datetime import datetime, timezone


def index_raw_message(
    org_id: str,
    source_type: str,
    raw_content: str,
    timestamp: datetime = None,
    permalink: str = None,
    metadata: dict = None,
    channel_id: str = None,
    channel_is_private: bool = False,
    db: Session = None,
) -> Source:
    """
    Stores a raw message as-is in the sources table.
    No extraction, no critic, no transformation.
    This is the historic backfill track.
    """
    org_uuid = uuid.UUID(org_id)

    source = Source(
        org_id=org_uuid,
        source_type=source_type,
        raw_content=raw_content,
        timestamp=timestamp or datetime.now(timezone.utc),
        permalink=permalink,
        source_metadata=metadata or {},
        channel_id=channel_id,
        channel_is_private=channel_is_private,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def bulk_index_messages(
    org_id: str,
    messages: list[dict],
    source_type: str,
    db: Session,
    channel_id: str = None,
    channel_is_private: bool = False,
) -> int:
    """
    Indexes a list of raw messages in bulk.
    Each message dict should have: content, timestamp, permalink (optional), metadata (optional).
    channel_id and channel_is_private are stored on every Source row so the ACL
    filter in search_memory knows which channels each fact came from.
    """
    org_uuid = uuid.UUID(org_id)
    count = 0

    for msg in messages:
        source = Source(
            org_id=org_uuid,
            source_type=source_type,
            raw_content=msg["content"],
            timestamp=msg.get("timestamp") or datetime.now(timezone.utc),
            permalink=msg.get("permalink"),
            source_metadata=msg.get("metadata", {}),
            channel_id=channel_id,
            channel_is_private=channel_is_private,
        )
        db.add(source)
        count += 1

    db.commit()
    return count