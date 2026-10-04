"""
backend/tests/test_scoring.py
Unit tests for deterministic scoring engine in pipeline/scoring.py:
  - Exact formula verification: overall = sum(eff_w * score / 4)
  - Proportional N/A weight redistribution
  - Gate logic: critical flag forces needs_review
  - Edge cases: all N/A, missing criterion, weights not summing to 100
"""
import pytest

from app.config.rubric_loader import ComplianceScoringConfig, CriterionConfig, RubricConfig
from app.pipeline.scoring import calculate_overall_score, compute_compliance_score
from app.pipeline.verify import VerifiedCriterion, VerifiedFlag


@pytest.fixture
def mock_rubric():
    criteria = [
        CriterionConfig(
            criterion_id="discovery",
            title="Discovery",
            weight=20.0,
            description="Discovery",
            anchors={0: "a0", 1: "a1", 2: "a2", 3: "a3", 4: "a4"},
        ),
        CriterionConfig(
            criterion_id="course_fit",
            title="Course Fit",
            weight=20.0,
            description="Course Fit",
            anchors={0: "a0", 1: "a1", 2: "a2", 3: "a3", 4: "a4"},
        ),
        CriterionConfig(
            criterion_id="pitch_quality",
            title="Pitch Quality",
            weight=15.0,
            description="Pitch Quality",
            anchors={0: "a0", 1: "a1", 2: "a2", 3: "a3", 4: "a4"},
        ),
        CriterionConfig(
            criterion_id="objection_handling",
            title="Objection Handling",
            weight=20.0,
            description="Objection Handling",
            anchors={0: "a0", 1: "a1", 2: "a2", 3: "a3", 4: "a4"},
        ),
        CriterionConfig(
            criterion_id="compliance",
            title="Compliance",
            weight=15.0,
            description="Compliance",
            anchors={0: "a0", 1: "a1", 2: "a2", 3: "a3", 4: "a4"},
        ),
        CriterionConfig(
            criterion_id="closing_next_steps",
            title="Closing",
            weight=10.0,
            description="Closing",
            anchors={0: "a0", 1: "a1", 2: "a2", 3: "a3", 4: "a4"},
        ),
    ]
    return RubricConfig(
        version=1,
        name="Test Rubric",
        criteria=criteria,
        compliance_scoring=ComplianceScoringConfig(
            no_flags=4,
            minor_only=3,
            one_major=2,
            multiple_major=1,
            any_critical=0,
        ),
    )


def test_compliance_score_mapping_complete():
    cfg = ComplianceScoringConfig(no_flags=4, minor_only=3, one_major=2, multiple_major=1, any_critical=0)

    # 1. No flags -> 4
    score, gate = compute_compliance_score([], cfg)
    assert score == 4
    assert not gate

    # 2. Minor only -> 3
    minor_flag = VerifiedFlag("R-07", "minor", 0.9, "Minor breach", "verified")
    score, gate = compute_compliance_score([minor_flag], cfg)
    assert score == 3
    assert not gate

    # 3. One major -> 2
    major_flag1 = VerifiedFlag("R-04", "major", 0.9, "Disparaged competitor", "verified")
    score, gate = compute_compliance_score([major_flag1], cfg)
    assert score == 2
    assert not gate

    # 4. Multiple major -> 1
    major_flag2 = VerifiedFlag("R-05", "major", 0.85, "Undisclosed limitation", "verified")
    score, gate = compute_compliance_score([major_flag1, major_flag2], cfg)
    assert score == 1
    assert not gate

    # 5. Any critical -> 0
    crit_flag = VerifiedFlag("R-01", "critical", 0.9, "Guaranteed rank", "verified")
    score, gate = compute_compliance_score([major_flag1, crit_flag], cfg)
    assert score == 0
    assert gate


