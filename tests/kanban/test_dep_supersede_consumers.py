# CUI // SP-CTI
"""The two consumers of the gating rule that a session actually TALKS to (kpr-fix-02).

``tests/kanban/test_dep_supersede.py`` covers the rule itself. This file covers
the two places a released task would still get stuck if only the dispatcher had
been fixed, because a half-fix moves a stall downstream rather than removing it:

``POST /api/kanban/tasks/<id>/move``
    the HTTP path a dispatched worker session uses to report its own completion.
    Its dependency guard refusing on a scalar the junction graph superseded is a
    refusal nothing on the board can clear — the predecessor is unrelated work.

``GET /api/kanban/tasks`` -> ``is_blocked``
    the board's own claim about why it is not draining. On 2026-08-18 it said 15
    of 16 backlog tasks were dependency-blocked while the dispatcher would have
    released four of them.

``tools/kanban/analyze_backlog.py``
    the report a human runs to ask the same question, which must not name a
    blocker dispatch never honoured.
"""
from __future__ import annotations

import sqlite3

import pytest

GATE_TITLE = "MANUAL-MODE GATE — hold this card for a human"


@pytest.fixture
def kanban_client(icdev_db, monkeypatch):
    """Flask client over the canonical kanban schema, via the storage layer.

    StorageConnection rather than a bare sqlite3 connection: runtime SQL in
    tools/dashboard/api/kanban.py is authored for PostgreSQL (``%s``) and relies
    on translate_sql to rewrite it, so talking straight to sqlite3 raises
    ``near "%": syntax error`` on every statement.
    """
    from flask import Flask

    from tools.db.storage import StorageConnection

    db_path = icdev_db

    def _fake_conn():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return StorageConnection(c, "sqlite")

    from tools.dashboard.api import kanban as kanban_mod

    monkeypatch.setattr(kanban_mod, "get_connection", _fake_conn)

    class _StubSSE:
        def broadcast(self, *a, **kw):
            pass

    monkeypatch.setattr(kanban_mod, "sse_manager", _StubSSE())
    # The verification gate is a different guard with its own suite; switch it
    # off so a 409 here can only ever mean "dependency_not_done".
    monkeypatch.setenv("ICDEV_KANBAN_VERIFY_GATE", "false")

    app = Flask(__name__)
    app.register_blueprint(kanban_mod.kanban_api)
    return app.test_client(), db_path


def _seed(db_path, rows, junction=()):
    conn = sqlite3.connect(str(db_path))
    # The list endpoint LEFT JOINs the Oracle prediction table for its annotation
    # columns. It is not part of MINIMAL_ICDEV_SCHEMA and nothing here reads it,
    # but an absent table is an OperationalError before the route reaches the
    # dependency question this file is about.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS oracle_predictions ("
        " id TEXT PRIMARY KEY, confidence REAL, prediction_text TEXT,"
        " lens_name TEXT, prediction_type TEXT)"
    )
    for tid, title, status, dep in rows:
        conn.execute(
            "INSERT INTO kanban_tasks (id, title, status, depends_on_task_id) "
            "VALUES (?,?,?,?)",
            (tid, title, status, dep),
        )
    for task_id, dep_id in junction:
        conn.execute(
            "INSERT INTO kanban_task_deps (task_id, depends_on_id, created_at) "
            "VALUES (?,?,?)",
            (task_id, dep_id, "2026-08-18T00:00:00+00:00"),
        )
    conn.commit()
    conn.close()


# ────────────────────────────────────────────────────────────────────────────
# The HTTP done-guard
# ────────────────────────────────────────────────────────────────────────────

def test_a_superseded_scalar_does_not_refuse_the_done_move(kanban_client):
    """Otherwise a finished task can never be marked done by the session that did it."""
    client, db_path = kanban_client
    _seed(
        db_path,
        [
            ("cef-rsv-01", "built", "done", None),
            ("cef-di-03", "sibling still open", "in_progress", None),
            ("cef-di-04", "freed", "in_progress", "cef-di-03"),
        ],
        junction=[("cef-di-04", "cef-rsv-01")],
    )
    r = client.post("/api/kanban/tasks/cef-di-04/move", json={"status": "done"})
    assert r.status_code == 200, r.get_data(as_text=True)


