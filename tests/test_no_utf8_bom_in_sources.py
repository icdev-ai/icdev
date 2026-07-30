# CUI // SP-CTI
"""No tracked source file may start with a UTF-8 BOM, and a failed statement in a
multi-statement script must be logged.

These belong together because one caused the other to matter.

PowerShell's `Set-Content -Encoding utf8` writes a BOM. Renumbering migration
309 -> 313 that way put a BOM at the front of
313_fa_mission_progress_reconcile.sql. The BOM became part of the file's FIRST
statement, so the DELETE that removes phantom fa_mission_progress rows was invalid
PostgreSQL. _pg_exec_statements caught the error, rolled back to its savepoint and
moved on WITHOUT LOGGING, the second statement (the UPDATE) succeeded, and
MigrationRunner recorded 313 as applied. Result: attempts correctly re-based to 0,
39 phantom rows still present, migration reported successful, no diagnostic
anywhere.

A BOM is legal in Python (utf-8-sig is handled) and invisible in a diff, so nothing
else catches it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BOM = b"\xef\xbb\xbf"

# Extensions where a leading BOM is either a syntax hazard or plain wrong.
CHECKED_SUFFIXES = {".py", ".sql", ".yaml", ".yml", ".json", ".md", ".html", ".ts", ".js"}


def _bom_files() -> list[str]:
    """Find BOM-prefixed tracked files with ONE git process.

    Opening ~19k tracked files individually is slow enough on Windows to blow the
    suite's timeout, so the search is delegated to `git grep`, which reads the index
    directly. `-P` anchors the BOM to a line start; only a file-initial BOM matters,
    and matching any line start is strictly tighter.
    """
    globs = [f"*{s}" for s in sorted(CHECKED_SUFFIXES)]
    proc = subprocess.run(
        ["git", "grep", "-I", "-l", "-P", r"^\xef\xbb\xbf", "--", *globs],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    # git grep exits 1 when there are no matches — that is the healthy case.
    if proc.returncode not in (0, 1):
        pytest.skip(f"git grep unavailable: {proc.stderr.strip()[:120]}")
    return sorted(p for p in proc.stdout.splitlines() if p.strip())


def test_no_tracked_source_file_starts_with_a_bom():
    offenders = _bom_files()
    assert not offenders, (
        "UTF-8 BOM at the start of tracked source file(s). A BOM breaks the first "
        "SQL statement of a migration and is invisible in a diff. Almost always "
        "PowerShell `Set-Content -Encoding utf8` — strip the first 3 bytes:\n  "
        + "\n  ".join(offenders)
    )


def test_migration_313_first_statement_is_executable_sql():
    """Specifically pin the file whose first statement the BOM invalidated."""
    p = REPO_ROOT / "tools" / "db" / "migrations" / "313_fa_mission_progress_reconcile.sql"
    raw = p.read_bytes()
    assert not raw.startswith(BOM), "BOM would invalidate the DELETE"
    text = raw.decode("utf-8")
    assert "﻿" not in text, "stray BOM character inside the file"
    # The DELETE must survive comment stripping as statement one.
    from tools.db.storage import _strip_sql_line_comments

    stmts = [s.strip() for s in _strip_sql_line_comments(text).split(";") if s.strip()]
    assert stmts, "no statements parsed"
    assert stmts[0].upper().startswith("DELETE"), (
        f"first executable statement is {stmts[0][:40]!r}, expected the DELETE"
    )
    assert stmts[1].upper().startswith("UPDATE")


def test_a_failed_statement_in_a_script_is_logged(caplog):
    """The silence is what turned a BOM into a half-applied migration."""
    import sqlite3

    from tools.db import storage

    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    script = "CREATE TABLE ok_one (id INTEGER); SYNTAX ERROR HERE; CREATE TABLE ok_two (id INTEGER);"
    with caplog.at_level("WARNING"):
        storage._pg_exec_statements(cur, script, "postgresql")

    assert any("skipping failed statement" in r.getMessage() for r in caplog.records), (
        "a skipped statement must leave a trace"
    )
    # And the surrounding statements still ran — the skip stays non-fatal.
    names = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"ok_one", "ok_two"} <= names


@pytest.mark.parametrize("sample", ["﻿-- comment\nDELETE FROM t;", "﻿SELECT 1;"])
def test_a_bom_really_does_break_the_first_statement(sample):
    """Document the mechanism so nobody 'fixes' the guard by loosening it."""
    from tools.db.storage import _strip_sql_line_comments

    stmts = [s.strip() for s in _strip_sql_line_comments(sample).split(";") if s.strip()]
    assert stmts[0].startswith("﻿") or "﻿" in stmts[0], (
        "the BOM rides along into the first statement, which is why PG rejects it"
    )
