"""
backend/app/api/calls.py

REST endpoints for call upload, status, and transcript retrieval.
POST /calls     — multipart upload, kicks off background transcription
GET  /calls/{id}  — current call status
GET  /calls/{id}/transcript  — segments (only available after transcribing)
POST /calls/{id}/segments/{segment_id}/role  — manual role override
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.api.rubric import get_or_seed_active_rubric
from app.config.rubric_loader import load_rubric_yaml
from app.config.settings import get_settings
from app.services.stt import get_stt_provider
from app.db.engine import get_db
from app.models.orm import (
    Call,
    ComplianceFlag,
    Counsellor,
    Evaluation,
    EvaluationCriterion,
    EvaluationEvidence,
    LlmRun,
    TranscriptSegment,
    TranscriptVersion,
)
from app.models.schemas import (
    CallListItemOut,
    CallOut,
    ComplianceFlagOut,
    CriterionScoreOut,
    EvaluationOut,
    EvidenceOut,
    RetranscribeRequest,
    RoleOverride,
    SegmentOut,
    TranscriptOut,
)
from app.pipeline.evaluation_runner import run_evaluation_pipeline
from app.pipeline.roles import apply_role_override
from app.pipeline.runner import run_transcription_pipeline
from app.pipeline.segments import NormalisedSegment

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/calls", tags=["calls"])

def _get_uploads_dir() -> Path:
    settings = get_settings()
    p = settings.uploads_dir
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ensure_audio_file_on_disk(call: Call) -> Path:
    """Ensure audio file exists on disk for processing, restoring from audio_data if needed."""
    if call.file_path and Path(call.file_path).exists() and Path(call.file_path).stat().st_size > 0:
        return Path(call.file_path)

    if call.audio_data:
        uploads_dir = _get_uploads_dir()
        suffix = Path(call.original_filename or "audio.mp3").suffix.lower() or ".mp3"
        hash_prefix = call.file_hash[:12] if call.file_hash else f"call_{call.id}"
        dest = uploads_dir / f"restored_{call.id}_{hash_prefix}{suffix}"
        dest.write_bytes(call.audio_data)
        call.file_path = str(dest)
        return dest

    raise HTTPException(404, detail="Audio file data not found on disk or in database")


def _dispatch_transcription(call: Call, mode: str, background_tasks: BackgroundTasks, db: Session) -> None:
    """
    Dispatch transcription:
    - On Vercel / serverless: start an asynchronous Sarvam batch job and save job ID.
    - Local development: use FastAPI BackgroundTasks for local execution.
    """
    settings = get_settings()
    if settings.is_vercel_env or settings.effective_public_base_url:
        try:
            audio_path = _ensure_audio_file_on_disk(call)
            provider = get_stt_provider()
            callback_url = None
            if settings.effective_public_base_url:
                callback_url = f"{settings.effective_public_base_url}/api/webhooks/sarvam"
                if settings.sarvam_webhook_token:
                    callback_url += f"?token={settings.sarvam_webhook_token}"

            job_id, err = provider.start_batch_job(
                audio_path=audio_path,
                mode=mode,
                callback_url=callback_url,
                callback_token=settings.sarvam_webhook_token or None,
            )
            if job_id:
                call.sarvam_job_id = job_id
                call.status = "transcribing"
                call.failure_reason = None
                db.commit()
                db.refresh(call)
                logger.info("Call %d dispatched to Sarvam async job %s", call.id, job_id)
                return
            else:
                logger.warning("Failed to start Sarvam batch job for call %d: %s", call.id, err)
                call.status = "failed"
                call.failure_reason = (err.get("detail") if err else None) or "Failed to start Sarvam batch job"
                db.commit()
                db.refresh(call)
                return
        except Exception as exc:
            logger.exception("Error initiating async batch job for call %d: %s", call.id, exc)
            call.status = "failed"
            call.failure_reason = str(exc)
            db.commit()
            db.refresh(call)
            return

    # Local development fallback: background task in current process
    background_tasks.add_task(_run_pipeline_task, call.id, mode=mode)


def _compute_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@router.get("/upload-limits")
def get_upload_limits():
    """Return upload limits configured in environment."""
    settings = get_settings()
    return {
        "max_upload_mb": settings.effective_max_upload_mb,
        "max_upload_bytes": settings.max_upload_bytes,
        "max_audio_minutes": settings.max_audio_minutes,
        "allowed_audio_extensions": settings.allowed_audio_extensions_list,
    }


@router.post("", response_model=CallOut, status_code=202)
async def upload_call(
    background_tasks: BackgroundTasks,
    counsellor_id: int = Form(..., description="ID of the counsellor who made this call"),
    file: UploadFile = File(..., description="Audio file (mp3, wav, m4a, …)"),
    transcription_mode: str = Form("auto", description="Transcription language mode: auto, hi, hinglish, en"),
    db: Session = Depends(get_db),
):
    """
    Upload a counselling call recording.
    Processing (STT, role mapping) runs asynchronously.
    Poll GET /calls/{id} for status updates.
    """
    settings = get_settings()

    mode = transcription_mode.lower().strip()
    if mode not in ("auto", "hi", "hinglish", "en"):
        raise HTTPException(
            400,
            detail=f"Invalid transcription_mode '{transcription_mode}'. Allowed modes: auto, hi, hinglish, en",
        )

    # ── Validate file extension early ─────────────────────────────────────────
    suffix = Path(file.filename or "audio").suffix.lower()
    if suffix not in settings.allowed_audio_extensions_list:
        raise HTTPException(
            400,
            detail=f"File type '{suffix}' is not supported. Allowed formats: {', '.join(settings.allowed_audio_extensions_list)}",
        )

    # ── Validate counsellor exists ────────────────────────────────────────────
    counsellor = db.get(Counsellor, counsellor_id)
    if counsellor is None:
        raise HTTPException(404, detail=f"Counsellor {counsellor_id} not found")

    # ── Validate file size early (from Content-Length header if available) ─────
    content_length = int(file.size or 0)
    if content_length > settings.max_upload_bytes:
        raise HTTPException(
            413,
            detail=f"File too large: {content_length / 1e6:.1f} MB > {settings.effective_max_upload_mb} MB limit",
        )

    # ── Save file to disk ─────────────────────────────────────────────────────
    uploads_dir = _get_uploads_dir()
    tmp_path = uploads_dir / f"tmp_{int(time.time() * 1000)}_{file.filename}"
    try:
        with open(tmp_path, "wb") as out:
            while chunk := await file.read(65536):
                out.write(chunk)
    except Exception as exc:
        if tmp_path.exists():
            tmp_path.unlink()
        raise HTTPException(500, detail=f"Failed to save file: {exc}") from exc

    # ── Re-check file size after save (handles chunked uploads) ───────────────
    file_size = tmp_path.stat().st_size
    if file_size == 0:
        tmp_path.unlink()
        raise HTTPException(400, detail="Uploaded file is empty.")
    if file_size > settings.max_upload_bytes:
        tmp_path.unlink()
        raise HTTPException(
            413,
            detail=f"File is {file_size / 1e6:.1f} MB, exceeds {settings.effective_max_upload_mb} MB limit.",
        )

    # Read binary bytes for durable database persistence (BYTEA)
    file_bytes = tmp_path.read_bytes()

    # ── Compute hash and check for duplicates ─────────────────────────────────
    file_hash = _compute_file_hash(tmp_path)
    existing = db.query(Call).filter(Call.file_hash == file_hash).first()
    if existing:
        tmp_path.unlink()
        if existing.status == "failed":
            logger.info("Retrying failed call_id=%d on re-upload", existing.id)
            existing.status = "uploaded"
            existing.failure_reason = None
            existing.transcription_mode = mode
            if not existing.audio_data:
                existing.audio_data = file_bytes
            db.commit()
            db.refresh(existing)
            _dispatch_transcription(existing, mode, background_tasks, db)
            return existing
        logger.info("Duplicate upload detected; returning existing call_id=%d", existing.id)
        return existing

    # ── Rename to final path with hash in filename ────────────────────────────
    suffix = Path(file.filename or "audio").suffix.lower() or ".bin"
    final_path = uploads_dir / f"{file_hash[:16]}{suffix}"
    shutil.move(str(tmp_path), str(final_path))

    # ── Create Call record ────────────────────────────────────────────────────
    db_call = Call(
        counsellor_id=counsellor_id,
        file_hash=file_hash,
        original_filename=file.filename or "upload",
        file_path=str(final_path),
        file_size_bytes=file_size,
        status="uploaded",
        transcription_mode=mode,
        audio_data=file_bytes,
    )
    db.add(db_call)
    db.commit()
    db.refresh(db_call)

    # ── Dispatch processing (async Sarvam on Vercel, background tasks locally) ──
    _dispatch_transcription(db_call, mode, background_tasks, db)

    logger.info("call_id=%d accepted for processing (mode=%s)", db_call.id, mode)
    return db_call


def _run_pipeline_task(call_id: int, mode: Optional[str] = None) -> None:
    """Background task wrapper: creates its own DB session, runs STT, then evaluates."""
    from app.db.engine import SessionLocal
    db = SessionLocal()
    try:
        run_transcription_pipeline(call_id, db, mode=mode)
        call = db.get(Call, call_id)
        if call and call.status == "transcribed":
            try:
                run_evaluation_pipeline(call_id, db)
                logger.info("Auto-evaluation completed for call_id=%d", call_id)
            except Exception as eval_exc:
                logger.exception("Evaluation pipeline failed for call %d: %s", call_id, eval_exc)
    finally:
        db.close()


@router.post("/{call_id}/retry", response_model=CallOut)
def retry_call(
    call_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Retry a failed call. Clears failure_reason and restarts transcription.
    """
    call = db.get(Call, call_id)
    if not call:
        raise HTTPException(404, detail=f"Call {call_id} not found")

    call.status = "uploaded"
    call.failure_reason = None
    db.commit()
    db.refresh(call)

    _dispatch_transcription(call, call.transcription_mode or "auto", background_tasks, db)
    return call


