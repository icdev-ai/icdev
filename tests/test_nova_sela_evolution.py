# CUI // SP-CTI
"""Tests for the NOVA SELA evolution loop (tools/genesis/reflexes/evolution.py).

Acceptance criteria:
  - After seeding 10 failure traces for a skill, run_evolution() produces
    >= 1 improvement_artifact row in agent_improvement_artifacts.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure repo root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._sql_compat import translating  # noqa: E402


# ── In-memory DB fixture ──────────────────────────────────────────────────────

_NOVA_SCHEMA = """
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
CREATE TABLE IF NOT EXISTS memory_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    type TEXT DEFAULT 'event',
    importance INTEGER DEFAULT 5,
    created_at TEXT DEFAULT (datetime('now')),
    source TEXT DEFAULT 'manual',
    classification TEXT DEFAULT 'CUI'
);
"""


@pytest.fixture()
def conn():
    """In-memory SQLite connection with NOVA tables pre-created.

    Handed straight to ``tools.genesis.reflexes.evolution`` via a patched
    ``tools.db.storage.get_connection``, so it must behave like the
    ``StorageConnection`` it stands in for: evolution.py authors ``%s``
    placeholders for PostgreSQL and only that wrapper rewrites them to ``?``.
    A bare sqlite3 connection made ``_query_low_performing_skills`` raise
    ``near "%": syntax error`` into its own ``except Exception`` — it logged a
    warning and returned ``[]``, so the reflex reported "no low performers"
    and the tests read as a missing feature rather than a broken fixture.

    ``unclosable=True`` replaces the old ``_NoCloseConn`` shim: run_evolution()
    closes the connection it is given, which would drop this in-memory DB
    before the test can assert against it.
    """
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(_NOVA_SCHEMA)
    db.commit()
    yield translating(db, unclosable=True)
    db.close()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _past(days: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _seed_lessons(db, task_ids) -> None:
    """Seed the lesson_learned rows a mutation's evidence bundle joins against.

    exa-refine-04 made an artifact carry the lessons that motivated it and
    rejects one that has none, so a fixture that seeds only traces now
    (correctly) produces a rejected artifact rather than a pending one.
    """
    for task_id in task_ids:
        db.execute(
            "INSERT INTO memory_entries (content, type, importance, created_at, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                json.dumps({
                    "task_id": task_id,
                    "task_title": f"Title for {task_id}",
                    "outcome": "failure",
                    "pattern": "timeout_quarantine",
                    "category": "Timeout quarantine",
                    "failure_count": 2,
                    "last_failure_reason": "timeout on dispatch",
                    "transitions_count": 3,
                    "recurrence_score": 0.4,
                    "is_systemic": True,
                    "recommendation": "reduce retries or add timeout handling",
                    "timestamp": _past(1),
                }, sort_keys=True),
                "lesson_learned", 8, _past(1), "auto",
            ),
        )
    db.commit()


def _seed_traces(db, skill: str, count: int, outcome: str = "failure",
                 task_id_prefix: str | None = None) -> list[str]:
    """Seed traces; returns the task ids so lessons can be attached to them."""
    task_ids: list[str] = []
    for i in range(count):
        task_id = f"{task_id_prefix}-{i}" if task_id_prefix else str(uuid.uuid4())
        task_ids.append(task_id)
        db.execute(
            """
            INSERT INTO agent_execution_traces
                (trace_id, task_id, task_type, skill_used, outcome,
                 events_json, lesson_pattern, improvement_notes,
                 started_at, completed_at, created_at)
            VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                task_id,
                "test_task",
                skill,
                outcome,
                f"timeout on {skill}",
                "reduce retries or add timeout handling",
                _past(1),
                _past(1),
                _past(1),
            ),
        )
    db.commit()
    return task_ids


def _count_artifacts(db, skill: str) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM agent_improvement_artifacts WHERE skill_used = ?",
        (skill,),
    ).fetchone()
    return int(row[0]) if row else 0


# ── Unit: _query_low_performing_skills ───────────────────────────────────────

def test_query_returns_low_performers(conn):
    from tools.genesis.reflexes.evolution import _query_low_performing_skills

    _seed_traces(conn, "slow_skill", 10, outcome="failure")
    _seed_traces(conn, "good_skill", 10, outcome="success")

    results = _query_low_performing_skills(
        conn, window_days=7, success_rate_threshold=0.75,
        min_trace_count=3, skill_limit=10,
    )

    skills = [r["skill_used"] for r in results]
    assert "slow_skill" in skills
    assert "good_skill" not in skills


