#!/usr/bin/env python3
# CUI // SP-CTI
"""Integration test for the hardened kanban scheduler (guard-1 through guard-7).

Tests:
1. Phantom threshold (guard-1): task claiming 5 paths where 3 don't exist → FAIL
2. Task-specific checks (guard-2): task claiming manifest entry that's missing → FAIL
3. Batch decomposition (guard-3): [Batch] card with 3 subjects → decomposed into 3 children
4. Dedup (guard-4): duplicate suggested_card insertion is skipped
5. Audit table (guard-5): every _verify_task_completed call writes a row to kanban_verifications
6. Post-task validation (guard-7): codelens/coherence fields populated in audit table

All tests use the real PostgreSQL DB via get_connection(). Cleanup is automatic.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

import pytest

from tools.db.storage import get_connection
from tools.genesis.reflexes.kanban import (
    _decompose_batch_tasks,
    _run_verify_checks,
    _verify_task_specific,
    _write_verification_log,
    MAX_EXECUTION_SECONDS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Redirect the main SQLite DB to a per-test temp file so this module never
    writes ``test-kbh-*`` rows into the live data/icdev.db (opx-kan-02).

    ``get_connection()`` reads ``ICDEV_DB_PATH`` at call time, so this single
    env redirect isolates the ``db_conn`` fixture, the inline ``_mk_task``
    inserts, AND every internal ``get_connection()`` reached by the scheduler
    guards under test. autouse so tests that touch the board *without* taking
    ``db_conn`` are covered too. The kanban schema is seeded into the temp DB
    via the production initializer."""
    db = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    try:
        from tools.kanban.init_db import init_kanban_tables
        init_kanban_tables()
    except Exception:  # pragma: no cover — table seeding is best-effort
        pass
    return db


@pytest.fixture
def db_conn():
    """Fresh DB connection per test (on the isolated temp DB)."""
    conn = get_connection()
    yield conn
    conn.close()


def _mk_task(conn, task_id: str, title: str, description: str,
             task_type: str = "build", priority: str = "medium",
             status: str = "backlog") -> None:
    """Insert a test task row."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO kanban_tasks "
        "(id, title, description, task_type, priority, status, "
        " executor_type, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (task_id, title, description, task_type, priority, status,
         "claude_cli", now, now),
    )
    conn.commit()


def _cleanup_test_tasks(conn) -> None:
    """Remove all test-* tasks."""
    conn.execute("DELETE FROM kanban_tasks WHERE id LIKE 'test-kbh-%'")
    conn.execute("DELETE FROM kanban_verifications WHERE task_id LIKE 'test-kbh-%'")
    conn.commit()


# ---------------------------------------------------------------------------
# guard-1: Phantom threshold (50%)
# ---------------------------------------------------------------------------


def test_guard1_phantom_threshold_rejects_50pct_missing(db_conn):
    """If 50%+ of claimed paths are missing, verification FAILS."""
    _cleanup_test_tasks(db_conn)

    task_id = "test-kbh-phantom"
    _mk_task(db_conn, task_id, "Test phantom", "Create new files")

    # Output claims 4 paths — 3 don't exist (75% missing)
    output = (
        "I created the following files:\n"
        "- tools/fake/nonexistent_a.py\n"
        "- tools/fake/nonexistent_b.py\n"
        "- tools/fake/nonexistent_c.py\n"
        "- tools/db/storage.py (modified)\n"  # this one exists
        + "Implementation complete. " * 20  # pad to >200 chars
    )

    verified, reason = _run_verify_checks(task_id, output)
    assert not verified, f"Expected FAIL but got PASS: {reason}"
    assert "PHANTOM" in reason, f"Expected PHANTOM in reason: {reason}"

    _cleanup_test_tasks(db_conn)


# ---------------------------------------------------------------------------
# guard-2: Task-specific checks
# ---------------------------------------------------------------------------


def test_guard2_batch_task_rejected(db_conn):
    """Batch cards must be decomposed before dispatch — specific check FAILS."""
    _cleanup_test_tasks(db_conn)

    task_id = "test-kbh-batch-reject"
    _mk_task(db_conn, task_id, "[Batch] test_rule: 3 items",
             "Test batch\n\nSubjects:\n  - a\n  - b\n  - c")

    ok, reason = _verify_task_specific(task_id)
    assert not ok, f"Batch task should FAIL specific check: {reason}"
    assert "atch" in reason or "decomposed" in reason.lower()

    _cleanup_test_tasks(db_conn)


def test_guard2_manifest_check_rejects_missing(db_conn):
    """Task says tool should be in manifest but it isn't → FAIL."""
    _cleanup_test_tasks(db_conn)

    task_id = "test-kbh-manifest-miss"
    _mk_task(
        db_conn, task_id,
        "tool_not_in_manifest gap: tools/totally_fake_subsystem_xyz/imaginary.py",
        "AUTO: tools/totally_fake_subsystem_xyz/imaginary.py should be in manifest",
    )

    ok, reason = _verify_task_specific(task_id)
    assert not ok, f"Missing manifest entry should FAIL: {reason}"
    assert "manifest" in reason.lower()

    _cleanup_test_tasks(db_conn)


