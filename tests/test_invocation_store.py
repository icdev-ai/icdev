# CUI // SP-CTI
"""Tests for the ``runtime_invocations`` rollup — arithmetic first.

The rollup is the whole product here: ``icdev runtime top`` and the dashboard
panel do nothing but render what :class:`InvocationStore` returns, so a wrong
average or a wrong error rate is a wrong answer everywhere at once. Every
assertion below is against a hand-computed dataset with the expected value
written out, not recomputed from the same expression the code uses.

The subtle one, and the reason ``completed`` is a reported column: ``avg()``
skips NULLs, and a ``running`` row has a NULL duration. So the average is over
COMPLETED invocations while ``calls`` counts every row, and the two have
different denominators.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.observability.invocation_store import InvocationStore

_MIGRATION = next(
    (Path(__file__).resolve().parent.parent / "tools/db/migrations")
    .glob("*_runtime_invocations/up.py")
)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """An InvocationStore over an isolated SQLite DB with migration 341 applied.

    The connection comes from ``tools.db.storage.get_connection`` rather than
    raw ``sqlite3`` on purpose: the store writes ``%s`` placeholders (PostgreSQL
    is primary) and only the StorageConnection wrapper translates them. A raw
    sqlite3 connection would make these tests assert against SQL the production
    path never runs.
    """
    db = tmp_path / "obs.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))

    import tools.db.storage as storage

    monkeypatch.setattr(storage, "DB_PATH", str(db), raising=False)
    monkeypatch.setattr(storage, "_BACKEND", "sqlite", raising=False)

    spec = importlib.util.spec_from_file_location("m_runtime_inv", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.up()

    return InvocationStore()


def _insert(rows):
    """Insert (surface, name, status, duration_ms, started_at) tuples."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        for i, (surface, name, status, duration, started) in enumerate(rows):
            conn.execute(
                "INSERT INTO runtime_invocations "
                "(id, surface, name, started_at, status, duration_ms) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (f"inv-{i:04d}", surface, name, started, status, duration),
            )
        conn.commit()
    finally:
        conn.close()


T0 = "2026-08-07T10:00:00+00:00"
T1 = "2026-08-07T11:00:00+00:00"
T2 = "2026-08-07T12:00:00+00:00"


#: mcp/rag_search : 100, 300, error 200          -> 3 calls, 1 error, avg 200, max 300
#: mcp/kg_search  : 50                           -> 1 call,  0 errors, avg 50, max 50
#: agent/builder  : 1000, running(NULL)          -> 2 calls, 0 errors, avg 1000, max 1000
#: role/reviewer  : running(NULL)                -> 1 call,  0 errors, avg None, max None
DATASET = [
    ("mcp", "rag_search", "ok", 100, T0),
    ("mcp", "rag_search", "ok", 300, T1),
    ("mcp", "rag_search", "error", 200, T2),
    ("mcp", "kg_search", "ok", 50, T2),
    ("agent", "builder", "ok", 1000, T0),
    ("agent", "builder", "running", None, T2),
    ("role", "reviewer", "running", None, T2),
]


def _by(rows, key, value):
    return next(r for r in rows if r[key] == value)


# ------------------------------------------------------------- per-surface

def test_per_surface_counts_calls_errors_and_durations(store):
    _insert(DATASET)
    rows = store.by_surface()

    assert store.last_error is None
    assert [r["surface"] for r in rows] == ["mcp", "agent", "role"], "busiest first"

    mcp = _by(rows, "surface", "mcp")
    assert mcp["calls"] == 4
    assert mcp["names"] == 2, "rag_search + kg_search"
    assert mcp["errors"] == 1
    assert mcp["running"] == 0
    assert mcp["completed"] == 4
    # (100 + 300 + 200 + 50) / 4 = 162.5
    assert mcp["avg_ms"] == 162.5
    assert mcp["max_ms"] == 300
    assert mcp["error_rate_pct"] == 25.0, "1 error of 4 calls"


def test_average_excludes_running_rows_but_calls_counts_them(store):
    """The denominator trap. agent/builder is 1000ms and one unfinished call."""
    _insert(DATASET)
    agent = _by(store.by_surface(), "surface", "agent")

    assert agent["calls"] == 2, "the running invocation is a call"
    assert agent["running"] == 1
    assert agent["completed"] == 1, "only one has a duration"
    assert agent["avg_ms"] == 1000.0, "1000/1, NOT 1000/2 = 500"
    assert agent["max_ms"] == 1000


