"""
Golden evaluation runner.

For each golden Q&A pair, calls the chat function and asks an LLM judge
whether the actual answer matches what was expected.
Stores the score back on the golden_eval row and logs a summary.
"""

from datetime import datetime, timezone
from openai import OpenAI
from core.config import settings
from core.database import SessionLocal
from models.golden_evals import GoldenEval
from models.session_logs import SessionLog
from models.business_profile import BusinessProfile
from services.chat import chat

client = OpenAI(
    api_key=settings.ai_api_key,
    base_url=settings.ai_base_url,
)

JUDGE_PROMPT = """You are an answer accuracy judge.

Question: {question}
Expected answer (what the system should say): {expected}
Actual answer (what the system said): {actual}

Does the actual answer correctly address the question and match the expected answer in substance?
Reply with only: PASS or FAIL"""


def _judge(question: str, expected: str, actual: str) -> float:
    """Returns 1.0 for PASS, 0.0 for FAIL."""
    prompt = JUDGE_PROMPT.format(question=question, expected=expected, actual=actual)
    response = client.chat.completions.create(
        model=settings.ai_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    verdict = response.choices[0].message.content.strip().upper()
    return 1.0 if "PASS" in verdict else 0.0


def run_evals_for_org(org_id: str, db) -> dict:
    """
    Runs all golden evals for one org.
    Returns a summary dict with pass rate.
    """
    import uuid
    org_uuid = uuid.UUID(org_id)
    evals = db.query(GoldenEval).filter_by(org_id=org_uuid).all()

    if not evals:
        return {"org_id": org_id, "total": 0, "passed": 0, "pass_rate": None}

    passed = 0
    for ev in evals:
        result = chat(org_id=org_id, question=ev.question, db=db)
        actual = result.get("answer", "")
        score = _judge(ev.question, ev.expected_answer, actual)

        ev.last_score = score
        ev.last_actual_answer = actual
        ev.last_run_at = datetime.now(timezone.utc)

        if score == 1.0:
            passed += 1

    pass_rate = round(passed / len(evals) * 100, 1)

    # Log the result
    log = SessionLog(
        org_id=org_uuid,
        log_type="eval_result",
        content=f"Eval run: {passed}/{len(evals)} passed ({pass_rate}%). {'⚠️ Below 80% threshold.' if pass_rate < 80 else 'OK.'}",
    )
    db.add(log)
    db.commit()

    print(f"[Evaluator] Org {org_id}: {passed}/{len(evals)} passed ({pass_rate}%)")
    return {"org_id": org_id, "total": len(evals), "passed": passed, "pass_rate": pass_rate}


def run_all_evals():
    """Called by the monthly scheduler job."""
    db = SessionLocal()
    try:
        profiles = db.query(BusinessProfile).all()
        for profile in profiles:
            run_evals_for_org(str(profile.org_id), db)
    finally:
        db.close()