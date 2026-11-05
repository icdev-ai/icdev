#!/usr/bin/env python3
# CUI // SP-CTI
"""AITelemetryLogger on PostgreSQL must not depend on a SQLite FILE (owui-api-04).

Every method guarded itself with ``if not self._db_path.exists(): return ...``.
That path is the SQLite file; on the PostgreSQL backend ``get_connection``
ignores it, so a PG-only deployment without a stray ``data/icdev.db`` (a fresh
container, a new worktree) wrote ZERO ``ai_telemetry`` rows and read back an
empty ledger -- with nothing logged. Found by owui-api-01's live smoke.

The connection is stubbed so the test asserts the guard, not a database.
"""

import logging
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.security import ai_telemetry_logger as mod  # noqa: E402


class _Cursor:
    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _Conn:
    def __init__(self, sink):
        self.sink = sink

    def execute(self, sql, params=()):
        self.sink.append(sql)
        return _Cursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def stub_conn(monkeypatch):
    executed = []
    monkeypatch.setattr(mod, "get_connection", lambda *a, **k: _Conn(executed))
    return executed


def _log(tmp_path):
    return mod.AITelemetryLogger(db_path=tmp_path / "absent.db").log_ai_interaction(
        model_id="m", provider="ollama", prompt_hash="p", response_hash="r",
        input_tokens=1, output_tokens=1, latency_ms=1.0, function="openai_compat_local")


def test_pg_backend_writes_without_a_sqlite_file(monkeypatch, tmp_path, stub_conn):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
    entry_id = _log(tmp_path)
    assert entry_id, "a PG deployment with no data/icdev.db silently dropped the row"
    assert any("INSERT INTO ai_telemetry" in s for s in stub_conn)


def test_sqlite_backend_without_its_file_still_skips_but_says_so(monkeypatch, tmp_path, stub_conn, caplog):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    with caplog.at_level(logging.WARNING):
        assert _log(tmp_path) is None
    assert stub_conn == []
    assert "absent.db" in caplog.text


def test_pg_backend_readers_do_not_report_an_empty_ledger(monkeypatch, tmp_path, stub_conn):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
    logger_inst = mod.AITelemetryLogger(db_path=tmp_path / "absent.db")
    logger_inst.detect_anomalies()
    assert stub_conn, "detect_anomalies returned before querying a PG database"
