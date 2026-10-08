"""
backend/app/services/stt.py

Speech-to-text abstraction layer.

Supported providers:

    groq
        Groq Whisper Large V3 Turbo.
        Synchronous HTTP API.
        Multilingual transcription.
        Segment timestamps.
        No speaker diarization.

    sarvam
        Existing Sarvam batch STT implementation.
        Supports speaker diarization and asynchronous jobs.

Provider selection:

    STT_PROVIDER=groq
    STT_PROVIDER=sarvam

The rest of the application consumes the provider-independent STTResult
structure and therefore does not need to know which STT provider is active.
"""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from app.config.settings import get_settings


logger = logging.getLogger(__name__)


# =============================================================================
# SHARED DATA STRUCTURES
# =============================================================================


@dataclass
class DiarizedEntry:
    """
    A single timestamped utterance.

    For Sarvam, speaker_label represents the actual diarized speaker.

    For Groq Whisper, speaker_label is currently speaker_0 because the
    transcription endpoint provides timestamps but not speaker diarization.
    """

    speaker_label: str
    text: str
    start_time_seconds: float
    end_time_seconds: float


@dataclass
class STTResult:
    """
    Provider-independent STT result.

    On failure:
        success=False
        failure_reason contains a machine-readable reason
        failure_detail contains human-readable diagnostic information
    """

    success: bool

    entries: list[DiarizedEntry] = field(
        default_factory=list
    )

    raw_transcript: str = ""

    provider: str = ""

    model: str = ""

    audio_duration_seconds: Optional[float] = None

    latency_ms: Optional[int] = None

    estimated_cost_inr: Optional[float] = None

    from_cache: bool = False

    detected_language: Optional[str] = None

    mode: str = "auto"

    failure_reason: Optional[str] = None

    failure_detail: Optional[str] = None


# =============================================================================
# ABSTRACT STT INTERFACE
# =============================================================================


class SpeechToText(ABC):
    """
    Provider-independent STT interface.
    """

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        mode: str = "auto",
    ) -> STTResult:
        """
        Transcribe audio_path.

        Implementations must not allow provider exceptions to escape.
        They should return STTResult(success=False, ...) on failure.
        """
        ...

    def start_batch_job(
        self,
        audio_path: Path,
        mode: str = "auto",
        callback_url: Optional[str] = None,
        callback_token: Optional[str] = None,
    ) -> tuple[str, Optional[dict]]:
        """
        Optional asynchronous provider interface.

        Groq does not use this path.

        Sarvam implements it.
        """

        return (
            "",
            {
                "reason": "not_implemented",
                "detail": (
                    "This STT provider does not support "
                    "asynchronous batch jobs."
                ),
            },
        )

    def fetch_job_result(
        self,
        job_id: str,
        mode: str = "auto",
    ) -> STTResult:
        """
        Optional asynchronous result retrieval.
        """

        return STTResult(
            success=False,
            failure_reason="not_implemented",
            failure_detail=(
                "This STT provider does not support "
                "asynchronous job retrieval."
            ),
        )


# =============================================================================
# CACHE HELPERS
# =============================================================================


