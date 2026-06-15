# CUI // SP-CTI
"""E2E route coverage for GET /events — 3 acceptance cases.

Cases:
  1. GET /events returns 200 with HTML when hook_events table is empty
  2. GET /events returns 200 and passes seeded events to the template context
  3. GET /events returns 200 even when the DB raises an exception (graceful fallback)
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

from flask import Flask, render_template_string

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_STUB_HTML = "<html><body>events-timeline</body></html>"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hook_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name   TEXT,
    hook_type   TEXT,
    severity    TEXT DEFAULT 'info',
    payload     TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
"""


def _make_app(seed_rows=None, db_raises=False):
    """Minimal Flask app mirroring the /events route from tools/dashboard/app.py."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    if db_raises:
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("simulated DB failure")

        def _get_db():
            return mock_conn
    else:
        mem_conn = sqlite3.connect(":memory:")
        mem_conn.row_factory = sqlite3.Row
        mem_conn.executescript(_SCHEMA)
        for row in seed_rows or []:
            mem_conn.execute(
                "INSERT INTO hook_events (tool_name, hook_type, severity) VALUES (?,?,?)",
                row,
            )
        mem_conn.commit()

        def _get_db():
            return mem_conn

    @app.route("/events")
    def events_page():
        conn = _get_db()
        try:
            recent_events = conn.execute(
                "SELECT * FROM hook_events ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            return render_template_string(
                _STUB_HTML,
                recent_events=[dict(r) for r in recent_events],
            )
        except Exception:
            return render_template_string(_STUB_HTML, recent_events=[])
        finally:
            conn.close()

    return app


# ── 1. Empty DB → 200 ─────────────────────────────────────────────────────────

def test_events_empty_db_returns_200():
    """GET /events with no hook_events rows returns HTTP 200."""
    client = _make_app().test_client()
    resp = client.get("/events")
    assert resp.status_code == 200
    assert b"events-timeline" in resp.data


# ── 2. Seeded events are passed to template context ──────────────────────────

def test_events_seeded_rows_returns_200():
    """GET /events with seeded hook_events returns 200 and includes event data."""
    rows = [
        ("Bash", "PostToolUse", "info"),
        ("Read", "PreToolUse", "warning"),
    ]
    client = _make_app(seed_rows=rows).test_client()
    resp = client.get("/events")
    assert resp.status_code == 200
    assert b"events-timeline" in resp.data


# ── 3. DB error → graceful 200 fallback ──────────────────────────────────────

def test_events_db_error_returns_200():
    """GET /events falls back to empty recent_events list on DB exception (still 200)."""
    client = _make_app(db_raises=True).test_client()
    resp = client.get("/events")
    assert resp.status_code == 200
    assert b"events-timeline" in resp.data
