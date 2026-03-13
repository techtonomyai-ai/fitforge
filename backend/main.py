"""
FitForge Backend — FastAPI MVP
Endpoints: auth, workouts, exercises, nutrition, progress, Stripe billing
"""

import os
import json
import hmac
import hashlib
import logging
from datetime import datetime, timedelta, date, timezone
from typing import Optional, List

import jwt
import stripe
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Request, Header, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean,
    DateTime, Text, ForeignKey, Date
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from passlib.context import CryptContext

# ─── Load env ────────────────────────────────────────────────────────────────
load_dotenv()

OPENAI_API_KEY        = os.getenv("OPENAI_API_KEY", "")
STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_BASIC_PRICE_ID = os.getenv("STRIPE_BASIC_PRICE_ID", "")
STRIPE_PRO_PRICE_ID   = os.getenv("STRIPE_PRO_PRICE_ID", "")
JWT_SECRET            = os.getenv("JWT_SECRET", "change-me-in-production-32chars!!")
FRONTEND_URL          = os.getenv("FRONTEND_URL", "http://localhost:8000")
JWT_ALGORITHM         = "HS256"
JWT_EXPIRE_HOURS      = 72

stripe.api_key = STRIPE_SECRET_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fitforge")

# ─── Database ─────────────────────────────────────────────────────────────────
DATABASE_URL = "sqlite:///./fitforge.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ─── ORM Models ───────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    id               = Column(Integer, primary_key=True, index=True)
    email            = Column(String, unique=True, index=True, nullable=False)
    hashed_password  = Column(String, nullable=False)
    created_at       = Column(DateTime, default=datetime.utcnow)
    trial_ends_at    = Column(DateTime)
    subscription     = Column(String, default="trial")   # trial | basic | pro | expired
    stripe_customer  = Column(String, nullable=True)
    calories_goal    = Column(Integer, default=2000)
    protein_goal     = Column(Integer, default=150)

    nutrition_logs   = relationship("NutritionLog", back_populates="user", cascade="all, delete")
    progress_logs    = relationship("ProgressLog",  back_populates="user", cascade="all, delete")


class WorkoutPlan(Base):
    __tablename__ = "workouts"
    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=True)
    plan_name   = Column(String, nullable=False)
    description = Column(Text)
    goal        = Column(String)
    level       = Column(String)
    days_per_week = Column(Integer)
    equipment   = Column(String)
    plan_json   = Column(Text)   # full AI JSON stored here
    created_at  = Column(DateTime, default=datetime.utcnow)


class Exercise(Base):
    __tablename__ = "exercises"
    id                = Column(Integer, primary_key=True, index=True)
    exercise_id       = Column(String, unique=True, index=True)
    name              = Column(String, nullable=False)
    muscle_groups     = Column(String)   # JSON list
    equipment         = Column(String)
    difficulty        = Column(String)
    instructions      = Column(Text)
    tips              = Column(Text)
    calories_per_minute = Column(Float, default=6.0)


class NutritionLog(Base):
    __tablename__ = "nutrition_logs"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    log_date   = Column(Date, default=date.today)
    meal_name  = Column(String)
    calories   = Column(Float, default=0)
    protein    = Column(Float, default=0)
    carbs      = Column(Float, default=0)
    fat        = Column(Float, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    user       = relationship("User", back_populates="nutrition_logs")


class ProgressLog(Base):
    __tablename__ = "progress_logs"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    log_date   = Column(Date, default=date.today)
    weight     = Column(Float, nullable=True)
    body_fat   = Column(Float, nullable=True)
    notes      = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    user       = relationship("User", back_populates="progress_logs")


Base.metadata.create_all(bind=engine)


# ─── Seed exercise library ────────────────────────────────────────────────────
def seed_exercises(db: Session):
    if db.query(Exercise).count() > 0:
        return
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "exercises.json")
    if not os.path.exists(data_path):
        return
    with open(data_path) as f:
        exercises = json.load(f)
    for ex in exercises:
        obj = Exercise(
            exercise_id=ex["id"],
            name=ex["name"],
            muscle_groups=json.dumps(ex.get("muscle_groups", [])),
            equipment=ex.get("equipment", "none"),
            difficulty=ex.get("difficulty", "beginner"),
            instructions=ex.get("instructions", ""),
            tips=ex.get("tips", ""),
            calories_per_minute=ex.get("calories_per_minute", 6.0),
        )
        db.add(obj)
    db.commit()
    logger.info(f"Seeded {len(exercises)} exercises.")


