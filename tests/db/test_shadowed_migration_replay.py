# CUI // SP-CTI
"""Tests for tools/db/shadowed_migration_replay.py (mvs-audit-03-d2).

The expensive part of the tool is the baseline (every migration the runner
applies, ~13s). These tests build a TINY migrations directory instead of the
real one, so the whole module is exercised end-to-end in well under a second
and the assertions are about behaviour rather than about today's tree.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.db import shadowed_migration_replay as smr  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures — a synthetic migrations tree with a deliberate shadowing
# ---------------------------------------------------------------------------
@pytest.fixture
def mini_migrations(tmp_path):
    """Two entries share version 002, so one of them is shadowed.

    ``002_alpha`` sorts first and wins; ``002_beta`` is the shadowed loser and
    declares a table nothing else creates, so it is a genuine gap. ``003_dup``
    re-declares a table 001 already created, so it is genuinely benign.
    """
    d = tmp_path / "migrations"
    (d / "001_base").mkdir(parents=True)
    (d / "001_base" / "up.sql").write_text(
        "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT);\n", encoding="utf-8"
    )
    (d / "002_alpha").mkdir()
    (d / "002_alpha" / "up.sql").write_text(
        "CREATE TABLE alpha_rows (id INTEGER PRIMARY KEY);\n", encoding="utf-8"
    )
    (d / "002_beta").mkdir()
    (d / "002_beta" / "up.sql").write_text(
        "CREATE TABLE beta_rows (id INTEGER PRIMARY KEY);\n"
        "CREATE INDEX idx_beta ON beta_rows(id);\n",
        encoding="utf-8",
    )
    (d / "003_dup").mkdir()
    (d / "003_dup" / "up.sql").write_text(
        "CREATE TABLE IF NOT EXISTS widgets (id INTEGER PRIMARY KEY, name TEXT);\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture
def baseline(tmp_path, mini_migrations):
    db = tmp_path / "baseline.db"
    smr.build_baseline(db, mini_migrations)
    return db


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------
def test_baseline_excludes_shadowed_entries(baseline):
    """The shadowed loser must NOT be in the comparison database.

    If it were, every shadowed entry would trivially score "already exists" and
    the tool would report a clean bill of health on a broken chain.
    """
    names = {r[0] for r in sqlite3.connect(str(baseline)).execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "widgets" in names
    assert "alpha_rows" in names, "the winning entry for v002 must be applied"
    assert "beta_rows" not in names, "the shadowed entry must never reach the baseline"


def test_baseline_reports_mode(tmp_path, mini_migrations):
    result = smr.build_baseline(tmp_path / "b.db", mini_migrations)
    assert result["mode"] == "migrations_only"
    assert result["applied_total"] >= 3


def test_baseline_captures_a_migration_that_opens_its_own_connection(
    tmp_path, mini_migrations, monkeypatch
):
    """A connection-discarding WINNER must still land in the baseline (d3).

    ``MigrationRunner`` is handed the baseline path, but a migration whose
    ``up()`` does ``conn = get_connection()`` ignores it and resolves
    ``ICDEV_DB_PATH`` instead. Unpinned during the baseline build, its tables go
    to whatever database the environment names — leaving the baseline short of
    schema the chain really does create, so a shadowed entry that declares the
    same table is scored ``schema_gap_detected`` when it is benign. Measured on
    the real tree, 61 tables escaped this way and one of the first 20 verdicts
    rested on the wrong evidence.
    """
    elsewhere = tmp_path / "ambient.db"
    monkeypatch.setenv("ICDEV_DB_PATH", str(elsewhere))

    entry = mini_migrations / "004_selfconn"
    entry.mkdir()
    (entry / "up.py").write_text(
        "from tools.db.storage import get_connection\n"
        "\n"
        "def up(conn=None):\n"
        "    conn = get_connection()\n"
        "    conn.execute('CREATE TABLE IF NOT EXISTS escaped_rows (id INTEGER)')\n"
        "    conn.commit()\n"
        "    conn.close()\n",
        encoding="utf-8",
    )

    db = tmp_path / "pinned_baseline.db"
    smr.build_baseline(db, mini_migrations)

    names = {r[0] for r in sqlite3.connect(str(db)).execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "escaped_rows" in names, "the write escaped the baseline"
    assert not elsewhere.exists(), "the build wrote to the ambient database"


# ---------------------------------------------------------------------------
# The two required verdicts
# ---------------------------------------------------------------------------
def test_gap_is_detected(tmp_path, mini_migrations, baseline):
    """An entry declaring a table no winner creates → schema_gap_detected."""
    snap = smr.snapshot(baseline)
    result = smr.replay_entry("002_beta", baseline, snap, tmp_path, mini_migrations)
    assert result["verdict"] == smr.VERDICT_GAP
    added = {o["name"] for o in result["delta"]["added_objects"]}
    assert "beta_rows" in added
    assert "idx_beta" in added, "indexes count as schema, not just tables"


def test_already_present_is_detected(tmp_path, mini_migrations, baseline):
    """An entry re-declaring an existing table → schema_already_exists."""
    snap = smr.snapshot(baseline)
    result = smr.replay_entry("003_dup", baseline, snap, tmp_path, mini_migrations)
    assert result["verdict"] == smr.VERDICT_PRESENT
    assert result["delta"]["empty"]


def test_replay_does_not_mutate_the_baseline(tmp_path, mini_migrations, baseline):
    """The candidate is a throwaway copy; the oracle must survive unchanged.

    A replay that wrote through to the baseline would poison every subsequent
    verdict in the same run.
    """
    before = smr.snapshot(baseline)
    smr.replay_entry("002_beta", baseline, before, tmp_path, mini_migrations)
    assert smr.snapshot(baseline) == before


# ---------------------------------------------------------------------------
# Schema comparison
# ---------------------------------------------------------------------------
def test_ddl_change_without_new_object_is_a_gap(tmp_path, mini_migrations, baseline):
    """A CHECK-constraint widening adds no table and no column.

    This is the case a table/column diff scores benign — the dashboard_users.role
    gap. Rebuilding the table changes the stored DDL text, and only a DDL
    comparison sees it.
    """
    entry = mini_migrations / "004_widen"
    entry.mkdir()
    (entry / "up.sql").write_text(
        "ALTER TABLE widgets RENAME TO widgets_old;\n"
        "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT "
        "CHECK (name IN ('a','b')));\n"
        "DROP TABLE widgets_old;\n",
        encoding="utf-8",
    )
    snap = smr.snapshot(baseline)
    result = smr.replay_entry("004_widen", baseline, snap, tmp_path, mini_migrations)
    assert result["verdict"] == smr.VERDICT_GAP
    assert not result["delta"]["added_columns"]
    assert [c["name"] for c in result["delta"]["changed_ddl"]] == ["widgets"]


def test_added_column_is_detected(tmp_path, mini_migrations, baseline):
    entry = mini_migrations / "005_col"
    entry.mkdir()
    (entry / "up.sql").write_text(
        "ALTER TABLE widgets ADD COLUMN owner TEXT;\n", encoding="utf-8"
    )
    snap = smr.snapshot(baseline)
    result = smr.replay_entry("005_col", baseline, snap, tmp_path, mini_migrations)
    assert result["verdict"] == smr.VERDICT_GAP
    assert result["delta"]["added_columns"] == {"widgets": ["owner"]}


# ---------------------------------------------------------------------------
# Error handling — the difference between this and the runner
# ---------------------------------------------------------------------------
def test_statements_after_an_already_exists_error_still_run(
    tmp_path, mini_migrations, baseline
):
    """executescript() aborts the whole script on the first error.

    An entry whose FIRST statement already exists would then never execute the
    rest, the delta would come back empty, and a real gap would be reported as
    benign. Statement-at-a-time execution is what prevents that.
    """
    entry = mini_migrations / "006_mixed"
    entry.mkdir()
    (entry / "up.sql").write_text(
        "CREATE TABLE widgets (id INTEGER PRIMARY KEY);\n"   # errors: already exists
        "CREATE TABLE late_table (id INTEGER PRIMARY KEY);\n",  # must still run
        encoding="utf-8",
    )
    snap = smr.snapshot(baseline)
    result = smr.replay_entry("006_mixed", baseline, snap, tmp_path, mini_migrations)
    assert result["verdict"] == smr.VERDICT_GAP
    assert result["execution"]["benign_skips"] == 1
    assert {o["name"] for o in result["delta"]["added_objects"]} == {"late_table"}


def test_pg_only_entry_is_inconclusive_not_present(tmp_path, mini_migrations, baseline):
    """Silence on SQLite is not a pass.

    A PostgreSQL-only migration applies nothing here. Reporting that as
    "already exists" is exactly the false clear this tool exists to avoid.
    """
    entry = mini_migrations / "007_pgonly"
    entry.mkdir()
    (entry / "up.sql").write_text(
        "-- @pg-only\nCREATE TABLE pg_thing (id SERIAL PRIMARY KEY);\n", encoding="utf-8"
    )
    snap = smr.snapshot(baseline)
    result = smr.replay_entry("007_pgonly", baseline, snap, tmp_path, mini_migrations)
    assert result["verdict"] == smr.VERDICT_INCONCLUSIVE
    assert "pg-only" in result["reason"].lower() or "postgres" in result["reason"].lower()


def test_unrunnable_sql_with_no_delta_is_inconclusive(tmp_path, mini_migrations, baseline):
    entry = mini_migrations / "008_broken"
    entry.mkdir()
    (entry / "up.sql").write_text(
        "ALTER TABLE nonexistent_table ADD COLUMN x TEXT;\n", encoding="utf-8"
    )
    snap = smr.snapshot(baseline)
    result = smr.replay_entry("008_broken", baseline, snap, tmp_path, mini_migrations)
    assert result["verdict"] == smr.VERDICT_INCONCLUSIVE
    assert result["execution"]["hard_error_count"] == 1


def test_entry_without_up_file_is_inconclusive(tmp_path, mini_migrations, baseline):
    (mini_migrations / "009_empty").mkdir()
    snap = smr.snapshot(baseline)
    result = smr.replay_entry("009_empty", baseline, snap, tmp_path, mini_migrations)
    assert result["verdict"] == smr.VERDICT_INCONCLUSIVE
    assert "nothing to execute" in result["reason"]


# ---------------------------------------------------------------------------
# up.py entries
# ---------------------------------------------------------------------------
def test_py_entry_that_opens_its_own_connection_hits_the_candidate(
    tmp_path, mini_migrations, baseline
):
    """A migration that ignores the conn it was handed must still be sandboxed.

    Several real entries do ``conn = get_connection()`` inside ``up()``,
    discarding the argument. Unless ICDEV_DB_PATH is pinned to the candidate for
    the call, such a migration writes somewhere else entirely — which leaves the
    candidate untouched and scores a real gap "already exists".
    """
    entry = mini_migrations / "010_selfconn"
    entry.mkdir()
    (entry / "up.py").write_text(
        "from tools.db.storage import get_connection\n"
        "\n"
        "def up(conn=None):\n"
        "    conn = get_connection()\n"
        "    conn.execute('CREATE TABLE IF NOT EXISTS self_conn_table (id INTEGER)')\n"
        "    conn.commit()\n"
        "    conn.close()\n",
        encoding="utf-8",
    )
    snap = smr.snapshot(baseline)
    result = smr.replay_entry("010_selfconn", baseline, snap, tmp_path, mini_migrations)
    assert result["verdict"] == smr.VERDICT_GAP
    assert {o["name"] for o in result["delta"]["added_objects"]} == {"self_conn_table"}
    assert smr.snapshot(baseline) == snap, "the baseline must not have been written to"


def test_bypass_flag_ignores_the_dunder_main_block(tmp_path):
    """``get_connection()`` under ``if __name__ == '__main__'`` never runs.

    Nearly every migration has one, so a substring search over the file flags
    them all and the signal is worthless.
    """
    main_only = tmp_path / "main_only.py"
    main_only.write_text(
        "def up(conn):\n"
        "    conn.execute('CREATE TABLE t (id INTEGER)')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    from tools.db.storage import get_connection\n"
        "    up(get_connection())\n",
        encoding="utf-8",
    )
    assert smr._bypasses_passed_connection(main_only) is False

    inside_up = tmp_path / "inside_up.py"
    inside_up.write_text(
        "from tools.db.storage import get_connection\n"
        "\n"
        "def up(conn=None):\n"
        "    conn = get_connection()\n",
        encoding="utf-8",
    )
    assert smr._bypasses_passed_connection(inside_up) is True


# ---------------------------------------------------------------------------
# Statement splitting
# ---------------------------------------------------------------------------
def test_split_keeps_a_trigger_body_intact():
    """Splitting on ';' would cut a CREATE TRIGGER in half."""
    sql = (
        "CREATE TABLE t (id INTEGER);\n"
        "CREATE TRIGGER trg AFTER INSERT ON t BEGIN\n"
        "  UPDATE t SET id = id;\n"
        "END;\n"
    )
    statements = smr.split_statements(sql)
    assert len(statements) == 2
    assert statements[1].strip().endswith("END;")


def test_split_ignores_semicolons_inside_literals():
    statements = smr.split_statements(
        "INSERT INTO t VALUES ('a;b');\nINSERT INTO t VALUES ('c');\n"
    )
    assert len(statements) == 2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def test_cli_json_is_parseable_despite_migration_chatter(tmp_path):
    """Migrations print to stdout; that must not corrupt --json.

    Run as a subprocess so the real stdout stream is what gets parsed.
    """
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "db" / "shadowed_migration_replay.py"),
         "--list", "--json"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
        env={**__import__("os").environ, "PYTHONPATH": str(REPO_ROOT),
             "ICDEV_STORAGE_BACKEND": "sqlite"},
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    payload = json.loads(proc.stdout)
    assert payload["count"] == len(payload["shadowed"])


def test_cli_requires_a_selection():
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "db" / "shadowed_migration_replay.py")],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
        env={**__import__("os").environ, "PYTHONPATH": str(REPO_ROOT),
             "ICDEV_STORAGE_BACKEND": "sqlite"},
    )
    assert proc.returncode != 0
    assert "--list" in proc.stderr
