# CUI // SP-CTI
"""Tests for AuditStore + `icdev audit tail`.

Uses a real SQLite database rather than mocks: the whole point of the store is
that it produces correct SQL against a live schema, and a mocked connection
would assert the shape of the query instead of the truth of the answer.
"""
from __future__ import annotations

import json
import sqlite3
import sys

import pytest

from tools.audit.store import ALL_SOURCES, AuditFilter, AuditStore
from tools.cli import audit_tail


class _PgStyleSqlite:
    """Wrap sqlite3 so it accepts the PostgreSQL-style ``%s`` the store emits.

    A subclass/attribute patch is not possible — ``sqlite3.Connection.execute``
    is read-only — so this delegates instead. It mirrors what
    ``tools.db.storage`` does for the real SQLite backend.
    """

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return self._conn.execute(sql.replace("%s", "?"), params)

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ---------------------------------------------------------------- fixtures

@pytest.fixture()
def feed_db(tmp_path):
    """A SQLite DB with both feed tables and interleaved rows.

    Timestamps interleave the two sources on purpose so the merge ordering is
    actually exercised — if tail() concatenated instead of merging, the audit
    rows and hook rows would come back in two blocks and the assertions fail.
    """
    path = tmp_path / "feed.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE audit_trail (id INTEGER PRIMARY KEY, project_id TEXT, "
        "event_type TEXT, actor TEXT, action TEXT, details TEXT, "
        "classification TEXT, session_id TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE hook_events (id INTEGER PRIMARY KEY, session_id TEXT, "
        "hook_type TEXT, tool_name TEXT, project_id TEXT, payload TEXT, "
        "classification TEXT, signature TEXT, created_at TEXT, severity TEXT, "
        "message TEXT)"
    )
    conn.executemany(
        "INSERT INTO audit_trail (id, project_id, event_type, actor, action, "
        "details, classification, session_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (1, "proj-a", "build_started", "alice", "build.start", "", "CUI", "s1",
             "2026-08-02T10:00:00+00:00"),
            (2, "proj-b", "build_failed", "bob", "build.fail", "", "CUI", "s2",
             "2026-08-02T10:00:02+00:00"),
            (3, "proj-a", "build_started", "alice", "build.start", "", "CUI", "s1",
             "2026-08-02T10:00:04+00:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO hook_events (id, session_id, hook_type, tool_name, project_id, "
        "payload, classification, signature, created_at, severity, message) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "s1", "pre_tool_use", "Bash", "proj-a", None, "CUI", "",
             "2026-08-02T10:00:01+00:00", "info", ""),
            (2, "s3", "post_tool_use", "Edit", "proj-c", None, "CUI", "",
             "2026-08-02T10:00:03+00:00", "high", "edit failed"),
        ],
    )
    conn.commit()
    conn.close()

    return lambda: _PgStyleSqlite(str(path))


@pytest.fixture()
def store(feed_db):
    return AuditStore(connection_factory=feed_db)


