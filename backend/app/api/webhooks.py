"""
backend/app/api/webhooks.py
Sarvam Speech-to-Text asynchronous webhook handler.
Receives job completion/failure callbacks, updates call records,
persists transcript segments, and triggers the evaluation pipeline.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.engine import get_db
from app.models.orm import Call, Evaluation, SttRun, TranscriptSegment, TranscriptVersion
from app.pipeline.evaluation_runner import run_evaluation_pipeline
from app.pipeline.roles import map_roles
from app.pipeline.segments import normalise_segments
from app.services.stt import get_stt_provider

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/sarvam")
@router.post("/api/webhooks/sarvam")
async def sarvam_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Handle Sarvam batch job completion / failure callbacks.
    Authenticates via SARVAM_WEBHOOK_TOKEN.
    Processes output, persists segments, and runs evaluation pipeline.
    """
    settings = get_settings()

    # 1. Authenticate webhook callback if SARVAM_WEBHOOK_TOKEN is configured
    expected_token = settings.sarvam_webhook_token.strip()
    if expected_token:
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif auth_header:
            token = auth_header.strip()

        if not token:
            token = request.headers.get("X-Sarvam-Token", "").strip() or request.headers.get("X-Auth-Token", "").strip()
        if not token:
            token = request.query_params.get("token", "").strip()

        if token != expected_token:
            logger.warning("Sarvam webhook authentication failed")
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "detail": "Invalid or missing webhook token"},
            )

    # 2. Parse payload
    try:
        body = await request.json()
    except Exception:
        body = {}

    job_id = body.get("job_id") or body.get("jobId") or body.get("id") or request.query_params.get("job_id")
    job_status = str(body.get("status") or "").lower()

    if not job_id:
        logger.warning("Sarvam webhook received without job_id: %s", body)
        return JSONResponse(status_code=400, content={"error": "missing_job_id", "detail": "Payload has no job_id"})

    logger.info("Sarvam webhook received for job_id=%s, status=%s", job_id, job_status)

    # 3. Locate Call by sarvam_job_id
    call = db.query(Call).filter(Call.sarvam_job_id == job_id).first()
    if not call:
        logger.warning("Sarvam webhook: no call found for sarvam_job_id=%s", job_id)
        return {"status": "ignored", "detail": f"No call found for job_id {job_id}"}

    # 4. Idempotency: if already transcribed or evaluated, skip re-processing
    if call.status in ("transcribed", "completed", "needs_review"):
        logger.info("Sarvam webhook: call %d is already in status '%s', skipping", call.id, call.status)
        return {"status": "already_processed", "call_id": call.id, "call_status": call.status}

    # 5. Handle Sarvam failure
    if job_status in ("failed", "error") or body.get("error"):
        error_msg = body.get("error_message") or body.get("error") or "Sarvam job reported failure"
        call.status = "failed"
        call.failure_reason = str(error_msg)
        db.commit()
        logger.warning("Sarvam webhook marked call %d as failed: %s", call.id, error_msg)
        return {"status": "failed_recorded", "call_id": call.id}

    # 6. Fetch job results from Sarvam and parse transcript
    provider = get_stt_provider()
    effective_mode = call.transcription_mode or "auto"
    stt_result = provider.fetch_job_result(job_id=job_id, mode=effective_mode)

    # Record STT run in db
    stt_run = SttRun(
        call_id=call.id,
        provider=stt_result.provider or "sarvam",
        model=stt_result.model or "saaras:v4",
        audio_duration_seconds=stt_result.audio_duration_seconds,
        estimated_cost_inr=stt_result.estimated_cost_inr,
        latency_ms=stt_result.latency_ms,
        status="succeeded" if stt_result.success else stt_result.failure_reason or "failed",
        error_detail=stt_result.failure_detail,
        from_cache=stt_result.from_cache,
    )
    db.add(stt_run)
    db.flush()

    if not stt_result.success:
        call.status = "failed"
        call.failure_reason = stt_result.failure_reason or "stt_fetch_failed"
        db.commit()
        return {"status": "failed", "call_id": call.id, "reason": call.failure_reason}

    if stt_result.audio_duration_seconds:
        call.duration_seconds = stt_result.audio_duration_seconds

    # Normalise segments
    segments = normalise_segments(stt_result.entries)
    if not segments:
        call.status = "failed"
        call.failure_reason = "empty_transcript"
        db.commit()
        return {"status": "failed", "call_id": call.id, "reason": "empty_transcript"}

    # Map roles
    role_result = map_roles(segments)
    if role_result.warning and len(set(s.speaker_label for s in segments)) == 1:
        call.failure_reason = role_result.warning

    # Create new TranscriptVersion
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

    # Invalidate previous evaluations as stale
    existing_evals = db.query(Evaluation).filter(Evaluation.call_id == call.id).all()
    for ev in existing_evals:
        ev.is_stale = True
        ev.stale_reason = "stale: based on an older transcript"

    call.status = "transcribed"
    db.commit()

    # 7. Continue evaluation pipeline
    try:
        run_evaluation_pipeline(call_id=call.id, db=db, transcript_version_id=new_version.id)
        db.refresh(call)
        logger.info("Evaluation completed for call %d after webhook (status=%s)", call.id, call.status)
    except Exception as eval_exc:
        logger.exception("Evaluation failed after webhook for call %d: %s", call.id, eval_exc)
        # Call remains in 'transcribed' state so user can inspect transcript and retry evaluation

    return {"status": "success", "call_id": call.id, "call_status": call.status}
