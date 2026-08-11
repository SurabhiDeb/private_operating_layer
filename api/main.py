import time
from apscheduler.schedulers.background import BackgroundScheduler
from services.briefing import run_daily_briefings
from services.slack_backfill import ingest_new_slack_messages
from core.database import SessionLocal as DbSession
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from core.config import settings
from core.database import engine, Base
from api.routes.onboarding import router as onboarding_router
from api.routes.context import router as context_router
from api.routes.ingest import router as ingest_router
from api.routes.chat import router as chat_router
from api.routes.auth import router as auth_router
from api.routes.integrations import router as integrations_router
from api.routes.agent import router as agent_router
from services.backfill_processor import process_backfill_batch
from services.evaluator import run_all_evals
from services.slack_health import run_all_health_checks
from api.routes.email import router as email_router
from services.heartbeat import beat, check_all as heartbeat_check_all
from models.process_heartbeat import ProcessHeartbeat  # ensures table is created

Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_name, debug=settings.debug)


def run_daily_ingestion():
    """
    Staggered daily ingestion — processes one org at a time with a 5-minute gap
    between each to avoid hammering the API with all clients at once.
    Org 1 starts at 7:00, org 2 at 7:05, org 3 at 7:10, etc.
    """
    db = DbSession()
    try:
        from models.business_profile import BusinessProfile
        profiles = db.query(BusinessProfile).all()
        for i, profile in enumerate(profiles):
            if i > 0:
                time.sleep(300)  # 5-minute gap between orgs
            print(f"[DailyIngestion] Starting org {profile.org_id} ({i+1}/{len(profiles)})")
            ingest_new_slack_messages(str(profile.org_id), db, since_hours=24)
        print(f"[DailyIngestion] Complete for {len(profiles)} org(s).")
        beat("daily_ingestion")
    except Exception as e:
        print(f"[DailyIngestion] Error: {e}")
        beat("daily_ingestion", status="error", error=str(e))
    finally:
        db.close()


def run_micro_batch():
    """
    Runs every 30 minutes to pick up fresh Slack messages without waiting until 7AM.
    Uses since_hours=0.5 so it only fetches the last 30 minutes.
    """
    db = DbSession()
    try:
        from models.business_profile import BusinessProfile
        profiles = db.query(BusinessProfile).all()
        for profile in profiles:
            ingest_new_slack_messages(str(profile.org_id), db, since_hours=0.5)
        print(f"[MicroBatch] Complete for {len(profiles)} org(s).")
        beat("micro_batch")
    except Exception as e:
        print(f"[MicroBatch] Error: {e}")
        beat("micro_batch", status="error", error=str(e))
    finally:
        db.close()

def _run_daily_briefings():
    try:
        run_daily_briefings()
        beat("daily_briefings")
    except Exception as e:
        beat("daily_briefings", status="error", error=str(e))
        raise

def _process_backfill_batch():
    try:
        process_backfill_batch()
        beat("backfill_processor")
    except Exception as e:
        beat("backfill_processor", status="error", error=str(e))
        raise

def _run_all_evals():
    try:
        run_all_evals()
        beat("evals")
    except Exception as e:
        beat("evals", status="error", error=str(e))
        raise

def _run_all_health_checks():
    try:
        run_all_health_checks()
        beat("slack_health")
    except Exception as e:
        beat("slack_health", status="error", error=str(e))
        raise

scheduler = BackgroundScheduler()
scheduler.add_job(_run_daily_briefings, "cron", hour=8, minute=0)
scheduler.add_job(run_daily_ingestion, "cron", hour=7, minute=0)
scheduler.add_job(run_micro_batch, "interval", minutes=30)
scheduler.add_job(_process_backfill_batch, "interval", minutes=2)
scheduler.add_job(_run_all_evals, "cron", day=1, hour=6, minute=0)
scheduler.add_job(_run_all_health_checks, "cron", hour=6, minute=30)
scheduler.start()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(onboarding_router)
app.include_router(context_router)
app.include_router(ingest_router)
app.include_router(chat_router)
app.include_router(auth_router)
app.include_router(integrations_router)
app.include_router(agent_router)
app.include_router(email_router)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_dashboard():
    return FileResponse("static/index.html")

@app.get("/health")
def health():
    # Check database connectivity
    db_ok = True
    try:
        db = DbSession()
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db.close()
    except Exception as e:
        db_ok = False

    # Check all background process heartbeats
    processes = heartbeat_check_all()
    any_overdue = any(p["overdue"] for p in processes)

    overall = "ok" if (db_ok and not any_overdue) else "degraded"

    return {
        "status": overall,
        "app": settings.app_name,
        "database": "ok" if db_ok else "unreachable",
        "processes": processes,
    }