#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Workflow Discipline Engine (Phase 66, D-WF-1 through D-WF-7).

Tests the PLAN→APPLY→UNIFY loop lifecycle, acceptance criteria tracking,
reconciliation, next action routing, handoff generation, and process
verification — all LLM-agnostic.
"""

import sqlite3

import pytest

# Module imports
from tools.workflow.loop_engine import (
    abandon_loop,
    add_acceptance_criterion,
    close_loop,
    complete_task,
    create_loop,
    finalize_plan,
    get_loop_status,
    list_loops,
    start_apply,
    start_unify,
    verify_criterion,
)
from tools.workflow.reconciler import get_reconciliation, reconcile
from tools.workflow.next_action import recommend
from tools.workflow.handoff_generator import generate_handoff, get_handoff, list_handoffs
from tools.workflow.process_verifier import check_project_processes, verify_loop_processes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_db(tmp_path):
    """Create a temporary test database with required tables."""
    db_path = tmp_path / "test_icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")

    # Create workflow tables
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS workflow_loops (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            phase_name TEXT NOT NULL,
            loop_type TEXT DEFAULT 'build',
            status TEXT DEFAULT 'planning',
            plan_summary TEXT,
            boundaries TEXT,
            task_count INTEGER DEFAULT 0,
            tasks_completed INTEGER DEFAULT 0,
            acceptance_criteria_count INTEGER DEFAULT 0,
            acceptance_pass_count INTEGER DEFAULT 0,
            acceptance_fail_count INTEGER DEFAULT 0,
            acceptance_skip_count INTEGER DEFAULT 0,
            planned_at TEXT,
            apply_started_at TEXT,
            apply_completed_at TEXT,
            unify_started_at TEXT,
            closed_at TEXT,
            created_by TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workflow_acceptance_criteria (
            id TEXT PRIMARY KEY,
            loop_id TEXT NOT NULL,
            criterion_number INTEGER NOT NULL,
            given_text TEXT,
            when_text TEXT,
            then_text TEXT,
            bdd_story_id TEXT,
            status TEXT DEFAULT 'pending',
            evidence TEXT,
            verified_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workflow_reconciliations (
            id TEXT PRIMARY KEY,
            loop_id TEXT NOT NULL,
            planned_tasks INTEGER DEFAULT 0,
            completed_tasks INTEGER DEFAULT 0,
            deviations TEXT,
            lessons_learned TEXT,
            process_checks TEXT,
            required_processes_invoked INTEGER DEFAULT 0,
            required_processes_total INTEGER DEFAULT 0,
            overall_result TEXT,
            classification TEXT DEFAULT 'CUI',
            reconciled_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workflow_handoffs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            loop_id TEXT,
            loop_status TEXT,
            chat_context_id TEXT,
            decisions_made TEXT,
            blockers TEXT,
            next_actions TEXT,
            context_summary TEXT,
            handoff_to TEXT,
            created_by TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            actor TEXT,
            action TEXT,
            details TEXT,
            project_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS compliance_artifacts (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            artifact_type TEXT,
            generated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS security_findings (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            severity TEXT,
            status TEXT DEFAULT 'open'
        );
    """)
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Loop Engine Tests
# ---------------------------------------------------------------------------


