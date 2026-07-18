# CUI // SP-CTI
"""PG-dialect regression guard for tools/agent/mailbox.py::receive().

Live ACE runs repeatedly logged::

    translate_sql: bare ? placeholder detected in SQL — use %s for psycopg2
    directly. SQL: SELECT * FROM agent_mailbox WHERE to_agent_id = ? AND
    read_at IS NULL AND message_type = ? ORDER BY priority DESC, creat...

The mailbox ``receive()`` query was authored with SQLite-style ``?`` markers and
leaned on ``translate_sql`` to rewrite them to ``%s`` on the PostgreSQL path —
exactly the load-bearing runtime shim the pgp-tx-03 gate forbids (CLAUDE.md:
``translate_sql`` is an init/seed/migrate fallback ONLY). This test pins the
fix: the SQL that ``receive()`` emits must already be psycopg2-native, so
pushing it through the PG translator raises no bare-``?`` warning.
"""
from __future__ import annotations

import logging
import sqlite3

import pytest

import tools.agent.mailbox as mailbox
from tools.db.storage import get_connection, translate_sql

_MAILBOX_DDL = """
CREATE TABLE IF NOT EXISTS agent_mailbox (
    id TEXT PRIMARY KEY,
    from_agent_id TEXT NOT NULL,
    to_agent_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    priority INTEGER DEFAULT 5,
    in_reply_to TEXT,
    hmac_signature TEXT NOT NULL,
    read_at TIMESTAMP,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Substring of the warning translate_sql emits on the PG path (tools/db/storage.py).
_BARE_Q_WARNING = "bare ? placeholder detected"


class _RecordingConn:
    """Delegating wrapper that records every SQL string passed to execute()."""

    def __init__(self, inner, sink):
        self._inner = inner
        self._sink = sink

    def execute(self, sql, params=None):
        self._sink.append(sql)
        return self._inner.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture
def mailbox_db(tmp_path, monkeypatch):
    """Tmp SQLite DB with the agent_mailbox table; recording _get_db patched in.

    Returns (db_path, sink) where ``sink`` is the list of SQL strings executed
    through mailbox._get_db connections.
    """
    db_path = tmp_path / "icdev.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(_MAILBOX_DDL)
    raw.commit()
    raw.close()

    # Keep the audit trail out of the real DB during the test.
    monkeypatch.setattr(mailbox, "audit_log_event", lambda **kwargs: None)

    sink: list[str] = []
    _resolved = str(db_path)

    def _factory(db_path=None):
        # Ignore whatever mailbox passes (it forwards its own None); always
        # bind the fixture's tmp DB captured in the closure.
        return _RecordingConn(get_connection(db_path=_resolved), sink)

    monkeypatch.setattr(mailbox, "_get_db", _factory)
    return _resolved, sink


def _select_statements(sink):
    return [
        s
        for s in sink
        if "FROM agent_mailbox" in s and s.strip().upper().startswith("SELECT")
    ]


def test_receive_filters_by_type_and_returns_only_matching(mailbox_db):
    """End-to-end: receive() with a type filter exercises all three placeholders."""
    _, sink = mailbox_db

    mailbox.send("planner", "orchestrator", "request", "hello", "body-1", priority=7)
    mailbox.send("planner", "orchestrator", "notification", "other", "body-2", priority=3)

    sink.clear()  # isolate the receive() query from the prior INSERTs
    results = mailbox.receive(
        agent_id="orchestrator", unread_only=True, message_type="request", limit=10
    )

    subjects = [r["subject"] for r in results]
    assert subjects == ["hello"], f"expected only the request message, got {subjects}"


def test_receive_query_carries_no_bare_placeholder(mailbox_db):
    """The runtime SELECT must be psycopg2-native — no bare ? for translate_sql to rewrite."""
    _, sink = mailbox_db

    sink.clear()
    mailbox.receive(agent_id="orchestrator", unread_only=True, message_type="request", limit=5)

    selects = _select_statements(sink)
    assert selects, "receive() issued no SELECT against agent_mailbox"
    for sql in selects:
        assert "?" not in sql, f"bare ? placeholder in runtime mailbox query: {sql!r}"


def test_receive_sql_is_pg_clean_no_translate_warning(mailbox_db, caplog):
    """Pushing the emitted SQL through the PG translator raises no bare-? warning."""
    _, sink = mailbox_db

    sink.clear()
    # unread_only + message_type filter reproduces the exact shape from the live log.
    mailbox.receive(agent_id="orchestrator", unread_only=True, message_type="request", limit=5)
    selects = _select_statements(sink)
    assert selects, "receive() issued no SELECT against agent_mailbox"

    with caplog.at_level(logging.WARNING):
        for sql in selects:
            translated = translate_sql(sql, "postgresql")
            # PG-native placeholders survive translation unchanged.
            assert "?" not in translated

    assert _BARE_Q_WARNING not in caplog.text, (
        "translate_sql emitted a bare-? warning for the mailbox query path — "
        f"the runtime SQL still relies on the ?→%s shim. Captured: {caplog.text}"
    )
