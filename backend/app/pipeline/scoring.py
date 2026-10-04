"""
backend/app/pipeline/scoring.py
Pure Python scoring engine:
  - Deterministic weighted score calculation (0–100)
  - Proportional redistribution of weights for Not Applicable (N/A) criteria
  - Compliance criterion score derived from verified flags
  - Gate logic: any verified critical flag triggers needs_review
  - Strict input validation (weights must sum to 100)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from app.config.rubric_loader import ComplianceScoringConfig, RubricConfig
from app.pipeline.verify import VerifiedCriterion, VerifiedFlag


@dataclass
class CriterionScoreResult:
    criterion_id: str
    raw_score: Optional[int]          # 0–4
    confidence: Optional[float]
    rationale: Optional[str]
    not_applicable: bool
    configured_weight: float
    effective_weight: float           # after N/A redistribution
    weighted_contribution: float      # effective_weight * (raw_score / 4.0)


@dataclass
class ScoreCalculationResult:
    overall_score: Optional[float]    # 0.0 to 100.0, rounded to 1 decimal place; None if all N/A
    has_critical_flag: bool           # True if any verified critical compliance flag exists
    status: str                       # 'completed' or 'needs_review'
    criteria_results: List[CriterionScoreResult] = field(default_factory=list)
    compliance_score: int = 4
    redistributed: bool = False
    notes: List[str] = field(default_factory=list)


def compute_compliance_score(
    flags: Sequence[VerifiedFlag],
    config: Optional[ComplianceScoringConfig] = None,
    min_confidence_for_gate: float = 0.6,
) -> tuple[int, bool]:
    """
    Compute compliance criterion score (0–4) from verified compliance flags.
    Mapping:
      - no_flags = 4
      - minor_only = 3
      - one_major = 2
      - multiple_major = 1
      - any_critical = 0
    Gating:
      - A critical flag gates to needs_review only if confidence >= min_confidence_for_gate (default 0.6).
    """
    if config is None:
        config = ComplianceScoringConfig()

    verified_flags = [f for f in flags if f.is_verified]
    if not verified_flags:
        return config.no_flags, False

    critical_flags = [f for f in verified_flags if f.severity.lower() == "critical"]
    major_flags = [f for f in verified_flags if f.severity.lower() == "major"]
    minor_flags = [f for f in verified_flags if f.severity.lower() == "minor"]

    # Critical flag gates call only if confidence >= min_confidence_for_gate
    has_critical_gate = any(f.confidence >= min_confidence_for_gate for f in critical_flags)

    if critical_flags:
        return config.any_critical, has_critical_gate
    elif len(major_flags) >= 2:
        return config.multiple_major, False
    elif len(major_flags) == 1:
        return config.one_major, False
    elif minor_flags:
        return config.minor_only, False
    else:
        return config.no_flags, False


def calculate_overall_score(
    criteria: Sequence[VerifiedCriterion],
    flags: Sequence[VerifiedFlag],
    rubric: RubricConfig,
    weights_override: Optional[Dict[str, float]] = None,
    min_confidence_for_gate: Optional[float] = None,
) -> ScoreCalculationResult:
    """
    Compute the deterministic overall score and gate decisions.

    Formula:
      overall_score = sum(effective_weight_i * score_i / 4.0)
    """
    effective_min_confidence_gate = (
        min_confidence_for_gate
        if min_confidence_for_gate is not None
        else getattr(rubric, "min_confidence_for_gate", 0.70)
    )

    # 1. Determine weights
    base_weights = weights_override if weights_override is not None else rubric.weights_dict
    configured_weights = dict(base_weights)

    total_weight = sum(configured_weights.values())
    if abs(total_weight - 100.0) > 0.01:
        raise ValueError(f"Rubric weights must sum to 100.0 within 0.01 tolerance. Current sum: {total_weight}")

    # 2. Check for missing criteria in rubric
    rubric_ids = set(configured_weights.keys())
    input_criteria_by_id = {c.criterion_id: c for c in criteria}

    # 3. Compute compliance criterion score from verified flags
    compliance_score, has_critical_flag = compute_compliance_score(
        flags, rubric.compliance_scoring, min_confidence_for_gate=effective_min_confidence_gate
    )

    notes: List[str] = []

    # 4. Collate scores for all rubric criteria
    raw_scores: Dict[str, Optional[int]] = {}
    na_flags: Dict[str, bool] = {}
    confidences: Dict[str, Optional[float]] = {}
    rationales: Dict[str, Optional[str]] = {}

    for cid in rubric_ids:
        if cid == "compliance":
            raw_scores[cid] = compliance_score
            na_flags[cid] = False
            confidences[cid] = 1.0
            rationales[cid] = (
                f"Computed from {len([f for f in flags if f.is_verified])} verified compliance flag(s)."
            )
        elif cid in input_criteria_by_id:
            vc = input_criteria_by_id[cid]
            raw_scores[cid] = vc.score
            na_flags[cid] = vc.not_applicable
            confidences[cid] = vc.confidence
            rationales[cid] = vc.rationale
        else:
            raise ValueError(f"Missing evaluation for rubric criterion: '{cid}'")

    # 5. Handle N/A redistribution
    applicable_cids = [cid for cid in rubric_ids if not na_flags[cid]]
    na_cids = [cid for cid in rubric_ids if na_flags[cid]]

    effective_weights: Dict[str, float] = {}

    applicable_weight_sum = sum(configured_weights[cid] for cid in applicable_cids)

    if not applicable_cids or applicable_weight_sum <= 0:
        # Edge case: All criteria are marked N/A or remaining criteria have zero weight
        notes.append("All criteria were marked Not Applicable or have zero weight.")
        return ScoreCalculationResult(
            overall_score=None,
            has_critical_flag=has_critical_flag,
            status="needs_review" if has_critical_flag else "completed",
            criteria_results=[],
            compliance_score=compliance_score,
            redistributed=False,
            notes=notes,
        )

    redistributed = len(na_cids) > 0
    scale_factor = 100.0 / applicable_weight_sum

    for cid in rubric_ids:
        if na_flags[cid]:
            effective_weights[cid] = 0.0
        else:
            effective_weights[cid] = configured_weights[cid] * scale_factor

    # 6. Compute weighted score
    criteria_results: List[CriterionScoreResult] = []
    total_score = 0.0

    for cid in sorted(rubric_ids):
        raw_s = raw_scores[cid]
        is_na = na_flags[cid]
        conf_w = configured_weights[cid]
        eff_w = effective_weights[cid]

        if is_na or raw_s is None:
            contrib = 0.0
        else:
            contrib = eff_w * (raw_s / 4.0)
            total_score += contrib

        criteria_results.append(
            CriterionScoreResult(
                criterion_id=cid,
                raw_score=raw_s if not is_na else None,
                confidence=confidences[cid],
                rationale=rationales[cid],
                not_applicable=is_na,
                configured_weight=round(conf_w, 2),
                effective_weight=round(eff_w, 2),
                weighted_contribution=round(contrib, 2),
            )
        )

    rounded_overall = round(total_score, 1)

    # 7. Gate logic
    min_review_score = getattr(rubric, "min_overall_score_for_review", 50.0)
    if has_critical_flag:
        status = "needs_review"
        notes.append("Call gated to 'needs_review' due to verified critical compliance flag.")
    elif rounded_overall < min_review_score:
        status = "needs_review"
        notes.append(f"Call gated to 'needs_review' due to overall score below {min_review_score}.")
    else:
        status = "completed"

    return ScoreCalculationResult(
        overall_score=rounded_overall,
        has_critical_flag=has_critical_flag,
        status=status,
        criteria_results=criteria_results,
        compliance_score=compliance_score,
        redistributed=redistributed,
        notes=notes,
    )
