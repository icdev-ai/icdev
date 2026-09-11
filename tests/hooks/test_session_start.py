# CUI // SP-CTI
"""xrv-mem-01 — the SessionStart hook injects a token-capped memory index.

The documented Session Start Protocol was a manual step nobody ran, and
``.claude/settings.json`` wired every hook kind except SessionStart. These
tests pin the shape that makes the automatic version safe to ship:

* the block stays under the token cap and is CAPPED, never truncated silently
* an empty database prints NOTHING (a block reading "no memory" would be a
  clean bill of health for a read that found nothing)
* an exception in the builder prints nothing and exits 0
* settings.json names the hook, and the file exists
* ``hook_events`` accepts ``session_start`` on a fresh DDL, the migration
  rebuilds the PostgreSQL constraint and is a STATED no-op on SQLite
* the headless twin and the hook share ONE builder
* the two phantom manifest rows are gone

No live database: every read goes to a SQLite fixture through the seams'
own overrides (``memory_read.DB_PATH``, ``session_context_builder.DB_PATH``,
``send_event.DB_PATH``).
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tools.hooks import hook_event_types, session_context
from tools.hooks.hook_event_types import (
    HOOK_EVENT_TYPES,
    HOOK_TYPE_CONSTRAINT,
    hook_type_check_sql,
    rebuild_hook_type_constraint,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = REPO_ROOT / ".claude" / "hooks" / "session_start.py"
SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"
MIGRATION_DIR = REPO_ROOT / "tools" / "db" / "migrations" / "20260911220746_hook_events_session_start_type"

memory_read = importlib.import_module("tools.memory.memory_read")
context_builder = importlib.import_module("tools.project.session_context_builder")


# ── fixtures ───────────────────────────────────────────────────────────


def _make_db(path: Path, entries: int, content=None) -> Path:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        f"""
        CREATE TABLE memory_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            type TEXT DEFAULT 'event',
            importance INTEGER DEFAULT 5,
            created_at TEXT DEFAULT (datetime('now')),
            user_id TEXT, tenant_id TEXT,
            classification TEXT DEFAULT 'CUI',
            compartment TEXT DEFAULT ''
        );
        CREATE TABLE hook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            hook_type TEXT NOT NULL {hook_type_check_sql()},
            tool_name TEXT, project_id TEXT, payload TEXT,
            classification TEXT DEFAULT 'CUI', signature TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    for i in range(entries):
        body = content(i) if content else f"entry number {i}: deployed the helm chart to staging"
        conn.execute(
            "INSERT INTO memory_entries (content, type, created_at) VALUES (?, ?, ?)",
            # monotonic: a later i is a NEWER row, so "newest first" is by id too
            (body, "event" if i % 2 else "fact", f"2026-09-{(i // 60) + 1:02d}T10:{i % 60:02d}:00"),
        )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def db_with_entries(tmp_path):
    return _make_db(tmp_path / "fixture.db", 12)


@pytest.fixture
def empty_db(tmp_path):
    return _make_db(tmp_path / "empty.db", 0)


@pytest.fixture
def seams(monkeypatch, tmp_path):
    """Point every seam the hook reads at a fixture, never the checkout's DB."""

    def _wire(db: Path):
        monkeypatch.setattr(memory_read, "DB_PATH", db)
        monkeypatch.setattr(context_builder, "DB_PATH", db)
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
        return db

    return _wire


def _load_hook():
    spec = importlib.util.spec_from_file_location("icdev_session_start_hook", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_hook_inprocess(monkeypatch, capsys, stdin: dict) -> tuple[int, str]:
    hook = _load_hook()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stdin)))
    with pytest.raises(SystemExit) as exc:
        hook.main()
    return exc.value.code, capsys.readouterr().out


# ── the block ──────────────────────────────────────────────────────────


def test_block_on_fixture_db_is_within_budget_and_indexed(seams, db_with_entries, tmp_path):
    seams(db_with_entries)
    result = session_context.build_block(directory=str(tmp_path), started=time.perf_counter())
    assert result["reason"] == "ok" and result["emitted"] is True
    assert result["tokens"] is not None and result["tokens"] <= result["max_tokens"]
    assert result["entries_read"] == 12 and result["entries_shown"] == 12
    assert result["capped"] is False
    lines = [line for line in result["context"].splitlines() if line.startswith("- #")]
    assert len(lines) == 12
    # newest first, id last in the row -> first in the line; a headline, not the body
    assert re.match(r"- #\d+ \[(event|fact)\] 2026-09-\d\d entry number 11", lines[0])
    # the row now carries id LAST (the six positional columns are unchanged)
    row = memory_read.read_db_recent(1)[0]
    assert len(row) == 7 and isinstance(row[6], int)


