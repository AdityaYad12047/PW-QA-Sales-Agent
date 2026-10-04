"""
backend/app/api/settings.py
Key-value settings endpoints for system parameters and operational assumptions.
"""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.models.orm import SystemSetting

router = APIRouter(prefix="/settings", tags=["Settings"])


class SettingUpdate(BaseModel):
    value: Optional[str] = None
    description: Optional[str] = None


@router.get("", response_model=Dict[str, Optional[str]])
def get_all_settings(db: Session = Depends(get_db)):
    """Fetch all key-value settings from the database."""
    settings = db.query(SystemSetting).all()
    return {s.key: s.value for s in settings}


@router.get("/{key}")
def get_setting(key: str, db: Session = Depends(get_db)):
    """Fetch a single setting by key. Returns None if unset."""
    setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not setting:
        return {"key": key, "value": None}
    return {"key": setting.key, "value": setting.value, "description": setting.description}


@router.put("/{key}")
def update_setting(key: str, payload: SettingUpdate, db: Session = Depends(get_db)):
    """Update or insert a setting key-value pair."""
    setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not setting:
        setting = SystemSetting(key=key, value=payload.value, description=payload.description)
        db.add(setting)
    else:
        setting.value = payload.value
        if payload.description is not None:
            setting.description = payload.description
    db.commit()
    db.refresh(setting)
    return {"key": setting.key, "value": setting.value}
