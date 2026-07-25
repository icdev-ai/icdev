# CUI // SP-CTI
"""Regression tests for per-reflex connection isolation (crx-gen-01).

A Genesis reflex that opens get_connection() but raises before close() used to
leak a checked-out pool connection (and, historically, an idle-in-transaction
session that accumulated into the kanban_tasks lock storm). daemon.py now wraps
each reflex dispatch in tools.db.storage.reflex_connection_scope(), which rolls
back and closes any connection the reflex left open on that thread — while
leaving connections the reflex closed itself untouched (no double putconn()).

Acceptance criteria:
  1. A connection opened inside the scope and never closed is reclaimed
     (rolled back + closed) on scope exit.
  2. A connection the body closed itself is not double-closed / re-pooled.
  3. A reflex that raises mid-transaction is isolated: the scope reclaims its
     leaked connection and the NEXT reflex still gets a working connection.
"""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch

from tools.db.storage import get_connection, reflex_connection_scope


# ---------------------------------------------------------------------------
# Storage-level scope behavior
# ---------------------------------------------------------------------------
class TestReflexConnectionScope:
    def test_leaked_connection_is_reclaimed(self):
        """A connection left open inside the scope is closed on exit."""
        leaked = {}
        with reflex_connection_scope():
            conn = get_connection()
            conn.execute("SELECT 1")
            leaked["conn"] = conn
            # Deliberately do NOT close — simulate a reflex that forgot cleanup.
        assert leaked["conn"]._closed is True

    def test_leaked_on_exception_is_reclaimed(self):
        """The scope reclaims the connection even when the body raises."""
        leaked = {}
        try:
            with reflex_connection_scope():
                conn = get_connection()
                conn.execute("SELECT 1")
                leaked["conn"] = conn
                raise RuntimeError("reflex blew up mid-transaction")
        except RuntimeError:
            pass
        assert leaked["conn"]._closed is True

    def test_self_closed_connection_not_double_closed(self):
        """A connection the body closed itself is left untouched (no double close)."""
        with reflex_connection_scope():
            conn = get_connection()
            conn.execute("SELECT 1")
            conn.close()
            assert conn._closed is True
        # Second close via reclamation must be skipped — no exception raised,
        # and the flag stays True.
        assert conn._closed is True

    def test_next_scope_works_after_leak(self):
        """A leak in one scope does not break connections in the next scope."""
        with reflex_connection_scope():
            _ = get_connection()  # leaked intentionally
        # Next unit of work must get a fully usable connection.
        with reflex_connection_scope():
            conn2 = get_connection()
            row = conn2.execute("SELECT 1 AS ok").fetchone()
            assert row is not None
            conn2.close()

    def test_nested_scopes_track_innermost(self):
        """Nested scopes reclaim independently without leaking across levels."""
        outer = {}
        inner = {}
        with reflex_connection_scope():
            outer["conn"] = get_connection()  # tracked by outer
            with reflex_connection_scope():
                inner["conn"] = get_connection()  # tracked by inner
            # Inner scope exited: its connection reclaimed, outer's still open.
            assert inner["conn"]._closed is True
            assert outer["conn"]._closed is False
        assert outer["conn"]._closed is True


# ---------------------------------------------------------------------------
# Daemon-level isolation of a crashing reflex
# ---------------------------------------------------------------------------
def _make_daemon():
    from tools.genesis.daemon import GenesisDaemon

    cfg = {
        "enabled": True,
        "trust_mode": "full",
        "trust_kernel": {
            "circuit_breaker": {"max_consecutive_failures": 3},
            "risk_tiers": {
                "green": {"approval": "auto", "sandbox": False},
                "yellow": {"approval": "auto", "sandbox": True},
                "orange": {"approval": "human", "sandbox": True},
            },
        },
        "defaults": {"reflex_timeout_seconds": 300, "stub_loc_min": 10, "stub_loc_full": 15},
        "a2a": {"enabled": False, "gateway_url": "https://localhost:8443"},
    }
    return GenesisDaemon(cfg)


class _FakeReflexModule:
    """Stand-in reflex module whose run() leaks a connection and raises."""

    IMPLEMENTATION_STATUS = "full"

    def __init__(self, sink: Dict[str, Any], crash: bool):
        self._sink = sink
        self._crash = crash

    def run(self, config, trust):
        conn = get_connection()
        conn.execute("SELECT 1")
        self._sink["conn"] = conn  # leaked: never closed by the reflex
        if self._crash:
            raise RuntimeError("boom mid-transaction")
        return {"success": True, "metric_value": 1.0, "details": {}}


class TestDaemonReflexIsolation:
    def test_crashing_reflex_is_isolated_and_reclaimed(self):
        daemon = _make_daemon()
        trust = daemon.trust
        sink: Dict[str, Any] = {}
        fake = _FakeReflexModule(sink, crash=True)

        with patch("tools.genesis.daemon.importlib.import_module", return_value=fake):
            success, _metric, _details = daemon._run_reflex_impl_inner(
                "faketest", {"risk_tier": "green"}, trust
            )

        # The crashing reflex is reported as a failure, not propagated.
        assert success is False
        # Its leaked connection was reclaimed by the per-reflex scope.
        assert sink["conn"]._closed is True

        # A subsequent reflex still works end-to-end.
        sink2: Dict[str, Any] = {}
        ok_reflex = _FakeReflexModule(sink2, crash=False)
        with patch("tools.genesis.daemon.importlib.import_module", return_value=ok_reflex):
            success2, _m2, _d2 = daemon._run_reflex_impl_inner(
                "faketest2", {"risk_tier": "green"}, trust
            )
        assert success2 is True
        assert sink2["conn"]._closed is True
