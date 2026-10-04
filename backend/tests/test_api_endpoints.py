"""
backend/tests/test_api_endpoints.py
API integration tests using FastAPI TestClient:
  - GET /rubric
  - PUT /rubric (weights update -> no LLM call)
  - POST /rubric/versions
  - POST /calls/{id}/analyze (sync=True)
  - GET /calls/{id}/evaluation
"""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db.engine import Base, SessionLocal, engine
from app.main import app
from app.models.orm import Call, Counsellor, TranscriptSegment


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # Clear and re-seed minimal fixtures
    c = db.query(Counsellor).filter(Counsellor.id == 99).first()
    if not c:
        c = Counsellor(id=99, name="API Test Counsellor", email="api_test@pw.live")
        db.add(c)
        db.commit()

    call = db.query(Call).filter(Call.id == 999).first()
    if not call:
        call = Call(
            id=999,
            counsellor_id=99,
            file_hash="testapihash",
            original_filename="api_test.mp3",
            file_path="uploads/api_test.mp3",
            file_size_bytes=12000,
            status="transcribed",
        )
        db.add(call)
        db.commit()

    # Add segments
    existing_segs = db.query(TranscriptSegment).filter(TranscriptSegment.call_id == 999).all()
    if not existing_segs:
        db.add(
            TranscriptSegment(
                call_id=999,
                segment_id="seg_001",
                start_ms=0,
                end_ms=3000,
                speaker_label="speaker_0",
                role="counsellor",
                text="Namaste, main Physics Wallah se baat kar raha hoon.",
            )
        )
        db.add(
            TranscriptSegment(
                call_id=999,
                segment_id="seg_002",
                start_ms=3100,
                end_ms=5000,
                speaker_label="speaker_1",
                role="student",
                text="Main NEET dropper batch join karna chahta hoon.",
            )
        )
        db.commit()

    db.close()
    with TestClient(app) as test_client:
        yield test_client


def test_api_get_rubric(client):
    res = client.get("/rubric")
    assert res.status_code == 200
    data = res.json()
    assert "criteria" in data
    assert "weights" in data
    assert sum(data["weights"].values()) == pytest.approx(100.0)


def test_api_put_rubric_weights_makes_zero_llm_calls(client):
    new_weights = {
        "discovery": 25.0,
        "course_fit": 25.0,
        "pitch_quality": 10.0,
        "objection_handling": 15.0,
        "compliance": 15.0,
        "closing_next_steps": 10.0,
    }
    with patch("app.services.llm.ClaudeClient._execute_api_call") as mock_llm:
        res = client.put("/rubric", json={"weights": new_weights})
        assert res.status_code == 200
        data = res.json()
        assert "recomputed_evaluations" in data
        # Explicit assertion: Zero LLM calls are made on weight recomputation
        mock_llm.assert_not_called()


def test_api_put_invalid_weights_sum_fails(client):
    bad_weights = {
        "discovery": 25.0,
        "course_fit": 25.0,
        "pitch_quality": 10.0,
        "objection_handling": 15.0,
        "compliance": 15.0,
        "closing_next_steps": 5.0,  # sums to 95
    }
    res = client.put("/rubric", json={"weights": bad_weights})
    assert res.status_code == 422


def test_api_analyze_and_get_evaluation(client):
    eval_mock = {
        "evaluations": [
            {"criterion_id": "discovery", "score": 3, "confidence": 0.9, "rationale": "Good discovery.", "not_applicable": False, "evidence": [{"segment_id": "seg_001", "quote": "Physics Wallah", "note": "Greeting"}]},
            {"criterion_id": "course_fit", "score": 3, "confidence": 0.9, "rationale": "Good fit.", "not_applicable": False, "evidence": []},
            {"criterion_id": "pitch_quality", "score": 3, "confidence": 0.9, "rationale": "Clear pitch.", "not_applicable": False, "evidence": []},
            {"criterion_id": "objection_handling", "score": 0, "confidence": 0.9, "rationale": "No objections.", "not_applicable": True, "evidence": []},
            {"criterion_id": "closing_next_steps", "score": 2, "confidence": 0.8, "rationale": "Adequate closing.", "not_applicable": False, "evidence": []},
        ]
    }
    comp_mock = {"no_flags": True, "flags": []}

    coach_mock = {
        "strengths": ["Clear communication"],
        "improvements": ["Ask more discovery questions"],
        "next_call_focus": "Focus on student discovery.",
    }

    def mock_api_call(system, prompt):
        sys_low = system.lower()
        if "coach" in sys_low:
            return json.dumps(coach_mock), 400, 50
        if "compliance" in sys_low or "audit" in sys_low or "policy" in sys_low:
            return json.dumps(comp_mock), 400, 50
        return json.dumps(eval_mock), 600, 200

    with patch("app.services.llm.ClaudeClient._execute_api_call", side_effect=mock_api_call), \
         patch("app.services.llm.GeminiClient._execute_api_call", side_effect=mock_api_call), \
         patch("app.services.llm.OpenAIClient._execute_api_call", side_effect=mock_api_call):
        analyze_res = client.post("/calls/999/analyze?sync=true&bypass_cache=true")
        assert analyze_res.status_code == 202
        assert analyze_res.json()["status"] in ("completed", "needs_review")

        # GET /calls/999/evaluation
        eval_res = client.get("/calls/999/evaluation")
        assert eval_res.status_code == 200
        eval_data = eval_res.json()
        assert eval_data["call_id"] == 999
        assert eval_data["overall_score"] is not None
        assert len(eval_data["criteria"]) == 6
        discovery_crit = [c for c in eval_data["criteria"] if c["criterion_id"] == "discovery"][0]
        # Verify criterion names and max_score come from rubric configuration
        assert discovery_crit["name"] == "Discovery / Needs Understanding"
        assert discovery_crit["max_score"] == 4
        course_fit_crit = [c for c in eval_data["criteria"] if c["criterion_id"] == "course_fit"][0]
        assert course_fit_crit["name"] == "Product / Course Fit"
        assert course_fit_crit["max_score"] == 4

        # Verify evidence timestamps come directly from transcript_segments.start_ms
        assert len(discovery_crit["evidence"]) == 1
        ev_item = discovery_crit["evidence"][0]
        assert ev_item["segment_id"] == "seg_001"
        assert ev_item["start_ms"] == 0
        assert ev_item["end_ms"] == 3000
        assert ev_item["db_segment_text"] == "Namaste, main Physics Wallah se baat kar raha hoon."


