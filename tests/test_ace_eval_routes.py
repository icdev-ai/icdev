# CUI // SP-CTI
"""ACE session evaluations through the routes, on a real scratch database (icx-cnf-04, PM-ACE-13).

tests/test_agent_evaluator.py and tests/test_eval_trends.py hold the scoring and
the trend query with injected connections. This file runs the routes the
console calls end to end against one SQLite file (ACE_DB_PATH):
  * POST /api/ace/sessions/<id>/eval scores a stored session and persists it (201);
  * an unknown session is a 404, and nothing is written for it;
  * GET /api/ace/sessions/<id>/eval reads back exactly what was stored;
  * GET /api/ace/evals lists it, and GET /api/ace/evals/trends counts it.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent

# The columns score_session() selects (see tests/test_agent_evaluator.py).
_SESSIONS = """
CREATE TABLE agent_loop_sessions (
    session_id TEXT PRIMARY KEY, instance_id TEXT NOT NULL DEFAULT '',
    coworker_id TEXT NOT NULL DEFAULT '', llm_function TEXT NOT NULL DEFAULT '',
    result_subtype TEXT NOT NULL DEFAULT '', turns INTEGER NOT NULL DEFAULT 0,
    total_input_tokens INTEGER NOT NULL DEFAULT 0, total_output_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0.0, messages_json TEXT NOT NULL DEFAULT '[]',
    system_prompt TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_MESSAGES = [
    {"role": "user", "content": "Summarise x.py"},
    {"role": "assistant", "content": [
        {"type": "text", "text": "Plan: read the file, then summarise it in one line."},
        {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "x.py"}}]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "def f(): pass"}]}]},
    {"role": "assistant", "content": [{"type": "text", "text": "It defines f()."}]},
]


@pytest.fixture()
def eval_db(tmp_path, monkeypatch):
    db = tmp_path / "ace_evals.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SESSIONS)
    conn.executescript((REPO / "tools/db/migrations/223_agent_evals.sql").read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO agent_loop_sessions (session_id, instance_id, coworker_id, llm_function, "
        "result_subtype, turns, total_input_tokens, total_output_tokens, total_cost_usd, messages_json) "
        "VALUES ('sess-1', 'i1', 'cw-1', 'code', 'success', 3, 120, 40, 0.02, ?)",
        (json.dumps(_MESSAGES),))
    conn.commit()
    conn.close()
    monkeypatch.setenv("ACE_DB_PATH", str(db))
    return db


@pytest.fixture()
def client(eval_db):
    bp_mod = importlib.import_module("icdev.tools.ace.blueprint")
    bp_mod._state["db_ready"] = True
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(bp_mod.ace_bp)
    if "ace_api" not in app.blueprints:
        app.register_blueprint(bp_mod.ace_api_bp)
    with app.test_client() as c:
        yield c


def _count(db, session_id):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM agent_evals WHERE session_id = ?",
                            (session_id,)).fetchone()[0]
    finally:
        conn.close()


def test_post_scores_a_stored_session_and_persists_it(client, eval_db):
    res = client.post("/api/ace/sessions/sess-1/eval", json={"max_iterations": 12})
    assert res.status_code == 201, res.get_json()
    body = res.get_json()
    assert body["eval_id"].startswith("aev-")
    assert (body["outcome"], body["done"], body["turns_used"]) == ("success", True, 3)
    assert body["efficiency_score"] == 0.75
    assert body["total_tool_calls"] == 1
    assert _count(eval_db, "sess-1") == 1
    # Re-scoring replaces the stored eval, never stacks a second one.
    assert client.post("/api/ace/sessions/sess-1/eval", json={}).status_code == 201
    assert _count(eval_db, "sess-1") == 1


def test_an_unknown_session_is_a_404_and_writes_nothing(client, eval_db):
    res = client.post("/api/ace/sessions/sess-nope/eval", json={})
    assert res.status_code == 404
    assert _count(eval_db, "sess-nope") == 0
    assert client.get("/api/ace/sessions/sess-nope/eval").status_code == 404


def test_the_read_side_returns_what_was_stored(client):
    posted = client.post("/api/ace/sessions/sess-1/eval", json={"reasoning_style": "cot"}).get_json()

    got = client.get("/api/ace/sessions/sess-1/eval").get_json()
    for key in ("outcome", "done", "turns_used", "efficiency_score", "total_tool_calls",
                "reasoning_style", "grading_version"):
        assert got[key] == posted[key], key

    listed = client.get("/api/ace/evals").get_json()
    assert [e["session_id"] for e in listed["evals"]] == ["sess-1"]
    assert listed["total"] == 1

    trends = client.get("/api/ace/evals/trends?days=7&bucket=day").get_json()
    assert (trends["days"], trends["bucket"]) == (7, "day")
    assert sum(t["count"] for t in trends["trends"]) == 1
    assert trends["trends"][0]["pct_done"] == 100.0
