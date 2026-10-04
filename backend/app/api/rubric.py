"""
backend/app/api/rubric.py
Endpoints for managing rubric versions and weights:
  - GET /rubric: Get active rubric definition and weights
  - PUT /rubric: Update weights only -> deterministically recomputes all past evaluations (NO LLM calls)
  - POST /rubric/versions: Create a new rubric version definition
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.rubric_loader import CriterionConfig, RubricConfig, load_rubric_yaml, validate_weights_sum
from app.db.engine import get_db
from app.models.orm import Evaluation, EvaluationCriterion, RubricVersion
from app.pipeline.scoring import calculate_overall_score

router = APIRouter(prefix="/rubric", tags=["Rubric"])


class UpdateWeightsRequest(BaseModel):
    weights: Dict[str, float] = Field(
        ...,
        description="Map of criterion_id to weight. Must sum to exactly 100."
    )


class NewRubricVersionRequest(BaseModel):
    criteria: List[Dict[str, Any]]
    change_note: Optional[str] = "Rubric definition update"


def get_or_seed_active_rubric(db: Session) -> RubricVersion:
    """Fetch the active rubric version from DB, or seed v1 from rubric_v1.yaml."""
    stmt = select(RubricVersion).where(RubricVersion.is_active == True).order_by(RubricVersion.version.desc())
    active = db.execute(stmt).scalars().first()

    if not active:
        yaml_rubric = load_rubric_yaml()
        active = RubricVersion(
            version=yaml_rubric.version,
            criteria_json=[c.model_dump() for c in yaml_rubric.criteria],
            weights_json=yaml_rubric.weights_dict,
            is_active=True,
            change_note="Initial seed from rubric_v1.yaml",
        )
        db.add(active)
        db.commit()
        db.refresh(active)

    return active


@router.get("", response_model=Dict[str, Any])
def get_current_rubric(db: Session = Depends(get_db)):
    """Return the active rubric definition, criteria, and weights."""
    active = get_or_seed_active_rubric(db)
    return {
        "version_id": active.id,
        "version": active.version,
        "is_active": active.is_active,
        "criteria": active.criteria_json,
        "weights": active.weights_json,
        "created_at": active.created_at.isoformat(),
        "change_note": active.change_note,
    }


@router.put("", response_model=Dict[str, Any])
def update_rubric_weights(
    payload: UpdateWeightsRequest,
    db: Session = Depends(get_db),
):
    """
    Update weights only.
    Validates that weights sum to 100.
    Deterministically recomputes all past evaluations stored in the DB without any LLM calls!
    """
    active = get_or_seed_active_rubric(db)
    rubric_cfg = RubricConfig(
        version=active.version,
        name="Active Rubric",
        criteria=[CriterionConfig.model_validate(c) for c in active.criteria_json],
    )

    try:
        validate_weights_sum(payload.weights, set(rubric_cfg.weights_dict.keys()))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(e),
        )

    # Save new weights on active version
    active.weights_json = payload.weights
    db.add(active)

    # Recompute evaluations that used this rubric version
    stmt = select(Evaluation).where(Evaluation.rubric_version_id == active.id)
    evaluations = db.execute(stmt).scalars().all()
    recomputed_count = 0

    for ev in evaluations:
        criteria_scores = ev.criteria_scores
        flags = ev.compliance_flags

        # Convert to pipeline types for deterministic scoring function
        from app.pipeline.verify import VerifiedCriterion, VerifiedFlag
        pipeline_criteria = [
            VerifiedCriterion(
                criterion_id=cs.criterion_id,
                score=cs.score,
                confidence=cs.confidence,
                rationale=cs.rationale,
                not_applicable=cs.not_applicable,
            )
            for cs in criteria_scores
            if cs.criterion_id != "compliance"
        ]
        pipeline_flags = [
            VerifiedFlag(
                rule_id=f.rule_id,
                severity=f.severity,
                confidence=f.confidence,
                explanation=f.explanation,
                status=f.status,
            )
            for f in flags
        ]

        # Calculate new score
        res = calculate_overall_score(
            criteria=pipeline_criteria,
            flags=pipeline_flags,
            rubric=rubric_cfg,
            weights_override=payload.weights,
        )

        ev.overall_score = res.overall_score
        ev.has_critical_flag = res.has_critical_flag
        if ev.call:
            ev.call.status = res.status

        # Update weight_used snapshot on each criterion row
        for cs in criteria_scores:
            if cs.criterion_id in payload.weights:
                cs.weight_used = payload.weights[cs.criterion_id]

        recomputed_count += 1

    db.commit()

    return {
        "message": f"Weights updated successfully. {recomputed_count} evaluation(s) recomputed without LLM calls.",
        "weights": payload.weights,
        "recomputed_evaluations": recomputed_count,
    }


@router.post("/versions", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
def create_rubric_version(
    payload: NewRubricVersionRequest,
    db: Session = Depends(get_db),
):
    """Create a new rubric version definition (e.g. changing criteria or anchors)."""
    # Deactivate current active versions
    stmt = select(RubricVersion).where(RubricVersion.is_active == True)
    actives = db.execute(stmt).scalars().all()
    next_ver = 1
    for a in actives:
        if a.version >= next_ver:
            next_ver = a.version + 1
        a.is_active = False

    try:
        # Validate through RubricConfig
        rubric_cfg = RubricConfig(
            version=next_ver,
            name=f"Rubric v{next_ver}",
            criteria=[CriterionConfig.model_validate(c) for c in payload.criteria],
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid rubric definition: {e}",
        )

    new_version = RubricVersion(
        version=next_ver,
        criteria_json=[c.model_dump() for c in rubric_cfg.criteria],
        weights_json=rubric_cfg.weights_dict,
        is_active=True,
        change_note=payload.change_note,
    )
    db.add(new_version)
    db.commit()
    db.refresh(new_version)

    return {
        "id": new_version.id,
        "version": new_version.version,
        "change_note": new_version.change_note,
        "criteria_count": len(new_version.criteria_json),
        "weights": new_version.weights_json,
    }
