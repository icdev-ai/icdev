"""Tests for the readiness check reporter."""
import subprocess
import sys
from pathlib import Path


def test_script_runs_without_error(tmp_path):
    script = Path(__file__).parent / "step2_starter.py"
    result = subprocess.run(
        [sys.executable, str(script), str(Path(__file__).parents[6])],
        capture_output=True,
        text=True,
    )
    assert "AGENT READINESS REPORT" in result.stdout


def test_exit_code_reflects_threshold(tmp_path):
    script = Path(__file__).parent / "step2_starter.py"
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    # An empty directory should score very low → exit 1
    assert result.returncode == 1


def test_output_shows_pillar_status(tmp_path):
    script = Path(__file__).parent / "step2_starter.py"
    result = subprocess.run(
        [sys.executable, str(script), str(Path(__file__).parents[6])],
        capture_output=True,
        text=True,
    )
    assert "PASS" in result.stdout or "FAIL" in result.stdout
