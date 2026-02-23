#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Phase 44 dirty-tracking state push (Feature 4 — D268-D270).

Covers: version monotonicity, incremental changes, client registration,
debounce batching, context filtering, diagnostics.
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from tools.dashboard.state_tracker import StateTracker, ClientState


@pytest.fixture
def tracker():
    """Fresh StateTracker per test (no singleton)."""
    return StateTracker(debounce_ms=5, max_changes_buffer=50)


# ---------------------------------------------------------------------------
# Client management
# ---------------------------------------------------------------------------

class TestClientRegistration:
    def test_register_client(self, tracker):
        client = tracker.register_client("c-1")
        assert isinstance(client, ClientState)
        assert client.client_id == "c-1"
        assert tracker.client_count == 1

    def test_unregister_client(self, tracker):
        tracker.register_client("c-1")
        tracker.unregister_client("c-1")
        assert tracker.client_count == 0

    def test_unregister_nonexistent(self, tracker):
        tracker.unregister_client("nonexistent")  # should not raise
        assert tracker.client_count == 0

    def test_set_viewing_context(self, tracker):
        client = tracker.register_client("c-1")
        tracker.set_viewing_context("c-1", "ctx-abc")
        assert client.viewing_context == "ctx-abc"

    def test_set_viewing_unknown_client(self, tracker):
        tracker.set_viewing_context("unknown", "ctx-abc")  # should not raise


# ---------------------------------------------------------------------------
# Version tracking
# ---------------------------------------------------------------------------

class TestVersionTracking:
    def test_initial_version_is_zero(self, tracker):
        assert tracker.get_version("ctx-1") == 0

    def test_mark_dirty_increments(self, tracker):
        v1 = tracker.mark_dirty("ctx-1", "new_message")
        assert v1 == 1
        v2 = tracker.mark_dirty("ctx-1", "new_message")
        assert v2 == 2

    def test_version_monotonically_increasing(self, tracker):
        versions = [tracker.mark_dirty("ctx-1", "msg") for _ in range(10)]
        assert versions == list(range(1, 11))

    def test_independent_context_versions(self, tracker):
        tracker.mark_dirty("ctx-a", "msg")
        tracker.mark_dirty("ctx-a", "msg")
        tracker.mark_dirty("ctx-b", "msg")
        assert tracker.get_version("ctx-a") == 2
        assert tracker.get_version("ctx-b") == 1


# ---------------------------------------------------------------------------
# Incremental updates
# ---------------------------------------------------------------------------

class TestIncrementalUpdates:
    def test_get_updates_all(self, tracker):
        tracker.mark_dirty("ctx-1", "msg", {"turn": 1})
        tracker.mark_dirty("ctx-1", "msg", {"turn": 2})
        tracker.register_client("c-1")

        result = tracker.get_updates("c-1", "ctx-1", since_version=0)
        assert result["dirty_version"] == 2
        assert result["up_to_date"] is False
        assert len(result["changes"]) == 2

    def test_get_updates_incremental(self, tracker):
        tracker.mark_dirty("ctx-1", "msg", {"turn": 1})
        tracker.mark_dirty("ctx-1", "msg", {"turn": 2})
        tracker.mark_dirty("ctx-1", "msg", {"turn": 3})
        tracker.register_client("c-1")

        result = tracker.get_updates("c-1", "ctx-1", since_version=2)
        assert len(result["changes"]) == 1
        assert result["changes"][0]["version"] == 3

    def test_up_to_date(self, tracker):
        tracker.mark_dirty("ctx-1", "msg")
        tracker.register_client("c-1")

        result = tracker.get_updates("c-1", "ctx-1", since_version=1)
        assert result["up_to_date"] is True
        assert result["changes"] == []

    def test_acknowledge_updates_pushed_version(self, tracker):
        tracker.register_client("c-1")
        tracker.mark_dirty("ctx-1", "msg")
        tracker.acknowledge("c-1", 1)

        with tracker._lock:
            assert tracker._clients["c-1"].pushed_version == 1


# ---------------------------------------------------------------------------
# Buffer management
# ---------------------------------------------------------------------------

class TestBufferManagement:
    def test_max_changes_buffer(self):
        tracker = StateTracker(debounce_ms=1, max_changes_buffer=5)
        for i in range(10):
            tracker.mark_dirty("ctx-1", "msg")

        with tracker._lock:
            assert len(tracker._context_changes["ctx-1"]) == 5
            # Should keep the most recent entries
            assert tracker._context_changes["ctx-1"][-1]["version"] == 10


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

class TestDiagnostics:
    def test_diagnostics(self, tracker):
        tracker.register_client("c-1")
        tracker.mark_dirty("ctx-1", "msg")
        tracker.mark_dirty("ctx-2", "msg")

        diag = tracker.get_diagnostics()
        assert diag["clients"] == 1
        assert diag["tracked_contexts"] == 2

    def test_tracked_contexts_property(self, tracker):
        assert tracker.tracked_contexts == 0
        tracker.mark_dirty("ctx-1", "msg")
        assert tracker.tracked_contexts == 1
