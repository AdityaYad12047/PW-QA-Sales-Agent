"""
backend/tests/test_smoke_cli.py
Tests asserting that scripts/smoke_real.py exits with a clear error
when required API keys are missing.
"""
import os
import subprocess
import sys
from pathlib import Path
import pytest


def test_smoke_real_exits_when_anthropic_key_missing(tmp_path):
    repo_root = Path(__file__).resolve().parent.parent.parent
    script_path = repo_root / "scripts" / "smoke_real.py"

    # Create dummy environment with empty keys
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = ""
    env["SARVAM_API_KEY"] = "dummy_sarvam"
    env["CLAUDE_MODEL"] = "claude-3-5-sonnet-20241022"

    proc = subprocess.run(
        [sys.executable, str(script_path), "--calls-dir", str(tmp_path)],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode != 0
    assert "ANTHROPIC_API_KEY is not set" in (proc.stderr + proc.stdout)


def test_smoke_real_exits_when_sarvam_key_missing(tmp_path):
    repo_root = Path(__file__).resolve().parent.parent.parent
    script_path = repo_root / "scripts" / "smoke_real.py"

    # Create dummy environment with empty sarvam key
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = "dummy_anthropic"
    env["SARVAM_API_KEY"] = ""
    env["CLAUDE_MODEL"] = "claude-3-5-sonnet-20241022"

    proc = subprocess.run(
        [sys.executable, str(script_path), "--calls-dir", str(tmp_path)],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode != 0
    assert "SARVAM_API_KEY is not set" in (proc.stderr + proc.stdout)