class TestLoopEngine:
    """Tests for loop_engine.py — PLAN→APPLY→UNIFY lifecycle."""

    def test_create_loop(self, test_db):
        result = create_loop("proj-1", "auth-module", "build", "dev@test", test_db)
        assert "loop_id" in result
        assert result["status"] == "planning"
        assert result["project_id"] == "proj-1"
        assert result["phase_name"] == "auth-module"

    def test_add_acceptance_criterion(self, test_db):
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        result = add_acceptance_criterion(
            loop["loop_id"],
            "a user exists",
            "they log in",
            "they see the dashboard",
            db_path=test_db,
        )
        assert "criterion_id" in result
        assert result["criterion_number"] == 1
        assert result["status"] == "pending"

    def test_finalize_plan_requires_criteria(self, test_db):
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        # No criteria added — should fail
        result = finalize_plan(loop["loop_id"], "Test plan", 2, db_path=test_db)
        assert "error" in result
        assert "acceptance criteria" in result["error"].lower()

    def test_finalize_plan_success(self, test_db):
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        add_acceptance_criterion(
            loop["loop_id"],
            "given",
            "when",
            "then",
            db_path=test_db,
        )
        result = finalize_plan(loop["loop_id"], "Test plan", 2, db_path=test_db)
        assert result["status"] == "planned"
        assert result["task_count"] == 2
        assert result["acceptance_criteria_count"] == 1

    def test_finalize_plan_rejects_too_many_tasks(self, test_db):
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        add_acceptance_criterion(loop["loop_id"], "g", "w", "t", db_path=test_db)
        result = finalize_plan(loop["loop_id"], "Big plan", 20, db_path=test_db)
        assert "error" in result
        assert "exceeds" in result["error"].lower()

    def test_start_apply(self, test_db):
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        add_acceptance_criterion(loop["loop_id"], "g", "w", "t", db_path=test_db)
        finalize_plan(loop["loop_id"], "Plan", 2, db_path=test_db)
        result = start_apply(loop["loop_id"], test_db)
        assert result["status"] == "applying"

    def test_start_apply_wrong_state(self, test_db):
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        result = start_apply(loop["loop_id"], test_db)
        assert "error" in result

    def test_complete_task(self, test_db):
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        add_acceptance_criterion(loop["loop_id"], "g", "w", "t", db_path=test_db)
        finalize_plan(loop["loop_id"], "Plan", 2, db_path=test_db)
        start_apply(loop["loop_id"], test_db)
        result = complete_task(loop["loop_id"], test_db)
        assert result["tasks_completed"] == 1
        assert result["status"] == "applying"

    def test_complete_all_tasks_transitions_to_applied(self, test_db):
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        add_acceptance_criterion(loop["loop_id"], "g", "w", "t", db_path=test_db)
        finalize_plan(loop["loop_id"], "Plan", 2, db_path=test_db)
        start_apply(loop["loop_id"], test_db)
        complete_task(loop["loop_id"], test_db)
        result = complete_task(loop["loop_id"], test_db)
        assert result["tasks_completed"] == 2
        assert result["status"] == "applied"

    def test_verify_criterion(self, test_db):
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        crit = add_acceptance_criterion(loop["loop_id"], "g", "w", "t", db_path=test_db)
        result = verify_criterion(crit["criterion_id"], "pass", "Test passed", test_db)
        assert result["status"] == "pass"

    def test_verify_criterion_invalid_status(self, test_db):
        result = verify_criterion("fake-id", "invalid", db_path=test_db)
        assert "error" in result

    def test_start_unify(self, test_db):
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        add_acceptance_criterion(loop["loop_id"], "g", "w", "t", db_path=test_db)
        finalize_plan(loop["loop_id"], "Plan", 1, db_path=test_db)
        start_apply(loop["loop_id"], test_db)
        complete_task(loop["loop_id"], test_db)
        result = start_unify(loop["loop_id"], test_db)
        assert result["status"] == "unifying"

    def test_close_loop_requires_reconciliation(self, test_db):
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        add_acceptance_criterion(loop["loop_id"], "g", "w", "t", db_path=test_db)
        finalize_plan(loop["loop_id"], "Plan", 1, db_path=test_db)
        start_apply(loop["loop_id"], test_db)
        complete_task(loop["loop_id"], test_db)
        start_unify(loop["loop_id"], test_db)
        result = close_loop(loop["loop_id"], test_db)
        assert "error" in result
        assert "reconciliation" in result["error"].lower()

    def test_full_lifecycle(self, test_db):
        """Test complete PLAN→APPLY→UNIFY→CLOSE lifecycle."""
        loop = create_loop("proj-1", "auth", db_path=test_db)
        lid = loop["loop_id"]

        # Add criteria
        crit = add_acceptance_criterion(lid, "user exists", "login", "see dashboard", db_path=test_db)

        # Plan
        finalize_plan(lid, "Implement auth", 1, db_path=test_db)

        # Apply
        start_apply(lid, test_db)
        complete_task(lid, test_db)

        # Verify criteria
        verify_criterion(crit["criterion_id"], "pass", "Login works", test_db)

        # Unify
        start_unify(lid, test_db)
        reconcile(lid, "All good", test_db)

        # Close
        result = close_loop(lid, test_db)
        assert result["status"] == "closed"

    def test_abandon_loop(self, test_db):
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        result = abandon_loop(loop["loop_id"], "Requirements changed", test_db)
        assert result["status"] == "abandoned"

    def test_get_loop_status(self, test_db):
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        add_acceptance_criterion(loop["loop_id"], "g", "w", "t", db_path=test_db)
        result = get_loop_status(loop["loop_id"], test_db)
        assert result["status"] == "planning"
        assert len(result["acceptance_criteria"]) == 1

    def test_list_loops(self, test_db):
        create_loop("proj-1", "phase-1", db_path=test_db)
        create_loop("proj-1", "phase-2", db_path=test_db)
        result = list_loops("proj-1", db_path=test_db)
        assert result["total_loops"] == 2

    def test_list_loops_with_filter(self, test_db):
        create_loop("proj-1", "phase-1", db_path=test_db)
        loop2 = create_loop("proj-1", "phase-2", db_path=test_db)
        add_acceptance_criterion(loop2["loop_id"], "g", "w", "t", db_path=test_db)
        finalize_plan(loop2["loop_id"], "Plan", 1, db_path=test_db)
        result = list_loops("proj-1", "planned", test_db)
        assert result["total_loops"] == 1

    def test_add_criterion_with_bdd_link(self, test_db):
        """AC can optionally link to a BDD story in safe_decomposition."""
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        result = add_acceptance_criterion(
            loop["loop_id"],
            "user exists",
            "login",
            "see dashboard",
            bdd_story_id="sd-abc123",
            db_path=test_db,
        )
        assert result["bdd_story_id"] == "sd-abc123"

        # Verify it shows in loop status
        status = get_loop_status(loop["loop_id"], test_db)
        assert status["acceptance_criteria"][0]["bdd_story_id"] == "sd-abc123"

    def test_add_criterion_without_bdd_link(self, test_db):
        """AC without BDD link should have None."""
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        result = add_acceptance_criterion(
            loop["loop_id"],
            "g",
            "w",
            "t",
            db_path=test_db,
        )
        assert result["bdd_story_id"] is None

    def test_loop_not_found(self, test_db):
        result = get_loop_status("nonexistent", test_db)
        assert "error" in result


