#!/usr/bin/env python3
# CUI // SP-CTI
"""pytest unit tests for tools.strategos.wargame_advisor."""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.strategos.wargame_advisor import _infer_theater, _mock_assessment, get_ai_assessment

# ── Shared constants ────────────────────────────────────────────────────────

_VALID_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
_REQUIRED_KEYS = {
    "summary", "recommended_coa", "risk_level",
    "rationale", "llm_active", "generated_at", "error",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sg_wargames (
    id TEXT PRIMARY KEY, name TEXT, scenario TEXT, state TEXT,
    blue_strength REAL, red_strength REAL, blue_force TEXT, red_force TEXT,
    attrition_coefficients_json TEXT, outcome TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS sg_wargame_turns (
    id TEXT PRIMARY KEY, wargame_id TEXT NOT NULL, turn_number INTEGER NOT NULL,
    blue_losses INTEGER, red_losses INTEGER, blue_remaining INTEGER, red_remaining INTEGER,
    tempo_delta REAL, notes TEXT, created_at TEXT
);
"""


def _make_conn(blue: float = 1000.0, red: float = 800.0, wg_id: str = "wg-test-1") -> sqlite3.Connection:
    """Return an in-memory SQLite connection pre-loaded with one wargame row."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO sg_wargames VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (wg_id, "Test Wargame", "Taiwan Strait scenario", "active",
         blue, red, None, None, None, None, None),
    )
    conn.commit()
    return conn


def _call_no_llm(conn: sqlite3.Connection, wg_id: str = "wg-test-1") -> dict:
    """Call get_ai_assessment() with DB and LLM fully mocked (no-LLM path)."""
    mock_storage = MagicMock()
    mock_storage.get_connection.return_value = conn
    mock_storage.is_pg.return_value = False

    mock_router_mod = MagicMock()
    mock_router_mod.LLMRouter.return_value.has_any_llm.return_value = False

    with patch.dict(sys.modules, {
        "tools.db.storage": mock_storage,
        "tools.llm.router": mock_router_mod,
    }):
        return get_ai_assessment(wg_id)


# ── _mock_assessment ────────────────────────────────────────────────────────

class TestMockAssessment:
    """Direct tests of the deterministic fallback function."""

    def _wg(self, blue: int = 1000, red: int = 800) -> dict:
        return {"blue_strength": blue, "red_strength": red, "scenario": "Taiwan", "name": "Test"}

    def test_returns_required_keys(self):
        assert _REQUIRED_KEYS.issubset(_mock_assessment(self._wg(), None).keys())

    def test_risk_level_low_when_blue_dominant(self):
        result = _mock_assessment(self._wg(blue=2000, red=800), None)
        assert result["risk_level"] == "LOW"
        assert result["risk_level"] in _VALID_RISK_LEVELS

    def test_risk_level_high_when_red_dominant(self):
        result = _mock_assessment(self._wg(blue=800, red=2000), None)
        assert result["risk_level"] == "HIGH"
        assert result["risk_level"] in _VALID_RISK_LEVELS

    def test_risk_level_medium_when_balanced(self):
        result = _mock_assessment(self._wg(blue=500, red=490), None)
        assert result["risk_level"] == "MEDIUM"
        assert result["risk_level"] in _VALID_RISK_LEVELS

    def test_llm_active_is_false(self):
        assert _mock_assessment(self._wg(), None)["llm_active"] is False

    def test_summary_is_non_empty_string(self):
        summary = _mock_assessment(self._wg(), None)["summary"]
        assert isinstance(summary, str) and summary

    def test_summary_blue_tempo_annotation(self):
        result = _mock_assessment(self._wg(), {"tempo_delta": 0.5, "turn_number": 2})
        assert "Blue holds OODA tempo advantage" in result["summary"]

    def test_summary_red_tempo_annotation(self):
        result = _mock_assessment(self._wg(), {"tempo_delta": -0.5, "turn_number": 2})
        assert "Red holds OODA tempo advantage" in result["summary"]

    def test_no_tempo_annotation_when_within_threshold(self):
        result = _mock_assessment(self._wg(), {"tempo_delta": 0.05, "turn_number": 1})
        assert "OODA tempo" not in result["summary"]

    def test_generated_at_is_iso8601(self):
        ts = _mock_assessment(self._wg(), None)["generated_at"]
        assert datetime.fromisoformat(ts) is not None


# ── get_ai_assessment — no-LLM path ────────────────────────────────────────

class TestGetAiAssessmentNoLLM:
    """get_ai_assessment() must return a valid dict and never raise when LLM is absent."""

    def test_returns_dict(self):
        assert isinstance(_call_no_llm(_make_conn()), dict)

    def test_required_keys_present(self):
        assert _REQUIRED_KEYS.issubset(_call_no_llm(_make_conn()).keys())

    def test_does_not_raise(self):
        result = _call_no_llm(_make_conn())
        assert result is not None

    def test_risk_level_is_valid(self):
        assert _call_no_llm(_make_conn())["risk_level"] in _VALID_RISK_LEVELS

    def test_llm_active_is_false(self):
        assert _call_no_llm(_make_conn())["llm_active"] is False

    def test_summary_contains_stub_marker(self):
        assert "[STUB" in _call_no_llm(_make_conn())["summary"]

    def test_blue_dominant_yields_low_risk(self):
        result = _call_no_llm(_make_conn(blue=2000.0, red=800.0))
        assert result["risk_level"] == "LOW"

    def test_red_dominant_yields_high_risk(self):
        result = _call_no_llm(_make_conn(blue=800.0, red=2000.0))
        assert result["risk_level"] == "HIGH"

    def test_unknown_wargame_id_raises_value_error(self):
        empty_conn = sqlite3.connect(":memory:")
        empty_conn.executescript(_SCHEMA)
        empty_conn.commit()
        with pytest.raises(ValueError, match="not found"):
            _call_no_llm(empty_conn, wg_id="ghost-id")


# ── _infer_theater ──────────────────────────────────────────────────────────

class TestInferTheater:
    def test_taiwan_keyword(self):
        assert _infer_theater("PLA crossing the Taiwan Strait") == "taiwan"

    def test_ukraine_keyword(self):
        assert _infer_theater("Russia advances into Ukraine") == "ukraine"

    def test_middle_east_keyword(self):
        assert _infer_theater("Conflict in the Middle East near Iran") == "middle_east"

    def test_korea_keyword(self):
        assert _infer_theater("DPRK aggression on the Korean Peninsula") == "korea"

    def test_cyber_keyword(self):
        assert _infer_theater("Information Warfare and cyber operations") == "cyber"

    def test_unspecified_fallback(self):
        assert _infer_theater("A generic modern battlefield scenario") == "unspecified"

    def test_case_insensitive(self):
        assert _infer_theater("TAIWAN STRAIT CRISIS") == "taiwan"