def test_guard2_non_matching_task_passes(db_conn):
    """Tasks that don't match any specific pattern should PASS (no-op)."""
    _cleanup_test_tasks(db_conn)

    task_id = "test-kbh-neutral"
    _mk_task(db_conn, task_id, "Generic refactor", "Improve performance of foo()")

    ok, _reason = _verify_task_specific(task_id)
    assert ok, "Non-matching tasks should pass specific check"

    _cleanup_test_tasks(db_conn)


# ---------------------------------------------------------------------------
# guard-3: Batch decomposition
# ---------------------------------------------------------------------------


def test_guard3_batch_decomposition(db_conn):
    """Batch card with 3 subjects → 3 children created, parent marked 'decomposed'."""
    _cleanup_test_tasks(db_conn)

    batch_id = "test-kbh-batch-decomp"
    _mk_task(
        db_conn, batch_id,
        "[Batch] test_rule: 3 findings",
        "Test batch\n\nSubjects:\n  - /route/alpha\n  - /route/beta\n  - /route/gamma",
        task_type="chore", priority="low",
    )

    task_row = dict(db_conn.execute(
        "SELECT * FROM kanban_tasks WHERE id = ?", (batch_id,)
    ).fetchone())

    result = _decompose_batch_tasks([task_row], db_conn)

    # Parent should be 'decomposed'
    parent = dict(db_conn.execute(
        "SELECT status FROM kanban_tasks WHERE id = ?", (batch_id,)
    ).fetchone())
    assert parent["status"] == "decomposed", f"Parent not decomposed: {parent}"

    # Result should have children (up to MAX_AUTO_PROMOTE)
    assert len(result) >= 1, f"Expected children, got {len(result)}"
    for child in result:
        assert child["id"] != batch_id, "Result should not contain the batch itself"
        # Verify child exists in DB
        r = db_conn.execute(
            "SELECT status, title FROM kanban_tasks WHERE id = ?", (child["id"],)
        ).fetchone()
        assert r is not None, f"Child {child['id']} not in DB"

    # Cleanup children + parent
    for child in result:
        db_conn.execute("DELETE FROM kanban_tasks WHERE id = ?", (child["id"],))
    # Also cleanup any third child not in result list
    db_conn.execute(
        "DELETE FROM kanban_tasks WHERE description LIKE ?",
        (f"%AUTO-DECOMPOSED from batch task {batch_id}%",),
    )
    db_conn.commit()
    _cleanup_test_tasks(db_conn)


# ---------------------------------------------------------------------------
# guard-5: Audit table logging
# ---------------------------------------------------------------------------