# ---------------------------------------------------------------------------
# Reconciler Tests
# ---------------------------------------------------------------------------


class TestReconciler:
    """Tests for reconciler.py — planned-vs-actual delta."""

    def _setup_loop(self, test_db, tasks=2, fail_criteria=False):
        """Helper to create a loop in UNIFYING state."""
        loop = create_loop("proj-1", "test-phase", db_path=test_db)
        lid = loop["loop_id"]
        crit = add_acceptance_criterion(lid, "g", "w", "t", db_path=test_db)
        finalize_plan(lid, "Test plan", tasks, db_path=test_db)
        start_apply(lid, test_db)
        for _ in range(tasks):
            complete_task(lid, test_db)
        if fail_criteria:
            verify_criterion(crit["criterion_id"], "fail", "Failed", test_db)
        else:
            verify_criterion(crit["criterion_id"], "pass", "Passed", test_db)
        start_unify(lid, test_db)
        return lid, crit["criterion_id"]

    def test_reconcile_success(self, test_db):
        lid, _ = self._setup_loop(test_db)
        result = reconcile(lid, "All tasks completed", test_db)
        assert result["overall_result"] == "success"
        assert result["tasks"]["completion_rate"] == 1.0

    def test_reconcile_failed_criteria(self, test_db):
        lid, _ = self._setup_loop(test_db, fail_criteria=True)
        result = reconcile(lid, db_path=test_db)
        assert result["overall_result"] == "failed"
        assert result["acceptance_criteria"]["fail"] == 1

    def test_reconcile_deviations_tracked(self, test_db):
        lid, _ = self._setup_loop(test_db, fail_criteria=True)
        result = reconcile(lid, db_path=test_db)
        assert len(result["deviations"]) > 0
        types = [d["type"] for d in result["deviations"]]
        assert "acceptance_failure" in types

    def test_get_reconciliation(self, test_db):
        lid, _ = self._setup_loop(test_db)
        reconcile(lid, "Lessons here", test_db)
        result = get_reconciliation(lid, test_db)
        assert result["overall_result"] == "success"
        assert result["lessons_learned"] == "Lessons here"

    def test_get_reconciliation_not_found(self, test_db):
        result = get_reconciliation("fake-id", test_db)
        assert "error" in result