def test_min_confidence_for_gate_threshold():
    cfg = ComplianceScoringConfig(no_flags=4, minor_only=3, one_major=2, multiple_major=1, any_critical=0)

    # Critical flag with confidence 0.55 < default threshold 0.6 -> does NOT gate
    low_conf_crit = VerifiedFlag("R-01", "critical", 0.55, "Possible guarantee", "verified")
    score, gate = compute_compliance_score([low_conf_crit], cfg, min_confidence_for_gate=0.6)
    assert score == 0
    assert not gate  # Gate is False because confidence < 0.6

    # Critical flag with confidence 0.65 >= default threshold 0.6 -> DOES gate
    high_conf_crit = VerifiedFlag("R-01", "critical", 0.65, "Definite guarantee", "verified")
    score, gate = compute_compliance_score([high_conf_crit], cfg, min_confidence_for_gate=0.6)
    assert score == 0
    assert gate  # Gate is True because confidence >= 0.6


def test_weights_sum_0_01_tolerance(mock_rubric):
    criteria = [
        VerifiedCriterion(criterion_id="discovery", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="course_fit", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="pitch_quality", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="objection_handling", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="closing_next_steps", score=4, confidence=1.0, rationale="", not_applicable=False),
    ]
    # Sum = 100.008 (within 0.01 tolerance) -> should succeed
    tolerated_weights = {
        "discovery": 20.002,
        "course_fit": 20.002,
        "pitch_quality": 15.002,
        "objection_handling": 20.001,
        "compliance": 15.001,
        "closing_next_steps": 10.0,
    }
    res = calculate_overall_score(criteria, [], mock_rubric, weights_override=tolerated_weights)
    assert res.overall_score == 100.0

    # Sum = 100.05 (> 0.01 tolerance) -> should fail
    bad_weights = dict(tolerated_weights)
    bad_weights["discovery"] = 20.05
    with pytest.raises(ValueError, match="within 0.01 tolerance"):
        calculate_overall_score(criteria, [], mock_rubric, weights_override=bad_weights)


def test_standard_scoring_calculation(mock_rubric):
    # Perfect score: all 4s -> overall score must be 100.0
    criteria = [
        VerifiedCriterion(criterion_id="discovery", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="course_fit", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="pitch_quality", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="objection_handling", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="closing_next_steps", score=4, confidence=1.0, rationale="", not_applicable=False),
    ]
    # No flags -> compliance score is 4
    flags = []

    res = calculate_overall_score(criteria, flags, mock_rubric)
    assert res.overall_score == 100.0
    assert res.status == "completed"
    assert not res.has_critical_flag
    assert not res.redistributed


def test_proportional_na_weight_redistribution(mock_rubric):
    # objection_handling (weight 20) is marked N/A
    # Remaining applicable weight is 80 (20 + 20 + 15 + 15 + 10)
    # Scale factor = 100 / 80 = 1.25
    # Effective weights:
    # discovery: 20 * 1.25 = 25
    # course_fit: 20 * 1.25 = 25
    # pitch_quality: 15 * 1.25 = 18.75
    # objection_handling: 0
    # compliance: 15 * 1.25 = 18.75
    # closing_next_steps: 10 * 1.25 = 12.5
    # Sum of effective weights = 100
    criteria = [
        VerifiedCriterion(criterion_id="discovery", score=2, confidence=1.0, rationale="", not_applicable=False),   # 25 * 0.5 = 12.5
        VerifiedCriterion(criterion_id="course_fit", score=4, confidence=1.0, rationale="", not_applicable=False),  # 25 * 1.0 = 25.0
        VerifiedCriterion(criterion_id="pitch_quality", score=2, confidence=1.0, rationale="", not_applicable=False), # 18.75 * 0.5 = 9.375
        VerifiedCriterion(criterion_id="objection_handling", score=0, confidence=1.0, rationale="", not_applicable=True), # 0
        VerifiedCriterion(criterion_id="closing_next_steps", score=4, confidence=1.0, rationale="", not_applicable=False), # 12.5 * 1.0 = 12.5
    ]
    # No flags -> compliance score 4 (clean), contribution = 18.75 * 1.0 = 18.75
    flags = []

    res = calculate_overall_score(criteria, flags, mock_rubric)
    # Expected overall = 12.5 + 25.0 + 9.375 + 12.5 + 18.75 = 78.125 -> rounded to 78.1
    assert res.redistributed is True
    assert res.overall_score == pytest.approx(78.1, abs=0.1)
    assert res.status == "completed"


