"""
backend/tests/test_roles.py
Unit tests for pipeline/roles.py — pure heuristic logic, no DB, no I/O.
"""
import pytest

from app.pipeline.segments import NormalisedSegment
from app.pipeline.roles import map_roles, apply_role_override


def make_seg(seg_id: str, speaker: str, text: str, start_ms: int = 0, end_ms: int = 1000):
    return NormalisedSegment(
        segment_id=seg_id,
        start_ms=start_ms,
        end_ms=end_ms,
        speaker_label=speaker,
        text=text,
    )


# ─────────────────────────────────────────────────────────────────────────────
# map_roles
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_segments():
    result = map_roles([])
    assert result.segments == []
    assert result.warning is not None


def test_first_speaker_gets_counsellor_by_default():
    segs = [
        make_seg("seg_001", "speaker_0", "Hello aap ka naam kya hai?"),
        make_seg("seg_002", "speaker_1", "Mera naam Rahul hai"),
    ]
    result = map_roles(segs)
    roles = {s.segment_id: s.role for s in result.segments}
    assert roles["seg_001"] == "counsellor"
    assert roles["seg_002"] in ("student", "unknown")


def test_counsellor_keywords_boost_score():
    segs = [
        make_seg("seg_001", "speaker_0", "Kaunsa class mein ho? Target exam kaunsa hai? JEE prepare kar rahe ho?"),
        make_seg("seg_002", "speaker_1", "Mera beta class 11 mein hai. Ghar pe poochna hoga fees ke baare mein."),
    ]
    result = map_roles(segs)
    roles = {s.speaker_label: s.role for s in result.segments}
    assert roles["speaker_0"] == "counsellor"
    # speaker_1 should be student (ghar pe, beta signals)


def test_only_one_counsellor_per_call():
    segs = [
        make_seg("seg_001", "speaker_0", "Hello main counsellor hoon. JEE target exam hai? Course recommend karunga."),
        make_seg("seg_002", "speaker_1", "Hello main bhi counsellor hoon. Physics Wallah se. JEE NEET course."),
        make_seg("seg_003", "speaker_2", "Mera beta hai sir."),
    ]
    result = map_roles(segs)
    counsellors = [s for s in result.segments if s.role == "counsellor"]
    unique_speakers_as_counsellor = set(s.speaker_label for s in counsellors)
    assert len(unique_speakers_as_counsellor) == 1


def test_single_speaker_produces_warning():
    segs = [make_seg("seg_001", "speaker_0", "Hello, main counsellor hoon")]
    result = map_roles(segs)
    assert result.warning is not None
    assert "one speaker" in result.warning.lower() or "single" in result.warning.lower()


def test_role_confidence_between_0_and_1():
    segs = [
        make_seg("seg_001", "speaker_0", "Namaste, kaunsa course chahiye?"),
        make_seg("seg_002", "speaker_1", "Mera beta hai, ghar pe poochna hai."),
    ]
    result = map_roles(segs)
    for seg in result.segments:
        assert seg.role_confidence is not None
        assert 0.0 <= seg.role_confidence <= 1.0


def test_role_source_is_heuristic():
    segs = [make_seg("seg_001", "speaker_0", "Hello namaste")]
    result = map_roles(segs)
    assert result.segments[0].role_source == "heuristic"


# ─────────────────────────────────────────────────────────────────────────────
# apply_role_override
# ─────────────────────────────────────────────────────────────────────────────

def test_override_changes_all_segments_of_speaker():
    segs = [
        make_seg("seg_001", "speaker_0", "Hello"),
        make_seg("seg_002", "speaker_0", "Course kaunsa?"),
        make_seg("seg_003", "speaker_1", "Mera beta"),
    ]
    # Initially role is unknown
    found = apply_role_override(segs, "seg_001", "counsellor")
    assert found is True
    # Both speaker_0 segments should now be counsellor
    for s in segs:
        if s.speaker_label == "speaker_0":
            assert s.role == "counsellor"
            assert s.role_source == "manual_override"
            assert s.role_confidence == 1.0


def test_override_does_not_affect_other_speakers():
    segs = [
        make_seg("seg_001", "speaker_0", "Hello"),
        make_seg("seg_002", "speaker_1", "Mera beta"),
    ]
    apply_role_override(segs, "seg_001", "counsellor")
    # speaker_1 should be untouched
    assert segs[1].role == "unknown"
    assert segs[1].role_source == "heuristic"


def test_override_returns_false_for_missing_segment():
    segs = [make_seg("seg_001", "speaker_0", "Hello")]
    found = apply_role_override(segs, "seg_999", "counsellor")
    assert found is False