@pytest.fixture()
def runtime_db(tmp_path):
    """A SQLite DB shaped exactly like migration 341's ``runtime_invocations``.

    Column list and types are copied from the migration DDL rather than
    minimised, so a column renamed there fails these tests instead of silently
    reading NULL. Rows cover the states the reader has to distinguish: a closed
    ok row, a closed error row, a still-``running`` row with no
    ``completed_at``/``duration_ms``, a child row with a ``parent_id``, and a
    row whose ``arg_keys`` is NULL because its args were not a mapping.
    """
    path = tmp_path / "runtime.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE runtime_invocations ("
        " id TEXT PRIMARY KEY, surface TEXT NOT NULL, name TEXT NOT NULL,"
        " session_id TEXT, project_id TEXT, parent_id TEXT,"
        " started_at TEXT NOT NULL, completed_at TEXT, duration_ms INTEGER,"
        " status TEXT NOT NULL DEFAULT 'running', error_class TEXT,"
        " error_message TEXT, arg_keys TEXT, classification TEXT DEFAULT 'CUI',"
        " created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.executemany(
        "INSERT INTO runtime_invocations (id, surface, name, session_id, "
        "project_id, parent_id, started_at, completed_at, duration_ms, status, "
        "error_class, error_message, arg_keys, classification, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("inv-1", "mcp", "rag_search", "s1", "proj-a", None,
             "2026-08-02T10:00:00+00:00", "2026-08-02T10:00:01+00:00", 1200,
             "ok", None, None, '["query", "limit"]', "CUI",
             "2026-08-02T10:00:00+00:00"),
            ("inv-2", "mcp", "rag_search", "s1", "proj-a", None,
             "2026-08-02T10:00:02+00:00", "2026-08-02T10:00:02+00:00", 40,
             "error", "ValueError", "bad query", '["query"]', "CUI",
             "2026-08-02T10:00:02+00:00"),
            ("inv-3", "agent", "builder", "s2", "proj-b", None,
             "2026-08-02T10:00:04+00:00", None, None, "running", None, None,
             None, "CUI", "2026-08-02T10:00:04+00:00"),
            ("inv-4", "role", "reviewer", "s2", "proj-b", "inv-3",
             "2026-08-02T10:00:03+00:00", "2026-08-02T10:00:05+00:00", 2000,
             "ok", None, None, "not-json", "CUI", "2026-08-02T10:00:03+00:00"),
        ],
    )
    conn.commit()
    conn.close()
    return lambda: _PgStyleSqlite(str(path))


@pytest.fixture()
def runtime_store(runtime_db):
    return AuditStore(connection_factory=runtime_db)


# ---------------------------------------------------------------- AuditStore

def test_tail_merges_both_sources_newest_first(store):
    rows = store.tail(AuditFilter(limit=10))
    assert len(rows) == 5
    times = [r["created_at"] for r in rows]
    assert times == sorted(times, reverse=True), "must be newest-first"
    assert {r["source"] for r in rows} == set(ALL_SOURCES), "both sources present"


def test_tail_interleaves_rather_than_concatenates(store):
    """The two sources must be genuinely merged by time, not appended."""
    rows = store.tail(AuditFilter(limit=10))
    sources = [r["source"] for r in rows]
    # Ordered by time the sources alternate; a concatenation would group them.
    assert sources != sorted(sources), "sources appear grouped — not merged"


def test_limit_is_applied_after_the_merge(store):
    rows = store.tail(AuditFilter(limit=2))
    assert len(rows) == 2
    assert rows[0]["created_at"] == "2026-08-02T10:00:04+00:00"


def test_filter_by_project(store):
    rows = store.tail(AuditFilter(project_id="proj-a", limit=10))
    assert rows, "expected proj-a rows"
    assert all(r["project_id"] == "proj-a" for r in rows)


def test_filter_by_event_type_spans_both_naming_columns(store):
    """event_type filters audit_trail.event_type AND hook_events.hook_type."""
    assert all(r["event_type"] == "build_started"
               for r in store.tail(AuditFilter(event_types=["build_started"], limit=10)))
    hook = store.tail(AuditFilter(event_types=["pre_tool_use"], limit=10))
    assert len(hook) == 1 and hook[0]["source"] == "hook_events"


def test_filter_by_since_is_strictly_newer(store):
    rows = store.tail(AuditFilter(since="2026-08-02T10:00:02+00:00", limit=10))
    assert [r["created_at"] for r in rows] == [
        "2026-08-02T10:00:04+00:00",
        "2026-08-02T10:00:03+00:00",
    ]


def test_actor_filter_excludes_hook_events(store):
    """hook_events has no actor column — it must drop out, not match blindly."""
    rows = store.tail(AuditFilter(actor="alice", limit=10))
    assert rows and all(r["source"] == "audit_trail" for r in rows)
    assert all(r["actor"] == "alice" for r in rows)


