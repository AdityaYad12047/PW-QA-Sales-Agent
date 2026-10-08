"""
backend/app/pipeline/segments.py

Normalize STT output into readable conversation turns.

The Groq Whisper endpoint supplies timestamped transcription segments but does
not identify speakers.  This module therefore deliberately separates two
concerns:

1. Preserve Whisper's timing information and prevent many adjacent Whisper
   fragments from being collapsed into one 10-minute paragraph.
2. Produce stable transcript segments that the role-inference layer can label.

No speaker identity is invented here.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from app.services.stt import DiarizedEntry


# Original pipeline merged same-speaker fragments whenever the gap was below
# 1500 ms.  With Groq every entry is speaker_0, so that rule could merge an
# entire call into one giant segment.  Keep short pauses merged, but impose
# hard limits so a conversation remains readable.
DEFAULT_MERGE_THRESHOLD_MS = 900
MAX_MERGED_DURATION_MS = 18_000
MAX_MERGED_CHARS = 420

# Sentence-ending punctuation.  English/Hinglish calls frequently have weak
# punctuation from ASR, so this is only one of several split signals.
_SENTENCE_END_RE = re.compile(r"[.!?।]+(?:['\")\]]+)?$")


@dataclass
class NormalisedSegment:
    """A single transcript turn ready for DB insertion and LLM prompts."""

    segment_id: str
    start_ms: int
    end_ms: int
    speaker_label: str
    role: str = "unknown"
    role_confidence: Optional[float] = None
    role_source: str = "heuristic"
    text: str = ""


def _split_long_entry(entry: dict) -> list[dict]:
    """Split an unusually long ASR entry on sentence boundaries/length.

    This is a presentation/evaluation safeguard, not speaker diarization.
    Timestamps are distributed proportionally across the text chunks because
    the provider-independent STTResult currently stores entry-level timing.
    """
    text = entry["text"].strip()
    duration = max(0, entry["end_ms"] - entry["start_ms"])

    if len(text) <= MAX_MERGED_CHARS and duration <= MAX_MERGED_DURATION_MS:
        return [entry]

    # Prefer punctuation boundaries.  If ASR supplied little punctuation,
    # fall back to word-count chunks so the UI never receives a giant block.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", text) if s.strip()]

    if len(sentences) <= 1:
        words = text.split()
        sentences = [
            " ".join(words[i : i + 55])
            for i in range(0, len(words), 55)
        ]

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > MAX_MERGED_CHARS:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)

    if not chunks:
        return [entry]

    total_chars = max(1, sum(len(c) for c in chunks))
    result: list[dict] = []
    cursor = entry["start_ms"]

    for index, chunk in enumerate(chunks):
        if index == len(chunks) - 1:
            end = entry["end_ms"]
        else:
            share = len(chunk) / total_chars
            end = cursor + max(1, int(duration * share))
        result.append(
            {
                "speaker_label": entry["speaker_label"],
                "text": chunk,
                "start_ms": cursor,
                "end_ms": max(cursor, end),
            }
        )
        cursor = max(cursor, end)

    return result


def normalise_segments(
    entries: list[DiarizedEntry],
    merge_threshold_ms: int = DEFAULT_MERGE_THRESHOLD_MS,
) -> list[NormalisedSegment]:
    """Convert raw STT entries into readable, stable transcript segments.

    Important difference from the old implementation: Groq returns every
    entry with ``speaker_0``.  Therefore we do NOT merge an entire call just
    because the provider label is identical.  We preserve meaningful pauses,
    sentence boundaries and hard size limits so role inference can operate on
    individual conversational turns.
    """
    if not entries:
        return []

    converted: list[dict] = []
    for entry in entries:
        text = (entry.text or "").strip()
        if not text:
            continue

        converted.append(
            {
                "speaker_label": entry.speaker_label or "speaker_0",
                "text": text,
                "start_ms": max(0, int(entry.start_time_seconds * 1000)),
                "end_ms": max(
                    int(entry.start_time_seconds * 1000),
                    int(entry.end_time_seconds * 1000),
                ),
            }
        )

    if not converted:
        return []

    # Split oversized provider entries first.
    expanded: list[dict] = []
    for item in converted:
        expanded.extend(_split_long_entry(item))

    # Merge only genuinely short fragments.  For Groq's synthetic speaker_0,
    # a longer pause or a sentence boundary should remain a new turn.
    merged: list[dict] = []
    for curr in expanded:
        if not merged:
            merged.append(curr)
            continue

        prev = merged[-1]
        gap_ms = curr["start_ms"] - prev["end_ms"]
        same_speaker = curr["speaker_label"] == prev["speaker_label"]
        combined_chars = len(prev["text"]) + 1 + len(curr["text"])
        combined_duration = curr["end_ms"] - prev["start_ms"]

        previous_ends_sentence = bool(_SENTENCE_END_RE.search(prev["text"]))

        can_merge = (
            same_speaker
            and gap_ms >= 0
            and gap_ms < merge_threshold_ms
            and not previous_ends_sentence
            and combined_chars <= MAX_MERGED_CHARS
            and combined_duration <= MAX_MERGED_DURATION_MS
        )

        if can_merge:
            prev["end_ms"] = curr["end_ms"]
            prev["text"] = (
                prev["text"].rstrip()
                + " "
                + curr["text"].lstrip()
            )
        else:
            merged.append(curr)

    segments: list[NormalisedSegment] = []
    for i, seg in enumerate(merged, start=1):
        segments.append(
            NormalisedSegment(
                segment_id=f"seg_{i:03d}",
                start_ms=seg["start_ms"],
                end_ms=seg["end_ms"],
                speaker_label=seg["speaker_label"],
                text=seg["text"],
            )
        )

    return segments


def normalise_text_for_matching(text: str) -> str:
    """Canonical text form used by evidence verification."""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    return text.casefold()


def format_segment_for_prompt(seg: NormalisedSegment) -> str:
    """Format a transcript segment for the evaluator prompt."""
    start_sec = max(0, seg.start_ms // 1000)
    minutes = start_sec // 60
    seconds = start_sec % 60
    timestamp = f"{minutes:02d}:{seconds:02d}"
    return (
        f"[{seg.segment_id} | {seg.role} | {timestamp}] {seg.text}"
    )