@router.post("/{call_id}/retranscribe", response_model=CallOut)
def retranscribe_call(
    call_id: int,
    payload: RetranscribeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Re-transcribe call with specified language mode (auto, hi, hinglish, en).
    Creates a new transcript version with its own segments and marks existing evaluations as stale.
    Sets status to transcribed.
    """
    call = db.get(Call, call_id)
    if not call:
        raise HTTPException(404, detail=f"Call {call_id} not found")

    mode = payload.mode.lower().strip()
    if mode not in ("auto", "hi", "hinglish", "en"):
        raise HTTPException(
            400,
            detail=f"Invalid transcription_mode '{payload.mode}'. Allowed modes: auto, hi, hinglish, en",
        )

    call.transcription_mode = mode
    call.status = "transcribing"
    call.failure_reason = None
    db.commit()
    db.refresh(call)

    settings = get_settings()
    if settings.is_vercel_env or settings.effective_public_base_url:
        _dispatch_transcription(call, mode, background_tasks, db)
    else:
        run_transcription_pipeline(call.id, db, mode=mode)
        db.refresh(call)
    return call

@router.get("", response_model=list[CallListItemOut])
def list_calls(
    counsellor_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """List calls ordered by creation time descending."""
    query = db.query(Call)
    if counsellor_id is not None:
        query = query.filter(Call.counsellor_id == counsellor_id)
    if status is not None:
        query = query.filter(Call.status == status)
    calls = query.order_by(Call.created_at.desc()).limit(limit).all()

    result: list[CallListItemOut] = []
    for c in calls:
        eval_record = c.evaluation
        result.append(
            CallListItemOut(
                id=c.id,
                counsellor_id=c.counsellor_id,
                counsellor_name=c.counsellor.name if c.counsellor else f"Counsellor #{c.counsellor_id}",
                original_filename=c.original_filename,
                file_size_bytes=c.file_size_bytes,
                duration_seconds=c.duration_seconds,
                status=c.status,
                failure_reason=c.failure_reason,
                overall_score=eval_record.overall_score if eval_record else None,
                has_critical_flag=eval_record.has_critical_flag if eval_record else None,
                created_at=c.created_at,
            )
        )
    return result


@router.get("/{call_id}/audio")
def get_call_audio(call_id: int, db: Session = Depends(get_db)):
    """
    Stream audio file for the call.
    Prioritizes durable database audio_data (BYTEA) across serverless invocations,
    falling back to local file on disk for local development.
    """
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(404, detail=f"Call {call_id} not found")

    # 1. Prefer durable database audio_data
    if call.audio_data:
        suffix = Path(call.original_filename or call.file_path or "audio.mp3").suffix.lower()
        media_type = "audio/mpeg" if suffix in (".mp3", ".mpeg") else "audio/wav" if suffix == ".wav" else "audio/mp4"
        return Response(
            content=call.audio_data,
            media_type=media_type,
            headers={
                "Content-Disposition": f'inline; filename="{call.original_filename or "audio"}"',
                "Content-Length": str(len(call.audio_data)),
                "Accept-Ranges": "bytes",
            },
        )

    # 2. Fall back to local file on disk
    if call.file_path and Path(call.file_path).exists():
        suffix = Path(call.file_path).suffix.lower()
        media_type = "audio/mpeg" if suffix in (".mp3", ".mpeg") else "audio/wav" if suffix == ".wav" else "audio/mp4"
        return FileResponse(call.file_path, media_type=media_type)

    raise HTTPException(404, detail="Audio file not found on disk or in database")


@router.get("/{call_id}", response_model=CallOut)
def get_call(call_id: int, db: Session = Depends(get_db)):
    """Get the current status and metadata of a call."""
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(404, detail=f"Call {call_id} not found")
    return call


@router.get("/{call_id}/transcript", response_model=TranscriptOut)
def get_transcript(
    call_id: int,
    version: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """
    Return the transcript with speaker roles.
    Optionally select a specific version with ?version=<n> (defaults to latest).
    Returns 202 if transcription is still in progress.
    """
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(404, detail=f"Call {call_id} not found")

    if call.status in ("uploaded", "transcribing"):
        raise HTTPException(202, detail=f"Transcription in progress (status: {call.status})")

    if call.status == "failed" and not call.segments and not call.transcript_versions:
        raise HTTPException(
            422,
            detail=f"Transcription failed: {call.failure_reason or 'unknown error'}",
        )

    # Load available transcript versions
    versions = (
        db.query(TranscriptVersion)
        .filter(TranscriptVersion.call_id == call_id)
        .order_by(TranscriptVersion.version_number.asc())
        .all()
    )
    available_versions = [v.version_number for v in versions] if versions else [1]

    target_version = None
    if version is not None:
        target_version = next((v for v in versions if v.version_number == version), None)
        if target_version is None:
            raise HTTPException(404, detail=f"Transcript version {version} not found for call {call_id}")
    elif versions:
        target_version = versions[-1]

    seg_query = db.query(TranscriptSegment).filter(TranscriptSegment.call_id == call_id)
    if target_version is not None:
        has_v_segs = (
            db.query(TranscriptSegment)
            .filter(
                TranscriptSegment.call_id == call_id,
                TranscriptSegment.transcript_version_id == target_version.id,
            )
            .count()
        )
        if has_v_segs > 0:
            seg_query = seg_query.filter(TranscriptSegment.transcript_version_id == target_version.id)

    segments_db = seg_query.order_by(TranscriptSegment.start_ms).all()

    segments_out = [
        SegmentOut(
            segment_id=s.segment_id,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            speaker_label=s.speaker_label,
            role=s.role,
            role_confidence=s.role_confidence,
            role_source=s.role_source,
            text=s.text,
        )
        for s in segments_db
    ]

    return TranscriptOut(
        call_id=call_id,
        status=call.status,
        version_number=target_version.version_number if target_version else 1,
        transcript_version_id=target_version.id if target_version else None,
        mode=target_version.mode if target_version else getattr(call, "transcription_mode", "auto"),
        detected_language=target_version.detected_language if target_version else None,
        available_versions=available_versions,
        segments=segments_out,
    )


@router.post("/{call_id}/analyze", status_code=202)
def analyze_call(
    call_id: int,
    bypass_cache: bool = False,
    db: Session = Depends(get_db),
):
    """
    Explicitly run evaluation on the latest transcript version of the call.
    Returns estimated token count and evaluation summary.
    """
    call = db.get(Call, call_id)
    if not call:
        raise HTTPException(404, detail=f"Call {call_id} not found")

    if call.status in ("uploaded", "transcribing"):
        raise HTTPException(400, detail=f"Call is still transcribing (status: {call.status})")

    latest_tv = (
        db.query(TranscriptVersion)
        .filter(TranscriptVersion.call_id == call_id)
        .order_by(TranscriptVersion.version_number.desc())
        .first()
    )
    seg_query = db.query(TranscriptSegment).filter(TranscriptSegment.call_id == call_id)
    if latest_tv:
        has_v = (
            db.query(TranscriptSegment)
            .filter(
                TranscriptSegment.call_id == call_id,
                TranscriptSegment.transcript_version_id == latest_tv.id,
            )
            .count()
        )
        if has_v > 0:
            seg_query = seg_query.filter(TranscriptSegment.transcript_version_id == latest_tv.id)

    segments = seg_query.all()
    if not segments:
        raise HTTPException(400, detail="Cannot analyze call: no transcript segments found")

    word_count = sum(len(s.text.split()) for s in segments)
    estimated_tokens = int(word_count * 1.4) + 1200

    evaluation = run_evaluation_pipeline(
        call_id=call.id,
        db=db,
        bypass_cache=bypass_cache,
        transcript_version_id=latest_tv.id if latest_tv else None,
    )

    return {
        "status": "completed",
        "estimated_tokens": estimated_tokens,
        "call_id": call.id,
        "overall_score": evaluation.overall_score,
        "has_critical_flag": evaluation.has_critical_flag,
        "transcript_version_id": evaluation.transcript_version_id,
        "is_stale": evaluation.is_stale,
    }


@router.post("/{call_id}/segments/{segment_id}/role", response_model=TranscriptOut)
def override_role(
    call_id: int,
    segment_id: str,
    override: RoleOverride,
    db: Session = Depends(get_db),
):
    """
    Manually override the role for a speaker (all segments of that speaker
    are updated to maintain consistency).
    """
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(404, detail=f"Call {call_id} not found")

    segments_db = (
        db.query(TranscriptSegment)
        .filter(TranscriptSegment.call_id == call_id)
        .order_by(TranscriptSegment.start_ms)
        .all()
    )
    if not segments_db:
        raise HTTPException(404, detail="No transcript found for this call")

    # Convert ORM rows → NormalisedSegment for the helper function
    norm_segs = [
        NormalisedSegment(
            segment_id=s.segment_id,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            speaker_label=s.speaker_label,
            role=s.role,
            role_confidence=s.role_confidence,
            role_source=s.role_source,
            text=s.text,
        )
        for s in segments_db
    ]

    found = apply_role_override(norm_segs, segment_id, override.role)
    if not found:
        raise HTTPException(404, detail=f"Segment {segment_id} not found in call {call_id}")

    # Write overrides back to DB
    norm_by_id = {s.segment_id: s for s in norm_segs}
    for db_seg in segments_db:
        updated = norm_by_id[db_seg.segment_id]
        db_seg.role = updated.role
        db_seg.role_confidence = updated.role_confidence
        db_seg.role_source = updated.role_source

    db.commit()

    return TranscriptOut(
        call_id=call_id,
        status=call.status,
        segments=[
            SegmentOut(
                segment_id=s.segment_id,
                start_ms=s.start_ms,
                end_ms=s.end_ms,
                speaker_label=s.speaker_label,
                role=s.role,
                role_confidence=s.role_confidence,
                role_source=s.role_source,
                text=s.text,
            )
            for s in norm_segs
        ],
    )


def _run_analysis_task(call_id: int) -> None:
    """Background task wrapper for call analysis."""
    from app.db.engine import SessionLocal
    db = SessionLocal()
    try:
        run_evaluation_pipeline(call_id, db)
    finally:
        db.close()


@router.post("/{call_id}/retranscribe", status_code=202)
def retranscribe_call(
    call_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Re-run transcription pipeline on a call."""
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(404, detail=f"Call {call_id} not found")

    call.status = "uploaded"
    call.failure_reason = None
    db.commit()
    background_tasks.add_task(_run_pipeline_task, call.id)
    return {"message": "Transcription restarted", "call_id": call_id, "status": "uploaded"}


@router.post("/{call_id}/analyze", status_code=202)
def analyze_call(
    call_id: int,
    background_tasks: BackgroundTasks,
    sync: bool = False,
    bypass_cache: bool = False,
    db: Session = Depends(get_db),
):
    """
    Trigger full QA evaluation (rubric + compliance + verification + scoring).
    If sync=True, runs synchronously (useful for tests/CLI).
    Otherwise runs in background and returns 202 Accepted.
    """
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(404, detail=f"Call {call_id} not found")

    if call.status in ("uploaded", "transcribing"):
        raise HTTPException(
            400,
            detail=f"Call is not ready for analysis (current status: {call.status}). Wait for transcription to complete.",
        )

    if sync:
        eval_record = run_evaluation_pipeline(call_id, db, bypass_cache=bypass_cache)
        return {"message": "Analysis completed", "call_id": call_id, "status": call.status, "overall_score": eval_record.overall_score}

    call.status = "analyzing"
    db.commit()
    background_tasks.add_task(_run_analysis_task, call_id)
    return {"message": "Analysis started", "call_id": call_id, "status": "analyzing"}


@router.get("/{call_id}/evaluation", response_model=EvaluationOut)
def get_call_evaluation(call_id: int, db: Session = Depends(get_db)):
    """
    Retrieve full evaluation details: scores, verified evidence from DB, compliance flags, and timings.
    """
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(404, detail=f"Call {call_id} not found")

    if call.status == "analyzing":
        raise HTTPException(202, detail="Call is currently being analyzed")

    eval_record = (
        db.query(Evaluation).filter(Evaluation.call_id == call_id).first()
    )
    if eval_record is None:
        raise HTTPException(404, detail=f"No evaluation found for call {call_id}. Call status: {call.status}")

    # Build segment dictionary to populate db_segment_text and timestamps for display integrity
    segments = db.query(TranscriptSegment).filter(TranscriptSegment.call_id == call_id).all()
    seg_map = {s.segment_id: s for s in segments}

    # Fetch criteria scores and attached evidence
    rubric_criteria_map: dict[str, Any] = {}
    if eval_record.rubric_version and eval_record.rubric_version.criteria_json:
        for c in eval_record.rubric_version.criteria_json:
            rubric_criteria_map[c.get("criterion_id")] = c
    else:
        active_rubric = get_or_seed_active_rubric(db)
        if active_rubric and active_rubric.criteria_json:
            for c in active_rubric.criteria_json:
                rubric_criteria_map[c.get("criterion_id")] = c

    criteria_out: list[CriterionScoreOut] = []
    for cs in eval_record.criteria_scores:
        ev_items = [
            EvidenceOut(
                segment_id=ev.segment_id,
                quote=ev.quote,
                note=ev.note,
                verification_status=ev.verification_status,
                verification_detail=ev.verification_detail,
                db_segment_text=seg_map[ev.segment_id].text if ev.segment_id in seg_map else None,
                start_ms=seg_map[ev.segment_id].start_ms if ev.segment_id in seg_map else None,
                end_ms=seg_map[ev.segment_id].end_ms if ev.segment_id in seg_map else None,
            )
            for ev in eval_record.evidence_items
            if ev.criterion_id == cs.criterion_id and ev.flag_id is None
        ]
        crit_cfg = rubric_criteria_map.get(cs.criterion_id)
        if crit_cfg:
            c_name = crit_cfg.get("title") or crit_cfg.get("name")
            anchors = crit_cfg.get("anchors")
            if anchors and isinstance(anchors, dict):
                c_max = max(int(k) for k in anchors.keys())
            elif anchors and isinstance(anchors, list):
                c_max = len(anchors) - 1
            else:
                c_max = 4
        else:
            c_name = None
            c_max = 4

        criteria_out.append(
            CriterionScoreOut(
                criterion_id=cs.criterion_id,
                name=c_name,
                max_score=c_max,
                score=cs.score,
                weight=cs.weight_used,
                confidence=cs.confidence,
                rationale=cs.rationale,
                not_applicable=cs.not_applicable,
                evidence=ev_items,
            )
        )

    # Fetch compliance flags and attached evidence
    flags_out: list[ComplianceFlagOut] = []
    for f in eval_record.compliance_flags:
        ev_items = [
            EvidenceOut(
                segment_id=ev.segment_id,
                quote=ev.quote,
                note=ev.note,
                verification_status=ev.verification_status,
                verification_detail=ev.verification_detail,
                db_segment_text=seg_map[ev.segment_id].text if ev.segment_id in seg_map else None,
                start_ms=seg_map[ev.segment_id].start_ms if ev.segment_id in seg_map else None,
                end_ms=seg_map[ev.segment_id].end_ms if ev.segment_id in seg_map else None,
            )
            for ev in eval_record.evidence_items
            if ev.flag_id == f.id
        ]
        flags_out.append(
            ComplianceFlagOut(
                rule_id=f.rule_id,
                severity=f.severity,
                confidence=f.confidence,
                explanation=f.explanation,
                status=f.status,
                evidence=ev_items,
            )
        )

    # Calculate LLM cost from LlmRuns
    llm_runs = db.query(LlmRun).filter(LlmRun.call_id == call_id).all()
    total_cost = sum(r.estimated_cost_usd or 0.0 for r in llm_runs)

    timing = {
        "t_upload_s": call.t_upload_s,
        "t_stt_s": call.t_stt_s,
        "t_llm_s": call.t_llm_s,
        "duration_seconds": call.duration_seconds,
    }

    rubric_cfg = load_rubric_yaml()
    min_review_score = getattr(rubric_cfg, "min_overall_score_for_review", 50.0)
    min_gate_conf = getattr(rubric_cfg, "min_confidence_for_gate", 0.70)
    low_conf_thresh = getattr(rubric_cfg, "low_confidence_display_threshold", 0.70)

    return EvaluationOut(
        call_id=call.id,
        rubric_version=eval_record.rubric_version.version if eval_record.rubric_version else 1,
        overall_score=eval_record.overall_score,
        has_critical_flag=eval_record.has_critical_flag,
        status=call.status,
        transcript_version_id=eval_record.transcript_version_id,
        is_stale=eval_record.is_stale,
        stale_reason=eval_record.stale_reason,
        min_overall_score_for_review=min_review_score,
        min_confidence_for_gate=min_gate_conf,
        low_confidence_display_threshold=low_conf_thresh,
        criteria=criteria_out,
        compliance_flags=flags_out,
        timing=timing,
        llm_cost_usd=round(total_cost, 4),
        coaching=eval_record.coaching.content_json if eval_record.coaching else None,
        created_at=eval_record.created_at,
    )