# ---------------------------------------------------------------------------
# Next Action Tests
# ---------------------------------------------------------------------------


class TestNextAction:
    """Tests for next_action.py — single next action recommender."""

    def test_recommend_no_loops(self, test_db):
        result = recommend("proj-1", test_db)
        assert "recommendation" in result
        assert "Create a new workflow loop" in result["recommendation"]

    def test_recommend_planning_loop(self, test_db):
        create_loop("proj-1", "phase-1", db_path=test_db)
        result = recommend("proj-1", test_db)
        assert "recommendation" in result
        assert "plan" in result["recommendation"].lower() or "Finalize" in result["recommendation"]

    def test_recommend_applied_loop(self, test_db):
        loop = create_loop("proj-1", "phase-1", db_path=test_db)
        lid = loop["loop_id"]
        add_acceptance_criterion(lid, "g", "w", "t", db_path=test_db)
        finalize_plan(lid, "Plan", 1, db_path=test_db)
        start_apply(lid, test_db)
        complete_task(lid, test_db)
        result = recommend("proj-1", test_db)
        assert "UNIFY" in result["recommendation"] or "unify" in result["recommendation"].lower()

    def test_recommend_returns_single_action(self, test_db):
        create_loop("proj-1", "phase-1", db_path=test_db)
        create_loop("proj-1", "phase-2", db_path=test_db)
        result = recommend("proj-1", test_db)
        # Should return one recommendation, not a list
        assert isinstance(result["recommendation"], str)


# ---------------------------------------------------------------------------
# Handoff Generator Tests
# ---------------------------------------------------------------------------


class TestHandoffGenerator:
    """Tests for handoff_generator.py — structured session handoffs."""

    def test_generate_handoff_empty(self, test_db):
        result = generate_handoff("proj-1", db_path=test_db)
        assert "handoff_id" in result
        assert "next_actions" in result
        assert "markdown" in result

    def test_generate_handoff_with_loop(self, test_db):
        create_loop("proj-1", "auth", db_path=test_db)
        result = generate_handoff("proj-1", db_path=test_db)
        assert len(result["open_loops"]) == 1
        assert result["open_loops"][0]["phase"] == "auth"

    def test_generate_handoff_with_failed_criteria(self, test_db):
        loop = create_loop("proj-1", "auth", db_path=test_db)
        crit = add_acceptance_criterion(loop["loop_id"], "g", "w", "t", db_path=test_db)
        verify_criterion(crit["criterion_id"], "fail", "Failed", test_db)
        result = generate_handoff("proj-1", db_path=test_db)
        assert len(result["blockers"]) > 0

    def test_handoff_markdown_contains_cui(self, test_db):
        result = generate_handoff("proj-1", db_path=test_db)
        assert "CUI" in result["markdown"]

    def test_list_handoffs(self, test_db):
        generate_handoff("proj-1", db_path=test_db)
        generate_handoff("proj-1", db_path=test_db)
        result = list_handoffs("proj-1", test_db)
        assert result["total"] == 2

    def test_get_handoff(self, test_db):
        created = generate_handoff("proj-1", db_path=test_db)
        result = get_handoff(created["handoff_id"], test_db)
        assert result["project_id"] == "proj-1"

    def test_get_handoff_not_found(self, test_db):
        result = get_handoff("fake-id", test_db)
        assert "error" in result


# ---------------------------------------------------------------------------
# Process Verifier Tests
# ---------------------------------------------------------------------------


