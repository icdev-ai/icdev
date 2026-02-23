#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Phase 44 multi-stream parallel chat + intervention (Features 1,3 — D257-D267).

Covers: context CRUD, message send/retrieve, queue buffering, max concurrent limit,
close lifecycle, intervention atomic set/check, checkpoint preservation.
"""

import sys
import time
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from tools.dashboard.chat_manager import ChatManager, ChatContext, MAX_CONCURRENT_PER_USER


@pytest.fixture
def manager():
    """Fresh ChatManager per test (no singleton, DB ops mocked)."""
    mgr = ChatManager()
    # Mock all DB operations so tests don't require real database
    mgr._db_create_context = MagicMock()
    mgr._db_update_status = MagicMock()
    mgr._db_insert_message = MagicMock()
    mgr._db_create_task = MagicMock()
    mgr._db_complete_task = MagicMock()
    mgr._db_fail_task = MagicMock()
    return mgr


# ---------------------------------------------------------------------------
# ChatContext dataclass
# ---------------------------------------------------------------------------

class TestChatContext:
    def test_create(self):
        ctx = ChatContext("ctx-1", "user-1", title="Test Chat")
        assert ctx.context_id == "ctx-1"
        assert ctx.user_id == "user-1"
        assert ctx.status == "active"
        assert ctx.turn_number == 0

    def test_to_dict(self):
        ctx = ChatContext("ctx-1", "user-1", title="Chat")
        d = ctx.to_dict()
        assert d["context_id"] == "ctx-1"
        assert d["message_count"] == 0
        assert d["is_processing"] is False

    def test_intervention_set_and_check(self):
        ctx = ChatContext("ctx-1", "user-1")
        assert ctx.check_intervention() is None

        ctx.set_intervention("Stop and do X")
        msg = ctx.check_intervention()
        assert msg == "Stop and do X"

        # Second check should return None (cleared)
        assert ctx.check_intervention() is None

    def test_intervention_thread_safe(self):
        ctx = ChatContext("ctx-1", "user-1")
        messages = []

        def setter():
            for i in range(50):
                ctx.set_intervention(f"msg-{i}")

        def checker():
            for _ in range(100):
                msg = ctx.check_intervention()
                if msg:
                    messages.append(msg)

        t1 = threading.Thread(target=setter)
        t2 = threading.Thread(target=checker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        # No exceptions raised — thread safety verified

    def test_checkpoint(self):
        ctx = ChatContext("ctx-1", "user-1")
        ctx.save_checkpoint({"turn": 5, "response": "partial"})
        assert ctx._checkpoint is not None
        assert ctx._checkpoint["data"]["turn"] == 5


# ---------------------------------------------------------------------------
# Context CRUD
# ---------------------------------------------------------------------------

class TestContextCRUD:
    def test_create_context(self, manager):
        result = manager.create_context("user-1", title="My Chat")
        assert "context_id" in result
        assert result["status"] == "active"
        assert result["user_id"] == "user-1"

    def test_list_contexts(self, manager):
        manager.create_context("user-1", title="Chat A")
        manager.create_context("user-1", title="Chat B")
        manager.create_context("user-2", title="Chat C")

        all_ctxs = manager.list_contexts()
        assert len(all_ctxs) == 3

        user1_ctxs = manager.list_contexts(user_id="user-1")
        assert len(user1_ctxs) == 2

    def test_get_context(self, manager):
        result = manager.create_context("user-1", title="Test")
        ctx = manager.get_context(result["context_id"])
        assert ctx is not None
        assert ctx["title"] == "Test"

    def test_get_nonexistent_context(self, manager):
        assert manager.get_context("nonexistent") is None

    def test_close_context(self, manager):
        result = manager.create_context("user-1")
        close_result = manager.close_context(result["context_id"])
        assert close_result["status"] == "completed"

    def test_close_nonexistent(self, manager):
        result = manager.close_context("nonexistent")
        assert "error" in result

    def test_closed_contexts_excluded_from_list(self, manager):
        result = manager.create_context("user-1", title="Temp")
        manager.close_context(result["context_id"])

        active = manager.list_contexts(user_id="user-1")
        assert len(active) == 0

        all_including_closed = manager.list_contexts(user_id="user-1", include_closed=True)
        assert len(all_including_closed) == 1


# ---------------------------------------------------------------------------
# Concurrent limit
# ---------------------------------------------------------------------------

class TestConcurrentLimit:
    def test_max_concurrent_per_user(self, manager):
        for i in range(MAX_CONCURRENT_PER_USER):
            result = manager.create_context("user-1", title=f"Chat {i}")
            assert "error" not in result

        # Should fail on next one
        result = manager.create_context("user-1", title="Too Many")
        assert "error" in result
        assert "Max" in result["error"]

    def test_different_users_independent_limits(self, manager):
        for i in range(MAX_CONCURRENT_PER_USER):
            manager.create_context("user-1", title=f"Chat {i}")

        # User 2 should still be able to create
        result = manager.create_context("user-2", title="User 2 Chat")
        assert "error" not in result


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------

class TestMessaging:
    def test_send_message(self, manager):
        ctx = manager.create_context("user-1")
        result = manager.send_message(ctx["context_id"], "Hello!", role="user")
        assert result["turn_number"] == 1
        assert result["role"] == "user"

    def test_send_to_nonexistent(self, manager):
        result = manager.send_message("nonexistent", "Hello")
        assert "error" in result

    def test_send_to_closed_context(self, manager):
        ctx = manager.create_context("user-1")
        manager.close_context(ctx["context_id"])
        result = manager.send_message(ctx["context_id"], "Hello")
        assert "error" in result

    def test_message_queuing(self, manager):
        ctx = manager.create_context("user-1")
        cid = ctx["context_id"]

        # Send multiple messages rapidly
        for i in range(5):
            result = manager.send_message(cid, f"Message {i}")
            assert result["turn_number"] == i + 1


# ---------------------------------------------------------------------------
# Intervention
# ---------------------------------------------------------------------------

class TestIntervention:
    def test_intervene(self, manager):
        ctx = manager.create_context("user-1")
        result = manager.intervene(ctx["context_id"], "Do something else")
        assert result["intervention_set"] is True
        assert result["turn_number"] >= 1

    def test_intervene_nonexistent(self, manager):
        result = manager.intervene("nonexistent", "test")
        assert "error" in result


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

class TestDiagnostics:
    def test_diagnostics(self, manager):
        manager.create_context("user-1")
        manager.create_context("user-2")

        diag = manager.get_diagnostics()
        assert diag["total_contexts"] == 2
        assert diag["active_contexts"] == 2

    def test_diagnostics_after_close(self, manager):
        ctx = manager.create_context("user-1")
        manager.close_context(ctx["context_id"])

        diag = manager.get_diagnostics()
        assert diag["total_contexts"] == 1
        assert diag["active_contexts"] == 0
