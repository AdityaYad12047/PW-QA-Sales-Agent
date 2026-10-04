"""
backend/app/pipeline/verify.py
Pure Python verification layer for LLM evidence and compliance flags.
Checks:
  1. segment_id exists in call
  2. quote is an exact normalized substring of segment text
  3. role check (counsellor evidence must be spoken by counsellor)
  4. compliance flag has >= 1 verified evidence item
Records all rejections in verification_log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence, Set

from app.pipeline.segments import normalise_text_for_matching
from app.prompts.compliance import ComplianceFlagInput
from app.prompts.evaluator import CriterionEvaluation


class SegmentProtocol(Protocol):
    @property
    def segment_id(self) -> str: ...
    @property
    def role(self) -> str: ...
    @property
    def text(self) -> str: ...


@dataclass
class EvidenceVerificationResult:
    segment_id: str
    quote: str
    note: Optional[str] = None
    # 'verified' | 'rejected_missing_segment' | 'rejected_quote_not_found' | 'rejected_wrong_role'
    status: str = "unverified"
    detail: str = ""
    # Actual database text for segment to guarantee display integrity
    db_segment_text: Optional[str] = None
    db_segment_role: Optional[str] = None

    @property
    def is_verified(self) -> bool:
        return self.status == "verified"


@dataclass
class VerifiedFlag:
    rule_id: str
    severity: str
    confidence: float
    explanation: str
    # 'verified' | 'unverified' | 'uncategorized'
    status: str
    evidence: List[EvidenceVerificationResult] = field(default_factory=list)

    @property
    def is_verified(self) -> bool:
        return self.status == "verified"


@dataclass
class VerifiedCriterion:
    criterion_id: str
    score: Optional[int]
    confidence: Optional[float]
    rationale: Optional[str]
    not_applicable: bool
    evidence: List[EvidenceVerificationResult] = field(default_factory=list)


@dataclass
class VerificationSummary:
    criteria: List[VerifiedCriterion] = field(default_factory=list)
    flags: List[VerifiedFlag] = field(default_factory=list)
    verification_log: List[str] = field(default_factory=list)
    errors_for_retry: List[str] = field(default_factory=list)
    has_critical_flag: bool = False


def verify_evidence_item(
    segment_id: str,
    quote: str,
    segments_by_id: Dict[str, Any],
    require_role: Optional[str] = None,
    note: Optional[str] = None,
) -> EvidenceVerificationResult:
    """
    Verify a single piece of LLM-returned evidence.
    1. Check segment_id exists
    2. Check quote is normalized substring of segment text
    3. Check role matches require_role if specified
    """
    seg = segments_by_id.get(segment_id)
    if not seg:
        return EvidenceVerificationResult(
            segment_id=segment_id,
            quote=quote,
            note=note,
            status="rejected_missing_segment",
            detail=f"Segment '{segment_id}' does not exist in this call transcript.",
        )

    # Substring check with normalized text
    norm_quote = normalise_text_for_matching(quote)
    norm_text = normalise_text_for_matching(seg.text)

    if not norm_quote:
        return EvidenceVerificationResult(
            segment_id=segment_id,
            quote=quote,
            note=note,
            status="rejected_quote_not_found",
            detail="Quote is empty or whitespace-only.",
            db_segment_text=seg.text,
            db_segment_role=seg.role,
        )

    if norm_quote not in norm_text:
        return EvidenceVerificationResult(
            segment_id=segment_id,
            quote=quote,
            note=note,
            status="rejected_quote_not_found",
            detail=f"Quote '{quote}' not found in text of {segment_id}.",
            db_segment_text=seg.text,
            db_segment_role=seg.role,
        )

    # Role check
    if require_role and seg.role.lower() != require_role.lower():
        return EvidenceVerificationResult(
            segment_id=segment_id,
            quote=quote,
            note=note,
            status="rejected_wrong_role",
            detail=f"Segment {segment_id} was spoken by '{seg.role}', but expected '{require_role}'.",
            db_segment_text=seg.text,
            db_segment_role=seg.role,
        )

    return EvidenceVerificationResult(
        segment_id=segment_id,
        quote=quote,
        note=note,
        status="verified",
        detail="Quote verified successfully against database segment.",
        db_segment_text=seg.text,
        db_segment_role=seg.role,
    )


def verify_rubric_evaluations(
    evaluations: Sequence[CriterionEvaluation],
    segments: Sequence[Any],
) -> tuple[List[VerifiedCriterion], List[str], List[str]]:
    """
    Verify evidence for each criterion evaluation.
    Returns (verified_criteria, verification_log, errors_for_retry).
    """
    segments_by_id = {s.segment_id: s for s in segments}
    verified_list: List[VerifiedCriterion] = []
    log: List[str] = []
    retry_errors: List[str] = []

    for ce in evaluations:
        verified_evidence: List[EvidenceVerificationResult] = []
        for ev in ce.evidence:
            res = verify_evidence_item(
                segment_id=ev.segment_id,
                quote=ev.quote,
                segments_by_id=segments_by_id,
                require_role=None,  # Rubric criteria may cite student statements (e.g. objection) or counsellor
                note=ev.note,
            )
            verified_evidence.append(res)
            if not res.is_verified:
                msg = f"[Criterion {ce.criterion_id}] Evidence rejected: {res.detail}"
                log.append(msg)
                retry_errors.append(msg)
            else:
                log.append(f"[Criterion {ce.criterion_id}] Evidence verified: {res.segment_id}")

        verified_list.append(
            VerifiedCriterion(
                criterion_id=ce.criterion_id,
                score=ce.score,
                confidence=ce.confidence,
                rationale=ce.rationale,
                not_applicable=ce.not_applicable,
                evidence=verified_evidence,
            )
        )

    return verified_list, log, retry_errors


def verify_compliance_flags(
    flags: Sequence[ComplianceFlagInput],
    segments: Sequence[Any],
    valid_rule_ids: Optional[Set[str]] = None,
) -> tuple[List[VerifiedFlag], List[str], List[str]]:
    """
    Verify compliance flags:
      1. Check rule_id is valid
      2. Verify all evidence quotes with require_role='counsellor'
      3. Flag is verified only if >= 1 evidence item passed verification
    Returns (verified_flags, verification_log, errors_for_retry).
    """
    segments_by_id = {s.segment_id: s for s in segments}
    verified_flags: List[VerifiedFlag] = []
    log: List[str] = []
    retry_errors: List[str] = []

    for f in flags:
        status = "unverified"

        # Check rule ID validity
        if valid_rule_ids and f.rule_id not in valid_rule_ids:
            status = "uncategorized"
            msg = f"[Flag {f.rule_id}] Unknown rule ID not found in policy."
            log.append(msg)
            retry_errors.append(msg)

        verified_ev_list: List[EvidenceVerificationResult] = []
        for ev in f.evidence:
            # Policy rules govern counsellor conduct; evidence must be from counsellor
            res = verify_evidence_item(
                segment_id=ev.segment_id,
                quote=ev.quote,
                segments_by_id=segments_by_id,
                require_role="counsellor",
                note=f"Flagged for {f.rule_id} ({f.severity})",
            )
            verified_ev_list.append(res)
            if not res.is_verified:
                msg = f"[Flag {f.rule_id}] Evidence rejected: {res.detail}"
                log.append(msg)
                retry_errors.append(msg)
            else:
                log.append(f"[Flag {f.rule_id}] Evidence verified on segment {res.segment_id}")

        # Rule: Must have at least 1 verified evidence item
        if status != "uncategorized":
            has_passed_evidence = any(ev.is_verified for ev in verified_ev_list)
            if has_passed_evidence and len(verified_ev_list) > 0:
                status = "verified"
            else:
                status = "unverified"
                msg = f"[Flag {f.rule_id}] Flag has NO verified evidence items; marked unverified."
                log.append(msg)
                retry_errors.append(msg)

        verified_flags.append(
            VerifiedFlag(
                rule_id=f.rule_id,
                severity=f.severity.lower(),
                confidence=f.confidence,
                explanation=f.explanation,
                status=status,
                evidence=verified_ev_list,
            )
        )

    return verified_flags, log, retry_errors