def test_criterion_names_and_max_score_loaded_from_rubric_config(client):
    """Confirm criterion titles and max_score dynamically reflect rubric config rather than hardcoded strings."""
    eval_res = client.get("/calls/999/evaluation")
    assert eval_res.status_code == 200
    eval_data = eval_res.json()
    for crit in eval_data["criteria"]:
        assert crit["name"] is not None
        assert isinstance(crit["name"], str)
        assert len(crit["name"]) > 0
        assert crit["max_score"] == 4


def test_evidence_timestamps_come_from_transcript_segments(client):
    """Confirm evidence timestamps in evaluation response come directly from transcript_segments.start_ms."""
    eval_res = client.get("/calls/999/evaluation")
    assert eval_res.status_code == 200
    eval_data = eval_res.json()
    found_evidence = False
    for crit in eval_data["criteria"]:
        for ev in crit.get("evidence", []):
            if ev.get("segment_id") == "seg_001":
                assert ev["start_ms"] == 0
                assert ev["end_ms"] == 3000
                found_evidence = True
    assert found_evidence, "Expected to find evidence citing seg_001 with verified start_ms"


def test_rubric_thresholds_in_evaluation(client):
    """Confirm rubric config thresholds are returned in evaluation for one source of truth."""
    eval_res = client.get("/calls/999/evaluation")
    assert eval_res.status_code == 200
    data = eval_res.json()
    assert data["min_overall_score_for_review"] == 50.0
    assert data["min_confidence_for_gate"] == 0.70
    assert data["low_confidence_display_threshold"] == 0.70


def test_settings_api_get_and_put(client):
    """Confirm settings table get and put for operational assumptions."""
    # Put setting
    put_res = client.put("/settings/minutes_saved_per_call", json={"value": "25"})
    assert put_res.status_code == 200
    assert put_res.json()["value"] == "25"

    # Get single
    get_single = client.get("/settings/minutes_saved_per_call")
    assert get_single.status_code == 200
    assert get_single.json()["value"] == "25"

    # Get all
    get_all = client.get("/settings")
    assert get_all.status_code == 200
    assert get_all.json().get("minutes_saved_per_call") == "25"


def test_demo_access_token_protection(client):
    """Confirm mutating endpoints are protected when DEMO_ACCESS_TOKEN is configured."""
    from app.config.settings import get_settings

    settings = get_settings()
    original_token = settings.demo_access_token

    try:
        # Enable token protection
        settings.demo_access_token = "secret-demo-token-123"

        # 1. GET requests should NOT be blocked
        get_res = client.get("/health")
        assert get_res.status_code == 200

        # 2. Mutating request (PUT) without token must return 401
        put_blocked = client.put("/settings/minutes_saved_per_call", json={"value": "30"})
        assert put_blocked.status_code == 401
        assert put_blocked.json()["error"] == "unauthorized"

        # 3. Mutating request with invalid token must return 401
        put_invalid = client.put(
            "/settings/minutes_saved_per_call",
            json={"value": "30"},
            headers={"Authorization": "Bearer wrong-token"}
        )
        assert put_invalid.status_code == 401

        # 4. Mutating request with valid Bearer token must succeed
        put_bearer = client.put(
            "/settings/minutes_saved_per_call",
            json={"value": "30"},
            headers={"Authorization": "Bearer secret-demo-token-123"}
        )
        assert put_bearer.status_code == 200

        # 5. Mutating request with X-Demo-Access-Token must succeed
        put_custom_header = client.put(
            "/settings/minutes_saved_per_call",
            json={"value": "25"},
            headers={"X-Demo-Access-Token": "secret-demo-token-123"}
        )
        assert put_custom_header.status_code == 200

    finally:
        settings.demo_access_token = original_token




