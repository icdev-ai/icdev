#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for NOVA ECHO — tools/workflow/reflexion_agent.py

Validates: import, colearn-gated behaviour, _compute_score, dry_run,
DB persistence, run_batch_reflexion discovery, get_latest_improvement.
All tests run on the SQLite test backend.

Because _COLEARN_ENABLED is a module-level constant, tests that need it
enabled monkeypatch the module attribute directly.
"""

import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_traces(task_type: str, n_success: int = 3, n_failure: int = 0) -> list[str]:
    """Insert traces directly for a given task_type and close them.

    Returns the *task* ids (not the trace ids): those are what the exa-refine-04
    evidence gate joins on, so a caller that needs a supported proposal can feed
    them straight to `_seed_lessons`.
    """
    from tools.workflow.trace_logger import start_trace, close_trace
    task_ids = []
    for i in range(n_success):
        task_id = f"task-{uuid.uuid4().hex[:6]}"
        close_trace(start_trace(task_id, task_type, "icdev-build"), "success", "success_first_try")
        task_ids.append(task_id)
    for i in range(n_failure):
        task_id = f"task-{uuid.uuid4().hex[:6]}"
        close_trace(
            start_trace(task_id, task_type, "icdev-build"),
            "failure", "missing_dependency", "dependency not found",
        )
        task_ids.append(task_id)
    return task_ids


def _supporting_evidence(task_type: str, task_ids: list[str],
                         pattern: str = "missing_dependency") -> dict:
    """A `refinement_evidence/v1` bundle that the exa-refine-04 gate accepts.

    `tools/workflow/refinement_evidence.py` rejects any proposal whose trajectory
    has no `lesson_learned` rows behind it — deliberately, so a refinement
    motivated by nothing can never reach GEPA or a review queue. Traces alone are
    therefore NOT enough to produce a 'pending' artifact.

    The lessons live in `memory_entries`, which the SQLite test backend does not
    create, so tests that need an *accepted* proposal patch `collect_evidence`
    with this bundle rather than seeding that table. Only collection is stubbed:
    the real `evaluate_evidence` gate still runs and still has to pass. The
    rejection path needs no stub at all — see
    `test_generate_rejects_artifact_with_no_lesson_evidence`, which exercises
    collection for real.
    """
    from tools.workflow.refinement_evidence import EVIDENCE_SCHEMA

    lessons = [
        {"task_id": tid, "pattern": pattern, "category": "dependency",
         "outcome": "failure", "recurrence_score": 0.5, "is_systemic": False}
        for tid in task_ids
    ]
    return {
        "schema": EVIDENCE_SCHEMA,
        "task_type": task_type,
        "task_ids": list(task_ids),
        "lessons": lessons,
        "lesson_count": len(lessons),
        "patterns": [{"pattern": pattern, "lesson_count": len(lessons),
                      "recurrence_score": 0.5, "is_systemic": False}],
        "recurrence_score": 0.5,
        "dominant_pattern": pattern,
        "systemic_count": 0,
    }


def _unique_type() -> str:
    return f"nova-reflex-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Import check
# ---------------------------------------------------------------------------

def test_reflexion_agent_imports():
    """Module imports without error."""
    import tools.workflow.reflexion_agent  # noqa: F401


def test_reflexion_agent_functions_exist():
    """All public functions are present and callable."""
    from tools.workflow.reflexion_agent import (
        generate_improvement_artifact,
        get_latest_improvement,
        run_batch_reflexion,
    )
    for fn in (generate_improvement_artifact, get_latest_improvement, run_batch_reflexion):
        assert callable(fn)


# ---------------------------------------------------------------------------
# Colearn disabled (default) behaviour
# ---------------------------------------------------------------------------

def test_generate_returns_skipped_when_colearn_disabled():
    """With ICDEV_HARNESS_COLEARN disabled, generate_improvement_artifact returns skipped."""
    import tools.workflow.reflexion_agent as ra
    orig = ra._COLEARN_ENABLED
    ra._COLEARN_ENABLED = False
    try:
        result = ra.generate_improvement_artifact("build", dry_run=False)
        assert result.get("skipped") is True
        assert "ICDEV_HARNESS_COLEARN" in result.get("reason", "")
    finally:
        ra._COLEARN_ENABLED = orig


def test_get_latest_improvement_empty_when_colearn_disabled():
    """get_latest_improvement returns '' when colearn is disabled."""
    import tools.workflow.reflexion_agent as ra
    orig = ra._COLEARN_ENABLED
    ra._COLEARN_ENABLED = False
    try:
        result = ra.get_latest_improvement("build")
        assert result == ""
    finally:
        ra._COLEARN_ENABLED = orig


def test_run_batch_reflexion_skipped_when_colearn_disabled():
    """run_batch_reflexion with colearn disabled returns skipped results."""
    import tools.workflow.reflexion_agent as ra
    orig = ra._COLEARN_ENABLED
    ra._COLEARN_ENABLED = False
    try:
        task_type = _unique_type()
        _seed_traces(task_type, n_success=5)
        result = ra.run_batch_reflexion([task_type])
        assert result["task_types_processed"] == 1
        assert result["results"][task_type].get("skipped") is True
    finally:
        ra._COLEARN_ENABLED = orig


# ---------------------------------------------------------------------------
# _compute_score (internal, imported for direct testing)
# ---------------------------------------------------------------------------

def test_compute_score_all_success():
    """_compute_score returns 1.0 when all traces succeeded."""
    from tools.workflow.reflexion_agent import _compute_score
    traces = [{"outcome": "success"} for _ in range(5)]
    assert _compute_score(traces) == 1.0


def test_compute_score_all_failure():
    """_compute_score returns 0.0 when all traces failed."""
    from tools.workflow.reflexion_agent import _compute_score
    traces = [{"outcome": "failure"} for _ in range(4)]
    assert _compute_score(traces) == 0.0


def test_compute_score_mixed():
    """_compute_score returns correct fraction for mixed outcomes."""
    from tools.workflow.reflexion_agent import _compute_score
    traces = [
        {"outcome": "success"},
        {"outcome": "success"},
        {"outcome": "failure"},
        {"outcome": "timeout"},
    ]
    score = _compute_score(traces)
    assert abs(score - 0.5) < 0.001


def test_compute_score_empty():
    """_compute_score returns 0.0 for empty trace list."""
    from tools.workflow.reflexion_agent import _compute_score
    assert _compute_score([]) == 0.0


# ---------------------------------------------------------------------------
# Colearn enabled — insufficient traces
# ---------------------------------------------------------------------------

def test_generate_skipped_when_insufficient_traces():
    """generate_improvement_artifact skips when fewer than 3 traces exist."""
    import tools.workflow.reflexion_agent as ra
    orig = ra._COLEARN_ENABLED
    ra._COLEARN_ENABLED = True
    try:
        task_type = _unique_type()
        _seed_traces(task_type, n_success=1)  # only 1 trace
        result = ra.generate_improvement_artifact(task_type, dry_run=True)
        assert result.get("skipped") is True
        assert "insufficient" in result.get("reason", "").lower()
    finally:
        ra._COLEARN_ENABLED = orig


# ---------------------------------------------------------------------------
# Colearn enabled — dry_run (no DB write)
# ---------------------------------------------------------------------------

def test_generate_dry_run_does_not_write_artifact():
    """dry_run=True returns result dict but does not persist to DB."""
    import tools.workflow.reflexion_agent as ra
    from tools.db.storage import get_connection

    orig = ra._COLEARN_ENABLED
    ra._COLEARN_ENABLED = True
    try:
        task_type = _unique_type()
        _seed_traces(task_type, n_success=5)

        # Mock LLM to return deterministic text
        with patch.object(ra, "_call_llm", return_value="## Root Cause\nTest cause.\n\n## Proposed Improvements\n1. Improve X.\n\n## Expected Impact\nBetter score."):
            result = ra.generate_improvement_artifact(task_type, dry_run=True)

        assert result.get("dry_run") is True
        assert "artifact_id" in result
        assert result["traces_analyzed"] >= 3

        # No row in DB
        conn = get_connection()
        count = conn.execute(
            "SELECT COUNT(*) FROM agent_improvement_artifacts WHERE task_type = ?",
            (task_type,),
        ).fetchone()[0]
        conn.close()
        assert count == 0, "dry_run should not write to DB"
    finally:
        ra._COLEARN_ENABLED = orig


# ---------------------------------------------------------------------------
# Colearn enabled — actual write
# ---------------------------------------------------------------------------

def test_generate_writes_artifact_to_db():
    """dry_run=False with colearn enabled writes to agent_improvement_artifacts."""
    import tools.workflow.reflexion_agent as ra
    from tools.db.storage import get_connection

    orig = ra._COLEARN_ENABLED
    ra._COLEARN_ENABLED = True
    try:
        task_type = _unique_type()
        task_ids = _seed_traces(task_type, n_success=3, n_failure=2)
        # The evidence gate joins the trajectory to lesson_learned rows; without
        # them the artifact is written 'rejected_no_evidence', not 'pending'.
        evidence = _supporting_evidence(task_type, task_ids)

        with patch.object(ra, "collect_evidence", return_value=evidence), \
             patch.object(ra, "_call_llm", return_value="## Root Cause\nMissing deps.\n\n## Proposed Improvements\n1. Add dep check.\n\n## Expected Impact\nFewer failures."):
            result = ra.generate_improvement_artifact(task_type, skill_used="icdev-build", dry_run=False)

        assert "error" not in result
        assert result.get("dry_run") is False
        assert result.get("evidence_rejected") is False, result.get("evidence_reason")

        conn = get_connection()
        row = conn.execute(
            "SELECT artifact_id, generation_n, baseline_score, status "
            "FROM agent_improvement_artifacts WHERE task_type = ?",
            (task_type,),
        ).fetchone()
        conn.close()

        assert row is not None, "Artifact not written to DB"
        if isinstance(row, dict):
            assert row["status"] == "pending"
            assert row["generation_n"] == 1
        else:
            assert row[3] == "pending"
            assert row[1] == 1
    finally:
        ra._COLEARN_ENABLED = orig


def test_generate_rejects_artifact_with_no_lesson_evidence():
    """exa-refine-04: a proposal with no lesson_learned rows behind it is rejected.

    The traces alone look like a motive; they are not one. The artifact is still
    persisted (it is the record of the rejection) but MUST NOT carry 'pending',
    which is the status GEPA and the review queue select on.
    """
    import tools.workflow.reflexion_agent as ra
    from tools.db.storage import get_connection
    from tools.workflow.refinement_evidence import load_config

    if not load_config().get("require_evidence", True):
        pytest.skip("deployment has turned the evidence gate off (attach-only)")

    orig = ra._COLEARN_ENABLED
    ra._COLEARN_ENABLED = True
    try:
        task_type = _unique_type()
        _seed_traces(task_type, n_success=3, n_failure=2)  # traces but NO lessons

        with patch.object(ra, "_call_llm", return_value="Improvement with no evidence."):
            result = ra.generate_improvement_artifact(task_type, skill_used="icdev-build", dry_run=False)

        assert result.get("evidence_rejected") is True
        assert result.get("lesson_count") == 0
        assert result.get("status") == "rejected_no_evidence"

        conn = get_connection()
        row = conn.execute(
            "SELECT status FROM agent_improvement_artifacts WHERE task_type = ?",
            (task_type,),
        ).fetchone()
        conn.close()

        assert row is not None, "Rejected artifact should still be recorded"
        status = row["status"] if isinstance(row, dict) else row[0]
        assert status == "rejected_no_evidence"
        assert status != "pending", "unsupported proposal must never be queued as pending"

        # ...and it must be invisible to the consumer that serves refinements.
        assert ra.get_latest_improvement(task_type) == ""
    finally:
        ra._COLEARN_ENABLED = orig


def test_generate_increments_generation_n():
    """Each call increments generation_n for the same task_type."""
    import tools.workflow.reflexion_agent as ra
    from tools.db.storage import get_connection

    orig = ra._COLEARN_ENABLED
    ra._COLEARN_ENABLED = True
    try:
        task_type = _unique_type()
        _seed_traces(task_type, n_success=5)

        with patch.object(ra, "_call_llm", return_value="Improvement gen 1"):
            ra.generate_improvement_artifact(task_type, dry_run=False)
        with patch.object(ra, "_call_llm", return_value="Improvement gen 2"):
            ra.generate_improvement_artifact(task_type, dry_run=False)

        conn = get_connection()
        rows = conn.execute(
            "SELECT generation_n FROM agent_improvement_artifacts WHERE task_type = ? ORDER BY generation_n",
            (task_type,),
        ).fetchall()
        conn.close()

        gens = [r[0] if isinstance(r, (list, tuple)) else r["generation_n"] for r in rows]
        assert gens == [1, 2], f"Expected generations [1,2], got {gens}"
    finally:
        ra._COLEARN_ENABLED = orig


def test_generate_uses_deterministic_fallback_when_llm_fails():
    """If LLM returns empty, a deterministic fallback text is used."""
    import tools.workflow.reflexion_agent as ra

    orig = ra._COLEARN_ENABLED
    ra._COLEARN_ENABLED = True
    try:
        task_type = _unique_type()
        _seed_traces(task_type, n_success=3, n_failure=1)

        with patch.object(ra, "_call_llm", return_value=""):
            result = ra.generate_improvement_artifact(task_type, dry_run=True)

        assert "improvement_text" in result
        text = result["improvement_text"]
        assert len(text) > 0
        assert "deterministic" in text or "Success rate" in text
    finally:
        ra._COLEARN_ENABLED = orig


# ---------------------------------------------------------------------------
# get_latest_improvement
# ---------------------------------------------------------------------------

def test_get_latest_improvement_returns_formatted_text():
    """get_latest_improvement returns formatted artifact text for existing artifact."""
    import tools.workflow.reflexion_agent as ra

    orig = ra._COLEARN_ENABLED
    ra._COLEARN_ENABLED = True
    try:
        task_type = _unique_type()
        task_ids = _seed_traces(task_type, n_success=4)
        # get_latest_improvement selects WHERE status = 'pending', so the
        # artifact has to clear the evidence gate to be servable at all.
        evidence = _supporting_evidence(task_type, task_ids)

        with patch.object(ra, "collect_evidence", return_value=evidence), \
             patch.object(ra, "_call_llm", return_value="Improvement: add retry logic."):
            ra.generate_improvement_artifact(task_type, skill_used="icdev-build", dry_run=False)

        text = ra.get_latest_improvement(task_type)
        assert text != ""
        assert "ECHO" in text
        assert "Gen-" in text
    finally:
        ra._COLEARN_ENABLED = orig


def test_get_latest_improvement_empty_when_no_artifact():
    """get_latest_improvement returns '' when no artifact exists for task_type."""
    import tools.workflow.reflexion_agent as ra

    orig = ra._COLEARN_ENABLED
    ra._COLEARN_ENABLED = True
    try:
        result = ra.get_latest_improvement(_unique_type())
        assert result == ""
    finally:
        ra._COLEARN_ENABLED = orig


# ---------------------------------------------------------------------------
# run_batch_reflexion
# ---------------------------------------------------------------------------

def test_run_batch_reflexion_processes_specified_types():
    """run_batch_reflexion processes all task_types in the provided list."""
    import tools.workflow.reflexion_agent as ra

    orig = ra._COLEARN_ENABLED
    ra._COLEARN_ENABLED = False
    try:
        types = [_unique_type(), _unique_type(), _unique_type()]
        result = ra.run_batch_reflexion(types)
        assert result["task_types_processed"] == 3
        assert set(result["results"].keys()) == set(types)
    finally:
        ra._COLEARN_ENABLED = orig


def test_run_batch_reflexion_discovers_from_traces():
    """run_batch_reflexion with task_types=None discovers from agent_execution_traces."""
    import tools.workflow.reflexion_agent as ra

    orig = ra._COLEARN_ENABLED
    ra._COLEARN_ENABLED = False
    try:
        task_type = _unique_type()
        _seed_traces(task_type, n_success=2)

        result = ra.run_batch_reflexion(task_types=None)
        # Should have discovered at least our task_type
        assert result["task_types_processed"] >= 1
        assert task_type in result.get("results", {})
    finally:
        ra._COLEARN_ENABLED = orig