def test_guard5_verification_audit_logged(db_conn):
    """Every _write_verification_log writes a row to kanban_verifications."""
    _cleanup_test_tasks(db_conn)

    task_id = "test-kbh-audit"
    _write_verification_log(task_id, False, "PHANTOM: test audit write")

    row = db_conn.execute(
        "SELECT task_id, result, reason FROM kanban_verifications "
        "WHERE task_id = ? ORDER BY verified_at DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    assert row is not None, "Expected row in kanban_verifications"
    d = dict(row)
    assert d["task_id"] == task_id
    assert d["result"] == "phantom"  # PHANTOM keyword → phantom enum
    assert "PHANTOM" in d["reason"]

    db_conn.execute("DELETE FROM kanban_verifications WHERE task_id = ?", (task_id,))
    db_conn.commit()


def test_guard5_table_has_validation_columns(db_conn):
    """kanban_verifications has guard-7 post-task validation columns."""
    is_pg = getattr(db_conn, "_backend", "sqlite") == "postgresql"
    if is_pg:
        cols = db_conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'kanban_verifications'"
        ).fetchall()
        col_names = {dict(c)["column_name"] for c in cols}
    else:
        # SQLite: use PRAGMA table_info
        cols = db_conn.execute("PRAGMA table_info(kanban_verifications)").fetchall()
        col_names = {r[1] if isinstance(r, (list, tuple)) else dict(r).get("name") for r in cols}

    if not col_names:
        pytest.skip("kanban_verifications table not present in this backend")

    required = {
        "codelens_passed", "ruff_issues", "bandit_issues",
        "coherence_passed", "e2e_ran", "e2e_passed", "companion_synced",
    }
    missing = required - col_names
    assert not missing, f"Missing columns: {missing}"


# ---------------------------------------------------------------------------
# guard-6: Configuration
# ---------------------------------------------------------------------------


def test_guard6_timeout_lowered_to_15min():
    """MAX_EXECUTION_SECONDS lowered from 1800 (30 min) to 900 (15 min)."""
    assert MAX_EXECUTION_SECONDS == 900, (
        f"Expected 900s (15 min), got {MAX_EXECUTION_SECONDS}s"
    )


# ---------------------------------------------------------------------------
# guard-18: Failure tracking + auto-decomposition flag
# ---------------------------------------------------------------------------


def _has_failure_count_column(conn) -> bool:
    """Check if migration 020 has been applied."""
    is_pg = getattr(conn, "_backend", "sqlite") == "postgresql"
    if is_pg:
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'kanban_tasks' "
            "AND column_name = 'failure_count'"
        ).fetchone()
        return bool(row)
    cols = conn.execute("PRAGMA table_info(kanban_tasks)").fetchall()
    return any(
        (r[1] if isinstance(r, (list, tuple)) else dict(r).get("name")) == "failure_count"
        for r in cols
    )


