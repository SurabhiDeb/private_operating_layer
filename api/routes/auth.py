from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from passlib.context import CryptContext
from core.database import get_db
from models.users import User
from models.business_profile import BusinessProfile
import uuid

router = APIRouter(prefix="/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class RegisterInput(BaseModel):
    email: str
    password: str
    business_name: str
    industry: str
    core_services: str
    what_they_dont_do: str
    team_size: int

class LoginInput(BaseModel):
    email: str
    password: str

@router.post("/register")
def register(data: RegisterInput, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter_by(email=data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered.")

    existing_org = db.query(BusinessProfile).filter_by(business_name=data.business_name).first()
    if existing_org:
        raise HTTPException(status_code=400, detail="Business already exists.")

    org = BusinessProfile(
        business_name=data.business_name,
        industry=data.industry,
        core_services=data.core_services,
        what_they_dont_do=data.what_they_dont_do,
        team_size=data.team_size,
    )
    db.add(org)
    db.flush()

    if len(data.password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="Password must be 72 characters or fewer.")
    user = User(
        email=data.email,
        hashed_password=pwd_context.hash(data.password),
        org_id=org.org_id,
    )
    db.add(user)
    db.commit()

    return {
        "message": "Account created successfully.",
        "org_id": str(org.org_id),
        "email": user.email,
    }

@router.post("/login")
def login(data: LoginInput, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(email=data.email).first()
    if not user or not pwd_context.verify(data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    return {
        "message": "Login successful.",
        "org_id": str(user.org_id),
        "email": user.email,
    }