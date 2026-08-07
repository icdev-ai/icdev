#!/usr/bin/env python3
# CUI // SP-CTI
"""Replay a shadowed migration against a throwaway SQLite database (mvs-audit-03-d2).

``tools/db/migration_versions.py --shadowed`` names the migrations that will
never run: two entries share a version, ``MigrationRunner`` keeps only the first
per version, and the loser is dead on disk. The open question for each one is
whether its schema is *missing* from a database built today, or whether some
other migration already declares the same objects and the shadowing is harmless.

This tool answers that by **execution** rather than by parsing:

1. Build a BASELINE — a fresh throwaway SQLite database with every migration the
   runner actually applies, in runner order. Shadowed entries are excluded for
   free: ``get_pending_migrations`` dedupes by version, so building the baseline
   with the ordinary converge runner *is* "all NON-shadowed migrations in order".
2. Copy the baseline to a CANDIDATE file, execute the shadowed entry's ``up.sql``
   / ``up()`` against the copy, and diff the resulting schema against the
   baseline snapshot.
3. An empty diff means every object the entry declares already exists —
   ``schema_already_exists``. A non-empty diff is schema no deployment gets —
   ``schema_gap_detected``.

Why execution and not a name diff: a migration that only widens a CHECK
constraint adds no table and no column, so a table/column comparison scores it
benign. Replaying it rebuilds the table and the new DDL text differs, which is
how ``dashboard_users.role`` (four RBAC roles rejected on PostgreSQL, two
shadowed migrations written to fix it) is visible here. The snapshot therefore
compares normalised ``sqlite_master`` DDL text, not just object names.

The honest limits, reported in the output rather than hidden:

* This is a SQLite oracle. Roughly a third of the chain is PostgreSQL-only DDL,
  so a PostgreSQL-only entry cannot be replayed here at all — it is reported
  ``inconclusive``, never "already exists". Silence is not a pass.
* The baseline itself is incomplete for the same reason (some migrations fail on
  SQLite). ``baseline.remaining_failures`` is in every report so a verdict is
  never read without it.
* ``schema_gap_detected`` means THE MIGRATION CHAIN does not produce the object.
  It does not mean nothing produces it: several canvases create their tables at
  app startup from ``db/init_db.py``, which no baseline here runs. Confirm a
  declaring source in the tree before calling a gap a defect. This is why the
  60 entries score 43 gaps here while the four-oracle audit in PR #1296 found 6.
* A migration whose ``up()`` deliberately no-ops on SQLite ("CHECK is generated
  at schema-init time") is indistinguishable from one whose objects are already
  present: both execute cleanly and change nothing. Such an entry scores
  ``schema_already_exists`` on this oracle and needs a PostgreSQL check.
  ``execution.bypasses_passed_connection`` flags the related hazard.

Usage::

    python tools/db/shadowed_migration_replay.py --list
    python tools/db/shadowed_migration_replay.py --sample 3 --json
    python tools/db/shadowed_migration_replay.py --migration 010_network_intelligence_schema
    python tools/db/shadowed_migration_replay.py --all --json
    # reuse an already-built baseline (~13s to build) across runs
    python tools/db/shadowed_migration_replay.py --all --baseline-db /tmp/base.db
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Pin the backend and the SQLite path BEFORE tools.db.storage is imported.
#
# This is a safety interlock, not a convenience. A shadowed entry's up.py is
# arbitrary code, and some migrations call get_connection() themselves instead
# of using the connection handed to up(). In a shell that exports
# ICDEV_STORAGE_BACKEND=postgresql and ICDEV_DATABASE_URL (the normal local
# setup here) such a call would resolve the ambient environment and execute the
# migration against the LIVE database. Pinning sqlite makes get_backend()
# return "sqlite" so get_connection() never reaches the PG pool, and pinning
# ICDEV_DB_PATH aims any ambient SQLite resolution at the throwaway file.
# storage.DB_PATH is bound at import time (storage.py:141), so the assignment
# has to happen here, above the import, rather than inside main().
_REPLAY_DB_ENV = "ICDEV_DB_PATH"
os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"

from tools.db.migration_runner import MIGRATIONS_DIR, MigrationRunner  # noqa: E402
from tools.db.migration_versions import shadowed_migrations  # noqa: E402

# Bookkeeping tables that say nothing about a migration's schema contribution.
_IGNORED_OBJECTS = {"schema_migrations", "sqlite_sequence"}

# Errors that mean "this object is already here" — exactly the condition the
# tool is testing for, so they are evidence, not failures. Every other error is
# a hard error (PG-only syntax, a missing prerequisite table, a typo).
_BENIGN_ERROR_MARKERS = (
    "already exists",
    "duplicate column name",
)

VERDICT_GAP = "schema_gap_detected"
VERDICT_PRESENT = "schema_already_exists"
VERDICT_INCONCLUSIVE = "inconclusive"


# ----------------------------------------------------------------------------
# Entry resolution
# ----------------------------------------------------------------------------
def entry_up_source(name: str, migrations_dir: Path | None = None) -> dict[str, Any]:
    """Locate the up-side of one migration entry and classify how to replay it.

    Four shapes exist on disk. The last two are the ones ``discover_migrations``
    drops on the floor, and they are still replayable here because this tool
    reads the file directly rather than going through discovery.
    """
    d = migrations_dir or MIGRATIONS_DIR
    path = d / name

    if path.is_file() and path.suffix == ".sql":
        return {"kind": "sql", "path": path, "sql_path": path, "py_path": None}
    if path.is_file() and path.suffix == ".py":
        # A bare NNN_name.py file — never discovered by the runner (it only
        # looks inside directories for up.py), so it is dead twice over.
        return {"kind": "py", "path": path, "sql_path": None, "py_path": path}
    if path.is_dir():
        up_sql = path / "up.sql"
        up_py = path / "up.py"
        if up_sql.is_file():
            return {"kind": "sql", "path": path, "sql_path": up_sql, "py_path": None}
        if up_py.is_file():
            return {"kind": "py", "path": path, "sql_path": None, "py_path": up_py}
        return {"kind": "none", "path": path, "sql_path": None, "py_path": None}
    return {"kind": "missing", "path": path, "sql_path": None, "py_path": None}


# ----------------------------------------------------------------------------
# Schema snapshot / diff
# ----------------------------------------------------------------------------
def _normalise_ddl(sql: str) -> str:
    """Collapse whitespace so formatting churn is not reported as a change."""
    return " ".join((sql or "").split())


def snapshot(db_path: Path) -> dict[str, Any]:
    """Describe one SQLite file: objects, their DDL text, and table columns.

    A raw ``sqlite3`` connection on purpose. The point of a snapshot is to
    describe THIS file — the one just built — and ``get_connection()`` would
    resolve the ambient environment instead.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        objects: dict[str, dict[str, str]] = {}
        columns: dict[str, dict[str, str]] = {}
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for otype, name, sql in rows:
            key = str(name).lower()
            if key in _IGNORED_OBJECTS:
                continue
            objects[key] = {"type": str(otype), "ddl": _normalise_ddl(str(sql or ""))}
            if otype == "table":
                columns[key] = {
                    str(c[1]).lower(): str(c[2] or "")
                    for c in conn.execute(f'PRAGMA table_info("{name}")').fetchall()
                }
        return {"objects": objects, "columns": columns}
    finally:
        conn.close()


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What the replay added or changed, relative to the baseline."""
    b_obj, a_obj = before["objects"], after["objects"]
    b_col, a_col = before["columns"], after["columns"]

    added_objects = [
        {"name": n, "type": a_obj[n]["type"]} for n in sorted(set(a_obj) - set(b_obj))
    ]
    # A DDL text change with no new object is the enum-widening case: SQLite
    # cannot ALTER a CHECK constraint, so a widening migration rebuilds the
    # table and the stored DDL differs. Invisible to a name/column diff.
    changed_ddl = [
        {
            "name": n,
            "baseline_ddl": b_obj[n]["ddl"],
            "replayed_ddl": a_obj[n]["ddl"],
        }
        for n in sorted(set(a_obj) & set(b_obj))
        if a_obj[n]["ddl"] != b_obj[n]["ddl"]
    ]
    added_columns = {}
    for table in sorted(set(a_col) & set(b_col)):
        new_cols = sorted(set(a_col[table]) - set(b_col[table]))
        if new_cols:
            added_columns[table] = new_cols

    return {
        "added_objects": added_objects,
        "added_columns": added_columns,
        "changed_ddl": changed_ddl,
        "empty": not (added_objects or added_columns or changed_ddl),
    }


# ----------------------------------------------------------------------------
# SQL execution
# ----------------------------------------------------------------------------
def split_statements(sql: str) -> list[str]:
    """Split a script into individual statements.

    ``executescript`` is what the runner uses, but it aborts the whole script at
    the first error — so an entry whose second statement raises "already exists"
    never executes statements 3..n, and the replay would under-report the delta
    and call a real gap benign. Statement-at-a-time execution with per-statement
    error tolerance is the difference between this tool and the runner.

    Splitting uses ``sqlite3.complete_statement`` (a wrapper over
    ``sqlite3_complete``) rather than ``sql.split(";")``: it ignores semicolons
    inside string literals and comments, and it knows a CREATE TRIGGER body runs
    to its END, which naive splitting would cut in half.
    """
    statements: list[str] = []
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stripped = buf.strip()
            if stripped:
                statements.append(stripped)
            buf = ""
    tail = buf.strip()
    if tail:
        statements.append(tail)
    return statements


def _bypasses_passed_connection(py_path: Path) -> bool:
    """True when ``up()`` itself opens a connection instead of using its argument.

    Scoped to the body of ``up`` via AST rather than a substring search over the
    file: almost every migration mentions ``get_connection`` in its
    ``if __name__ == "__main__"`` block, which never runs on import, so a plain
    ``"get_connection(" in source`` test flags nearly all of them and the signal
    is worthless. 010_network_intelligence_schema is exactly that false
    positive; 018_reflex_observations.py is the true one.
    """
    import ast

    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "up":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    func = inner.func
                    name = getattr(func, "id", None) or getattr(func, "attr", None)
                    if name == "get_connection":
                        return True
    return False


def _is_benign(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _BENIGN_ERROR_MARKERS)


def _apply_sql(db_path: Path, sql: str) -> dict[str, Any]:
    """Execute a filtered script statement-by-statement, tolerating errors."""
    conn = sqlite3.connect(str(db_path))
    executed, benign, hard = 0, [], []
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        for stmt in split_statements(sql):
            try:
                conn.execute(stmt)
                executed += 1
            except Exception as exc:  # noqa: BLE001
                record = {"statement": stmt[:200], "error": str(exc)}
                (benign if _is_benign(exc) else hard).append(record)
        conn.commit()
    finally:
        conn.close()
    return {"executed": executed, "benign_skips": benign, "hard_errors": hard}


def _apply_py(db_path: Path, py_path: Path) -> dict[str, Any]:
    """Import a migration's up.py and call ``up(conn)`` against the throwaway db.

    No statement-level tolerance is possible here — the module decides its own
    control flow — so a raising ``up()`` is recorded whole. The connection is
    wrapped in ``StorageConnection`` exactly as ``MigrationRunner.apply_migration``
    does, so ``%s``-authored SQL (the PG-primary house style) is translated to
    SQLite placeholders and the module sees the connection it expects.

    The connection handed in is NOT always the one used. Several migrations open
    ``up(conn=None)`` with ``conn = get_connection()``, discarding the argument
    and resolving the ambient environment instead (018_reflex_observations.py is
    one). ``ICDEV_DB_PATH`` is therefore pinned to THIS candidate for the call —
    without that, such a migration writes to whatever database the environment
    names. During development of this tool that was the baseline file, which
    left the candidate untouched and scored a real gap ``schema_already_exists``;
    in a shell exporting a PostgreSQL URL it would have been the live database.
    ``_get_sqlite_connection`` re-reads the variable on every call and caches
    nothing, so the pin is effective and is restored afterwards.
    """
    import importlib.util

    from tools.db.storage import StorageConnection

    raw = sqlite3.connect(str(db_path))
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys=OFF")
    conn = StorageConnection(raw, "sqlite")
    hard: list[dict[str, str]] = []
    executed = 0
    previous = os.environ.get(_REPLAY_DB_ENV)
    os.environ[_REPLAY_DB_ENV] = str(db_path)
    try:
        spec = importlib.util.spec_from_file_location(f"replay_{py_path.parent.name}", str(py_path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "up"):
            hard.append({"statement": "<module>", "error": "no up() function"})
        else:
            module.up(conn)
            conn.commit()
            executed = 1
    except SystemExit as exc:
        # A module-scope sys.exit() would otherwise take this process down with
        # it and lose every verdict computed so far.
        hard.append({"statement": "<module>", "error": f"SystemExit({exc.code})"})
    except Exception as exc:  # noqa: BLE001
        hard.append({"statement": "<module>", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        if previous is None:
            os.environ.pop(_REPLAY_DB_ENV, None)
        else:
            os.environ[_REPLAY_DB_ENV] = previous
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return {
        "executed": executed,
        "benign_skips": [],
        "hard_errors": hard,
        "bypasses_passed_connection": _bypasses_passed_connection(py_path),
    }


# ----------------------------------------------------------------------------
# Baseline
# ----------------------------------------------------------------------------
def _run_init_db(db_path: Path) -> dict[str, Any]:
    """Seed the baseline with the ~527 tables ``init_icdev_db.py`` creates.

    Run as a subprocess with a scrubbed environment rather than imported: the
    script binds its own module-level ``DB_PATH`` from ``ICDEV_DB_PATH`` at
    import time, and this process may already have imported storage with
    different values. A child process gets the pins cleanly, and
    ``ICDEV_DATABASE_URL`` is dropped because it outranks the PG database vars
    and must not survive into a run that is meant to be SQLite-only.
    """
    import subprocess

    env = dict(os.environ)
    env["ICDEV_STORAGE_BACKEND"] = "sqlite"
    env["ICDEV_DB_PATH"] = str(db_path)
    env["PYTHONPATH"] = str(BASE_DIR)
    for leaked in ("ICDEV_DATABASE_URL", "ICDEV_PG_DATABASE", "ICDEV_PG_DB"):
        env.pop(leaked, None)
    proc = subprocess.run(
        [sys.executable, str(BASE_DIR / "tools" / "db" / "init_icdev_db.py"),
         "--db-path", str(db_path)],
        cwd=str(BASE_DIR), env=env, capture_output=True, text=True, timeout=900,
    )
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "stderr_tail": (proc.stderr or "")[-500:]}


@contextlib.contextmanager
def _pinned_db_path(db_path: Path):
    """Aim any ambient SQLite resolution at ``db_path`` for the duration.

    ``_get_sqlite_connection`` re-reads ``ICDEV_DB_PATH`` on every call, so this
    redirects the migrations that call ``get_connection()`` themselves instead of
    using the connection they were handed. Restores the previous value, including
    its absence.
    """
    previous = os.environ.get(_REPLAY_DB_ENV)
    os.environ[_REPLAY_DB_ENV] = str(db_path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(_REPLAY_DB_ENV, None)
        else:
            os.environ[_REPLAY_DB_ENV] = previous


def build_baseline(
    db_path: Path,
    migrations_dir: Path | None = None,
    with_init_db: bool = False,
) -> dict[str, Any]:
    """Build the comparison database: every migration the runner actually runs.

    ``migrate_up_converge`` rather than a single pass because some migrations
    ALTER a table a LATER migration creates; converging retries them until a
    pass makes no progress. Shadowed entries need no explicit exclusion —
    ``get_pending_migrations`` keeps the first entry per version, which is the
    definition of the winner, so the losers are already absent.

    Two baselines, because "already exists" has two different meanings:

    * ``with_init_db=False`` (default) — migrations only. Answers "does the
      MIGRATION CHAIN already declare this?", which is the right question for
      PostgreSQL, where ``init_icdev_db.py`` refuses to run and the chain is the
      only source of schema.
    * ``with_init_db=True`` — ``init_icdev_db.py`` first, then the chain.
      Answers "does a fresh SQLite DEPLOYMENT already have this?".

    Measured 2026-08-07, the two baselines are IDENTICAL: init creates 527
    tables and the chain creates 872, and the 527 are a strict subset — the
    set difference is empty and both baselines snapshot to the same 2371
    objects. So the default costs nothing in accuracy today. The flag is kept
    because that subset relationship is an accident of the current tree, not an
    invariant anything enforces; if init ever declares a table the chain does
    not, a migrations-only baseline would start scoring it as a gap, and this
    flag is how that gets caught rather than believed.
    """
    d = migrations_dir or MIGRATIONS_DIR
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    init_result = _run_init_db(db_path) if with_init_db else None

    # The same connection-discarding hazard _apply_py guards against applies to
    # the baseline itself, and there it is worse: the baseline is the oracle every
    # verdict is measured against. MigrationRunner is told db_path, but a
    # migration whose up() does `conn = get_connection()` ignores that and
    # resolves the environment. Unpinned, 61 tables (sg_conflict_events among
    # them) landed in the repo's data/icdev.db instead of the baseline, so the
    # baseline lacked schema the chain really does create and the shadowed entry
    # that also created it was scored a gap it is not. Measured on the first 20
    # entries, pinning moves the baseline from 2371 to 2539 objects.
    with _pinned_db_path(db_path):
        runner = MigrationRunner(db_path=db_path, migrations_dir=d, engine="sqlite")
        result = runner.migrate_up_converge()
    return {
        "db_path": str(db_path),
        # Always reported. tools/db/migrations and icdev/tools/db/migrations are
        # separate trees that hold DIFFERENT files — 60 shadowed entries in one,
        # 78 in the other — so a report that does not name the directory it
        # audited cannot be compared with another report.
        "migrations_dir": str(d),
        "mode": "init_db+migrations" if with_init_db else "migrations_only",
        "init_db": init_result,
        "applied_total": result["applied_total"],
        "remaining_failures": len(result["remaining_failures"]),
        "note": (
            "remaining_failures are migrations SQLite cannot apply (mostly "
            "PostgreSQL-only DDL). Objects they would create are absent from "
            "this baseline, so a gap verdict resting on such an object is weak."
        ),
    }


# ----------------------------------------------------------------------------
# Replay
# ----------------------------------------------------------------------------
def replay_entry(
    name: str,
    baseline_db: Path,
    baseline_snapshot: dict[str, Any],
    workdir: Path,
    migrations_dir: Path | None = None,
) -> dict[str, Any]:
    """Replay one shadowed entry against a private copy of the baseline."""
    source = entry_up_source(name, migrations_dir)
    result: dict[str, Any] = {"migration": name, "kind": source["kind"]}

    if source["kind"] in ("none", "missing"):
        result["verdict"] = VERDICT_INCONCLUSIVE
        result["reason"] = (
            "no up.sql or up.py — nothing to execute"
            if source["kind"] == "none"
            else f"entry not found at {source['path']}"
        )
        return result

    candidate = workdir / f"candidate_{name.replace('/', '_')}.db"
    shutil.copyfile(baseline_db, candidate)

    try:
        if source["kind"] == "sql":
            raw_sql = source["sql_path"].read_text(encoding="utf-8")
            # Reuse the runner's own directive filter so @sqlite-only/@pg-only
            # are honoured identically to a real migration run. Re-implementing
            # it would let the two drift.
            filtered = MigrationRunner(
                db_path=candidate, migrations_dir=migrations_dir or MIGRATIONS_DIR, engine="sqlite"
            )._filter_sql(raw_sql)
            statements = split_statements(filtered)
            if not statements:
                result["verdict"] = VERDICT_INCONCLUSIVE
                result["reason"] = (
                    "no SQLite-applicable statements after @pg-only filtering — "
                    "PostgreSQL-only migration, not replayable on this oracle"
                )
                return result
            execution = _apply_sql(candidate, filtered)
        else:
            execution = _apply_py(candidate, source["py_path"])

        delta = diff_snapshots(baseline_snapshot, snapshot(candidate))
        result["execution"] = {
            "statements_executed": execution["executed"],
            "benign_skips": len(execution["benign_skips"]),
            "hard_errors": execution["hard_errors"][:5],
            "hard_error_count": len(execution["hard_errors"]),
        }
        if execution.get("bypasses_passed_connection"):
            result["execution"]["bypasses_passed_connection"] = True
        result["delta"] = delta

        # Sandbox integrity. The verdict is only meaningful if the replay wrote
        # to the candidate and nowhere else; a migration that opens its own
        # connection could still escape if it hardcodes a path. Re-snapshot the
        # baseline and refuse to report a verdict computed against a moved
        # oracle — an escaped write makes the candidate diff look empty, which
        # reads as "already exists" and would launder a real gap.
        if snapshot(baseline_db) != baseline_snapshot:
            result["verdict"] = VERDICT_INCONCLUSIVE
            result["reason"] = (
                "replay wrote outside its candidate database — the baseline "
                "changed during execution, so the diff is not trustworthy"
            )
            return result

        if not delta["empty"]:
            result["verdict"] = VERDICT_GAP
            result["reason"] = _describe_delta(delta)
        elif execution["hard_errors"]:
            result["verdict"] = VERDICT_INCONCLUSIVE
            result["reason"] = (
                f"{len(execution['hard_errors'])} statement(s) could not run on SQLite "
                "and produced no schema change — cannot distinguish 'already present' "
                f"from 'not replayable here': {execution['hard_errors'][0]['error']}"
            )
        else:
            result["verdict"] = VERDICT_PRESENT
            result["reason"] = (
                f"replay changed nothing; {len(execution['benign_skips'])} object(s) "
                "reported already-exists against the baseline"
            )
        return result
    finally:
        candidate.unlink(missing_ok=True)


def _describe_delta(delta: dict[str, Any]) -> str:
    parts = []
    if delta["added_objects"]:
        parts.append(
            "new "
            + ", ".join(f"{o['type']} {o['name']}" for o in delta["added_objects"][:5])
        )
    if delta["added_columns"]:
        parts.append(
            "new columns "
            + ", ".join(f"{t}.{c}" for t, cs in delta["added_columns"].items() for c in cs[:3])
        )
    if delta["changed_ddl"]:
        parts.append("changed DDL on " + ", ".join(c["name"] for c in delta["changed_ddl"][:5]))
    return "; ".join(parts)


def run(
    names: list[str],
    baseline_db: Path | None = None,
    workdir: Path | None = None,
    migrations_dir: Path | None = None,
    with_init_db: bool = False,
) -> dict[str, Any]:
    """Build (or reuse) the baseline, then replay every requested entry."""
    d = migrations_dir or MIGRATIONS_DIR
    owned_workdir = workdir is None
    work = workdir or Path(tempfile.mkdtemp(prefix="mvs_replay_"))
    work.mkdir(parents=True, exist_ok=True)
    # Migrations chatter on stdout ("Migration 018 up: ... created"), and both
    # the baseline build and every replay execute migration code. Left alone
    # that text interleaves with the report and makes --json unparseable. Send
    # it to stderr so it stays visible to a human but out of the payload.
    with contextlib.redirect_stdout(sys.stderr):
        return _run_inner(names, baseline_db, work, d, owned_workdir, with_init_db)


def _run_inner(
    names: list[str],
    baseline_db: Path | None,
    work: Path,
    d: Path,
    owned_workdir: bool,
    with_init_db: bool = False,
) -> dict[str, Any]:
    try:
        base_path = baseline_db or (work / "baseline.db")
        reused = baseline_db is not None and baseline_db.is_file()
        if reused:
            baseline = {
                "db_path": str(base_path),
                "reused": True,
                "note": "pre-built baseline reused; not rebuilt this run",
            }
        else:
            baseline = build_baseline(base_path, d, with_init_db=with_init_db)
            baseline["reused"] = False

        # Default any ambient SQLite resolution at a decoy inside the workdir,
        # so code that reads ICDEV_DB_PATH outside a replay (import-time module
        # constants, say) can never land on a real database. Each replay then
        # re-points this at its own candidate; see _apply_py.
        os.environ[_REPLAY_DB_ENV] = str(work / "decoy.db")

        base_snap = snapshot(base_path)
        baseline["objects"] = len(base_snap["objects"])

        results = [replay_entry(n, base_path, base_snap, work, d) for n in names]
        summary: dict[str, int] = {}
        for r in results:
            summary[r["verdict"]] = summary.get(r["verdict"], 0) + 1
        return {"baseline": baseline, "results": results, "summary": summary}
    finally:
        if owned_workdir:
            shutil.rmtree(work, ignore_errors=True)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _shadowed_names(migrations_dir: Path | None = None) -> list[str]:
    d = migrations_dir or MIGRATIONS_DIR
    seen, names = set(), []
    for row in shadowed_migrations(d):
        if row["shadowed"] not in seen:
            seen.add(row["shadowed"])
            names.append(row["shadowed"])
    return names


def _print_human(report: dict[str, Any]) -> None:
    base = report["baseline"]
    if base.get("reused"):
        print(f"baseline: reused {base['db_path']} ({base['objects']} objects)")
    else:
        print(f"migrations: {base['migrations_dir']}")
        print(
            f"baseline [{base['mode']}]: {base['applied_total']} migrations applied, "
            f"{base['objects']} objects, {base['remaining_failures']} could not "
            f"apply on SQLite"
        )
        print(
            "  a gap here means THE MIGRATION CHAIN does not produce the object. "
            "Tables created at app startup by a canvas db/init_db.py are not in "
            "any baseline, so confirm a declaring source in the tree before "
            "calling a gap a defect."
        )
    print()
    for r in report["results"]:
        print(f"{r['migration']}  [{r['kind']}]")
        print(f"  -> {r['verdict']}: {r.get('reason', '')}")
    print()
    print("summary: " + ", ".join(f"{k}={v}" for k, v in sorted(report["summary"].items())))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Replay shadowed migrations against a throwaway SQLite database"
    )
    ap.add_argument("--list", action="store_true", help="list shadowed migration entries")
    ap.add_argument(
        "--migration", action="append", default=[], metavar="NAME",
        help="replay this entry (repeatable)",
    )
    ap.add_argument("--all", action="store_true", help="replay every shadowed entry")
    ap.add_argument("--sample", type=int, metavar="N", help="replay the first N shadowed entries")
    ap.add_argument(
        "--baseline-db", type=Path, metavar="PATH",
        help="build the baseline here and reuse it if it already exists",
    )
    ap.add_argument(
        "--with-init-db", action="store_true",
        help=(
            "seed the baseline with init_icdev_db.py before the chain. Measured "
            "to produce an identical baseline today (init's 527 tables are a "
            "subset of the chain's 872); use it to re-check that still holds"
        ),
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    available = _shadowed_names()

    if args.list:
        if args.json:
            print(json.dumps(
                {"shadowed": available, "count": len(available),
                 "migrations_dir": str(MIGRATIONS_DIR)},
                indent=2,
            ))
        else:
            print(f"{len(available)} shadowed migration entr(ies) in {MIGRATIONS_DIR}:")
            for n in available:
                print(f"  {n}")
        return 0

    if args.all:
        names = available
    elif args.sample:
        names = available[: args.sample]
    elif args.migration:
        names = args.migration
        unknown = [n for n in names if n not in available]
        if unknown:
            print(
                "warning: not listed as shadowed — replaying anyway: " + ", ".join(unknown),
                file=sys.stderr,
            )
    else:
        ap.error("choose one of --list, --all, --sample N, or --migration NAME")

    report = run(names, baseline_db=args.baseline_db, with_init_db=args.with_init_db)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
