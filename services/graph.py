"""
Kuzu graph database service — semantic memory layer.

Each org gets its own Kuzu database stored under GRAPH_DB_PATH/{org_id}/.
Kuzu is embedded (no separate server process), so it fits naturally inside
the single-tenant DigitalOcean instance alongside PostgreSQL.

Node table:  Entity   — mirrors approved entities from the PostgreSQL entities table
Rel  table:  RELATED_TO — typed relationships extracted at ingestion time

Relationship types used by the extractor:
  owns, works_on, belongs_to, made_by, assigned_to, reports_to, part_of, manages
"""

import os
import kuzu
from pathlib import Path

GRAPH_DB_BASE = os.getenv("GRAPH_DB_PATH", "./graph_dbs")


def _get_conn(org_id: str) -> kuzu.Connection:
    """Open (or create) the Kuzu database for this org and return a connection."""
    base = Path(GRAPH_DB_BASE)
    base.mkdir(parents=True, exist_ok=True)  # ensure base dir exists; Kuzu creates the org subdir itself
    db_path = base / org_id
    db = kuzu.Database(str(db_path))
    return kuzu.Connection(db)


def init_graph_schema(org_id: str) -> None:
    """
    Idempotently create the node and relationship tables for this org.
    Called once during onboarding and again defensively before first write.
    """
    conn = _get_conn(org_id)
    conn.execute("""
        CREATE NODE TABLE IF NOT EXISTS Entity(
            entity_id INT64,
            org_id   STRING,
            entity_type STRING,
            name     STRING,
            content  STRING,
            PRIMARY KEY(entity_id)
        )
    """)
    conn.execute("""
        CREATE REL TABLE IF NOT EXISTS RELATED_TO(
            FROM Entity TO Entity,
            relationship_type STRING
        )
    """)


def write_entity_node(
    org_id: str,
    entity_id: int,
    entity_type: str,
    name: str,
    content: str,
) -> None:
    """
    Write an approved entity as a node in the graph.
    entity_id matches the PostgreSQL entities.id so the two stores stay in sync.
    """
    conn = _get_conn(org_id)
    try:
        conn.execute(
            """
            CREATE (:Entity {
                entity_id:   $eid,
                org_id:      $oid,
                entity_type: $etype,
                name:        $name,
                content:     $content
            })
            """,
            {"eid": entity_id, "oid": org_id, "etype": entity_type,
             "name": name or "", "content": content},
        )
    except Exception:
        # Node already exists — update content in place
        conn.execute(
            "MATCH (e:Entity {entity_id: $eid}) SET e.content = $content",
            {"eid": entity_id, "content": content},
        )


def write_relationship(
    org_id: str,
    from_entity_id: int,
    to_entity_id: int,
    relationship_type: str,
) -> None:
    """
    Write a directed relationship between two entity nodes.
    Both nodes must already exist (call write_entity_node first).
    """
    conn = _get_conn(org_id)
    try:
        conn.execute(
            """
            MATCH (a:Entity {entity_id: $from_id}),
                  (b:Entity {entity_id: $to_id})
            CREATE (a)-[:RELATED_TO {relationship_type: $rel_type}]->(b)
            """,
            {
                "from_id": from_entity_id,
                "to_id": to_entity_id,
                "rel_type": relationship_type,
            },
        )
    except Exception:
        pass  # Relationship may already exist; safe to skip


def query_graph(org_id: str, question: str, db=None, user_id: str = None) -> list[dict]:
    """
    Return all relationships in this org's graph.
    The chat agent passes the raw question; keyword filtering happens upstream.
    Used for relationship-type queries ("who owns X?", "which projects belong to Y?").
    """
    conn = _get_conn(org_id)
    try:
        result = conn.execute(
            """
            MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity)
            WHERE a.org_id = $oid
            RETURN a.entity_type, a.name, r.relationship_type,
                   b.entity_type, b.name, b.content, a.entity_id
            LIMIT 30
            """,
            {"oid": org_id},
        )
        rows = []
        while result.has_next():
            row = result.get_next()
            rows.append({
                "from_type": row[0],
                "from_name": row[1],
                "relationship": row[2],
                "to_type": row[3],
                "to_name": row[4],
                "to_content": row[5],
                "entity_id": row[6],
            })

        # If user_id provided, filter out entities from private channels the user can't see
        if user_id and db:
            from sqlalchemy import text
            import uuid
            org_uuid = uuid.UUID(org_id)
            accessible = db.execute(
                text("""
                    SELECT e.id FROM entities e
                    LEFT JOIN sources s ON e.source_id = s.id
                    WHERE e.org_id = :org_id
                    AND (
                        s.channel_is_private = false
                        OR s.channel_id IN (
                            SELECT channel_id FROM channel_memberships
                            WHERE org_id = :org_id AND user_id = :user_id
                        )
                        OR s.channel_id IS NULL
                    )
                """),
                {"org_id": str(org_uuid), "user_id": user_id}
            ).fetchall()
            accessible_ids = {r[0] for r in accessible}
            rows = [r for r in rows if r["entity_id"] in accessible_ids]

        # Remove entity_id from final output
        for r in rows:
            r.pop("entity_id", None)
            
        return rows
    except Exception:
        return []


def query_entity_relationships(org_id: str, entity_name: str) -> list[dict]:
    """
    Return all relationships where the named entity appears as source or target.
    Used for focused lookups ("tell me everything about Project Delta").
    """
    conn = _get_conn(org_id)
    try:
        result = conn.execute(
            """
            MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity)
            WHERE a.org_id = $oid
              AND (a.name CONTAINS $name OR b.name CONTAINS $name)
            RETURN a.entity_type, a.name, r.relationship_type,
                   b.entity_type, b.name, b.content
            LIMIT 20
            """,
            {"oid": org_id, "name": entity_name},
        )
        rows = []
        while result.has_next():
            row = result.get_next()
            rows.append({
                "from_type": row[0],
                "from_name": row[1],
                "relationship": row[2],
                "to_type": row[3],
                "to_name": row[4],
                "to_content": row[5],
            })
        return rows
    except Exception:
        return []
