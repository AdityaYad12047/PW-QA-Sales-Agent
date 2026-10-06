"""
backend/app/api/webhooks.py

Sarvam Speech-to-Text asynchronous webhook handler.

Receives Sarvam batch-job callbacks, updates call records, downloads the
completed transcript output, persists transcript segments, and triggers
the evaluation pipeline.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.engine import get_db
from app.models.orm import (
    Call,
    Evaluation,
    SttRun,
    TranscriptSegment,
    TranscriptVersion,
)
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
    Handle Sarvam batch STT job callbacks.

    Sarvam callback authentication:
        X-SARVAM-JOB-CALLBACK-TOKEN

    Sarvam callback payload:
        job_id
        job_state

    Transcript output is fetched separately from Sarvam after the
    job reaches Completed.
    """

    settings = get_settings()

    # ================================================================
    # 1. Authenticate Sarvam callback
    # ================================================================

    expected_token = settings.sarvam_webhook_token.strip()

    if expected_token:
        callback_token = request.headers.get(
            "X-SARVAM-JOB-CALLBACK-TOKEN",
            "",
        ).strip()

        if callback_token != expected_token:
            logger.warning(
                "Sarvam webhook authentication failed"
            )

            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "detail": "Invalid or missing webhook token",
                },
            )

    # ================================================================
    # 2. Parse webhook payload
    # ================================================================

    try:
        body = await request.json()
    except Exception:
        logger.exception(
            "Sarvam webhook contained invalid JSON"
        )

        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_json",
                "detail": "Webhook body is not valid JSON",
            },
        )

    # ================================================================
    # IMPORTANT DIAGNOSTIC LOGGING
    #
    # We have already confirmed that Sarvam reaches this endpoint.
    # The production logs showed:
    #
    #   Unknown Sarvam job_state='' for job_id=...
    #
    # Therefore, log the exact payload received from Sarvam so we can
    # match our parser to the real production payload.
    #
    # This does NOT log API keys or callback authentication tokens.
    # ================================================================

    logger.warning(
        "Sarvam webhook raw payload: %s",
        json.dumps(
            body,
            default=str,
            ensure_ascii=False,
        ),
    )

    # ================================================================
    # 3. Extract job ID and job state
    # ================================================================

    # Sarvam normally sends these at the top level.
    #
    # We also defensively inspect common nested containers so that
    # a wrapper/proxy response does not cause the state to be lost.
    payload = body

    for container_key in (
        "data",
        "result",
        "payload",
    ):
        nested = body.get(container_key)

        if isinstance(nested, dict):
            if (
                nested.get("job_id")
                or nested.get("job_state")
                or nested.get("jobState")
                or nested.get("jobId")
            ):
                payload = nested
                break

    job_id = (
        body.get("job_id")
        or body.get("jobId")
        or payload.get("job_id")
        or payload.get("jobId")
    )

    job_state = str(
        body.get("job_state")
        or body.get("jobState")
        or payload.get("job_state")
        or payload.get("jobState")
        or ""
    ).strip().lower()

    if not job_id:
        logger.warning(
            "Sarvam webhook received without job_id: %s",
            body,
        )

        return JSONResponse(
            status_code=400,
            content={
                "error": "missing_job_id",
                "detail": "Payload has no job_id",
            },
        )

    logger.info(
        "Sarvam webhook received: job_id=%s job_state=%s",
        job_id,
        job_state,
    )

    # ================================================================
    # 4. Find call
    # ================================================================

    call = (
        db.query(Call)
        .filter(Call.sarvam_job_id == job_id)
        .first()
    )

    if not call:
        logger.warning(
            "No call found for Sarvam job_id=%s",
            job_id,
        )

        return {
            "status": "ignored",
            "detail": f"No call found for job_id {job_id}",
        }

    # ================================================================
    # 5. Idempotency
    # ================================================================

    if call.status in (
        "transcribed",
        "completed",
        "needs_review",
    ):
        logger.info(
            "Call %d already processed with status=%s",
            call.id,
            call.status,
        )

        return {
            "status": "already_processed",
            "call_id": call.id,
            "call_status": call.status,
        }

    # ================================================================
    # 6. Intermediate Sarvam states
    #
    # Do NOT attempt to download transcript here.
    # ================================================================

    if job_state in (
        "accepted",
        "pending",
        "running",
    ):
        if call.status != "transcribing":
            call.status = "transcribing"
            call.failure_reason = None
            db.commit()

        logger.info(
            "Sarvam job %s still running: %s",
            job_id,
            job_state,
        )

        return {
            "status": "acknowledged",
            "call_id": call.id,
            "job_id": job_id,
            "job_state": job_state,
        }

    # ================================================================
    # 7. Failed job
    # ================================================================

    if job_state == "failed":
        error_msg = (
            body.get("error_message")
            or body.get("error")
            or payload.get("error_message")
            or payload.get("error")
            or "Sarvam job reported failure"
        )

        call.status = "failed"
        call.failure_reason = str(error_msg)

        db.commit()

        logger.warning(
            "Sarvam job failed for call %d: %s",
            call.id,
            error_msg,
        )

        return {
            "status": "failed_recorded",
            "call_id": call.id,
            "job_id": job_id,
        }

    # ================================================================
    # 8. Only Completed can continue
    # ================================================================

    if job_state != "completed":
        logger.warning(
            "Unknown Sarvam job_state=%r for job_id=%s",
            job_state,
            job_id,
        )

        return {
            "status": "ignored",
            "call_id": call.id,
            "job_id": job_id,
            "job_state": job_state,
        }

    logger.info(
        "Sarvam job %s completed. Fetching transcript.",
        job_id,
    )

    # ================================================================
    # 9. Fetch completed transcript
    # ================================================================

    provider = get_stt_provider()

    effective_mode = (
        call.transcription_mode or "auto"
    )

    try:
        stt_result = provider.fetch_job_result(
            job_id=job_id,
            mode=effective_mode,
        )

    except Exception as exc:
        logger.exception(
            "Failed to fetch Sarvam result for call %d / job %s",
            call.id,
            job_id,
        )

        call.status = "failed"
        call.failure_reason = (
            f"stt_fetch_exception: {exc}"
        )

        db.commit()

        return {
            "status": "failed",
            "call_id": call.id,
            "reason": "stt_fetch_exception",
        }

    # ================================================================
    # 10. Record STT run
    # ================================================================

    stt_run = SttRun(
        call_id=call.id,
        provider=stt_result.provider or "sarvam",
        model=stt_result.model or "saaras:v4",
        audio_duration_seconds=(
            stt_result.audio_duration_seconds
        ),
        estimated_cost_inr=(
            stt_result.estimated_cost_inr
        ),
        latency_ms=stt_result.latency_ms,
        status=(
            "succeeded"
            if stt_result.success
            else stt_result.failure_reason or "failed"
        ),
        error_detail=stt_result.failure_detail,
        from_cache=stt_result.from_cache,
    )

    db.add(stt_run)
    db.flush()

    if not stt_result.success:
        call.status = "failed"

        call.failure_reason = (
            stt_result.failure_reason
            or stt_result.failure_detail
            or "stt_fetch_failed"
        )

        db.commit()

        return {
            "status": "failed",
            "call_id": call.id,
            "reason": call.failure_reason,
        }

    # ================================================================
    # 11. Duration
    # ================================================================

    if stt_result.audio_duration_seconds:
        call.duration_seconds = (
            stt_result.audio_duration_seconds
        )

    # ================================================================
    # 12. Normalize transcript
    # ================================================================

    segments = normalise_segments(
        stt_result.entries
    )

    if not segments:
        call.status = "failed"
        call.failure_reason = "empty_transcript"

        db.commit()

        logger.error(
            "Sarvam job %s completed but returned no transcript segments",
            job_id,
        )

        return {
            "status": "failed",
            "call_id": call.id,
            "reason": "empty_transcript",
        }

    # ================================================================
    # 13. Map speaker roles
    # ================================================================

    role_result = map_roles(segments)

    if (
        role_result.warning
        and len(
            set(
                s.speaker_label
                for s in segments
            )
        ) == 1
    ):
        call.failure_reason = (
            role_result.warning
        )

    # ================================================================
    # 14. Create TranscriptVersion
    # ================================================================

    latest_ver = (
        db.query(TranscriptVersion)
        .filter(
            TranscriptVersion.call_id == call.id
        )
        .order_by(
            TranscriptVersion.version_number.desc()
        )
        .first()
    )

    next_version = (
        latest_ver.version_number + 1
        if latest_ver
        else 1
    )

    lang_param = (
        "hi-IN"
        if effective_mode == "hi"
        else (
            "en-IN"
            if effective_mode == "en"
            else None
        )
    )

    new_version = TranscriptVersion(
        call_id=call.id,
        version_number=next_version,
        mode=effective_mode,
        provider=(
            stt_result.provider
            or "sarvam"
        ),
        provider_params_json={
            "model": (
                stt_result.model
                or "saaras:v4"
            ),
            "with_diarization": True,
            "language_code": lang_param,
        },
        detected_language=(
            stt_result.detected_language
        ),
        duration_seconds=(
            stt_result.audio_duration_seconds
        ),
        cost_est=(
            stt_result.estimated_cost_inr
            or 0.0
        ),
    )

    db.add(new_version)
    db.flush()

    # ================================================================
    # 15. Persist transcript segments
    # ================================================================

    for idx, seg in enumerate(
        role_result.segments
    ):
        seg_id = (
            f"seg_v{next_version}_{idx + 1:03d}"
            if next_version > 1
            else seg.segment_id
        )

        db_seg = TranscriptSegment(
            call_id=call.id,
            transcript_version_id=(
                new_version.id
            ),
            segment_id=seg_id,
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            speaker_label=seg.speaker_label,
            role=seg.role,
            role_confidence=(
                seg.role_confidence
            ),
            role_source=seg.role_source,
            text=seg.text,
        )

        db.add(db_seg)

    # ================================================================
    # 16. Invalidate older evaluations
    # ================================================================

    existing_evals = (
        db.query(Evaluation)
        .filter(
            Evaluation.call_id == call.id
        )
        .all()
    )

    for ev in existing_evals:
        ev.is_stale = True
        ev.stale_reason = (
            "stale: based on an older transcript"
        )

    # ================================================================
    # 17. Mark transcription complete
    # ================================================================

    call.status = "transcribed"
    call.failure_reason = None

    db.commit()

    logger.info(
        "Transcript persisted for call %d: %d segments",
        call.id,
        len(role_result.segments),
    )

    # ================================================================
    # 18. Run evaluation
    # ================================================================

    try:
        run_evaluation_pipeline(
            call_id=call.id,
            db=db,
            transcript_version_id=(
                new_version.id
            ),
        )

        db.refresh(call)

        logger.info(
            "Evaluation completed for call %d (status=%s)",
            call.id,
            call.status,
        )

    except Exception as eval_exc:
        logger.exception(
            "Evaluation failed after webhook for call %d: %s",
            call.id,
            eval_exc,
        )

        # Keep the call transcribed so the transcript
        # remains available and evaluation can be retried.

    return {
        "status": "success",
        "call_id": call.id,
        "call_status": call.status,
    }
