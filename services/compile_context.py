from sqlalchemy.orm import Session
from models.business_profile import BusinessProfile
from models.entities import Entity
import uuid

def compile_context(org_id: str, db: Session) -> str:
    org_uuid = uuid.UUID(org_id)

    profile = db.query(BusinessProfile).filter_by(org_id=org_uuid).first()
    if not profile:
        return "No business profile found for this organisation."

    lines = [
        f"Business: {profile.business_name}",
        f"Industry: {profile.industry}",
        f"Core services: {profile.core_services}",
        f"What they don't do: {profile.what_they_dont_do}",
        f"Team size: {profile.team_size}",
        "",
        "== Known facts ==",
    ]

    entities = (
        db.query(Entity)
        .filter_by(org_id=org_uuid)
        .filter(Entity.superseded_at == None)
        .order_by(Entity.entity_type, Entity.created_at.desc())
        .all()
    )

    if not entities:
        lines.append("No entities on record yet.")
    else:
        for e in entities:
            lines.append(f"[{e.entity_type}] {e.name or ''}: {e.content}")

    return "\n".join(lines)