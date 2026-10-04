"""
backend/tests/test_transcription_versions.py
Tests covering Language/Transcription Mode & Transcript Versions (Requirement C.5):
  (a) re-transcribe creates a new version with new segment ids
  (b) old evaluation is marked stale
  (c) evidence from an old version cannot be verified against the new one
  (d) invalid mode is rejected
  (e) STT failure leaves the previous version intact
"""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.db.engine import Base, SessionLocal, engine
from app.main import app
from app.models.orm import (
    Call,
    Counsellor,
    Evaluation,
    LlmRun,
    RubricVersion,
    SttRun,
    TranscriptSegment,
    TranscriptVersion,
)
from app.pipeline.verify import verify_evidence_item
from app.services.stt import DiarizedEntry, STTResult


@pytest.fixture
def test_setup():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # Clean up test call and all related foreign key records
    db.query(SttRun).filter(SttRun.call_id == 888).delete()
    db.query(LlmRun).filter(LlmRun.call_id == 888).delete()
    db.query(Evaluation).filter(Evaluation.call_id == 888).delete()
    db.query(TranscriptSegment).filter(TranscriptSegment.call_id == 888).delete()
    db.query(TranscriptVersion).filter(TranscriptVersion.call_id == 888).delete()
    db.query(Call).filter(Call.id == 888).delete()

    c = db.query(Counsellor).filter(Counsellor.id == 88).first()
    if not c:
        c = Counsellor(id=88, name="Language Test Counsellor", email="lang_test@pw.live")
        db.add(c)
        db.commit()

    # Initial call
    call = Call(
        id=888,
        counsellor_id=88,
        file_hash="testhash888",
        original_filename="call_language_test.mp3",
        file_path="uploads/call_language_test.mp3",
        file_size_bytes=24000,
        status="transcribed",
        transcription_mode="auto",
    )
    db.add(call)
    db.commit()

    # Version 1
    v1 = TranscriptVersion(
        call_id=888,
        version_number=1,
        mode="auto",
        provider="sarvam",
        detected_language="hi",
        duration_seconds=120.0,
    )
    db.add(v1)
    db.flush()

    s1 = TranscriptSegment(
        call_id=888,
        transcript_version_id=v1.id,
        segment_id="seg_001",
        start_ms=0,
        end_ms=4000,
        speaker_label="speaker_0",
        role="counsellor",
        text="Namaste, main Physics Wallah se counselling team se baat kar raha hoon.",
    )
    s2 = TranscriptSegment(
        call_id=888,
        transcript_version_id=v1.id,
        segment_id="seg_002",
        start_ms=4500,
        end_ms=9000,
        speaker_label="speaker_1",
        role="student",
        text="Haan ji, mujhe JEE batch ke baare mein jaanna hai.",
    )
    db.add_all([s1, s2])

    # Rubric version
    rv = db.query(RubricVersion).filter(RubricVersion.version == 1).first()
    rv_id = rv.id if rv else 1

    # Evaluation for Version 1
    eval_rec = Evaluation(
        call_id=888,
        rubric_version_id=rv_id,
        transcript_version_id=v1.id,
        overall_score=85.0,
        has_critical_flag=False,
        is_stale=False,
    )
    db.add(eval_rec)
    db.commit()

    yield db, call, v1

    # Teardown
    db.query(SttRun).filter(SttRun.call_id == 888).delete()
    db.query(LlmRun).filter(LlmRun.call_id == 888).delete()
    db.query(Evaluation).filter(Evaluation.call_id == 888).delete()
    db.query(TranscriptSegment).filter(TranscriptSegment.call_id == 888).delete()
    db.query(TranscriptVersion).filter(TranscriptVersion.call_id == 888).delete()
    db.query(Call).filter(Call.id == 888).delete()
    db.commit()
    db.close()


def test_retranscribe_creates_new_version_with_new_segment_ids(test_setup):
    """(a) re-transcribe creates a new version with new segment ids."""
    db, call, v1 = test_setup
    client = TestClient(app)

    fake_entries = [
        DiarizedEntry(speaker_label="speaker_0", text="नमस्ते, मैं फिजिक्स वाला से बोल रहा हूँ।", start_time_seconds=0.0, end_time_seconds=4.0),
        DiarizedEntry(speaker_label="speaker_1", text="हाँ जी, मुझे जेईई बैच की जानकारी चाहिए।", start_time_seconds=4.5, end_time_seconds=9.0),
        DiarizedEntry(speaker_label="speaker_0", text="जरूर, आप अभी किस कक्षा में पढ़ाई कर रहे हैं?", start_time_seconds=9.5, end_time_seconds=14.0),
    ]
    mock_stt_result = STTResult(
        success=True,
        entries=fake_entries,
        provider="sarvam",
        audio_duration_seconds=120.0,
        detected_language="hi-IN",
        mode="hi",
    )

    with patch("app.pipeline.runner.get_stt_provider") as mock_get_provider:
        mock_provider = MagicMock()
        mock_provider.transcribe.return_value = mock_stt_result
        mock_get_provider.return_value = mock_provider

        res = client.post("/calls/888/retranscribe", json={"mode": "hi"})
        assert res.status_code == 200

        # Check versions in DB
        versions = db.query(TranscriptVersion).filter(TranscriptVersion.call_id == 888).order_by(TranscriptVersion.version_number).all()
        assert len(versions) == 2
        v2 = versions[1]
        assert v2.version_number == 2
        assert v2.mode == "hi"

        # Check segments in DB for version 2
        v2_segments = db.query(TranscriptSegment).filter(TranscriptSegment.transcript_version_id == v2.id).all()
        assert len(v2_segments) == 3
        # Ensure new segment IDs are unique per version
        seg_ids = [s.segment_id for s in v2_segments]
        assert all(sid.startswith("seg_v2_") for sid in seg_ids)
        assert "seg_v2_001" in seg_ids
        assert "seg_v2_002" in seg_ids

        # Ensure old version 1 segments are still intact
        v1_segments = db.query(TranscriptSegment).filter(TranscriptSegment.transcript_version_id == v1.id).all()
        assert len(v1_segments) == 2
        assert "seg_001" in [s.segment_id for s in v1_segments]