def test_guard18_first_failure_stays_in_backlog(db_conn):
    """First verification failure increments count but keeps task in backlog."""
    if not _has_failure_count_column(db_conn):
        pytest.skip("migration 020 not applied in this backend")
    _cleanup_test_tasks(db_conn)
    from tools.genesis.reflexes.kanban import _record_failure_and_maybe_flag

    task_id = "test-kbh-fail-1"
    _mk_task(db_conn, task_id, "Test failing task", "Generic work")

    status = _record_failure_and_maybe_flag(task_id, "some failure")
    assert status in ("backlog", "needs_decomposition"), f"Expected backlog or needs_decomposition, got {status}"

    row = db_conn.execute(
        "SELECT failure_count, last_failure_reason FROM kanban_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    d = dict(row)
    assert d["failure_count"] == 1
    assert "some failure" in (d["last_failure_reason"] or "")

    _cleanup_test_tasks(db_conn)


def test_guard18_third_failure_flags_for_decomposition(db_conn):
    """Third verification failure flags task as needs_decomposition."""
    if not _has_failure_count_column(db_conn):
        pytest.skip("migration 020 not applied in this backend")
    _cleanup_test_tasks(db_conn)
    from tools.genesis.reflexes.kanban import (
        _record_failure_and_maybe_flag,
        MAX_FAILURES_BEFORE_DECOMPOSITION,
    )

    task_id = "test-kbh-fail-3"
    _mk_task(db_conn, task_id, "Test chronically failing", "Too big to complete")

    last_status = None
    for i in range(MAX_FAILURES_BEFORE_DECOMPOSITION):
        last_status = _record_failure_and_maybe_flag(task_id, f"failure #{i+1}")

    assert last_status == "needs_decomposition", (
        f"After {MAX_FAILURES_BEFORE_DECOMPOSITION} failures expected "
        f"needs_decomposition, got {last_status}"
    )

    row = db_conn.execute(
        "SELECT failure_count FROM kanban_tasks WHERE id = ?", (task_id,)
    ).fetchone()
    assert dict(row)["failure_count"] == MAX_FAILURES_BEFORE_DECOMPOSITION

    _cleanup_test_tasks(db_conn)


def test_guard25_coherence_misclass_regression():
    """Regression: 'coherence broken' reasons must classify as coherence_broken
    even when the reason ALSO mentions bandit in the remediation annotation.
    """
    from tools.workflow.auto_remediate import (
        classify_failure, FAILURE_COHERENCE_BROKEN, REMEDIABLE,
    )
    # This is the exact reason that caused the Webhook Wiring task to fail:
    reason = (
        "Verified: branch has commits since dispatch: 2c8d98ef fix: "
        "suppress B108 false positive | "
        "Task-specific checks passed | "
        "VALIDATION FAILED: coherence broken by cwd changes | "
        "REMEDIATION=bandit_security: bandit_security is not auto-remediable"
    )
    assert classify_failure(reason) == FAILURE_COHERENCE_BROKEN, (
        "Coherence broken must be detected BEFORE bandit even when the "
        "reason string mentions bandit (from a prior REMEDIATION annotation)"
    )
    # And coherence_broken IS remediable (rebase onto main), so the task
    # shouldn't have been rejected with 'human review needed'.
    assert FAILURE_COHERENCE_BROKEN in REMEDIABLE


def test_guard25_pre_dispatch_autoresolves_false_positive_manifest(db_conn):
    """Pre-dispatch check: tool_not_in_manifest task where tool IS already
    in manifest.md returns (True, reason) so the task auto-completes
    without ever dispatching Claude.
    """
    from tools.genesis.reflexes.kanban import _pre_dispatch_check

    # tools/genesis/reflexes/awareness.py is in the real manifest
    t = {
        "id": "test-predisp",
        "title": "tool_not_in_manifest gap: tools/genesis/reflexes/awareness.py",
        "description": "Python tool not documented in tools/manifest.md",
    }
    resolved, reason = _pre_dispatch_check(t)
    assert resolved, f"Expected auto-resolve, got: {reason}"
    assert "already in tools/manifest.md" in reason.lower() or "manifest" in reason.lower()


def test_guard25_pre_dispatch_skips_real_gaps():
    """Pre-dispatch check: tool_not_in_manifest with a tool that is NOT
    in manifest should NOT auto-resolve (real gap must be fixed by agent).
    """
    from tools.genesis.reflexes.kanban import _pre_dispatch_check

    t = {
        "id": "test-predisp-real",
        "title": "tool_not_in_manifest gap: tools/imaginary_subsystem_xyz/fake_tool.py",
        "description": "fake tool not in manifest",
    }
    resolved, _reason = _pre_dispatch_check(t)
    assert not resolved, "Real gaps must NOT auto-resolve"


def test_guard25_pre_dispatch_api_routes_are_auto_resolved():
    """Pre-dispatch: route_not_listed for /api/* routes is N/A (API routes
    don't belong in the Pages list), so those are auto-resolved.
    """
    from tools.genesis.reflexes.kanban import _pre_dispatch_check

    t = {
        "id": "test-predisp-api",
        "title": "route_not_listed gap: /api/some/endpoint",
        "description": "API route",
    }
    resolved, reason = _pre_dispatch_check(t)
    assert resolved
    assert "api" in reason.lower()


def test_guard21_classifier_routes_failures_correctly():
    """classify_failure maps verification reasons to the right category."""
    from tools.workflow.auto_remediate import (
        classify_failure, REMEDIABLE, UNREMEDIABLE,
        FAILURE_NO_COMMITS, FAILURE_PHANTOM_PATHS, FAILURE_RUFF_ISSUES,
        FAILURE_BANDIT_SECURITY,
    )

    assert classify_failure("No git commits found") == FAILURE_NO_COMMITS
    assert classify_failure("PHANTOM COMPLETION: 3 paths missing") == FAILURE_PHANTOM_PATHS
    assert classify_failure("ruff found 5 issues", {"ruff_issues": 5}) == FAILURE_RUFF_ISSUES
    assert classify_failure("bandit found 1 medium+ issue") == FAILURE_BANDIT_SECURITY

    # Sanity on remediation categories
    assert FAILURE_NO_COMMITS in UNREMEDIABLE
    assert FAILURE_PHANTOM_PATHS in UNREMEDIABLE
    assert FAILURE_BANDIT_SECURITY in UNREMEDIABLE
    assert FAILURE_RUFF_ISSUES in REMEDIABLE


def test_guard22_retry_coaching_includes_last_failure(db_conn):
    """After a failure, the next dispatch prompt includes coaching text."""
    if not _has_failure_count_column(db_conn):
        pytest.skip("migration 020 not applied in this backend")
    _cleanup_test_tasks(db_conn)

    from tools.genesis.reflexes.kanban import _get_retry_coaching
    from datetime import datetime, timezone

    task_id = "test-kbh-coaching"
    now = datetime.now(timezone.utc).isoformat()
    db_conn.execute(
        "INSERT INTO kanban_tasks (id, title, description, task_type, priority, "
        "status, executor_type, failure_count, last_failure_reason, "
        "last_failure_at, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, "Test retry coaching", "Add a new tool", "build", "high",
         "backlog", "claude_cli", 1,
         "ruff found 3 issues in agent files", now, now, now),
    )
    db_conn.commit()

    coaching = _get_retry_coaching(task_id)
    assert "RETRY ATTEMPT #2" in coaching, "Should indicate this is attempt 2"
    assert "ruff_issues" in coaching.lower() or "ruff" in coaching.lower()
    assert "--fix" in coaching, "ruff coaching should suggest --fix"
    assert "scope small" in coaching.lower() or "decomposition" in coaching.lower()

    _cleanup_test_tasks(db_conn)


