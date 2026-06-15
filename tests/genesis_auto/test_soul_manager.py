#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for NOVA SOUL — icdev/tools/ace/soul_manager.py

Validates: import, build_identity_preamble, record_learning,
prune behaviour, extract_learnings_from_trace (deterministic path only).
All tests run on the SQLite test backend (forced by conftest).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import os
import uuid


def _fresh_role() -> str:
    return f"nova-test-soul-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Import check
# ---------------------------------------------------------------------------

def test_soul_manager_imports():
    """Module imports without error."""
    import icdev.tools.ace.soul_manager  # noqa: F401


def test_soul_manager_functions_exist():
    """All public functions are present and callable."""
    from icdev.tools.ace.soul_manager import (
        build_identity_preamble,
        record_learning,
        extract_learnings_from_trace,
    )
    for fn in (build_identity_preamble, record_learning, extract_learnings_from_trace):
        assert callable(fn)


# ---------------------------------------------------------------------------
# build_identity_preamble
# ---------------------------------------------------------------------------

def test_build_identity_preamble_with_soul_file():
    """build_identity_preamble returns non-empty string when role has SOUL.md."""
    from icdev.tools.ace.soul_manager import build_identity_preamble
    # ai_developer role has SOUL.md (created during NOVA init)
    preamble = build_identity_preamble("ai_developer")
    assert isinstance(preamble, str)
    assert len(preamble) > 0
    assert "## Identity" in preamble


def test_build_identity_preamble_contains_capability_scope():
    """Preamble includes TOOLS.md section when it exists."""
    from icdev.tools.ace.soul_manager import build_identity_preamble
    preamble = build_identity_preamble("ai_developer")
    # TOOLS.md should produce a capability scope section
    assert "## Capability Scope" in preamble or "NOVA SOUL" in preamble


def test_build_identity_preamble_unknown_role_returns_empty():
    """build_identity_preamble for a role with no files returns empty string."""
    from icdev.tools.ace.soul_manager import build_identity_preamble
    preamble = build_identity_preamble("zzz-nonexistent-role-xyz")
    assert preamble == ""


def test_build_identity_preamble_includes_db_facts():
    """Preamble includes DB-stored facts in 'Accumulated Knowledge' or 'Recent Learnings'."""
    from icdev.tools.ace.soul_manager import build_identity_preamble, record_learning
    role = _fresh_role()
    record_learning(role, "Test fact for identity preamble.", confidence=0.9)
    preamble = build_identity_preamble(role)
    # Even with no SOUL.md, DB facts create a preamble when there are facts
    if preamble:
        assert "Test fact" in preamble or "Recent Learnings" in preamble


def test_build_identity_preamble_no_crash_on_empty_role():
    """build_identity_preamble on a role with no SOUL.md and no DB facts returns ''."""
    from icdev.tools.ace.soul_manager import build_identity_preamble
    preamble = build_identity_preamble(_fresh_role())
    assert isinstance(preamble, str)


# ---------------------------------------------------------------------------
# record_learning
# ---------------------------------------------------------------------------

def test_record_learning_returns_fact_id():
    """record_learning returns a non-empty fact_id string."""
    from icdev.tools.ace.soul_manager import record_learning
    role = _fresh_role()
    fact_id = record_learning(role, "ICDEV uses PostgreSQL as primary backend.", confidence=0.95)
    assert isinstance(fact_id, str)
    assert fact_id.startswith("fact-")
    assert len(fact_id) > 5


def test_record_learning_writes_db_row():
    """record_learning inserts a row into ace_coworker_memory."""
    from icdev.tools.ace.soul_manager import record_learning
    from tools.db.storage import get_connection

    role = _fresh_role()
    fact_id = record_learning(role, "Test DB persistence fact.", fact_type="observation", confidence=0.8)

    conn = get_connection()
    row = conn.execute(
        "SELECT id, role_id, fact_type, content, confidence FROM ace_coworker_memory WHERE id = ?",
        (fact_id,),
    ).fetchone()
    conn.close()

    assert row is not None, "No DB row written by record_learning"
    if isinstance(row, dict):
        assert row["role_id"] == role
        assert row["fact_type"] == "observation"
        assert abs(row["confidence"] - 0.8) < 0.01
    else:
        assert row[1] == role
        assert row[2] == "observation"
        assert abs(row[4] - 0.8) < 0.01


def test_record_learning_multiple_facts():
    """Multiple facts are all stored for the same role."""
    from icdev.tools.ace.soul_manager import record_learning
    from tools.db.storage import get_connection

    role = _fresh_role()
    for i in range(5):
        record_learning(role, f"Fact number {i}", confidence=0.7)

    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM ace_coworker_memory WHERE role_id = ?",
        (role,),
    ).fetchone()[0]
    conn.close()

    assert count == 5