class TestProcessVerifier:
    """Tests for process_verifier.py — required process invocation check."""

    def _setup_loop_with_audit(self, test_db, processes=None):
        """Helper to create a loop with audit trail entries."""
        loop = create_loop("proj-1", "build-phase", "build", db_path=test_db)
        lid = loop["loop_id"]
        add_acceptance_criterion(lid, "g", "w", "t", db_path=test_db)
        finalize_plan(lid, "Build plan", 2, db_path=test_db)
        start_apply(lid, test_db)

        # Add audit trail entries for invoked processes
        # Use ISO format matching _now() so timestamp comparison works
        from datetime import datetime, timezone

        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = sqlite3.connect(str(test_db))
        for proc in processes or []:
            conn.execute(
                "INSERT INTO audit_trail (event_type, actor, action, project_id, created_at) "
                "VALUES (?, 'builder', ?, 'proj-1', ?)",
                (proc, f"Ran {proc}", now_ts),
            )
        conn.commit()
        conn.close()
        return lid

    def test_verify_all_processes_present(self, test_db):
        lid = self._setup_loop_with_audit(
            test_db,
            [
                "security.sast",
                "security.secret_detection",
                "compliance.cui_marking",
                "testing.unit",
            ],
        )
        result = verify_loop_processes(lid, test_db)
        assert result["all_invoked"] is True
        assert result["pass_rate"] == 1.0

    def test_verify_missing_processes(self, test_db):
        lid = self._setup_loop_with_audit(test_db, ["security.sast"])
        result = verify_loop_processes(lid, test_db)
        assert result["all_invoked"] is False
        assert len(result["missing_processes"]) > 0

    def test_verify_loop_not_found(self, test_db):
        result = verify_loop_processes("fake-id", test_db)
        assert "error" in result

    def test_check_project_processes(self, test_db):
        # Add some audit trail entries
        conn = sqlite3.connect(str(test_db))
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action, project_id, created_at) "
            "VALUES ('security.sast', 'builder', 'Ran SAST', 'proj-1', datetime('now'))"
        )
        conn.commit()
        conn.close()
        result = check_project_processes("proj-1", "build", 24, test_db)
        assert result["invoked_processes"] >= 1

    def test_check_project_no_processes(self, test_db):
        result = check_project_processes("proj-1", "research", 24, test_db)
        # Research has no required processes
        assert result["all_invoked"] is True


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestIntegration:
    """End-to-end integration tests."""

    def test_full_workflow_with_handoff(self, test_db):
        """Full cycle: create → plan → apply → unify → close, with handoff."""
        # Create
        loop = create_loop("proj-1", "feature-x", db_path=test_db)
        lid = loop["loop_id"]

        # Add criteria
        c1 = add_acceptance_criterion(lid, "API exists", "POST /users", "returns 201", db_path=test_db)
        c2 = add_acceptance_criterion(lid, "DB has users", "query users", "returns list", db_path=test_db)

        # Plan
        finalize_plan(lid, "Build user API", 3, ["*.ato.*"], test_db)

        # Generate handoff after plan
        handoff = generate_handoff("proj-1", db_path=test_db)
        assert handoff["open_loops"][0]["status"] == "planned"

        # Apply
        start_apply(lid, test_db)
        for _ in range(3):
            complete_task(lid, test_db)

        # Verify criteria
        verify_criterion(c1["criterion_id"], "pass", "API works", test_db)
        verify_criterion(c2["criterion_id"], "pass", "DB works", test_db)

        # Unify
        start_unify(lid, test_db)
        recon = reconcile(lid, "Feature complete", test_db)
        assert recon["overall_result"] == "success"

        # Close
        result = close_loop(lid, test_db)
        assert result["status"] == "closed"

        # Verify final status
        status = get_loop_status(lid, test_db)
        assert status["status"] == "closed"
        assert status["acceptance_summary"]["pass"] == 2
        assert status["acceptance_summary"]["fail"] == 0

    def test_recommend_after_close(self, test_db):
        """After closing a loop, recommend creating a new one."""
        loop = create_loop("proj-1", "done", db_path=test_db)
        lid = loop["loop_id"]
        crit = add_acceptance_criterion(lid, "g", "w", "t", db_path=test_db)
        finalize_plan(lid, "Done", 1, db_path=test_db)
        start_apply(lid, test_db)
        complete_task(lid, test_db)
        verify_criterion(crit["criterion_id"], "pass", "", test_db)
        start_unify(lid, test_db)
        reconcile(lid, db_path=test_db)
        close_loop(lid, test_db)

        result = recommend("proj-1", test_db)
        assert "Create" in result["recommendation"] or "new" in result["recommendation"].lower()


