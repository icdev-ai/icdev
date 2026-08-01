# CUI // SP-CTI
"""Tests for check_insert_schema_parity coherence check (swp-gate-01).

The gate exists because `CREATE TABLE IF NOT EXISTS` never alters an existing
table: a table created by an older migration keeps its old columns while the
DDL in the source moves on, and the INSERT that names the new column raises at
runtime inside `except Exception: pass`. The feature reports success and
persists nothing. A scan against live PostgreSQL found 95 such statements.

Most tests build a synthetic repo under tmp_path and inject a fake live schema,
so they neither need nor touch a real database. Two tests DO open a real SQLite
database, because the PRAGMA-based schema read is the fallback path and a mock
would only assert its own stub.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workflow import coherence_checker as cc  # noqa: E402


def _build_repo(tmp_path: pathlib.Path, files: dict, config: str = "") -> pathlib.Path:
    """Write a synthetic repo: tools/ modules plus the gate config."""
    repo = tmp_path / "repo"
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")
    (repo / "tools").mkdir(parents=True, exist_ok=True)
    cfg = repo / "args" / "insert_schema_gate.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(config, encoding="utf-8")
    return repo


def _run(tmp_path, monkeypatch, files, schema, config="", changed_files=None):
    repo = _build_repo(tmp_path, files, config)
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    monkeypatch.setattr(cc, "_INSERT_SCHEMA_CONFIG", repo / "args" / "insert_schema_gate.yaml")
    monkeypatch.setattr(cc, "_live_table_columns", lambda: (schema, "postgresql", ""))
    return cc.check_insert_schema_parity(changed_files)


_SCHEMA = {"kanban_tasks": {"id", "title", "dispatch_source", "created_at"}}


# ---------------------------------------------------------------------------
# Core contract: a broken INSERT fails, a correct one passes
# ---------------------------------------------------------------------------


def test_matching_insert_passes(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "tools/x/writer.py": '''
                from tools.db.storage import get_connection

                def write(conn, task_id, title, now):
                    conn.execute(
                        """
                        INSERT INTO kanban_tasks (id, title, dispatch_source, created_at)
                        VALUES (%s, %s, 'x', %s)
                        """,
                        (task_id, title, now),
                    )
            ''',
        },
        _SCHEMA,
    )
    assert result.status == "pass", result.message
    assert result.extra == []


def test_broken_insert_fails(tmp_path, monkeypatch):
    """The gate's reason for existing: a column absent from the live schema FAILS."""
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "tools/x/writer.py": '''
                from tools.db.storage import get_connection

                def write(conn, task_id, title, now):
                    # `source` does not exist — the real column is `dispatch_source`.
                    conn.execute(
                        """
                        INSERT INTO kanban_tasks (id, title, source, created_at)
                        VALUES (%s, %s, 'x', %s)
                        """,
                        (task_id, title, now),
                    )
            ''',
        },
        _SCHEMA,
    )
    assert result.status == "fail", result.message
    assert len(result.extra) == 1
    assert "kanban_tasks" in result.extra[0]
    assert "'source'" in result.extra[0]
    assert "tools/x/writer.py" in result.extra[0]


def test_finding_reports_the_statement_line(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "tools/x/writer.py": '''
                # line 2
                # line 3
                SQL = "INSERT INTO kanban_tasks (id, nope) VALUES (%s, %s)"
            ''',
        },
        _SCHEMA,
    )
    assert result.status == "fail"
    assert result.extra[0].startswith("tools/x/writer.py:4:"), result.extra[0]


def test_multiple_missing_columns_each_reported(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {"tools/x/writer.py": 'SQL = "INSERT INTO kanban_tasks (id, a, b) VALUES (%s, %s, %s)"\n'},
        _SCHEMA,
    )
    assert result.status == "fail"
    assert len(result.extra) == 2
    assert result.missing == ["a", "b"]


# ---------------------------------------------------------------------------
# Grandfathering — the pre-existing backlog warns instead of blocking
# ---------------------------------------------------------------------------


def test_grandfathered_mismatch_warns(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {"tools/x/writer.py": 'SQL = "INSERT INTO kanban_tasks (id, source) VALUES (%s, %s)"\n'},
        _SCHEMA,
        config='grandfathered:\n  "tools/x/writer.py:kanban_tasks:source": "swp-scan-01"\n',
    )
    assert result.status == "warn", result.message
    assert result.missing == ["tools/x/writer.py:kanban_tasks:source"]


def test_new_mismatch_still_fails_alongside_grandfathered(tmp_path, monkeypatch):
    """Grandfathering one column must not excuse the next one to appear."""
    result = _run(
        tmp_path,
        monkeypatch,
        {"tools/x/writer.py": 'SQL = "INSERT INTO kanban_tasks (id, source, owner) VALUES (%s, %s, %s)"\n'},
        _SCHEMA,
        config='grandfathered:\n  "tools/x/writer.py:kanban_tasks:source": "swp-scan-01"\n',
    )
    assert result.status == "fail"
    assert len(result.extra) == 1
    assert "'owner'" in result.extra[0]


