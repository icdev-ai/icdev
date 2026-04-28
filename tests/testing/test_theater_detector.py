# CUI // SP-CTI
"""Unit tests for tools/testing/theater_detector.py — 10 tests.

Coverage:
  8 anti-pattern tests (one fixture per anti-pattern)
  1 clean-file test  (passed=True, antipatterns_found=[])
  1 severity-aggregation test (>=3 anti-patterns → block)

Tests use tmp_path only; no filesystem access beyond the fixture.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from tools.testing.theater_detector import TheaterDetector  # noqa: E402


# ── 1. tautological_assertion ─────────────────────────────────────────────────

def test_tautological_assertion(tmp_path: pathlib.Path) -> None:
    (tmp_path / "t.py").write_text(
        "def test_always_true():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    result = TheaterDetector().detect(tmp_path)
    assert "tautological_assertion" in result.antipatterns_found


# ── 2. mock_dominated ─────────────────────────────────────────────────────────

def test_mock_dominated(tmp_path: pathlib.Path) -> None:
    # 6 MagicMock lines + 1 assertion → 6/7 ≈ 86% mock setup, triggers rule
    (tmp_path / "t.py").write_text(
        "from unittest.mock import MagicMock\n"
        "\n"
        "def test_heavy_mock():\n"
        "    mock_a = MagicMock()\n"
        "    mock_b = MagicMock()\n"
        "    mock_c = MagicMock()\n"
        "    mock_d = MagicMock()\n"
        "    mock_e = MagicMock()\n"
        "    mock_f = MagicMock()\n"
        "    assert mock_a is not None\n",
        encoding="utf-8",
    )
    result = TheaterDetector().detect(tmp_path)
    assert "mock_dominated" in result.antipatterns_found


# ── 3. fixture_theater ────────────────────────────────────────────────────────

def test_fixture_theater(tmp_path: pathlib.Path) -> None:
    (tmp_path / "t.py").write_text(
        "import pytest\n"
        "import requests\n"
        "\n"
        "@pytest.fixture\n"
        "def network_fixture():\n"
        "    r = requests.get('http://example.com')\n"
        "    yield r\n"
        "\n"
        "def test_uses_fixture(network_fixture):\n"
        "    assert network_fixture is not None\n",
        encoding="utf-8",
    )
    result = TheaterDetector().detect(tmp_path)
    assert "fixture_theater" in result.antipatterns_found


# ── 4. assertion_free ─────────────────────────────────────────────────────────

def test_assertion_free(tmp_path: pathlib.Path) -> None:
    (tmp_path / "t.py").write_text(
        "def test_no_assertions():\n"
        "    x = 1 + 1\n"
        "    y = x * 2\n",
        encoding="utf-8",
    )
    result = TheaterDetector().detect(tmp_path)
    assert "assertion_free" in result.antipatterns_found


# ── 5. hardcoded_oracle ───────────────────────────────────────────────────────

def test_hardcoded_oracle(tmp_path: pathlib.Path) -> None:
    # String literal longer than 3 chars in an assertion with no nearby comment
    (tmp_path / "t.py").write_text(
        "def test_magic_literal():\n"
        "    result = compute()\n"
        "    assert result == 'expected_value'\n",
        encoding="utf-8",
    )
    result = TheaterDetector().detect(tmp_path)
    assert "hardcoded_oracle" in result.antipatterns_found


# ── 6. smoke_masquerade ───────────────────────────────────────────────────────

def test_smoke_masquerade(tmp_path: pathlib.Path) -> None:
    # test_unit_* function whose body only references top-level import names
    (tmp_path / "t.py").write_text(
        "import os\n"
        "import sys\n"
        "\n"
        "def test_unit_only_imports():\n"
        "    os.getcwd()\n"
        "    sys.path\n",
        encoding="utf-8",
    )
    result = TheaterDetector().detect(tmp_path)
    assert "smoke_masquerade" in result.antipatterns_found


# ── 7. always_green ───────────────────────────────────────────────────────────

def test_always_green(tmp_path: pathlib.Path) -> None:
    (tmp_path / "t.py").write_text(
        "def test_swallows_assertion_error():\n"
        "    try:\n"
        "        assert False\n"
        "    except AssertionError:\n"
        "        pass\n",
        encoding="utf-8",
    )
    result = TheaterDetector().detect(tmp_path)
    assert "always_green" in result.antipatterns_found


# ── 8. spec_drift ─────────────────────────────────────────────────────────────

def test_spec_drift(tmp_path: pathlib.Path) -> None:
    # Feature file with steps that have no matching step definitions
    (tmp_path / "example.feature").write_text(
        "Feature: Login\n"
        "  Scenario: Valid user\n"
        "    Given the user has valid credentials\n"
        "    When the user submits the login form\n"
        "    Then the dashboard is displayed\n",
        encoding="utf-8",
    )
    (tmp_path / "steps.py").write_text(
        "# no step definitions here\n"
        "def helper():\n"
        "    pass\n",
        encoding="utf-8",
    )
    result = TheaterDetector().detect(tmp_path)
    assert "spec_drift" in result.antipatterns_found


# ── 9. clean file ─────────────────────────────────────────────────────────────

def test_clean_file(tmp_path: pathlib.Path) -> None:
    # 2 is in _BORING_INTS so not flagged; comment satisfies hardcoded_oracle anyway
    (tmp_path / "t.py").write_text(
        "def test_addition():\n"
        "    result = 1 + 1\n"
        "    # adding two ones gives two\n"
        "    assert result == 2\n",
        encoding="utf-8",
    )
    result = TheaterDetector().detect(tmp_path)
    assert result.passed is True
    assert result.antipatterns_found == []


# ── 10. severity aggregation ──────────────────────────────────────────────────

def test_severity_aggregation_block(tmp_path: pathlib.Path) -> None:
    """Three distinct anti-patterns in one directory → severity='block'."""
    (tmp_path / "t.py").write_text(
        # tautological_assertion
        "def test_tautology():\n"
        "    assert True\n"
        "\n"
        # assertion_free
        "def test_empty():\n"
        "    x = 1\n"
        "\n"
        # always_green
        "def test_swallows():\n"
        "    try:\n"
        "        do_something()\n"
        "    except AssertionError:\n"
        "        pass\n",
        encoding="utf-8",
    )
    result = TheaterDetector().detect(tmp_path)
    assert result.severity == "block"
    assert len(result.antipatterns_found) >= 3