def test_query_respects_min_trace_count(conn):
    from tools.genesis.reflexes.evolution import _query_low_performing_skills

    _seed_traces(conn, "rare_failure_skill", 2, outcome="failure")  # below min_trace_count=3

    results = _query_low_performing_skills(
        conn, window_days=7, success_rate_threshold=0.75,
        min_trace_count=3, skill_limit=10,
    )
    skills = [r["skill_used"] for r in results]
    assert "rare_failure_skill" not in skills


def test_query_respects_window(conn):
    from tools.genesis.reflexes.evolution import _query_low_performing_skills

    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds")
    for _ in range(5):
        conn.execute(
            """
            INSERT INTO agent_execution_traces
                (trace_id, task_id, task_type, skill_used, outcome,
                 events_json, lesson_pattern, improvement_notes,
                 started_at, completed_at, created_at)
            VALUES (?, ?, ?, ?, 'failure', '[]', '', '', ?, ?, ?)
            """,
            (str(uuid.uuid4()), str(uuid.uuid4()), "t", "ancient_skill", old_ts, old_ts, old_ts),
        )
    conn.commit()

    results = _query_low_performing_skills(
        conn, window_days=7, success_rate_threshold=0.75,
        min_trace_count=3, skill_limit=10,
    )
    skills = [r["skill_used"] for r in results]
    assert "ancient_skill" not in skills


# ── Unit: _score_mutation ─────────────────────────────────────────────────────

def test_score_mutation_weights():
    from tools.genesis.reflexes.evolution import _score_mutation

    fitness = {"correctness": 0.5, "procedure": 0.3, "conciseness": 0.2}
    result = _score_mutation("add timeout handling for retry", "retry", fitness)

    assert 0.0 <= result["composite_score"] <= 1.0
    assert set(result.keys()) == {
        "mutation", "correctness", "procedure", "conciseness", "composite_score"
    }
    expected = round(
        0.5 * result["correctness"]
        + 0.3 * result["procedure"]
        + 0.2 * result["conciseness"],
        4,
    )
    assert abs(result["composite_score"] - expected) < 1e-6


def test_score_short_mutation_higher_conciseness():
    from tools.genesis.reflexes.evolution import _score_mutation

    fitness = {"correctness": 0.5, "procedure": 0.3, "conciseness": 0.2}
    short = _score_mutation("add retry limit", "skill", fitness)
    long_text = "add retry limit " + " ".join(["word"] * 100)
    long = _score_mutation(long_text, "skill", fitness)
    assert short["conciseness"] >= long["conciseness"]


# ── Integration: run_evolution (mutations mocked) ────────────────────────────

_GOOD_MUTATIONS = [
    "add validation and timeout handling for retry loop",
    "cache previous results to avoid redundant calls",
    "limit concurrency to prevent resource exhaustion",
    "log failure context for better debugging",
]


def test_run_evolution_produces_artifact(conn):
    """Seed 10 failure traces → run_evolution() → >= 1 artifact row."""
    from tools.genesis.reflexes import evolution

    skill = "test_skill_sela"
    task_ids = _seed_traces(conn, skill, 10, outcome="failure", task_id_prefix="sela-task")
    # exa-refine-04: an artifact must carry the lessons that motivated it.
    _seed_lessons(conn, task_ids)

    with patch("tools.genesis.reflexes.evolution._generate_mutations",
               return_value=_GOOD_MUTATIONS), \
         patch("tools.db.storage.get_connection", return_value=conn):
        result = evolution.run_evolution(config={
            "trace_window_days": 7,
            "success_rate_threshold": 0.75,
            "min_trace_count": 3,
            "skill_limit": 10,
            "mutation_count": 4,
            "artifact_min_score": 0.5,  # lowered so deterministic scorer passes
            "fitness": {"correctness": 0.5, "procedure": 0.3, "conciseness": 0.2},
            "mutation_function": "skill_mutation",
        })

    assert result["skills_evaluated"] >= 1
    assert result["artifacts_created"] >= 1
    assert any(a["skill"] == skill for a in result["artifacts"])

    artifact_count = _count_artifacts(conn, skill)
    assert artifact_count >= 1, f"Expected row in agent_improvement_artifacts, got {artifact_count}"

    # ...and it is 'pending' (selectable), carrying its motivating lesson rows.
    row = conn.execute(
        "SELECT status, evidence_traces FROM agent_improvement_artifacts "
        "WHERE skill_used = ?",
        (skill,),
    ).fetchone()
    assert row[0] == "pending"
    evidence = json.loads(row[1])
    assert evidence["lesson_count"] >= 1
    assert evidence["dominant_pattern"] == "timeout_quarantine"