# ---------------------------------------------------------------------------
# Workflow Extension Tests
# ---------------------------------------------------------------------------


class TestWorkflowExtension:
    """Tests for 030_workflow_loop_chat.py extension."""

    def _load_ext(self):
        import importlib

        return importlib.import_module("tools.extensions.builtins.030_workflow_loop_chat")

    def test_extension_exports(self):
        mod = self._load_ext()
        assert "chat_message_after" in mod.EXTENSION_HOOKS
        hook = mod.EXTENSION_HOOKS["chat_message_after"]
        assert hook["name"] == "workflow_loop_chat"
        assert callable(hook["handler"])

    def test_handle_skips_user_role(self):
        mod = self._load_ext()
        result = mod.handle({"role": "user", "context_id": "ctx-1", "turn_number": 1})
        assert "workflow_advisory" not in result

    def test_handle_skips_no_project(self):
        mod = self._load_ext()
        result = mod.handle(
            {
                "role": "assistant",
                "context_id": "ctx-1",
                "turn_number": 1,
                "project_id": "",
            }
        )
        assert "workflow_advisory" not in result


# ---------------------------------------------------------------------------
# Handoff Chat Context Link Tests
# ---------------------------------------------------------------------------


class TestHandoffChatLink:
    """Tests for chat_context_id on handoff documents."""

    def test_handoff_with_chat_context(self, test_db):
        result = generate_handoff(
            "proj-1",
            created_by="dev",
            chat_context_id="ctx-abc123",
            db_path=test_db,
        )
        assert "handoff_id" in result

        # Verify stored
        fetched = get_handoff(result["handoff_id"], test_db)
        assert fetched["chat_context_id"] == "ctx-abc123"

    def test_handoff_without_chat_context(self, test_db):
        result = generate_handoff("proj-1", db_path=test_db)
        fetched = get_handoff(result["handoff_id"], test_db)
        assert fetched["chat_context_id"] is None


# ---------------------------------------------------------------------------
# Intake Bridge Tests
# ---------------------------------------------------------------------------