def test_source_selection(store):
    only_hooks = store.tail(AuditFilter(sources=["hook_events"], limit=10))
    assert only_hooks and all(r["source"] == "hook_events" for r in only_hooks)


def test_severity_is_carried_from_hook_events(store):
    rows = store.tail(AuditFilter(sources=["hook_events"], limit=10))
    assert any(r["severity"] == "high" for r in rows)


def test_missing_table_degrades_instead_of_raising(tmp_path):
    """audit_trail present, hook_events absent — return what exists."""
    path = tmp_path / "partial.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE audit_trail (id INTEGER PRIMARY KEY, project_id TEXT, "
        "event_type TEXT, actor TEXT, action TEXT, details TEXT, "
        "classification TEXT, session_id TEXT, created_at TEXT)"
    )
    conn.execute("INSERT INTO audit_trail (id, event_type, actor, action, created_at) "
                 "VALUES (1,'e','a','act','2026-08-02T10:00:00+00:00')")
    conn.commit()
    conn.close()

    def factory():
        return _PgStyleSqlite(str(path))

    rows = AuditStore(connection_factory=factory).tail(AuditFilter(limit=10))
    assert len(rows) == 1 and rows[0]["source"] == "audit_trail"


def test_event_types_counts_are_descending(store):
    types = store.event_types()
    counts = [t["count"] for t in types]
    assert counts == sorted(counts, reverse=True)
    assert dict((t["event_type"], t["count"]) for t in types)["build_started"] == 2


# ------------------------------------------- AuditStore.read_runtime_invocations

def test_read_runtime_invocations_returns_rows_newest_first(runtime_store):
    """The acceptance criterion: a plain call returns rows without error."""
    rows = runtime_store.read_runtime_invocations(limit=10)
    assert len(rows) == 4
    stamps = [r["started_at"] for r in rows]
    assert stamps == sorted(stamps, reverse=True), "must be newest-first"
    assert {r["source"] for r in rows} == {"runtime_invocations"}


def test_read_runtime_invocations_exposes_every_migration_column(runtime_store):
    """A dropped column would otherwise show up only at a distant call site."""
    row = runtime_store.read_runtime_invocations(limit=1, name="rag_search",
                                                 status="ok")[0]
    assert row["id"] == "inv-1"
    assert row["surface"] == "mcp"
    assert row["session_id"] == "s1"
    assert row["project_id"] == "proj-a"
    assert row["duration_ms"] == 1200
    assert row["completed_at"] == "2026-08-02T10:00:01+00:00"
    assert row["classification"] == "CUI"


def test_limit_is_applied_to_runtime_rows(runtime_store):
    rows = runtime_store.read_runtime_invocations(limit=2)
    assert len(rows) == 2
    assert rows[0]["id"] == "inv-3"


def test_runtime_filters_are_and_combined(runtime_store):
    assert [r["id"] for r in runtime_store.read_runtime_invocations(surface="mcp")] \
        == ["inv-2", "inv-1"]
    assert [r["id"] for r in
            runtime_store.read_runtime_invocations(surface="mcp", status="error")] \
        == ["inv-2"]
    assert runtime_store.read_runtime_invocations(surface="mcp", status="nope") == []


def test_runtime_since_filter_is_strictly_newer(runtime_store):
    rows = runtime_store.read_runtime_invocations(since="2026-08-02T10:00:02+00:00")
    assert [r["id"] for r in rows] == ["inv-3", "inv-4"]


def test_runtime_parent_id_filter_finds_child_steps(runtime_store):
    """parent_id is how a role step is tied to the run that issued it."""
    rows = runtime_store.read_runtime_invocations(parent_id="inv-3")
    assert [r["id"] for r in rows] == ["inv-4"]


def test_min_duration_filter_excludes_running_rows(runtime_store):
    """A running row has NULL duration_ms; SQL NULL must not pass >=."""
    rows = runtime_store.read_runtime_invocations(min_duration_ms=1000)
    assert [r["id"] for r in rows] == ["inv-4", "inv-1"]  # newest-first by started_at
    assert "inv-3" not in [r["id"] for r in rows], "NULL duration must not match"


