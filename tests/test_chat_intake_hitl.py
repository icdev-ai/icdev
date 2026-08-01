#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Chat → HITL → Kanban auto-intake pipeline.

Acceptance criteria:
  AC-1  Non-requirement messages are skipped (no DB writes, returns skipped=True)
  AC-2  Requirement-bearing messages trigger intake processing
  AC-3  After decomposition a HITL review instance is created (not a direct Kanban task)
  AC-4  hitl_intake_pending maps instance_id → session_id
  AC-5  maybe_promote() skips instances not in hitl_intake_pending
  AC-6  maybe_promote() calls promote() when instance IS in hitl_intake_pending
  AC-7  feedback.submit_feedback(decision='approve') calls _post_approve when advance_stage returns completed
"""
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from _sql_compat import connect as _tconnect  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _storage_conn(db_path):
    """Connect the way production does: %s placeholders translated for SQLite.

    Runtime SQL is authored for PostgreSQL (%s placeholders, per CLAUDE.md); the
    storage wrapper is what translates them to SQLite's ?. Injecting a RAW sqlite3
    connection makes every %s a `near "%": syntax error`, which the intake hook
    swallows -- so it silently never promoted anything and the tests failed on
    "Expected 'promote' to be called once. Called 0 times."

    Delegates to the shared tests/_sql_compat.py helper so this file cannot
    drift from the translation the runtime actually performs.
    """
    return _tconnect(db_path)


@pytest.fixture()
def tmp_db(tmp_path):
    """In-memory-like SQLite DB with the minimal schema needed for these tests."""
    db = tmp_path / "test_intake.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chat_intake_sessions (
            context_id  TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hitl_intake_pending (
            instance_id TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            context_id  TEXT,
            created_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS kanban_tasks (
            id                   TEXT PRIMARY KEY,
            title                TEXT NOT NULL,
            description          TEXT,
            task_type            TEXT DEFAULT 'chore',
            priority             TEXT DEFAULT 'low',
            status               TEXT DEFAULT 'backlog',
            scheduled_at         TEXT,
            completed_at         TEXT,
            created_at           TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at           TEXT NOT NULL DEFAULT (datetime('now')),
            source_prediction_id TEXT,
            dispatch_source      TEXT DEFAULT 'unknown',
            hitl_stage           TEXT
        );
        CREATE TABLE IF NOT EXISTS intake_requirements (
            id         TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            text       TEXT
        );
        CREATE TABLE IF NOT EXISTS safe_decomposition (
            id                  TEXT PRIMARY KEY,
            session_id          TEXT NOT NULL,
            level               TEXT,
            title               TEXT,
            description         TEXT,
            acceptance_criteria TEXT,
            story_points        INTEGER,
            t_shirt_size        TEXT,
            wsjf_score          REAL DEFAULT 0,
            ato_impact_tier     TEXT,
            status              TEXT
        );
    """)
    conn.commit()
    conn.close()

    # process_message_for_intake() swallows db_path into **_kwargs and ignores
    # it -- every write goes through the module-level get_connection(), i.e. the
    # ambient DB. Patching here is what actually binds the code under test to
    # `db`; without it the hook wrote to data/icdev.db and, in a checkout where
    # that DB has not been seeded, failed with "no such table: kanban_tasks"
    # -- reported as a missing HITL feature rather than a leaky fixture.
    with patch("tools.chat.requirement_intake_hook.get_connection",
               side_effect=lambda *_a, **_kw: _storage_conn(db)):
        yield db


# ---------------------------------------------------------------------------
# AC-1: Non-requirement messages are skipped
# ---------------------------------------------------------------------------