def test_guard22_no_coaching_on_first_run(db_conn):
    """First-run tasks get no retry coaching (failure_count=0)."""
    if not _has_failure_count_column(db_conn):
        pytest.skip("migration 020 not applied in this backend")
    _cleanup_test_tasks(db_conn)
    from tools.genesis.reflexes.kanban import _get_retry_coaching

    task_id = "test-kbh-fresh"
    _mk_task(db_conn, task_id, "Fresh task", "Do some work")
    coaching = _get_retry_coaching(task_id)
    assert coaching == "", "Fresh task should have empty coaching"
    _cleanup_test_tasks(db_conn)


def test_guard23_dispatch_source_column_exists(db_conn):
    """Migration 021 adds dispatch_source to kanban_tasks and kanban_verifications."""
    is_pg = getattr(db_conn, "_backend", "sqlite") == "postgresql"

    def cols(table):
        if is_pg:
            rows = db_conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=?",
                (table,),
            ).fetchall()
            return {dict(c)["column_name"] for c in rows}
        rows = db_conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r[1] if isinstance(r, (list, tuple)) else dict(r).get("name") for r in rows}

    tasks_cols = cols("kanban_tasks")
    verif_cols = cols("kanban_verifications")

    if "dispatch_source" not in tasks_cols:
        pytest.skip("migration 021 not applied in this backend")

    assert "dispatch_source" in tasks_cols
    assert "dispatch_source" in verif_cols


def test_guard24_manifest_check_passes_when_tool_already_present(db_conn):
    """False-positive manifest gap: if tool IS already in manifest, _verify_task_specific
    returns (True, positive_signal) so the no-change path can accept it."""
    _cleanup_test_tasks(db_conn)

    from tools.genesis.reflexes.kanban import _verify_task_specific

    # This path exists AND is referenced in tools/manifest.md
    task_id = "test-kbh-mfalready"
    _mk_task(
        db_conn, task_id,
        "[Oracle/internal_awareness] Gap detected: tool_not_in_manifest on tools/genesis/reflexes/audit.py",
        (
            "Gap detected: tool_not_in_manifest on tools/genesis/reflexes/audit.py. "
            "Python tool file in tools/ not documented in tools/manifest.md"
        ),
        task_type="fix",
    )
    ok, reason = _verify_task_specific(task_id)
    assert ok, f"Expected PASS when tool already in manifest, got: {reason}"
    assert "expected outcome achieved" in reason.lower() or "present" in reason.lower()

    _cleanup_test_tasks(db_conn)


