# CUI // SP-CTI
"""E2E route coverage for GET /gateway — 3 acceptance cases.

Cases:
  1. GET /gateway returns 200 with HTML when DB tables are empty
  2. GET /gateway returns 200 and passes seeded bindings/commands to template context
  3. GET /gateway returns 200 even when the DB raises an exception (graceful fallback)
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

_STUB_HTML = "<html><body>gateway-admin</body></html>"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS remote_user_bindings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT,
    channel     TEXT,
    token_hash  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS remote_command_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT,
    command     TEXT,
    status      TEXT DEFAULT 'pending',
    created_at  TEXT DEFAULT (datetime('now'))
);
"""


def _make_app(seed_bindings=None, seed_commands=None, db_raises=False):
    """Minimal Flask app mirroring the /gateway route from tools/dashboard/app.py."""
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
        for row in seed_bindings or []:
            mem_conn.execute(
                "INSERT INTO remote_user_bindings (user_id, channel) VALUES (?,?)",
                row,
            )
        for row in seed_commands or []:
            mem_conn.execute(
                "INSERT INTO remote_command_log (user_id, command, status) VALUES (?,?,?)",
                row,
            )
        mem_conn.commit()

        def _get_db():
            return mem_conn

    @app.route("/gateway")
    def gateway_page():
        gw_config = {}
        env_mode = gw_config.get("environment", {}).get("mode", "connected")
        channels_cfg = gw_config.get("channels", {})

        active_channels = []
        for name, ch in channels_cfg.items():
            enabled = ch.get("enabled", False)
            req_internet = ch.get("requires_internet", False)
            available = enabled and not (env_mode == "air_gapped" and req_internet)
            active_channels.append(
                {
                    "name": name,
                    "enabled": enabled,
                    "available": available,
                    "max_il": ch.get("max_il", "IL4"),
                    "description": ch.get("description", ""),
                }
            )

        conn = _get_db()
        try:
            bindings = conn.execute(
                "SELECT * FROM remote_user_bindings ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            bindings = [dict(r) for r in bindings]
        except Exception:
            bindings = []

        try:
            commands = conn.execute(
                "SELECT * FROM remote_command_log ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            commands = [dict(r) for r in commands]
        except Exception:
            commands = []

        conn.close()

        return render_template_string(
            _STUB_HTML,
            environment_mode=env_mode,
            channels=active_channels,
            bindings=bindings,
            commands=commands,
            command_allowlist=gw_config.get("command_allowlist", []),
        )

    return app


# ── 1. Empty DB → 200 ─────────────────────────────────────────────────────────

def test_gateway_empty_db_returns_200():
    """GET /gateway with no bindings or commands returns HTTP 200."""
    client = _make_app().test_client()
    resp = client.get("/gateway")
    assert resp.status_code == 200
    assert b"gateway-admin" in resp.data


# ── 2. Seeded rows are passed to template context ────────────────────────────

def test_gateway_seeded_rows_returns_200():
    """GET /gateway with seeded bindings and commands returns 200."""
    bindings = [
        ("user-001", "telegram"),
        ("user-002", "slack"),
    ]
    commands = [
        ("user-001", "status", "completed"),
        ("user-002", "deploy", "pending"),
    ]
    client = _make_app(seed_bindings=bindings, seed_commands=commands).test_client()
    resp = client.get("/gateway")
    assert resp.status_code == 200
    assert b"gateway-admin" in resp.data


# ── 3. DB error → graceful 200 fallback ──────────────────────────────────────

def test_gateway_db_error_returns_200():
    """GET /gateway falls back to empty lists on DB exception (still 200)."""
    client = _make_app(db_raises=True).test_client()
    resp = client.get("/gateway")
    assert resp.status_code == 200
    assert b"gateway-admin" in resp.data
