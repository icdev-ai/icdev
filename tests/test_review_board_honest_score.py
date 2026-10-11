#!/usr/bin/env python3
# CUI // SP-CTI
"""rem-hyg-22: the Review Board must not score, or evidence, reviews that never ran.

Measured 2026-10-11: every reflex sat disabled in review_board_reflex_state,
`daemon.py --once` printed `"results": []`, health_scorer reported 100.0 / A,
and compliance_bridge tried to register `status='current'` cATO evidence for
six controls from that 100 -- failing on PostgreSQL only because its
`INSERT OR REPLACE` had no matching conflict target.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tools.db.storage import StorageConnection, translate_sql


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "rb.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE review_board_reflex_state (
            reflex_name TEXT PRIMARY KEY, enabled INTEGER DEFAULT 1, last_run_at TEXT,
            next_run_at TEXT, consecutive_failures INTEGER DEFAULT 0,
            circuit_breaker_open INTEGER DEFAULT 0, circuit_breaker_tripped_at TEXT,
            total_runs INTEGER DEFAULT 0, total_successes INTEGER DEFAULT 0,
            total_failures INTEGER DEFAULT 0, last_metric_value REAL, last_error TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE review_board_findings (
            id TEXT PRIMARY KEY, reflex_name TEXT, severity TEXT, category TEXT, title TEXT,
            fix_applied INTEGER DEFAULT 0, created_at TEXT);
        CREATE TABLE review_board_audit (
            id TEXT PRIMARY KEY, event_type TEXT NOT NULL, reflex_name TEXT, risk_tier TEXT,
            details TEXT, success INTEGER, duration_ms INTEGER, metric_name TEXT,
            metric_value REAL, created_at TEXT NOT NULL);
        CREATE TABLE cato_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
            control_id TEXT NOT NULL, evidence_type TEXT NOT NULL, evidence_source TEXT NOT NULL,
            evidence_path TEXT, evidence_hash TEXT, collected_at TEXT, expires_at TEXT,
            is_fresh INTEGER DEFAULT 1, freshness_check_at TEXT, status TEXT DEFAULT 'current',
            automation_frequency TEXT,
            UNIQUE(project_id, control_id, evidence_type, evidence_source));
    """)
    for name in ("sre", "qa", "security", "perf", "ux", "docs", "product", "report"):
        conn.execute("INSERT INTO review_board_reflex_state (reflex_name, enabled) VALUES (?, 0)", (name,))
    conn.commit()
    conn.close()

    def _make_conn():
        c = sqlite3.connect(str(path))
        c.row_factory = sqlite3.Row
        return StorageConnection(c, "sqlite")

    with patch("tools.review_board.health_scorer._get_connection", side_effect=_make_conn), patch(
        "tools.review_board.compliance_bridge._get_connection", side_effect=_make_conn
    ), patch("tools.review_board.daemon.get_connection", side_effect=_make_conn), patch(
        "tools.daemon.base.get_connection", side_effect=_make_conn
    ):
        yield path


def _rows(path, sql):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _record_runs(path, name, runs, successes, last_run_at):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "UPDATE review_board_reflex_state SET enabled=1, total_runs=?, total_successes=?, last_run_at=? "
        "WHERE reflex_name=?",
        (runs, successes, last_run_at, name),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# (2) empty denominator -> unmeasured, never 100
# ---------------------------------------------------------------------------
def test_no_reviews_scores_none_not_100(db):
    from tools.review_board.health_scorer import compute_health_score

    result = compute_health_score()

    assert result["score"] is None
    assert result["grade"] == "N/A"
    assert result["status"] == "unmeasured"
    assert all(v is None for v in result["breakdown"]["persona_scores"].values())
    # No fabricated row lands in the history a later evidence sync would read.
    assert _rows(db, "SELECT COUNT(*) FROM review_board_health_history")[0][0] == 0


def test_a_persona_that_never_ran_has_no_score_beside_one_that_did(db):
    from tools.review_board.health_scorer import compute_health_score

    _record_runs(db, "sre", 4, 3, _iso(datetime.now(timezone.utc)))
    result = compute_health_score()

    assert result["status"] == "measured"
    assert result["score"] is not None
    assert result["breakdown"]["persona_scores"]["sre"] == 75.0
    assert result["breakdown"]["persona_scores"]["qa"] is None
    assert _rows(db, "SELECT COUNT(*) FROM review_board_health_history")[0][0] == 1


