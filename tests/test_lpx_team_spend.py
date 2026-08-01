# CUI // SP-CTI
"""lpx-teams-03 — per-team spend attribution from ttx_api_log tests.

Proves a facilitator can answer "what did each team spend this exercise" from a
single-store query: token/cost columns on ttx_api_log, populated at the existing
log_api_receipt hook, summed per team (including zero-call teams).
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()

    # gameday db.py migrate() must run against this DB (process-global guard).
    gd = importlib.import_module("apps.ai_gameday.db")
    gd._migrated = False
    gd.migrate()

    engine_mod = importlib.import_module("tools.ttx.engine")
    spend_mod = importlib.import_module("tools.ttx.team_spend")
    return engine_mod, spend_mod


def _seed(n_teams=3):
    from tools.db.storage import get_connection

    c = get_connection()
    c.execute("INSERT INTO ttx_sessions (scenario_slug, join_code) VALUES (%s, %s)", ("scn", "JX1"))
    c.commit()
    sid = dict(c.execute("SELECT session_id FROM ttx_sessions ORDER BY session_id DESC").fetchone())["session_id"]
    tids = []
    for i in range(n_teams):
        c.execute("INSERT INTO ttx_teams (session_id, team_name, join_code) VALUES (%s, %s, %s)",
                  (sid, f"team{i}", f"T{sid}-{i}"))
        c.commit()
        tids.append(dict(c.execute("SELECT team_id FROM ttx_teams ORDER BY team_id DESC").fetchone())["team_id"])
    return sid, tids


def test_columns_exist_after_migrate(env):
    from tools.db.storage import column_exists, get_connection

    c = get_connection()
    assert column_exists(c, "ttx_api_log", "token_count")
    assert column_exists(c, "ttx_api_log", "cost_usd")


def test_receipt_records_token_and_cost(env):
    engine_mod, spend_mod = env
    sid, tids = _seed(3)
    eng = engine_mod.GameDayEngine() if hasattr(engine_mod, "GameDayEngine") else engine_mod.TTXEngine()
    eng.log_api_receipt(sid, tids[0], "tool_a", "/x", "call-1", "h1", token_count=1200, cost_usd=0.05)
    eng.log_api_receipt(sid, tids[0], "tool_b", "/y", "call-2", "h2", token_count=800, cost_usd=0.03)
    eng.log_api_receipt(sid, tids[1], "tool_a", "/x", "call-3", "h3", token_count=500, cost_usd=0.02)

    report = {r["team_id"]: r for r in spend_mod.team_spend_report(sid)}
    assert report[tids[0]]["call_count"] == 2
    assert report[tids[0]]["total_tokens"] == 2000
    assert abs(report[tids[0]]["total_cost_usd"] - 0.08) < 1e-9
    assert report[tids[1]]["total_cost_usd"] == 0.02


def test_zero_call_teams_included(env):
    engine_mod, spend_mod = env
    sid, tids = _seed(3)
    eng = engine_mod.GameDayEngine() if hasattr(engine_mod, "GameDayEngine") else engine_mod.TTXEngine()
    eng.log_api_receipt(sid, tids[0], "tool_a", "/x", "c1", "h", token_count=100, cost_usd=0.01)
    report = spend_mod.team_spend_report(sid)
    assert len(report) == 3  # all teams present, even with zero calls
    quiet = [r for r in report if r["team_id"] in (tids[1], tids[2])]
    assert all(r["call_count"] == 0 and r["total_cost_usd"] == 0.0 for r in quiet)


def test_backward_compatible_default_zero(env):
    engine_mod, spend_mod = env
    sid, tids = _seed(1)
    eng = engine_mod.GameDayEngine() if hasattr(engine_mod, "GameDayEngine") else engine_mod.TTXEngine()
    # Old-style call without token/cost still works, defaults to 0.
    eng.log_api_receipt(sid, tids[0], "tool_a", "/x", "c1", "h")
    report = spend_mod.team_spend_report(sid)
    assert report[0]["call_count"] == 1
    assert report[0]["total_tokens"] == 0
    assert report[0]["total_cost_usd"] == 0.0


def test_session_total_rollup(env):
    engine_mod, spend_mod = env
    sid, tids = _seed(2)
    eng = engine_mod.GameDayEngine() if hasattr(engine_mod, "GameDayEngine") else engine_mod.TTXEngine()
    eng.log_api_receipt(sid, tids[0], "a", "/x", "c1", "h", token_count=100, cost_usd=0.10)
    eng.log_api_receipt(sid, tids[1], "a", "/x", "c2", "h", token_count=200, cost_usd=0.20)
    total = spend_mod.session_spend_total(sid)
    assert total["total_calls"] == 2
    assert total["total_tokens"] == 300
    assert abs(total["total_cost_usd"] - 0.30) < 1e-9