def test_old_evaluation_marked_stale_on_retranscribe(test_setup):
    """(b) old evaluation is marked stale upon re-transcription."""
    db, call, v1 = test_setup
    client = TestClient(app)

    fake_entries = [
        DiarizedEntry(speaker_label="speaker_0", text="English greeting from Physics Wallah counselling.", start_time_seconds=0.0, end_time_seconds=4.0),
        DiarizedEntry(speaker_label="speaker_1", text="Yes I need information on JEE courses.", start_time_seconds=4.5, end_time_seconds=9.0),
        DiarizedEntry(speaker_label="speaker_0", text="Which class are you currently studying in?", start_time_seconds=9.5, end_time_seconds=14.0),
    ]
    mock_stt_result = STTResult(
        success=True,
        entries=fake_entries,
        provider="sarvam",
        audio_duration_seconds=120.0,
        detected_language="en-IN",
        mode="en",
    )

    with patch("app.pipeline.runner.get_stt_provider") as mock_get_provider:
        mock_provider = MagicMock()
        mock_provider.transcribe.return_value = mock_stt_result
        mock_get_provider.return_value = mock_provider

        res = client.post("/calls/888/retranscribe", json={"mode": "en"})
        assert res.status_code == 200

        # Verify old evaluation is marked stale
        db.expire_all()
        evaluation = db.query(Evaluation).filter(Evaluation.call_id == 888).first()
        assert evaluation is not None
        assert evaluation.is_stale is True
        assert "stale: based on an older transcript" in evaluation.stale_reason


def test_evidence_from_old_version_cannot_verify_against_new_version(test_setup):
    """(c) evidence from an old version cannot be verified against the new one."""
    db, call, v1 = test_setup

    # Version 2 segments
    v2_segments_by_id = {
        "seg_v2_001": MagicMock(segment_id="seg_v2_001", role="counsellor", text="नमस्ते, मैं फिजिक्स वाला से बोल रहा हूँ।"),
        "seg_v2_002": MagicMock(segment_id="seg_v2_002", role="student", text="हाँ जी, मुझे जानकारी चाहिए।"),
    }

    # Attempt to verify an evidence item citing an old version segment ID ("seg_001")
    old_evidence_quote = "Physics Wallah se counselling"
    ver_res = verify_evidence_item(
        segment_id="seg_001",
        quote=old_evidence_quote,
        segments_by_id=v2_segments_by_id,
        require_role="counsellor",
    )
    assert not ver_res.is_verified
    assert ver_res.status == "rejected_missing_segment"


def test_invalid_transcription_mode_rejected(test_setup):
    """(d) invalid mode is rejected."""
    client = TestClient(app)

    # 1. On retranscribe endpoint
    res = client.post("/calls/888/retranscribe", json={"mode": "klingon"})
    assert res.status_code == 422 or res.status_code == 400

    # 2. On upload endpoint
    res_upload = client.post(
        "/calls",
        data={"counsellor_id": 88, "transcription_mode": "invalid_mode"},
        files={"file": ("test.mp3", b"dummy audio content", "audio/mpeg")},
    )
    assert res_upload.status_code == 400
    assert "Invalid transcription_mode" in res_upload.json()["detail"]


def test_stt_failure_leaves_previous_version_intact(test_setup):
    """(e) STT failure leaves the previous version intact."""
    db, call, v1 = test_setup
    client = TestClient(app)

    mock_failed_stt = STTResult(
        success=False,
        failure_reason="rate_limit",
        failure_detail="429 Rate limit exceeded on Sarvam STT",
        provider="sarvam",
    )

    with patch("app.pipeline.runner.get_stt_provider") as mock_get_provider:
        mock_provider = MagicMock()
        mock_provider.transcribe.return_value = mock_failed_stt
        mock_get_provider.return_value = mock_provider

        res = client.post("/calls/888/retranscribe", json={"mode": "hi"})
        assert res.status_code == 200

        # Check call status is failed with failure_reason
        db.expire_all()
        reloaded_call = db.get(Call, 888)
        assert reloaded_call.status == "failed"
        assert reloaded_call.failure_reason == "rate_limit"

        # Previous version 1 must remain intact and NO version 2 created
        versions = db.query(TranscriptVersion).filter(TranscriptVersion.call_id == 888).all()
        assert len(versions) == 1
        assert versions[0].version_number == 1

        # Previous segments must remain intact
        segments = db.query(TranscriptSegment).filter(TranscriptSegment.call_id == 888).all()
        assert len(segments) == 2
        assert "seg_001" in [s.segment_id for s in segments]
