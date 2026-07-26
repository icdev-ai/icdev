# CUI // SP-CTI
"""lpx-obs-01 — proxy spend + rate metrics collector.

Proves the observability collector aggregates ICDEV's own spend/rate ledgers
(the always-present source of truth) and degrades gracefully when the proxy
Prometheus endpoint is absent — no proxy container is required, preserving the
air-gap guarantee (lpx-egress-01).

Uses the shared conftest schema / get_connection; no raw sqlite3.
"""

from __future__ import annotations

import importlib

import pytest

pm = importlib.import_module("tools.llm.proxy_metrics")
pk = importlib.import_module("tools.llm.proxy_keys")
pb = importlib.import_module("tools.llm.proxy_budgets")
ptl = importlib.import_module("tools.llm.proxy_team_limits")

from tools.db.storage import get_connection


@pytest.fixture(autouse=True)
def _no_proxy_env(monkeypatch):
    for var in ("ICDEV_LLM_PROXY_ENABLED", "ICDEV_LLM_PROXY_BASE_URL",
                "ICDEV_LLM_PROXY_VIRTUAL_KEY", "ICDEV_LLM_LOCAL_COPY"):
        monkeypatch.delenv(var, raising=False)
    yield


def _seed_spend(conn):
    pb.ensure_schema(conn)
    issued = pk.issue_key(scope_type="team", scope_ref="7", session_id="42",
                          max_budget_usd=10.0, budget_window="exercise", conn=conn)
    key_id = issued["key_id"]
    pb.record_spend(key_id, cost_usd=0.25, input_tokens=1000, output_tokens=400, conn=conn)
    pb.record_spend(key_id, cost_usd=0.10, input_tokens=500, output_tokens=200, conn=conn)
    return key_id


def test_ledger_spend_aggregates_by_key_and_scope():
    conn = get_connection()
    try:
        key_id = _seed_spend(conn)
        out = pm.collect_ledger_spend(window_hours=24, conn=conn)
        assert out["available"] is True
        assert out["total_spend_usd"] == pytest.approx(0.35, abs=1e-6)
        assert out["event_count"] == 2
        assert out["total_input_tokens"] == 1500
        keys = {r["key_id"] for r in out["by_key"]}
        assert key_id in keys
        scopes = {r["scope_type"] for r in out["by_scope"]}
        assert "team" in scopes
    finally:
        conn.close()


def test_ledger_rate_aggregates_team_usage():
    conn = get_connection()
    try:
        ptl.ensure_schema(conn)
        # Record a couple of calls into the current minute bucket.
        ptl.record_team_call(1, 1, tokens=100, conn=conn)
        ptl.record_team_call(1, 1, tokens=200, conn=conn)
        out = pm.collect_ledger_rate(window_hours=24, conn=conn)
        assert out["available"] is True
        assert out["total_requests"] >= 2
        assert out["total_tokens"] >= 300
    finally:
        conn.close()


def test_prometheus_scrape_noop_when_proxy_disabled():
    out = pm.scrape_proxy_prometheus()
    assert out["available"] is False
    assert "disabled" in out["reason"].lower()


def test_prometheus_scrape_unreachable_is_graceful(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    monkeypatch.setenv("ICDEV_LLM_PROXY_BASE_URL", "http://127.0.0.1:1")
    out = pm.scrape_proxy_prometheus(timeout=0.2)
    assert out["available"] is False
    assert "unreachable" in out["reason"].lower()


def test_collect_proxy_metrics_shape():
    conn = get_connection()
    try:
        _seed_spend(conn)
        out = pm.collect_proxy_metrics(window_hours=24, conn=conn)
        assert out["proxy_enabled"] is False
        assert "ledger_spend" in out and out["ledger_spend"]["available"] is True
        assert "ledger_rate" in out
        assert "prometheus" in out
        assert "source_note" in out
    finally:
        conn.close()
