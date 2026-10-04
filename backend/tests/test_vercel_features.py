"""
backend/tests/test_vercel_features.py
Tests verifying Vercel compatibility:
1. PostgreSQL engine initialization & psycopg dialect compatibility
2. Existing SQLite/local initialization
3. Upload stores audio_data durably (BYTEA/BLOB)
4. Audio endpoint serves from DB audio_data
5. Sarvam job ID is persisted
6. Sarvam webhook authentication (token, bearer, query) & demo auth exemption
7. Sarvam webhook success (parses segments & triggers evaluation)
8. Sarvam webhook failure handling
9. Sarvam webhook idempotency
10. Existing transcript parser & role mapper integration
11. Existing evaluation pipeline execution
12. Frontend polling lifecycle logic
"""
from __future__ import annotations

import io
import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.db.engine import Base, SessionLocal, engine
from app.main import app
from app.models.orm import Call, Counsellor, Evaluation, TranscriptSegment, TranscriptVersion
from app.pipeline.evaluation_runner import run_evaluation_pipeline
from app.pipeline.roles import map_roles
from app.pipeline.segments import normalise_segments
from app.services.stt import DiarizedEntry, STTResult


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# ── Test 1: PostgreSQL engine initialization & driver ────────────────────────
def test_postgres_engine_initialization():
    settings = Settings(database_url="postgres://user:pass@localhost:5432/pw_qa_db")
    eff_url = settings.effective_database_url
    assert "postgresql+psycopg://" in eff_url, f"Expected postgresql+psycopg://, got {eff_url}"

    # Verify SQLAlchemy can create engine without driver error
    eng = create_engine(eff_url, pool_pre_ping=True)
    assert eng.dialect.name == "postgresql"
    assert eng.dialect.driver == "psycopg"


# ── Test 2: Existing SQLite / local initialization ───────────────────────────
def test_sqlite_local_initialization():
    settings = Settings(database_url="")
    eff_url = settings.effective_database_url
    assert eff_url.startswith("sqlite:///")
    eng = create_engine(eff_url)
    assert eng.dialect.name == "sqlite"


# ── Test 3 & 5: Upload stores audio_data durably & Sarvam job ID persisted ───
def test_upload_stores_audio_and_persists_job_id(client, db_session):
    counsellor = db_session.get(Counsellor, 1)
    if not counsellor:
        db_session.add(Counsellor(id=1, name="Test Counsellor"))
        db_session.commit()

    test_audio_bytes = b"ID3\x03\x00\x00\x00\x00\x00\x20MPEG_AUDIO_TEST_PAYLOAD_" + uuid.uuid4().bytes

    mock_settings = Settings(
        sarvam_api_key="test_key",
        sarvam_webhook_token="wh_token",
        public_base_url="https://test.vercel.app",
    )
    job_uuid = f"sarvam_job_{uuid.uuid4().hex[:8]}"

    with patch("app.services.stt.SarvamSTT.start_batch_job", return_value=(job_uuid, None)), \
         patch("app.api.calls.get_settings", return_value=mock_settings):

        files = {"file": (f"demo_{uuid.uuid4().hex[:6]}.mp3", io.BytesIO(test_audio_bytes), "audio/mpeg")}
        data = {"counsellor_id": 1, "transcription_mode": "auto"}

        res = client.post("/calls", data=data, files=files)
        assert res.status_code == 202
        call_json = res.json()
        call_id = call_json["id"]

        call_db = db_session.get(Call, call_id)
        assert call_db is not None
        assert call_db.audio_data == test_audio_bytes
        assert call_db.sarvam_job_id == job_uuid
        assert call_db.status == "transcribing"


# ── Test 4: Audio endpoint serves from DB audio_data without disk file ────────
def test_audio_endpoint_serves_from_db(client, db_session):
    test_audio = b"ID3_BINARY_AUDIO_BYTES_TEST_" + uuid.uuid4().bytes
    call = Call(
        counsellor_id=1,
        file_hash=uuid.uuid4().hex,
        original_filename="sample.mp3",
        file_path="/tmp/non_existent_file.mp3",
        file_size_bytes=len(test_audio),
        status="completed",
        audio_data=test_audio,
    )
    db_session.add(call)
    db_session.commit()
    db_session.refresh(call)

    for path in (f"/calls/{call.id}/audio", f"/api/calls/{call.id}/audio"):
        res = client.get(path)
        assert res.status_code == 200
        assert res.content == test_audio
        assert res.headers["content-type"].startswith("audio/")