def test_none_valued_filters_are_dropped_not_matched_against_null(runtime_store):
    """So a caller can forward optional CLI flags without branching.

    ``project_id=None`` must mean "no filter", not ``project_id = NULL`` — the
    latter matches nothing in SQL and would look like an empty table.
    """
    assert len(runtime_store.read_runtime_invocations(project_id=None,
                                                      surface=None)) == 4


def test_arg_keys_are_decoded_to_a_list(runtime_store):
    by_id = {r["id"]: r for r in runtime_store.read_runtime_invocations()}
    assert by_id["inv-1"]["arg_keys"] == ["query", "limit"]
    assert by_id["inv-3"]["arg_keys"] == [], "NULL arg_keys must be [] not None"
    assert by_id["inv-4"]["arg_keys"] == [], "unparseable arg_keys must not raise"


def test_running_row_keeps_null_duration_rather_than_zero(runtime_store):
    """0 ms would read as "instantaneous"; None reads as "not finished"."""
    running = runtime_store.read_runtime_invocations(status="running")[0]
    assert running["duration_ms"] is None
    assert running["completed_at"] == ""


def test_unknown_runtime_filter_raises(runtime_store):
    """Silently ignoring it would return the whole table as a false match."""
    with pytest.raises(ValueError) as exc:
        runtime_store.read_runtime_invocations(sruface="mcp")
    assert "sruface" in str(exc.value)


def test_runtime_filter_values_are_parameterized(runtime_store):
    """A quote in a filter value must be data, not SQL."""
    assert runtime_store.read_runtime_invocations(name="'; DROP TABLE x--") == []
    assert len(runtime_store.read_runtime_invocations()) == 4


def test_missing_runtime_table_returns_empty_not_raises(store):
    """The feed DB has no runtime_invocations — an un-migrated DB is not an error."""
    assert store.read_runtime_invocations(limit=10) == []


# ---------------------------------------------------------------- CLI

def test_cli_prints_oldest_first_so_newest_is_nearest_the_prompt(store, monkeypatch, capsys):
    monkeypatch.setattr(audit_tail, "AuditStore", lambda *a, **k: store, raising=False)
    monkeypatch.setattr("tools.audit.store.AuditStore", lambda *a, **k: store)
    rc = audit_tail.main(["--limit", "5", "--no-color"])
    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 0 and len(out) == 5
    stamps = [line[:19] for line in out]
    assert stamps == sorted(stamps), "screen order must be oldest-first"


def test_cli_json_emits_one_object_per_line(store, monkeypatch, capsys):
    monkeypatch.setattr("tools.audit.store.AuditStore", lambda *a, **k: store)
    rc = audit_tail.main(["--limit", "3", "--json"])
    lines = [ln for ln in capsys.readouterr().out.strip().splitlines() if ln]
    assert rc == 0 and len(lines) == 3
    for line in lines:
        assert set(json.loads(line)) >= {"id", "source", "created_at", "event_type"}