def test_over_cap_block_is_capped_oldest_first_and_reported(seams, tmp_path):
    long_body = lambda i: f"entry {i} " + ("lorem ipsum dolor sit amet " * 12)  # noqa: E731
    seams(_make_db(tmp_path / "big.db", 400, long_body))
    result = session_context.build_block(
        directory=str(tmp_path), db_limit=400, max_tokens=300, started=time.perf_counter()
    )
    assert result["reason"] == "ok" and result["capped"] is True
    assert result["tokens"] <= 300
    assert 0 < result["entries_shown"] < result["entries_read"] == 400
    shown = [line for line in result["context"].splitlines() if line.startswith("- #")]
    assert shown[0].startswith("- #400 ")  # newest kept, oldest dropped


def test_empty_db_composes_nothing(seams, empty_db, tmp_path):
    seams(empty_db)
    result = session_context.build_block(directory=str(tmp_path), started=time.perf_counter())
    assert result["context"] is None and result["emitted"] is False
    assert result["reason"] == "empty"
    assert result["tokens"] is None  # never 0 over nothing composed
    assert result["entries_read"] == 0
    # the "not a registered project" warning is read and NOT injected
    assert result["project_source"] == "none" and result["warnings_read"] == 1
    assert result["warnings_shown"] == 0


def test_builder_exception_is_a_named_reason_with_no_block():
    def boom(limit):
        raise RuntimeError("db on fire")

    result = session_context.build_block(read_recent=boom, started=time.perf_counter())
    assert result["context"] is None and result["emitted"] is False
    assert result["reason"] == "error:RuntimeError"
    assert result["tokens"] is None


def test_over_budget_withholds_a_composed_block(seams, db_with_entries, tmp_path):
    seams(db_with_entries)
    result = session_context.build_block(
        directory=str(tmp_path), budget_seconds=0.5, started=time.perf_counter() - 5.0
    )
    assert result["context"] is None and result["emitted"] is False
    assert result["reason"] == "over_budget"
    assert result["elapsed_ms"] >= 5000  # the cost is reported, not hidden


def test_reason_vocabulary_is_closed():
    src = (REPO_ROOT / "tools" / "hooks" / "session_context.py").read_text(encoding="utf-8")
    literal = set(re.findall(r'result\["reason"\] = "([a-z_]+)"', src))
    assert literal <= set(session_context.REASONS)
    assert 'f"error:{type(exc).__name__}"' in src


def test_headline_reads_json_bodies_and_truncates():
    body = json.dumps({"category": "Unmerged / stranded branch", "commit_summary": ""})
    assert session_context.headline(body) == "Unmerged / stranded branch"
    assert session_context.headline("first line\nsecond line") == "first line"
    long = "x" * 500
    assert len(session_context.headline(long)) == session_context.HEADLINE_CHARS
    assert session_context.headline(long).endswith("…")


# ── the hook ───────────────────────────────────────────────────────────


def test_hook_prints_the_block_and_records_a_session_start_event(
    seams, db_with_entries, tmp_path, monkeypatch, capsys
):
    seams(db_with_entries)
    send_event = importlib.import_module("send_event") if "send_event" in sys.modules else None
    if send_event is None:
        sys.path.insert(0, str(HOOK_PATH.parent))
        send_event = importlib.import_module("send_event")
    monkeypatch.setattr(send_event, "DB_PATH", db_with_entries)
    monkeypatch.setattr(send_event, "forward_to_dashboard", lambda *_a, **_k: None)

    code, out = _run_hook_inprocess(
        monkeypatch, capsys, {"session_id": "s-test", "cwd": str(tmp_path), "source": "startup"}
    )
    assert code == 0
    assert out.startswith("## ICDEV session context")
    assert out.count("- #") == 12

    rows = sqlite3.connect(str(db_with_entries)).execute(
        "SELECT session_id, hook_type, payload FROM hook_events"
    ).fetchall()
    assert len(rows) == 1 and rows[0][0] == "s-test" and rows[0][1] == "session_start"
    payload = json.loads(rows[0][2])
    assert payload["reason"] == "ok" and payload["source"] == "startup"
    assert payload["tokens"] <= 1500 and "elapsed_ms" in payload


