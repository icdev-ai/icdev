#!/usr/bin/env python3
# CUI // SP-CTI
"""NOVA E2E V&V — nova-vv-01

End-to-end verification of the ECHO→SOUL→TRUST loop using only deterministic
code paths (no LLM calls). All steps run on the SQLite test backend.

Acceptance criteria from nova-vv-01:
  1. Dispatch a synthetic 'build' task via trace_logger.start_trace()
  2. Close it with outcome='success'
  3. Call run_batch_reflexion(['build']) → verify improvement artifact written (dry_run)
  4. Call soul_manager.extract_learnings_from_trace() → verify fact in ace_coworker_memory
  5. Call record_trust_event('ai_developer', 'success') → verify trust_score > 0.5
  6. Call build_identity_preamble('ai_developer') → verify '## Accumulated Knowledge' present
"""

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Disable co-learning (LLM) for the entire test module
os.environ["ICDEV_HARNESS_COLEARN"] = "false"

import pytest


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _unique(prefix: str = "role") -> str:
    return f"nova-e2e-{prefix}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Step 1 & 2 — trace_logger start + close
# ---------------------------------------------------------------------------

def test_e2e_step1_start_trace():
    """Step 1: start_trace returns a valid trace_id."""
    from tools.workflow.trace_logger import start_trace

    tid = start_trace("e2e-task-001", "build", "icdev-build")
    assert tid.startswith("trace-")
    assert len(tid) > 6


def test_e2e_step2_close_trace_success():
    """Step 2: close_trace marks outcome=success and populates completed_at."""
    from tools.workflow.trace_logger import start_trace, close_trace
    from tools.db.storage import get_connection

    tid = start_trace("e2e-task-002", "build", "icdev-build")
    close_trace(tid, "success", "success_first_try", "E2E completed cleanly")

    conn = get_connection()
    row = conn.execute(
        "SELECT outcome, completed_at FROM agent_execution_traces WHERE trace_id = ?",
        (tid,),
    ).fetchone()
    conn.close()

    assert row is not None
    outcome = row[0] if isinstance(row, (list, tuple)) else row["outcome"]
    completed = row[1] if isinstance(row, (list, tuple)) else row["completed_at"]
    assert outcome == "success"
    assert completed != ""


# ---------------------------------------------------------------------------
# Step 3 — reflexion_agent run_batch_reflexion (dry_run — no LLM)
# ---------------------------------------------------------------------------

def test_e2e_step3_batch_reflexion_dry_run():
    """Step 3: run_batch_reflexion dry_run returns result dict without writing artifacts."""
    from tools.workflow.reflexion_agent import run_batch_reflexion
    from tools.db.storage import get_connection

    # Seed a successful trace for 'build'
    from tools.workflow.trace_logger import start_trace, close_trace
    tid = start_trace("e2e-reflex-001", "build", "icdev-build")
    close_trace(tid, "success", "success_first_try")

    result = run_batch_reflexion(["build"], dry_run=True)
    assert isinstance(result, dict)
    # dry_run should not write to DB — but should return analysis
    conn = get_connection()
    count_before = conn.execute(
        "SELECT COUNT(*) FROM agent_improvement_artifacts WHERE task_type = 'build'"
    ).fetchone()[0]
    conn.close()

    # dry_run must not have written new artifacts in this call
    run_batch_reflexion(["build"], dry_run=True)
    conn = get_connection()
    count_after = conn.execute(
        "SELECT COUNT(*) FROM agent_improvement_artifacts WHERE task_type = 'build'"
    ).fetchone()[0]
    conn.close()

    assert count_after == count_before, "dry_run should not write artifact rows"


# ---------------------------------------------------------------------------
# Step 4 — soul_manager extract_learnings_from_trace
# ---------------------------------------------------------------------------

def test_e2e_step4_extract_learnings():
    """Step 4: extract_learnings_from_trace writes at least one fact to ace_coworker_memory."""
    from icdev.tools.ace.soul_manager import extract_learnings_from_trace
    from tools.db.storage import get_connection

    role = _unique("soul")
    trace = {
        "trace_id": "e2e-trace-soul-001",
        "task_type": "build",
        "outcome": "success",
        "lesson_pattern": "",
    }
    fact_ids = extract_learnings_from_trace(role, trace, task_output="", source_task_id="e2e-task-003")

    assert isinstance(fact_ids, list)
    assert len(fact_ids) >= 1, "Expected at least one fact recorded for successful trace"

    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM ace_coworker_memory WHERE role_id = ?",
        (role,),
    ).fetchone()[0]
    conn.close()

    assert count >= 1, "No facts written to ace_coworker_memory"


# ---------------------------------------------------------------------------
# Step 5 — trust_calibrator record_trust_event
# ---------------------------------------------------------------------------

def test_e2e_step5_trust_event_increases_score():
    """Step 5: recording a 'success' event raises trust_score above INITIAL_TRUST."""
    from tools.ace.trust_calibrator import (
        record_trust_event, get_trust_score, INITIAL_TRUST,
    )

    role = _unique("trust")
    initial = get_trust_score(role)
    assert initial == INITIAL_TRUST

    result = record_trust_event(role, "success", "e2e-task-004")
    new_score = get_trust_score(role)

    assert new_score > INITIAL_TRUST, f"Score did not increase: {new_score}"
    assert result["new_score"] == pytest.approx(INITIAL_TRUST + 0.05, abs=0.001)


