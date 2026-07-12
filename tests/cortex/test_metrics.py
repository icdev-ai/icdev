# CUI // SP-CTI
"""Tests for Cortex observability aggregation (tools/cortex/metrics.py).

Covers the pure aggregation over synthetic rows, the record_audit -> summarize
round-trip (accounting carried in gates_json), and graceful degradation when
the cortex_audit table is absent.
"""
from __future__ import annotations

import pytest

from tools.cortex import metrics
from tools.cortex.db.init_db import init_db, record_audit


@pytest.fixture
def cortex_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "cortex.db"))
    init_db()
    return tmp_path


# --------------------------------------------------------------------------- #
# Pure aggregation
# --------------------------------------------------------------------------- #
def test_aggregate_handles_tuple_and_dict_rows():
    import json
    # order: function, tenant_id, classification, outcome, blocked, gates_json
    tuple_rows = [
        ("cortex.complete", "t-a", "CUI", "pass", 0,
         json.dumps({"cost_usd": 0.01, "latency_ms": 100, "redactions_applied": 1,
                     "domain": "proposal"})),
        ("cortex.complete", "t-a", "CUI", "blocked", 1,
         json.dumps({"cost_usd": 0.0, "latency_ms": 0, "domain": "proposal"})),
        ("cortex.search", "t-b", "CUI", "warn", 0,
         json.dumps({"cost_usd": 0.02, "latency_ms": 300, "cache_hit": True})),
    ]
    dict_rows = [
        {"function": "cortex.ask", "tenant_id": "t-a", "classification": "CUI",
         "outcome": "pass", "blocked": False,
         "gates_json": json.dumps({"cost_usd": 0.005, "latency_ms": 50})},
    ]
    out = metrics._aggregate(tuple_rows + dict_rows, 24)
    s = out["summary"]
    assert out["available"] is True
    assert s["calls"] == 4
    assert s["blocked"] == 1
    assert s["block_rate_pct"] == 25.0
    assert s["redactions"] == 1
    assert s["cache_hits"] == 1
    assert abs(s["cost_usd"] - 0.035) < 1e-9
    # avg latency over rows with latency > 0: (100 + 300 + 50) / 3
    assert s["avg_latency_ms"] == pytest.approx((100 + 300 + 50) / 3, abs=0.1)
    by_fn = {f["function"]: f for f in out["by_function"]}
    assert by_fn["cortex.complete"]["calls"] == 2
    assert by_fn["cortex.complete"]["blocked"] == 1
    assert out["by_outcome"] == {"pass": 2, "blocked": 1, "warn": 1}
    domains = {d["domain"]: d["calls"] for d in out["by_domain"]}
    assert domains["proposal"] == 2
    assert domains["(none)"] == 2  # search + ask carried no domain


def test_aggregate_empty_is_zeroed():
    out = metrics._aggregate([], 24)
    assert out["available"] is True
    assert out["summary"]["calls"] == 0
    assert out["summary"]["block_rate_pct"] == 0.0
    assert out["by_function"] == []


# --------------------------------------------------------------------------- #
# record_audit -> summarize round-trip (accounting lives in gates_json)
# --------------------------------------------------------------------------- #
def test_summarize_reads_accounting_written_by_record_audit(cortex_db):
    record_audit({
        "operation": "cortex.complete", "tenant_id": "t-a", "classification": "CUI",
        "domain": "network", "outcomes": {"operation": "pass"}, "blocked": False,
        "cost_usd": 0.0123, "latency_ms": 250, "provider": "ollama", "model": "x",
    })
    record_audit({
        "operation": "cortex.complete", "tenant_id": "t-a", "classification": "CUI",
        "blocked": True, "blocked_gate": "pre_check",
    })
    out = metrics.summarize(window_hours=24)
    assert out["available"] is True
    assert out["summary"]["calls"] == 2
    assert out["summary"]["blocked"] == 1
    assert abs(out["summary"]["cost_usd"] - 0.0123) < 1e-6
    fns = {f["function"]: f for f in out["by_function"]}
    assert fns["cortex.complete"]["calls"] == 2
    domains = {d["domain"]: d["calls"] for d in out["by_domain"]}
    assert domains.get("network") == 1


def test_summarize_degrades_when_table_missing(tmp_path, monkeypatch):
    # Point at a brand-new DB with NO cortex tables created — summarize must not
    # raise, just report unavailable.
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "empty.db"))
    out = metrics.summarize(window_hours=24)
    assert out["available"] is False
    assert out["summary"]["calls"] == 0
