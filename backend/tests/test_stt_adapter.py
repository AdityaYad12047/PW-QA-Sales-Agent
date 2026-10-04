"""
backend/tests/test_stt_adapter.py
Tests for services/stt.py using mocked provider responses.
No real API calls are made in these tests.
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.stt import (
    DiarizedEntry,
    STTResult,
    SarvamSTT,
    _file_sha256,
)
from app.pipeline.segments import normalise_text_for_matching


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

import os

def make_audio_file(suffix: str = ".mp3", size: int = 1024) -> Path:
    """Create a temporary fake audio file with random bytes for testing."""
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(os.urandom(size))
    tmp.close()
    return Path(tmp.name)


MOCK_SARVAM_OUTPUT = {
    "transcript": "Hello how can I help you today?",
    "diarized_transcript": {
        "entries": [
            {"speaker_id": "speaker_0", "text": "Hello how can I help you today?",
             "start_time_seconds": 0.01, "end_time_seconds": 2.5},
            {"speaker_id": "speaker_1", "text": "I have a question about the course.",
             "start_time_seconds": 2.8, "end_time_seconds": 5.2},
        ]
    }
}


# ─────────────────────────────────────────────────────────────────────────────
# File validation tests
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_empty_file():
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        path = Path(f.name)
    # File exists but is 0 bytes
    stt = SarvamSTT(api_key="fake")
    result = stt.transcribe(path)
    assert not result.success
    assert result.failure_reason == "empty_audio"
    path.unlink(missing_ok=True)


def test_validate_unsupported_format():
    audio = make_audio_file(suffix=".xyz")
    stt = SarvamSTT(api_key="fake")
    result = stt.transcribe(audio)
    assert not result.success
    assert result.failure_reason == "unsupported_format"
    audio.unlink(missing_ok=True)


def test_validate_file_not_found():
    stt = SarvamSTT(api_key="fake")
    result = stt.transcribe(Path("/nonexistent/audio.mp3"))
    assert not result.success
    assert result.failure_reason == "file_not_found"


def test_missing_api_key():
    audio = make_audio_file(suffix=".mp3")
    stt = SarvamSTT(api_key="")  # empty key
    result = stt.transcribe(audio)
    assert not result.success
    assert result.failure_reason == "missing_api_key"
    audio.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Successful transcription (mocked SDK)
# ─────────────────────────────────────────────────────────────────────────────

def test_successful_transcription_with_mock():
    """Mock the sarvamai SDK to return a valid diarized transcript."""
    audio = make_audio_file(suffix=".mp3")

    # Build a mock job
    mock_job = MagicMock()
    mock_client = MagicMock()
    mock_client.speech_to_text_job.create_job.return_value = mock_job

    # Simulate download_outputs writing a JSON file
    def fake_download(output_dir: str):
        out = Path(output_dir) / "result.json"
        out.write_text(json.dumps(MOCK_SARVAM_OUTPUT), encoding="utf-8")

    mock_job.download_outputs.side_effect = fake_download

    with patch("app.services.stt.SarvamSTT._call_sarvam") as mock_call:
        mock_call.return_value = STTResult(
            success=True,
            entries=[
                DiarizedEntry("speaker_0", "Hello how can I help you today?", 0.01, 2.5),
                DiarizedEntry("speaker_1", "I have a question about the course.", 2.8, 5.2),
            ],
            raw_transcript="Hello how can I help you today?",
            provider="sarvam",
            model="saaras:v4",
            audio_duration_seconds=5.2,
            estimated_cost_inr=5.2 / 3600 * 45.0,
        )

        stt = SarvamSTT(api_key="fake_key")
        result = stt.transcribe(audio)

    assert result.success
    assert len(result.entries) == 2
    assert result.entries[0].speaker_label == "speaker_0"
    assert result.entries[1].speaker_label == "speaker_1"
    assert result.audio_duration_seconds == pytest.approx(5.2)

    audio.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Cache tests
# ─────────────────────────────────────────────────────────────────────────────

def test_cache_hit_skips_api_call():
    """Second call for same file should return from cache without calling API."""
    import tempfile
    audio = make_audio_file(suffix=".mp3", size=2048)

    # Inject a result via the cache layer
    from app.services.stt import _cache_path, _save_cache
    from app.config.settings import get_settings
    settings = get_settings()

    file_hash = _file_sha256(audio)
    cache_dir = settings.cache_path / "stt"
    cache_file = _cache_path(cache_dir, file_hash, "sarvam")

    fake_result = STTResult(
        success=True,
        entries=[DiarizedEntry("speaker_0", "Cached text", 0.0, 1.0)],
        raw_transcript="Cached text",
        provider="sarvam",
        model="saaras:v4",
        audio_duration_seconds=1.0,
        estimated_cost_inr=0.0125,
    )
    _save_cache(cache_file, fake_result)

    stt = SarvamSTT(api_key="fake")
    result = stt.transcribe(audio)

    assert result.success
    assert result.from_cache is True
    assert result.entries[0].text == "Cached text"

    cache_file.unlink(missing_ok=True)
    audio.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Failure cases
# ─────────────────────────────────────────────────────────────────────────────

def test_timeout_failure():
    audio = make_audio_file(suffix=".wav")
    with patch("app.services.stt.SarvamSTT._call_sarvam") as mock_call:
        mock_call.return_value = STTResult(
            success=False,
            provider="sarvam",
            failure_reason="timeout",
            failure_detail="Job did not complete within 600s",
        )
        stt = SarvamSTT(api_key="real_key")
        result = stt.transcribe(audio)
    assert not result.success
    assert result.failure_reason == "timeout"
    audio.unlink(missing_ok=True)


def test_corrupt_audio_produces_empty_transcript_failure():
    """If Sarvam returns empty transcript, we fail with empty_transcript reason."""
    audio = make_audio_file(suffix=".mp3")
    with patch("app.services.stt.SarvamSTT._call_sarvam") as mock_call:
        mock_call.return_value = STTResult(
            success=False,
            provider="sarvam",
            failure_reason="empty_transcript",
            failure_detail="Sarvam returned an empty transcript.",
        )
        stt = SarvamSTT(api_key="real_key")
        result = stt.transcribe(audio)
    assert not result.success
    assert result.failure_reason == "empty_transcript"
    audio.unlink(missing_ok=True)


def test_rate_limit_failure():
    audio = make_audio_file(suffix=".mp3")
    with patch("app.services.stt.SarvamSTT._call_sarvam") as mock_call:
        mock_call.return_value = STTResult(
            success=False,
            provider="sarvam",
            failure_reason="rate_limit",
            failure_detail="429 Too Many Requests",
        )
        stt = SarvamSTT(api_key="real_key")
        result = stt.transcribe(audio)
    assert not result.success
    assert result.failure_reason == "rate_limit"
    audio.unlink(missing_ok=True)


def test_extract_entries_sarvam_diarization_format():
    """Verify that both 'transcript' and 'text' keys and numeric speaker_id work."""
    stt = SarvamSTT(api_key="fake")
    data = {
        "transcript": "Full call text",
        "diarized_transcript": {
            "entries": [
                {
                    "speaker_id": "1",
                    "transcript": "Haanji namaste Physics Wallah se bol raha hoon.",
                    "start_time_seconds": 1.2,
                    "end_time_seconds": 3.4,
                },
                {
                    "speaker_id": 2,
                    "text": "Haan sir bataye Arjuna batch ke baare me.",
                    "start_time_seconds": 3.5,
                    "end_time_seconds": 5.8,
                }
            ]
        }
    }
    entries = stt._extract_entries(data)
    assert len(entries) == 2
    assert entries[0].speaker_label == "speaker_1"
    assert entries[0].text == "Haanji namaste Physics Wallah se bol raha hoon."
    assert entries[1].speaker_label == "speaker_2"
    assert entries[1].text == "Haan sir bataye Arjuna batch ke baare me."
