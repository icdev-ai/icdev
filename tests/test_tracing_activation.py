#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tracing activation at startup (obx-trc-01, D290).

Covers the split-brain repair and the env-gated activation helper:
  (a) `from tools.observability import get_tracer, enable_tracing` succeeds
      against the top-level (previously empty) package.
  (b) With ICDEV_TRACING_ENABLED unset/true, enable_tracing_if_enabled()
      swaps the global ProxyTracer to a real (non-Null) backend and a
      start_span/end round-trip persists a row into otel_spans.
  (c) With ICDEV_TRACING_ENABLED=false the helper is a no-op and the tracer
      stays a NullTracer.

conftest forces ICDEV_STORAGE_BACKEND=sqlite, so the SQLite backend is used.
The otel_spans table is created locally per the DDL in
tools/db/init_icdev_db.py (the shared MINIMAL_ICDEV_SCHEMA does not define it).
"""

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure repo root import (mirrors test_tracer.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# (a) Top-level import must resolve now that the package is populated.
from tools.observability import (  # noqa: E402
    configure_tracer,
    enable_tracing,
    enable_tracing_if_enabled,
    get_tracer,
)
from tools.observability.tracer import NullTracer, ProxyTracer  # noqa: E402

# DDL copied from tools/db/init_icdev_db.py (~4470).
_OTEL_SPANS_DDL = """
CREATE TABLE IF NOT EXISTS otel_spans (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_span_id TEXT,
    name TEXT NOT NULL,
    kind TEXT DEFAULT 'INTERNAL',
    start_time TEXT NOT NULL,
    end_time TEXT,
    duration_ms INTEGER DEFAULT 0,
    status_code TEXT DEFAULT 'UNSET',
    status_message TEXT,
    attributes TEXT,
    events TEXT,
    agent_id TEXT,
    project_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def otel_db(tmp_path):
    """A SQLite DB file that has the otel_spans table."""
    db_path = tmp_path / "trace_icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_OTEL_SPANS_DDL)
    conn.close()
    return db_path


@pytest.fixture(autouse=True)
def _reset_proxy():
    """Isolate the global ProxyTracer singleton across tests."""
    yield
    # Restore to a Null backend so activation state does not leak.
    configure_tracer(NullTracer())


# ============================================================
# (a) Import repair
# ============================================================


def test_top_level_import_succeeds():
    """The formerly empty tools.observability package now exposes the API."""
    assert callable(get_tracer)
    assert callable(enable_tracing)
    assert callable(enable_tracing_if_enabled)
    assert isinstance(get_tracer(), ProxyTracer)


# ============================================================
# (b) Activation on (default / true) + round-trip persistence
# ============================================================


def test_helper_activates_when_toggle_unset():
    """Unset ICDEV_TRACING_ENABLED defaults ON: proxy swaps to a real backend."""
    configure_tracer(NullTracer())  # start from Null
    env = {k: v for k, v in os.environ.items() if k != "ICDEV_TRACING_ENABLED"}
    with patch.dict(os.environ, env, clear=True):
        tracer = enable_tracing_if_enabled()
    assert tracer is not None
    assert not isinstance(get_tracer().actual, NullTracer)


def test_helper_activates_when_toggle_true():
    """Explicit true also activates."""
    configure_tracer(NullTracer())
    with patch.dict(os.environ, {"ICDEV_TRACING_ENABLED": "true"}):
        tracer = enable_tracing_if_enabled()
    assert tracer is not None
    assert not isinstance(get_tracer().actual, NullTracer)


def test_activation_round_trip_persists_span(otel_db):
    """After activation, a start_span/end round-trip writes a row to otel_spans."""
    with patch.dict(os.environ, {"ICDEV_TRACING_ENABLED": "true"}):
        enable_tracing_if_enabled()

    proxy = get_tracer()
    actual = proxy.actual
    assert not isinstance(actual, NullTracer)

    # Point the live backend at the controlled DB and flush eagerly.
    actual._db_path = otel_db
    actual._buffer_size = 1

    with proxy.start_span("obx.roundtrip") as span:
        span.set_attribute("obx.key", "obx.val")
    proxy.flush()

    conn = sqlite3.connect(str(otel_db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM otel_spans WHERE name='obx.roundtrip'"
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0]["status_code"] == "OK"


# ============================================================
# (c) Activation off
# ============================================================


def test_helper_noop_when_toggle_false():
    """ICDEV_TRACING_ENABLED=false keeps the tracer Null."""
    configure_tracer(NullTracer())
    with patch.dict(os.environ, {"ICDEV_TRACING_ENABLED": "false"}):
        tracer = enable_tracing_if_enabled()
    assert tracer is None
    assert isinstance(get_tracer().actual, NullTracer)


@pytest.mark.parametrize("val", ["false", "0", "no", "FALSE", "No", " false "])
def test_helper_noop_for_falsey_values(val):
    """Falsey spellings (case/space-insensitive) all disable activation."""
    configure_tracer(NullTracer())
    with patch.dict(os.environ, {"ICDEV_TRACING_ENABLED": val}):
        tracer = enable_tracing_if_enabled()
    assert tracer is None
    assert isinstance(get_tracer().actual, NullTracer)


# ============================================================
# Entry points that must ACTIVATE tracing (obs-cov-04)
# ============================================================
#
# The machinery above works and always did. otel_spans was nevertheless empty on
# the live PostgreSQL board — measured 2026-08-02, 0 rows — because activation
# is opt-in per process and only tools/dashboard/app.py and
# tools/mcp/base_server.py ever called it.
#
# Every `tracer.start_span(...)` in tools/llm/router.py and
# tools/agent/bedrock_client.py was therefore a silent no-op inside the kanban
# scheduler: the process where most of this board's LLM traffic originates. The
# writer was never the problem — SQLiteTracer.flush() succeeds on PostgreSQL
# because translate_sql rewrites its `INSERT OR IGNORE` into an ON CONFLICT
# form (verified by inserting and reading back a probe span).
#
# Asserted against the source rather than by booting the scheduler: booting it
# needs a populated board and a free single-instance lock, while the property
# under test is only that the entry point activates tracing before it runs
# cycles.

_ENTRY_POINTS_REQUIRING_ACTIVATION = (
    "tools/genesis/kanban_scheduler.py",
    "tools/dashboard/app.py",
    "tools/mcp/base_server.py",
)


@pytest.mark.parametrize("rel_path", _ENTRY_POINTS_REQUIRING_ACTIVATION)
def test_entry_point_activates_tracing(rel_path):
    """A long-running entry point must activate tracing, or its spans vanish."""
    src = (Path(__file__).resolve().parent.parent / rel_path).read_text(
        encoding="utf-8", errors="replace"
    )
    assert "enable_tracing_if_enabled" in src, (
        f"{rel_path} does not activate tracing — every start_span() it reaches "
        f"is a no-op and otel_spans goes empty again"
    )
