# CUI // SP-CTI
"""Tests for ACE evidence_report (Phase 4 — ATO evidence provenance)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# In-memory DB fixture
# ---------------------------------------------------------------------------


def _make_ace_db(tmp_path: Path) -> sqlite3.Connection:
    """Return an open SQLite connection with a minimal ACE schema."""
    conn = sqlite3.connect(str(tmp_path / "ace.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE ace_instances (
            id TEXT PRIMARY KEY, name TEXT DEFAULT '', role_id TEXT DEFAULT '',
            state TEXT DEFAULT 'complete', trust_tier TEXT DEFAULT 'green',
            config_json TEXT DEFAULT '{}', result_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT '2026-01-01T00:00:00',
            updated_at TEXT DEFAULT '2026-01-01T00:00:00',
            completed_at TEXT
        );
        CREATE TABLE ace_coworkers (
            id TEXT PRIMARY KEY, instance_id TEXT, role_id TEXT DEFAULT '',
            display_name TEXT DEFAULT '', state TEXT DEFAULT 'done',
            trust_tier TEXT DEFAULT 'green', assigned_step TEXT DEFAULT '',
            last_active_at TEXT, created_at TEXT DEFAULT '2026-01-01T00:00:00'
        );
        CREATE TABLE ace_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, instance_id TEXT,
            coworker_id TEXT, action TEXT NOT NULL, detail TEXT DEFAULT '',
            actor TEXT DEFAULT 'system', classification TEXT DEFAULT 'CUI',
            control_refs TEXT DEFAULT '', created_at TEXT DEFAULT '2026-01-01T00:00:00'
        );
        CREATE TABLE ace_artifacts (
            id TEXT PRIMARY KEY, instance_id TEXT, coworker_id TEXT,
            artifact_type TEXT DEFAULT 'document', title TEXT DEFAULT '',
            classification TEXT DEFAULT 'CUI', content_json TEXT DEFAULT '{}',
            content_md TEXT DEFAULT '', created_at TEXT DEFAULT '2026-01-01T00:00:00'
        );
    """)
    return conn


