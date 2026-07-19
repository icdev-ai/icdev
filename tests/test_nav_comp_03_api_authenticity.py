# CUI // SP-CTI
"""Regression tests for nav-comp-03 — Compliance-menu API data-authenticity audit.

The compliance API family must never present fabricated posture. Before this
task several blueprints:

  * swallowed DB errors to 0 / empty lists (``_safe_count`` → 0, list endpoints
    returning ``{...: [], "error": ...}`` with HTTP 200), so a failed compliance
    read rendered as "0 incidents / 0 appeals / nothing overdue" — a
    fabricated-healthy posture;
  * used the SQLite-only ``date('now')`` (ai_accountability ``/overdue``) and a
    raw ``%s`` literal (cato ``/refresh``) that raised on the "wrong" backend
    under a swallowing except; and
  * left compliance-posture **mutations** (audits, card generation, STIG status
    flips, SbD assessment, PR analysis, evidence refresh) with no role gate.

These tests prove the fixes: fail-loud reads, backend-agnostic SQL, and
``@require_role``-gated mutations that deny unauthenticated callers with 401.
"""

from pathlib import Path

import pytest
from flask import Flask

import tools.dashboard.api.ai_accountability as acct_mod
import tools.dashboard.api.ai_transparency as trans_mod
import tools.dashboard.api.cato as cato_mod
import tools.dashboard.api.sbd as sbd_mod
import tools.dashboard.api.stig_manager as stig_mod
import tools.dashboard.api.pr_intel as pr_mod
from tools.db.storage import get_connection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _client(blueprint):
    """Fresh Flask app with only the blueprint registered (no app auth wiring).

    require_role reads ``flask.g.current_user``; with no before_request setting
    it, an unauthenticated request aborts 401 — exactly what a mutation gate
    must do.
    """
    app = Flask(__name__)
    app.register_blueprint(blueprint)
    return app.test_client()


def _conn(db_path):
    return get_connection(db_path=str(db_path))


# ---------------------------------------------------------------------------
# Group A — _safe_count fail-loud (both AI blueprints)
# ---------------------------------------------------------------------------
def test_accountability_safe_count_missing_table_is_honest_zero(icdev_db):
    """A missing table is an honest 0 (feature not migrated), not an error."""
    conn = _conn(icdev_db)
    try:
        assert acct_mod._safe_count(conn, "no_such_accountability_table") == 0
    finally:
        conn.close()


def test_accountability_safe_count_query_error_propagates(icdev_db, monkeypatch):
    """A genuine query failure must NOT be masked as 0 (fabricated healthy)."""
    # Force table_exists True so the COUNT runs against a table that isn't there.
    monkeypatch.setattr(acct_mod, "table_exists", lambda conn, t: True)
    conn = _conn(icdev_db)
    try:
        with pytest.raises(Exception):
            acct_mod._safe_count(conn, "definitely_missing_table")
    finally:
        conn.close()


def test_transparency_safe_count_missing_table_is_honest_zero(icdev_db):
    conn = _conn(icdev_db)
    try:
        assert trans_mod._safe_count(conn, "no_such_transparency_table") == 0
    finally:
        conn.close()


def test_transparency_safe_count_query_error_propagates(icdev_db, monkeypatch):
    monkeypatch.setattr(trans_mod, "table_exists", lambda conn, t: True)
    conn = _conn(icdev_db)
    try:
        with pytest.raises(Exception):
            trans_mod._safe_count(conn, "definitely_missing_table")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Group B — /stats fails loud (HTTP 500) instead of fabricating zeros
# ---------------------------------------------------------------------------
def test_accountability_stats_fails_loud_on_count_error(icdev_db, monkeypatch):
    monkeypatch.setattr(acct_mod, "DB_PATH", Path(str(icdev_db)))

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(acct_mod, "_safe_count", _boom)
    resp = _client(acct_mod.ai_accountability_api).get("/api/ai-accountability/stats")
    assert resp.status_code == 500
    assert "error" in resp.get_json()


def test_transparency_stats_fails_loud_on_count_error(icdev_db, monkeypatch):
    monkeypatch.setattr(trans_mod, "DB_PATH", Path(str(icdev_db)))

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(trans_mod, "_safe_count", _boom)
    resp = _client(trans_mod.ai_transparency_api).get("/api/ai-transparency/stats")
    assert resp.status_code == 500
    assert "error" in resp.get_json()


