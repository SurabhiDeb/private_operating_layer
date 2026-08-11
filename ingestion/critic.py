from openai import OpenAI
from core.config import settings
import json

client = OpenAI(
    api_key=settings.ai_api_key,
    base_url=settings.ai_base_url,
)

CRITIC_PROMPT = """You are a strict COO reviewing an AI-proposed business fact.

Your job is to check if the proposed fact is:
1. Clearly supported by the original message
2. Free of invented details or overconfident interpretations
3. A genuine business fact worth storing in permanent memory

Original message:
{message}

Proposed fact:
Type: {entity_type}
Name: {name}
Content: {content}

Respond with a JSON object only:
{{
  "approved": true or false,
  "confidence_score": a number from 0.0 to 1.0 representing how confident you are,
  "reason": "one sentence explanation"
}}

Scoring guide:
- 0.9–1.0: clearly stated, no ambiguity
- 0.7–0.89: likely true but minor gaps
- 0.5–0.69: uncertain, needs human review
- 0.0–0.49: too vague, invented, or unsupported — reject

Be strict. If in doubt, score low.
"""


def critique_entity(raw_message: str, entity: dict) -> dict:
    prompt = CRITIC_PROMPT.format(
        message=raw_message,
        entity_type=entity.get("entity_type", ""),
        name=entity.get("name", ""),
        content=entity.get("content", ""),
    )

    response = client.chat.completions.create(
        model=settings.ai_model,
        messages=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "You are a strict COO reviewing AI-proposed business facts. Be precise and conservative.",
                        "cache_control": {"type": "ephemeral"}
                    }
                ]
            },
            {"role": "user", "content": prompt}
        ],
        temperature=0,
        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
    )

    text = response.choices[0].message.content.strip()

    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    try:
        result = json.loads(text)
        score = float(result.get("confidence_score", 0.5))
        return {
            "approved": result.get("approved", False),
            "confidence_score": score,
            "reason": result.get("reason", ""),
        }
    except (json.JSONDecodeError, ValueError):
        return {"approved": False, "confidence_score": 0.0, "reason": "Critic returned invalid response."}