class TestIntakeBridge:
    """Tests for intake → workflow loop bridge."""

    @pytest.fixture
    def bridge_db(self, tmp_path):
        """DB with intake + workflow tables."""
        db_path = tmp_path / "bridge_test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS intake_sessions (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                customer_name TEXT,
                customer_org TEXT,
                session_status TEXT DEFAULT 'active',
                classification TEXT DEFAULT 'CUI',
                impact_level TEXT DEFAULT 'IL4',
                readiness_score REAL DEFAULT 0.0,
                gap_count INTEGER DEFAULT 0,
                ambiguity_count INTEGER DEFAULT 0,
                context_summary TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS safe_decomposition (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                project_id TEXT,
                parent_id TEXT,
                level TEXT,
                title TEXT,
                description TEXT,
                acceptance_criteria TEXT,
                status TEXT DEFAULT 'draft',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS workflow_loops (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                phase_name TEXT NOT NULL,
                loop_type TEXT DEFAULT 'build',
                status TEXT DEFAULT 'planning',
                plan_summary TEXT,
                boundaries TEXT,
                task_count INTEGER DEFAULT 0,
                tasks_completed INTEGER DEFAULT 0,
                acceptance_criteria_count INTEGER DEFAULT 0,
                acceptance_pass_count INTEGER DEFAULT 0,
                acceptance_fail_count INTEGER DEFAULT 0,
                acceptance_skip_count INTEGER DEFAULT 0,
                planned_at TEXT,
                apply_started_at TEXT,
                apply_completed_at TEXT,
                unify_started_at TEXT,
                closed_at TEXT,
                created_by TEXT,
                classification TEXT DEFAULT 'CUI',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_acceptance_criteria (
                id TEXT PRIMARY KEY,
                loop_id TEXT NOT NULL,
                criterion_number INTEGER NOT NULL,
                given_text TEXT,
                when_text TEXT,
                then_text TEXT,
                bdd_story_id TEXT,
                status TEXT DEFAULT 'pending',
                evidence TEXT,
                verified_at TEXT,
                created_at TEXT NOT NULL
            );
        """)

        # Insert test intake session with readiness >= 0.7
        conn.execute(
            """INSERT INTO intake_sessions
               (id, project_id, customer_name, customer_org, session_status,
                readiness_score, impact_level, context_summary, created_at, updated_at)
               VALUES ('sess-001', 'proj-1', 'Jane', 'DoD', 'active',
                       0.85, 'IL4', '{"goal": "build-auth"}',
                       datetime('now'), datetime('now'))"""
        )
        # Insert decomposed stories
        conn.execute(
            """INSERT INTO safe_decomposition
               (id, session_id, project_id, level, title, acceptance_criteria)
               VALUES ('sd-001', 'sess-001', 'proj-1', 'story',
                       'User authentication',
                       'Given a registered user\n    When they submit credentials\n    Then they are authenticated')"""
        )
        conn.execute(
            """INSERT INTO safe_decomposition
               (id, session_id, project_id, level, title, acceptance_criteria)
               VALUES ('sd-002', 'sess-001', 'proj-1', 'story',
                       'Session management',
                       'Given an authenticated user\n    When session expires\n    Then they are logged out')"""
        )

        # Also insert a low-readiness session
        conn.execute(
            """INSERT INTO intake_sessions
               (id, project_id, customer_name, session_status,
                readiness_score, impact_level, context_summary, created_at, updated_at)
               VALUES ('sess-low', 'proj-2', 'Bob', 'active',
                       0.3, 'IL2', '{}', datetime('now'), datetime('now'))"""
        )

        conn.commit()
        conn.close()
        return db_path

    def test_check_readiness_ready(self, bridge_db):
        from tools.workflow.intake_bridge import check_bridge_readiness

        result = check_bridge_readiness("sess-001", bridge_db)
        assert result["ready"] is True
        assert result["story_count"] == 2
        assert result["readiness_score"] == 0.85

    def test_check_readiness_too_low(self, bridge_db):
        from tools.workflow.intake_bridge import check_bridge_readiness

        result = check_bridge_readiness("sess-low", bridge_db)
        assert result["ready"] is False
        assert "below" in result["reason"].lower()

    def test_check_readiness_not_found(self, bridge_db):
        from tools.workflow.intake_bridge import check_bridge_readiness

        result = check_bridge_readiness("nonexistent", bridge_db)
        assert result["ready"] is False

    def test_bridge_creates_loop(self, bridge_db):
        from tools.workflow.intake_bridge import bridge_intake_to_loop

        result = bridge_intake_to_loop("sess-001", db_path=bridge_db)
        assert "loop_id" in result
        assert result["task_count"] == 2
        assert result["acceptance_criteria_seeded"] == 2
        assert result["plan_status"] == "planned"

    def test_bridge_parses_gherkin(self, bridge_db):
        from tools.workflow.intake_bridge import bridge_intake_to_loop

        result = bridge_intake_to_loop("sess-001", db_path=bridge_db)

        # Verify criteria were parsed from Gherkin
        conn = sqlite3.connect(str(bridge_db))
        conn.row_factory = sqlite3.Row
        criteria = conn.execute(
            "SELECT * FROM workflow_acceptance_criteria WHERE loop_id = ? ORDER BY criterion_number",
            (result["loop_id"],),
        ).fetchall()
        conn.close()

        assert len(criteria) == 2
        # First story has "Given a registered user"
        assert "registered user" in criteria[0]["given_text"]
        assert criteria[0]["bdd_story_id"] == "sd-001"

    def test_bridge_rejects_low_readiness(self, bridge_db):
        from tools.workflow.intake_bridge import bridge_intake_to_loop

        result = bridge_intake_to_loop("sess-low", db_path=bridge_db)
        assert "error" in result
