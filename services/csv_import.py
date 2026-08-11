import csv
import io
import uuid
from sqlalchemy.orm import Session
from models.entities import Entity
from models.sources import Source
from services.embeddings import get_embedding

def import_csv(org_id: str, file_content: bytes, entity_type: str, db: Session) -> dict:
    org_uuid = uuid.UUID(org_id)
    content_str = file_content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(content_str))

    count = 0
    for row in reader:
        content = ", ".join(f"{k}: {v}" for k, v in row.items() if v)
        name = row.get("name") or row.get("Name") or row.get("company") or row.get("Company") or f"Row {count + 1}"

        source = Source(
            org_id=org_uuid,
            source_type="csv_import",
            raw_content=content,
        )
        db.add(source)
        db.flush()

        embedding = get_embedding(content)

        entity = Entity(
            org_id=org_uuid,
            entity_type=entity_type,
            name=name,
            content=content,
            source_id=source.id,
            approved_by="csv_import",
            embedding=embedding,
        )
        db.add(entity)
        count += 1

    db.commit()
    return {"imported": count}