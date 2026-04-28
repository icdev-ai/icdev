"""pytest unit tests for tools/quality/rigor_gates.py — 6 tests minimum."""
from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

# Ensure project root is importable (conftest.py does this globally, but
# guard here so tests/quality can also be run in isolation).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.quality.rigor_gates import EvaluationResult, ILTierProfile, evaluate, load_profile

_SCRIPT = _PROJECT_ROOT / "tools" / "quality" / "rigor_gates.py"


def _write_coverage_xml(path: Path, line_rate: float) -> None:
    """Write a minimal coverage.xml with the given line-rate (0.0–1.0)."""
    root = ET.Element("coverage", {"line-rate": str(line_rate), "version": "7.0"})
    ET.SubElement(root, "packages")
    ET.ElementTree(root).write(str(path), encoding="utf-8", xml_declaration=True)


# ---------------------------------------------------------------------------
# Test 1 — load_profile('il4') returns correct ILTierProfile thresholds
# ---------------------------------------------------------------------------
def test_load_profile_il4_returns_correct_thresholds():
    profile = load_profile("il4")

    assert isinstance(profile, ILTierProfile)
    assert profile.tier == "il4"
    assert profile.coverage_minimum == 80
    assert profile.sast_required is True


# ---------------------------------------------------------------------------
# Test 2 — load_profile('invalid') raises ValueError
# ---------------------------------------------------------------------------
def test_load_profile_invalid_raises_valueerror():
    with pytest.raises(ValueError, match="Unknown IL tier"):
        load_profile("invalid_tier")


def test_load_profile_invalid_tier_raises_value_error():
    with pytest.raises(ValueError, match="Unknown IL tier"):
        load_profile("invalid")


# ---------------------------------------------------------------------------
# Test 3 — evaluate() coverage=85 against il4 → passed=True
# ---------------------------------------------------------------------------
def test_evaluate_coverage_85_against_il4_passes():
    result = evaluate("il4", coverage_pct=85.0)

    assert isinstance(result, EvaluationResult)
    assert result.passed is True
    assert result.violations == []
    assert result.coverage_actual == pytest.approx(85.0)


# ---------------------------------------------------------------------------
# Test 4 — evaluate() coverage=70 against il5 → passed=False, violation listed
# ---------------------------------------------------------------------------
def test_evaluate_coverage_70_against_il5_fails_with_violation():
    result = evaluate("il5", coverage_pct=70.0)

    assert result.passed is False
    assert len(result.violations) >= 1
    # Violation message must mention coverage
    assert any("coverage" in v.lower() for v in result.violations)
    # Violation message should reference the expected minimum
    assert any(str(result.coverage_minimum) in v for v in result.violations)


# ---------------------------------------------------------------------------
# Test 5 — evaluate() degrades gracefully when no .coverage file present
# ---------------------------------------------------------------------------
def test_evaluate_graceful_when_coverage_absent(tmp_path):
    # tmp_path is empty — no coverage.xml of any kind
    result = evaluate("il4", project_dir=tmp_path)

    # Should not raise; coverage_actual is None
    assert result.coverage_actual is None
    # Missing coverage data is NOT a violation (graceful degrade)
    assert result.passed is True
    assert result.violations == []


# ---------------------------------------------------------------------------
# Test 6 — CLI --json output is valid JSON with 'passed' key
# ---------------------------------------------------------------------------
def test_cli_json_output_has_passed_key(tmp_path):
    # Write a coverage.xml at 90% — above il4 threshold of 80%
    _write_coverage_xml(tmp_path / "coverage.xml", 0.90)

    proc = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--tier", "il4",
            "--project-dir", str(tmp_path),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, f"CLI failed: {proc.stderr}"
    data = json.loads(proc.stdout)
    assert "passed" in data
    assert data["passed"] is True