def test_stale_grandfather_entry_reported(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {"tools/x/writer.py": 'SQL = "INSERT INTO kanban_tasks (id, title) VALUES (%s, %s)"\n'},
        _SCHEMA,
        config='grandfathered:\n  "tools/x/writer.py:kanban_tasks:source": "fixed already"\n',
    )
    assert result.status == "warn"
    assert result.extra == ["tools/x/writer.py:kanban_tasks:source"]


def test_stale_entries_not_reported_on_a_diff_scoped_run(tmp_path, monkeypatch):
    """A diff-scoped run never read the other files, so it cannot call them stale."""
    repo = _build_repo(
        tmp_path,
        {
            "tools/x/writer.py": 'SQL = "INSERT INTO kanban_tasks (id, title) VALUES (%s, %s)"\n',
            "tools/y/other.py": 'SQL = "INSERT INTO kanban_tasks (id, source) VALUES (%s, %s)"\n',
        },
        config='grandfathered:\n  "tools/y/other.py:kanban_tasks:source": "swp-scan-01"\n',
    )
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    monkeypatch.setattr(cc, "_INSERT_SCHEMA_CONFIG", repo / "args" / "insert_schema_gate.yaml")
    monkeypatch.setattr(cc, "_live_table_columns", lambda: (_SCHEMA, "postgresql", ""))

    result = cc.check_insert_schema_parity([repo / "tools" / "x" / "writer.py"])
    assert result.status == "pass", result.message
    assert result.extra == []


def test_ignore_tables_suppresses_a_colliding_name(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {"tools/x/writer.py": 'SQL = "INSERT INTO kanban_tasks (id, source) VALUES (%s, %s)"\n'},
        _SCHEMA,
        config="ignore_tables:\n  - kanban_tasks\n",
    )
    assert result.status == "pass", result.message


def test_unreadable_config_fails_closed(tmp_path, monkeypatch):
    """A malformed allowlist must make the gate stricter, never looser."""
    result = _run(
        tmp_path,
        monkeypatch,
        {"tools/x/writer.py": 'SQL = "INSERT INTO kanban_tasks (id, source) VALUES (%s, %s)"\n'},
        _SCHEMA,
        config="grandfathered: [[[ not yaml\n",
    )
    assert result.status == "fail"


# ---------------------------------------------------------------------------
# Precision — what the scanner must NOT claim
# ---------------------------------------------------------------------------


def test_unknown_table_is_skipped(tmp_path, monkeypatch):
    """A table absent from this database belongs to another one; do not guess."""
    result = _run(
        tmp_path,
        monkeypatch,
        {"tools/x/writer.py": 'SQL = "INSERT INTO some_other_db_table (a, b) VALUES (%s, %s)"\n'},
        _SCHEMA,
    )
    assert result.status == "pass", result.message


def test_dynamic_column_list_is_skipped(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {"tools/x/writer.py": 'SQL = f"INSERT INTO kanban_tasks ({cols}) VALUES ({holes})"\n'},
        _SCHEMA,
    )
    assert result.status == "pass", result.message


def test_dynamic_table_name_is_skipped(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {"tools/x/writer.py": 'SQL = f"INSERT INTO {table} (id, source) VALUES (%s, %s)"\n'},
        _SCHEMA,
    )
    assert result.status == "pass", result.message


def test_foreign_database_module_is_skipped(tmp_path, monkeypatch):
    """tools/ais/ais_importer.py writes to the geosigint app's own sg_tracks."""
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "tools/x/writer.py": '''
                from apps.geosigint.models import get_connection

                SQL = "INSERT INTO kanban_tasks (id, source) VALUES (%s, %s)"
            ''',
        },
        _SCHEMA,
    )
    assert result.status == "pass", result.message


def test_canvas_init_db_import_is_not_foreign(tmp_path, monkeypatch):
    """Canvas db/init_db modules delegate to tools.db.storage — same database."""
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "tools/x/writer.py": '''
                from tools.network.db.init_db import get_connection

                SQL = "INSERT INTO kanban_tasks (id, source) VALUES (%s, %s)"
            ''',
        },
        _SCHEMA,
    )
    assert result.status == "fail", result.message


def test_insert_or_replace_and_qualified_table_are_parsed(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "tools/x/writer.py": (
                'A = "INSERT OR REPLACE INTO kanban_tasks (id, source) VALUES (?, ?)"\n'
                'B = "INSERT INTO public.kanban_tasks (id, owner) VALUES (%s, %s)"\n'
            ),
        },
        _SCHEMA,
    )
    assert result.status == "fail"
    assert len(result.extra) == 2


