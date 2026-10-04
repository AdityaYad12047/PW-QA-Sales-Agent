"""
backend/app/api/counsellors.py
Basic CRUD for counsellors (Phase 1: create + list only).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.models.orm import Counsellor
from app.models.schemas import CounsellorCreate, CounsellorOut

router = APIRouter(prefix="/counsellors", tags=["counsellors"])


@router.post("", response_model=CounsellorOut, status_code=201)
def create_counsellor(body: CounsellorCreate, db: Session = Depends(get_db)):
    """Create a new counsellor."""
    if body.email:
        existing = db.query(Counsellor).filter(Counsellor.email == body.email).first()
        if existing:
            raise HTTPException(409, detail=f"Counsellor with email {body.email} already exists")
    c = Counsellor(name=body.name, email=body.email)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@router.get("", response_model=list[CounsellorOut])
def list_counsellors(db: Session = Depends(get_db)):
    """List all counsellors with real call count."""
    counsellors = db.query(Counsellor).order_by(Counsellor.id).all()
    return [
        CounsellorOut(
            id=c.id,
            name=c.name,
            email=c.email,
            created_at=c.created_at,
            calls_count=len(c.calls),
        )
        for c in counsellors
    ]


@router.get("/{counsellor_id}", response_model=CounsellorOut)
def get_counsellor(counsellor_id: int, db: Session = Depends(get_db)):
    c = db.get(Counsellor, counsellor_id)
    if c is None:
        raise HTTPException(404, detail=f"Counsellor {counsellor_id} not found")
    return CounsellorOut(
        id=c.id,
        name=c.name,
        email=c.email,
        created_at=c.created_at,
        calls_count=len(c.calls),
    )