# ---------------------------------------------------------------------------
# (3) cATO evidence: PG-native upsert on the real key, never from an empty run
# ---------------------------------------------------------------------------
def test_upsert_sql_names_the_real_unique_key_and_survives_pg_translation():
    from tools.review_board.compliance_bridge import _UPSERT_EVIDENCE_SQL

    assert "OR REPLACE" not in _UPSERT_EVIDENCE_SQL.upper()
    pg_sql = " ".join(translate_sql(_UPSERT_EVIDENCE_SQL, "postgresql").split())
    assert "ON CONFLICT (project_id, control_id, evidence_type, evidence_source) DO UPDATE SET" in pg_sql
    assert pg_sql.count("ON CONFLICT") == 1


def test_register_refuses_an_unmeasured_score(db):
    from tools.review_board.compliance_bridge import CONTROL_MAPPINGS, register_cato_evidence

    results = register_cato_evidence(None, {})

    assert {r["control_id"] for r in results} == set(CONTROL_MAPPINGS)
    assert all(r["status"] == "refused" for r in results)
    assert _rows(db, "SELECT COUNT(*) FROM cato_evidence")[0][0] == 0


def test_register_upserts_one_row_per_control_carrying_the_score(db):
    from tools.review_board.compliance_bridge import register_cato_evidence

    first = register_cato_evidence(62.0, {}, score_ref="rbh-1")
    second = register_cato_evidence(71.5, {}, score_ref="rbh-2")

    assert all(r["status"] == "registered" for r in first + second)
    rows = _rows(db, "SELECT control_id, evidence_path, status FROM cato_evidence ORDER BY control_id")
    assert len(rows) == 6
    assert all("score=71.5" in r[1] and "rbh-2" in r[1] for r in rows)
    assert all(r[2] == "current" for r in rows)


def test_sync_registers_nothing_when_no_review_ran_even_with_a_stale_100_in_history(db):
    from tools.review_board import compliance_bridge
    from tools.review_board.health_scorer import ensure_tables

    ensure_tables()
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO review_board_health_history (id, score, grade, trend, breakdown, created_at) "
        "VALUES ('rbh-old', 100.0, 'A', 'stable', '{}', ?)",
        (_iso(datetime.now(timezone.utc)),),
    )
    conn.commit()
    conn.close()

    with patch.object(compliance_bridge, "log_to_audit_trail", return_value=-1):
        result = compliance_bridge.sync_all()

    assert result["measurement"]["score"] is None
    assert all(r["status"] == "refused" for r in result["evidence"])
    assert _rows(db, "SELECT COUNT(*) FROM cato_evidence")[0][0] == 0


def test_sync_registers_six_rows_from_a_real_run(db):
    from tools.review_board import compliance_bridge
    from tools.review_board.health_scorer import compute_health_score

    _record_runs(db, "security", 2, 2, _iso(datetime.now(timezone.utc)))
    measured = compute_health_score()

    with patch.object(compliance_bridge, "log_to_audit_trail", return_value=-1):
        result = compliance_bridge.sync_all()

    assert [r["status"] for r in result["evidence"]] == ["registered"] * 6
    assert all(r["score"] == measured["score"] for r in result["evidence"])
    assert _rows(db, "SELECT COUNT(*) FROM cato_evidence")[0][0] == 6


def test_sync_refuses_when_the_last_run_is_outside_the_window(db):
    from tools.review_board import compliance_bridge
    from tools.review_board.health_scorer import compute_health_score

    _record_runs(db, "sre", 1, 1, _iso(datetime.now(timezone.utc) - timedelta(days=3)))
    compute_health_score()

    with patch.object(compliance_bridge, "log_to_audit_trail", return_value=-1):
        result = compliance_bridge.sync_all()

    assert result["measurement"]["score"] is None
    assert "last 24h" in result["measurement"]["reason"]
    assert _rows(db, "SELECT COUNT(*) FROM cato_evidence")[0][0] == 0


# ---------------------------------------------------------------------------
# (1) --once says WHY nothing ran
# ---------------------------------------------------------------------------
def test_due_pass_reports_disabled_reflexes_instead_of_an_empty_list(db):
    from tools.review_board.daemon import ReviewBoardDaemon

    config = {
        "enabled": True,
        "trust_kernel": {},
        "reflexes": {"sre": {"enabled": True, "schedule": "every 1h"}, "qa": {"enabled": True, "schedule": "every 6h"}},
    }
    daemon = ReviewBoardDaemon(config)
    results = daemon.run_due_reflexes()

    assert results == []
    assert "disabled" in daemon.last_skipped["sre"]
    assert "--enable sre" in daemon.last_skipped["sre"]
    assert "disabled" in daemon.last_skipped["qa"]


def test_temp_cleanup_never_walks_live_worktrees():
    from tools.review_board.fixers.perf_fixes import SAFE_CLEAN_DIRS

    assert not any("worktrees" in d for d in SAFE_CLEAN_DIRS)
