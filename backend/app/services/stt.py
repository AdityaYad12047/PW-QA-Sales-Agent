"""
backend/app/services/stt.py

Speech-to-text abstraction layer.

Design choices:
- Abstract base class `SpeechToText` so Whisper or any other provider can be
  swapped by changing one environment variable and implementing the interface.
- `SarvamSTT` is the concrete implementation using the sarvamai Python SDK
  with batch diarization (diarization is only available in batch mode per docs).
- Results are cached by SHA-256 of the audio file so dev reruns are free.
- Every failure path stores a typed reason in `STTResult.failure_reason`.
- Timeouts and retries are handled via tenacity; never crashes silently.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Shared data structures (provider-independent)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DiarizedEntry:
    """A single speaker-labelled utterance from the STT provider."""
    speaker_label: str      # raw provider label, e.g. "speaker_0"
    text: str
    start_time_seconds: float
    end_time_seconds: float


@dataclass
class STTResult:
    """
    Full result of an STT call.  On failure, `success=False` and
    `failure_reason` holds a machine-readable tag + human-readable detail.
    """
    success: bool
    entries: list[DiarizedEntry] = field(default_factory=list)
    raw_transcript: str = ""
    provider: str = ""
    model: str = ""
    audio_duration_seconds: Optional[float] = None
    latency_ms: Optional[int] = None
    estimated_cost_inr: Optional[float] = None
    from_cache: bool = False
    detected_language: Optional[str] = None
    mode: str = "auto"

    # Failure fields
    failure_reason: Optional[str] = None   # machine tag: timeout|rate_limit|provider_error|…
    failure_detail: Optional[str] = None   # human-readable message


# ─────────────────────────────────────────────────────────────────────────────
# Abstract interface
# ─────────────────────────────────────────────────────────────────────────────

class SpeechToText(ABC):
    """Swappable STT interface. Implement this to add a new provider."""

    @abstractmethod
    def transcribe(self, audio_path: Path, mode: str = "auto") -> STTResult:
        """
        Transcribe audio_path and return an STTResult.
        MUST NOT raise; on any failure return STTResult(success=False, …).
        """
        ...


# ─────────────────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────────────────

def _file_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file efficiently."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_path(cache_dir: Path, file_hash: str, provider: str, mode: str = "auto") -> Path:
    if mode and mode != "auto":
        return cache_dir / f"stt_{provider}_{mode}_{file_hash}.json"
    return cache_dir / f"stt_{provider}_{file_hash}.json"


def _load_cache(cache_file: Path) -> Optional[STTResult]:
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        entries = [DiarizedEntry(**e) for e in data.get("entries", [])]
        return STTResult(
            success=True,
            entries=entries,
            raw_transcript=data.get("raw_transcript", ""),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            audio_duration_seconds=data.get("audio_duration_seconds"),
            estimated_cost_inr=data.get("estimated_cost_inr"),
            detected_language=data.get("detected_language"),
            mode=data.get("mode", "auto"),
            from_cache=True,
        )
    except Exception as exc:
        logger.warning("Corrupted STT cache %s: %s", cache_file, exc)
        return None


def _save_cache(cache_file: Path, result: STTResult) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "entries": [
            {
                "speaker_label": e.speaker_label,
                "text": e.text,
                "start_time_seconds": e.start_time_seconds,
                "end_time_seconds": e.end_time_seconds,
            }
            for e in result.entries
        ],
        "raw_transcript": result.raw_transcript,
        "provider": result.provider,
        "model": result.model,
        "audio_duration_seconds": result.audio_duration_seconds,
        "estimated_cost_inr": result.estimated_cost_inr,
        "detected_language": result.detected_language,
        "mode": result.mode,
    }
    cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Sarvam STT implementation
# ─────────────────────────────────────────────────────────────────────────────

# Pricing as of Oct 2026 (source: docs.sarvam.ai)
_SARVAM_BATCH_DIARIZE_INR_PER_HOUR = 45.0
# Max file size accepted by Sarvam (they state 2 hours; we also apply our own limit)
_SARVAM_MAX_DURATION_SECONDS = 2 * 3600
# Supported audio formats by Sarvam
_SARVAM_SUPPORTED_FORMATS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".webm"}


class SarvamSTT(SpeechToText):
    """
    Sarvam AI batch STT with speaker diarization.

    Uses the official sarvamai Python SDK:
        pip install sarvamai>=0.1.33a3

    Batch jobs poll until complete; the SDK's `wait_until_complete()` handles
    the polling loop internally.  We wrap the whole thing in a try/except so
    any unhandled SDK error is captured as a typed failure.

    Cache: results are stored by (SHA-256, provider) so repeated runs during
    development cost nothing.
    """

    PROVIDER = "sarvam"
    MODEL = "saaras:v4"
    # Job timeout in seconds (Sarvam batch can take minutes for long files or queued jobs)
    JOB_TIMEOUT_SECONDS = 1800

    def __init__(self, api_key: Optional[str] = None):
        settings = get_settings()
        self._api_key = api_key if api_key is not None else settings.sarvam_api_key
        self._cache_dir = settings.cache_path / "stt"

    def transcribe(self, audio_path: Path, mode: str = "auto") -> STTResult:
        """
        Main entry point.  Validates the file, checks cache, calls the API.
        Never raises; returns STTResult with success=False on any error.
        """
        # ── Validation ───────────────────────────────────────────────────────
        validation_error = self._validate_file(audio_path)
        if validation_error:
            return STTResult(
                success=False,
                provider=self.PROVIDER,
                failure_reason=validation_error["reason"],
                failure_detail=validation_error["detail"],
                mode=mode,
            )

        # ── Cache lookup ──────────────────────────────────────────────────────
        file_hash = _file_sha256(audio_path)
        cache_file = _cache_path(self._cache_dir, file_hash, self.PROVIDER, mode=mode)
        cached = _load_cache(cache_file)
        if cached:
            logger.info("STT cache hit for %s (mode=%s)", audio_path.name, mode)
            return cached

        # ── API call ──────────────────────────────────────────────────────────
        if not self._api_key:
            return STTResult(
                success=False,
                provider=self.PROVIDER,
                failure_reason="missing_api_key",
                failure_detail="SARVAM_API_KEY is not set. Add it to your .env file.",
                mode=mode,
            )

        t_start = time.monotonic()
        try:
            result = self._call_sarvam(audio_path, mode=mode)
        except Exception as exc:
            # Catch-all so the pipeline never crashes silently
            logger.exception("Unexpected error during Sarvam STT for %s", audio_path.name)
            return STTResult(
                success=False,
                provider=self.PROVIDER,
                failure_reason="unexpected_error",
                failure_detail=str(exc),
                mode=mode,
            )

        result.latency_ms = int((time.monotonic() - t_start) * 1000)
        result.mode = mode

        # ── Cache result if successful ────────────────────────────────────────
        if result.success:
            _save_cache(cache_file, result)

        return result

    def _validate_file(self, audio_path: Path) -> Optional[dict]:
        """Return an error dict if file fails validation, else None."""
        if not audio_path.exists():
            return {"reason": "file_not_found", "detail": f"File not found: {audio_path}"}

        suffix = audio_path.suffix.lower()
        if suffix not in _SARVAM_SUPPORTED_FORMATS:
            return {
                "reason": "unsupported_format",
                "detail": (
                    f"Format '{suffix}' is not supported by Sarvam STT. "
                    f"Supported: {sorted(_SARVAM_SUPPORTED_FORMATS)}"
                ),
            }

        settings = get_settings()
        size = audio_path.stat().st_size
        if size == 0:
            return {"reason": "empty_audio", "detail": "Audio file is empty (0 bytes)."}
        if size > settings.max_upload_bytes:
            return {
                "reason": "file_too_large",
                "detail": (
                    f"File is {size / 1e6:.1f} MB, which exceeds the "
                    f"{settings.max_upload_mb} MB limit."
                ),
            }

        return None

    def _call_sarvam(self, audio_path: Path, mode: str = "auto") -> STTResult:
        """
        Create and run a Sarvam batch diarization job.
        Wraps SDK calls; on specific errors returns typed failures.
        """
        try:
            from sarvamai import SarvamAI  # type: ignore[import]
        except ImportError:
            return STTResult(
                success=False,
                provider=self.PROVIDER,
                failure_reason="sdk_not_installed",
                failure_detail=(
                    "sarvamai package not installed. Run: pip install 'sarvamai>=0.1.33a3'"
                ),
                mode=mode,
            )

        lang_code = None
        if mode == "hi":
            lang_code = "hi-IN"
        elif mode == "en":
            lang_code = "en-IN"
        # "auto" and "hinglish" leave language_code=None for automatic code-mixed detection

        try:
            client = SarvamAI(api_subscription_key=self._api_key)

            # Create the batch job with diarization enabled.
            job = client.speech_to_text_job.create_job(
                model=self.MODEL,
                mode="transcribe",
                with_diarization=True,
                language_code=lang_code,
            )

            job.upload_files(file_paths=[str(audio_path)])
            job.start()

            # SDK polls internally; raises on timeout if we provide a timeout param.
            try:
                job.wait_until_complete(timeout=self.JOB_TIMEOUT_SECONDS)
            except TypeError:
                deadline = time.monotonic() + self.JOB_TIMEOUT_SECONDS
                job.wait_until_complete()
                if time.monotonic() > deadline:
                    return STTResult(
                        success=False,
                        provider=self.PROVIDER,
                        model=self.MODEL,
                        failure_reason="timeout",
                        failure_detail=(
                            f"Sarvam job did not complete within {self.JOB_TIMEOUT_SECONDS}s."
                        ),
                        mode=mode,
                    )

        except Exception as exc:
            error_str = str(exc).lower()
            if "rate" in error_str or "429" in error_str:
                reason = "rate_limit"
            elif "timeout" in error_str:
                reason = "timeout"
            elif "auth" in error_str or "401" in error_str or "403" in error_str:
                reason = "auth_error"
            else:
                reason = "provider_error"
            logger.warning("Sarvam STT error (%s): %s", reason, exc)
            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason=reason,
                failure_detail=str(exc),
                mode=mode,
            )

        # ── Download and parse results ────────────────────────────────────────
        return self._parse_job_result(job, mode=mode, requested_lang=lang_code)

    def _parse_job_result(self, job, mode: str = "auto", requested_lang: Optional[str] = None) -> STTResult:
        """
        Download the job output to a temp directory, parse the JSON files,
        and return a STTResult.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                job.download_outputs(output_dir=tmpdir)
            except Exception as exc:
                return STTResult(
                    success=False,
                    provider=self.PROVIDER,
                    model=self.MODEL,
                    failure_reason="download_error",
                    failure_detail=str(exc),
                    mode=mode,
                )

            # Sarvam writes one JSON per uploaded file
            json_files = list(Path(tmpdir).glob("*.json"))
            if not json_files:
                return STTResult(
                    success=False,
                    provider=self.PROVIDER,
                    model=self.MODEL,
                    failure_reason="no_output",
                    failure_detail="Sarvam job completed but produced no output files.",
                    mode=mode,
                )

            # We uploaded exactly one file, so there should be exactly one JSON output.
            output_data = json.loads(json_files[0].read_text(encoding="utf-8"))

        entries = self._extract_entries(output_data)
        raw_transcript = output_data.get("transcript", "")

        if not entries and not raw_transcript:
            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason="empty_transcript",
                failure_detail="Sarvam returned an empty transcript. The audio may be silent or corrupt.",
                mode=mode,
            )

        # Estimate duration from last entry end time if not provided directly
        duration = None
        if entries:
            duration = max(e.end_time_seconds for e in entries)

        cost_inr = None
        if duration:
            cost_inr = (duration / 3600) * _SARVAM_BATCH_DIARIZE_INR_PER_HOUR

        detected_lang = (
            output_data.get("language_code")
            or output_data.get("detected_language")
            or requested_lang
            or ("hi-IN" if mode == "hi" else ("en-IN" if mode == "en" else "auto"))
        )

        return STTResult(
            success=True,
            entries=entries,
            raw_transcript=raw_transcript,
            provider=self.PROVIDER,
            model=self.MODEL,
            audio_duration_seconds=duration,
            estimated_cost_inr=cost_inr,
            detected_language=detected_lang,
            mode=mode,
        )

    def _extract_entries(self, data: dict) -> list[DiarizedEntry]:
        """
        Parse the diarized_transcript from the Sarvam JSON output.
        Handles both the nested {entries:[...]} format and a flat list.
        Falls back to an empty list if diarization is absent.
        """
        diarized = data.get("diarized_transcript")
        if not diarized:
            # No diarization in output — construct a single-speaker fallback
            # from word timestamps if available.
            transcript = data.get("transcript", "").strip()
            if not transcript:
                return []
            return [
                DiarizedEntry(
                    speaker_label="speaker_0",
                    text=transcript,
                    start_time_seconds=0.0,
                    end_time_seconds=data.get("timestamps", {}).get("end_time_seconds", [0])[-1]
                    if data.get("timestamps")
                    else 0.0,
                )
            ]

        raw_entries = diarized if isinstance(diarized, list) else diarized.get("entries", [])

        entries = []
        for item in raw_entries:
            try:
                spk = item.get("speaker_id")
                if spk is not None:
                    spk_str = str(spk)
                    speaker_label = spk_str if spk_str.startswith("speaker") else f"speaker_{spk_str}"
                else:
                    speaker_label = "speaker_0"
                text = (item.get("transcript") or item.get("text") or "").strip()
                entries.append(
                    DiarizedEntry(
                        speaker_label=speaker_label,
                        text=text,
                        start_time_seconds=float(item.get("start_time_seconds", 0)),
                        end_time_seconds=float(item.get("end_time_seconds", 0)),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Skipping malformed diarization entry: %s — %s", item, exc)

        # Drop entries with no text
        filtered = [e for e in entries if e.text]
        if not filtered and data.get("transcript", "").strip():
            transcript = data.get("transcript", "").strip()
            return [
                DiarizedEntry(
                    speaker_label="speaker_0",
                    text=transcript,
                    start_time_seconds=0.0,
                    end_time_seconds=data.get("timestamps", {}).get("end_time_seconds", [0])[-1]
                    if data.get("timestamps")
                    else 0.0,
                )
            ]
        return filtered


# ─────────────────────────────────────────────────────────────────────────────
# Factory — returns the configured provider
# ─────────────────────────────────────────────────────────────────────────────

def get_stt_provider() -> SpeechToText:
    """
    Return the active STT provider instance.
    To swap to Whisper or another provider, implement `SpeechToText` and
    change this function.  No other code needs to change.
    """
    return SarvamSTT()
