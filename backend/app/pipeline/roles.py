"""
backend/app/pipeline/roles.py

Map raw speaker labels (speaker_0, speaker_1, …) to semantic roles:
  counsellor | student | parent | unknown

Strategy (no LLM, heuristic only):
1. Who speaks first?  In counselling calls the counsellor almost always
   opens with a greeting/introduction.
2. Discovery signals: segments asking about class, course, exam, target year
   are counsellor moves.
3. Response signals: segments mentioning "mera beta/beti", price objections,
   "ghar pe poochna" are student/parent moves.
4. Confidence is the weighted average of signals fired.
5. If only one speaker is detected, we assign them 'counsellor' with low
   confidence and store a warning so the pipeline can flag this case.

The mapping result is stored on each NormalisedSegment.  A manual override
API (`/calls/{id}/segments/{seg}/role`) can set `role_source="manual_override"`.
"""
from __future__ import annotations

import re
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from app.pipeline.segments import NormalisedSegment

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Signal keyword sets
# (Covers Hindi, Hinglish and English; all casefolded before matching)
# ─────────────────────────────────────────────────────────────────────────────

# Phrases strongly associated with the counsellor role
_COUNSELLOR_PHRASES = [
    r"\bphysics wallah\b", r"\bpw\b",
    r"\bkaunsa class\b", r"\bkaunsi class\b", r"\bwhich class\b",
    r"\btarget exam\b", r"\bkaunsa exam\b",
    r"\bpreparation\b", r"\btaiyari\b",
    r"\bcourse recommend\b", r"\bbatch recommend\b",
    r"\bscholars?hip\b",
    r"\bnama?stake\b",  # "namaste"
    r"\bhello.*main\b",   # "Hello, main [name] bol raha hoon"
    r"\bmain.*counsellor\b",
    r"\bkya aap\b", r"\baap ka\b", r"\baap kaunsa\b",
    r"\bjee\b", r"\bneet\b", r"\bcuet\b",  # exam names used in discovery
    r"\bfees?\b", r"\bpricing\b", r"\bcost\b",
    r"\bemi\b",
    r"\bregistration\b",
    r"\bdemo class\b",
    r"\btrial\b",
]

# Phrases strongly associated with the student/parent role
_STUDENT_PARENT_PHRASES = [
    r"\bmera beta\b", r"\bbeti\b",
    r"\bghar pe\b", r"\bgharvalo\b",
    r"\bmere papa\b", r"\bmeri mummy\b", r"\bmom\b", r"\bdad\b",
    r"\bsochna hai\b", r"\bsoch ke batata\b",
    r"\bbahut mahanga\b", r"\bmehenga\b", r"\bafford\b",
    r"\bpaise\b", r"\bbudget\b",
    r"\bdusri coaching\b", r"\ballen\b", r"\baakash\b",
    r"\bthoda time\b",
    r"\bok bhaiya\b", r"\bthank you bhaiya\b", r"\bthank you didi\b",
    r"\bsir\b", r"\bma'am\b",   # student addressing counsellor
]

# Weight of each signal type
_OPENING_SPEAKER_WEIGHT = 0.4   # who opened the call
_KEYWORD_MATCH_WEIGHT = 0.6     # keyword signal (per segment, averaged)


@dataclass
class RoleMappingResult:
    """Output of map_roles(). Contains the updated segments + diagnostics."""
    segments: list[NormalisedSegment]
    speaker_to_role: dict[str, str]       # e.g. {"speaker_0": "counsellor"}
    speaker_confidence: dict[str, float]  # per-speaker confidence
    warning: Optional[str] = None         # set if diarization produced <=1 speaker