# ---------------------------------------------------------------------------
# Group C — /overdue cross-backend (Python cutoff, no date('now'))
# ---------------------------------------------------------------------------
def test_overdue_uses_python_cutoff_and_filters_correctly(icdev_db, monkeypatch):
    """The Python-computed cutoff query runs on SQLite and returns only overdue rows."""
    conn = _conn(icdev_db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ai_reassessment_schedule "
        "(id INTEGER PRIMARY KEY, project_id TEXT, next_due TEXT)"
    )
    conn.execute("INSERT INTO ai_reassessment_schedule (project_id, next_due) VALUES ('p1', '2000-01-01')")
    conn.execute("INSERT INTO ai_reassessment_schedule (project_id, next_due) VALUES ('p1', '2999-01-01')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(acct_mod, "DB_PATH", Path(str(icdev_db)))
    resp = _client(acct_mod.ai_accountability_api).get("/api/ai-accountability/overdue")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 1, "only the past-due row must be reported as overdue"
    assert data["overdue"][0]["next_due"] == "2000-01-01"


def test_overdue_fails_loud_on_query_error(icdev_db, monkeypatch):
    """Missing table (or any read error) returns 500, not an empty all-clear list."""
    monkeypatch.setattr(acct_mod, "DB_PATH", Path(str(icdev_db)))
    # ai_reassessment_schedule does not exist in the minimal schema.
    resp = _client(acct_mod.ai_accountability_api).get("/api/ai-accountability/overdue")
    assert resp.status_code == 500
    body = resp.get_json()
    assert "error" in body
    assert "overdue" not in body  # no fake empty list


# ---------------------------------------------------------------------------
# Group D — mutation endpoints are role-gated (401 when unauthenticated)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "blueprint, path",
    [
        (cato_mod.cato_api, "/api/cato/refresh"),
        (sbd_mod.sbd_api, "/api/sbd/assess"),
        (trans_mod.ai_transparency_api, "/api/ai-transparency/audit"),
        (trans_mod.ai_transparency_api, "/api/ai-transparency/model-card"),
        (trans_mod.ai_transparency_api, "/api/ai-transparency/system-card"),
        (acct_mod.ai_accountability_api, "/api/ai-accountability/audit"),
        (stig_mod.stig_manager_api, "/api/stig-manager/assess"),
        (pr_mod.pr_intel_api, "/api/pr-intel/analyze"),
    ],
)
def test_mutation_denies_unauthenticated(blueprint, path):
    resp = _client(blueprint).post(path, json={"project_id": "p1"})
    assert resp.status_code == 401, f"{path} must deny unauthenticated callers with 401"


# ---------------------------------------------------------------------------
# Group E — source scans: dialect fixes + require_role wired
# ---------------------------------------------------------------------------
def test_no_sqlite_only_date_now_in_accountability():
    src = Path(acct_mod.__file__).read_text(encoding="utf-8")
    assert "date('now')" not in src, "SQLite-only date('now') must be gone"
    assert "datetime('now'" not in src


def test_cato_refresh_uses_qmark_not_raw_percent_literal():
    src = Path(cato_mod.__file__).read_text(encoding="utf-8")
    assert "project_id = %s AND status = 'expired'" not in src, (
        "raw %s literal breaks on the default SQLite backend"
    )
    assert "project_id = ? AND status = 'expired'" in src


def test_transparency_safe_count_no_raw_percent_literal():
    src = Path(trans_mod.__file__).read_text(encoding="utf-8")
    assert "FROM {table} WHERE project_id = %s" not in src


@pytest.mark.parametrize(
    "mod",
    [acct_mod, trans_mod, cato_mod, sbd_mod, stig_mod, pr_mod],
)
def test_require_role_imported(mod):
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "from tools.dashboard.auth import require_role" in src
    assert "@require_role(" in src


@pytest.mark.parametrize(
    "mod",
    [acct_mod, trans_mod, cato_mod, sbd_mod, stig_mod, pr_mod],
)
def test_no_empty_as_healthy_error_returns(mod):
    """Compliance reads must not return an empty payload alongside an error at 200."""
    src = Path(mod.__file__).read_text(encoding="utf-8")
    # The pre-fix anti-pattern: `jsonify({..., "error": str(e)})` with no
    # status code returns HTTP 200 alongside an empty payload — a read that
    # swallows failure into a healthy-looking empty result.
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.endswith('"error": str(e)})') or stripped.endswith('"error": str(exc)})'):
            raise AssertionError(f"empty-as-healthy error return in {mod.__name__}: {stripped}")
