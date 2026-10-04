"""
scripts/smoke_real.py
Runs the REAL QA pipeline on audio calls in eval/data/calls/ using Sarvam STT and Claude LLM.
Requires real API keys: ANTHROPIC_API_KEY and SARVAM_API_KEY must be set in the environment or .env.
Exits with a clear error if either key is missing. No simulation fallback.

Saves raw LLM JSON outputs to eval/runs/smoke/<call_stem>/ and prints:
  - Provider-reported token usage (Input, Output, Total)
  - Wall-clock STT and LLM latency
  - Response cache-hit status (STT and LLM)
  - Overall score, gate status, and per-criterion rationales
  - Verified compliance flags and verified quotes
  - Full verification audit log (rejected items with reasons)
  - Aggregate summary of rejection rates, retry rates, cost, and latency
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config.settings import get_settings
from app.db.engine import Base, SessionLocal, engine
from app.models.orm import (
    Call,
    ComplianceFlag,
    Counsellor,
    Evaluation,
    EvaluationCriterion,
    EvaluationEvidence,
    LlmRun,
    SttRun,
    TranscriptSegment,
)
from app.pipeline.evaluation_runner import run_evaluation_pipeline
from app.pipeline.roles import map_roles
from app.pipeline.segments import normalise_segments
from app.services.llm import ClaudeClient
from app.services.stt import SarvamSTT

logging.basicConfig(level=logging.WARNING)


def _compute_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_smoke_test(
    calls_dir: Path,
    limit: int = 3,
    api_key: Optional[str] = None,
    bypass_cache: bool = False,
):
    settings = get_settings()
    anthropic_key = api_key or os.environ.get("ANTHROPIC_API_KEY") or settings.anthropic_api_key
    sarvam_key = os.environ.get("SARVAM_API_KEY") or settings.sarvam_api_key

    # ── Strict validation: No simulation fallback allowed ─────────────────────
    if not anthropic_key:
        print(
            "\n[ERROR] ANTHROPIC_API_KEY is not set. Please set it in your environment or .env file.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not sarvam_key:
        print(
            "\n[ERROR] SARVAM_API_KEY is not set. Please set it in your environment or .env file.",
            file=sys.stderr,
        )
        sys.exit(1)

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # Ensure demo counsellor exists
    counsellor = db.query(Counsellor).first()
    if not counsellor:
        counsellor = Counsellor(name="Demo Counsellor Amit", email="counsellor_amit@pw.live")
        db.add(counsellor)
        db.commit()
        db.refresh(counsellor)

    call_files = sorted(list(calls_dir.glob("*.mp3")) + list(calls_dir.glob("*.wav")))
    if not call_files:
        print(f"\n[ERROR] No audio files (.mp3/.wav) found in directory: {calls_dir}")
        sys.exit(1)

    call_files = call_files[:limit]
    print("=" * 80)
    print("  PW COUNSELLING QA — REAL DATA SMOKE TEST")
    print(f"  Target directory   : {calls_dir}")
    print(f"  Files selected     : {len(call_files)}")
    print(f"  Model              : {settings.claude_model}")
    print(f"  Price version      : {settings.llm_price_version} (${settings.llm_cost_per_million_input_usd}/M in, ${settings.llm_cost_per_million_output_usd}/M out)")
    print("  Live APIs          : Anthropic & Sarvam configured (No simulation)")
    print("=" * 80)

    # Initialize live client instances
    stt_provider = SarvamSTT(api_key=sarvam_key)
    llm_client = ClaudeClient(api_key=anthropic_key)

    # Metrics trackers
    total_calls = 0
    total_latency_s = 0.0
    total_cost_usd = 0.0
    total_evidence_count = 0
    rejected_evidence_count = 0
    calls_retried_count = 0
    calls_gated_count = 0

    base_runs_dir = Path("eval/runs/smoke")
    base_runs_dir.mkdir(parents=True, exist_ok=True)

    for idx, audio_file in enumerate(call_files, 1):
        print(f"\n[{idx}/{len(call_files)}] Processing Call: {audio_file.name}...")
        file_hash = _compute_hash(audio_file)
        file_size = audio_file.stat().st_size

        # 1. DB Call entry
        db_call = db.query(Call).filter(Call.file_hash == file_hash).first()
        if not db_call:
            db_call = Call(
                counsellor_id=counsellor.id,
                file_hash=file_hash,
                original_filename=audio_file.name,
                file_path=str(audio_file),
                file_size_bytes=file_size,
                status="uploaded",
            )
            db.add(db_call)
            db.commit()
            db.refresh(db_call)

        t_pipeline_start = time.perf_counter()

        # 2. STT Stage (Live Sarvam STT with content-hash cache)
        t_stt_start = time.perf_counter()
        stt_res = stt_provider.transcribe(audio_file)
        wall_clock_stt_s = round(time.perf_counter() - t_stt_start, 3)

        if not stt_res.success:
            print(f"  [FAILED] STT failed: {stt_res.failure_reason} ({stt_res.failure_detail})")
            continue

        db_call.duration_seconds = stt_res.audio_duration_seconds
        db_call.t_stt_s = wall_clock_stt_s

        # 3. Segments & Role Mapping Stage
        norm_segs = normalise_segments(stt_res.entries)
        decision = map_roles(norm_segs)

        # Clear existing segments for clean re-run
        db.query(TranscriptSegment).filter(TranscriptSegment.call_id == db_call.id).delete()
        for seg in norm_segs:
            db.add(
                TranscriptSegment(
                    call_id=db_call.id,
                    segment_id=seg.segment_id,
                    start_ms=seg.start_ms,
                    end_ms=seg.end_ms,
                    speaker_label=seg.speaker_label,
                    role=seg.role,
                    role_confidence=seg.role_confidence,
                    role_source=seg.role_source,
                    text=seg.text,
                )
            )
        db_call.status = "transcribed"
        db.commit()

        # 4. LLM Analysis Stage (Live Claude Calls + Verification + Scoring)
        t_llm_start = time.perf_counter()
        eval_record = run_evaluation_pipeline(
            call_id=db_call.id,
            db=db,
            client=llm_client,
            bypass_cache=bypass_cache,
        )
        wall_clock_llm_s = round(time.perf_counter() - t_llm_start, 3)
        total_time_s = round(time.perf_counter() - t_pipeline_start, 3)

        db.refresh(db_call)
        db.refresh(eval_record)

        # Gather LLM runs data
        llm_runs = db.query(LlmRun).filter(LlmRun.call_id == db_call.id).all()
        in_tokens = sum(r.input_tokens or 0 for r in llm_runs)
        out_tokens = sum(r.output_tokens or 0 for r in llm_runs)
        total_tokens = in_tokens + out_tokens
        call_cost_usd = sum(r.estimated_cost_usd or 0.0 for r in llm_runs)
        was_retried = any(r.attempt_number > 1 or "retry" in r.call_type for r in llm_runs)

        # Check cache hits per stage
        eval_run = next((r for r in llm_runs if r.call_type == "evaluator"), None)
        comp_run = next((r for r in llm_runs if r.call_type == "compliance"), None)

        eval_cached = eval_run.from_cache if eval_run else False
        comp_cached = comp_run.from_cache if comp_run else False
        stt_cached = getattr(stt_res, "from_cache", False)

        # Gather evidence & rejections
        evidence_items = (
            db.query(EvaluationEvidence)
            .filter(EvaluationEvidence.evaluation_id == eval_record.id)
            .all()
        )
        rejected_items = [e for e in evidence_items if e.verification_status != "verified"]

        # Save raw LLM JSON outputs to eval/runs/smoke/<call_stem>/
        call_run_dir = base_runs_dir / audio_file.stem
        call_run_dir.mkdir(parents=True, exist_ok=True)

        raw_eval = getattr(eval_record, "_raw_evaluator", None)
        if raw_eval:
            (call_run_dir / "evaluator_raw.json").write_text(
                json.dumps(raw_eval, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        raw_comp = getattr(eval_record, "_raw_compliance", None)
        if raw_comp:
            (call_run_dir / "compliance_raw.json").write_text(
                json.dumps(raw_comp, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        summary_payload = {
            "call_id": db_call.id,
            "filename": audio_file.name,
            "status": db_call.status,
            "overall_score": eval_record.overall_score,
            "has_critical_flag": eval_record.has_critical_flag,
            "wall_clock_stt_seconds": wall_clock_stt_s,
            "wall_clock_llm_seconds": wall_clock_llm_s,
            "total_wall_clock_seconds": total_time_s,
            "provider_tokens": {
                "input_tokens": in_tokens,
                "output_tokens": out_tokens,
                "total_tokens": total_tokens,
            },
            "estimated_cost_usd": call_cost_usd,
            "cache_status": {
                "stt_from_cache": stt_cached,
                "evaluator_from_cache": eval_cached,
                "compliance_from_cache": comp_cached,
            },
        }
        (call_run_dir / "evaluation_summary.json").write_text(
            json.dumps(summary_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Track aggregates
        total_calls += 1
        total_latency_s += total_time_s
        total_cost_usd += call_cost_usd
        total_evidence_count += len(evidence_items)
        rejected_evidence_count += len(rejected_items)
        if was_retried:
            calls_retried_count += 1
        if eval_record.has_critical_flag or db_call.status == "needs_review":
            calls_gated_count += 1

        # ── Print Call Details ───────────────────────────────────────────────
        counsellor_spk = [spk for spk, r in decision.speaker_to_role.items() if r == "counsellor"]
        c_str = counsellor_spk[0] if counsellor_spk else "none"
        conf = decision.speaker_confidence.get(c_str, 0.0)

        print("-" * 80)
        print(f"CALL SUMMARY: {audio_file.name}")
        print(f"  Status             : {db_call.status.upper()}")
        print(f"  Overall Score      : {eval_record.overall_score}/100")
        print(f"  Critical Flag Gate : {'TRIGGERED' if eval_record.has_critical_flag else 'None'}")
        print(f"  Wall-Clock Latency : STT: {wall_clock_stt_s}s | LLM: {wall_clock_llm_s}s | Total: {total_time_s}s")
        print(f"  Token Usage        : Input: {in_tokens:,} | Output: {out_tokens:,} | Total: {total_tokens:,}")
        print(f"  Estimated LLM Cost : ${call_cost_usd:.4f} USD (Price Version: {settings.llm_price_version})")
        print(f"  Cache Status       : STT: {'HIT' if stt_cached else 'MISS'} | Evaluator: {'HIT' if eval_cached else 'MISS'} | Compliance: {'HIT' if comp_cached else 'MISS'}")
        print(f"  Diarization Role   : Counsellor={c_str}, Conf={conf:.2f}")
        print(f"  Raw LLM JSON Saved : {call_run_dir.resolve()}")

        print("\n  [PER-CRITERION SCORES]")
        for cs in eval_record.criteria_scores:
            na_str = " (N/A - Weight Redistributed)" if cs.not_applicable else ""
            print(f"    - {cs.criterion_id:<22} : {cs.score}/4  [Weight: {cs.weight_used}%]{na_str}")
            print(f"      Rationale: {cs.rationale}")

        print("\n  [COMPLIANCE FLAGS & EVIDENCE]")
        if not eval_record.compliance_flags:
            print("    No policy violations detected. (Clean Call)")
        else:
            for flag in eval_record.compliance_flags:
                flag_ev = [e for e in evidence_items if e.flag_id == flag.id]
                status_icon = "VERIFIED" if flag.status == "verified" else "UNVERIFIED"
                print(f"    - [{flag.rule_id}] Severity: {flag.severity.upper()} | Status: {status_icon} | Conf: {flag.confidence:.2f}")
                print(f"      Explanation: {flag.explanation}")
                for ev in flag_ev:
                    print(f"      Quote [{ev.segment_id}]: \"{ev.quote}\" -> {ev.verification_status}")

        print("\n  [VERIFICATION AUDIT LOG]")
        if not rejected_items:
            print("    All cited evidence quotes verified successfully against database text.")
        else:
            for rej in rejected_items:
                print(f"    - REJECTED [{rej.segment_id}]: Quote: \"{rej.quote}\"")
                print(f"      Reason: {rej.verification_status} — {rej.verification_detail}")
        print("-" * 80)

    # ── Aggregate Summary ────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  SMOKE TEST AGGREGATE EVALUATION SUMMARY")
    print("=" * 80)
    rejection_rate = (rejected_evidence_count / total_evidence_count * 100) if total_evidence_count else 0.0
    retry_rate = (calls_retried_count / total_calls * 100) if total_calls else 0.0
    gated_rate = (calls_gated_count / total_calls * 100) if total_calls else 0.0
    avg_cost = total_cost_usd / total_calls if total_calls else 0.0
    avg_latency = total_latency_s / total_calls if total_calls else 0.0

    print(f"  Total Calls Processed       : {total_calls}")
    print(f"  Verification Rejection Rate : {rejection_rate:.1f}% ({rejected_evidence_count}/{total_evidence_count} evidence quotes)")
    print(f"  Repair Retry Rate           : {retry_rate:.1f}% ({calls_retried_count}/{total_calls} calls)")
    print(f"  Needs-Review Gated Rate     : {gated_rate:.1f}% ({calls_gated_count}/{total_calls} calls)")
    print(f"  Average Cost per Call       : ${avg_cost:.4f} USD")
    print(f"  Average Pipeline Latency    : {avg_latency:.2f}s")
    print("=" * 80)

    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run real-data smoke test on QA pipeline.")
    parser.add_argument("--calls-dir", type=Path, default=Path("eval/data/calls"))
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--api-key", type=str, default=None)
    parser.add_argument("--bypass-cache", action="store_true")
    args = parser.parse_args()

    run_smoke_test(
        calls_dir=args.calls_dir,
        limit=args.limit,
        api_key=args.api_key,
        bypass_cache=args.bypass_cache,
    )
