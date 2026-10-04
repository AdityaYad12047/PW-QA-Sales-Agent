"""
backend/app/pipeline/segments.py

Normalize raw STT provider output into transcript segments with:
  - Stable IDs: seg_001, seg_002, …
  - Millisecond timestamps
  - Merging of adjacent same-speaker fragments under a silence threshold
  - Dropping of empty/whitespace-only segments

Design: pure functions that take the raw DiarizedEntry list and return a
list of normalised dicts.  Pure = no DB, no I/O, easy to unit-test.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from app.services.stt import DiarizedEntry


# If two adjacent same-speaker entries have a gap shorter than this, merge them.
# 1500ms is a reasonable pause-mid-sentence threshold for call centre audio.
DEFAULT_MERGE_THRESHOLD_MS = 1500


@dataclass
class NormalisedSegment:
    """A single transcript segment ready for DB insertion and LLM prompts."""
    segment_id: str           # seg_001, seg_002, …
    start_ms: int
    end_ms: int
    speaker_label: str        # raw provider label, e.g. "speaker_0"
    role: str = "unknown"     # filled in by pipeline/roles.py
    role_confidence: Optional[float] = None
    role_source: str = "heuristic"
    text: str = ""


def normalise_segments(
    entries: list[DiarizedEntry],
    merge_threshold_ms: int = DEFAULT_MERGE_THRESHOLD_MS,
) -> list[NormalisedSegment]:
    """
    Convert raw DiarizedEntry list → list[NormalisedSegment].

    Steps:
    1. Convert float seconds → integer milliseconds.
    2. Drop entries with empty text after stripping.
    3. Merge adjacent same-speaker entries with gap < merge_threshold_ms.
    4. Assign stable sequential IDs (seg_001 …).
    """
    if not entries:
        return []

    # ── Step 1 & 2: convert + filter ─────────────────────────────────────────
    converted: list[dict] = []
    for entry in entries:
        text = entry.text.strip()
        if not text:
            continue
        converted.append(
            {
                "speaker_label": entry.speaker_label,
                "text": text,
                "start_ms": int(entry.start_time_seconds * 1000),
                "end_ms": int(entry.end_time_seconds * 1000),
            }
        )

    if not converted:
        return []

    # ── Step 3: merge adjacent same-speaker entries ───────────────────────────
    merged: list[dict] = [converted[0]]
    for curr in converted[1:]:
        prev = merged[-1]
        gap_ms = curr["start_ms"] - prev["end_ms"]
        same_speaker = curr["speaker_label"] == prev["speaker_label"]

        if same_speaker and gap_ms < merge_threshold_ms:
            # Extend the previous segment; join text with a space
            prev["end_ms"] = curr["end_ms"]
            prev["text"] = prev["text"].rstrip() + " " + curr["text"].lstrip()
        else:
            merged.append(curr)

    # ── Step 4: assign stable IDs ─────────────────────────────────────────────
    segments = []
    for i, seg in enumerate(merged, start=1):
        seg_id = f"seg_{i:03d}"   # seg_001 … seg_999
        segments.append(
            NormalisedSegment(
                segment_id=seg_id,
                start_ms=seg["start_ms"],
                end_ms=seg["end_ms"],
                speaker_label=seg["speaker_label"],
                text=seg["text"],
            )
        )

    return segments


# ─────────────────────────────────────────────────────────────────────────────
# Text normalisation helpers (used by the evidence verification layer too)
# ─────────────────────────────────────────────────────────────────────────────

def normalise_text_for_matching(text: str) -> str:
    """
    Canonical form used when checking whether a quote exists in a segment:
      1. Unicode NFC normalisation (collapses composed/decomposed characters)
      2. Collapse runs of whitespace to a single space
      3. Strip leading/trailing whitespace
      4. Casefold (locale-independent lowercase)

    Callers MUST apply this to both the segment text and the quote before
    checking containment.  This function is unit-tested directly.
    """
    text = unicodedata.normalize("NFC", text)
    # Replace any whitespace sequence (including \n, \t, \u00a0) with a single space
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    text = text.casefold()
    return text


def format_segment_for_prompt(seg: NormalisedSegment) -> str:
    """
    Format a segment as the line Claude sees in the prompt.
    Example: [seg_014 | counsellor | 02:31] Aap kaunsa course le rahe hain?
    """
    start_sec = seg.start_ms // 1000
    minutes = start_sec // 60
    seconds = start_sec % 60
    timestamp = f"{minutes:02d}:{seconds:02d}"
    return f"[{seg.segment_id} | {seg.role} | {timestamp}] {seg.text}"