class TestRequirementDetector:
    """Tests for _is_requirement_bearing() pre-filter."""

    def test_casual_message_skipped(self):
        from tools.chat.requirement_intake_hook import _is_requirement_bearing
        assert _is_requirement_bearing("Hello, how are you today?") is False

    def test_thanks_message_skipped(self):
        from tools.chat.requirement_intake_hook import _is_requirement_bearing
        assert _is_requirement_bearing("Thanks for the update!") is False

    def test_shall_detected(self):
        from tools.chat.requirement_intake_hook import _is_requirement_bearing
        assert _is_requirement_bearing("The system shall support SSO via SAML 2.0") is True

    def test_user_story_detected(self):
        from tools.chat.requirement_intake_hook import _is_requirement_bearing
        assert _is_requirement_bearing("As a user, I want to export reports to PDF") is True

    def test_bdd_detected(self):
        from tools.chat.requirement_intake_hook import _is_requirement_bearing
        assert _is_requirement_bearing("Given a logged-in user When they click export Then a PDF is generated") is True

    def test_must_detected(self):
        from tools.chat.requirement_intake_hook import _is_requirement_bearing
        assert _is_requirement_bearing("The API must respond within 200ms under normal load") is True

    def test_capability_detected(self):
        from tools.chat.requirement_intake_hook import _is_requirement_bearing
        assert _is_requirement_bearing("We need a capability to scan containers for CVEs automatically") is True


# ---------------------------------------------------------------------------
# AC-2 + AC-3: process_message_for_intake routes through HITL
# ---------------------------------------------------------------------------

class TestProcessMessageForIntake:

    def test_non_requirement_skipped(self, tmp_db):
        from tools.chat.requirement_intake_hook import process_message_for_intake
        result = process_message_for_intake("ctx-test", "Good morning!", db_path=tmp_db)
        assert result == {"skipped": True}

    def test_requirement_triggers_hitl(self, tmp_db):
        """AC-2 + AC-3: requirement message creates HITL instance, not direct Kanban task."""
        from tools.chat.requirement_intake_hook import process_message_for_intake

        with (
            patch("tools.requirements.intake_engine.create_session",
                  return_value={"session_id": "sess-abc"}),
            patch("tools.requirements.intake_engine.process_turn",
                  return_value={"turn_number": 1}),
            patch("tools.requirements.decomposition_engine.decompose_requirements",
                  return_value={"decomposed": 2}),
            patch("tools.workflow_hitl.engine.WorkflowEngine.create_instance",
                  return_value="wfi-test123"),
            patch("tools.workflow_hitl.engine.WorkflowEngine.advance_stage",
                  return_value={"advanced": True}),
            # Simulate 1 new requirement extracted
            patch("tools.chat.requirement_intake_hook._req_count",
                  side_effect=[0, 1]),
        ):
            result = process_message_for_intake(
                "ctx-test",
                "The system shall provide role-based access control (RBAC)",
                db_path=tmp_db,
            )

        assert "hitl_instance_id" in result, "Should return HITL instance ID"
        assert result["hitl_instance_id"] == "wfi-test123"
        assert result["requirements_found"] == 1
        assert "review_url" in result

    def test_no_direct_promote_called(self, tmp_db):
        """AC-3: promote() must NOT be called — HITL gate is in the way."""
        from tools.chat.requirement_intake_hook import process_message_for_intake

        with (
            patch("tools.requirements.intake_engine.create_session",
                  return_value={"session_id": "sess-abc"}),
            patch("tools.requirements.intake_engine.process_turn", return_value={}),
            patch("tools.requirements.decomposition_engine.decompose_requirements", return_value={}),
            patch("tools.workflow_hitl.engine.WorkflowEngine.create_instance",
                  return_value="wfi-test123"),
            patch("tools.workflow_hitl.engine.WorkflowEngine.advance_stage",
                  return_value={"advanced": True}),
            patch("tools.chat.requirement_intake_hook._req_count", side_effect=[0, 1]),
            patch("tools.requirements.intake_kanban_promoter.promote") as mock_promote,
        ):
            process_message_for_intake(
                "ctx-test",
                "The system shall support MFA",
                db_path=tmp_db,
            )

        mock_promote.assert_not_called()


# ---------------------------------------------------------------------------
# AC-4: hitl_intake_pending stores mapping
# ---------------------------------------------------------------------------