def _file_sha256(path: Path) -> str:
    """
    Compute SHA-256 efficiently.
    """

    digest = hashlib.sha256()

    with open(path, "rb") as file:
        for chunk in iter(
            lambda: file.read(65536),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _cache_path(
    cache_dir: Path,
    file_hash: str,
    provider: str,
    mode: str = "auto",
) -> Path:

    if mode and mode != "auto":
        return (
            cache_dir
            / f"stt_{provider}_{mode}_{file_hash}.json"
        )

    return (
        cache_dir
        / f"stt_{provider}_{file_hash}.json"
    )


def _load_cache(
    cache_file: Path,
) -> Optional[STTResult]:

    if not cache_file.exists():
        return None

    try:
        data = json.loads(
            cache_file.read_text(
                encoding="utf-8"
            )
        )

        entries = [
            DiarizedEntry(**entry)
            for entry in data.get(
                "entries",
                [],
            )
        ]

        return STTResult(
            success=True,
            entries=entries,
            raw_transcript=data.get(
                "raw_transcript",
                "",
            ),
            provider=data.get(
                "provider",
                "",
            ),
            model=data.get(
                "model",
                "",
            ),
            audio_duration_seconds=data.get(
                "audio_duration_seconds"
            ),
            estimated_cost_inr=data.get(
                "estimated_cost_inr"
            ),
            detected_language=data.get(
                "detected_language"
            ),
            mode=data.get(
                "mode",
                "auto",
            ),
            from_cache=True,
        )

    except Exception as exc:
        logger.warning(
            "Corrupted STT cache %s: %s",
            cache_file,
            exc,
        )

        return None


def _save_cache(
    cache_file: Path,
    result: STTResult,
) -> None:

    cache_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    data = {
        "entries": [
            {
                "speaker_label": entry.speaker_label,
                "text": entry.text,
                "start_time_seconds": (
                    entry.start_time_seconds
                ),
                "end_time_seconds": (
                    entry.end_time_seconds
                ),
            }
            for entry in result.entries
        ],
        "raw_transcript": result.raw_transcript,
        "provider": result.provider,
        "model": result.model,
        "audio_duration_seconds": (
            result.audio_duration_seconds
        ),
        "estimated_cost_inr": (
            result.estimated_cost_inr
        ),
        "detected_language": (
            result.detected_language
        ),
        "mode": result.mode,
    }

    cache_file.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# =============================================================================
# GROQ WHISPER STT
# =============================================================================


class GroqSTT(SpeechToText):
    """
    Groq Whisper Large V3 Turbo implementation.

    API:
        POST https://api.groq.com/openai/v1/audio/transcriptions

    Request:
        multipart/form-data

    Response:
        verbose_json

    Timestamp granularity:
        segment

    Important:
        Groq Whisper does not provide speaker diarization through this
        endpoint. All returned segments therefore use speaker_0.

    This is intentionally explicit rather than pretending Whisper identified
    speakers.
    """

    PROVIDER = "groq"

    MODEL = "whisper-large-v3-turbo"

    API_URL = (
        "https://api.groq.com/openai/v1/"
        "audio/transcriptions"
    )

    # Groq documents a 25 MB direct upload limit for the free tier.
    # The application itself is already limited to ~4 MB on Vercel.
    MAX_FILE_BYTES = 25 * 1024 * 1024

    REQUEST_TIMEOUT_SECONDS = 300.0

    COST_USD_PER_HOUR = 0.04

    SUPPORTED_FORMATS = {
        ".mp3",
        ".wav",
        ".m4a",
        ".ogg",
        ".flac",
        ".aac",
        ".webm",
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
    ):
        settings = get_settings()

        self._api_key = (
            api_key
            if api_key is not None
            else settings.groq_api_key
        )

        self._cache_dir = (
            settings.cache_path / "stt"
        )

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def _validate_file(
        self,
        audio_path: Path,
    ) -> Optional[dict]:

        if not audio_path.exists():
            return {
                "reason": "file_not_found",
                "detail": (
                    f"File not found: {audio_path}"
                ),
            }

        suffix = audio_path.suffix.lower()

        if suffix not in self.SUPPORTED_FORMATS:
            return {
                "reason": "unsupported_format",
                "detail": (
                    f"Format '{suffix}' is not supported "
                    "by Groq Whisper. Supported formats: "
                    f"{sorted(self.SUPPORTED_FORMATS)}"
                ),
            }

        size = audio_path.stat().st_size

        if size == 0:
            return {
                "reason": "empty_audio",
                "detail": "Audio file is empty.",
            }

        settings = get_settings()

        # Respect application-level upload limit first.
        if size > settings.max_upload_bytes:
            return {
                "reason": "file_too_large",
                "detail": (
                    f"File is {size / 1e6:.2f} MB, "
                    f"which exceeds the application's "
                    f"{settings.effective_max_upload_mb} MB "
                    "upload limit."
                ),
            }

        # Additional Groq free-tier protection.
        if size > self.MAX_FILE_BYTES:
            return {
                "reason": "file_too_large",
                "detail": (
                    f"File is {size / 1e6:.2f} MB, "
                    "which exceeds the Groq direct-upload "
                    "limit configured for this provider."
                ),
            }

        return None

    # -------------------------------------------------------------------------
    # Language
    # -------------------------------------------------------------------------

    @staticmethod
    def _language_for_mode(
        mode: str,
    ) -> Optional[str]:

        normalized = (
            mode or "auto"
        ).lower().strip()

        if normalized == "hi":
            return "hi"

        if normalized == "en":
            return "en"

        # For auto and hinglish, do not force a language.
        return None

    # -------------------------------------------------------------------------
    # Prompt
    # -------------------------------------------------------------------------

    @staticmethod
    def _prompt_for_mode(
        mode: str,
    ) -> str:

        normalized = (
            mode or "auto"
        ).lower().strip()

        if normalized in {
            "hi",
            "hinglish",
            "auto",
        }:
            return (
                "This is an Indian counselling or sales call. "
                "Preserve Hindi, English and Hinglish wording "
                "as spoken. Preserve names and common education "
                "terms such as Physics Wallah, PW, JEE, NEET, "
                "CUET, batch, course, fees, EMI and scholarship. "
                "Do not translate Hindi or Hinglish into English."
            )

        return (
            "This is an education counselling and sales call. "
            "Preserve names, course names, exam names and pricing "
            "terms accurately."
        )

    # -------------------------------------------------------------------------
    # Main transcription
    # -------------------------------------------------------------------------

    def transcribe(
        self,
        audio_path: Path,
        mode: str = "auto",
    ) -> STTResult:

        validation_error = self._validate_file(
            audio_path
        )

        if validation_error:
            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason=validation_error[
                    "reason"
                ],
                failure_detail=validation_error[
                    "detail"
                ],
                mode=mode,
            )

        if not self._api_key:
            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason="missing_api_key",
                failure_detail=(
                    "GROQ_API_KEY is not configured."
                ),
                mode=mode,
            )

        # ---------------------------------------------------------------------
        # Cache
        # ---------------------------------------------------------------------

        file_hash = _file_sha256(
            audio_path
        )

        cache_file = _cache_path(
            self._cache_dir,
            file_hash,
            self.PROVIDER,
            mode=mode,
        )

        cached = _load_cache(
            cache_file
        )

        if cached:
            logger.info(
                "Groq STT cache hit: %s",
                audio_path.name,
            )

            return cached

        # ---------------------------------------------------------------------
        # API call
        # ---------------------------------------------------------------------

        started = time.monotonic()

        try:
            suffix = audio_path.suffix.lower()

            mime_types = {
                ".mp3": "audio/mpeg",
                ".wav": "audio/wav",
                ".m4a": "audio/mp4",
                ".ogg": "audio/ogg",
                ".flac": "audio/flac",
                ".aac": "audio/aac",
                ".webm": "audio/webm",
            }

            mime_type = mime_types.get(
                suffix,
                "application/octet-stream",
            )

            # Read the complete file into bytes. This avoids multipart
            # serialization problems with file handles in serverless runtimes.
            audio_bytes = audio_path.read_bytes()

            files = {
                "file": (
                    audio_path.name,
                    audio_bytes,
                    mime_type,
                )
            }

            form_data = {
                "model": self.MODEL,
                "response_format": "verbose_json",
                "timestamp_granularities[]": "segment",
                "temperature": "0",
                "prompt": self._prompt_for_mode(mode),
            }

            language = self._language_for_mode(mode)

            if language:
                form_data["language"] = language

            headers = {
                "Authorization": f"Bearer {self._api_key}",
            }

            with httpx.Client(
                timeout=self.REQUEST_TIMEOUT_SECONDS
            ) as client:
                response = client.post(
                    self.API_URL,
                    headers=headers,
                    files=files,
                    data=form_data,
                )

                # Convert non-2xx Groq responses into an explicit HTTP error
                # so the failure shown in the application contains the actual
                # provider response instead of being misreported as an empty
                # transcript.
                response.raise_for_status()

            # -----------------------------------------------------------------
            # Parse response
            # -----------------------------------------------------------------

            try:
                payload = response.json()
            except Exception as exc:
                return STTResult(
                    success=False,
                    provider=self.PROVIDER,
                    model=self.MODEL,
                    failure_reason="invalid_response",
                    failure_detail=(
                        "Groq returned a non-JSON response: "
                        f"{exc}"
                    ),
                    mode=mode,
                )

            raw_transcript = str(
                payload.get("text", "") or ""
            ).strip()

            raw_segments = payload.get("segments", [])

            entries: list[DiarizedEntry] = []

            if isinstance(raw_segments, list):
                for segment in raw_segments:
                    if not isinstance(segment, dict):
                        continue

                    text = str(
                        segment.get("text", "") or ""
                    ).strip()

                    if not text:
                        continue

                    try:
                        start = float(
                            segment.get("start", 0.0) or 0.0
                        )
                        end = float(
                            segment.get("end", start) or start
                        )
                    except (TypeError, ValueError):
                        start = 0.0
                        end = 0.0

                    entries.append(
                        DiarizedEntry(
                            # Groq Whisper does not provide speaker diarization.
                            speaker_label="speaker_0",
                            text=text,
                            start_time_seconds=max(0.0, start),
                            end_time_seconds=max(start, end),
                        )
                    )

            # -----------------------------------------------------------------
            # Fallback when verbose_json contains text but no segments.
            # -----------------------------------------------------------------

            if not entries and raw_transcript:
                duration = float(
                    payload.get("duration", 0.0) or 0.0
                )

                entries = [
                    DiarizedEntry(
                        speaker_label="speaker_0",
                        text=raw_transcript,
                        start_time_seconds=0.0,
                        end_time_seconds=duration,
                    )
                ]

            if not entries and not raw_transcript:
                return STTResult(
                    success=False,
                    provider=self.PROVIDER,
                    model=self.MODEL,
                    failure_reason="empty_transcript",
                    failure_detail=(
                        "Groq returned no transcript text or segments."
                    ),
                    latency_ms=int(
                        (time.monotonic() - started) * 1000
                    ),
                    mode=mode,
                )

            # -----------------------------------------------------------------
            # Duration
            # -----------------------------------------------------------------

            response_duration = payload.get("duration")

            try:
                duration = (
                    float(response_duration)
                    if response_duration is not None
                    else None
                )
            except (TypeError, ValueError):
                duration = None

            if not duration and entries:
                duration = max(
                    entry.end_time_seconds
                    for entry in entries
                )

            # -----------------------------------------------------------------
            # Detected language
            # -----------------------------------------------------------------

            detected_language = payload.get("language") or None

            if detected_language == "hi":
                detected_language = "hi-IN"
            elif detected_language == "en":
                detected_language = "en-IN"
            elif not detected_language:
                requested_language = self._language_for_mode(mode)

                if requested_language == "hi":
                    detected_language = "hi-IN"
                elif requested_language == "en":
                    detected_language = "en-IN"
                else:
                    detected_language = "auto"

            # -----------------------------------------------------------------
            # Result
            # -----------------------------------------------------------------

            result = STTResult(
                success=True,
                entries=entries,
                raw_transcript=raw_transcript,
                provider=self.PROVIDER,
                model=self.MODEL,
                audio_duration_seconds=duration,
                latency_ms=int(
                    (time.monotonic() - started) * 1000
                ),
                # Do not invent INR conversion here.
                # Groq pricing is documented in USD/hour.
                estimated_cost_inr=None,
                detected_language=detected_language,
                mode=mode,
            )

            _save_cache(
                cache_file,
                result,
            )

            logger.info(
                (
                    "Groq STT completed: file=%s "
                    "duration=%.2fs segments=%d "
                    "latency=%dms"
                ),
                audio_path.name,
                duration or 0.0,
                len(entries),
                result.latency_ms or 0,
            )

            return result

        except httpx.HTTPStatusError as exc:
            response_text = exc.response.text[:2000]
            logger.error(
                "Groq HTTP error %s: %s",
                exc.response.status_code,
                response_text,
            )

            reason = "provider_error"
            if exc.response.status_code in {401, 403}:
                reason = "auth_error"
            elif exc.response.status_code == 429:
                reason = "rate_limit"

            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason=reason,
                failure_detail=(
                    f"Groq HTTP {exc.response.status_code}: "
                    f"{response_text}"
                ),
                latency_ms=int(
                    (time.monotonic() - started) * 1000
                ),
                mode=mode,
            )

        except httpx.TimeoutException as exc:
            logger.exception("Groq request timed out")

            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason="timeout",
                failure_detail=f"Groq request timed out: {exc}",
                latency_ms=int(
                    (time.monotonic() - started) * 1000
                ),
                mode=mode,
            )

        except httpx.RequestError as exc:
            logger.exception("Groq network error")

            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason="network_error",
                failure_detail=str(exc),
                latency_ms=int(
                    (time.monotonic() - started) * 1000
                ),
                mode=mode,
            )

        except Exception as exc:
            logger.exception("Unexpected Groq STT error")

            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason="unexpected_error",
                failure_detail=str(exc),
                latency_ms=int(
                    (time.monotonic() - started) * 1000
                ),
                mode=mode,
            )


