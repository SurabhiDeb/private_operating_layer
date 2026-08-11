from sqlalchemy.orm import Session
from models.entities import Entity
from models.sources import Source
from services.embeddings import get_embedding
import uuid

def map_hubspot_contact(record: dict, org_id: uuid.UUID, db: Session):
    name = f"{record.get('firstName', '')} {record.get('lastName', '')}".strip()
    email = record.get('email', '')
    company = record.get('company', '')
    content = f"Name: {name}. Email: {email}. Company: {company}."

    source = Source(
        org_id=org_id,
        source_type="hubspot_contact",
        raw_content=content,
    )
    db.add(source)
    db.flush()

    embedding = get_embedding(content)

    entity = Entity(
        org_id=org_id,
        entity_type="Person",
        name=name,
        content=content,
        source_id=source.id,
        approved_by="nango_import",
        embedding=embedding,
    )
    db.add(entity)

def map_hubspot_deal(record: dict, org_id: uuid.UUID, db: Session):
    name = record.get('dealname', 'Unnamed deal')
    stage = record.get('dealstage', '')
    amount = record.get('amount', '')
    content = f"Deal: {name}. Stage: {stage}. Amount: {amount}."

    source = Source(
        org_id=org_id,
        source_type="hubspot_deal",
        raw_content=content,
    )
    db.add(source)
    db.flush()

    embedding = get_embedding(content)

    entity = Entity(
        org_id=org_id,
        entity_type="Project",
        name=name,
        content=content,
        source_id=source.id,
        approved_by="nango_import",
        embedding=embedding,
    )
    db.add(entity)

def import_hubspot(org_id: str, connection_id: str, db: Session):
    from services.nango import get_hubspot_contacts, get_hubspot_deals
    org_uuid = uuid.UUID(org_id)

    contacts = get_hubspot_contacts(connection_id)
    for record in contacts:
        map_hubspot_contact(record, org_uuid, db)

    deals = get_hubspot_deals(connection_id)
    for record in deals:
        map_hubspot_deal(record, org_uuid, db)

    db.commit()
    return {"contacts": len(contacts), "deals": len(deals)}