def test_hook_prints_nothing_on_an_empty_db(seams, empty_db, tmp_path, monkeypatch, capsys):
    seams(empty_db)
    code, out = _run_hook_inprocess(monkeypatch, capsys, {"cwd": str(tmp_path)})
    assert code == 0 and out == ""


def test_hook_prints_nothing_and_exits_zero_when_the_builder_raises(monkeypatch, capsys):
    def boom(**_kw):
        raise RuntimeError("builder on fire")

    monkeypatch.setattr(session_context, "build_block", boom)
    code, out = _run_hook_inprocess(monkeypatch, capsys, {"cwd": "."})
    assert code == 0 and out == ""


def test_hook_subprocess_never_fails_on_garbage_stdin():
    """Driven the way Claude Code drives it; the DB is pointed at an absent
    SQLite file so the run touches no live board."""
    env = dict(os.environ, ICDEV_STORAGE_BACKEND="sqlite", ICDEV_DATABASE_URL="",
               ICDEV_DB_PATH=str(REPO_ROOT / ".tmp" / "definitely-absent-xrv-mem-01.db"),
               ICDEV_SESSION_START_HOOK="1")
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)], input="not json at all", capture_output=True,
        text=True, cwd=str(REPO_ROOT), env=env, timeout=60,
    )
    assert proc.returncode == 0


def test_kill_switch_prints_nothing(monkeypatch, capsys):
    monkeypatch.setenv("ICDEV_SESSION_START_HOOK", "0")
    code, out = _run_hook_inprocess(monkeypatch, capsys, {"cwd": "."})
    assert code == 0 and out == ""


# ── settings.json and the validator ────────────────────────────────────


def test_settings_json_names_the_hook_and_states_why_or_true_is_fine():
    data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    entries = [
        h["command"]
        for block in data["hooks"]["SessionStart"]
        for h in block["hooks"]
    ]
    assert len(entries) == 1 and "session_start.py" in entries[0]
    assert HOOK_PATH.exists()
    # `|| true` is acceptable HERE and only because the hook says so itself.
    doc = ast.get_docstring(ast.parse(HOOK_PATH.read_text(encoding="utf-8")))
    assert "|| true" in entries[0] and "|| true`` IS ACCEPTABLE" in doc


def test_claude_dir_validator_hooks_refs_is_green():
    from tools.testing.claude_dir_validator import check_settings_hook_references

    result = check_settings_hook_references()
    assert result.status == "pass", result.message


# ── hook_events: the constant, the DDL, the migration, the writers ─────


def test_init_ddl_derives_its_check_from_the_constant():
    init_mod = importlib.import_module("tools.db.init_icdev_db")
    ddl = init_mod.SCHEMA_SQL
    start = ddl.index("CREATE TABLE IF NOT EXISTS hook_events")
    check = re.search(r"hook_type TEXT NOT NULL CHECK\(hook_type IN \((.*?)\)\)", ddl[start:], re.S)
    assert check, "hook_events lost its CHECK"
    assert set(re.findall(r"'([^']+)'", check.group(1))) == set(HOOK_EVENT_TYPES)
    assert "@@HOOK_EVENT_TYPES@@" not in ddl


def test_every_hook_type_a_shipped_hook_writes_is_admitted():
    """The defect this closes: user_prompt_submit and pre_compact were written
    for months into a refused INSERT."""
    written = set()
    for hook_file in (REPO_ROOT / ".claude" / "hooks").glob("*.py"):
        written |= set(re.findall(r'hook_type="([a-z_]+)"', hook_file.read_text(encoding="utf-8")))
    assert written, "no hook writes an event?"
    assert written <= set(HOOK_EVENT_TYPES), written - set(HOOK_EVENT_TYPES)
    assert {"session_start", "user_prompt_submit", "pre_compact"} <= set(HOOK_EVENT_TYPES)


def test_fresh_sqlite_ddl_accepts_session_start(tmp_path):
    db = _make_db(tmp_path / "fresh.db", 0)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO hook_events (session_id, hook_type) VALUES ('s', 'session_start')")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO hook_events (session_id, hook_type) VALUES ('s', 'not_a_hook')")
    conn.close()


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_xrv_mem_01_up", MIGRATION_DIR / "up.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_is_a_python_migration_the_runner_will_run():
    # The runner prefers up.sql when both exist; a leftover scaffold up.sql
    # would silently shadow up.py.
    assert (MIGRATION_DIR / "up.py").exists()
    assert not (MIGRATION_DIR / "up.sql").exists()
    assert json.loads((MIGRATION_DIR / "meta.json").read_text(encoding="utf-8"))["database"] == "icdev"