def test_a_surface_with_nothing_finished_reports_no_duration_not_zero(store):
    """`role` has one running call. A 0ms average would read as instantaneous."""
    _insert(DATASET)
    role = _by(store.by_surface(), "surface", "role")

    assert role["calls"] == 1 and role["running"] == 1 and role["completed"] == 0
    assert role["avg_ms"] is None
    assert role["max_ms"] is None


def test_error_rate_denominator_is_calls_including_running(store):
    """2 errors + 1 running + 1 ok = 4 calls -> 50.0%, not 66.7% of completed."""
    _insert([
        ("mcp", "flaky", "error", 10, T0),
        ("mcp", "flaky", "error", 20, T0),
        ("mcp", "flaky", "ok", 30, T0),
        ("mcp", "flaky", "running", None, T0),
    ])
    mcp = _by(store.by_surface(), "surface", "mcp")
    assert mcp["calls"] == 4 and mcp["errors"] == 2
    assert mcp["error_rate_pct"] == 50.0


def test_zero_calls_is_zero_percent_not_a_division_error(store):
    assert store.by_surface() == []
    assert store.last_error is None, "empty is not broken"


def test_error_rate_rounds_to_one_decimal(store):
    """1 of 3 is 33.333...%; the column is 1dp so it must be 33.3."""
    _insert([
        ("mcp", "t", "error", 1, T0),
        ("mcp", "t", "ok", 1, T0),
        ("mcp", "t", "ok", 1, T0),
    ])
    assert _by(store.by_surface(), "surface", "mcp")["error_rate_pct"] == 33.3


# ---------------------------------------------------------------- per-name

def test_per_name_groups_by_surface_and_name(store):
    _insert(DATASET)
    rows = store.by_name()

    rag = _by(rows, "name", "rag_search")
    assert rag["surface"] == "mcp"
    assert rag["calls"] == 3 and rag["errors"] == 1
    assert rag["avg_ms"] == 200.0, "(100 + 300 + 200) / 3"
    assert rag["max_ms"] == 300
    assert rag["error_rate_pct"] == 33.3


def test_surface_filter_excludes_other_surfaces(store):
    _insert(DATASET)
    rows = store.by_name(surface="mcp")
    assert {r["surface"] for r in rows} == {"mcp"}
    assert {r["name"] for r in rows} == {"rag_search", "kg_search"}


def test_limit_is_applied_after_the_sort_not_before(store):
    """A slow tool called twice must outrank a fast tool called fifty times.

    This is why the sort is in SQL. With LIMIT 1 on a calls-sorted query and a
    client-side re-sort, `slow_tool` could never appear.
    """
    _insert(
        [("mcp", "chatty", "ok", 5, T0)] * 50
        + [("mcp", "slow_tool", "ok", 9000, T0)] * 2
    )
    top = store.by_name(order_by="duration", limit=1)
    assert [r["name"] for r in top] == ["slow_tool"]
    assert top[0]["max_ms"] == 9000

    assert [r["name"] for r in store.by_name(order_by="calls", limit=1)] == ["chatty"]


def test_sort_by_errors_surfaces_the_failing_tool(store):
    _insert(
        [("mcp", "healthy", "ok", 10, T0)] * 30
        + [("mcp", "broken", "error", 10, T0)] * 3
    )
    assert store.by_name(order_by="errors", limit=1)[0]["name"] == "broken"


def test_unknown_sort_key_falls_back_to_calls_rather_than_reaching_sql(store):
    _insert(DATASET)
    injected = "calls; DROP TABLE runtime_invocations"
    rows = store.by_name(order_by=injected)

    assert store.last_error is None
    assert [r["name"] for r in rows][0] == "rag_search", "default calls order"
    assert store.by_surface(), "the table is still there"


# ------------------------------------------------------------- time window

def test_since_is_inclusive_of_its_own_boundary(store):
    _insert(DATASET)
    # T1 rows and later: rag_search 300 + rag_search 200 + kg_search 50,
    # agent running, role running.
    mcp = _by(store.by_surface(since=T1), "surface", "mcp")
    assert mcp["calls"] == 3, "the T0 100ms call is excluded, both T1/T2 kept"
    assert mcp["avg_ms"] == pytest.approx(183.3, abs=0.1), "(300 + 200 + 50) / 3"


def test_since_after_every_row_yields_an_empty_rollup(store):
    _insert(DATASET)
    assert store.by_surface(since="2027-01-01T00:00:00+00:00") == []
    assert store.last_error is None


# ------------------------------------------------- JSON-safety and failure

