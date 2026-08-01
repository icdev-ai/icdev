#!/usr/bin/env python3
# CUI // SP-CTI
"""7 unit tests for tools/kanban/des_audit_logger.py (nwa-des-07)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _storage_conn(db_path):
    """Connect the way production does, via tools.db.storage.

    Patching get_connection with a RAW sqlite3 connection is the bug this
    replaces: runtime SQL is authored for PostgreSQL (%s placeholders, per
    CLAUDE.md) and the storage wrapper is what translates them to SQLite's ?.
    A raw sqlite3 connection turns every %s into `near "%": syntax error`,
    which the loggers swallow -- so they silently returned "" and the tests
    failed on an empty event id rather than on the real error.
    """
    from tools.db.storage import get_connection as _real_get_connection

    return _real_get_connection(db_path=str(db_path))


def _make_db(tmp_path):
    """Return a sqlite3 connection backed by a temp file with the DES table."""
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS des_execution_events ("
        "id TEXT PRIMARY KEY, task_id TEXT NOT NULL, event_type TEXT NOT NULL, "
        "skill_invoked TEXT, inputs_json TEXT, outputs_json TEXT, executor TEXT, "
        "dispatch_source TEXT, task_status TEXT, duration_ms INTEGER, "
        "verification_signals TEXT, queued_at TEXT, occurred_at TEXT NOT NULL, "
        "classification TEXT NOT NULL DEFAULT 'CUI')"
    )
    conn.commit()
    return db_path, conn


def _make_logger(tmp_path):
    """Return a DESAuditLogger whose get_connection() reads tmp_path DB."""
    db_path, conn = _make_db(tmp_path)
    conn.close()

    from tools.kanban.des_audit_logger import DESAuditLogger

    real_conn = sqlite3.connect(str(db_path))
    real_conn.row_factory = sqlite3.Row
    return DESAuditLogger(), db_path, real_conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_log_dispatch_inserts_dispatch_row(tmp_path):
    """log_dispatch() must insert a row with event_type='dispatch'."""
    db_path, conn = _make_db(tmp_path)
    conn.close()

    from tools.kanban.des_audit_logger import DESAuditLogger

    logger = DESAuditLogger()
    with patch(
        "tools.kanban.des_audit_logger.get_connection",
        side_effect=lambda *_a, **_kw: _storage_conn(db_path),
    ):
        event_id = logger.log_dispatch(
            task_id="task-001",
            skill="kanban_reflex",
            inputs={"title": "test"},
            executor="scheduler",
            source="auto_promote",
            queued_at="2026-04-28T00:00:00+00:00",
        )

    assert event_id != ""
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    row = conn2.execute(
        "SELECT * FROM des_execution_events WHERE id=?", (event_id,)
    ).fetchone()
    assert row is not None
    assert row["event_type"] == "dispatch"
    assert row["task_id"] == "task-001"
    conn2.close()


def test_log_completion_inserts_completion_row(tmp_path):
    """log_completion() must insert a row with event_type='completion'."""
    db_path, conn = _make_db(tmp_path)
    conn.close()

    from tools.kanban.des_audit_logger import DESAuditLogger

    logger = DESAuditLogger()
    with patch(
        "tools.kanban.des_audit_logger.get_connection",
        side_effect=lambda *_a, **_kw: _storage_conn(db_path),
    ):
        event_id = logger.log_completion(
            task_id="task-002",
            status="done",
            outputs={"exit_code": 0},
            duration_ms=1234,
        )

    assert event_id != ""
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    row = conn2.execute(
        "SELECT * FROM des_execution_events WHERE id=?", (event_id,)
    ).fetchone()
    assert row is not None
    assert row["event_type"] == "completion"
    assert row["task_status"] == "done"
    assert row["duration_ms"] == 1234
    conn2.close()


def test_log_verification_inserts_verification_row(tmp_path):
    """log_verification() must insert a row with event_type='verification'."""
    db_path, conn = _make_db(tmp_path)
    conn.close()

    from tools.kanban.des_audit_logger import DESAuditLogger

    logger = DESAuditLogger()
    signals = {"codelens": True, "coherence": True, "e2e": True, "passed": True}
    with patch(
        "tools.kanban.des_audit_logger.get_connection",
        side_effect=lambda *_a, **_kw: _storage_conn(db_path),
    ):
        event_id = logger.log_verification(task_id="task-003", signals=signals)

    assert event_id != ""
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    row = conn2.execute(
        "SELECT * FROM des_execution_events WHERE id=?", (event_id,)
    ).fetchone()
    assert row is not None
    assert row["event_type"] == "verification"
    stored = json.loads(row["verification_signals"])
    assert stored["passed"] is True
    conn2.close()


def test_log_gate_override_inserts_gate_override_row(tmp_path):
    """log_gate_override() must insert a row with event_type='gate_override'."""
    db_path, conn = _make_db(tmp_path)
    conn.close()

    from tools.kanban.des_audit_logger import DESAuditLogger

    logger = DESAuditLogger()
    with patch(
        "tools.kanban.des_audit_logger.get_connection",
        side_effect=lambda *_a, **_kw: _storage_conn(db_path),
    ):
        event_id = logger.log_gate_override(
            task_id="task-004",
            reason="manual bypass",
            operator="operator",
        )

    assert event_id != ""
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    row = conn2.execute(
        "SELECT * FROM des_execution_events WHERE id=?", (event_id,)
    ).fetchone()
    assert row is not None
    assert row["event_type"] == "gate_override"
    payload = json.loads(row["inputs_json"])
    assert payload["reason"] == "manual bypass"
    conn2.close()


def test_no_update_or_delete_issued(tmp_path):
    """_insert() must only call INSERT, never UPDATE or DELETE."""
    db_path, conn = _make_db(tmp_path)
    conn.close()

    from tools.kanban.des_audit_logger import DESAuditLogger

    executed_sql: list[str] = []

    class _RecordingConn:
        """Thin wrapper that records all SQL before delegating to real conn."""

        def __init__(self, real):
            self._real = real

        def execute(self, sql, *args, **kwargs):
            executed_sql.append(sql.strip().upper())
            return self._real.execute(sql, *args, **kwargs)

        def commit(self):
            return self._real.commit()

        def close(self):
            return self._real.close()

        def __getattr__(self, name):
            # Delegate everything else (e.g. ``_conn`` / ``_backend`` / ``cursor``)
            # to the wrapped StorageConnection so the backend-aware table_exists()
            # probe can introspect through the same connection.
            return getattr(self._real, name)

    # Wrap the STORAGE connection, not a raw sqlite3 one -- the recorder must sit
    # in front of the same translating wrapper production uses, or the %s
    # placeholders never translate and nothing is ever executed to record.
    real_conn = _storage_conn(db_path)
    recording_conn = _RecordingConn(real_conn)

    logger = DESAuditLogger()
    with patch(
        "tools.kanban.des_audit_logger.get_connection",
        return_value=recording_conn,
    ):
        logger.log_dispatch(
            task_id="task-005",
            skill="test_skill",
            inputs={},
            executor="test",
            source="test",
        )

    real_conn.close()

    for sql in executed_sql:
        assert not sql.startswith("UPDATE"), f"Unexpected UPDATE: {sql}"
        assert not sql.startswith("DELETE"), f"Unexpected DELETE: {sql}"

    insert_calls = [s for s in executed_sql if s.startswith("INSERT")]
    assert len(insert_calls) >= 1


def test_degrades_gracefully_when_table_missing(tmp_path):
    """When des_execution_events table is absent, returns '' without raising."""
    db_path = tmp_path / "empty.db"
    empty_conn = sqlite3.connect(str(db_path))
    empty_conn.close()

    from tools.kanban.des_audit_logger import DESAuditLogger

    logger = DESAuditLogger()
    empty_conn2 = sqlite3.connect(str(db_path))
    empty_conn2.row_factory = sqlite3.Row

    with patch(
        "tools.kanban.des_audit_logger.get_connection",
        return_value=empty_conn2,
    ):
        result = logger.log_dispatch(
            task_id="task-006",
            skill="skill",
            inputs={},
            executor="test",
            source="test",
        )

    empty_conn2.close()
    assert result == ""


def test_cli_query_returns_valid_json(tmp_path):
    """CLI --query --task-id returns a valid JSON list."""
    db_path, conn = _make_db(tmp_path)

    conn.execute(
        """
        INSERT INTO des_execution_events
            (id, task_id, event_type, occurred_at)
        VALUES (?, ?, ?, ?)
        """,
        ("evt-cli-001", "task-cli", "dispatch", "2026-04-28T01:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    env = os.environ.copy()
    env["ICDEV_DB_PATH"] = str(db_path)
    env["ICDEV_STORAGE_BACKEND"] = "sqlite"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "kanban" / "des_audit_logger.py"),
            "--query",
            "--task-id", "task-cli",
            "--json",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["task_id"] == "task-cli"