def test_cli_follow_exits_zero_on_keyboard_interrupt(store, monkeypatch, capsys):
    """Ctrl-C is how you stop a tail — it must be exit 0, not a traceback."""
    monkeypatch.setattr("tools.audit.store.AuditStore", lambda *a, **k: store)

    def boom(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(audit_tail.time, "sleep", boom)
    assert audit_tail.main(["--limit", "1", "--follow", "--no-color"]) == 0


def test_cli_follow_survives_a_transient_query_failure(store, monkeypatch, capsys):
    """One failed poll must not end a long-running follow."""
    monkeypatch.setattr("tools.audit.store.AuditStore", lambda *a, **k: store)
    calls = {"n": 0}
    real_tail = store.tail

    def flaky(filters=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("connection reset")
        if calls["n"] >= 3:
            raise KeyboardInterrupt
        return real_tail(filters)

    monkeypatch.setattr(store, "tail", flaky)
    monkeypatch.setattr(audit_tail.time, "sleep", lambda _s: None)
    assert audit_tail.main(["--limit", "1", "--follow", "--no-color"]) == 0
    assert "retrying" in capsys.readouterr().err


def test_cli_empty_result_names_the_database(monkeypatch, capsys, tmp_path):
    """An empty feed and the WRONG database must not look identical."""
    path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE audit_trail (id INTEGER PRIMARY KEY, project_id TEXT, "
                 "event_type TEXT, actor TEXT, action TEXT, details TEXT, "
                 "classification TEXT, session_id TEXT, created_at TEXT)")
    conn.commit()
    conn.close()

    def factory():
        return _PgStyleSqlite(str(path))

    empty = AuditStore(connection_factory=factory)
    monkeypatch.setattr("tools.audit.store.AuditStore", lambda *a, **k: empty)
    assert audit_tail.main(["--limit", "5", "--no-color"]) == 0
    assert "read from" in capsys.readouterr().err


def test_cli_list_types(store, monkeypatch, capsys):
    monkeypatch.setattr("tools.audit.store.AuditStore", lambda *a, **k: store)
    assert audit_tail.main(["--list-types"]) == 0
    assert "build_started" in capsys.readouterr().out


# ------------------------------------------------- PostgreSQL (primary backend)

def _pg_available() -> bool:
    """True when a real PostgreSQL board is reachable.

    Gates on the RESOLVED backend, not on ``ICDEV_STORAGE_BACKEND`` — the raw
    env var is the wrong thing to read here because ``tests/conftest.py``
    overwrites it to ``sqlite`` unless ``ICDEV_PYTEST_PG=1`` is set. Reading it
    directly would make these tests skip permanently, and a PostgreSQL test
    that never runs is worse than no test: it reads as coverage it does not
    provide. Run with ``ICDEV_PYTEST_PG=1`` to exercise them.
    """
    try:
        from tools.db import storage

        if getattr(storage, "_BACKEND", "") != "postgresql":
            return False
        conn = storage.get_connection()
        try:
            conn.execute("SELECT 1 FROM audit_trail LIMIT 1").fetchone()
            return True
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return False


pg_only = pytest.mark.skipif(
    not _pg_available(),
    reason="requires a reachable PostgreSQL board — run with ICDEV_PYTEST_PG=1",
)


@pg_only
def test_pg_since_cursor_is_strictly_newer():
    """The --follow cursor must compare correctly against PostgreSQL.

    This is not covered by the SQLite tests above and is the one place the two
    backends could genuinely diverge: on PG ``audit_trail.created_at`` is
    ``timestamp WITHOUT time zone``, while the cursor we generate is a
    tz-AWARE ISO string. PG casts that text to timestamp by DROPPING the
    offset — which is correct only because the column stores UTC. If either
    fact changes, --follow silently returns nothing forever, so pin both.
    """
    store = AuditStore()
    base = store.tail(AuditFilter(limit=20))
    if len(base) < 4:
        pytest.skip("not enough rows on this board to exercise the cursor")

    stamps = sorted(r["created_at"] for r in base)
    assert all("+00:00" in s or s.endswith("Z") for s in stamps), \
        "cursor lost its timezone — the PG cast assumption no longer holds"

    mid = stamps[len(stamps) // 2]
    above = store.tail(AuditFilter(since=mid, limit=50))
    assert all(r["created_at"] > mid for r in above), "PG returned a row at/before the cursor"
    assert len(above) >= sum(1 for s in stamps if s > mid), "PG dropped rows the cursor should include"


@pg_only
def test_pg_tail_merges_both_sources():
    """Smoke the real board: the merge must work on the primary backend too."""
    rows = AuditStore().tail(AuditFilter(limit=25))
    assert rows, "live board returned nothing"
    stamps = [r["created_at"] for r in rows]
    assert stamps == sorted(stamps, reverse=True), "not newest-first on PG"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
