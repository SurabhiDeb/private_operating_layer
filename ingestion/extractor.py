from openai import OpenAI
from core.config import settings
import json

client = OpenAI(
    api_key=settings.ai_api_key,
    base_url=settings.ai_base_url,
)

ENTITY_TYPES = ["Client", "Project", "Decision", "Person", "Process", "Belief"]

RELATIONSHIP_TYPES = [
    "owns", "works_on", "belongs_to", "made_by",
    "assigned_to", "reports_to", "part_of", "manages",
]

EXTRACTION_PROMPT = """You are an expert business analyst. Read the message below and extract:

1. Business facts worth remembering (entities)
2. Relationships between those entities

Return a single JSON object with exactly two keys:

"entities": array of objects, each with:
  - entity_type: one of {entity_types}
  - name: short label (max 10 words)
  - content: the full fact as a clear, standalone sentence

"relationships": array of objects, each with:
  - from_name: name of the source entity (must match a name in entities)
  - from_type: entity_type of the source
  - relationship_type: one of {rel_types}
  - to_name: name of the target entity (must match a name in entities)
  - to_type: entity_type of the target

Rules:
- Only extract what is clearly stated. Do not infer or invent.
- Relationships must reference names that appear in the entities list.
- If nothing is worth extracting, return {{"entities": [], "relationships": []}}.
- Return JSON only. No explanation, no markdown.

Message:
{{message}}
""".format(
    entity_types=", ".join(ENTITY_TYPES),
    rel_types=", ".join(RELATIONSHIP_TYPES),
)

BATCH_EXTRACTION_PROMPT = """You are an expert business analyst. Read each numbered message below and extract business facts from each one.

Return a JSON array where each item corresponds to a message by its number:

[
  {{
    "message_index": 0,
    "entities": [...],
    "relationships": [...]
  }},
  ...
]

Entity format:
  - entity_type: one of {entity_types}
  - name: short label (max 10 words)
  - content: the full fact as a clear, standalone sentence

Relationship format:
  - from_name, from_type, relationship_type, to_name, to_type

Rules:
- Only extract what is clearly stated. Do not infer or invent.
- If a message has nothing worth extracting, return empty arrays for it.
- Return JSON only. No explanation, no markdown.

Messages:
{{messages}}
""".format(
    entity_types=", ".join(ENTITY_TYPES),
    rel_types=", ".join(RELATIONSHIP_TYPES),
)

# Messages shorter than this use the fast (cheaper) model
FAST_MODEL_THRESHOLD = 300


def _select_model(content: str) -> str:
    """
    Routes short, simple messages to the fast model.
    Long or complex messages use the full model.
    """
    if len(content) < FAST_MODEL_THRESHOLD:
        return settings.ai_model_fast
    return settings.ai_model


def _parse_result(text: str) -> dict:
    """Strips markdown fences and parses JSON."""
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        result = json.loads(text)
        if not isinstance(result, dict):
            return {"entities": [], "relationships": []}
        return {
            "entities": result.get("entities", []) if isinstance(result.get("entities"), list) else [],
            "relationships": result.get("relationships", []) if isinstance(result.get("relationships"), list) else [],
        }
    except json.JSONDecodeError:
        return {"entities": [], "relationships": []}


def extract_entities(raw_content: str, org_id: str = None, db=None) -> dict:
    """
    Extracts entities from a single message.
    Automatically routes to fast or full model based on content length.
    """
    if org_id and db:
        from services.token_tracker import is_over_budget
        if is_over_budget(org_id, db):
            raise Exception(f"Token budget exceeded for org {org_id}.")

    model = _select_model(raw_content)
    prompt = EXTRACTION_PROMPT.replace("{message}", raw_content)

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )

    extract_entities._last_token_count = response.usage.total_tokens if response.usage else 0

    if org_id and db and response.usage:
        from services.token_tracker import record_token_usage
        record_token_usage(org_id, response.usage.total_tokens, db)

    return _parse_result(response.choices[0].message.content.strip())


def extract_entities_batch(contents: list[str], org_id: str = None, db=None) -> list[dict]:
    """
    Extracts entities from multiple messages in a single API call.
    Used by the backfill processor to cut API calls by up to 90%.

    Returns a list of result dicts in the same order as contents.
    """
    if not contents:
        return []

    if org_id and db:
        from services.token_tracker import is_over_budget
        if is_over_budget(org_id, db):
            raise Exception(f"Token budget exceeded for org {org_id}.")

    # Format all messages into one numbered block
    messages_block = "\n\n".join(
        f"[{i}] {content}" for i, content in enumerate(contents)
    )
    prompt = BATCH_EXTRACTION_PROMPT.replace("{messages}", messages_block)

    response = client.chat.completions.create(
        model=settings.ai_model,   # always use full model for batch (mixed complexity)
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )

    if org_id and db and response.usage:
        from services.token_tracker import record_token_usage
        record_token_usage(org_id, response.usage.total_tokens, db)

    text = response.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    # Build a default result list (empty for each message)
    results = [{"entities": [], "relationships": []} for _ in contents]

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            for item in parsed:
                idx = item.get("message_index")
                if idx is not None and 0 <= idx < len(contents):
                    results[idx] = {
                        "entities": item.get("entities", []),
                        "relationships": item.get("relationships", []),
                    }
    except json.JSONDecodeError:
        pass

    return results