def test_run_evolution_rejects_a_mutation_with_no_lesson_evidence(conn):
    """No lesson_learned rows behind a skill → the mutation never goes 'pending'.

    'pending' is what GEPA selects on, so a non-'pending' status is what keeps an
    unmotivated refinement away from a human reviewer (exa-refine-04).
    """
    from tools.genesis.reflexes import evolution

    skill = "unmotivated_skill"
    _seed_traces(conn, skill, 10, outcome="failure", task_id_prefix="no-lesson")
    # deliberately NO _seed_lessons

    with patch("tools.genesis.reflexes.evolution._generate_mutations",
               return_value=_GOOD_MUTATIONS), \
         patch("tools.db.storage.get_connection", return_value=conn):
        result = evolution.run_evolution(config={
            "trace_window_days": 7,
            "success_rate_threshold": 0.75,
            "min_trace_count": 3,
            "skill_limit": 10,
            "mutation_count": 4,
            "artifact_min_score": 0.5,
            "fitness": {"correctness": 0.5, "procedure": 0.3, "conciseness": 0.2},
            "mutation_function": "skill_mutation",
        })

    assert result["artifacts_created"] == 0
    assert result["artifacts_rejected_no_evidence"] >= 1
    row = conn.execute(
        "SELECT status FROM agent_improvement_artifacts WHERE skill_used = ?",
        (skill,),
    ).fetchone()
    assert row[0] != "pending"


def test_run_evolution_skips_when_no_mutations(conn):
    """If _generate_mutations returns [], skill goes to skipped_skills."""
    from tools.genesis.reflexes import evolution

    skill = "unmutable_skill"
    _seed_traces(conn, skill, 10, outcome="failure")

    with patch("tools.genesis.reflexes.evolution._generate_mutations", return_value=[]), \
         patch("tools.db.storage.get_connection", return_value=conn):
        result = evolution.run_evolution(config={
            "trace_window_days": 7,
            "success_rate_threshold": 0.75,
            "min_trace_count": 3,
            "skill_limit": 10,
            "mutation_count": 4,
            "artifact_min_score": 0.75,
            "fitness": {"correctness": 0.5, "procedure": 0.3, "conciseness": 0.2},
            "mutation_function": "skill_mutation",
        })

    assert skill in result["skipped_skills"]
    assert _count_artifacts(conn, skill) == 0


def test_run_evolution_skips_below_score_threshold(conn):
    """Mutations scoring below artifact_min_score are NOT inserted."""
    from tools.genesis.reflexes import evolution

    skill = "low_score_skill"
    _seed_traces(conn, skill, 10, outcome="failure")

    # Generic single-word mutations score poorly
    low_mutations = ["improve it", "make it better", "update it", "change something"]

    with patch("tools.genesis.reflexes.evolution._generate_mutations",
               return_value=low_mutations), \
         patch("tools.db.storage.get_connection", return_value=conn):
        result = evolution.run_evolution(config={
            "trace_window_days": 7,
            "success_rate_threshold": 0.75,
            "min_trace_count": 3,
            "skill_limit": 10,
            "mutation_count": 4,
            "artifact_min_score": 0.99,  # virtually unreachable threshold
            "fitness": {"correctness": 0.5, "procedure": 0.3, "conciseness": 0.2},
            "mutation_function": "skill_mutation",
        })

    assert _count_artifacts(conn, skill) == 0
    assert skill in result["skipped_skills"] or result["artifacts_created"] == 0


# ── Genesis reflex envelope: run() ───────────────────────────────────────────

def test_run_reflex_envelope(conn):
    from tools.genesis.reflexes import evolution

    _seed_traces(conn, "envelope_skill", 5, outcome="failure")

    mutations = ["add timeout handling for envelope_skill retry"]
    with patch("tools.genesis.reflexes.evolution._generate_mutations",
               return_value=mutations), \
         patch("tools.db.storage.get_connection", return_value=conn):
        result = evolution.run(
            config={
                "trace_window_days": 7,
                "success_rate_threshold": 0.75,
                "min_trace_count": 3,
                "skill_limit": 10,
                "mutation_count": 4,
                "artifact_min_score": 0.3,
                "fitness": {"correctness": 0.5, "procedure": 0.3, "conciseness": 0.2},
                "mutation_function": "skill_mutation",
            },
            trust=None,
        )

    assert result["success"] is True
    assert "metric_value" in result
    assert "details" in result
    assert isinstance(result["metric_value"], float)