def test_every_value_is_json_serializable(store):
    """PostgreSQL returns Decimal from avg(); Decimal breaks json.dumps.

    Coercion happens in the store so no caller has to know which backend it
    read from. Asserted directly on the coercion helper as well, because SQLite
    hands back a float and would not catch a regression on its own.
    """
    from decimal import Decimal

    from tools.observability.invocation_store import _num

    assert _num(Decimal("162.5")) == 162.5
    assert isinstance(_num(Decimal("162.5")), float)
    assert _num(None) is None

    _insert(DATASET)
    payload = {"by_surface": store.by_surface(), "by_name": store.by_name()}
    assert json.loads(json.dumps(payload))["by_surface"][0]["surface"] == "mcp"


def test_a_failed_query_reports_the_error_instead_of_an_empty_rollup(store):
    """Blank-because-empty and blank-because-broken must be distinguishable."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        conn.execute("DROP TABLE runtime_invocations")
        conn.commit()
    finally:
        conn.close()

    assert store.by_surface() == []
    assert store.last_error is not None
    assert "runtime_invocations" in store.last_error


def test_a_dead_connection_is_reported_not_raised(store):
    """A telemetry reader that raised into a dashboard render is worse than blank."""
    def _boom():
        raise RuntimeError("PostgreSQL unavailable")

    broken = InvocationStore(connection_factory=_boom)
    assert broken.by_surface() == []
    assert broken.by_name() == []
    assert "PostgreSQL unavailable" in (broken.last_error or "")


def test_last_error_clears_on_the_next_successful_read(store):
    """A stale error would make the dashboard show a banner forever."""
    broken = InvocationStore(connection_factory=lambda: (_ for _ in ()).throw(RuntimeError("x")))
    broken.by_surface()
    assert broken.last_error

    _insert(DATASET)
    from tools.db.storage import get_connection

    broken._connect = get_connection
    assert broken.by_surface()
    assert broken.last_error is None


def test_limit_is_capped_regardless_of_what_the_caller_asks_for(store):
    from tools.observability.invocation_store import MAX_ROWS

    _insert(DATASET)
    # Not a row-count assertion (there are only 4 groups) — the guard is that an
    # absurd limit neither raises nor reaches the database verbatim.
    assert store.by_name(limit=10 ** 9)
    assert store.last_error is None
    assert MAX_ROWS == 1000


# ------------------------------------------------- recorder API delegation

def test_recorder_summary_delegates_to_the_store(store):
    """`summary()` is the documented entry point; it must not fork the SQL."""
    from tools.observability import invocation_recorder as R

    _insert(DATASET)
    rows = R.summary(surface="mcp")
    assert {r["name"] for r in rows} == {"rag_search", "kg_search"}
    assert _by(rows, "name", "rag_search")["avg_ms"] == 200.0


# --------------------------------------------------------- CLI time window

def test_explicit_since_wins_over_relative_hours():
    from tools.cli.runtime_top import resolve_since

    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    assert resolve_since(T0, 24, now=now) == T0
    assert resolve_since(None, 2, now=now) == "2026-08-07T10:00:00+00:00"
    assert resolve_since(None, None, now=now) is None


# ------------------------------------------------------------ CLI dispatch

def test_icdev_runtime_top_is_reachable_through_the_dispatcher(store, capsys):
    """The rollup is only a product if `icdev runtime top` actually routes to it."""
    from tools.cli.__main__ import main

    _insert(DATASET)
    assert main(["runtime", "top", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] is None
    assert _by(payload["by_surface"], "surface", "mcp")["calls"] == 4
    assert _by(payload["by_name"], "name", "rag_search")["avg_ms"] == 200.0


def test_dispatcher_forwards_flags_rather_than_swallowing_them(store, capsys):
    from tools.cli.__main__ import main

    _insert(DATASET)
    assert main(["runtime", "top", "--surface", "agent", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert {r["surface"] for r in payload["by_name"]} == {"agent"}
    assert payload["surface"] == "agent"


def test_bare_runtime_prints_its_index_and_an_unknown_word_is_an_error(capsys):
    from tools.cli.__main__ import main

    assert main(["runtime"]) == 0
    assert "icdev runtime top" in capsys.readouterr().out

    assert main(["runtime", "bogus"]) == 2, "an unknown subcommand must not exit 0"


def test_runtime_top_exits_nonzero_when_the_query_failed(store, capsys):
    """A CI job piping this must be able to tell a broken read from a quiet one."""
    from tools.cli.__main__ import main
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        conn.execute("DROP TABLE runtime_invocations")
        conn.commit()
    finally:
        conn.close()

    assert main(["runtime", "top"]) == 1
    assert "query failed" in capsys.readouterr().err


def test_the_usage_index_advertises_the_subcommand():
    """An undiscoverable command is barely shipped."""
    from tools.cli.__main__ import USAGE

    assert "runtime top" in USAGE
