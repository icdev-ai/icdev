# CUI // SP-CTI
"""lpx-teams-02 — per-team gameday budget tests.

A team's budget is its key's budget (exercise-windowed). Exhaustion must fail
with a clear facilitator message, not a generic error, and must block ONLY that
team (see the abac-pip lesson — always test the deny case).
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest


@pytest.fixture
def tb(tmp_path, monkeypatch):
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.delenv("ICDEV_LLM_PROXY_ENABLED", raising=False)
    monkeypatch.setenv("ICDEV_LLM_PROXY_KEY_TTL_DAYS", "0")  # no default expiry noise

    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()

    keys = importlib.import_module("tools.llm.proxy_keys")
    budg = importlib.import_module("tools.llm.proxy_budgets")
    mod = importlib.import_module("tools.llm.proxy_team_budgets")
    keys._migrated = False
    budg._migrated = False
    keys.ensure_schema()
    budg.ensure_schema()
    return mod


def test_provision_creates_exercise_scoped_team_key(tb):
    out = tb.provision_team_budget(42, 7, 10.0)
    assert out["reused"] is False
    assert out["budget_window"] == "exercise"
    assert "virtual_key" in out  # issued once
    # Re-provision updates in place (no duplicate key).
    again = tb.provision_team_budget(42, 7, 25.0)
    assert again["reused"] is True
    status = tb.team_budget_status(42)
    assert len(status) == 1
    assert status[0]["budget_usd"] == 25.0


def test_check_allows_within_budget(tb):
    tb.provision_team_budget(1, 1, 10.0)
    r = tb.check_team_budget(1, 1, projected_cost_usd=1.0)
    assert r["allowed"] is True
    assert r["action"] == "allow"


def test_exhausted_budget_blocks_with_facilitator_message(tb):
    tb.provision_team_budget(1, 1, 5.0)
    tb.record_team_spend(1, 1, cost_usd=5.0)
    r = tb.check_team_budget(1, 1)
    assert r["allowed"] is False
    assert r["action"] == "block"
    assert "exhausted" in r["facilitator_message"].lower()
    assert "Team 1" in r["facilitator_message"]


def test_block_is_scoped_to_one_team(tb):
    tb.provision_team_budget(9, 1, 5.0)
    tb.provision_team_budget(9, 2, 5.0)
    tb.record_team_spend(9, 1, cost_usd=5.0)
    assert tb.check_team_budget(9, 1)["allowed"] is False
    # Opponent unaffected.
    assert tb.check_team_budget(9, 2)["allowed"] is True


def test_budget_is_per_exercise_and_resets_per_session(tb):
    # Same team_id number, different sessions => separate budgets/windows.
    tb.provision_team_budget(100, 1, 5.0)
    tb.provision_team_budget(200, 1, 5.0)
    tb.record_team_spend(100, 1, cost_usd=5.0)
    assert tb.check_team_budget(100, 1)["allowed"] is False
    assert tb.check_team_budget(200, 1)["allowed"] is True  # next exercise, fresh


def test_no_budget_fails_open_with_message(tb):
    r = tb.check_team_budget(5, 5)
    assert r["allowed"] is True
    assert "no exercise budget" in r["facilitator_message"].lower()


def test_record_without_provision_raises(tb):
    with pytest.raises(ValueError):
        tb.record_team_spend(1, 99, cost_usd=1.0)


def test_status_reports_spend_vs_budget(tb):
    tb.provision_team_budget(3, 1, 10.0)
    tb.record_team_spend(3, 1, cost_usd=4.0)
    st = {s["team_id"]: s for s in tb.team_budget_status(3)}
    assert st[1]["spent_usd"] == 4.0
    assert st[1]["remaining_usd"] == 6.0
    assert st[1]["exhausted"] is False
