"""Tests for ACE webhook delivery (acw-evt-03).

Uses responses (HTTPretty alternative) to intercept HTTP requests and assert
correct delivery behaviour: successful POST, retry on 5xx, and DB logging.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
import responses as _resp

from icdev.tools.ace.db.init_db import SCHEMA


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WEBHOOK_URL = "http://example-webhook.test/hook"


@pytest.fixture()
def ace_db(tmp_path):
    """Temp SQLite DB with full ACE schema (includes ace_webhook_log)."""
    db_path = tmp_path / "ace_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.execute(
        "INSERT INTO ace_instances (id, name, role_id, state) VALUES (?, ?, ?, ?)",
        ("inst-test-001", "test instance", "ai_developer", "complete"),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def ace_conn(ace_db):
    conn = sqlite3.connect(str(ace_db))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _patch_canvas_conn(ace_db):
    """Redirect get_canvas_connection() to our temp SQLite DB.

    Patch at storage module level — the from-import inside webhook.py creates
    a local binding; patching the source ensures every call picks up the mock.
    """
    import sqlite3 as _sqlite3
    import importlib

    from icdev.tools.db.storage import translate_sql

    class _FakeConn:
        def __init__(self, path):
            self._conn = _sqlite3.connect(str(path))
            self._conn.row_factory = _sqlite3.Row

        def execute(self, sql, params=()):
            # Mirror get_canvas_connection's sqlite path: runtime SQL is authored
            # with PG %s placeholders; translate to ? before hitting sqlite3.
            return self._conn.execute(translate_sql(sql, "sqlite"), params)

        def executescript(self, sql):
            return self._conn.executescript(sql)

        def commit(self):
            self._conn.commit()

        def close(self):
            self._conn.close()

    def _fake_get_canvas_connection(env_var=None):
        return _FakeConn(ace_db)

    storage = importlib.import_module("icdev.tools.db.storage")
    original = storage.get_canvas_connection
    storage.get_canvas_connection = _fake_get_canvas_connection
    try:
        yield
    finally:
        storage.get_canvas_connection = original


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _log_rows(ace_conn) -> list[sqlite3.Row]:
    return ace_conn.execute(
        "SELECT * FROM ace_webhook_log WHERE instance_id = 'inst-test-001' ORDER BY id"
    ).fetchall()


# ---------------------------------------------------------------------------
# 1. Successful delivery — single attempt, status 200
# ---------------------------------------------------------------------------

@_resp.activate
def test_webhook_delivers_on_success(ace_conn):
    _resp.add(_resp.POST, WEBHOOK_URL, status=200, body="OK")

    from icdev.tools.ace import webhook

    result = webhook.deliver("inst-test-001", WEBHOOK_URL, {"event": "test"})

    assert result["status_code"] == 200
    assert result["attempt_count"] == 1

    rows = _log_rows(ace_conn)
    assert len(rows) == 1
    assert rows[0]["status_code"] == 200
    assert rows[0]["attempt_count"] == 1
    assert rows[0]["url"] == WEBHOOK_URL
    assert rows[0]["instance_id"] == "inst-test-001"


# ---------------------------------------------------------------------------
# 2. Retry on 5xx — two 500s then 200
# ---------------------------------------------------------------------------

@_resp.activate
def test_webhook_retries_on_5xx(ace_conn):
    _resp.add(_resp.POST, WEBHOOK_URL, status=500, body="Server Error")
    _resp.add(_resp.POST, WEBHOOK_URL, status=500, body="Server Error")
    _resp.add(_resp.POST, WEBHOOK_URL, status=200, body="OK")

    from icdev.tools.ace import webhook

    with patch("icdev.tools.ace.webhook.time") as mock_time:
        mock_time.sleep = MagicMock()
        result = webhook.deliver("inst-test-001", WEBHOOK_URL, {"event": "test"})

    assert result["status_code"] == 200
    assert result["attempt_count"] == 3

    # Exponential backoff: sleep(1) after attempt 1, sleep(2) after attempt 2
    assert mock_time.sleep.call_count == 2
    sleep_args = [c.args[0] for c in mock_time.sleep.call_args_list]
    assert sleep_args == [1, 2]

    rows = _log_rows(ace_conn)
    assert len(rows) == 1
    assert rows[0]["attempt_count"] == 3
    assert rows[0]["status_code"] == 200


# ---------------------------------------------------------------------------
# 3. All retries exhausted — 3x 500
# ---------------------------------------------------------------------------

@_resp.activate
def test_webhook_exhausts_retries(ace_conn):
    for _ in range(3):
        _resp.add(_resp.POST, WEBHOOK_URL, status=503, body="Unavailable")

    from icdev.tools.ace import webhook

    with patch("icdev.tools.ace.webhook.time") as mock_time:
        mock_time.sleep = MagicMock()
        result = webhook.deliver("inst-test-001", WEBHOOK_URL, {"event": "test"})

    assert result["status_code"] == 503
    assert result["attempt_count"] == 3

    rows = _log_rows(ace_conn)
    assert len(rows) == 1
    assert rows[0]["status_code"] == 503
    assert rows[0]["attempt_count"] == 3


# ---------------------------------------------------------------------------
# 4. 4xx is not retried
# ---------------------------------------------------------------------------

@_resp.activate
def test_webhook_no_retry_on_4xx(ace_conn):
    _resp.add(_resp.POST, WEBHOOK_URL, status=404, body="Not Found")

    from icdev.tools.ace import webhook

    with patch("icdev.tools.ace.webhook.time") as mock_time:
        mock_time.sleep = MagicMock()
        result = webhook.deliver("inst-test-001", WEBHOOK_URL, {"event": "test"})

    assert result["status_code"] == 404
    assert result["attempt_count"] == 1
    mock_time.sleep.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Payload sent to endpoint is well-formed JSON
# ---------------------------------------------------------------------------

@_resp.activate
def test_webhook_posts_json_payload(ace_conn):
    _resp.add(_resp.POST, WEBHOOK_URL, status=200, body="OK")

    from icdev.tools.ace import webhook

    payload = {"event": "ace.instance.complete", "instance_id": "inst-test-001"}
    webhook.deliver("inst-test-001", WEBHOOK_URL, payload)

    assert len(_resp.calls) == 1
    sent_body = json.loads(_resp.calls[0].request.body)
    assert sent_body["event"] == "ace.instance.complete"
    assert sent_body["instance_id"] == "inst-test-001"
    assert "application/json" in _resp.calls[0].request.headers["Content-Type"]


# ---------------------------------------------------------------------------
# 6. Controller._deliver_webhook calls webhook.deliver
# ---------------------------------------------------------------------------

@_resp.activate
def test_controller_deliver_webhook_integration(ace_conn):
    _resp.add(_resp.POST, WEBHOOK_URL, status=200, body="OK")

    from icdev.tools.ace.controller import ACEController

    ACEController._deliver_webhook("inst-test-001", WEBHOOK_URL)

    assert len(_resp.calls) == 1
    sent_body = json.loads(_resp.calls[0].request.body)
    assert sent_body["event"] == "ace.instance.complete"
    assert sent_body["instance_id"] == "inst-test-001"