def map_roles(segments: list[NormalisedSegment]) -> RoleMappingResult:
    """
    Assign counsellor / student / parent / unknown to each segment.
    Mutates `role`, `role_confidence`, `role_source` on each NormalisedSegment.
    Returns a RoleMappingResult for logging/storage.
    """
    if not segments:
        return RoleMappingResult(
            segments=[], speaker_to_role={}, speaker_confidence={},
            warning="No segments to map roles on."
        )

    unique_speakers = list(dict.fromkeys(s.speaker_label for s in segments))

    warning = None
    if len(unique_speakers) == 1:
        warning = (
            "Diarization produced only one speaker label. "
            "Role assignment will be low-confidence. "
            "Manual role review is recommended."
        )
        logger.warning(warning)

    # ── Score each speaker across all their segments ──────────────────────────
    speaker_scores: dict[str, dict] = {
        sp: {"counsellor": 0.0, "student_parent": 0.0, "seg_count": 0}
        for sp in unique_speakers
    }

    # Opening-speaker bonus: first speaker gets a counsellor signal
    first_speaker = segments[0].speaker_label
    speaker_scores[first_speaker]["counsellor"] += _OPENING_SPEAKER_WEIGHT

    counsellor_re = re.compile("|".join(_COUNSELLOR_PHRASES), re.IGNORECASE)
    student_re = re.compile("|".join(_STUDENT_PARENT_PHRASES), re.IGNORECASE)

    for seg in segments:
        sp = seg.speaker_label
        text = seg.text

        c_hits = len(counsellor_re.findall(text))
        s_hits = len(student_re.findall(text))

        speaker_scores[sp]["counsellor"] += c_hits * _KEYWORD_MATCH_WEIGHT
        speaker_scores[sp]["student_parent"] += s_hits * _KEYWORD_MATCH_WEIGHT
        speaker_scores[sp]["seg_count"] += 1

    # ── Assign roles ──────────────────────────────────────────────────────────
    speaker_to_role: dict[str, str] = {}
    speaker_confidence: dict[str, float] = {}

    for sp, scores in speaker_scores.items():
        c_score = scores["counsellor"]
        sp_score = scores["student_parent"]
        total = c_score + sp_score

        if total == 0:
            # No signal at all — default to counsellor for the first speaker
            if sp == first_speaker:
                role = "counsellor"
                conf = 0.3
            else:
                role = "unknown"
                conf = 0.1
        elif c_score > sp_score:
            role = "counsellor"
            # Confidence: ratio of dominant score to total, scaled to [0.4, 0.9]
            conf = min(0.9, 0.4 + 0.5 * (c_score / total))
        else:
            # Heuristic: we can't distinguish student from parent from audio alone.
            # Default to "student" with a note that parent detection requires context.
            # Phase 2 can refine this using the LLM transcript.
            role = "student"
            conf = min(0.9, 0.4 + 0.5 * (sp_score / total))

        speaker_to_role[sp] = role
        speaker_confidence[sp] = round(conf, 3)

    # ── Edge case: if multiple speakers all got "counsellor", only keep the
    # highest-confidence one; demote the rest to "student" ────────────────────
    counsellors = [sp for sp, r in speaker_to_role.items() if r == "counsellor"]
    if len(counsellors) > 1:
        best = max(counsellors, key=lambda sp: speaker_confidence[sp])
        for sp in counsellors:
            if sp != best:
                speaker_to_role[sp] = "student"
                speaker_confidence[sp] = max(0.1, speaker_confidence[sp] - 0.2)

    # ── Stamp each segment ────────────────────────────────────────────────────
    for seg in segments:
        sp = seg.speaker_label
        seg.role = speaker_to_role.get(sp, "unknown")
        seg.role_confidence = speaker_confidence.get(sp, 0.1)
        seg.role_source = "heuristic"

    return RoleMappingResult(
        segments=segments,
        speaker_to_role=speaker_to_role,
        speaker_confidence=speaker_confidence,
        warning=warning,
    )


def apply_role_override(
    segments: list[NormalisedSegment],
    segment_id: str,
    new_role: str,
) -> bool:
    """
    Apply a manual role override to all segments with the same speaker_label
    as the target segment.  This ensures the override is consistent across
    the whole call, not just one segment.

    Returns True if the segment was found, False otherwise.
    """
    target_seg = next((s for s in segments if s.segment_id == segment_id), None)
    if target_seg is None:
        return False

    target_speaker = target_seg.speaker_label
    for seg in segments:
        if seg.speaker_label == target_speaker:
            seg.role = new_role
            seg.role_source = "manual_override"
            seg.role_confidence = 1.0  # manual override is authoritative

    logger.info(
        "Manual role override: speaker '%s' -> '%s' (triggered by segment %s)",
        target_speaker, new_role, segment_id
    )
    return True
