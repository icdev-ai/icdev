# [TEMPLATE: CUI // SP-CTI]
"""nav-sec-09: token_tracker.log_usage must attribute dashboard-originated LLM
calls to the authenticated user.

Follow-up to nav-sec-03 (PR #565): migration D177 added ``user_id`` /
``api_key_source`` to ``agent_token_usage`` and the read side
(tools/dashboard/api/usage.py) scopes per-user, but the WRITER
(tools/agent/token_tracker.py::log_usage) never populated ``user_id`` — so
production rows landed NULL and non-admin users saw empty usage pages.

These tests prove the writer now:
  1. captures ``g.current_user`` when invoked inside a Flask request context,
  2. leaves ``user_id`` NULL (no crash) in a userless context (daemon/cron), and
  3. end-to-end: rows written via the new path are returned by the usage API for
     the matching non-admin user (and filtered from other users).
"""

import sqlite3

import pytest

from tools.agent.token_tracker import log_usage
from tools.db.init_icdev_db import DASHBOARD_AUTH_ALTER_SQL, SCHEMA_SQL

try:
    from flask import Flask, g
except ImportError:  # pragma: no cover - Flask is a hard dep for the dashboard
    pytest.skip("Flask not installed", allow_module_level=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path):
    """Temporary ICDEV™ DB with the full schema + dashboard auth columns.

    Mirrors the fixture in tests/test_usage_tracking.py so the read side sees the
    same ``user_id`` / ``api_key_source`` columns the API selects.
    """
    path = tmp_path / "test_icdev.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA_SQL)
    for sql in DASHBOARD_AUTH_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # Column already exists — idempotent
    conn.commit()
    conn.close()
    return path


def _fetch_rows(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT user_id, api_key_source, input_tokens FROM agent_token_usage ORDER BY id"
        ).fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. In a request context, the authenticated user is captured
# ---------------------------------------------------------------------------


def test_log_usage_captures_request_user(db_path):
    app = Flask(__name__)
    with app.test_request_context():
        g.current_user = {"id": "user-alice", "role": "developer"}
        row_id = log_usage(
            agent_id="builder-agent",
            project_id="proj-test",
            model_id="claude-sonnet-4-6",
            input_tokens=1000,
            output_tokens=500,
            db_path=db_path,
        )
    assert row_id is not None
    rows = _fetch_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["user_id"] == "user-alice"
    # Unknown key source falls back to the schema default rather than NULL.
    assert rows[0]["api_key_source"] == "config"


def test_log_usage_request_user_as_row(db_path):
    """g.current_user may be a DB Row rather than a dict — still resolved."""
    app = Flask(__name__)
    with app.test_request_context():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        user_row = conn.execute("SELECT 'user-bob' AS id, 'developer' AS role").fetchone()
        conn.close()
        g.current_user = user_row
        log_usage(
            agent_id="builder-agent",
            project_id="proj-test",
            model_id="claude-sonnet-4-6",
            input_tokens=10,
            output_tokens=5,
            db_path=db_path,
        )
    rows = _fetch_rows(db_path)
    assert rows[0]["user_id"] == "user-bob"


# ---------------------------------------------------------------------------
# 2. Outside any request context, user_id stays NULL and nothing crashes
# ---------------------------------------------------------------------------


def test_log_usage_userless_context_is_null(db_path):
    # No Flask request context active — a daemon / cron / CLI call.
    row_id = log_usage(
        agent_id="reflex-daemon",
        project_id="proj-test",
        model_id="claude-sonnet-4-6",
        input_tokens=42,
        output_tokens=7,
        db_path=db_path,
    )
    assert row_id is not None
    rows = _fetch_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["user_id"] is None


def test_log_usage_explicit_user_wins_over_context(db_path):
    """An explicit user_id is never overridden by request-context resolution."""
    app = Flask(__name__)
    with app.test_request_context():
        g.current_user = {"id": "user-alice", "role": "developer"}
        log_usage(
            agent_id="builder-agent",
            project_id="proj-test",
            model_id="claude-sonnet-4-6",
            input_tokens=1,
            output_tokens=1,
            user_id="explicit-user",
            api_key_source="byok",
            db_path=db_path,
        )
    rows = _fetch_rows(db_path)
    assert rows[0]["user_id"] == "explicit-user"
    assert rows[0]["api_key_source"] == "byok"


# ---------------------------------------------------------------------------
# 3. End-to-end: rows written via the new path flow through the usage API
# ---------------------------------------------------------------------------


@pytest.fixture()
def usage_app(db_path, monkeypatch):
    from tools.dashboard.api.usage import usage_api

    monkeypatch.setattr("tools.dashboard.api.usage.DB_PATH", str(db_path))
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(usage_api)
    return app


def test_end_to_end_non_admin_sees_own_written_rows(db_path, usage_app):
    # Seed one row for alice via the WRITER inside her request context (auto-attributed)
    # and one row for bob explicitly — proving the read side filters correctly.
    with usage_app.test_request_context():
        g.current_user = {"id": "user-alice", "role": "developer"}
        log_usage(
            agent_id="builder-agent",
            project_id="proj-test",
            model_id="claude-sonnet-4-6",
            input_tokens=1000,
            output_tokens=500,
            db_path=db_path,
        )
    log_usage(
        agent_id="builder-agent",
        project_id="proj-test",
        model_id="claude-sonnet-4-6",
        input_tokens=9000,
        output_tokens=4000,
        user_id="user-bob",
        db_path=db_path,
    )

    # Non-admin alice hits the API — she must see only her own 1000 input tokens.
    @usage_app.before_request
    def _auth():
        g.current_user = {"id": "user-alice", "role": "developer"}

    client = usage_app.test_client()

    totals = client.get("/api/usage/totals").get_json()
    assert totals["total_input"] == 1000
    assert totals["total_requests"] == 1

    summary = client.get("/api/usage/summary").get_json()["usage"]
    assert len(summary) == 1
    assert summary[0]["user_id"] == "user-alice"
    assert summary[0]["total_input"] == 1000