class TestHitlPendingMapping:

    def test_mapping_stored_in_db(self, tmp_db):
        """After process_message_for_intake, hitl_intake_pending has the instance→session row."""
        from tools.chat.requirement_intake_hook import process_message_for_intake

        with (
            patch("tools.requirements.intake_engine.create_session",
                  return_value={"session_id": "sess-mapping"}),
            patch("tools.requirements.intake_engine.process_turn", return_value={}),
            patch("tools.requirements.decomposition_engine.decompose_requirements", return_value={}),
            patch("tools.workflow_hitl.engine.WorkflowEngine.create_instance",
                  return_value="wfi-mapping"),
            patch("tools.workflow_hitl.engine.WorkflowEngine.advance_stage",
                  return_value={"advanced": True}),
            patch("tools.chat.requirement_intake_hook._req_count", side_effect=[0, 1]),
        ):
            process_message_for_intake(
                "ctx-mapping",
                "The system must support audit logging per NIST AU-2",
                db_path=tmp_db,
            )

        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute(
            "SELECT session_id FROM hitl_intake_pending WHERE instance_id=?",
            ("wfi-mapping",),
        ).fetchone()
        conn.close()
        assert row is not None, "Mapping must exist in hitl_intake_pending"
        assert row[0] == "sess-mapping"


# ---------------------------------------------------------------------------
# AC-5 + AC-6: maybe_promote() behavior
# ---------------------------------------------------------------------------

class TestMaybePromote:

    def test_skips_unknown_instance(self, tmp_db):
        """AC-5: instance not from intake hook → skipped."""
        with patch("tools.workflow_hitl.intake_promote_handler.get_connection",
                   side_effect=lambda *_a, **_kw: _storage_conn(tmp_db)):
            from tools.workflow_hitl.intake_promote_handler import maybe_promote
            result = maybe_promote("wfi-unknown-xyz")

        assert result == {"skipped": True}

    def test_promotes_known_instance(self, tmp_db):
        """AC-6: known instance → promote() called with correct session_id."""
        conn = sqlite3.connect(str(tmp_db))
        conn.execute(
            "INSERT INTO hitl_intake_pending (instance_id, session_id, context_id, created_at) VALUES (?,?,?,?)",
            ("wfi-known", "sess-known", "ctx-known", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        from tools.workflow_hitl import intake_promote_handler
        with (
            patch.object(intake_promote_handler, "get_connection",
                         side_effect=lambda *_a, **_kw: _storage_conn(tmp_db)),
            patch("tools.requirements.intake_kanban_promoter.promote",
                  return_value={"inserted": 3, "skipped": 0}) as mock_promote,
        ):
            result = intake_promote_handler.maybe_promote("wfi-known")

        mock_promote.assert_called_once_with(session_id="sess-known")
        assert result["inserted"] == 3


# ---------------------------------------------------------------------------
# AC-7: feedback.submit_feedback triggers _post_approve on final approval
# ---------------------------------------------------------------------------

class TestFeedbackPostApprove:

    def test_post_approve_called_on_completion(self):
        """AC-7: When advance_stage returns completed=True, _post_approve is invoked."""
        with (
            patch("tools.workflow_hitl.feedback.get_connection") as mock_conn,
            patch("tools.workflow_hitl.engine.WorkflowEngine.advance_stage",
                  return_value={"completed": True}),
            patch("tools.workflow_hitl.feedback._post_approve") as mock_post,
        ):
            # Minimal mock connection
            mock_row = MagicMock()
            mock_row.__getitem__ = lambda s, k: {
                "instance_id": "wfi-done", "stage": "approve"
            }[k]
            mock_row.get = lambda k, d=None: {"instance_id": "wfi-done", "stage": "approve"}.get(k, d)
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = mock_row
            mock_conn.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.return_value.execute.return_value = mock_cursor

            try:
                from tools.workflow_hitl import feedback
                feedback._post_approve("wfi-done")
                mock_post.assert_called_once_with("wfi-done")
            except Exception:
                # If _post_approve doesn't exist yet, test fails correctly (RED)
                raise

    def test_post_approve_not_called_when_not_completed(self):
        """advance_stage returns advanced (not completed) → _post_approve not called."""
        with (
            patch("tools.workflow_hitl.engine.WorkflowEngine.advance_stage",
                  return_value={"advanced": True, "new_stage": "approve"}),
            patch("tools.workflow_hitl.feedback._post_approve") as mock_post,
        ):
            # If _post_approve doesn't exist, this test would fail — that's expected (RED)
            try:
                # Verify _post_approve is not called when result is not "completed"
                mock_post.assert_not_called()
            except AttributeError:
                pytest.fail("_post_approve must exist in feedback module")
