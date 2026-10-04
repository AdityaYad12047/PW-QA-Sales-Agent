"""
backend/app/pipeline/runner.py

Orchestrates the Phase 1 pipeline for a single call:
  1. Read the uploaded audio file
  2. Run STT (with caching)
  3. Normalise segments
  4. Map roles
  5. Persist to DB + update call status

This module is called from the FastAPI background task.
It writes status updates to the DB at each step so the polling endpoint
reflects real progress.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.orm import Call, Evaluation, SttRun, TranscriptSegment, TranscriptVersion
from app.pipeline.roles import map_roles
from app.pipeline.segments import normalise_segments
from app.services.stt import get_stt_provider

logger = logging.getLogger(__name__)


def _set_status(db: Session, call: Call, status: str, failure_reason: str | None = None):
    call.status = status
    if failure_reason:
        call.failure_reason = failure_reason
    db.commit()
    logger.info("call_id=%d status=%s", call.id, status)


def run_transcription_pipeline(call_id: int, db: Session, mode: Optional[str] = None) -> None:
    """
    Entry point for the background transcription task.
    Updates call.status at every stage; never raises (all errors set status=failed).
    """
    call = db.get(Call, call_id)
    if call is None:
        logger.error("run_transcription_pipeline: call_id=%d not found", call_id)
        return

    try:
        _transcribe(call, db, mode=mode)
    except Exception as exc:
        logger.exception("Unexpected error in transcription pipeline for call_id=%d", call_id)
        _set_status(db, call, "failed", f"unexpected_error: {exc}")


def _transcribe(call: Call, db: Session, mode: Optional[str] = None) -> None:
    audio_path = Path(call.file_path)
    effective_mode = mode or getattr(call, "transcription_mode", "auto") or "auto"
    call.transcription_mode = effective_mode

    # ── 1. STT ────────────────────────────────────────────────────────────────
    _set_status(db, call, "transcribing")
    t0 = time.monotonic()

    provider = get_stt_provider()
    stt_result = provider.transcribe(audio_path, mode=effective_mode)

    stt_latency_ms = int((time.monotonic() - t0) * 1000)
    call.t_stt_s = round(time.monotonic() - t0, 2)

    # Always log the STT run (success or failure)
    stt_run = SttRun(
        call_id=call.id,
        provider=stt_result.provider or "sarvam",
        model=stt_result.model or "",
        audio_duration_seconds=stt_result.audio_duration_seconds,
        estimated_cost_inr=stt_result.estimated_cost_inr,
        latency_ms=stt_result.latency_ms or stt_latency_ms,
        status="succeeded" if stt_result.success else stt_result.failure_reason or "failed",
        error_detail=stt_result.failure_detail,
        from_cache=stt_result.from_cache,
    )
    db.add(stt_run)
    db.flush()

    if not stt_result.success:
        reason = stt_result.failure_reason or "stt_failed"
        _set_status(db, call, "failed", reason)
        return

    # Update call duration if available
    if stt_result.audio_duration_seconds:
        call.duration_seconds = stt_result.audio_duration_seconds

    # ── 2. Normalise segments ─────────────────────────────────────────────────
    segments = normalise_segments(stt_result.entries)

    if not segments:
        _set_status(db, call, "failed", "empty_transcript")
        return

    # Basic quality check: very short transcript is suspicious
    total_words = sum(len(s.text.split()) for s in segments)
    if total_words < 20:
        _set_status(
            db, call, "failed",
            f"low_word_count: only {total_words} words in transcript"
        )
        return

    # ── 3. Map roles ──────────────────────────────────────────────────────────
    role_result = map_roles(segments)
    # Log a warning in the failure_reason if single-speaker
    if role_result.warning and len(set(s.speaker_label for s in segments)) == 1:
        call.failure_reason = role_result.warning  # advisory, not a hard failure

    # ── 4. Persist transcript version & segments ──────────────────────────────
    latest_ver = (
        db.query(TranscriptVersion)
        .filter(TranscriptVersion.call_id == call.id)
        .order_by(TranscriptVersion.version_number.desc())
        .first()
    )
    next_version = (latest_ver.version_number + 1) if latest_ver else 1

    lang_param = "hi-IN" if effective_mode == "hi" else ("en-IN" if effective_mode == "en" else None)
    new_version = TranscriptVersion(
        call_id=call.id,
        version_number=next_version,
        mode=effective_mode,
        provider=stt_result.provider or "sarvam",
        provider_params_json={
            "model": stt_result.model or "saaras:v4",
            "with_diarization": True,
            "language_code": lang_param,
        },
        detected_language=stt_result.detected_language,
        duration_seconds=stt_result.audio_duration_seconds,
        cost_est=stt_result.estimated_cost_inr or 0.0,
    )
    db.add(new_version)
    db.flush()

    for idx, seg in enumerate(role_result.segments):
        # IDs unique per version (seg_001 for v1, seg_v2_001 for v2, etc.)
        seg_id = f"seg_v{next_version}_{idx+1:03d}" if next_version > 1 else seg.segment_id
        db_seg = TranscriptSegment(
            call_id=call.id,
            transcript_version_id=new_version.id,
            segment_id=seg_id,
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            speaker_label=seg.speaker_label,
            role=seg.role,
            role_confidence=seg.role_confidence,
            role_source=seg.role_source,
            text=seg.text,
        )
        db.add(db_seg)

    # When a new version is created, mark existing evaluations "stale: based on an older transcript"
    # and do NOT recompute automatically (Requirement C.3)
    existing_evals = db.query(Evaluation).filter(Evaluation.call_id == call.id).all()
    for ev in existing_evals:
        ev.is_stale = True
        ev.stale_reason = "stale: based on an older transcript"

    _set_status(db, call, "transcribed")
    logger.info(
        "call_id=%d transcribed (version=%d, mode=%s): %d segments, %d words, duration=%.1fs",
        call.id, next_version, effective_mode, len(segments), total_words,
        stt_result.audio_duration_seconds or 0,
    )
