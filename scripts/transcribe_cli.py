"""
scripts/transcribe_cli.py
Quick CLI for testing transcription without the UI.
Usage:
    python scripts/transcribe_cli.py path/to/audio.mp3 [--counsellor-id 1]

Prints the transcript with segment IDs, timestamps and roles to stdout.
Useful for the Phase 1 checkpoint: run this on eval/data/calls/*.mp3 and
visually inspect speaker separation and Hindi/Hinglish handling.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.config.settings import get_settings  # noqa: E402
from app.pipeline.roles import map_roles  # noqa: E402
from app.pipeline.segments import format_segment_for_prompt, normalise_segments  # noqa: E402
from app.services.stt import get_stt_provider  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Transcribe an audio file and print the result.")
    parser.add_argument("audio", type=Path, help="Path to the audio file")
    parser.add_argument(
        "--merge-threshold-ms", type=int, default=1500,
        help="Silence threshold for merging adjacent same-speaker segments (ms)"
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Bypass the cache (re-runs the STT API call)"
    )
    args = parser.parse_args()

    audio_path: Path = args.audio.resolve()
    if not audio_path.exists():
        print(f"ERROR: File not found: {audio_path}", file=sys.stderr)
        sys.exit(1)

    settings = get_settings()
    print(f"Audio file : {audio_path}")
    print(f"File size  : {audio_path.stat().st_size / 1e6:.2f} MB")
    print(f"Cache dir  : {settings.cache_path / 'stt'}")
    print()

    # ── Run STT ───────────────────────────────────────────────────────────────
    if args.no_cache:
        # Invalidate cache by temporarily removing the cache file
        from app.services.stt import _file_sha256, _cache_path
        file_hash = _file_sha256(audio_path)
        cache_file = _cache_path(settings.cache_path / "stt", file_hash, "sarvam")
        if cache_file.exists():
            cache_file.unlink()
            print("[Cache cleared for this file]")

    print("Running STT... (this may take a few minutes for long files)")
    provider = get_stt_provider()
    result = provider.transcribe(audio_path)

    if not result.success:
        print(f"\nSTT FAILED", file=sys.stderr)
        print(f"  reason : {result.failure_reason}", file=sys.stderr)
        print(f"  detail : {result.failure_detail}", file=sys.stderr)
        sys.exit(1)

    print(f"STT complete (from_cache={result.from_cache})")
    print(f"  provider         : {result.provider} / {result.model}")
    print(f"  duration         : {result.audio_duration_seconds:.1f}s" if result.audio_duration_seconds else "  duration: unknown")
    print(f"  latency          : {result.latency_ms}ms" if result.latency_ms else "")
    print(f"  estimated cost   : ₹{result.estimated_cost_inr:.4f}" if result.estimated_cost_inr else "")
    print(f"  raw entries      : {len(result.entries)}")
    print()

    # ── Normalise ─────────────────────────────────────────────────────────────
    segments = normalise_segments(result.entries, merge_threshold_ms=args.merge_threshold_ms)
    role_result = map_roles(segments)

    if role_result.warning:
        print(f"[WARNING] {role_result.warning}")
        print()

    print(f"Speaker → Role mapping:")
    for speaker, role in role_result.speaker_to_role.items():
        conf = role_result.speaker_confidence.get(speaker, 0)
        print(f"  {speaker} → {role} (confidence: {conf:.2f})")
    print()

    # ── Print transcript ───────────────────────────────────────────────────────
    total_words = sum(len(s.text.split()) for s in segments)
    print(f"{'─'*70}")
    print(f"TRANSCRIPT  ({len(segments)} segments, {total_words} words)")
    print(f"{'─'*70}")
    for seg in segments:
        print(format_segment_for_prompt(seg))
    print(f"{'─'*70}")


if __name__ == "__main__":
    main()
