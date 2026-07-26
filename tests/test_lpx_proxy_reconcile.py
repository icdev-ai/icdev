# CUI // SP-CTI
"""lpx-obs-02 — reconcile proxy spend vs token_tracker.

Proves the reconciler compares windowed AGGREGATES (not a row join, since the two
ledgers are keyed differently), flags divergence past a threshold, and treats an
inactive proxy as 'nothing to reconcile' rather than a false alarm.

Uses the shared conftest schema / get_connection; no raw sqlite3.
"""

from __future__ import annotations

import importlib

import pytest

rc = importlib.import_module("tools.llm.proxy_reconcile")
pk = importlib.import_module("tools.llm.proxy_keys")
pb = importlib.import_module("tools.llm.proxy_budgets")
tt = importlib.import_module("tools.agent.token_tracker")

from tools.db.storage import get_connection


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in ("ICDEV_LLM_PROXY_ENABLED", "ICDEV_LLM_PROXY_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    # The conftest DB is shared across tests in a session; isolate each test by
    # clearing the (non-audit) ledgers it aggregates. Best-effort.
    conn = get_connection()
    try:
        pb.ensure_schema(conn)
        for tbl in ("llm_proxy_spend", "llm_proxy_keys", "agent_token_usage"):
            try:
                conn.execute(f"DELETE FROM {tbl}")  # nosec B608 — fixed identifiers, test-only
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()
    yield


def _record_proxy(conn, cost, itok=0, otok=0, tenant="t1"):
    pb.ensure_schema(conn)
    issued = pk.issue_key(scope_type="tenant", scope_ref=tenant, max_budget_usd=None,
                          budget_window="none", tenant_id=tenant, conn=conn)
    pb.record_spend(issued["key_id"], cost_usd=cost, input_tokens=itok,
                    output_tokens=otok, conn=conn)


def _record_tracker(conn, cost, itok=0, otok=0):
    tt.log_usage("agent-x", "proj-x", "model-x", itok, otok,
                 cost_estimate_usd=cost)


def test_proxy_inactive_is_not_a_false_alarm():
    conn = get_connection()
    try:
        # No proxy spend recorded → nothing to reconcile.
        report = rc.reconcile(window_hours=24, conn=conn)
        assert report["status"] == "proxy_inactive"
        assert report["gate_fail"] is False
    finally:
        conn.close()


def test_reconciled_when_within_threshold():
    conn = get_connection()
    try:
        _record_proxy(conn, 1.00, itok=1000, otok=500)
        _record_tracker(conn, 1.02, itok=1000, otok=500)  # ~2% apart
        report = rc.reconcile(window_hours=24, threshold_pct=10.0, conn=conn)
        assert report["status"] == "reconciled"
        assert report["gate_fail"] is False
        assert report["divergence"]["spend_pct"] < 10.0
    finally:
        conn.close()


def test_divergent_when_over_threshold():
    conn = get_connection()
    try:
        _record_proxy(conn, 1.00)
        _record_tracker(conn, 5.00)  # 80% divergence
        report = rc.reconcile(window_hours=24, threshold_pct=10.0, conn=conn)
        assert report["status"] == "divergent"
        assert report["gate_fail"] is True
        assert report["divergence"]["spend_pct"] > 10.0
        assert report["structural_notes"]  # gaps explained, not hidden
    finally:
        conn.close()


def test_no_join_uses_aggregates():
    # The reconciler must not require a shared key between the two ledgers.
    conn = get_connection()
    try:
        _record_proxy(conn, 2.00)          # keyed by virtual key/scope
        _record_tracker(conn, 2.00)        # keyed by agent — no common column
        report = rc.reconcile(window_hours=24, conn=conn)
        assert report["proxy"]["spend_usd"] == pytest.approx(2.00, abs=1e-6)
        assert report["token_tracker"]["spend_usd"] == pytest.approx(2.00, abs=1e-6)
        assert report["status"] == "reconciled"
    finally:
        conn.close()


def test_divergence_pct_bounded_and_zero_safe():
    assert rc._divergence_pct(0.0, 0.0) == 0.0
    assert rc._divergence_pct(1.0, 0.0) == 100.0
    assert 0.0 <= rc._divergence_pct(3.0, 5.0) <= 100.0