def test_gate_critical_flag_forces_needs_review(mock_rubric):
    # Perfect score in all other criteria
    criteria = [
        VerifiedCriterion(criterion_id="discovery", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="course_fit", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="pitch_quality", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="objection_handling", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="closing_next_steps", score=4, confidence=1.0, rationale="", not_applicable=False),
    ]
    # Critical verified compliance flag present -> compliance score becomes 0
    flags = [
        VerifiedFlag(
            rule_id="R-01",
            severity="critical",
            confidence=0.99,
            explanation="Guaranteed rank claimed",
            status="verified",
        )
    ]

    res = calculate_overall_score(criteria, flags, mock_rubric)
    assert res.has_critical_flag is True
    assert res.compliance_score == 0
    # Even though overall score is high (85.0), status MUST be needs_review
    assert res.overall_score == 85.0
    assert res.status == "needs_review"


def test_unverified_critical_flag_does_not_trigger_gate(mock_rubric):
    criteria = [
        VerifiedCriterion(criterion_id="discovery", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="course_fit", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="pitch_quality", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="objection_handling", score=4, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="closing_next_steps", score=4, confidence=1.0, rationale="", not_applicable=False),
    ]
    # Unverified flag (evidence failed verification)
    flags = [
        VerifiedFlag(
            rule_id="R-01",
            severity="critical",
            confidence=0.5,
            explanation="Hallucinated evidence",
            status="unverified",
        )
    ]

    res = calculate_overall_score(criteria, flags, mock_rubric)
    # Unverified flag is excluded from gate and compliance score
    assert not res.has_critical_flag
    assert res.compliance_score == 4
    assert res.overall_score == 100.0
    assert res.status == "completed"


def test_all_criteria_not_applicable(mock_rubric):
    criteria = [
        VerifiedCriterion(criterion_id="discovery", score=None, confidence=None, rationale="", not_applicable=True),
        VerifiedCriterion(criterion_id="course_fit", score=None, confidence=None, rationale="", not_applicable=True),
        VerifiedCriterion(criterion_id="pitch_quality", score=None, confidence=None, rationale="", not_applicable=True),
        VerifiedCriterion(criterion_id="objection_handling", score=None, confidence=None, rationale="", not_applicable=True),
        VerifiedCriterion(criterion_id="closing_next_steps", score=None, confidence=None, rationale="", not_applicable=True),
    ]
    # And override weights so compliance is 0
    weights_override = {
        "discovery": 25.0,
        "course_fit": 25.0,
        "pitch_quality": 25.0,
        "objection_handling": 25.0,
        "compliance": 0.0,
        "closing_next_steps": 0.0,
    }
    res = calculate_overall_score(criteria, [], mock_rubric, weights_override=weights_override)
    assert res.overall_score is None
    assert any("All criteria" in n for n in res.notes)


def test_missing_criterion_raises_error(mock_rubric):
    # Only 4 criteria provided, 'course_fit' missing
    criteria = [
        VerifiedCriterion(criterion_id="discovery", score=3, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="pitch_quality", score=3, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="objection_handling", score=3, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="closing_next_steps", score=3, confidence=1.0, rationale="", not_applicable=False),
    ]
    with pytest.raises(ValueError, match="Missing evaluation for rubric criterion: 'course_fit'"):
        calculate_overall_score(criteria, [], mock_rubric)


def test_invalid_weights_sum_raises_error(mock_rubric):
    criteria = [
        VerifiedCriterion(criterion_id="discovery", score=3, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="course_fit", score=3, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="pitch_quality", score=3, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="objection_handling", score=3, confidence=1.0, rationale="", not_applicable=False),
        VerifiedCriterion(criterion_id="closing_next_steps", score=3, confidence=1.0, rationale="", not_applicable=False),
    ]
    bad_weights = {"discovery": 50.0, "course_fit": 20.0, "pitch_quality": 10.0, "objection_handling": 10.0, "compliance": 5.0, "closing_next_steps": 0.0} # sums to 95
    with pytest.raises(ValueError, match="weights must sum to 100"):
        calculate_overall_score(criteria, [], mock_rubric, weights_override=bad_weights)
