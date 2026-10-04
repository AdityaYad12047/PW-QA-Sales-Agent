"""
backend/app/config/rubric_loader.py
Pydantic schemas and loader for evaluation rubric configuration.
Validates weights sum to 100 on load and on update.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class CriterionConfig(BaseModel):
    criterion_id: str
    title: str
    weight: float = Field(ge=0.0, le=100.0)
    description: str
    anchors: Dict[int, str]
    good_example: str = ""
    bad_example: str = ""

    @field_validator("anchors")
    @classmethod
    def validate_anchors(cls, v: Dict[int, str]) -> Dict[int, str]:
        # Anchor scores must include 0, 1, 2, 3, 4
        required = {0, 1, 2, 3, 4}
        keys = {int(k) for k in v.keys()}
        if not required.issubset(keys):
            raise ValueError(f"Anchors must include levels 0, 1, 2, 3, 4. Found: {keys}")
        return {int(k): text for k, text in v.items()}


class ComplianceScoringConfig(BaseModel):
    no_flags: int = 4
    minor_only: int = 3
    one_major: int = 2
    multiple_major: int = 1
    any_critical: int = 0


class RubricConfig(BaseModel):
    version: int
    name: str
    description: str = ""
    min_overall_score_for_review: float = 50.0
    min_confidence_for_gate: float = 0.70
    low_confidence_display_threshold: float = 0.70
    compliance_scoring: ComplianceScoringConfig = Field(default_factory=ComplianceScoringConfig)
    criteria: List[CriterionConfig]

    @model_validator(mode="after")
    def validate_total_weight(self) -> "RubricConfig":
        total = sum(c.weight for c in self.criteria)
        if abs(total - 100.0) > 0.01:
            raise ValueError(f"Rubric criteria weights must sum to 100.0 within 0.01 tolerance, got {total}")
        return self

    @property
    def weights_dict(self) -> Dict[str, float]:
        return {c.criterion_id: c.weight for c in self.criteria}

    @property
    def criteria_dict(self) -> Dict[str, CriterionConfig]:
        return {c.criterion_id: c for c in self.criteria}


def validate_weights_sum(weights: Dict[str, float], expected_ids: Optional[set[str]] = None) -> None:
    """Validate that weights sum to 100 within 0.01 tolerance and match expected criterion IDs."""
    if expected_ids:
        missing = expected_ids - set(weights.keys())
        extra = set(weights.keys()) - expected_ids
        if missing:
            raise ValueError(f"Missing weights for criteria: {missing}")
        if extra:
            raise ValueError(f"Unexpected criteria in weights: {extra}")

    for cid, w in weights.items():
        if w < 0:
            raise ValueError(f"Weight for {cid} cannot be negative: {w}")

    total = sum(weights.values())
    if abs(total - 100.0) > 0.01:
        raise ValueError(f"Weights must sum to 100.0 within 0.01 tolerance, got {total}")


def load_rubric_yaml(path: Optional[Path] = None) -> RubricConfig:
    """Load and validate the rubric YAML file."""
    if path is None:
        path = Path(__file__).parent / "rubric_v1.yaml"

    if not path.exists():
        raise FileNotFoundError(f"Rubric file not found: {path}")

    raw_data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return RubricConfig.model_validate(raw_data)