def _seed_instance(conn: sqlite3.Connection, inst_id: str = "inst-test-001") -> None:
    conn.execute(
        "INSERT INTO ace_instances (id, name, role_id, state, trust_tier) "
        "VALUES (?, 'Test Instance', 'ai_developer', 'complete', 'green')",
        (inst_id,),
    )
    conn.execute(
        "INSERT INTO ace_coworkers (id, instance_id, role_id, display_name, state) "
        "VALUES (?, ?, 'ai_developer', 'AI Developer', 'done')",
        (f"{inst_id}-cw-1", inst_id),
    )
    conn.execute(
        "INSERT INTO ace_audit_log (instance_id, coworker_id, action, detail, actor, "
        "classification, control_refs) VALUES (?, ?, 'step_complete', 'step=analyze', "
        "'coworker_thread', 'CUI', 'AU-9,AU-12')",
        (inst_id, f"{inst_id}-cw-1"),
    )
    conn.execute(
        "INSERT INTO ace_audit_log (instance_id, coworker_id, action, detail, actor, "
        "classification, control_refs) VALUES (?, ?, 'coworker_done', 'all steps completed', "
        "'coworker_thread', 'CUI', '')",
        (inst_id, f"{inst_id}-cw-1"),
    )
    conn.execute(
        "INSERT INTO ace_artifacts (id, instance_id, coworker_id, artifact_type, title) "
        "VALUES (?, ?, ?, 'document', 'Analysis Report')",
        (f"{inst_id}-art-1", inst_id, f"{inst_id}-cw-1"),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# _collect()
# ---------------------------------------------------------------------------


class TestCollect:
    def _patched_conn(self, conn):
        return patch("icdev.tools.db.storage.get_canvas_connection", return_value=conn)

    def test_collect_returns_required_keys(self, tmp_path):
        conn = _make_ace_db(tmp_path)
        _seed_instance(conn, "inst-1")
        with self._patched_conn(conn):
            from icdev.tools.ace.evidence_report import _collect
            data = _collect("inst-1")
        assert "instance" in data
        assert "coworkers" in data
        assert "audit_trail" in data
        assert "artifacts" in data
        assert "control_refs" in data
        assert "summary" in data

    def test_collect_latest_resolves_to_most_recent(self, tmp_path):
        conn = _make_ace_db(tmp_path)
        _seed_instance(conn, "inst-alpha")
        with self._patched_conn(conn):
            from icdev.tools.ace.evidence_report import _collect
            data = _collect("latest")
        assert data["instance"]["id"] == "inst-alpha"

    def test_collect_unknown_instance_raises(self, tmp_path):
        conn = _make_ace_db(tmp_path)
        with self._patched_conn(conn):
            from icdev.tools.ace.evidence_report import _collect
            with pytest.raises(ValueError, match="not found"):
                _collect("inst-nonexistent")

    def test_collect_deduplicates_control_refs(self, tmp_path):
        conn = _make_ace_db(tmp_path)
        _seed_instance(conn, "inst-2")
        # Add another audit row with overlapping controls
        conn.execute(
            "INSERT INTO ace_audit_log (instance_id, action, control_refs) "
            "VALUES ('inst-2', 'file_read', 'AU-9,AC-3')",
        )
        conn.commit()
        with self._patched_conn(conn):
            from icdev.tools.ace.evidence_report import _collect
            data = _collect("inst-2")
        # AU-9 appears twice but should be deduplicated
        refs = data["control_refs"]
        assert refs.count("AU-9") == 1
        assert "AC-3" in refs
        assert "AU-12" in refs

    def test_collect_summary_counts(self, tmp_path):
        conn = _make_ace_db(tmp_path)
        _seed_instance(conn, "inst-3")
        with self._patched_conn(conn):
            from icdev.tools.ace.evidence_report import _collect
            data = _collect("inst-3")
        summary = data["summary"]
        assert summary["total_actions"] == 2
        assert summary["total_artifacts"] == 1
        assert summary["coworker_count"] == 1


# ---------------------------------------------------------------------------
# generate() format outputs
# ---------------------------------------------------------------------------


class TestGenerate:
    def _patched_conn(self, conn):
        return patch("icdev.tools.db.storage.get_canvas_connection", return_value=conn)

    def _mock_banner(self):
        return patch(
            "icdev.tools.ace.evidence_report._get_banner",
            return_value={"banner_top": "// CUI //", "banner_bottom": "// CUI //"},
        )

    def test_generate_json_returns_dict(self, tmp_path):
        conn = _make_ace_db(tmp_path)
        _seed_instance(conn, "inst-j")
        with self._patched_conn(conn), self._mock_banner():
            from icdev.tools.ace.evidence_report import generate
            result = generate("inst-j", fmt="json")
        assert isinstance(result, dict)
        assert result["instance"]["id"] == "inst-j"

    def test_generate_ssp_contains_classification_banner(self, tmp_path):
        conn = _make_ace_db(tmp_path)
        _seed_instance(conn, "inst-s")
        with self._patched_conn(conn), self._mock_banner():
            from icdev.tools.ace.evidence_report import generate
            result = generate("inst-s", fmt="ssp")
        assert isinstance(result, str)
        assert "// CUI //" in result
        assert "ATO EVIDENCE REPORT" in result
        assert "SECTION 1" in result
        assert "SECTION 3 — AUDIT TRAIL" in result
        assert "SECTION 5 — NIST 800-53" in result

    def test_generate_ssp_shows_control_refs(self, tmp_path):
        conn = _make_ace_db(tmp_path)
        _seed_instance(conn, "inst-cr")
        with self._patched_conn(conn), self._mock_banner():
            from icdev.tools.ace.evidence_report import generate
            result = generate("inst-cr", fmt="ssp")
        assert "AU-9" in result
        assert "AU-12" in result

    def test_generate_markdown_contains_table(self, tmp_path):
        conn = _make_ace_db(tmp_path)
        _seed_instance(conn, "inst-m")
        with self._patched_conn(conn), self._mock_banner():
            from icdev.tools.ace.evidence_report import generate
            result = generate("inst-m", fmt="markdown")
        assert isinstance(result, str)
        assert "## Audit Trail" in result
        assert "| Timestamp |" in result

    def test_generate_markdown_shows_nist_refs(self, tmp_path):
        conn = _make_ace_db(tmp_path)
        _seed_instance(conn, "inst-mr")
        with self._patched_conn(conn), self._mock_banner():
            from icdev.tools.ace.evidence_report import generate
            result = generate("inst-mr", fmt="markdown")
        assert "NIST 800-53" in result
        assert "`AU-9`" in result


# ---------------------------------------------------------------------------
# Classification watermark
# ---------------------------------------------------------------------------


class TestWatermark:
    def test_public_below_cui(self):
        from icdev.tools.ace.evidence_report import _watermark_classification
        assert _watermark_classification(["PUBLIC", "CUI"]) == "CUI"

    def test_secret_wins(self):
        from icdev.tools.ace.evidence_report import _watermark_classification
        assert _watermark_classification(["CUI", "SECRET", "PUBLIC"]) == "SECRET"

    def test_empty_list_returns_default(self):
        from icdev.tools.ace.evidence_report import _watermark_classification
        result = _watermark_classification([])
        assert result == "CUI"

    def test_single_known_value(self):
        from icdev.tools.ace.evidence_report import _watermark_classification
        assert _watermark_classification(["ECI"]) == "ECI"


# ---------------------------------------------------------------------------
# init_db adds control_refs column
# ---------------------------------------------------------------------------


class TestInitDbControlRefs:
    def test_init_db_schema_has_control_refs(self):
        from icdev.tools.ace.db.init_db import SCHEMA
        assert "control_refs" in SCHEMA
