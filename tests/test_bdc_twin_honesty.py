#!/usr/bin/env python3
# CUI // SP-CTI
"""Honesty tests for tools/boundary_canvas/twin.py (task bdr-sec-4).

Covers the three honesty fixes:
  1. Swallowed exceptions -> explicit error payloads (take_snapshot,
     crosswalk_drift) that are distinguishable from a clean-empty result.
  2. Fake Chain-of-Debate -> the default deterministic path is labelled
     method="heuristic"; a real LLM debate runs only behind the OFF-by-default
     twin.chain_of_debate.enabled flag; flag-off never touches LLMRouter.
  3. Fake scores -> evidence_score / score derived from the delta (or None when
     empty); no 0.75 / 0.8 magic literals remain in the module source.
"""

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TWIN_PY = ROOT / "tools" / "boundary_canvas" / "twin.py"

_DDL = """
CREATE TABLE project_controls (project_id TEXT);
CREATE TABLE evidence (project_id TEXT);
CREATE TABLE compliance_snapshots (
    snapshot_id TEXT, project_id TEXT, framework_id TEXT, control_id TEXT,
    implementation_status TEXT, evidence_ref TEXT, taken_at TEXT
);
"""

_DEBATE_OFF = {
    "chain_of_debate": {"enabled": False, "llm_function": "reasoning"},
    "rating_bands": {"green": 0.8, "amber": 0.5},
}
_DEBATE_ON = {
    "chain_of_debate": {"enabled": True, "llm_function": "reasoning"},
    "rating_bands": {"green": 0.8, "amber": 0.5},
}


class _RaisingConn:
    """A connection whose every query raises — simulates a DB outage."""

    def execute(self, *a, **k):
        raise RuntimeError("db down")

    def commit(self):
        pass


@pytest.fixture
def mem_db():
    """Translating StorageConnection over in-memory SQLite with empty twin tables."""
    from tools.db.storage import StorageConnection

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(_DDL)
    raw.commit()
    conn = StorageConnection(raw, "sqlite")
    yield conn
    raw.close()


# ---------------------------------------------------------------------------
# 1. Swallowed exceptions -> explicit error payloads
# ---------------------------------------------------------------------------

class TestTakeSnapshotHonesty:
    def test_db_error_returns_error_payload_not_zeroed_success(self):
        with patch("tools.boundary_canvas.twin.get_connection", return_value=_RaisingConn()):
            from tools.boundary_canvas.twin import take_snapshot
            result = take_snapshot("proj-x")
        assert result["status"] == "error"
        assert result["persisted"] is False
        # Counts are None (unknown), NOT a fake 0 that reads like "clean empty".
        assert result["control_count"] is None
        assert result["evidence_count"] is None
        assert result.get("error")

    def test_clean_empty_is_distinguishable_from_error(self, mem_db):
        with patch("tools.boundary_canvas.twin.get_connection", return_value=mem_db):
            from tools.boundary_canvas.twin import take_snapshot
            result = take_snapshot("proj-empty")
        assert result["status"] == "ok"
        assert result["persisted"] is True
        assert result["control_count"] == 0
        assert result["evidence_count"] == 0


class TestCrosswalkDriftHonesty:
    def test_db_error_returns_error_payload(self):
        with patch("tools.boundary_canvas.twin.get_connection", return_value=_RaisingConn()):
            from tools.boundary_canvas.twin import crosswalk_drift
            result = crosswalk_drift("proj", "NIST 800-53", "CMMC Level 2")
        assert result["status"] == "error"
        assert result["drifts"] == []
        assert result["total"] == 0
        assert result.get("error")

    def test_clean_empty_is_ok_status(self, mem_db):
        with patch("tools.boundary_canvas.twin.get_connection", return_value=mem_db):
            from tools.boundary_canvas.twin import crosswalk_drift
            result = crosswalk_drift("proj", "NIST 800-53", "CMMC Level 2")
        assert result["status"] == "ok"
        assert result["drifts"] == []
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# 2. Chain-of-Debate honesty
# ---------------------------------------------------------------------------

