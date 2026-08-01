# CUI // SP-CTI
"""Tests for ACE SkillPromoter (Phase 3 — SIPA-gated skill candidate promotion)."""
from __future__ import annotations

import sqlite3
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> str:
    """Create an in-memory-backed SQLite ACE DB and return connection URI hint."""
    db_path = str(tmp_path / "ace.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE ace_skill_candidates (
            id TEXT PRIMARY KEY,
            role_id TEXT NOT NULL,
            source_role TEXT NOT NULL DEFAULT '',
            instance_id TEXT NOT NULL DEFAULT '',
            candidate_yaml TEXT NOT NULL DEFAULT '',
            trust_tier TEXT NOT NULL DEFAULT 'yellow',
            status TEXT NOT NULL DEFAULT 'pending',
            sipa_verdict TEXT,
            sipa_score REAL,
            rejection_reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_candidate(db_path: str, candidate_id: str, role_id: str, yaml_str: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ace_skill_candidates (id, role_id, candidate_yaml, trust_tier) "
        "VALUES (?, ?, ?, 'green')",
        (candidate_id, role_id, yaml_str),
    )
    conn.commit()
    conn.close()


def _get_status(db_path: str, candidate_id: str) -> dict:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT status, sipa_verdict, sipa_score, rejection_reason FROM ace_skill_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    conn.close()
    return dict(zip(("status", "sipa_verdict", "sipa_score", "rejection_reason"), row or ("unknown", None, None, None)))


# ---------------------------------------------------------------------------
# skill_promoter.py unit tests
# ---------------------------------------------------------------------------


class TestSkillPromoterRun:
    """Tests for icdev.tools.ace.skill_promoter.run() with mocked DB + SIPA."""

    def _run_with_mock_db(self, tmp_path, candidates, sipa_verdict="clean", sipa_score=5.0):
        """Run promoter with mocked DB returning given candidates and SIPA verdict."""
        from icdev.tools.ace.skill_promoter import run

        candidates_dir = tmp_path / "candidates"

        with patch("icdev.tools.ace.skill_promoter._load_pending", return_value=candidates), \
             patch("icdev.tools.ace.skill_promoter._run_sipa", return_value=(sipa_verdict, sipa_score)), \
             patch("icdev.tools.ace.skill_promoter._ROLES_CANDIDATES_DIR", candidates_dir), \
             patch("icdev.tools.ace.skill_promoter._update_status") as mock_update, \
             patch("icdev.tools.ace.skill_promoter._promote") as mock_promote, \
             patch("icdev.tools.ace.skill_promoter._reject") as mock_reject:
            result = run({}, None)
        return result, mock_promote, mock_reject, mock_update

    def test_empty_pending_returns_zeros(self, tmp_path):
        from icdev.tools.ace.skill_promoter import run

        with patch("icdev.tools.ace.skill_promoter._load_pending", return_value=[]):
            result = run({}, None)

        assert result["promoted"] == 0
        assert result["rejected"] == 0
        assert result["assessed"] == 0

    def test_clean_verdict_calls_promote(self, tmp_path):
        candidates = [{"id": "c1", "role_id": "test_role", "candidate_yaml": "role_id: test_role\n"}]
        result, mock_promote, mock_reject, _ = self._run_with_mock_db(tmp_path, candidates, "clean", 2.0)
        assert result["promoted"] == 1
        assert result["rejected"] == 0
        mock_promote.assert_called_once()
        mock_reject.assert_not_called()

    def test_low_risk_verdict_calls_promote(self, tmp_path):
        candidates = [{"id": "c2", "role_id": "test_role", "candidate_yaml": "role_id: test_role\n"}]
        result, mock_promote, mock_reject, _ = self._run_with_mock_db(tmp_path, candidates, "low_risk", 15.0)
        assert result["promoted"] == 1
        mock_promote.assert_called_once()

    def test_malicious_verdict_calls_reject(self, tmp_path):
        candidates = [{"id": "c3", "role_id": "bad_role", "candidate_yaml": "role_id: bad\n"}]
        result, mock_promote, mock_reject, _ = self._run_with_mock_db(tmp_path, candidates, "malicious", 95.0)
        assert result["rejected"] == 1
        assert result["promoted"] == 0
        mock_reject.assert_called_once()

    def test_high_risk_verdict_calls_reject(self, tmp_path):
        candidates = [{"id": "c4", "role_id": "risky_role", "candidate_yaml": "role_id: risky\n"}]
        result, mock_promote, mock_reject, _ = self._run_with_mock_db(tmp_path, candidates, "high_risk", 80.0)
        mock_reject.assert_called_once()

    def test_multiple_candidates_assessed(self, tmp_path):
        candidates = [
            {"id": f"c{i}", "role_id": f"role_{i}", "candidate_yaml": f"role_id: role_{i}\n"}
            for i in range(3)
        ]
        result, mock_promote, mock_reject, _ = self._run_with_mock_db(tmp_path, candidates, "clean", 5.0)
        assert result["assessed"] == 3
        assert result["promoted"] == 3
        assert mock_promote.call_count == 3

    def test_exception_in_assessment_counts_as_skipped(self, tmp_path):
        candidates = [{"id": "c5", "role_id": "boom", "candidate_yaml": "x"}]
        from icdev.tools.ace.skill_promoter import run

        with patch("icdev.tools.ace.skill_promoter._load_pending", return_value=candidates), \
             patch("icdev.tools.ace.skill_promoter._run_sipa", side_effect=RuntimeError("SIPA exploded")), \
             patch("icdev.tools.ace.skill_promoter._mark_skipped") as mock_skip:
            result = run({}, None)

        assert result["skipped"] == 1
        assert result["promoted"] == 0
        mock_skip.assert_called_once()


# ---------------------------------------------------------------------------
# _run_sipa fallback when SIPA not available
# ---------------------------------------------------------------------------


class TestSIPAFallback:
    """SIPA must fail CLOSED.

    This gate stands between an LLM-authored role spec and a promoted,
    staffable role. It previously returned ("clean", 0.0) whenever the scanner
    was absent or errored, so an unvetted candidate was promoted on the strength
    of the scanner not being installed — the gate was strongest exactly when it
    was working and absent when it was not. These tests pin the inverse.
    """

    def test_sipa_import_error_fails_closed(self, tmp_path):
        from icdev.tools.ace.skill_promoter import (
            _PROMOTABLE_VERDICTS,
            _VERDICT_UNAVAILABLE,
            _run_sipa,
        )

        with patch.dict("sys.modules", {"tools.integrity.engine": None}):
            verdict, score = _run_sipa("role_id: x\n", "test_role")

        assert verdict == _VERDICT_UNAVAILABLE
        assert verdict not in _PROMOTABLE_VERDICTS
        assert score == 0.0

    def test_sipa_assessment_error_fails_closed(self, tmp_path):
        """A scanner that raises has vetted nothing — same rule as absent."""
        from icdev.tools.ace.skill_promoter import (
            _PROMOTABLE_VERDICTS,
            _VERDICT_UNAVAILABLE,
            _run_sipa,
        )

        engine = types.ModuleType("tools.integrity.engine")
        engine.assess = MagicMock(side_effect=RuntimeError("scanner exploded"))

        with patch.dict("sys.modules", {"tools.integrity.engine": engine}):
            verdict, score = _run_sipa("role_id: x\n", "test_role")

        assert verdict == _VERDICT_UNAVAILABLE
        assert verdict not in _PROMOTABLE_VERDICTS


# ---------------------------------------------------------------------------
# Promote writes YAML to candidates dir
# ---------------------------------------------------------------------------


class TestPromoteOutput:
    def test_promote_writes_yaml_file(self, tmp_path):
        from icdev.tools.ace.skill_promoter import _promote

        candidates_dir = tmp_path / "candidates"
        candidate_yaml = "role_id: test_skill\ntrust_tier: green\n"

        with patch("icdev.tools.ace.skill_promoter._ROLES_CANDIDATES_DIR", candidates_dir), \
             patch("icdev.tools.ace.skill_promoter._update_status"), \
             patch("icdev.tools.ace.skill_promoter.log_component_audit", create=True):
            try:
                with patch("icdev.tools.ace.skill_promoter._promote", wraps=lambda *a, **k: None):
                    pass
            except Exception:
                pass

            _promote("cid-1", "test_skill", candidate_yaml, "clean", 3.0)

        out_file = candidates_dir / "test_skill.yaml"
        assert out_file.exists()
        loaded = yaml.safe_load(out_file.read_text(encoding="utf-8"))
        assert loaded["role_id"] == "test_skill"


# ---------------------------------------------------------------------------
# Reflex registry has ace_skill_promoter
# ---------------------------------------------------------------------------


class TestReflexRegistry:
    def test_ace_skill_promoter_in_registry(self):
        from tools.genesis.reflex_registry import get

        entry = get("ace_skill_promoter")
        assert entry.name == "ace_skill_promoter"
        assert entry.interval_h == 24.0

    def test_ace_skill_promoter_in_icdev_registry(self):
        from icdev.tools.genesis.reflex_registry import get

        entry = get("ace_skill_promoter")
        assert entry.name == "ace_skill_promoter"


# ---------------------------------------------------------------------------
# DB table schema (init_db includes ace_skill_candidates)
# ---------------------------------------------------------------------------


class TestSkillCandidatesSchema:
    def test_init_db_creates_ace_skill_candidates(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "ace_test.db")
        monkeypatch.setenv("ICDEV_ACE_DB_URL", f"sqlite:///{db_path}")

        with patch("icdev.tools.db.storage.get_canvas_connection") as mock_conn:
            conn = sqlite3.connect(db_path)
            mock_conn.return_value = conn
            from icdev.tools.ace.db.init_db import init
            init()

        conn2 = sqlite3.connect(db_path)
        tables = {r[0] for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn2.close()
        assert "ace_skill_candidates" in tables
