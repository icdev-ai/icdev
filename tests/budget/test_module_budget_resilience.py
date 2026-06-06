# CUI // SP-CTI
"""Budget bookkeeping must never block or crash the LLM critical path.

Regression guard for the lock-storm hardening: a locked / slow / unavailable
budget DB must fail OPEN (allow) rather than hang or raise.
"""
from __future__ import annotations

import importlib

import pytest

mbt = importlib.import_module("tools.budget.module_budget_tracker")


@pytest.fixture
def force_enabled(monkeypatch):
    """Force the module budget 'enabled' so the DB path is exercised."""
    monkeypatch.setattr(mbt, "_get_module_budget_config",
                        lambda module: {"enabled": True, "budget_usd": 100.0,
                                        "warning_threshold": 0.8, "hard_stop": True})


def test_check_fails_open_when_db_unavailable(force_enabled, monkeypatch):
    def _boom():
        raise RuntimeError("db locked / timeout")
    monkeypatch.setattr(mbt, "_get_conn", _boom)

    result = mbt.check_module_budget("generative_intelligence", function="code_generation")
    assert result["action"] == "allow"
    assert "failing open" in result["message"].lower()


def test_record_usage_swallows_db_errors(monkeypatch):
    def _boom():
        raise RuntimeError("db locked / timeout")
    monkeypatch.setattr(mbt, "_get_conn", _boom)

    # Must not raise, and returns a record id even when persistence fails.
    rid = mbt.record_module_usage("generative_intelligence", cost_usd=0.01, tokens=100)
    assert isinstance(rid, str) and rid.startswith("mbu-")


def test_unknown_module_allows_without_db(monkeypatch):
    # Should never touch the DB for an unknown module.
    monkeypatch.setattr(mbt, "_get_conn",
                        lambda: (_ for _ in ()).throw(AssertionError("DB must not be opened")))
    assert mbt.check_module_budget("not_a_module")["action"] == "allow"


def test_set_budget_timeouts_is_safe_on_sqlite():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    try:
        mbt._set_budget_timeouts(conn)  # must not raise on any backend
    finally:
        conn.close()
