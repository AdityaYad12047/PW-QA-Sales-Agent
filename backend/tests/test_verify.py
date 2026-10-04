"""
backend/tests/test_verify.py
Unit tests for pipeline/verify.py.
Tests quote containment, unicode NFC normalization, role checks, and flag verification rules.
"""
from dataclasses import dataclass
from typing import Optional

import pytest

from app.pipeline.verify import (
    EvidenceVerificationResult,
    verify_compliance_flags,
    verify_evidence_item,
    verify_rubric_evaluations,
)
from app.prompts.compliance import ComplianceEvidenceInput, ComplianceFlagInput
from app.prompts.evaluator import CriterionEvaluation, EvidenceItemInput


@dataclass
class DummySegment:
    segment_id: str
    role: str
    text: str


@pytest.fixture
def sample_segments():
    return [
        DummySegment(
            segment_id="seg_001",
            role="counsellor",
            text="Namaste, main Physics Wallah se bol raha hoon. Kaise help kar sakta hoon?",
        ),
        DummySegment(
            segment_id="seg_002",
            role="student",
            text="Mujhe JEE 2026 ke liye online batch ke baare mein janna hai.",
        ),
        DummySegment(
            segment_id="seg_003",
            role="counsellor",
            text="Hum guarantee dete hain ki aapka AIR under 100 pakka aayega hamare Arjuna batch se.",
        ),
        DummySegment(
            segment_id="seg_004",
            role="student",
            text="Fees kitni hai aur koi scholarship milegi kya?",
        ),
    ]


def test_verify_evidence_success(sample_segments):
    seg_map = {s.segment_id: s for s in sample_segments}
    res = verify_evidence_item(
        segment_id="seg_001",
        quote="Namaste, main Physics Wallah se bol raha hoon",
        segments_by_id=seg_map,
        require_role="counsellor",
    )
    assert res.is_verified
    assert res.status == "verified"
    assert res.db_segment_text == sample_segments[0].text


def test_verify_evidence_casefold_and_whitespace(sample_segments):
    seg_map = {s.segment_id: s for s in sample_segments}
    # Quote has different casing and extra internal whitespace
    res = verify_evidence_item(
        segment_id="seg_001",
        quote="namaste,   main physics  wallah se bol raha hoon",
        segments_by_id=seg_map,
        require_role=None,
    )
    assert res.is_verified
    assert res.status == "verified"


def test_verify_evidence_missing_segment(sample_segments):
    seg_map = {s.segment_id: s for s in sample_segments}
    res = verify_evidence_item(
        segment_id="seg_999",
        quote="Nonexistent text",
        segments_by_id=seg_map,
    )
    assert not res.is_verified
    assert res.status == "rejected_missing_segment"


def test_verify_evidence_quote_not_found(sample_segments):
    seg_map = {s.segment_id: s for s in sample_segments}
    res = verify_evidence_item(
        segment_id="seg_001",
        quote="Bilkul galat quote jo transcript mein nahi hai",
        segments_by_id=seg_map,
    )
    assert not res.is_verified
    assert res.status == "rejected_quote_not_found"


def test_verify_evidence_wrong_role(sample_segments):
    seg_map = {s.segment_id: s for s in sample_segments}
    # seg_002 is student, but require counsellor
    res = verify_evidence_item(
        segment_id="seg_002",
        quote="Mujhe JEE 2026 ke liye online batch",
        segments_by_id=seg_map,
        require_role="counsellor",
    )
    assert not res.is_verified
    assert res.status == "rejected_wrong_role"


def test_verify_compliance_flags_success(sample_segments):
    flags = [
        ComplianceFlagInput(
            rule_id="R-01",
            severity="critical",
            confidence=0.95,
            explanation="Guaranteed selection claim",
            evidence=[
                ComplianceEvidenceInput(
                    segment_id="seg_003",
                    quote="Hum guarantee dete hain ki aapka AIR under 100 pakka aayega",
                )
            ],
        )
    ]
    verified_flags, log, errors = verify_compliance_flags(
        flags=flags,
        segments=sample_segments,
        valid_rule_ids={"R-01", "R-02"},
    )
    assert len(verified_flags) == 1
    assert verified_flags[0].status == "verified"
    assert verified_flags[0].is_verified
    assert len(errors) == 0


def test_verify_compliance_flag_rejected_when_no_evidence_passes(sample_segments):
    # Flag cites non-existent quote
    flags = [
        ComplianceFlagInput(
            rule_id="R-01",
            severity="critical",
            confidence=0.9,
            explanation="Hallucinated evidence",
            evidence=[
                ComplianceEvidenceInput(
                    segment_id="seg_003",
                    quote="Fabricated text not in audio",
                )
            ],
        )
    ]
    verified_flags, log, errors = verify_compliance_flags(
        flags=flags,
        segments=sample_segments,
        valid_rule_ids={"R-01"},
    )
    assert len(verified_flags) == 1
    assert verified_flags[0].status == "unverified"
    assert not verified_flags[0].is_verified
    assert any("rejected" in msg for msg in log)


def test_verify_compliance_flag_student_evidence_rejected_for_counsellor_rule(sample_segments):
    # Flag cites student segment (seg_002) for a counsellor rule
    flags = [
        ComplianceFlagInput(
            rule_id="R-01",
            severity="critical",
            confidence=0.9,
            explanation="Student words cited as counsellor breach",
            evidence=[
                ComplianceEvidenceInput(
                    segment_id="seg_002",
                    quote="Mujhe JEE 2026 ke liye",
                )
            ],
        )
    ]
    verified_flags, log, errors = verify_compliance_flags(
        flags=flags,
        segments=sample_segments,
        valid_rule_ids={"R-01"},
    )
    assert verified_flags[0].status == "unverified"
    assert not verified_flags[0].is_verified
    assert verified_flags[0].evidence[0].status == "rejected_wrong_role"