# ─── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="FitForge API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()


# ─── DB dependency ────────────────────────────────────────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─── Auth helpers ─────────────────────────────────────────────────────────────
def hash_password(pw: str) -> str:
    return pwd_context.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    return pwd_context.verify(pw, hashed)


def create_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> int:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return int(payload["sub"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    user_id = decode_token(credentials.credentials)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_active(user: User = Depends(current_user)) -> User:
    """Require trial or paid subscription."""
    now = datetime.utcnow()
    if user.subscription == "trial":
        if user.trial_ends_at and now > user.trial_ends_at:
            raise HTTPException(status_code=402, detail="Trial expired — please subscribe")
    elif user.subscription == "expired":
        raise HTTPException(status_code=402, detail="Subscription required")
    return user


# ─── Pydantic schemas ─────────────────────────────────────────────────────────
class RegisterIn(BaseModel):
    email: str
    password: str

class LoginIn(BaseModel):
    email: str
    password: str

class GenerateWorkoutIn(BaseModel):
    goal: str
    level: str
    days_per_week: int
    equipment: str

class NutritionLogIn(BaseModel):
    name: str
    calories: float
    protein: float
    carbs: float
    fat: float

class MealPlanIn(BaseModel):
    calories_target: int
    protein_target: int
    preferences: Optional[List[str]] = []

class ProgressLogIn(BaseModel):
    weight: Optional[float] = None
    body_fat: Optional[float] = None
    notes: Optional[str] = None

class CheckoutIn(BaseModel):
    plan: str   # "basic" | "pro"


# ─── Startup ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    db = SessionLocal()
    try:
        seed_exercises(db)
    finally:
        db.close()


# ─── Auth endpoints ───────────────────────────────────────────────────────────
@app.post("/register", tags=["auth"])
async def register(body: RegisterIn, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        trial_ends_at=datetime.utcnow() + timedelta(days=7),
        subscription="trial",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_token(user.id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "subscription": user.subscription,
        "trial_ends_at": user.trial_ends_at.isoformat(),
    }


@app.post("/login", tags=["auth"])
async def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(user.id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "subscription": user.subscription,
    }


# ─── User profile ─────────────────────────────────────────────────────────────
@app.get("/me", tags=["user"])
async def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    today = date.today()
    nutrition = db.query(NutritionLog).filter(
        NutritionLog.user_id == user.id,
        NutritionLog.log_date == today,
    ).all()
    calories_today = sum(n.calories for n in nutrition)
    protein_today  = sum(n.protein  for n in nutrition)

    trial_days_left = None
    if user.subscription == "trial" and user.trial_ends_at:
        delta = user.trial_ends_at - datetime.utcnow()
        trial_days_left = max(0, delta.days)

    return {
        "id": user.id,
        "email": user.email,
        "subscription": user.subscription,
        "trial_ends_at": user.trial_ends_at.isoformat() if user.trial_ends_at else None,
        "trial_days_left": trial_days_left,
        "goals": {"calories": user.calories_goal, "protein": user.protein_goal},
        "today": {
            "calories": calories_today,
            "protein": protein_today,
            "meals_logged": len(nutrition),
        },
    }


# ─── Workouts ─────────────────────────────────────────────────────────────────
@app.get("/workouts", tags=["workouts"])
async def list_workouts(user: User = Depends(require_active), db: Session = Depends(get_db)):
    plans = db.query(WorkoutPlan).filter(
        (WorkoutPlan.user_id == user.id) | (WorkoutPlan.user_id == None)
    ).order_by(WorkoutPlan.created_at.desc()).all()
    return [
        {
            "id": p.id,
            "plan_name": p.plan_name,
            "description": p.description,
            "goal": p.goal,
            "level": p.level,
            "days_per_week": p.days_per_week,
            "equipment": p.equipment,
            "created_at": p.created_at.isoformat(),
        }
        for p in plans
    ]


@app.get("/workouts/{workout_id}", tags=["workouts"])
async def get_workout(workout_id: int, user: User = Depends(require_active), db: Session = Depends(get_db)):
    plan = db.query(WorkoutPlan).filter(WorkoutPlan.id == workout_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Workout not found")
    data = json.loads(plan.plan_json) if plan.plan_json else {}
    return {
        "id": plan.id,
        "plan_name": plan.plan_name,
        "description": plan.description,
        "goal": plan.goal,
        "level": plan.level,
        "days_per_week": plan.days_per_week,
        "equipment": plan.equipment,
        "plan": data,
        "created_at": plan.created_at.isoformat(),
    }


@app.post("/workouts/generate", tags=["workouts"])
async def generate_workout(
    body: GenerateWorkoutIn,
    user: User = Depends(require_active),
    db: Session = Depends(get_db),
):
    from ai_engine import generate_workout_plan
    plan_data = await generate_workout_plan(body.goal, body.level, body.days_per_week, body.equipment)

    plan = WorkoutPlan(
        user_id=user.id,
        plan_name=plan_data.get("plan_name", f"{body.goal} plan"),
        description=plan_data.get("description", ""),
        goal=body.goal,
        level=body.level,
        days_per_week=body.days_per_week,
        equipment=body.equipment,
        plan_json=json.dumps(plan_data),
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return {"id": plan.id, **plan_data}


# ─── Exercises ────────────────────────────────────────────────────────────────
@app.get("/exercises", tags=["exercises"])
async def list_exercises(
    muscle_group: Optional[str] = None,
    equipment: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Exercise)
    if equipment:
        q = q.filter(Exercise.equipment == equipment)
    exercises = q.all()
    result = []
    for ex in exercises:
        groups = json.loads(ex.muscle_groups) if ex.muscle_groups else []
        if muscle_group and muscle_group.lower() not in [g.lower() for g in groups]:
            continue
        result.append({
            "id": ex.exercise_id,
            "name": ex.name,
            "muscle_groups": groups,
            "equipment": ex.equipment,
            "difficulty": ex.difficulty,
            "instructions": ex.instructions,
            "tips": ex.tips,
            "calories_per_minute": ex.calories_per_minute,
        })
    return result


# ─── Nutrition ────────────────────────────────────────────────────────────────
@app.post("/nutrition/log", tags=["nutrition"])
async def log_nutrition(
    body: NutritionLogIn,
    user: User = Depends(require_active),
    db: Session = Depends(get_db),
):
    entry = NutritionLog(
        user_id=user.id,
        meal_name=body.name,
        calories=body.calories,
        protein=body.protein,
        carbs=body.carbs,
        fat=body.fat,
        log_date=date.today(),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"id": entry.id, "message": "Meal logged successfully"}


@app.get("/nutrition/today", tags=["nutrition"])
async def nutrition_today(user: User = Depends(require_active), db: Session = Depends(get_db)):
    today = date.today()
    logs = db.query(NutritionLog).filter(
        NutritionLog.user_id == user.id,
        NutritionLog.log_date == today,
    ).all()
    totals = {
        "calories": sum(n.calories for n in logs),
        "protein":  sum(n.protein  for n in logs),
        "carbs":    sum(n.carbs    for n in logs),
        "fat":      sum(n.fat      for n in logs),
    }
    return {
        "date": today.isoformat(),
        "totals": totals,
        "goals": {"calories": user.calories_goal, "protein": user.protein_goal},
        "remaining": {
            "calories": max(0, user.calories_goal - totals["calories"]),
            "protein":  max(0, user.protein_goal  - totals["protein"]),
        },
        "meals": [
            {
                "id": n.id,
                "name": n.meal_name,
                "calories": n.calories,
                "protein": n.protein,
                "carbs": n.carbs,
                "fat": n.fat,
            }
            for n in logs
        ],
    }


@app.post("/nutrition/meal-plan", tags=["nutrition"])
async def generate_meal_plan(
    body: MealPlanIn,
    user: User = Depends(require_active),
):
    from ai_engine import generate_meal_plan as ai_meal_plan
    plan = await ai_meal_plan(body.calories_target, body.protein_target, body.preferences)
    return plan


# ─── Progress ─────────────────────────────────────────────────────────────────
@app.post("/progress/log", tags=["progress"])
async def log_progress(
    body: ProgressLogIn,
    user: User = Depends(require_active),
    db: Session = Depends(get_db),
):
    entry = ProgressLog(
        user_id=user.id,
        log_date=date.today(),
        weight=body.weight,
        body_fat=body.body_fat,
        notes=body.notes,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"id": entry.id, "message": "Progress logged"}


@app.get("/progress", tags=["progress"])
async def get_progress(user: User = Depends(require_active), db: Session = Depends(get_db)):
    cutoff = date.today() - timedelta(days=30)
    logs = (
        db.query(ProgressLog)
        .filter(ProgressLog.user_id == user.id, ProgressLog.log_date >= cutoff)
        .order_by(ProgressLog.log_date)
        .all()
    )
    return [
        {
            "id": l.id,
            "date": l.log_date.isoformat(),
            "weight": l.weight,
            "body_fat": l.body_fat,
            "notes": l.notes,
        }
        for l in logs
    ]


# ─── Stripe Billing ───────────────────────────────────────────────────────────
@app.post("/checkout", tags=["billing"])
async def create_checkout(
    body: CheckoutIn,
    user: User = Depends(require_active),
    db: Session = Depends(get_db),
):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe not configured")

    price_id = STRIPE_BASIC_PRICE_ID if body.plan == "basic" else STRIPE_PRO_PRICE_ID
    if not price_id:
        raise HTTPException(status_code=503, detail="Stripe price IDs not configured")

    # Create or retrieve Stripe customer
    if not user.stripe_customer:
        customer = stripe.Customer.create(email=user.email, metadata={"user_id": user.id})
        user.stripe_customer = customer.id
        db.commit()

    session = stripe.checkout.Session.create(
        customer=user.stripe_customer,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=f"{FRONTEND_URL}/?checkout=success",
        cancel_url=f"{FRONTEND_URL}/?checkout=cancel",
        metadata={"user_id": user.id, "plan": body.plan},
    )
    return {"checkout_url": session.url, "session_id": session.id}


@app.post("/webhook", tags=["billing"])
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    db = SessionLocal()
    try:
        if event["type"] == "checkout.session.completed":
            session_obj = event["data"]["object"]
            user_id = int(session_obj["metadata"].get("user_id", 0))
            plan    = session_obj["metadata"].get("plan", "basic")
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                user.subscription = plan
                db.commit()
                logger.info(f"User {user_id} upgraded to {plan}")

        elif event["type"] == "customer.subscription.deleted":
            sub = event["data"]["object"]
            customer_id = sub["customer"]
            user = db.query(User).filter(User.stripe_customer == customer_id).first()
            if user:
                user.subscription = "expired"
                db.commit()
                logger.info(f"Subscription cancelled for customer {customer_id}")
    finally:
        db.close()

    return {"status": "ok"}


# ─── Health ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "healthy", "service": "FitForge API", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
