#!/usr/bin/env python3
"""Test that generated child apps get a portable connection helper.

Acceptance (pgp-ca-02):
- Generated init script contains backend-agnostic _get_db_connection().
- It imports the parent storage layer (tools.db.storage) and disables RLS
  because child tables lack tenant_id/classification columns.
- It falls back to sqlite3.connect() when the storage layer is unavailable.
- When ICDEV_STORAGE_BACKEND=postgresql but PG is unreachable, the init
  script falls back to SQLite at init time (runtime never silently switches).
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.builder.db_init_generator import generate_init_script, write_init_script


def test_generated_init_has_portable_connection_helper():
    """Source-level check: the generated script wires get_connection + RLS skip."""
    blueprint = {
        "app_name": "test_child",
        "classification": "CUI",
        "capabilities": {},
    }
    src = generate_init_script(blueprint)

    assert "def _get_db_connection(db_path):" in src
    assert "from tools.db.storage import get_connection" in src
    assert "conn.set_security_context(None)" in src
    assert "sqlite3.connect(str(db_path))" in src


def test_generated_init_verify_is_backend_aware():
    """pgrt-sweep-06: the generated verify/list step must be backend-aware.

    It should prefer the shared translation-independent ``list_tables`` helper
    (which works on PostgreSQL and SQLite and does not rely on cursor-level
    ``%s`` handling), and only fall back to a raw ``sqlite_master`` probe.
    """
    blueprint = {
        "app_name": "test_child",
        "classification": "CUI",
        "capabilities": {"knowledge_graph": True},
    }
    src = generate_init_script(blueprint)

    # Preferred path: the shared, translation-independent helper.
    assert "from tools.db.storage import list_tables" in src
    assert "list_tables(conn)" in src
    # A raw sqlite_master probe survives only as a standalone fallback, guarded by
    # an `except` block (never the primary path).
    assert "except Exception:" in src
    assert '"SELECT name FROM sqlite_master' in src
    # The primary listing must not depend on cursor-level %s translation.
    assert src.index("list_tables(conn)") < src.index("SELECT name FROM sqlite_master")


def test_child_app_generator_inline_fallback_is_backend_aware(monkeypatch, tmp_path):
    """pgrt-sweep-06: the inline fallback init script (used when the sister
    db_init_generator cannot be imported) must also emit backend-aware listing."""
    from tools.builder import child_app_generator

    # Force the inline-fallback branch of step_05.
    monkeypatch.setattr(child_app_generator, "_import_sister", lambda *a, **k: None)

    result = child_app_generator.step_05_db_init_script(
        tmp_path, {"app_name": "fallback_child"}
    )
    assert result["method"] == "fallback"
    src = Path(result["script_path"]).read_text(encoding="utf-8")

    assert "from tools.db.storage import list_tables" in src
    assert "list_tables(conn)" in src
    assert "except Exception:" in src
    assert '"SELECT name FROM sqlite_master' in src
    assert src.index("list_tables(conn)") < src.index("SELECT name FROM sqlite_master")


def test_generated_init_creates_sqlite_db_when_backend_is_sqlite():
    """End-to-end: with ICDEV_STORAGE_BACKEND=sqlite the script creates a .db file."""
    blueprint = {
        "app_name": "test_child",
        "classification": "CUI",
        "capabilities": {},
    }
    src = generate_init_script(blueprint)

    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "init_test_child_db.py"
        script_path.write_text(src, encoding="utf-8")
        db_path = Path(tmp) / "data" / "test_child.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        env["ICDEV_STORAGE_BACKEND"] = "sqlite"

        result = subprocess.run(
            [sys.executable, str(script_path), "--db-path", str(db_path)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
        assert db_path.exists(), "SQLite DB was not created"
        assert "test_child database initialized" in result.stdout or "Database initialized" in result.stdout


def test_generated_init_fallback_to_sqlite_when_pg_unreachable():
    """Init-only fallback: PG unreachable -> SQLite .db created, no runtime switch."""
    blueprint = {
        "app_name": "test_child",
        "classification": "CUI",
        "capabilities": {},
    }
    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)
        script_path = write_init_script(blueprint, output_dir)
        db_path = output_dir / "data" / "test_child.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        env["ICDEV_STORAGE_BACKEND"] = "postgresql"
        env["ICDEV_DATABASE_URL"] = "postgresql://invalid:invalid@localhost:5432/nonexistent"
        env.pop("ICDEV_PG_NO_FALLBACK", None)

        result = subprocess.run(
            [sys.executable, str(script_path), "--db-path", str(db_path)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
        assert db_path.exists(), "SQLite fallback DB was not created when PG unreachable"
        assert "test_child database initialized" in result.stdout or "Database initialized" in result.stdout
