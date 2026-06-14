# CUI // SP-CTI
"""Tests for tools.workflow.reflexion_agent — NOVA ECHO post-task improvement generator.

Acceptance criteria:
  1. Import succeeds.
  2. ICDEV_HARNESS_COLEARN=false  → generate_improvement_artifact returns skipped dict.
  3. ICDEV_HARNESS_COLEARN=true + mocked LLMRouter → artifact written to
     agent_improvement_artifacts table.
  4. All tests pass.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure repo root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Force SQLite backend
os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_in_memory_conn():
    """Return an in-memory SQLite connection with NOVA tables created."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Create NOVA tables directly (avoids touching storage.get_connection)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_execution_traces (
            trace_id          TEXT PRIMARY KEY,
            task_id           TEXT NOT NULL,
            task_type         TEXT NOT NULL DEFAULT '',
            skill_used        TEXT NOT NULL DEFAULT '',
            outcome           TEXT NOT NULL DEFAULT 'unknown',
            events_json       TEXT NOT NULL DEFAULT '[]',
            lesson_pattern    TEXT NOT NULL DEFAULT '',
            improvement_notes TEXT NOT NULL DEFAULT '',
            started_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at      TEXT NOT NULL DEFAULT '',
            created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS agent_improvement_artifacts (
            artifact_id      TEXT PRIMARY KEY,
            task_type        TEXT NOT NULL,
            skill_used       TEXT NOT NULL DEFAULT '',
            generation_n     INTEGER NOT NULL DEFAULT 1,
            improvement_text TEXT NOT NULL,
            composite_score  REAL NOT NULL DEFAULT 0.0,
            baseline_score   REAL NOT NULL DEFAULT 0.0,
            evidence_traces  TEXT NOT NULL DEFAULT '[]',
            applied_count    INTEGER NOT NULL DEFAULT 0,
            status           TEXT NOT NULL DEFAULT 'pending',
            created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            applied_at       TEXT
        );
        CREATE TABLE IF NOT EXISTS ace_coworker_memory (
            id               TEXT PRIMARY KEY,
            role_id          TEXT NOT NULL,
            fact_type        TEXT NOT NULL DEFAULT 'observation',
            content          TEXT NOT NULL,
            confidence       REAL NOT NULL DEFAULT 0.8,
            source_task_id   TEXT NOT NULL DEFAULT '',
            created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS ace_trust_ledger (
            id              TEXT PRIMARY KEY,
            role_id         TEXT NOT NULL,
            delta           REAL NOT NULL,
            reason          TEXT NOT NULL,
            new_score       REAL NOT NULL,
            source_task_id  TEXT NOT NULL DEFAULT '',
            recorded_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    return conn


def _seed_traces(conn, task_type: str, n_success: int = 3, n_failure: int = 2):
    """Insert synthetic completed traces into agent_execution_traces."""
    rows = []
    for i in range(n_success):
        rows.append((
            f"trace-s{i}", f"task-s{i}", task_type, "icdev-build",
            "success", "[]", "test_pattern", "looks good",
            "2026-01-01T00:00:00", "2026-01-01T00:01:00",
        ))
    for i in range(n_failure):
        rows.append((
            f"trace-f{i}", f"task-f{i}", task_type, "icdev-build",
            "failure", "[]", "import_error", "module not found",
            "2026-01-01T00:00:00", "2026-01-01T00:01:00",
        ))
    conn.executemany(
        """
        INSERT INTO agent_execution_traces
            (trace_id, task_id, task_type, skill_used, outcome, events_json,
             lesson_pattern, improvement_notes, started_at, completed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — import
# ─────────────────────────────────────────────────────────────────────────────

def test_reflexion_agent_import():
    """Module must be importable without errors."""
    try:
        import tools.workflow.reflexion_agent as mod  # noqa: F401
        assert hasattr(mod, "generate_improvement_artifact")
        assert hasattr(mod, "get_latest_improvement")
        assert hasattr(mod, "run_batch_reflexion")
    except ImportError as exc:
        pytest.fail(f"Import failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — colearn disabled → skipped result
# ─────────────────────────────────────────────────────────────────────────────

def test_generate_returns_skipped_when_colearn_disabled(monkeypatch):
    """With ICDEV_HARNESS_COLEARN=false, generate_improvement_artifact returns skipped."""
    monkeypatch.setenv("ICDEV_HARNESS_COLEARN", "false")

    # Reload module so the module-level _COLEARN_ENABLED re-evaluates
    import tools.workflow.reflexion_agent as mod
    monkeypatch.setattr(mod, "_COLEARN_ENABLED", False)

    result = mod.generate_improvement_artifact("build", skill_used="icdev-build")
    assert result.get("skipped") is True
    assert "ICDEV_HARNESS_COLEARN" in result.get("reason", "")


def test_get_latest_improvement_returns_empty_when_colearn_disabled(monkeypatch):
    """get_latest_improvement returns '' when co-learning is off."""
    import tools.workflow.reflexion_agent as mod
    monkeypatch.setattr(mod, "_COLEARN_ENABLED", False)

    result = mod.get_latest_improvement("build")
    assert result == ""


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — colearn enabled + mocked LLM → artifact persisted
# ─────────────────────────────────────────────────────────────────────────────

def test_generate_persists_artifact_with_mocked_llm(monkeypatch):
    """With colearn=true and mocked LLMRouter, artifact is written to DB."""
    import tools.workflow.reflexion_agent as mod

    monkeypatch.setattr(mod, "_COLEARN_ENABLED", True)

    conn = _make_in_memory_conn()
    _seed_traces(conn, "build", n_success=3, n_failure=2)

    # Mock get_traces_for_task_type to return from our in-memory conn
    def _fake_traces(task_type, limit=20):
        rows = conn.execute(
            "SELECT trace_id, task_id, task_type, skill_used, outcome, "
            "lesson_pattern, improvement_notes, started_at, completed_at "
            "FROM agent_execution_traces WHERE task_type = ? AND outcome != 'in_progress' "
            "LIMIT ?",
            (task_type, limit),
        ).fetchall()
        cols = ["trace_id", "task_id", "task_type", "skill_used", "outcome",
                "lesson_pattern", "improvement_notes", "started_at", "completed_at"]
        return [dict(zip(cols, row)) for row in rows]

    monkeypatch.setattr(mod, "get_traces_for_task_type", _fake_traces)

    # Mock _call_llm to return deterministic text
    monkeypatch.setattr(mod, "_call_llm", lambda prompt, skill: "## Root Cause\nTest failure. ## Proposed Improvements\n1. Fix imports.")

    # Mock _conn to return our in-memory connection
    monkeypatch.setattr(mod, "_conn", lambda: conn)

    # Mock _ensure_tables to no-op (tables already created)
    monkeypatch.setattr(mod, "_ensure_tables", lambda c: None)

    result = mod.generate_improvement_artifact("build", skill_used="icdev-build", dry_run=False)

    assert "artifact_id" in result, f"Expected artifact_id, got: {result}"
    assert result["task_type"] == "build"
    assert result["traces_analyzed"] == 5
    assert result["failures_found"] == 2
    assert result["dry_run"] is False

    # Verify the artifact was written to the DB
    row = conn.execute(
        "SELECT artifact_id, task_type, generation_n, status FROM agent_improvement_artifacts "
        "WHERE task_type = 'build'"
    ).fetchone()
    assert row is not None, "No artifact row found in agent_improvement_artifacts"
    assert row["task_type"] == "build"
    assert row["status"] == "pending"
    assert row["generation_n"] == 1


def test_generate_dry_run_does_not_write_to_db(monkeypatch):
    """dry_run=True returns artifact dict without writing to DB."""
    import tools.workflow.reflexion_agent as mod

    monkeypatch.setattr(mod, "_COLEARN_ENABLED", True)

    conn = _make_in_memory_conn()
    _seed_traces(conn, "test", n_success=4, n_failure=1)

    def _fake_traces(task_type, limit=20):
        rows = conn.execute(
            "SELECT trace_id, task_id, task_type, skill_used, outcome, "
            "lesson_pattern, improvement_notes, started_at, completed_at "
            "FROM agent_execution_traces WHERE task_type = ? AND outcome != 'in_progress' "
            "LIMIT ?",
            (task_type, limit),
        ).fetchall()
        cols = ["trace_id", "task_id", "task_type", "skill_used", "outcome",
                "lesson_pattern", "improvement_notes", "started_at", "completed_at"]
        return [dict(zip(cols, row)) for row in rows]

    monkeypatch.setattr(mod, "get_traces_for_task_type", _fake_traces)
    monkeypatch.setattr(mod, "_call_llm", lambda p, s: "Dry run improvement text.")
    monkeypatch.setattr(mod, "_conn", lambda: conn)
    monkeypatch.setattr(mod, "_ensure_tables", lambda c: None)

    result = mod.generate_improvement_artifact("test", dry_run=True)

    assert result.get("dry_run") is True
    assert "artifact_id" in result

    # Nothing should be in DB
    count = conn.execute(
        "SELECT COUNT(*) FROM agent_improvement_artifacts WHERE task_type = 'test'"
    ).fetchone()[0]
    assert count == 0, f"Expected 0 rows in dry_run, found {count}"


def test_generate_skips_when_insufficient_traces(monkeypatch):
    """generate_improvement_artifact skips when fewer than 3 traces exist."""
    import tools.workflow.reflexion_agent as mod

    monkeypatch.setattr(mod, "_COLEARN_ENABLED", True)

    conn = _make_in_memory_conn()
    _seed_traces(conn, "sparse", n_success=1, n_failure=1)  # only 2 traces

    def _fake_traces(task_type, limit=20):
        rows = conn.execute(
            "SELECT trace_id, task_id, task_type, skill_used, outcome, "
            "lesson_pattern, improvement_notes, started_at, completed_at "
            "FROM agent_execution_traces WHERE task_type = ? AND outcome != 'in_progress' "
            "LIMIT ?",
            (task_type, limit),
        ).fetchall()
        cols = ["trace_id", "task_id", "task_type", "skill_used", "outcome",
                "lesson_pattern", "improvement_notes", "started_at", "completed_at"]
        return [dict(zip(cols, row)) for row in rows]

    monkeypatch.setattr(mod, "get_traces_for_task_type", _fake_traces)

    result = mod.generate_improvement_artifact("sparse")
    assert result.get("skipped") is True
    assert "insufficient" in result.get("reason", "")


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — run_batch_reflexion
# ─────────────────────────────────────────────────────────────────────────────

def test_run_batch_reflexion_processes_specified_types(monkeypatch):
    """run_batch_reflexion processes the provided task_types list."""
    import tools.workflow.reflexion_agent as mod

    monkeypatch.setattr(mod, "_COLEARN_ENABLED", True)

    # Patch generate_improvement_artifact to return a predictable result
    captured = []

    def _fake_generate(task_type, skill_used="", window=20, dry_run=False):
        captured.append(task_type)
        return {"artifact_id": f"impr-{task_type[:8]}-abc", "task_type": task_type, "dry_run": dry_run}

    monkeypatch.setattr(mod, "generate_improvement_artifact", _fake_generate)

    result = mod.run_batch_reflexion(task_types=["build", "test", "review"], dry_run=True)

    assert result["task_types_processed"] == 3
    assert set(result["results"].keys()) == {"build", "test", "review"}
    assert captured == ["build", "test", "review"]


def test_run_batch_reflexion_empty_when_colearn_disabled(monkeypatch):
    """run_batch_reflexion with explicit list still routes through generate,
    which returns skipped when co-learning is off."""
    import tools.workflow.reflexion_agent as mod

    monkeypatch.setattr(mod, "_COLEARN_ENABLED", False)

    result = mod.run_batch_reflexion(task_types=["build"])
    assert result["task_types_processed"] == 1
    assert result["results"]["build"].get("skipped") is True


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — compute score helper
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_score_all_success():
    """_compute_score returns 1.0 when all traces are success."""
    import tools.workflow.reflexion_agent as mod
    traces = [{"outcome": "success"}] * 5
    assert mod._compute_score(traces) == 1.0


def test_compute_score_mixed():
    """_compute_score returns correct fraction for mixed outcomes."""
    import tools.workflow.reflexion_agent as mod
    traces = [
        {"outcome": "success"},
        {"outcome": "success"},
        {"outcome": "failure"},
        {"outcome": "timeout"},
    ]
    assert mod._compute_score(traces) == 0.5


def test_compute_score_empty():
    """_compute_score returns 0.0 for empty trace list."""
    import tools.workflow.reflexion_agent as mod
    assert mod._compute_score([]) == 0.0
