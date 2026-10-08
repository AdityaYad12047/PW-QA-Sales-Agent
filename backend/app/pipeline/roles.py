"""
backend/app/pipeline/roles.py

Infer conversational roles for transcript turns.

IMPORTANT:
Groq Whisper transcription provides timestamps, but the transcription
endpoint does not provide speaker diarization.  Therefore this module never
claims that ``speaker_0`` is a real audio speaker.  For Groq transcripts it
uses utterance-level conversational cues and dialogue state to infer
``counsellor`` vs ``student``/``parent``.  The UI/pipeline records the source
as ``inferred`` and keeps confidence low when evidence is weak.

Sarvam diarized labels continue to work through the same interface.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from app.pipeline.segments import NormalisedSegment

logger = logging.getLogger(__name__)


_COUNSELLOR_PHRASES = [
    r"\bphysics wallah\b", r"\bpw\b",
    r"\bkaunsa class\b", r"\bkaunsi class\b", r"\bwhich class\b",
    r"\btarget exam\b", r"\bkaunsa exam\b", r"\bwhich exam\b",
    r"\bpreparation\b", r"\btaiyari\b", r"\bpreparing\b",
    r"\bcourse recommend\b", r"\bbatch recommend\b",
    r"\bscholar(ship)?\b", r"\bnamaste\b",
    r"\bmain.*counsellor\b", r"\bi('m| am) .*counsellor\b",
    r"\bkya aap\b", r"\baap ka\b", r"\baapka\b",
    r"\bjee\b", r"\bneet\b", r"\bcuet\b",
    r"\bregistration\b", r"\bdemo class\b", r"\btrial\b",
    r"\bnext step\b", r"\bpayment link\b", r"\bwhatsapp\b",
    r"\bbatch\b", r"\bclasses?\b", r"\bcourse\b",
]

_STUDENT_PARENT_PHRASES = [
    r"\bmera beta\b", r"\bmeri beti\b", r"\bbeti\b",
    r"\bghar pe\b", r"\bgharwalo\b", r"\bghar walon\b",
    r"\bmere papa\b", r"\bmeri mummy\b", r"\bmom\b", r"\bdad\b",
    r"\bsochna hai\b", r"\bsoch ke batata\b", r"\bsoch ke batati\b",
    r"\bbahut mahanga\b", r"\bmehenga\b", r"\bafford\b",
    r"\bpaise\b", r"\bbudget\b", r"\bdusri coaching\b",
    r"\ballen\b", r"\baakash\b", r"\bunacademy\b",
    r"\bthoda time\b", r"\bok bhaiya\b", r"\bthank you bhaiya\b",
    r"\bthank you didi\b", r"\bmy son\b", r"\bmy daughter\b",
]

_COUNSELLOR_RE = re.compile("|".join(_COUNSELLOR_PHRASES), re.IGNORECASE)
_STUDENT_RE = re.compile("|".join(_STUDENT_PARENT_PHRASES), re.IGNORECASE)

_QUESTION_RE = re.compile(
    r"(?:\?|\b(?:kya|kaunsa|kaunsi|kitna|kitni|kab|kyun|kyon|how|what|which|when|why|where|tell me|can you|could you|do you|are you|have you)\b)",
    re.IGNORECASE,
)

_COUNSELLOR_FIRST_PERSON = re.compile(
    r"\b(?:main|hum|i)\b.*\b(?:bata|batata|batati|check|share|send|recommend|suggest|help)\b",
    re.IGNORECASE,
)

_STUDENT_FIRST_PERSON = re.compile(
    r"\b(?:main|i|we|hum)\b.*\b(?:chahta|chahti|chahiye|kar raha|kar rahi|padh raha|padh rahi|looking|interested|want)\b",
    re.IGNORECASE,
)


@dataclass
class RoleMappingResult:
    segments: list[NormalisedSegment]
    speaker_to_role: dict[str, str]
    speaker_confidence: dict[str, float]
    warning: Optional[str] = None


def _cue_scores(text: str) -> tuple[float, float]:
    """Return (counsellor_score, student_parent_score) for one utterance."""
    t = text.casefold().strip()
    c = min(4.0, float(len(_COUNSELLOR_RE.findall(t))))
    s = min(4.0, float(len(_STUDENT_RE.findall(t))))

    if _QUESTION_RE.search(t):
        c += 1.5
    if _COUNSELLOR_FIRST_PERSON.search(t):
        c += 1.5
    if _STUDENT_FIRST_PERSON.search(t):
        s += 1.5

    # Typical acknowledgement/answer markers are stronger student/parent
    # signals when the previous turn was a counsellor question.
    if re.search(r"\b(?:yes|haan|ji|nahi|no|actually|currently|right now|i am|mera)\b", t):
        s += 0.5

    return c, s


def _infer_utterance_roles(segments: list[NormalisedSegment]) -> tuple[dict[int, str], dict[int, float]]:
    """Infer roles from conversational evidence without pretending diarization."""
    roles: dict[int, str] = {}
    confidence: dict[int, float] = {}
    previous_role: Optional[str] = None
    previous_was_question = False

    for idx, seg in enumerate(segments):
        c, s = _cue_scores(seg.text)
        is_question = bool(_QUESTION_RE.search(seg.text))

        # Strong local evidence wins first.
        if c >= s + 1.0:
            role = "counsellor"
            conf = min(0.88, 0.50 + 0.10 * (c - s))
        elif s >= c + 1.0:
            role = "student"
            conf = min(0.88, 0.50 + 0.10 * (s - c))
        elif idx == 0:
            # Counselling calls commonly begin with the counsellor greeting.
            # This is an inference, not diarization.
            role = "counsellor"
            conf = 0.45
        elif previous_role == "counsellor" and previous_was_question:
            # A response after a counsellor discovery question is usually the
            # student/parent turn. This is conversational-state inference.
            role = "student"
            conf = 0.55
        elif previous_role == "student" and is_question:
            role = "counsellor"
            conf = 0.55
        elif previous_role is not None:
            # Keep the current conversational speaker only when no evidence
            # indicates a turn change. This avoids blindly alternating labels.
            role = previous_role
            conf = 0.38
        else:
            role = "unknown"
            conf = 0.20

        roles[idx] = role
        confidence[idx] = round(conf, 3)
        previous_role = role
        previous_was_question = is_question

    return roles, confidence


def map_roles(segments: list[NormalisedSegment]) -> RoleMappingResult:
    """Assign roles to transcript turns and return diagnostics."""
    if not segments:
        return RoleMappingResult(
            segments=[],
            speaker_to_role={},
            speaker_confidence={},
            warning="No segments to map roles on.",
        )

    unique_speakers = list(dict.fromkeys(s.speaker_label for s in segments))
    has_groq_style_single_speaker = len(unique_speakers) == 1 and unique_speakers[0] == "speaker_0"

    if has_groq_style_single_speaker:
        roles, confidences = _infer_utterance_roles(segments)
        counts: dict[str, int] = defaultdict(int)
        for idx, seg in enumerate(segments):
            seg.role = roles[idx]
            seg.role_confidence = confidences[idx]
            seg.role_source = "inferred"
            counts[seg.role] += 1

        warning = (
            "Groq Whisper does not provide speaker diarization. "
            "Roles were inferred from utterance content and conversation state; "
            "review low-confidence turns before treating speaker identity as ground truth."
        )
        logger.warning(warning)

        # This mapping is informational only. Do not pretend speaker_0 is a
        # real person; the UI can still display inferred roles per turn.
        speaker_to_role = {"speaker_0": "mixed"}
        speaker_confidence = {
            "speaker_0": round(
                sum(confidences.values()) / max(1, len(confidences)),
                3,
            )
        }

        return RoleMappingResult(
            segments=segments,
            speaker_to_role=speaker_to_role,
            speaker_confidence=speaker_confidence,
            warning=warning,
        )

    # Sarvam / other genuine diarized providers: retain the existing
    # speaker-level mapping approach.
    speaker_scores: dict[str, dict[str, float]] = {
        sp: {"counsellor": 0.0, "student_parent": 0.0}
        for sp in unique_speakers
    }
    first_speaker = segments[0].speaker_label
    speaker_scores[first_speaker]["counsellor"] += 0.4

    for seg in segments:
        c, s = _cue_scores(seg.text)
        speaker_scores[seg.speaker_label]["counsellor"] += c * 0.6
        speaker_scores[seg.speaker_label]["student_parent"] += s * 0.6

    speaker_to_role: dict[str, str] = {}
    speaker_confidence: dict[str, float] = {}

    for sp, scores in speaker_scores.items():
        c = scores["counsellor"]
        s = scores["student_parent"]
        total = c + s
        if total == 0:
            role = "counsellor" if sp == first_speaker else "unknown"
            conf = 0.30 if role == "counsellor" else 0.10
        elif c > s:
            role = "counsellor"
            conf = min(0.90, 0.40 + 0.50 * (c / total))
        else:
            role = "student"
            conf = min(0.90, 0.40 + 0.50 * (s / total))
        speaker_to_role[sp] = role
        speaker_confidence[sp] = round(conf, 3)

    # Ensure only one diarized speaker is treated as counsellor when the
    # heuristic happens to score several speakers that way.
    counsellors = [sp for sp, role in speaker_to_role.items() if role == "counsellor"]
    if len(counsellors) > 1:
        best = max(counsellors, key=lambda sp: speaker_confidence[sp])
        for sp in counsellors:
            if sp != best:
                speaker_to_role[sp] = "student"
                speaker_confidence[sp] = max(
                    0.1,
                    speaker_confidence[sp] - 0.2,
                )

    for seg in segments:
        sp = seg.speaker_label
        seg.role = speaker_to_role.get(sp, "unknown")
        seg.role_confidence = speaker_confidence.get(sp, 0.1)
        seg.role_source = "heuristic"

    return RoleMappingResult(
        segments=segments,
        speaker_to_role=speaker_to_role,
        speaker_confidence=speaker_confidence,
        warning=None,
    )


def apply_role_override(
    segments: list[NormalisedSegment],
    segment_id: str,
    new_role: str,
) -> bool:
    """Apply a manual role override to the selected segment's speaker."""
    target_seg = next(
        (s for s in segments if s.segment_id == segment_id),
        None,
    )
    if target_seg is None:
        return False

    target_speaker = target_seg.speaker_label
    for seg in segments:
        if seg.speaker_label == target_speaker:
            seg.role = new_role
            seg.role_source = "manual_override"
            seg.role_confidence = 1.0

    logger.info(
        "Manual role override: speaker '%s' -> '%s' (triggered by segment %s)",
        target_speaker,
        new_role,
        segment_id,
    )
    return True
