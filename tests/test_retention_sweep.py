# CUI // SP-CTI
"""Tests for the config-driven retention/archival framework (crx-db-03).

Covers the framework's hard invariants against a synthetic SQLite DB:
  * PRUNE reduces a non-append-only high-churn table to keep_days, bounded by
    the per-run cap, leaving recent rows untouched.
  * An append-only table is NEVER pruned — a `prune` strategy against it is
    force-downgraded to archive-to-cold (source rows preserved).
  * dry_run (the default) makes no changes at all — no deletes, no archive
    twin, no action-log rows.
  * Fail-closed: when the append-only set cannot be resolved, prune is refused
    (archive-only), never deleting.
  * Every live action is recorded in the append-only retention_action_log.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from tools.genesis.reflexes import retention_sweep as rs

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=100)).isoformat()   # older than any keep_days here
RECENT = (NOW - timedelta(days=5)).isoformat()   # newer than any keep_days here


def _fresh_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE churn_demo (id TEXT PRIMARY KEY, created_at TEXT, classification TEXT)"
    )
    conn.execute("CREATE TABLE audit_demo (id TEXT PRIMARY KEY, created_at TEXT)")
    # 5 old + 3 recent in the churn table
    for i in range(5):
        conn.execute("INSERT INTO churn_demo VALUES (?, ?, 'CUI')", (f"old{i}", OLD))
    for i in range(3):
        conn.execute("INSERT INTO churn_demo VALUES (?, ?, 'CUI')", (f"new{i}", RECENT))
    # 4 old + 2 recent in the append-only table
    for i in range(4):
        conn.execute("INSERT INTO audit_demo VALUES (?, ?)", (f"aold{i}", OLD))
    for i in range(2):
        conn.execute("INSERT INTO audit_demo VALUES (?, ?)", (f"anew{i}", RECENT))
    conn.commit()
    return conn


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _table_exists(conn, table):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


PRUNE_POLICY = {"keep_days": 30, "strategy": "prune", "id_column": "id"}
# Deliberately declares prune against an append-only table — must be forced to archive.
AUDIT_POLICY = {"keep_days": 30, "strategy": "prune", "id_column": "id"}


def test_dry_run_makes_no_changes():
    conn = _fresh_conn()
    res = rs.apply_retention(
        conn,
        {"churn_demo": PRUNE_POLICY, "audit_demo": AUDIT_POLICY},
        append_only={"audit_demo"},
        max_rows=5000,
        dry_run=True,
        now=NOW,
    )
    assert res["dry_run"] is True
    # Nothing deleted, nothing archived, no log/twin tables materialized.
    assert _count(conn, "churn_demo") == 8
    assert _count(conn, "audit_demo") == 6
    assert not _table_exists(conn, "audit_demo_archive")
    assert not _table_exists(conn, rs._ACTION_LOG)
    # But the report still shows what WOULD match.
    assert res["tables"]["churn_demo"]["rows_matched"] == 5


def test_prune_respects_keep_days_and_leaves_recent():
    conn = _fresh_conn()
    rs.apply_retention(
        conn,
        {"churn_demo": PRUNE_POLICY},
        append_only={"audit_demo"},
        max_rows=5000,
        dry_run=False,
        now=NOW,
    )
    # All 5 old rows pruned, all 3 recent rows preserved.
    assert _count(conn, "churn_demo") == 3
    remaining = {r[0] for r in conn.execute("SELECT id FROM churn_demo").fetchall()}
    assert remaining == {"new0", "new1", "new2"}


def test_prune_bounded_by_per_run_cap():
    conn = _fresh_conn()
    res = rs.apply_retention(
        conn,
        {"churn_demo": PRUNE_POLICY},
        append_only={"audit_demo"},
        max_rows=2,   # cap below the 5 eligible old rows
        dry_run=False,
        now=NOW,
    )
    # Only `cap` rows removed this run; 8 - 2 = 6 remain.
    assert _count(conn, "churn_demo") == 6
    assert res["tables"]["churn_demo"]["rows_affected"] == 2


def test_append_only_table_never_pruned_only_archived():
    conn = _fresh_conn()
    res = rs.apply_retention(
        conn,
        {"audit_demo": AUDIT_POLICY},   # asks for prune...
        append_only={"audit_demo"},
        max_rows=5000,
        dry_run=False,
        now=NOW,
    )
    rep = res["tables"]["audit_demo"]
    # Strategy force-downgraded to archive; source table untouched (NEVER pruned).
    assert rep["strategy"] == "archive"
    assert rep["forced_archive"] is True
    assert _count(conn, "audit_demo") == 6
    # Old rows copied to the cold twin.
    assert _table_exists(conn, "audit_demo_archive")
    assert _count(conn, "audit_demo_archive") == 4


def test_fail_closed_when_append_only_unknown():
    conn = _fresh_conn()
    res = rs.apply_retention(
        conn,
        {"churn_demo": PRUNE_POLICY},
        append_only=None,   # cannot resolve the append-only set → fail closed
        max_rows=5000,
        dry_run=False,
        now=NOW,
    )
    rep = res["tables"]["churn_demo"]
    # Prune refused; treated as archive-only. Source rows all preserved.
    assert rep["strategy"] == "archive"
    assert rep["forced_archive"] is True
    assert _count(conn, "churn_demo") == 8
    assert _table_exists(conn, "churn_demo_archive")


def test_actions_are_logged_in_live_mode():
    conn = _fresh_conn()
    rs.apply_retention(
        conn,
        {"churn_demo": PRUNE_POLICY, "audit_demo": AUDIT_POLICY},
        append_only={"audit_demo"},
        max_rows=5000,
        dry_run=False,
        now=NOW,
    )
    assert _table_exists(conn, rs._ACTION_LOG)
    rows = conn.execute(
        f"SELECT table_name, strategy, forced_archive, rows_affected, dry_run "
        f"FROM {rs._ACTION_LOG} ORDER BY table_name"
    ).fetchall()
    logged = {r[0]: r for r in rows}
    assert "churn_demo" in logged and "audit_demo" in logged
    assert logged["churn_demo"][1] == "prune"
    assert logged["audit_demo"][1] == "archive"
    assert logged["audit_demo"][2] == 1   # forced_archive
    assert all(r[4] == 0 for r in rows)   # dry_run flag false for live actions


def test_excluded_tables_are_skipped():
    conn = _fresh_conn()
    res = rs.apply_retention(
        conn,
        {"churn_demo": PRUNE_POLICY},
        append_only={"audit_demo"},
        excluded={"churn_demo"},
        max_rows=5000,
        dry_run=False,
        now=NOW,
    )
    assert res["tables"]["churn_demo"] == {"skipped": "excluded"}
    assert _count(conn, "churn_demo") == 8


def test_load_append_only_tables_parses_hook():
    """The real hook must parse to a non-empty set including our log table."""
    names = rs.load_append_only_tables()
    assert names is not None
    assert "audit_trail" in names
    assert "retention_action_log" in names
    assert "hook_events" in names


def test_config_loads_seeded_policies():
    cfg = rs.load_config()
    policies = cfg.get("policies", {})
    assert "agent_execution_traces" in policies
    assert policies["hook_events"]["strategy"] == "archive"
    assert cfg.get("dry_run") is True  # safety default
