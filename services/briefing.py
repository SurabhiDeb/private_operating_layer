from sqlalchemy.orm import Session
from openai import OpenAI
from core.config import settings
from core.database import SessionLocal
from models.business_profile import BusinessProfile
from models.session_logs import SessionLog
from services.compile_context import compile_context
import uuid

client = OpenAI(
    api_key=settings.ai_api_key,
    base_url=settings.ai_base_url,
)

DEFAULT_BRIEFING_FOCUS = (
    "Cover: what the team should focus on today, "
    "any open decisions or beliefs worth remembering, and one key reminder."
)

def generate_briefing(org_id: str, db: Session) -> str:
    context = compile_context(org_id, db)

    # Load client-defined focus, fall back to default if not set
    profile = db.query(BusinessProfile).filter_by(org_id=uuid.UUID(org_id)).first()
    focus = (profile.briefing_focus or DEFAULT_BRIEFING_FOCUS) if profile else DEFAULT_BRIEFING_FOCUS

    prompt = f"""You are an AI chief of staff for a small business team.
Based on the business memory below, write a short morning briefing.
{focus}
Keep it under 150 words. Be direct and practical.

{context}
"""

    response = client.chat.completions.create(
        model=settings.ai_model_fast,   # gpt-4o-mini — briefings don't need the full model
        messages=[
            {"role": "system", "content": "You write concise, practical morning briefings for small business teams."},
            {"role": "user", "content": prompt},
        ],
    )

    return response.choices[0].message.content

def run_daily_briefings():
    db = SessionLocal()
    try:
        profiles = db.query(BusinessProfile).all()
        for profile in profiles:
            org_id = str(profile.org_id)
            briefing = generate_briefing(org_id, db)
            log = SessionLog(
                org_id=profile.org_id,
                log_type="daily_briefing",
                content=briefing,
            )
            db.add(log)
        db.commit()
        print(f"Daily briefings generated for {len(profiles)} org(s).")
    finally:
        db.close()