def test_an_unsatisfied_junction_dep_still_refuses_the_done_move(kanban_client):
    """Narrowed, not disarmed — this guard is defense-in-depth for the E-gate class."""
    client, db_path = kanban_client
    _seed(
        db_path,
        [
            ("real-prereq", "not finished", "in_progress", None),
            ("child-01", "child", "in_progress", None),
        ],
        junction=[("child-01", "real-prereq")],
    )
    r = client.post("/api/kanban/tasks/child-01/move", json={"status": "done"})
    assert r.status_code == 409
    body = r.get_json()
    assert body["error"] == "dependency_not_done"
    assert body["depends_on_task_id"] == "real-prereq"


def test_a_manual_gate_scalar_still_refuses_the_done_move(kanban_client):
    """A gate is a HOLD; junction rows must not release what a human held."""
    client, db_path = kanban_client
    _seed(
        db_path,
        [
            ("agov-gate-00", GATE_TITLE, "in_progress", None),
            ("done-prereq", "built", "done", None),
            ("agov-01", "held work", "in_progress", "agov-gate-00"),
        ],
        junction=[("agov-01", "done-prereq")],
    )
    r = client.post("/api/kanban/tasks/agov-01/move", json={"status": "done"})
    assert r.status_code == 409
    assert r.get_json()["depends_on_task_id"] == "agov-gate-00"


# ────────────────────────────────────────────────────────────────────────────
# The board's own claim about why it is not draining
# ────────────────────────────────────────────────────────────────────────────

def test_is_blocked_reflects_gating_deps_not_the_scalar_column(kanban_client):
    client, db_path = kanban_client
    _seed(
        db_path,
        [
            ("cef-rsv-01", "built", "done", None),
            ("cef-di-03", "sibling", "in_progress", None),
            ("cef-di-04", "freed", "backlog", "cef-di-03"),
            ("held-01", "genuinely held", "backlog", "cef-di-03"),
        ],
        junction=[("cef-di-04", "cef-rsv-01")],
    )
    r = client.get("/api/kanban/tasks")
    assert r.status_code == 200, r.get_data(as_text=True)
    by_id = {t["id"]: t for t in r.get_json()["tasks"]}
    # Same scalar parent, same status — the junction rows are the whole difference.
    assert by_id["cef-di-04"]["is_blocked"] is False
    assert by_id["held-01"]["is_blocked"] is True


# ────────────────────────────────────────────────────────────────────────────
# The report a human runs to ask the same question
# ────────────────────────────────────────────────────────────────────────────

def test_analyze_backlog_names_only_a_blocker_dispatch_honours():
    from tools.db.storage import StorageConnection
    from tools.kanban import analyze_backlog

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE kanban_tasks (
            id TEXT PRIMARY KEY, title TEXT DEFAULT '', status TEXT,
            depends_on_task_id TEXT
        );
        CREATE TABLE kanban_task_deps (
            task_id TEXT, depends_on_id TEXT, created_at TEXT
        );
        """
    )
    conn = StorageConnection(c, "sqlite")
    for tid, status, dep in [
        ("cef-rsv-01", "done", None),
        ("cef-di-03", "in_progress", None),
        ("cef-di-04", "backlog", "cef-di-03"),
        ("held-01", "backlog", "cef-di-03"),
    ]:
        conn.execute(
            "INSERT INTO kanban_tasks (id, status, depends_on_task_id) VALUES (?,?,?)",
            (tid, status, dep),
        )
    conn.execute(
        "INSERT INTO kanban_task_deps (task_id, depends_on_id, created_at) "
        "VALUES (?,?,?)",
        ("cef-di-04", "cef-rsv-01", "2026-08-18T00:00:00+00:00"),
    )
    conn.commit()

    assert analyze_backlog._deps_satisfied("cef-di-04", conn) == (True, [])
    assert analyze_backlog._deps_satisfied("held-01", conn) == (False, ["cef-di-03"])
    # And the gate carve-out the report shares with the dispatcher.
    assert analyze_backlog._scalar_is_gate("cef-di-03", conn) is False
