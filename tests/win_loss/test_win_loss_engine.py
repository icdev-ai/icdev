"""Pytest tests for win_loss PatternAnalyzer and WinLossEngine — 6 tests."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


from tools.win_loss.pattern_analyzer import PatternAnalyzer
from tools.win_loss.win_loss_engine import WinLossEngine

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ─── helpers ─────────────────────────────────────────────────────────────────

_WIN_LOSS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pg_win_loss_records (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    our_strengths TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _bare_conn(extra_sql: str = "") -> sqlite3.Connection:
    """In-memory SQLite with only pg_win_loss_records (no other tables)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_WIN_LOSS_TABLE_SQL + extra_sql)
    return conn


def _insert_records(conn: sqlite3.Connection, records: list[tuple[str, str]]) -> None:
    """Insert (outcome, our_strengths) pairs as win/loss records."""
    for i, (outcome, strengths) in enumerate(records):
        conn.execute(
            "INSERT INTO pg_win_loss_records (id, opportunity_id, outcome, our_strengths)"
            " VALUES (?, ?, ?, ?)",
            (f"rec-{i}", f"opp-{i}", outcome, strengths),
        )
    conn.commit()


# ─── test 1: PatternAnalyzer returns [] when table missing ───────────────────

def test_pattern_analyzer_empty_when_table_missing():
    """PatternAnalyzer._run() returns [] when pg_win_loss_records does not exist."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    analyzer = PatternAnalyzer(config={"analysis": {"min_outcomes_for_pattern": 1}})
    result = analyzer._run(conn)
    conn.close()
    assert result == []


# ─── test 2: PatternAnalyzer computes win_rate correctly ─────────────────────

def test_pattern_analyzer_computes_win_rate():
    """PatternAnalyzer._run() computes correct win_rate from fixture data."""
    conn = _bare_conn()
    # tagA: 4 won + 1 lost = 5 total → win_rate = 0.8
    _insert_records(conn, [
        ("won", "tagA"),
        ("won", "tagA"),
        ("won", "tagA"),
        ("won", "tagA"),
        ("lost", "tagA"),
    ])
    analyzer = PatternAnalyzer(config={"analysis": {"min_outcomes_for_pattern": 5}})
    results = analyzer._run(conn)
    conn.close()
    assert len(results) == 1
    assert results[0].feature_tag == "tagA"
    assert abs(results[0].win_rate - 0.8) < 1e-9


# ─── test 3: feature below min_outcomes excluded ─────────────────────────────

def test_pattern_analyzer_excludes_below_min_outcomes():
    """Features with fewer occurrences than min_outcomes_for_pattern are excluded."""
    conn = _bare_conn()
    # tagB: only 3 total occurrences, min=5 → must be excluded
    _insert_records(conn, [
        ("won", "tagB"),
        ("won", "tagB"),
        ("lost", "tagB"),
    ])
    analyzer = PatternAnalyzer(config={"analysis": {"min_outcomes_for_pattern": 5}})
    results = analyzer._run(conn)
    conn.close()
    assert results == []


# ─── test 4: WinLossEngine.run() inserts to win_loss_analysis_runs ───────────

def test_engine_run_inserts_analysis_run(icdev_db, monkeypatch):
    """WinLossEngine.run() inserts exactly one row into win_loss_analysis_runs."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    engine = WinLossEngine(
        config={"analysis": {"min_outcomes_for_pattern": 1, "auto_signal_threshold": 0.99}}
    )
    engine.run()

    conn = sqlite3.connect(str(icdev_db))
    row = conn.execute("SELECT COUNT(*) FROM win_loss_analysis_runs").fetchone()
    conn.close()
    assert row[0] == 1


# ─── test 5: high-impact feature cross-registers to creative_feature_gaps ────

def test_engine_high_impact_cross_registers_to_creative_gaps(icdev_db, monkeypatch):
    """WinLossEngine.run() writes high-impact features to creative_feature_gaps."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    # 5 wins with tagX → win_rate = 1.0, high impact_score
    conn = sqlite3.connect(str(icdev_db))
    for i in range(5):
        conn.execute(
            "INSERT INTO pg_win_loss_records (id, opportunity_id, outcome, our_strengths)"
            " VALUES (?, ?, 'won', 'tagX')",
            (f"hif-{i}", f"opp-hif-{i}"),
        )
    conn.commit()
    conn.close()

    # auto_signal_threshold=0.0 ensures every pattern triggers cross-registration
    engine = WinLossEngine(
        config={"analysis": {"min_outcomes_for_pattern": 1, "auto_signal_threshold": 0.0}}
    )
    engine.run()

    conn = sqlite3.connect(str(icdev_db))
    rows = conn.execute("SELECT feature_name FROM creative_feature_gaps").fetchall()
    conn.close()
    assert any(r[0] == "tagX" for r in rows)


# ─── test 6: CLI --json output has 'patterns_found' key ─────────────────────

def test_cli_json_output_has_patterns_found(tmp_path):
    """CLI --run --json emits valid JSON containing the 'patterns_found' key."""
    db_path = tmp_path / "cli_test.db"
    # Empty DB — engine degrades gracefully when tables are absent
    sqlite3.connect(str(db_path)).close()

    env = {
        **os.environ,
        "ICDEV_DB_PATH": str(db_path),
        "ICDEV_STORAGE_BACKEND": "sqlite",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "tools.win_loss.win_loss_engine", "--run", "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_PROJECT_ROOT),
    )
    assert proc.returncode == 0, f"CLI exited {proc.returncode}:\n{proc.stderr}"
    data = json.loads(proc.stdout)
    assert "patterns_found" in data