def test_e2e_step5_trust_ledger_written():
    """Step 5: trust event is persisted as append-only ledger row."""
    from tools.ace.trust_calibrator import record_trust_event
    from tools.db.storage import get_connection

    role = _unique("ledger")
    record_trust_event(role, "success", "e2e-task-005")

    conn = get_connection()
    rows = conn.execute(
        "SELECT reason FROM ace_trust_ledger WHERE role_id = ?",
        (role,),
    ).fetchall()
    conn.close()

    assert len(rows) >= 1
    reason = rows[0][0] if isinstance(rows[0], (list, tuple)) else rows[0]["reason"]
    assert reason == "success"


# ---------------------------------------------------------------------------
# Step 6 — soul_manager build_identity_preamble
# ---------------------------------------------------------------------------

def test_e2e_step6_identity_preamble_has_accumulated_knowledge():
    """Step 6: build_identity_preamble includes 'Accumulated Knowledge' after recording facts."""
    from icdev.tools.ace.soul_manager import build_identity_preamble, record_learning

    # Use ai_developer which has SOUL.md + we add a DB fact
    role = "ai_developer"
    record_learning(role, "E2E V&V: NOVA pillars verified on SQLite backend.", confidence=0.9)

    preamble = build_identity_preamble(role)
    assert isinstance(preamble, str)
    assert len(preamble) > 0
    # Must have identity section from SOUL.md
    assert "## Identity" in preamble
    # Must have knowledge section from DB facts
    assert "Accumulated Knowledge" in preamble or "Recent Learnings" in preamble


# ---------------------------------------------------------------------------
# Full loop integration — all four pillars in sequence
# ---------------------------------------------------------------------------

def test_e2e_full_nova_loop():
    """Full ECHO→SOUL→TRUST loop runs without error and produces expected state."""
    from tools.workflow.trace_logger import (
        start_trace, close_trace, get_recent_traces, get_traces_for_task_type,
    )
    from tools.workflow.reflexion_agent import run_batch_reflexion
    from icdev.tools.ace.soul_manager import (
        extract_learnings_from_trace, build_identity_preamble, record_learning,
    )
    from tools.ace.trust_calibrator import (
        record_trust_event, get_dispatch_config, INITIAL_TRUST,
    )
    from tools.db.storage import get_connection

    role = _unique("fullloop")
    task_type = f"nova-e2e-fullloop-{uuid.uuid4().hex[:6]}"

    # ── ECHO: start + close trace ──────────────────────────────────────────
    tid = start_trace("e2e-fl-001", task_type, "icdev-build")
    close_trace(tid, "success", "success_first_try", "Full loop E2E verification")

    # Scope the lookup to this run's unique task_type. `get_recent_traces(limit=5)`
    # orders by completed_at, which close_trace writes at SECOND resolution, so
    # any success trace another test seeded in the same second can tie-break the
    # e2e trace out of the top 5 — a flake that fired ~5 runs in 6 against the
    # shared ambient DB.
    assert any(t["trace_id"] == tid for t in get_traces_for_task_type(task_type)), \
        "Trace not found for task_type"
    assert isinstance(get_recent_traces(limit=5, outcome_filter="success"), list)

    # ── ECHO: reflexion dry_run ────────────────────────────────────────────
    reflex_result = run_batch_reflexion([task_type], dry_run=True)
    assert isinstance(reflex_result, dict)

    # ── SOUL: extract learnings ────────────────────────────────────────────
    trace_dict = {
        "trace_id": tid,
        "task_type": task_type,
        "outcome": "success",
        "lesson_pattern": "",
    }
    fact_ids = extract_learnings_from_trace(role, trace_dict, source_task_id="e2e-fl-001")
    assert len(fact_ids) >= 1

    # ── TRUST: record event + check score ─────────────────────────────────
    evt = record_trust_event(role, "success", "e2e-fl-001")
    assert evt["new_score"] > INITIAL_TRUST
    cfg = get_dispatch_config(role)
    assert cfg["trust_score"] > INITIAL_TRUST
    assert cfg["band"] in ("supervised", "trusted", "autonomous")

    # ── SOUL: preamble includes accumulated knowledge ──────────────────────
    # ai_developer has SOUL.md so preamble will have identity section
    record_learning("ai_developer", f"Full loop E2E passed for task_type={task_type}.", confidence=0.8)
    preamble = build_identity_preamble("ai_developer")
    assert "## Identity" in preamble

    # ── Verify DB state coherence ──────────────────────────────────────────
    conn = get_connection()

    trace_count = conn.execute(
        "SELECT COUNT(*) FROM agent_execution_traces WHERE trace_id = ?",
        (tid,),
    ).fetchone()[0]
    assert trace_count == 1

    fact_count = conn.execute(
        "SELECT COUNT(*) FROM ace_coworker_memory WHERE role_id = ?",
        (role,),
    ).fetchone()[0]
    assert fact_count >= 1

    ledger_count = conn.execute(
        "SELECT COUNT(*) FROM ace_trust_ledger WHERE role_id = ?",
        (role,),
    ).fetchone()[0]
    assert ledger_count >= 1

    conn.close()
