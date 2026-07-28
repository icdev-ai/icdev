"""dwo-mem-01 — run-scoped memory store for Studio workflow steps."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.studio import run_memory

_ROOT = Path(__file__).resolve().parents[1]
_CLI = _ROOT / "tools" / "studio" / "run_memory.py"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS studio_run_memory (
    run_id     TEXT NOT NULL,
    key        TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, key)
);
"""


@pytest.fixture(autouse=True)
def _memory_db(tmp_path, monkeypatch):
    """Point storage at a throwaway SQLite db holding only the memory table."""
    import sqlite3

    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.delenv(run_memory.RUN_ID_ENV, raising=False)
    return db_path


def test_set_get_roundtrip():
    run_memory.set("run-a", "answer", {"n": 42, "ok": True})
    assert run_memory.get("run-a", "answer") == {"n": 42, "ok": True}


def test_get_missing_returns_default():
    assert run_memory.get("run-a", "nope") is None
    assert run_memory.get("run-a", "nope", default="fallback") == "fallback"


def test_set_overwrites():
    run_memory.set("run-b", "k", "first")
    run_memory.set("run-b", "k", "second")
    assert run_memory.get("run-b", "k") == "second"


def test_all_returns_every_key():
    run_memory.set("run-c", "one", 1)
    run_memory.set("run-c", "two", [2])
    assert run_memory.all("run-c") == {"one": 1, "two": [2]}


def test_delete():
    run_memory.set("run-d", "gone", "x")
    assert run_memory.delete("run-d", "gone") is True
    assert run_memory.get("run-d", "gone") is None
    assert run_memory.delete("run-d", "gone") is False


def test_memory_does_not_leak_between_runs():
    run_memory.set("run-e1", "secret", "e1-value")
    run_memory.set("run-e2", "secret", "e2-value")
    assert run_memory.get("run-e1", "secret") == "e1-value"
    assert run_memory.get("run-e2", "secret") == "e2-value"
    assert run_memory.get("run-e3", "secret") is None
    assert run_memory.all("run-e3") == {}


def test_copy_snapshots_parent_and_leaves_it_immutable():
    """A resume that forks a new run id inherits a copy of its parent."""
    run_memory.set("parent-run", "carried", {"step": "a"})
    copied = run_memory.copy("parent-run", "child-run")
    assert copied == 1
    assert run_memory.get("child-run", "carried") == {"step": "a"}

    run_memory.set("child-run", "carried", {"step": "b"})
    assert run_memory.get("parent-run", "carried") == {"step": "a"}


def test_same_run_id_resume_keeps_memory():
    """resume_run() re-enters the same run id, so memory survives unchanged."""
    run_memory.set("resumed-run", "progress", 3)
    # Nothing happens to memory across a same-id resume.
    assert run_memory.get("resumed-run", "progress") == 3


def test_run_id_required_when_env_absent(monkeypatch):
    monkeypatch.delenv(run_memory.RUN_ID_ENV, raising=False)
    with pytest.raises(ValueError):
        run_memory.get("", "k")


def test_run_id_falls_back_to_env(monkeypatch):
    monkeypatch.setenv(run_memory.RUN_ID_ENV, "env-run")
    run_memory.set("", "from-env", "yes")
    assert run_memory.get("env-run", "from-env") == "yes"


def test_inputs_key_is_reserved_name():
    assert run_memory.INPUTS_KEY == "_inputs"
    run_memory.set("run-inputs", run_memory.INPUTS_KEY, {"target": "x"})
    assert run_memory.get("run-inputs", "_inputs") == {"target": "x"}


# ── Acceptance: separate subprocesses ──────────────────────

def _cli(args, db_path, run_id=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["ICDEV_STORAGE_BACKEND"] = "sqlite"
    env["ICDEV_DB_PATH"] = str(db_path)
    if run_id:
        env["ICDEV_RUN_ID"] = run_id
    return subprocess.run(
        [sys.executable, str(_CLI), *args],
        capture_output=True,
        text=True,
        cwd=str(_ROOT),
        env=env,
        timeout=120,
    )


def test_step_a_writes_step_b_reads_across_subprocesses(_memory_db):
    """Acceptance: step A writes a key, step B reads it, separate processes."""
    db_path = _memory_db

    # Step A — writes, taking its run id from the runner-supplied env var.
    step_a = _cli(["--set", "handoff", "--value", '{"artifact": "report.pdf"}'],
                  db_path, run_id="proc-run")
    assert step_a.returncode == 0, step_a.stderr
    assert json.loads(step_a.stdout)["status"] == "ok"

    # Step B — a different process, reads what step A wrote.
    step_b = _cli(["--get", "handoff"], db_path, run_id="proc-run")
    assert step_b.returncode == 0, step_b.stderr
    assert json.loads(step_b.stdout)["value"] == {"artifact": "report.pdf"}

    # A different run sees nothing.
    other = _cli(["--get", "handoff"], db_path, run_id="other-run")
    assert json.loads(other.stdout)["value"] is None


def test_runner_exports_run_id_to_the_step_environment():
    """The contract: the runner puts ICDEV_RUN_ID in every step's env."""
    source = (_ROOT / "tools" / "studio" / "workflow_runner.py").read_text(encoding="utf-8")
    assert '_env["ICDEV_RUN_ID"] = run_id' in source
