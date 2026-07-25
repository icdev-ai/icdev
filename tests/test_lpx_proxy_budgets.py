# CUI // SP-CTI
"""lpx-keys-02 — per-key budget accounting + enforcement tests.

Proves budgets wire onto the three non-uniform grouping units (team / guild /
user) via a key's scope, that spend accumulates per window, and — the point of
the card — that the DENY case fires: an exhausted budget blocks ONLY that key's
scope (see the abac-pip lesson: always test the deny case).
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest


@pytest.fixture
def budgets(tmp_path, monkeypatch):
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.delenv("ICDEV_LLM_PROXY_ENABLED", raising=False)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()

    keys = importlib.import_module("tools.llm.proxy_keys")
    budg = importlib.import_module("tools.llm.proxy_budgets")
    keys._migrated = False
    budg._migrated = False
    keys.ensure_schema()
    budg.ensure_schema()
    return keys, budg


def test_unlimited_key_always_allows(budgets):
    keys, budg = budgets
    k = keys.issue_key(scope_type="tenant", scope_ref="default")  # no budget
    r = budg.check_budget(k["key_id"], projected_cost_usd=999.0)
    assert r["action"] == "allow"
    assert r["budget_usd"] is None


def test_spend_accumulates_and_blocks_at_budget(budgets):
    keys, budg = budgets
    k = keys.issue_key(scope_type="team", scope_ref="7", session_id="42",
                       max_budget_usd=10.0, budget_window="exercise")
    kid = k["key_id"]

    assert budg.check_budget(kid)["action"] == "allow"
    budg.record_spend(kid, cost_usd=4.0)
    assert budg.get_spend(kid)["spent_usd"] == 4.0
    # 80% warn threshold
    budg.record_spend(kid, cost_usd=4.5)
    assert budg.check_budget(kid)["action"] == "warn"
    # projecting over the top blocks
    assert budg.check_budget(kid, projected_cost_usd=2.0)["action"] == "block"
    # exhaust it fully -> block regardless of projection (THE DENY CASE)
    budg.record_spend(kid, cost_usd=2.0)
    denied = budg.check_budget(kid)
    assert denied["action"] == "block"
    assert denied["scope_type"] == "team"
    assert denied["scope_ref"] == "7"


def test_deny_is_scoped_only_to_that_key(budgets):
    """One team exhausting its budget must NOT block another team."""
    keys, budg = budgets
    blue = keys.issue_key(scope_type="team", scope_ref="1", session_id="99",
                          max_budget_usd=5.0, budget_window="exercise")
    red = keys.issue_key(scope_type="team", scope_ref="2", session_id="99",
                         max_budget_usd=5.0, budget_window="exercise")
    budg.record_spend(blue["key_id"], cost_usd=5.0)
    assert budg.check_budget(blue["key_id"])["action"] == "block"
    # Red is untouched.
    assert budg.check_budget(red["key_id"])["action"] == "allow"
    assert budg.get_spend(red["key_id"])["spent_usd"] == 0.0


def test_windows_isolate_spend(budgets):
    keys, budg = budgets
    k = keys.issue_key(scope_type="guild", scope_ref="g1", max_budget_usd=10.0, budget_window="month")
    kid = k["key_id"]
    from datetime import datetime, timezone

    jul = datetime(2026, 7, 15, tzinfo=timezone.utc)
    aug = datetime(2026, 8, 15, tzinfo=timezone.utc)
    budg.record_spend(kid, cost_usd=9.0, now=jul)
    assert budg.check_budget(kid, now=jul, projected_cost_usd=2.0)["action"] == "block"
    # New month resets the window.
    assert budg.check_budget(kid, now=aug)["action"] == "allow"
    assert budg.get_spend(kid, now=aug)["spent_usd"] == 0.0


def test_revoked_key_blocks(budgets):
    keys, budg = budgets
    k = keys.issue_key(scope_type="user", scope_ref="alice", max_budget_usd=50.0, budget_window="month")
    kid = k["key_id"]
    # Simulate revocation (keys-03 owns the op; here flip status directly).
    from tools.db.storage import get_connection

    c = get_connection()
    c.execute("UPDATE llm_proxy_keys SET status = %s WHERE key_id = %s", ("revoked", kid))
    c.commit()
    assert budg.check_budget(kid)["action"] == "block"


def test_resolve_active_key_by_scope(budgets):
    keys, budg = budgets
    keys.issue_key(scope_type="team", scope_ref="3", session_id="55", max_budget_usd=10.0, budget_window="exercise")
    found = budg.resolve_active_key("team", "3", session_id="55")
    assert found is not None
    assert found["scope_ref"] == "3"
    assert budg.resolve_active_key("team", "does-not-exist") is None


def test_record_spend_unknown_key_raises(budgets):
    keys, budg = budgets
    with pytest.raises(ValueError):
        budg.record_spend("nope", cost_usd=1.0)
