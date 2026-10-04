"""
backend/app/models/orm.py
SQLAlchemy ORM table definitions for every entity in the system.
All tables use Integer PKs and store timestamps as UTC ISO strings for
SQLite compatibility (Postgres would use native TIMESTAMP WITH TIME ZONE).
"""
from __future__ import annotations

import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,

    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Counsellors
# ─────────────────────────────────────────────────────────────────────────────
class Counsellor(Base):
    __tablename__ = "counsellors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, unique=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )

    calls: Mapped[list["Call"]] = relationship("Call", back_populates="counsellor")


# ─────────────────────────────────────────────────────────────────────────────
# Calls  (one row per uploaded audio file)
# ─────────────────────────────────────────────────────────────────────────────
class Call(Base):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    counsellor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("counsellors.id"), nullable=False
    )
    # SHA-256 hex digest of the uploaded file, used for duplicate detection + cache key
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Status machine: uploaded -> transcribing -> transcribed -> analyzing -> completed | needs_review | failed
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="uploaded")
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    transcription_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="auto")
    sarvam_job_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    audio_data: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)


    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    # Timing breakdown (stored in seconds; None if not yet measured)
    t_upload_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    t_stt_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    t_llm_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    counsellor: Mapped["Counsellor"] = relationship("Counsellor", back_populates="calls")
    segments: Mapped[list["TranscriptSegment"]] = relationship(
        "TranscriptSegment", back_populates="call", order_by="TranscriptSegment.start_ms"
    )
    transcript_versions: Mapped[list["TranscriptVersion"]] = relationship(
        "TranscriptVersion", back_populates="call", order_by="TranscriptVersion.version_number", cascade="all, delete-orphan"
    )
    stt_runs: Mapped[list["SttRun"]] = relationship("SttRun", back_populates="call")
    llm_runs: Mapped[list["LlmRun"]] = relationship("LlmRun", back_populates="call")
    evaluation: Mapped[Optional["Evaluation"]] = relationship(
        "Evaluation", back_populates="call", uselist=False
    )


