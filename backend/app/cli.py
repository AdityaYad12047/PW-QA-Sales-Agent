"""
backend/app/cli.py
Command-line interface for running the PW Counselling QA pipeline directly on audio files.
Usage:
    py -3.13 -m app.cli evaluate <audio_path> [--counsellor-id 1]
    py -3.13 -m app.cli smoke [--calls-dir eval/data/calls] [--limit 1]
"""
import argparse
import hashlib
import os
import shutil
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app.config.settings import get_settings
from app.db.engine import Base, SessionLocal, engine
from app.models.orm import Call, Counsellor, Evaluation, LlmRun
from app.pipeline.evaluation_runner import run_evaluation_pipeline
from app.pipeline.runner import run_transcription_pipeline


def _compute_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def evaluate_audio(audio_path: str, counsellor_id: int = 1, bypass_cache: bool = False):
    path = Path(audio_path)
    if not path.exists():
        print(f"[ERROR] File not found: {audio_path}", file=sys.stderr)
        sys.exit(1)

    settings = get_settings()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        # 1. Ensure counsellor exists
        counsellor = db.get(Counsellor, counsellor_id)
        if not counsellor:
            counsellor = Counsellor(
                id=counsellor_id,
                name=f"Counsellor {counsellor_id}",
                email=f"counsellor_{counsellor_id}@pw.live",
            )
            db.add(counsellor)
            db.commit()
            db.refresh(counsellor)
            print(f"[OK] Created initial counsellor: {counsellor.name} (id={counsellor.id})")

        # 2. Check/create call record
        file_hash = _compute_hash(path)
        call = db.query(Call).filter(Call.file_hash == file_hash).first()

        uploads_dir = Path("uploads").resolve()
        uploads_dir.mkdir(exist_ok=True)
        dest_path = uploads_dir / f"{file_hash[:16]}{path.suffix}"
        if not dest_path.exists():
            shutil.copyfile(path, dest_path)

        if not call:
            call = Call(
                counsellor_id=counsellor.id,
                file_hash=file_hash,
                original_filename=path.name,
                file_path=str(dest_path),
                file_size_bytes=path.stat().st_size,
                status="uploaded",
            )
            db.add(call)
            db.commit()
            db.refresh(call)
            print(f"[OK] Created Call record #{call.id} for {path.name}")
        else:
            print(f"[INFO] Using existing Call record #{call.id} for {path.name}")
            if not Path(call.file_path).exists() and dest_path.exists():
                call.file_path = str(dest_path)
                db.commit()

        # 3. Transcribe if needed
        if call.status in ("uploaded", "transcribing", "failed"):
            print(f"[*] Running transcription via Sarvam STT on {path.name}...")
            t0 = time.time()
            run_transcription_pipeline(call.id, db)
            db.refresh(call)
            print(f"[OK] Transcription completed in {time.time() - t0:.2f}s (status={call.status})")

        if call.status == "failed":
            print(f"[ERROR] Transcription failed: {call.failure_reason}", file=sys.stderr)
            sys.exit(1)

        # 4. Evaluate with LLM
        from app.services.llm import get_llm_client
        client = get_llm_client()
        print(f"[*] Running evaluation pipeline with model {client.model} ({client.__class__.__name__})...")
        t0 = time.time()
        eval_record = run_evaluation_pipeline(call_id=call.id, db=db, client=client, bypass_cache=bypass_cache)
        db.refresh(call)
        print(f"[OK] Evaluation finished in {time.time() - t0:.2f}s")

        # 5. Output Summary
        print("\n" + "=" * 60)
        print(f"  EVALUATION RESULT (Call #{call.id}: {call.original_filename})")
        print("=" * 60)
        print(f"  Overall Score  : {eval_record.overall_score:.1f}/100" if eval_record.overall_score is not None else "  Overall Score  : N/A")
        print(f"  Score Status   : {call.status.upper()}")
        print(f"  Needs Review   : {call.status == 'needs_review' or eval_record.has_critical_flag}")
        print(f"  Criteria Count : {len(eval_record.criteria_scores)}")
        print(f"  Flags Count    : {len(eval_record.compliance_flags)}")

        if eval_record.coaching:
            c = eval_record.coaching.content_json
            print("\n  Coaching & Action Plan:")
            if c.get("strengths"):
                print("    [Strengths]")
                for s in c["strengths"]:
                    print(f"      + {s}")
            if c.get("improvements"):
                print("    [Coaching Opportunities]")
                for imp in c["improvements"]:
                    print(f"      ! {imp}")
            if c.get("next_call_focus"):
                print(f"    [Next Call Focus]\n      -> {c['next_call_focus']}")
        
        runs = db.query(LlmRun).filter(LlmRun.call_id == call.id).all()
        print(f"\n  LLM Operations ({len(runs)} total):")
        for r in runs:
            print(f"    - [{r.call_type}] {r.model}: {r.input_tokens} in / {r.output_tokens} out | Latency: {(r.latency_ms or 0)/1000:.2f}s | Cache: {r.from_cache}")
        print("=" * 60 + "\n")

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="PW Counselling QA CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Evaluate subcommand
    eval_parser = subparsers.add_parser("evaluate", help="Evaluate a single audio recording")
    eval_parser.add_argument("audio_path", type=str, help="Path to audio file (.mp3/.wav/.m4a)")
    eval_parser.add_argument("--counsellor-id", type=int, default=1, help="Counsellor ID")
    eval_parser.add_argument("--bypass-cache", action="store_true", help="Bypass STT and LLM caches")

    # Smoke subcommand
    smoke_parser = subparsers.add_parser("smoke", help="Run smoke test against eval dataset")
    smoke_parser.add_argument("--calls-dir", type=str, default="eval/data/calls", help="Directory with calls")
    smoke_parser.add_argument("--limit", type=int, default=1, help="Number of files to process")
    smoke_parser.add_argument("--bypass-cache", action="store_true", help="Bypass cache")

    args = parser.parse_args()

    if args.command == "evaluate":
        evaluate_audio(args.audio_path, counsellor_id=args.counsellor_id, bypass_cache=args.bypass_cache)
    elif args.command == "smoke":
        from scripts.smoke_real import run_smoke_test
        run_smoke_test(Path(args.calls_dir), limit=args.limit, bypass_cache=args.bypass_cache)


if __name__ == "__main__":
    main()