class TestChainOfDebateHonesty:
    _DELTA = [
        {"control_id": "AC-2", "implementation_status": "satisfied", "evidence_ref": "ev"},
        {"control_id": "AU-6", "implementation_status": "not_satisfied"},
    ]

    def test_default_path_labelled_heuristic(self):
        """use_cod with the real (OFF) config yields a heuristic-labelled transcript."""
        from tools.boundary_canvas.twin import simulate_delta
        result = simulate_delta("proj", self._DELTA, use_cod=True)
        assert result["cod_method"] == "heuristic"
        assert result["cod_transcript"]
        for entry in result["cod_transcript"]:
            assert entry["method"] == "heuristic"
        # No fabricated judge_verdict — the heuristic does not adjudicate.
        assert all("judge_verdict" not in e for e in result["cod_transcript"])

    def test_flag_off_never_touches_llm_router(self):
        from tools.boundary_canvas.twin import simulate_delta
        with patch("tools.boundary_canvas.twin._load_twin_config", return_value=_DEBATE_OFF):
            with patch("tools.llm.router.LLMRouter") as MockRouter:
                result = simulate_delta("proj", self._DELTA, use_cod=True)
        MockRouter.assert_not_called()
        assert result["cod_method"] == "heuristic"

    def test_flag_on_calls_through_llm_router(self):
        from tools.boundary_canvas.twin import simulate_delta
        fake_response = SimpleNamespace(
            content='{"debate": [{"control_id": "AC-2", "status": "satisfied", '
            '"for": "evidence attached", "against": "verify scope", '
            '"judge_verdict": "satisfied"}]}'
        )
        with patch("tools.boundary_canvas.twin._load_twin_config", return_value=_DEBATE_ON):
            with patch("tools.llm.router.LLMRouter") as MockRouter:
                inst = MockRouter.return_value
                inst.is_no_llm_mode.return_value = False
                inst.has_any_llm.return_value = True
                inst.invoke.return_value = fake_response
                result = simulate_delta("proj", self._DELTA, use_cod=True)
        inst.invoke.assert_called_once()
        # Function name (not a hardcoded model id) is passed as the first arg.
        assert inst.invoke.call_args[0][0] == "reasoning"
        assert result["cod_method"] == "llm_debate"
        assert result["cod_transcript"][0]["method"] == "llm_debate"

    def test_flag_on_llm_failure_falls_back_to_heuristic(self):
        from tools.boundary_canvas.twin import simulate_delta
        with patch("tools.boundary_canvas.twin._load_twin_config", return_value=_DEBATE_ON):
            with patch("tools.llm.router.LLMRouter") as MockRouter:
                inst = MockRouter.return_value
                inst.is_no_llm_mode.return_value = False
                inst.has_any_llm.return_value = True
                inst.invoke.side_effect = RuntimeError("timeout")
                result = simulate_delta("proj", self._DELTA, use_cod=True)
        assert result["cod_method"] == "heuristic"
        assert all(e["method"] == "heuristic" for e in result["cod_transcript"])


# ---------------------------------------------------------------------------
# 3. Fake scores removed
# ---------------------------------------------------------------------------

class TestScoreHonesty:
    def test_no_fake_score_literals_in_source(self):
        src = TWIN_PY.read_text(encoding="utf-8")
        assert "0.75" not in src, "hardcoded evidence_score 0.75 must be gone"
        assert "0.8" not in src, "hardcoded default score 0.8 must be gone"

    def test_empty_delta_is_not_scored(self):
        from tools.boundary_canvas.twin import simulate_delta
        result = simulate_delta("proj", [])
        assert result["score"] is None
        assert result["control_score"] is None
        assert result["evidence_score"] is None
        assert result["rating"] == "unknown"

    def test_evidence_score_derived_from_delta(self):
        from tools.boundary_canvas.twin import simulate_delta
        delta = [
            {"control_id": "AC-2", "implementation_status": "satisfied", "evidence_ref": "ev"},
            {"control_id": "AC-3", "implementation_status": "not_satisfied"},
        ]
        result = simulate_delta("proj", delta)
        # 1 of 2 controls satisfied -> 0.5; 1 of 2 carries evidence -> 0.5.
        assert result["score"] == 0.5
        assert result["evidence_score"] == 0.5

    def test_full_evidence_gives_full_evidence_score(self):
        from tools.boundary_canvas.twin import simulate_delta
        delta = [
            {"control_id": "AC-2", "implementation_status": "satisfied", "evidence_ref": "a"},
            {"control_id": "AC-3", "implementation_status": "satisfied", "evidence_ref": "b"},
        ]
        result = simulate_delta("proj", delta)
        assert result["score"] == 1.0
        assert result["evidence_score"] == 1.0
        assert result["rating"] == "green"
