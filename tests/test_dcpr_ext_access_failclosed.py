# CUI // SP-CTI
"""Tests for dcpr-sec-04 — fail-closed toggle on check_ext_access local no-OPA path.

check_ext_access() gates IQE ext.* collection reads. When ICDEV_OPA_URL is blank
(the default), it historically ALWAYS returned allowed=True (fail-open). This suite
verifies the ICDEV_GOVERNANCE_FAIL_CLOSED toggle:
  (a) toggle ON  + no OPA  => allowed False, audit entry still written
  (b) toggle OFF (default) + no OPA => allowed True (behavior preserved)
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

# Shim-aware: resolve the module object once via importlib so setattr patches
# the same object the code under test executes against (tools.* vs icdev.tools.*).
_GOV = importlib.import_module("tools.data_canvas.data_mesh.governance_engine")


def _make_data_mesh_conn():
    """In-memory SQLite with the minimal data mesh audit schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE dm_policy_audit_log (
            id TEXT PRIMARY KEY,
            policy_id TEXT,
            user TEXT,
            resource TEXT,
            decision INTEGER,
            reason TEXT,
            method TEXT,
            classification TEXT,
            created_at TEXT
        );
        """
    )
    return conn


def _patch_dm_get_connection(conn):
    """Return a mock that behaves like data_canvas get_connection() context manager."""
    mock = MagicMock()
    mock.return_value.__enter__ = lambda s: conn
    mock.return_value.__exit__ = MagicMock(return_value=False)
    return mock


class TestExtAccessFailClosed:
    def test_toggle_on_no_opa_denies_and_audits(self, monkeypatch):
        """Toggle ON + no OPA => allowed False AND an audit row is written."""
        conn = _make_data_mesh_conn()
        monkeypatch.setenv("ICDEV_GOVERNANCE_FAIL_CLOSED", "1")
        monkeypatch.setattr(_GOV, "_OPA_URL", "", raising=False)
        monkeypatch.setattr(_GOV, "get_connection", _patch_dm_get_connection(conn), raising=False)

        result = _GOV.check_ext_access("splunk", "alerts")

        assert result["allowed"] is False
        assert result["method"] == "local"
        assert "fail-closed" in result["reason"]

        rows = conn.execute("SELECT * FROM dm_policy_audit_log").fetchall()
        assert len(rows) == 1
        row = dict(rows[0])
        assert row["decision"] == 0  # denied
        assert "ext.splunk.alerts" in row["resource"]

    def test_toggle_on_accepts_true_yes_case_insensitive(self, monkeypatch):
        """Toggle accepts '1'/'true'/'yes' case-insensitively."""
        monkeypatch.setattr(_GOV, "_OPA_URL", "", raising=False)
        conn = _make_data_mesh_conn()
        monkeypatch.setattr(_GOV, "get_connection", _patch_dm_get_connection(conn), raising=False)

        for token in ("true", "YES", "True", "1"):
            monkeypatch.setenv("ICDEV_GOVERNANCE_FAIL_CLOSED", token)
            result = _GOV.check_ext_access("tenable", "vulnerabilities")
            assert result["allowed"] is False, f"token={token!r} should deny"

    def test_toggle_off_default_no_opa_allows(self, monkeypatch):
        """Toggle OFF (unset) + no OPA => allowed True (historical behavior preserved)."""
        conn = _make_data_mesh_conn()
        monkeypatch.delenv("ICDEV_GOVERNANCE_FAIL_CLOSED", raising=False)
        monkeypatch.setattr(_GOV, "_OPA_URL", "", raising=False)
        monkeypatch.setattr(_GOV, "get_connection", _patch_dm_get_connection(conn), raising=False)

        result = _GOV.check_ext_access("splunk", "alerts")

        assert result["allowed"] is True
        assert result["method"] == "local"

        row = dict(conn.execute("SELECT * FROM dm_policy_audit_log").fetchone())
        assert row["decision"] == 1  # allowed

    def test_toggle_falsey_value_still_allows(self, monkeypatch):
        """A non-affirmative value (e.g. '0'/'false') leaves fail-open behavior intact."""
        conn = _make_data_mesh_conn()
        monkeypatch.setenv("ICDEV_GOVERNANCE_FAIL_CLOSED", "0")
        monkeypatch.setattr(_GOV, "_OPA_URL", "", raising=False)
        monkeypatch.setattr(_GOV, "get_connection", _patch_dm_get_connection(conn), raising=False)

        result = _GOV.check_ext_access("gdelt", "events")

        assert result["allowed"] is True