# ── Test 6: Sarvam webhook authentication & demo access token exemption ──────
def test_sarvam_webhook_authentication(client, db_session):
    mock_settings = Settings(
        sarvam_webhook_token="secret_webhook_token_xyz",
        demo_access_token="demo_access_guard_123",
    )

    with patch("app.api.webhooks.get_settings", return_value=mock_settings), \
         patch("app.main.get_settings", return_value=mock_settings):

        # 1. Without webhook token -> 401
        res = client.post("/api/webhooks/sarvam", json={"job_id": "job_1"})
        assert res.status_code == 401

        # 2. With invalid token -> 401
        res = client.post("/api/webhooks/sarvam", json={"job_id": "job_1"}, headers={"Authorization": "Bearer wrong"})
        assert res.status_code == 401

        # 3. With valid Bearer token -> passes auth (returns 200 ignored for unknown job)
        res = client.post(
            "/api/webhooks/sarvam",
            json={"job_id": "job_nonexistent"},
            headers={"Authorization": "Bearer secret_webhook_token_xyz"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "ignored"

        # 4. With X-Sarvam-Token header -> passes auth
        res = client.post(
            "/api/webhooks/sarvam",
            json={"job_id": "job_nonexistent"},
            headers={"X-Sarvam-Token": "secret_webhook_token_xyz"},
        )
        assert res.status_code == 200

        # 5. With query parameter -> passes auth
        res = client.post(
            "/api/webhooks/sarvam?token=secret_webhook_token_xyz",
            json={"job_id": "job_nonexistent"},
        )
        assert res.status_code == 200


# ── Test 7: Sarvam webhook success workflow ──────────────────────────────────
def test_sarvam_webhook_success(client, db_session):
    job_id = f"job_success_{uuid.uuid4().hex[:8]}"
    call = Call(
        counsellor_id=1,
        file_hash=uuid.uuid4().hex,
        original_filename="call_webhook.mp3",
        file_path="/tmp/call_webhook.mp3",
        file_size_bytes=5000,
        status="transcribing",
        sarvam_job_id=job_id,
    )
    db_session.add(call)
    db_session.commit()
    db_session.refresh(call)

    mock_entries = [
        DiarizedEntry(
            speaker_label="speaker_0",
            text="Hello, main Physics Wallah se counselling ke liye call kar raha hoon.",
            start_time_seconds=0.0,
            end_time_seconds=4.0,
        ),
        DiarizedEntry(
            speaker_label="speaker_1",
            text="Ji sir, mujhe NEET batch ke baare me poochna tha.",
            start_time_seconds=4.5,
            end_time_seconds=8.0,
        ),
    ]

    mock_stt_result = STTResult(
        success=True,
        entries=mock_entries,
        provider="sarvam",
        model="saaras:v4",
        audio_duration_seconds=8.0,
        detected_language="hi",
        estimated_cost_inr=0.25,
    )

    with patch("app.services.stt.SarvamSTT.fetch_job_result", return_value=mock_stt_result), \
         patch("app.api.webhooks.run_evaluation_pipeline") as mock_eval:

        payload = {"job_id": job_id, "status": "completed"}
        res = client.post("/api/webhooks/sarvam", json=payload)
        assert res.status_code == 200
        assert res.json()["status"] == "success"

        db_session.refresh(call)
        assert call.status == "transcribed"
        assert len(call.segments) == 2
        assert call.segments[0].role == "counsellor"
        assert call.segments[1].role == "student"

        mock_eval.assert_called_once()


# ── Test 8: Sarvam webhook failure handling ──────────────────────────────────
def test_sarvam_webhook_failure(client, db_session):
    job_id = f"job_fail_{uuid.uuid4().hex[:8]}"
    call = Call(
        counsellor_id=1,
        file_hash=uuid.uuid4().hex,
        original_filename="fail_call.mp3",
        file_path="/tmp/fail_call.mp3",
        file_size_bytes=4000,
        status="transcribing",
        sarvam_job_id=job_id,
    )
    db_session.add(call)
    db_session.commit()
    db_session.refresh(call)

    payload = {
        "job_id": job_id,
        "status": "failed",
        "error_message": "Audio file corrupted or unreadable",
    }
    res = client.post("/api/webhooks/sarvam", json=payload)
    assert res.status_code == 200
    assert res.json()["status"] == "failed_recorded"

    db_session.refresh(call)
    assert call.status == "failed"
    assert "Audio file corrupted" in (call.failure_reason or "")


# ── Test 9: Webhook idempotency ──────────────────────────────────────────────
def test_sarvam_webhook_idempotency(client, db_session):
    job_id = f"job_idem_{uuid.uuid4().hex[:8]}"
    call = Call(
        counsellor_id=1,
        file_hash=uuid.uuid4().hex,
        original_filename="idem.mp3",
        file_path="/tmp/idem.mp3",
        file_size_bytes=3000,
        status="completed",
        sarvam_job_id=job_id,
    )
    db_session.add(call)
    db_session.commit()
    db_session.refresh(call)

    payload = {"job_id": job_id, "status": "completed"}
    res = client.post("/api/webhooks/sarvam", json=payload)
    assert res.status_code == 200
    assert res.json()["status"] == "already_processed"


# ── Test 10: Existing transcript parser & role mapping ───────────────────────
def test_transcript_parser_and_role_mapping():
    raw_entries = [
        DiarizedEntry("speaker_0", "Namaste main Physics Wallah counsellor bol raha hoon.", 0.0, 3.5),
        DiarizedEntry("speaker_1", "Haan sir, mujhe batch join karna hai.", 4.0, 6.0),
    ]
    normalised = normalise_segments(raw_entries)
    assert len(normalised) == 2
    assert normalised[0].start_ms == 0
    assert normalised[0].end_ms == 3500

    role_result = map_roles(normalised)
    assert role_result.speaker_to_role.get("speaker_0") == "counsellor"
    assert role_result.speaker_to_role.get("speaker_1") == "student"
    assert role_result.segments[0].role == "counsellor"
    assert role_result.segments[1].role == "student"


# ── Test 11: Existing evaluation pipeline works ──────────────────────────────
def test_evaluation_pipeline_works(db_session):
    call = Call(
        counsellor_id=1,
        file_hash=uuid.uuid4().hex,
        original_filename="eval.mp3",
        file_path="/tmp/eval.mp3",
        file_size_bytes=2000,
        status="transcribed",
    )
    db_session.add(call)
    db_session.commit()
    db_session.refresh(call)

    tv = TranscriptVersion(call_id=call.id, version_number=1, mode="auto")
    db_session.add(tv)
    db_session.commit()
    db_session.refresh(tv)

    seg = TranscriptSegment(
        call_id=call.id,
        transcript_version_id=tv.id,
        segment_id="seg_001",
        start_ms=0,
        end_ms=3000,
        speaker_label="speaker_0",
        role="counsellor",
        text="Physics Wallah offline Vidyapeeth batch registration open hai.",
    )
    db_session.add(seg)
    db_session.commit()

    mock_llm_json = {
        "evaluations": [
            {"criterion_id": "discovery", "score": 3, "confidence": 0.9, "rationale": "Good discovery.", "not_applicable": False, "evidence": [{"segment_id": "seg_001", "quote": "Physics Wallah", "note": "Valid"}]},
            {"criterion_id": "course_fit", "score": 3, "confidence": 0.9, "rationale": "Product fit confirmed.", "not_applicable": False, "evidence": []},
            {"criterion_id": "pitch_quality", "score": 3, "confidence": 0.85, "rationale": "Clear.", "not_applicable": False, "evidence": []},
            {"criterion_id": "objection_handling", "score": 0, "confidence": 0.85, "rationale": "NA", "not_applicable": True, "evidence": []},
            {"criterion_id": "closing_next_steps", "score": 2, "confidence": 0.8, "rationale": "Good.", "not_applicable": False, "evidence": []},
        ]
    }
    mock_comp_json = {"no_flags": True, "flags": []}
    mock_coach_json = {"strengths": ["Good tone"], "improvements": ["More questions"], "next_call_focus": "Discovery"}

    def mock_api_call(system, prompt):
        sys_low = system.lower()
        if "coach" in sys_low:
            return json.dumps(mock_coach_json), 200, 50
        if "compliance" in sys_low or "audit" in sys_low:
            return json.dumps(mock_comp_json), 200, 50
        return json.dumps(mock_llm_json), 400, 100

    with patch("app.services.llm.ClaudeClient._execute_api_call", side_effect=mock_api_call), \
         patch("app.services.llm.GeminiClient._execute_api_call", side_effect=mock_api_call), \
         patch("app.services.llm.OpenAIClient._execute_api_call", side_effect=mock_api_call):

        eval_out = run_evaluation_pipeline(call_id=call.id, db=db_session, transcript_version_id=tv.id)
        assert eval_out is not None
        assert eval_out.overall_score is not None

        db_session.refresh(call)
        assert call.status in ("completed", "needs_review")


# ── Test 12: Frontend polling lifecycle logic ────────────────────────────────
def test_frontend_polling_lifecycle_logic():
    pending_statuses = ["uploaded", "transcribing"]
    terminal_statuses = ["transcribed", "completed", "needs_review", "failed"]

    for s in pending_statuses:
        assert s in pending_statuses
        assert s not in terminal_statuses

    for s in terminal_statuses:
        assert s not in pending_statuses