def test_insert_select_form_is_parsed(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {"tools/x/writer.py": 'SQL = "INSERT INTO kanban_tasks (id, source) SELECT id, x FROM other"\n'},
        _SCHEMA,
    )
    assert result.status == "fail"


def test_syntax_error_in_a_module_does_not_crash_the_check(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {"tools/x/broken.py": 'def f(:\n    SQL = "INSERT INTO kanban_tasks (id, title) VALUES (%s, %s)"\n'},
        _SCHEMA,
    )
    assert result.status in ("pass", "fail")  # ran to completion, did not raise


# ---------------------------------------------------------------------------
# Extractor semantics — so the "is skipped" tests above are not vacuous
# ---------------------------------------------------------------------------


def test_extractor_returns_line_table_and_columns():
    found = _extract("\n\nSQL = \"INSERT INTO kanban_tasks (id, Title) VALUES (%s, %s)\"\n")
    assert found == [(3, "kanban_tasks", ["id", "title"])]


@pytest.mark.parametrize(
    "source",
    [
        'f"INSERT INTO kanban_tasks ({cols}) VALUES (%s)"',       # interpolated column list
        'f"INSERT INTO {table} (id, title) VALUES (%s, %s)"',     # interpolated table
        '"INSERT INTO kanban_tasks (id, " + extra + ") VALUES"',  # concatenated
        '"INSERT INTO kanban_tasks VALUES (%s, %s)"',             # no column list
    ],
)
def test_extractor_skips_statements_it_cannot_read_statically(source):
    assert _extract(source) == []


def _extract(source: str):
    return cc._extract_insert_column_lists(source)


# ---------------------------------------------------------------------------
# Live schema read — the real SQLite PRAGMA path, no mocks
# ---------------------------------------------------------------------------


def _make_sqlite_db(path: pathlib.Path, table_count: int) -> None:
    conn = sqlite3.connect(str(path))
    for i in range(table_count):
        conn.execute(f"CREATE TABLE t{i} (id INTEGER PRIMARY KEY, alpha TEXT, beta TEXT)")
    conn.commit()
    conn.close()


def test_live_table_columns_reads_sqlite_pragma(tmp_path, monkeypatch):
    db = tmp_path / "data" / "probe.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    _make_sqlite_db(db, cc._INSERT_SCHEMA_MIN_TABLES + 5)

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))

    schema, backend, error = cc._live_table_columns()
    assert error == "", error
    assert backend == "sqlite"
    assert schema["t0"] == {"id", "alpha", "beta"}
    assert len(schema) >= cc._INSERT_SCHEMA_MIN_TABLES


def test_uninitialised_database_is_reported_not_silently_trusted(tmp_path, monkeypatch):
    """An empty DB must not let a broken INSERT pass by finding no tables."""
    db = tmp_path / "data" / "empty.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    _make_sqlite_db(db, 2)

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))

    schema, _backend, error = cc._live_table_columns()
    assert schema == {}
    assert "not initialised" in error


def test_missing_live_schema_warns_instead_of_failing(tmp_path, monkeypatch):
    repo = _build_repo(
        tmp_path,
        {"tools/x/writer.py": 'SQL = "INSERT INTO kanban_tasks (id, source) VALUES (%s, %s)"\n'},
    )
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    monkeypatch.setattr(cc, "_live_table_columns", lambda: ({}, "", "database unreachable"))

    result = cc.check_insert_schema_parity()
    assert result.status == "warn"
    assert "database unreachable" in result.message


# ---------------------------------------------------------------------------
# Registration — a check nobody runs is not a gate
# ---------------------------------------------------------------------------


def test_check_is_registered():
    assert cc.CHECK_REGISTRY["insert_schema_parity"] is cc.check_insert_schema_parity


@pytest.mark.parametrize("tier", ["full", "fast"])
def test_check_runs_in_both_tiers(tier):
    assert "insert_schema_parity" in cc.select_checks(tier)


def test_check_has_a_fix_tier():
    assert cc._FIX_REGISTRY["insert_schema_parity"] == "skip"


# ---------------------------------------------------------------------------
# The shipped baseline
# ---------------------------------------------------------------------------


def test_repo_baseline_is_wellformed():
    """Every grandfathered key names a file that exists, in path:table:column form."""
    ignored, grandfathered = cc._load_insert_schema_gate()
    assert grandfathered, "baseline is empty — the swp-scan-01 backlog should be recorded"
    assert isinstance(ignored, set)
    for key, reason in grandfathered.items():
        parts = key.split(":")
        assert len(parts) == 3, f"malformed key: {key}"
        rel, table, column = parts
        assert (cc.PROJECT_ROOT / rel).is_file(), f"grandfathered file is gone: {rel}"
        assert cc._SQL_IDENTIFIER_RE.match(table), key
        assert cc._SQL_IDENTIFIER_RE.match(column), key
        assert reason, f"entry without a reason: {key}"