# =============================================================================
# SARVAM STT
# =============================================================================


_SARVAM_BATCH_DIARIZE_INR_PER_HOUR = 45.0

_SARVAM_SUPPORTED_FORMATS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".ogg",
    ".flac",
    ".aac",
    ".webm",
}


class SarvamSTT(SpeechToText):
    """
    Existing Sarvam batch STT implementation.

    This remains available as a fallback and for existing Sarvam jobs.
    """

    PROVIDER = "sarvam"

    MODEL = "saaras:v4"

    JOB_TIMEOUT_SECONDS = 1800

    def __init__(
        self,
        api_key: Optional[str] = None,
    ):
        settings = get_settings()

        self._api_key = (
            api_key
            if api_key is not None
            else settings.sarvam_api_key
        )

        self._cache_dir = (
            settings.cache_path / "stt"
        )

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def _validate_file(
        self,
        audio_path: Path,
    ) -> Optional[dict]:

        if not audio_path.exists():
            return {
                "reason": "file_not_found",
                "detail": (
                    f"File not found: {audio_path}"
                ),
            }

        suffix = audio_path.suffix.lower()

        if suffix not in _SARVAM_SUPPORTED_FORMATS:
            return {
                "reason": "unsupported_format",
                "detail": (
                    f"Format '{suffix}' is not supported "
                    "by Sarvam."
                ),
            }

        settings = get_settings()

        size = audio_path.stat().st_size

        if size == 0:
            return {
                "reason": "empty_audio",
                "detail": (
                    "Audio file is empty."
                ),
            }

        if size > settings.max_upload_bytes:
            return {
                "reason": "file_too_large",
                "detail": (
                    f"File is {size / 1e6:.1f} MB, "
                    f"which exceeds the "
                    f"{settings.effective_max_upload_mb} MB "
                    "application limit."
                ),
            }

        return None

    # -------------------------------------------------------------------------
    # Synchronous Sarvam path
    # -------------------------------------------------------------------------

    def transcribe(
        self,
        audio_path: Path,
        mode: str = "auto",
    ) -> STTResult:

        validation_error = self._validate_file(
            audio_path
        )

        if validation_error:
            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason=validation_error[
                    "reason"
                ],
                failure_detail=validation_error[
                    "detail"
                ],
                mode=mode,
            )

        file_hash = _file_sha256(
            audio_path
        )

        cache_file = _cache_path(
            self._cache_dir,
            file_hash,
            self.PROVIDER,
            mode=mode,
        )

        cached = _load_cache(
            cache_file
        )

        if cached:
            return cached

        if not self._api_key:
            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason="missing_api_key",
                failure_detail=(
                    "SARVAM_API_KEY is not configured."
                ),
                mode=mode,
            )

        started = time.monotonic()

        try:
            result = self._call_sarvam(
                audio_path,
                mode=mode,
            )

        except Exception as exc:
            logger.exception(
                "Unexpected Sarvam STT error"
            )

            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason="unexpected_error",
                failure_detail=str(exc),
                mode=mode,
            )

        result.latency_ms = int(
            (
                time.monotonic()
                - started
            )
            * 1000
        )

        result.mode = mode

        if result.success:
            _save_cache(
                cache_file,
                result,
            )

        return result

    def _call_sarvam(
        self,
        audio_path: Path,
        mode: str = "auto",
    ) -> STTResult:

        try:
            from sarvamai import SarvamAI
        except ImportError:
            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason="sdk_not_installed",
                failure_detail=(
                    "sarvamai package is not installed."
                ),
                mode=mode,
            )

        lang_code = None

        if mode == "hi":
            lang_code = "hi-IN"

        elif mode == "en":
            lang_code = "en-IN"

        try:
            client = SarvamAI(
                api_subscription_key=self._api_key
            )

            job = (
                client
                .speech_to_text_job
                .create_job(
                    model=self.MODEL,
                    mode="transcribe",
                    with_diarization=True,
                    language_code=lang_code,
                )
            )

            job.upload_files(
                file_paths=[
                    str(audio_path)
                ]
            )

            job.start()

            try:
                job.wait_until_complete(
                    timeout=self.JOB_TIMEOUT_SECONDS
                )

            except TypeError:
                job.wait_until_complete()

            return self._parse_job_result(
                job,
                mode=mode,
                requested_lang=lang_code,
            )

        except Exception as exc:

            error_str = str(
                exc
            ).lower()

            if (
                "rate" in error_str
                or "429" in error_str
            ):
                reason = "rate_limit"

            elif "timeout" in error_str:
                reason = "timeout"

            elif (
                "auth" in error_str
                or "401" in error_str
                or "403" in error_str
            ):
                reason = "auth_error"

            else:
                reason = "provider_error"

            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason=reason,
                failure_detail=str(exc),
                mode=mode,
            )

    # -------------------------------------------------------------------------
    # Sarvam async job
    # -------------------------------------------------------------------------

    def start_batch_job(
        self,
        audio_path: Path,
        mode: str = "auto",
        callback_url: Optional[str] = None,
        callback_token: Optional[str] = None,
    ) -> tuple[str, Optional[dict]]:

        validation_error = self._validate_file(
            audio_path
        )

        if validation_error:
            return (
                "",
                validation_error,
            )

        if not self._api_key:
            return (
                "",
                {
                    "reason": "missing_api_key",
                    "detail": (
                        "SARVAM_API_KEY is not configured."
                    ),
                },
            )

        try:
            from sarvamai import SarvamAI

        except ImportError:
            return (
                "",
                {
                    "reason": "sdk_not_installed",
                    "detail": (
                        "sarvamai package is not installed."
                    ),
                },
            )

        lang_code = None

        if mode == "hi":
            lang_code = "hi-IN"

        elif mode == "en":
            lang_code = "en-IN"

        try:
            client = SarvamAI(
                api_subscription_key=self._api_key
            )

            callback_params = None

            if callback_url:

                callback_params = {
                    "url": callback_url
                }

                if callback_token:
                    callback_params[
                        "auth_token"
                    ] = callback_token

            job = (
                client
                .speech_to_text_job
                .create_job(
                    model=self.MODEL,
                    mode="transcribe",
                    with_diarization=True,
                    language_code=lang_code,
                    callback=(
                        callback_params
                        if callback_params
                        else None
                    ),
                )
            )

            job.upload_files(
                file_paths=[
                    str(audio_path)
                ]
            )

            job.start()

            return (
                job.job_id,
                None,
            )

        except Exception as exc:

            error_str = str(
                exc
            ).lower()

            if (
                "rate" in error_str
                or "429" in error_str
            ):
                reason = "rate_limit"

            elif "timeout" in error_str:
                reason = "timeout"

            elif (
                "auth" in error_str
                or "401" in error_str
                or "403" in error_str
            ):
                reason = "auth_error"

            else:
                reason = "provider_error"

            return (
                "",
                {
                    "reason": reason,
                    "detail": str(exc),
                },
            )

    # -------------------------------------------------------------------------
    # Sarvam result retrieval
    # -------------------------------------------------------------------------

    def fetch_job_result(
        self,
        job_id: str,
        mode: str = "auto",
    ) -> STTResult:

        if not self._api_key:
            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason="missing_api_key",
                failure_detail=(
                    "SARVAM_API_KEY is not configured."
                ),
                mode=mode,
            )

        try:
            from sarvamai import SarvamAI

            client = SarvamAI(
                api_subscription_key=self._api_key
            )

            job = (
                client
                .speech_to_text_job
                .get_job(
                    job_id=job_id
                )
            )

            lang_code = (
                "hi-IN"
                if mode == "hi"
                else (
                    "en-IN"
                    if mode == "en"
                    else None
                )
            )

            return self._parse_job_result(
                job,
                mode=mode,
                requested_lang=lang_code,
            )

        except Exception as exc:

            logger.exception(
                "Failed to fetch Sarvam job %s",
                job_id,
            )

            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason="fetch_job_error",
                failure_detail=str(exc),
                mode=mode,
            )

    # -------------------------------------------------------------------------
    # Parse Sarvam output
    # -------------------------------------------------------------------------

    def _parse_job_result(
        self,
        job,
        mode: str = "auto",
        requested_lang: Optional[str] = None,
    ) -> STTResult:

        with tempfile.TemporaryDirectory() as tmpdir:

            try:
                job.download_outputs(
                    output_dir=tmpdir
                )

            except Exception as exc:

                return STTResult(
                    success=False,
                    provider=self.PROVIDER,
                    model=self.MODEL,
                    failure_reason="download_error",
                    failure_detail=str(exc),
                    mode=mode,
                )

            json_files = list(
                Path(tmpdir).glob(
                    "*.json"
                )
            )

            if not json_files:
                return STTResult(
                    success=False,
                    provider=self.PROVIDER,
                    model=self.MODEL,
                    failure_reason="no_output",
                    failure_detail=(
                        "Sarvam completed but "
                        "produced no JSON output."
                    ),
                    mode=mode,
                )

            output_data = json.loads(
                json_files[0].read_text(
                    encoding="utf-8"
                )
            )

        entries = self._extract_entries(
            output_data
        )

        raw_transcript = str(
            output_data.get(
                "transcript",
                "",
            )
            or ""
        )

        if (
            not entries
            and not raw_transcript
        ):
            return STTResult(
                success=False,
                provider=self.PROVIDER,
                model=self.MODEL,
                failure_reason="empty_transcript",
                failure_detail=(
                    "Sarvam returned an empty transcript."
                ),
                mode=mode,
            )

        duration = None

        if entries:
            duration = max(
                entry.end_time_seconds
                for entry in entries
            )

        cost_inr = None

        if duration:
            cost_inr = (
                duration / 3600
            ) * _SARVAM_BATCH_DIARIZE_INR_PER_HOUR

        detected_language = (
            output_data.get(
                "language_code"
            )
            or output_data.get(
                "detected_language"
            )
            or requested_lang
            or (
                "hi-IN"
                if mode == "hi"
                else (
                    "en-IN"
                    if mode == "en"
                    else "auto"
                )
            )
        )

        return STTResult(
            success=True,
            entries=entries,
            raw_transcript=raw_transcript,
            provider=self.PROVIDER,
            model=self.MODEL,
            audio_duration_seconds=duration,
            estimated_cost_inr=cost_inr,
            detected_language=detected_language,
            mode=mode,
        )

    def _extract_entries(
        self,
        data: dict,
    ) -> list[DiarizedEntry]:

        diarized = data.get(
            "diarized_transcript"
        )

        if not diarized:

            transcript = str(
                data.get(
                    "transcript",
                    "",
                )
                or ""
            ).strip()

            if not transcript:
                return []

            timestamps = data.get(
                "timestamps",
                {},
            )

            end_times = (
                timestamps.get(
                    "end_time_seconds",
                    [0],
                )
                if isinstance(
                    timestamps,
                    dict,
                )
                else [0]
            )

            end_time = (
                end_times[-1]
                if end_times
                else 0
            )

            return [
                DiarizedEntry(
                    speaker_label="speaker_0",
                    text=transcript,
                    start_time_seconds=0.0,
                    end_time_seconds=float(
                        end_time or 0
                    ),
                )
            ]

        if isinstance(
            diarized,
            list,
        ):
            raw_entries = diarized

        else:
            raw_entries = diarized.get(
                "entries",
                [],
            )

        entries = []

        for item in raw_entries:

            if not isinstance(
                item,
                dict,
            ):
                continue

            try:
                speaker_id = item.get(
                    "speaker_id"
                )

                if speaker_id is not None:

                    speaker_string = str(
                        speaker_id
                    )

                    if speaker_string.startswith(
                        "speaker"
                    ):
                        speaker_label = (
                            speaker_string
                        )
                    else:
                        speaker_label = (
                            f"speaker_{speaker_string}"
                        )

                else:
                    speaker_label = "speaker_0"

                text = str(
                    item.get(
                        "transcript"
                    )
                    or item.get(
                        "text"
                    )
                    or ""
                ).strip()

                if not text:
                    continue

                entries.append(
                    DiarizedEntry(
                        speaker_label=speaker_label,
                        text=text,
                        start_time_seconds=float(
                            item.get(
                                "start_time_seconds",
                                0,
                            )
                            or 0
                        ),
                        end_time_seconds=float(
                            item.get(
                                "end_time_seconds",
                                0,
                            )
                            or 0
                        ),
                    )
                )

            except (
                KeyError,
                TypeError,
                ValueError,
            ) as exc:

                logger.warning(
                    "Skipping malformed Sarvam entry: %s",
                    exc,
                )

        if entries:
            return entries

        transcript = str(
            data.get(
                "transcript",
                "",
            )
            or ""
        ).strip()

        if transcript:
            return [
                DiarizedEntry(
                    speaker_label="speaker_0",
                    text=transcript,
                    start_time_seconds=0.0,
                    end_time_seconds=0.0,
                )
            ]

        return []


# =============================================================================
# PROVIDER FACTORY
# =============================================================================


def get_stt_provider() -> SpeechToText:
    """
    Return the provider configured by STT_PROVIDER.

    Production:
        STT_PROVIDER=groq

    Fallback:
        STT_PROVIDER=sarvam

    Unknown values fail explicitly instead of silently selecting a provider.
    """

    settings = get_settings()

    provider = (
        settings.stt_provider
        or "sarvam"
    ).strip().lower()

    if provider == "groq":
        return GroqSTT()

    if provider == "sarvam":
        return SarvamSTT()

    raise ValueError(
        "Unsupported STT_PROVIDER "
        f"'{settings.stt_provider}'. "
        "Expected 'groq' or 'sarvam'."
    )