# ─────────────────────────────────────────────────────────────────────────────
# Transcript Versions
# ─────────────────────────────────────────────────────────────────────────────
class TranscriptVersion(Base):
    __tablename__ = "transcript_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    call_id: Mapped[int] = mapped_column(Integer, ForeignKey("calls.id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="auto")  # auto | hi | hinglish | en
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="sarvam")
    provider_params_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    detected_language: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cost_est: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=0.0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )

    call: Mapped["Call"] = relationship("Call", back_populates="transcript_versions")
    segments: Mapped[list["TranscriptSegment"]] = relationship(
        "TranscriptSegment", back_populates="transcript_version", order_by="TranscriptSegment.start_ms", cascade="all, delete-orphan"
    )
    evaluations: Mapped[list["Evaluation"]] = relationship(
        "Evaluation", back_populates="transcript_version"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Transcript Segments
# ─────────────────────────────────────────────────────────────────────────────
class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    call_id: Mapped[int] = mapped_column(Integer, ForeignKey("calls.id"), nullable=False)
    transcript_version_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("transcript_versions.id"), nullable=True
    )

    # Stable ID used in prompts and evidence references (seg_001, seg_002, …)
    segment_id: Mapped[str] = mapped_column(String(20), nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    # Raw speaker label from diarization provider (e.g. "speaker_0")
    speaker_label: Mapped[str] = mapped_column(String(50), nullable=False)
    # Mapped role: counsellor | student | parent | unknown
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    # Confidence of role mapping (0.0–1.0, set by pipeline/roles.py)
    role_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # How the role was determined: heuristic | manual_override
    role_source: Mapped[str] = mapped_column(String(30), nullable=False, default="heuristic")

    text: Mapped[str] = mapped_column(Text, nullable=False)

    call: Mapped["Call"] = relationship("Call", back_populates="segments")
    transcript_version: Mapped[Optional["TranscriptVersion"]] = relationship(
        "TranscriptVersion", back_populates="segments"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Rubric Versions
# ─────────────────────────────────────────────────────────────────────────────
class RubricVersion(Base):
    __tablename__ = "rubric_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    # Full rubric stored as JSON (list of criterion dicts with weights/anchors)
    criteria_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    # Weights stored separately for quick validation (must sum to 100)
    weights_json: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
    change_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    evaluations: Mapped[list["Evaluation"]] = relationship(
        "Evaluation", back_populates="rubric_version"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Evaluations  (one per call per rubric version run)
# ─────────────────────────────────────────────────────────────────────────────
class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    call_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("calls.id"), nullable=False, unique=True
    )
    rubric_version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rubric_versions.id"), nullable=False
    )
    transcript_version_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("transcript_versions.id"), nullable=True
    )

    # Final weighted score 0–100, computed by Python, never by LLM
    overall_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # True if any verified critical flag exists — sets status to needs_review
    has_critical_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Stale evaluation flag (e.g. when a new transcript version is generated)
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stale_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    call: Mapped["Call"] = relationship("Call", back_populates="evaluation")
    transcript_version: Mapped[Optional["TranscriptVersion"]] = relationship(
        "TranscriptVersion", back_populates="evaluations"
    )
    rubric_version: Mapped["RubricVersion"] = relationship(
        "RubricVersion", back_populates="evaluations"
    )
    criteria_scores: Mapped[list["EvaluationCriterion"]] = relationship(
        "EvaluationCriterion", back_populates="evaluation", cascade="all, delete-orphan"
    )
    evidence_items: Mapped[list["EvaluationEvidence"]] = relationship(
        "EvaluationEvidence", back_populates="evaluation", cascade="all, delete-orphan"
    )
    compliance_flags: Mapped[list["ComplianceFlag"]] = relationship(
        "ComplianceFlag", back_populates="evaluation", cascade="all, delete-orphan"
    )
    coaching: Mapped[Optional["Coaching"]] = relationship(
        "Coaching", back_populates="evaluation", uselist=False, cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Per-criterion scores
# ─────────────────────────────────────────────────────────────────────────────
class EvaluationCriterion(Base):
    __tablename__ = "evaluation_criteria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    evaluation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("evaluations.id"), nullable=False
    )
    criterion_id: Mapped[str] = mapped_column(String(50), nullable=False)
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # 0–4
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 0–1
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    not_applicable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Weight used when this score was computed (snapshot so weight changes don't corrupt history)
    weight_used: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    evaluation: Mapped["Evaluation"] = relationship(
        "Evaluation", back_populates="criteria_scores"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Evidence items (tied to evaluations, verified by pipeline/verify.py)
# ─────────────────────────────────────────────────────────────────────────────
class EvaluationEvidence(Base):
    __tablename__ = "evaluation_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    evaluation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("evaluations.id"), nullable=False
    )
    criterion_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    flag_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("compliance_flags.id"), nullable=True
    )

    segment_id: Mapped[str] = mapped_column(String(20), nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Verification outcome: verified | rejected_missing_segment | rejected_quote_not_found
    #                        | rejected_wrong_role | unverified
    verification_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="unverified"
    )
    verification_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    evaluation: Mapped["Evaluation"] = relationship(
        "Evaluation", back_populates="evidence_items"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Compliance Flags
# ─────────────────────────────────────────────────────────────────────────────
class ComplianceFlag(Base):
    __tablename__ = "compliance_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    evaluation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("evaluations.id"), nullable=False
    )

    rule_id: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g. R-01
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # critical|major|minor
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)

    # verified: at least one evidence item passed verification
    # unverified: LLM claimed a violation but evidence failed; shown in "needs manual check"
    # uncategorized: unknown rule_id
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="unverified")

    evaluation: Mapped["Evaluation"] = relationship(
        "Evaluation", back_populates="compliance_flags"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Coaching  (LLM call 3 output)
# ─────────────────────────────────────────────────────────────────────────────
class Coaching(Base):
    __tablename__ = "coaching"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    evaluation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("evaluations.id"), nullable=False, unique=True
    )
    # Full JSON blob: {strengths, improvements, next_call_focus}
    content_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )

    evaluation: Mapped["Evaluation"] = relationship(
        "Evaluation", back_populates="coaching"
    )


# ─────────────────────────────────────────────────────────────────────────────
# LLM Run Log  (every Claude call, regardless of outcome)
# ─────────────────────────────────────────────────────────────────────────────
class LlmRun(Base):
    __tablename__ = "llm_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    call_id: Mapped[int] = mapped_column(Integer, ForeignKey("calls.id"), nullable=False)

    # Which of the three pipeline calls: evaluator | compliance | coaching
    call_type: Mapped[str] = mapped_column(String(30), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)

    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Approximate cost in USD (computed client-side from token counts + known pricing)
    estimated_cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # succeeded | failed_json_parse | failed_validation | failed_timeout | failed_api_error
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Whether result was served from cache (no API call made)
    from_cache: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    price_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="v1-2024-10")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )

    call: Mapped["Call"] = relationship("Call", back_populates="llm_runs")


# ─────────────────────────────────────────────────────────────────────────────
# STT Run Log
# ─────────────────────────────────────────────────────────────────────────────
class SttRun(Base):
    __tablename__ = "stt_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    call_id: Mapped[int] = mapped_column(Integer, ForeignKey("calls.id"), nullable=False)

    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. sarvam
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    audio_duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Cost in INR (Sarvam pricing is per-hour in INR)
    estimated_cost_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(40), nullable=False)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    from_cache: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )

    call: Mapped["Call"] = relationship("Call", back_populates="stt_runs")


# ─────────────────────────────────────────────────────────────────────────────
# System Settings (key-value config, e.g. operational assumptions)
# ─────────────────────────────────────────────────────────────────────────────
class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