def test_guard24_no_change_marker_no_longer_hard_fails(db_conn):
    """Agent saying 'no changes needed' on a legit false-positive should NOT hard-fail."""
    from tools.genesis.reflexes.kanban import _run_verify_checks

    _cleanup_test_tasks(db_conn)
    task_id = "test-kbh-nochange"
    _mk_task(
        db_conn, task_id,
        "[Oracle] Gap detected: tool_not_in_manifest on tools/genesis/reflexes/audit.py",
        "Python tool file in tools/ not documented",
        task_type="fix",
    )

    # Simulate agent output that would previously trigger the old fail marker
    agent_output = (
        "I investigated the gap for tools/genesis/reflexes/audit.py. This is a false positive - "
        "the tool is already in tools/manifest.md at two locations. No changes needed. "
        "Task marked done."
    ) * 3  # padding to exceed min-length threshold

    verified, reason = _run_verify_checks(task_id, agent_output)
    # Without commits it will still end up failing the git check, but it
    # should NOT hard-fail on the "no changes" string alone. The failure
    # reason should mention "no git commits" rather than "failure indicator".
    assert "failure indicator" not in reason.lower(), (
        f"Should not hard-fail on soft 'no changes' marker: {reason}"
    )

    _cleanup_test_tasks(db_conn)


def test_guard23_tag_task_source_sets_dispatch_source(db_conn):
    """_tag_task_source updates the row's dispatch_source column."""
    is_pg = getattr(db_conn, "_backend", "sqlite") == "postgresql"
    rows = db_conn.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='kanban_tasks' AND column_name='dispatch_source'"
        if is_pg
        else "PRAGMA table_info(kanban_tasks)"
    ).fetchall()
    has_col = bool(rows) if is_pg else any(
        (r[1] if isinstance(r, (list, tuple)) else dict(r).get("name")) == "dispatch_source"
        for r in rows
    )
    if not has_col:
        pytest.skip("migration 021 not applied in this backend")

    _cleanup_test_tasks(db_conn)
    from tools.genesis.reflexes.kanban import _tag_task_source

    task_id = "test-kbh-source"
    _mk_task(db_conn, task_id, "Source tag test", "x")

    _tag_task_source(task_id, "genesis_scheduler")

    row = db_conn.execute(
        "SELECT dispatch_source FROM kanban_tasks WHERE id = ?", (task_id,)
    ).fetchone()
    assert dict(row)["dispatch_source"] == "genesis_scheduler"

    _cleanup_test_tasks(db_conn)


def test_guard21_unremediable_failures_return_false(tmp_path):
    """attempt_remediation returns (False, ..., ...) for unremediable types."""
    from tools.workflow.auto_remediate import attempt_remediation

    # No-commits is genuinely a no-op — cannot fix
    ok, reason, info = attempt_remediation(
        cwd=str(tmp_path),
        task_id="test-irrelevant",
        reason="No git commits found on task branch",
        metrics={},
        modified_files=[],
    )
    assert not ok, "no_commits should NOT be auto-remediated"
    assert info["failure_type"] == "no_commits"
    assert not info["remediable"]

    # Bandit security also cannot be auto-fixed safely
    ok, _reason, info = attempt_remediation(
        cwd=str(tmp_path),
        task_id="test-sec",
        reason="bandit found 2 medium+ issues",
        metrics={"bandit_issues": 2},
        modified_files=[],
    )
    assert not ok, "bandit_security should NOT be auto-remediated"
    assert info["failure_type"] == "bandit_security"


def test_guard18_failure_count_column_exists(db_conn):
    """Migration 020 added failure_count + last_failure_reason columns."""
    is_pg = getattr(db_conn, "_backend", "sqlite") == "postgresql"
    if is_pg:
        cols = db_conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'kanban_tasks'"
        ).fetchall()
        col_names = {dict(c)["column_name"] for c in cols}
    else:
        cols = db_conn.execute("PRAGMA table_info(kanban_tasks)").fetchall()
        col_names = {r[1] if isinstance(r, (list, tuple)) else dict(r).get("name") for r in cols}

    if "failure_count" not in col_names:
        pytest.skip("migration 020 not applied in this backend")

    required = {"failure_count", "last_failure_reason", "last_failure_at"}
    missing = required - col_names
    assert not missing, f"Missing columns from migration 020: {missing}"


if __name__ == "__main__":
    # Direct invocation — run via pytest
    import subprocess
    subprocess.run(
        ["python", "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=str(BASE_DIR),
    )
