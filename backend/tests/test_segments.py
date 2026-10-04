"""
backend/tests/test_segments.py
Unit tests for pipeline/segments.py — pure functions, no DB, no I/O.
Run with: pytest backend/tests/test_segments.py -v
"""
import pytest

from app.pipeline.segments import (
    NormalisedSegment,
    format_segment_for_prompt,
    normalise_segments,
    normalise_text_for_matching,
)
from app.services.stt import DiarizedEntry


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_entry(speaker: str, text: str, start: float, end: float) -> DiarizedEntry:
    return DiarizedEntry(
        speaker_label=speaker, text=text,
        start_time_seconds=start, end_time_seconds=end
    )


# ─────────────────────────────────────────────────────────────────────────────
# normalise_segments
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_input_returns_empty():
    assert normalise_segments([]) == []


def test_single_entry_gets_seg_001():
    entries = [make_entry("speaker_0", "Hello", 0.0, 1.0)]
    result = normalise_segments(entries)
    assert len(result) == 1
    assert result[0].segment_id == "seg_001"
    assert result[0].start_ms == 0
    assert result[0].end_ms == 1000


def test_timestamps_converted_to_ms():
    entries = [make_entry("speaker_0", "Hi", 1.5, 3.25)]
    result = normalise_segments(entries)
    assert result[0].start_ms == 1500
    assert result[0].end_ms == 3250


def test_empty_text_entries_are_dropped():
    entries = [
        make_entry("speaker_0", "", 0.0, 1.0),
        make_entry("speaker_0", "  ", 1.0, 2.0),
        make_entry("speaker_0", "Hello", 2.0, 3.0),
    ]
    result = normalise_segments(entries)
    assert len(result) == 1
    assert result[0].text == "Hello"


def test_all_empty_returns_empty():
    entries = [make_entry("speaker_0", "", 0.0, 1.0)]
    assert normalise_segments([]) == []


def test_adjacent_same_speaker_merged():
    entries = [
        make_entry("speaker_0", "Hello", 0.0, 1.0),
        make_entry("speaker_0", "how are you?", 1.2, 2.0),  # gap 200ms < threshold
    ]
    result = normalise_segments(entries, merge_threshold_ms=1500)
    assert len(result) == 1
    assert "Hello" in result[0].text
    assert "how are you?" in result[0].text
    assert result[0].end_ms == 2000


def test_adjacent_same_speaker_not_merged_large_gap():
    entries = [
        make_entry("speaker_0", "Hello", 0.0, 1.0),
        make_entry("speaker_0", "how are you?", 3.0, 4.0),  # gap 2000ms > threshold
    ]
    result = normalise_segments(entries, merge_threshold_ms=1500)
    assert len(result) == 2


def test_different_speakers_not_merged():
    entries = [
        make_entry("speaker_0", "Hello", 0.0, 1.0),
        make_entry("speaker_1", "Hi there", 1.1, 2.0),  # gap < threshold but diff speaker
    ]
    result = normalise_segments(entries, merge_threshold_ms=1500)
    assert len(result) == 2


def test_sequential_ids():
    entries = [
        make_entry("speaker_0", "A", 0.0, 1.0),
        make_entry("speaker_1", "B", 2.0, 3.0),
        make_entry("speaker_0", "C", 4.0, 5.0),
    ]
    result = normalise_segments(entries)
    ids = [s.segment_id for s in result]
    assert ids == ["seg_001", "seg_002", "seg_003"]


def test_default_role_is_unknown():
    entries = [make_entry("speaker_0", "Hello", 0.0, 1.0)]
    result = normalise_segments(entries)
    assert result[0].role == "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# normalise_text_for_matching
# ─────────────────────────────────────────────────────────────────────────────

def test_nfc_normalisation():
    # é composed vs decomposed
    composed = "café"
    decomposed = "cafe\u0301"  # e + combining accent
    assert normalise_text_for_matching(composed) == normalise_text_for_matching(decomposed)


def test_casefold():
    assert normalise_text_for_matching("Hello WORLD") == "hello world"


def test_whitespace_collapsing():
    assert normalise_text_for_matching("hello   \t  world") == "hello world"


def test_strip():
    assert normalise_text_for_matching("  hello  ") == "hello"


def test_hindi_text_unchanged_by_casefold():
    # Devanagari script has no case; NFC should be stable
    text = "नमस्ते"
    result = normalise_text_for_matching(text)
    assert "नमस्ते" in result


# ─────────────────────────────────────────────────────────────────────────────
# format_segment_for_prompt
# ─────────────────────────────────────────────────────────────────────────────

def test_prompt_format():
    seg = NormalisedSegment(
        segment_id="seg_014",
        start_ms=151000,  # 2min 31sec
        end_ms=155000,
        speaker_label="speaker_0",
        role="counsellor",
        text="Aap kaunsa course le rahe hain?",
    )
    line = format_segment_for_prompt(seg)
    assert line == "[seg_014 | counsellor | 02:31] Aap kaunsa course le rahe hain?"


def test_prompt_format_zero_start():
    seg = NormalisedSegment(
        segment_id="seg_001",
        start_ms=0,
        end_ms=3000,
        speaker_label="speaker_0",
        role="unknown",
        text="Hello",
    )
    line = format_segment_for_prompt(seg)
    assert "[seg_001 | unknown | 00:00]" in line
