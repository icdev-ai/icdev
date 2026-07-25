# CUI // SP-CTI
"""Tests for reflex dependency ordering + execution caps (crx-gen-03).

Covers:
  * tools.daemon.base.topological_reflex_order — intra-cycle ordering honoring
    `depends_on`, ignoring not-due dependencies, stable for independents, and
    cycle-safe (never hangs).
  * GenesisDaemon.run_reflex_impl — per-reflex hard execution cap via
    `max_execution_seconds` (with `timeout_seconds` as a backward-compat alias,
    and max_execution_seconds taking precedence).
"""
from __future__ import annotations

import time
from unittest.mock import patch

from tools.daemon.base import topological_reflex_order


class TestTopologicalReflexOrder:
    def test_no_deps_preserves_order(self):
        due = ["a", "b", "c"]
        assert topological_reflex_order(due, {}) == ["a", "b", "c"]

    def test_dependency_runs_after(self):
        # b depends on a; even though b is listed first, a must run first.
        due = ["b", "a"]
        deps = {"b": ["a"]}
        assert topological_reflex_order(due, deps) == ["a", "b"]

    def test_not_due_dependency_ignored(self):
        # b depends on z, but z is not due this cycle → constraint ignored.
        due = ["b", "a"]
        deps = {"b": ["z"]}
        assert topological_reflex_order(due, deps) == ["b", "a"]

    def test_independents_stable(self):
        due = ["a", "b", "c", "d"]
        deps = {"d": ["a"]}  # only d constrained; a,b,c keep relative order
        assert topological_reflex_order(due, deps) == ["a", "b", "c", "d"]

    def test_chain_ordering(self):
        due = ["c", "b", "a"]
        deps = {"c": ["b"], "b": ["a"]}
        assert topological_reflex_order(due, deps) == ["a", "b", "c"]

    def test_cycle_does_not_hang(self):
        due = ["a", "b"]
        deps = {"a": ["b"], "b": ["a"]}
        out = topological_reflex_order(due, deps)
        assert sorted(out) == ["a", "b"]
        assert len(out) == 2  # every node emitted exactly once

    def test_self_edge_ignored(self):
        due = ["a", "b"]
        deps = {"a": ["a"]}
        assert topological_reflex_order(due, deps) == ["a", "b"]


def _make_daemon():
    from tools.genesis.daemon import GenesisDaemon

    cfg = {
        "enabled": True,
        "trust_mode": "full",
        "trust_kernel": {
            "circuit_breaker": {"max_consecutive_failures": 3},
            "risk_tiers": {"green": {"approval": "auto", "sandbox": False}},
        },
        "defaults": {"reflex_timeout_seconds": 300, "stub_loc_min": 10, "stub_loc_full": 15},
        "a2a": {"enabled": False, "gateway_url": "https://localhost:8443"},
    }
    return GenesisDaemon(cfg)


def _slow_inner(name, config, trust):
    time.sleep(2.0)
    return (True, 0.0, {})


class TestExecutionCap:
    def test_max_execution_seconds_enforced(self):
        d = _make_daemon()
        with patch.object(d, "_run_reflex_impl_inner", side_effect=_slow_inner):
            ok, _metric, details = d.run_reflex_impl("x", {"max_execution_seconds": 0.2}, d.trust)
        assert ok is False
        assert details.get("timeout") is True

    def test_timeout_seconds_alias_still_works(self):
        d = _make_daemon()
        with patch.object(d, "_run_reflex_impl_inner", side_effect=_slow_inner):
            ok, _metric, details = d.run_reflex_impl("x", {"timeout_seconds": 0.2}, d.trust)
        assert ok is False
        assert details.get("timeout") is True

    def test_max_execution_seconds_takes_precedence(self):
        # timeout_seconds would allow the 2s inner to finish (5s), but
        # max_execution_seconds=0.2 must win and trip the watchdog first.
        d = _make_daemon()
        with patch.object(d, "_run_reflex_impl_inner", side_effect=_slow_inner):
            ok, _metric, details = d.run_reflex_impl(
                "x", {"timeout_seconds": 5, "max_execution_seconds": 0.2}, d.trust
            )
        assert ok is False
        assert details.get("timeout") is True

    def test_within_cap_completes(self):
        d = _make_daemon()

        def fast_inner(name, config, trust):
            return (True, 1.0, {"status": "ok"})

        with patch.object(d, "_run_reflex_impl_inner", side_effect=fast_inner):
            ok, metric, details = d.run_reflex_impl("x", {"max_execution_seconds": 5}, d.trust)
        assert ok is True
        assert details.get("status") == "ok"
