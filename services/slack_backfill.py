import uuid as uuid_lib
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from services.slack_fetcher import get_channels, get_messages, get_new_messages, get_channel_members
from ingestion.raw_indexer import bulk_index_messages
from ingestion.pipeline import run_pipeline
from models.sources import Source
from models.channel_memberships import ChannelMembership
from models.business_profile import BusinessProfile
import uuid as uuid_lib
from ingestion.pre_filter import should_skip, bundle_threads, clean_text


def _store_channel_memberships(org_id: str, channel_id: str, db: Session) -> None:
    """
    Fetch all members of a private channel and store them in channel_memberships.
    Uses ON CONFLICT DO NOTHING so re-running backfill never creates duplicates.
    """
    org_uuid = uuid_lib.UUID(org_id)
    members = get_channel_members(channel_id)

    for user_id in members:
        stmt = pg_insert(ChannelMembership).values(
            org_id=org_uuid,
            channel_id=channel_id,
            user_id=user_id,
        ).on_conflict_do_nothing(constraint="uq_channel_member")
        db.execute(stmt)


def backfill_slack(org_id: str, db: Session, days_back: int = 180) -> dict:
    """
    Historic backfill — pulls last N days of Slack messages and stores them raw.
    Also captures channel membership for every private channel found.
    """
    profile = db.query(BusinessProfile).filter_by(org_id=uuid_lib.UUID(org_id)).first()
    workspace_domain = profile.slack_workspace_domain if profile else None

    channels = get_channels()
    total = 0

    for channel in channels:
        channel_id = channel["id"]
        is_private = channel.get("is_private", False)

        messages = get_messages(channel_id, days_back=days_back, workspace_domain=workspace_domain)
        if messages:
            # Filter noise then bundle threads before indexing
            messages = [m for m in messages if not should_skip(m["content"], m.get("metadata"))]
            for m in messages:
                m["content"] = clean_text(m["content"])
            messages = bundle_threads(messages)
            count = bulk_index_messages(
                org_id=org_id,
                messages=messages,
                source_type="slack_history",
                db=db,
                channel_id=channel_id,
                channel_is_private=is_private,
            )
            total += count

        # Store who has access to this channel so the ACL filter works at query time
        if is_private:
            _store_channel_memberships(org_id, channel_id, db)

    db.commit()
    return {"channels": len(channels), "messages_indexed": total}


def ingest_new_slack_messages(org_id: str, db: Session, since_hours: float = 24) -> dict:
    """
    Daily ingestion — pulls last 24 hours of Slack messages,
    stores them raw, then runs the extraction pipeline on each.
    """
    profile = db.query(BusinessProfile).filter_by(org_id=uuid_lib.UUID(org_id)).first()
    workspace_domain = profile.slack_workspace_domain if profile else None

    channels = get_channels()
    org_uuid = uuid_lib.UUID(org_id)
    total_indexed = 0
    total_proposed = 0
    total_approved = 0

    for channel in channels:
        channel_id = channel["id"]
        is_private = channel.get("is_private", False)
        messages = get_new_messages(channel_id, days_back=since_hours / 24, workspace_domain=workspace_domain)

        # Filter noise and clean footers before pipeline
        messages = [m for m in messages if not should_skip(m["content"], m.get("metadata"))]
        for m in messages:
            m["content"] = clean_text(m["content"])
        messages = bundle_threads(messages)

        for msg in messages:
            source = Source(
                org_id=org_uuid,
                source_type="slack_daily",
                raw_content=msg["content"],
                timestamp=msg["timestamp"],
                permalink=msg.get("permalink"),
                source_metadata=msg.get("metadata", {}),
                channel_id=channel_id,
                channel_is_private=is_private,
            )
            db.add(source)
            db.flush()

            result = run_pipeline(
                org_id=org_id,
                source_id=source.id,
                raw_content=msg["content"],
                db=db,
            )

            total_indexed += 1
            total_proposed += result["proposed"]
            total_approved += result["approved"]

    db.commit()
    return {
        "channels": len(channels),
        "messages_processed": total_indexed,
        "entities_proposed": total_proposed,
        "entities_approved": total_approved,
    }