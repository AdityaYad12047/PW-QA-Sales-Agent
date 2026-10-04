"""
backend/tests/test_evaluation_pipeline.py
Integration tests for the entire evaluation pipeline with mocked Claude:
  (a) valid output -> complete scoring, DB rows persisted, clean status
  (b) invalid JSON -> automatic repair retry succeeds
  (c) hallucinated segment ID -> rejected_missing_segment, flag unverified
  (d) quote not in segment -> rejected_quote_not_found, dropped from verified evidence
  (e) wrong-role evidence -> rejected_wrong_role, compliance flag unverified
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.engine import Base
from app.models.orm import Call, Counsellor, Evaluation, TranscriptSegment
from app.pipeline.evaluation_runner import run_evaluation_pipeline
from app.services.llm import ClaudeClient


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    db = TestingSession()

    # Seed counsellor
    c = Counsellor(id=1, name="Test Counsellor", email="test@pw.live")
    db.add(c)
    db.commit()

    # Seed call
    call = Call(
        id=101,
        counsellor_id=1,
        file_hash="fakehash123",
        original_filename="call1.mp3",
        file_path="uploads/call1.mp3",
        file_size_bytes=10000,
        status="transcribed",
    )
    db.add(call)
    db.commit()

    # Seed transcript segments
    segs = [
        TranscriptSegment(
            call_id=101,
            segment_id="seg_001",
            start_ms=0,
            end_ms=2500,
            speaker_label="speaker_0",
            role="counsellor",
            text="Hello, main Physics Wallah se baat kar raha hoon. Aap kis exam ki taiyari kar rahe hain?",
        ),
        TranscriptSegment(
            call_id=101,
            segment_id="seg_002",
            start_ms=2600,
            end_ms=4500,
            speaker_label="speaker_1",
            role="student",
            text="Main JEE 2026 ke liye soch raha tha lekin thoda physics weak hai.",
        ),
        TranscriptSegment(
            call_id=101,
            segment_id="seg_003",
            start_ms=4600,
            end_ms=8000,
            speaker_label="speaker_0",
            role="counsellor",
            text="Arjuna JEE batch mein basic se padhate hain aur daily doubt support rehta hai.",
        ),
        TranscriptSegment(
            call_id=101,
            segment_id="seg_004",
            start_ms=8200,
            end_ms=10500,
            speaker_label="speaker_0",
            role="counsellor",
            text="Kal subah 11 baje main aapko follow up call karunga batch enrollment ke liye.",
        ),
    ]
    for s in segs:
        db.add(s)
    db.commit()

    yield db
    db.close()
    engine.dispose()


def make_valid_evaluator_json():
    return {
        "evaluations": [
            {
                "criterion_id": "discovery",
                "score": 3,
                "confidence": 0.9,
                "rationale": "Counsellor probed target exam and weak areas effectively.",
                "not_applicable": False,
                "evidence": [
                    {
                        "segment_id": "seg_001",
                        "quote": "Aap kis exam ki taiyari kar rahe hain?",
                        "note": "Probed target exam",
                    }
                ],
            },
            {
                "criterion_id": "course_fit",
                "score": 3,
                "confidence": 0.9,
                "rationale": "Recommended Arjuna JEE linking to weak physics.",
                "not_applicable": False,
                "evidence": [
                    {
                        "segment_id": "seg_003",
                        "quote": "Arjuna JEE batch mein basic se padhate hain",
                        "note": "Recommended course",
                    }
                ],
            },
            {
                "criterion_id": "pitch_quality",
                "score": 3,
                "confidence": 0.85,
                "rationale": "Clear and polite pitch.",
                "not_applicable": False,
                "evidence": [],
            },
            {
                "criterion_id": "objection_handling",
                "score": 0,
                "confidence": 0.9,
                "rationale": "Student did not raise objections.",
                "not_applicable": True,
                "evidence": [],
            },
            {
                "criterion_id": "closing_next_steps",
                "score": 3,
                "confidence": 0.9,
                "rationale": "Clear follow-up time set.",
                "not_applicable": False,
                "evidence": [
                    {
                        "segment_id": "seg_004",
                        "quote": "Kal subah 11 baje main aapko follow up call karunga",
                        "note": "Next step confirmed",
                    }
                ],
            },
        ]
    }


def make_clean_compliance_json():
    return {
        "no_flags": True,
        "flags": [],
    }


def test_integration_pipeline_valid_run(test_db):
    """Scenario (a): Valid output from Claude -> successful evaluation and DB storage."""
    client = ClaudeClient(api_key="mock_key")

    def mock_api_call(system, prompt):
        if "compliance" in prompt.lower() or "policy" in prompt.lower():
            return json.dumps(make_clean_compliance_json()), 400, 50
        return json.dumps(make_valid_evaluator_json()), 600, 200

    with patch.object(client, "_execute_api_call", side_effect=mock_api_call):
        eval_record = run_evaluation_pipeline(call_id=101, db=test_db, client=client, bypass_cache=True)

    assert eval_record is not None
    assert eval_record.overall_score is not None
    assert eval_record.overall_score > 60.0
    assert not eval_record.has_critical_flag
    assert test_db.get(Call, 101).status == "completed"

    # Verify criteria stored in DB
    assert len(eval_record.criteria_scores) == 6
    # Verify evidence stored in DB
    assert len(eval_record.evidence_items) >= 3


def test_integration_pipeline_invalid_json_repair(test_db):
    """Scenario (b): Invalid JSON on attempt 1 -> repair retry succeeds."""
    client = ClaudeClient(api_key="mock_key")
    attempt_counter = {"eval": 0}

    def mock_api_call(system, prompt):
        if "compliance" in prompt.lower() or "policy" in prompt.lower():
            return json.dumps(make_clean_compliance_json()), 400, 50

        attempt_counter["eval"] += 1
        if attempt_counter["eval"] == 1:
            # First attempt returns invalid, broken JSON
            return "This is not JSON at all! {broken: ", 500, 20
        # Second attempt (repair) returns valid JSON
        return json.dumps(make_valid_evaluator_json()), 600, 200

    with patch.object(client, "_execute_api_call", side_effect=mock_api_call):
        eval_record = run_evaluation_pipeline(call_id=101, db=test_db, client=client, bypass_cache=True)

    assert attempt_counter["eval"] == 2
    assert eval_record.overall_score is not None
    assert test_db.get(Call, 101).status == "completed"


def test_integration_pipeline_hallucinated_segment_id(test_db):
    """Scenario (c): Claude returns hallucinated segment ID -> rejected_missing_segment."""
    client = ClaudeClient(api_key="mock_key")

    eval_data = make_valid_evaluator_json()
    # Replace seg_001 with hallucinated seg_999
    eval_data["evaluations"][0]["evidence"][0]["segment_id"] = "seg_999"

    def mock_api_call(system, prompt):
        if "compliance" in prompt.lower() or "policy" in prompt.lower():
            return json.dumps(make_clean_compliance_json()), 400, 50
        return json.dumps(eval_data), 600, 200

    with patch.object(client, "_execute_api_call", side_effect=mock_api_call):
        eval_record = run_evaluation_pipeline(call_id=101, db=test_db, client=client, bypass_cache=True)

    # Find the evidence for discovery
    ev_item = [e for e in eval_record.evidence_items if e.criterion_id == "discovery"][0]
    assert ev_item.verification_status == "rejected_missing_segment"
    assert ev_item.verification_detail is not None and "does not exist" in ev_item.verification_detail


def test_integration_pipeline_quote_not_in_segment(test_db):
    """Scenario (d): Quote is not a substring of segment text -> rejected_quote_not_found."""
    client = ClaudeClient(api_key="mock_key")

    eval_data = make_valid_evaluator_json()
    eval_data["evaluations"][0]["evidence"][0]["quote"] = "Completely made up phrase"

    def mock_api_call(system, prompt):
        if "compliance" in prompt.lower() or "policy" in prompt.lower():
            return json.dumps(make_clean_compliance_json()), 400, 50
        return json.dumps(eval_data), 600, 200

    with patch.object(client, "_execute_api_call", side_effect=mock_api_call):
        eval_record = run_evaluation_pipeline(call_id=101, db=test_db, client=client, bypass_cache=True)

    ev_item = [e for e in eval_record.evidence_items if e.criterion_id == "discovery"][0]
    assert ev_item.verification_status == "rejected_quote_not_found"


def test_integration_pipeline_wrong_role_evidence_unverified_flag(test_db):
    """Scenario (e): Claude cites student segment for a counsellor rule -> rejected_wrong_role, flag unverified."""
    client = ClaudeClient(api_key="mock_key")

    compliance_data = {
        "no_flags": False,
        "flags": [
            {
                "rule_id": "R-01",
                "severity": "critical",
                "confidence": 0.9,
                "explanation": "Student words mistakenly cited as counsellor breach",
                "evidence": [
                    {
                        "segment_id": "seg_002",  # seg_002 is student!
                        "quote": "Main JEE 2026 ke liye soch raha tha",
                    }
                ],
            }
        ],
    }

    def mock_api_call(system, prompt):
        if "compliance" in prompt.lower() or "policy" in prompt.lower():
            return json.dumps(compliance_data), 400, 50
        return json.dumps(make_valid_evaluator_json()), 600, 200

    with patch.object(client, "_execute_api_call", side_effect=mock_api_call):
        eval_record = run_evaluation_pipeline(call_id=101, db=test_db, client=client, bypass_cache=True)

    flag = eval_record.compliance_flags[0]
    assert flag.status == "unverified"
    # Evidence was rejected for wrong role
    flag_ev = [e for e in eval_record.evidence_items if e.flag_id == flag.id][0]
    assert flag_ev.verification_status == "rejected_wrong_role"
    # Unverified critical flag MUST NOT gate the call to needs_review!
    assert not eval_record.has_critical_flag
    assert test_db.get(Call, 101).status == "completed"


def test_integration_pipeline_unknown_rule_id_becomes_uncategorized(test_db):
    """Unknown rule ID not found in synthetic policy -> status is 'uncategorized'."""
    client = ClaudeClient(api_key="mock_key")

    compliance_data = {
        "no_flags": False,
        "flags": [
            {
                "rule_id": "R-999",  # Does not exist in demo policy
                "severity": "critical",
                "confidence": 0.9,
                "explanation": "Claim under non-existent rule",
                "evidence": [
                    {
                        "segment_id": "seg_001",
                        "quote": "Physics Wallah",
                    }
                ],
            }
        ],
    }

    def mock_api_call(system, prompt):
        if "compliance" in prompt.lower() or "policy" in prompt.lower():
            return json.dumps(compliance_data), 400, 50
        return json.dumps(make_valid_evaluator_json()), 600, 200

    with patch.object(client, "_execute_api_call", side_effect=mock_api_call):
        eval_record = run_evaluation_pipeline(call_id=101, db=test_db, client=client, bypass_cache=True)

    flag = eval_record.compliance_flags[0]
    assert flag.status == "uncategorized"
    assert not eval_record.has_critical_flag


def test_integration_pipeline_verification_repair_retry_succeeds(test_db):
    """Verification failure triggers repair retry with error details; Claude fixes quote and it passes."""
    client = ClaudeClient(api_key="mock_key")
    call_log = []

    def mock_api_call(system, prompt):
        if "compliance" in prompt.lower() or "policy" in prompt.lower():
            return json.dumps(make_clean_compliance_json()), 400, 50

        call_log.append(prompt)
        if len(call_log) == 1:
            # First attempt: returns hallucinated quote for discovery
            bad_data = make_valid_evaluator_json()
            bad_data["evaluations"][0]["evidence"][0]["quote"] = "Fabricated quote not in transcript"
            return json.dumps(bad_data), 600, 200
        else:
            # Second attempt: confirm error was passed into prompt, then return correct quote
            assert "[PREVIOUS ATTEMPT CORRECTIONS]" in prompt
            assert "rejected" in prompt.lower() or "quote" in prompt.lower()
            return json.dumps(make_valid_evaluator_json()), 600, 200

    with patch.object(client, "_execute_api_call", side_effect=mock_api_call):
        eval_record = run_evaluation_pipeline(call_id=101, db=test_db, client=client, bypass_cache=True)

    assert len(call_log) == 2
    # Verify the repaired evidence is now verified
    discovery_ev = [e for e in eval_record.evidence_items if e.criterion_id == "discovery"][0]
    assert discovery_ev.verification_status == "verified"


def test_integration_pipeline_verification_repair_still_fails_drops_evidence(test_db):
    """Verification failure triggers retry, but repair still returns invalid quote -> evidence remains rejected."""
    client = ClaudeClient(api_key="mock_key")
    call_log = []

    def mock_api_call(system, prompt):
        if "compliance" in prompt.lower() or "policy" in prompt.lower():
            return json.dumps(make_clean_compliance_json()), 400, 50

        call_log.append(prompt)
        # Both attempts return the invalid quote
        bad_data = make_valid_evaluator_json()
        bad_data["evaluations"][0]["evidence"][0]["quote"] = "Still wrong quote"
        return json.dumps(bad_data), 600, 200

    with patch.object(client, "_execute_api_call", side_effect=mock_api_call):
        eval_record = run_evaluation_pipeline(call_id=101, db=test_db, client=client, bypass_cache=True)

    assert len(call_log) == 2
    # Evidence remains rejected
    discovery_ev = [e for e in eval_record.evidence_items if e.criterion_id == "discovery"][0]
    assert discovery_ev.verification_status == "rejected_quote_not_found"