def test_migration_applies_on_sqlite_as_a_stated_noop(tmp_path):
    from tools.db.storage import StorageConnection

    raw = sqlite3.connect(str(_make_db(tmp_path / "m.db", 0)))
    conn = StorageConnection(raw, "sqlite")
    out = _load_migration().up(conn)
    assert out["constraint_rebuilt"] is False
    assert out["note"] and "sqlite" in out["note"]
    assert rebuild_hook_type_constraint(conn) is False


def test_migration_rebuilds_the_named_constraint_on_postgres():
    class _PgConn:
        _backend = "postgresql"

        def __init__(self):
            self.statements = []

        def execute(self, sql, params=None):
            self.statements.append(" ".join(sql.split()))

        def commit(self):
            self.statements.append("COMMIT")

    conn = _PgConn()
    assert _load_migration().up(conn)["constraint_rebuilt"] is True
    drop, add, commit = conn.statements
    assert drop == f"ALTER TABLE hook_events DROP CONSTRAINT IF EXISTS {HOOK_TYPE_CONSTRAINT}"
    assert add.startswith(f"ALTER TABLE hook_events ADD CONSTRAINT {HOOK_TYPE_CONSTRAINT} CHECK")
    assert set(re.findall(r"'([^']+)'", add)) == set(HOOK_EVENT_TYPES)
    assert commit == "COMMIT"
    # the live constraint's name (pg_get_constraintdef, 2026-09-11), so DROP finds it
    assert HOOK_TYPE_CONSTRAINT == "hook_events_hook_type_check"


def test_constant_module_is_stdlib_only():
    tree = ast.parse((REPO_ROOT / "tools" / "hooks" / "hook_event_types.py").read_text(encoding="utf-8"))
    names = {n.module.split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
             for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
             for a in (n.names if isinstance(n, ast.Import) else [None])}
    assert names <= {"__future__"}, names
    assert hook_event_types.HOOK_EVENT_TYPES == HOOK_EVENT_TYPES


# ── headless parity ────────────────────────────────────────────────────


def test_headless_twin_shares_the_one_builder(seams, db_with_entries, tmp_path, monkeypatch):
    from tools.airgap import hook_compat

    seams(db_with_entries)
    result = hook_compat.run_session_start(directory=str(tmp_path), record=False)
    direct = session_context.build_block(directory=str(tmp_path), started=time.perf_counter())
    assert result["context"] == direct["context"]
    assert result["event_recorded"] is None

    # structurally: both callers import the builder; neither composes a line
    hook_src = HOOK_PATH.read_text(encoding="utf-8")
    compat_src = inspect_source(hook_compat.run_session_start)
    assert "from tools.hooks.session_context import build_block" in hook_src
    assert "from tools.hooks.session_context import build_block" in compat_src
    for src in (hook_src, compat_src):
        assert "## ICDEV session context" not in src


def inspect_source(fn) -> str:
    import inspect

    return inspect.getsource(fn)


def test_headless_twin_records_through_its_own_store_event(seams, db_with_entries, tmp_path, monkeypatch):
    from tools.airgap import hook_compat

    seams(db_with_entries)
    seen = {}

    def fake_store(session_id, hook_type, tool_name=None, payload=None, classification="CUI"):
        seen.update(session_id=session_id, hook_type=hook_type, payload=payload)
        return 7

    monkeypatch.setattr(hook_compat, "store_event", fake_store)
    result = hook_compat.run_session_start("s-headless", directory=str(tmp_path))
    assert result["event_recorded"] is True
    assert seen["hook_type"] == "session_start" and seen["session_id"] == "s-headless"
    assert seen["payload"]["source"] == "headless" and "context" not in seen["payload"]


# ── manifest drift ─────────────────────────────────────────────────────


@pytest.mark.parametrize("shard", [
    "tools/manifest/memory-system.md",
    "icdev/tools/manifest/memory-system.md",
])
def test_memory_manifest_names_no_phantom_file(shard):
    text = (REPO_ROOT / shard).read_text(encoding="utf-8")
    assert "tools/memory/scoped_provider.py" not in text
    assert "tools/memory/init_memory_db.py" not in text
    named = set(re.findall(r"\| (tools/memory/[a-z_]+\.py)", text))
    missing = sorted(p for p in named if not (REPO_ROOT / p).exists())
    assert not missing, missing
    assert ".claude/hooks/session_start.py" in text and "tools/hooks/session_context.py" in text
