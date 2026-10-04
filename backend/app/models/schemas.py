"""
backend/app/models/schemas.py
Pydantic v2 schemas used for API request/response and internal data transfer.
Keeping schemas separate from ORM models makes serialisation explicit.
"""
from __future__ import annotations

import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────────────────────
# Base helpers
# ─────────────────────────────────────────────────────────────────────────────
class OrmBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ─────────────────────────────────────────────────────────────────────────────
# Counsellors
# ─────────────────────────────────────────────────────────────────────────────
class CounsellorCreate(BaseModel):
    name: str
    email: Optional[str] = None


class CounsellorOut(OrmBase):
    id: int
    name: str
    email: Optional[str]
    created_at: datetime.datetime
    calls_count: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Calls
# ─────────────────────────────────────────────────────────────────────────────
class CallOut(OrmBase):
    id: int
    counsellor_id: int
    original_filename: str
    file_hash: str
    file_size_bytes: int
    duration_seconds: Optional[float]
    status: str
    failure_reason: Optional[str]
    transcription_mode: str = "auto"
    created_at: datetime.datetime
    updated_at: datetime.datetime


class CallListItemOut(BaseModel):
    id: int
    counsellor_id: int
    counsellor_name: Optional[str] = None
    original_filename: str
    file_size_bytes: int
    duration_seconds: Optional[float] = None
    status: str
    failure_reason: Optional[str] = None
    transcription_mode: str = "auto"
    overall_score: Optional[float] = None
    has_critical_flag: Optional[bool] = None
    created_at: datetime.datetime


# ─────────────────────────────────────────────────────────────────────────────
# Transcript Segments & Versions
# ─────────────────────────────────────────────────────────────────────────────
class SegmentOut(OrmBase):
    segment_id: str
    start_ms: int
    end_ms: int
    speaker_label: str
    role: str
    role_confidence: Optional[float]
    role_source: str
    text: str


class TranscriptVersionOut(OrmBase):
    id: int
    call_id: int
    version_number: int
    mode: str
    provider: str
    detected_language: Optional[str] = None
    duration_seconds: Optional[float] = None
    cost_est: Optional[float] = None
    created_at: datetime.datetime


class TranscriptOut(BaseModel):
    call_id: int
    status: str
    version_number: int = 1
    transcript_version_id: Optional[int] = None
    mode: str = "auto"
    detected_language: Optional[str] = None
    available_versions: list[int] = Field(default_factory=list)
    segments: list[SegmentOut]


class RetranscribeRequest(BaseModel):
    mode: str = Field(..., pattern="^(auto|hi|hinglish|en)$")


# ─────────────────────────────────────────────────────────────────────────────
# Role override (manual correction)
# ─────────────────────────────────────────────────────────────────────────────
class RoleOverride(BaseModel):
    segment_id: str
    role: str = Field(..., pattern="^(counsellor|student|parent|unknown)$")


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────
class HealthOut(BaseModel):
    status: str
    db: str
    version: str = "1.0.0"


# ─────────────────────────────────────────────────────────────────────────────
# Error envelope
# ─────────────────────────────────────────────────────────────────────────────
class ErrorOut(BaseModel):
    error: str
    detail: Optional[Any] = None


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation and Evidence Schemas
# ─────────────────────────────────────────────────────────────────────────────
class EvidenceOut(BaseModel):
    segment_id: str
    quote: str
    note: Optional[str] = None
    verification_status: str
    verification_detail: Optional[str] = None
    db_segment_text: Optional[str] = None
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None


class CriterionScoreOut(BaseModel):
    criterion_id: str
    name: Optional[str] = None
    max_score: int = 4
    score: Optional[int]
    weight: float
    confidence: Optional[float] = None
    rationale: Optional[str] = None
    not_applicable: bool = False
    evidence: list[EvidenceOut] = []


class ComplianceFlagOut(BaseModel):
    rule_id: str
    severity: str
    confidence: float
    explanation: str
    status: str
    evidence: list[EvidenceOut] = []


class EvaluationOut(BaseModel):
    call_id: int
    rubric_version: int
    overall_score: Optional[float]
    has_critical_flag: bool
    status: str
    min_overall_score_for_review: float = 50.0
    min_confidence_for_gate: float = 0.70
    low_confidence_display_threshold: float = 0.70
    transcript_version_id: Optional[int] = None
    is_stale: bool = False
    stale_reason: Optional[str] = None
    criteria: list[CriterionScoreOut]
    compliance_flags: list[ComplianceFlagOut]
    timing: dict[str, Optional[float]]
    llm_cost_usd: Optional[float] = 0.0
    coaching: Optional[dict[str, Any]] = None
    created_at: datetime.datetime

