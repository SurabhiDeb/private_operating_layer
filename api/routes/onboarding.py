from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from core.database import get_db
from models.business_profile import BusinessProfile
import uuid

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

class OnboardingInput(BaseModel):
    business_name: str
    industry: str
    core_services: str
    what_they_dont_do: str
    team_size: int

class BriefingFocusInput(BaseModel):
    briefing_focus: str

@router.post("/create")
def create_org(data: OnboardingInput, db: Session = Depends(get_db)):
    # Check if business already exists
    existing = db.query(BusinessProfile).filter_by(business_name=data.business_name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Organisation already exists")

    org = BusinessProfile(
        business_name=data.business_name,
        industry=data.industry,
        core_services=data.core_services,
        what_they_dont_do=data.what_they_dont_do,
        team_size=data.team_size
    )
    db.add(org)
    db.commit()
    db.refresh(org)

    return {
        "message": "Organisation created successfully",
        "org_id": str(org.org_id),
        "business_name": org.business_name
    }


@router.patch("/briefing-focus/{org_id}")
def update_briefing_focus(org_id: str, body: BriefingFocusInput, db: Session = Depends(get_db)):
    """Update the briefing focus for an org. The text is injected into the daily briefing prompt."""
    profile = db.query(BusinessProfile).filter_by(org_id=uuid.UUID(org_id)).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Organisation not found.")
    profile.briefing_focus = body.briefing_focus.strip()
    db.commit()
    return {"message": "Briefing focus updated.", "briefing_focus": profile.briefing_focus}


@router.get("/briefing-focus/{org_id}")
def get_briefing_focus(org_id: str, db: Session = Depends(get_db)):
    """Get the current briefing focus for an org."""
    profile = db.query(BusinessProfile).filter_by(org_id=uuid.UUID(org_id)).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Organisation not found.")
    return {"briefing_focus": profile.briefing_focus or ""}