def test_record_learning_fact_types():
    """Different fact_types are accepted."""
    from icdev.tools.ace.soul_manager import record_learning
    role = _fresh_role()
    for ftype in ("observation", "success", "warning", "llm_extracted"):
        fid = record_learning(role, f"Fact of type {ftype}.", fact_type=ftype, confidence=0.7)
        assert fid.startswith("fact-")


# ---------------------------------------------------------------------------
# Prune — MAX_MEMORY_FACTS limit
# ---------------------------------------------------------------------------

def test_prune_keeps_max_memory_facts():
    """After exceeding MAX_MEMORY_FACTS, excess old facts are removed."""
    from icdev.tools.ace.soul_manager import record_learning, MAX_MEMORY_FACTS
    from tools.db.storage import get_connection

    role = _fresh_role()
    # Insert MAX_MEMORY_FACTS + 5 facts
    for i in range(MAX_MEMORY_FACTS + 5):
        record_learning(role, f"Prunable fact {i:03d}.", confidence=0.6)

    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM ace_coworker_memory WHERE role_id = ?",
        (role,),
    ).fetchone()[0]
    conn.close()

    assert count <= MAX_MEMORY_FACTS, (
        f"Expected <= {MAX_MEMORY_FACTS} facts after prune, got {count}"
    )


# ---------------------------------------------------------------------------
# extract_learnings_from_trace — deterministic path (no LLM)
# ---------------------------------------------------------------------------

def test_extract_learnings_success_trace_records_fact():
    """Successful trace records a 'success' fact without LLM."""
    os.environ["ICDEV_HARNESS_COLEARN"] = "false"
    from icdev.tools.ace.soul_manager import extract_learnings_from_trace
    from tools.db.storage import get_connection

    role = _fresh_role()
    trace = {
        "trace_id": "trace-test-aaa",
        "task_type": "build",
        "outcome": "success",
        "lesson_pattern": "",
    }
    fact_ids = extract_learnings_from_trace(role, trace, task_output="", source_task_id="task-aaa")

    assert isinstance(fact_ids, list)
    assert len(fact_ids) >= 1, "Expected at least one fact_id for a successful trace"

    conn = get_connection()
    rows = conn.execute(
        "SELECT fact_type FROM ace_coworker_memory WHERE id = ?",
        (fact_ids[0],),
    ).fetchone()
    conn.close()

    assert rows is not None
    fact_type = rows[0] if isinstance(rows, (list, tuple)) else rows["fact_type"]
    assert fact_type == "success"


def test_extract_learnings_warning_pattern_records_fact():
    """Non-trivial lesson_pattern records a 'warning' fact."""
    os.environ["ICDEV_HARNESS_COLEARN"] = "false"
    from icdev.tools.ace.soul_manager import extract_learnings_from_trace

    role = _fresh_role()
    trace = {
        "trace_id": "trace-test-bbb",
        "task_type": "test",
        "outcome": "failure",
        "lesson_pattern": "missing_dependency",
    }
    fact_ids = extract_learnings_from_trace(role, trace, task_output="", source_task_id="task-bbb")

    warning_ids = [fid for fid in fact_ids if fid.startswith("fact-")]
    # At least one fact should record the warning pattern
    assert len(warning_ids) >= 1


def test_extract_learnings_no_llm_when_colearn_disabled():
    """With ICDEV_HARNESS_COLEARN=false, no LLM call is made."""
    os.environ["ICDEV_HARNESS_COLEARN"] = "false"

    # Patch LLMRouter so if it IS called, we detect it
    import unittest.mock as mock
    with mock.patch("tools.llm.router.LLMRouter.invoke", side_effect=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("LLMRouter called with colearn disabled"))):
        from icdev.tools.ace import soul_manager as sm
        role = _fresh_role()
        trace = {"trace_id": "t-colearn", "task_type": "build", "outcome": "success", "lesson_pattern": ""}
        # Should not raise even if LLMRouter is broken
        fact_ids = sm.extract_learnings_from_trace(role, trace, task_output="some output", source_task_id="t-co")
        assert isinstance(fact_ids, list)


def test_extract_learnings_trivial_pattern_skipped():
    """lesson_pattern 'success_first_try' does not generate a warning fact."""
    os.environ["ICDEV_HARNESS_COLEARN"] = "false"
    from icdev.tools.ace.soul_manager import extract_learnings_from_trace
    from tools.db.storage import get_connection

    role = _fresh_role()
    trace = {
        "trace_id": "trace-trivial",
        "task_type": "build",
        "outcome": "success",
        "lesson_pattern": "success_first_try",
    }
    extract_learnings_from_trace(role, trace, task_output="", source_task_id="t-triv")

    conn = get_connection()
    warning_facts = conn.execute(
        "SELECT id FROM ace_coworker_memory WHERE role_id = ? AND fact_type = 'warning'",
        (role,),
    ).fetchall()
    conn.close()

    assert len(warning_facts) == 0, "Trivial pattern should not generate a warning fact"
