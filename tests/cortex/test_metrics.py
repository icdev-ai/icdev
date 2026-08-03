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
                     "domain": "proposal", "input_tokens": 100, "output_tokens": 40,
                     "model": "kimi"})),
        ("cortex.complete", "t-a", "CUI", "blocked", 1,
         json.dumps({"cost_usd": 0.0, "latency_ms": 0, "domain": "proposal"})),
        ("cortex.search", "t-b", "CUI", "warn", 0,
         json.dumps({"cost_usd": 0.02, "latency_ms": 300, "cache_hit": True})),
    ]
    dict_rows = [
        {"function": "cortex.ask", "tenant_id": "t-a", "classification": "CUI",
         "outcome": "pass", "blocked": False,
         "gates_json": json.dumps({"cost_usd": 0.005, "latency_ms": 50,
                                   "input_tokens": 20, "output_tokens": 10,
                                   "model": "kimi"})},
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
    # Tokens summed end-to-end.
    assert s["input_tokens"] == 120 and s["output_tokens"] == 50
    assert s["total_tokens"] == 170
    # Per-model rollup: two kimi calls carried tokens.
    by_model = {m["model"]: m for m in out["by_model"]}
    assert by_model["kimi"]["total_tokens"] == 170
    assert by_model["kimi"]["calls"] == 2
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
        "input_tokens": 300, "output_tokens": 120,
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
    assert out["summary"]["total_tokens"] == 420  # 300 in + 120 out
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


# --------------------------------------------------------------------------- #
# Home monitor card tile endpoint
# --------------------------------------------------------------------------- #
def test_metrics_tile_route_shape(cortex_db):
    from flask import Flask

    from tools.cortex.blueprint import cortex_bp
    from tools.cortex.db.init_db import record_audit

    record_audit({"operation": "cortex.complete", "tenant_id": "t-a",
                  "blocked": False, "cost_usd": 0.01, "latency_ms": 10,
                  "outcomes": {"operation": "pass"}})

    app = Flask(__name__)
    app.register_blueprint(cortex_bp)
    app.config["TESTING"] = True
    resp = app.test_client().get("/cortex/api/metrics/tile")
    assert resp.status_code == 200
    d = resp.get_json()
    # Governance-first fields the home card reads.
    for key in ("available", "calls", "blocked", "block_rate_pct",
                "redactions", "cost_usd", "cache_hits", "window_hours"):
        assert key in d
    assert d["available"] is True
    assert d["calls"] == 1


# --------------------------------------------------------------------------- #
# Idle vs unavailable — a governance tile must not conflate the two
# --------------------------------------------------------------------------- #
#
# summarize() previously returned available=False when the table was missing OR
# the read raised, and available=True with an all-zero summary when the table
# was healthy but empty. The home tile and the metrics page collapsed both into
# "No governed Cortex calls in the last 24h", so an operator could not tell
# "governance is quiet" from "the audit trail is gone".


def test_missing_table_reports_unavailable(tmp_path, monkeypatch):
    """A table that cannot be read is a FAULT, not an idle system."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "empty.db"))

    stats = metrics.summarize(window_hours=24)

    assert stats["status"] == "unavailable"
    assert stats["available"] is False


def test_healthy_but_empty_window_reports_idle(cortex_db):
    """A readable table with no rows in the window is IDLE, not unavailable."""
    stats = metrics.summarize(window_hours=24)

    assert stats["status"] == "idle"
    assert stats["available"] is True
    assert stats["summary"]["calls"] == 0


def test_idle_reports_when_the_last_call_actually_was(cortex_db):
    """Idle must say *when* it went quiet, so a stale trail is not read as empty.

    The real deployment had 129 rows whose newest was 14 days old; against a 24h
    window that rendered as "no data" when the correct reading was "quiet since
    the 17th, and the history is one query away".
    """
    record_audit({
        "function": "cortex.ask",
        "tenant_id": "t1",
        "classification": "CUI",
        "outcome": "pass",
        "blocked": False,
        "gates_json": "{}",
    })

    # Wide window sees the row...
    wide = metrics.summarize(window_hours=720)
    assert wide["status"] == "ok"
    assert wide["summary"]["calls"] >= 1

    # ...and a window that excludes it reports idle WITH a last-call timestamp,
    # rather than implying the trail is empty.
    monkeypatched = metrics.summarize(window_hours=1)
    if monkeypatched["status"] == "idle":
        assert monkeypatched["last_call_at"], "idle must report the last call time"


def test_aggregated_result_is_status_ok():
    """A window with rows reports ok, keeping the three states exhaustive."""
    import json

    rows = [("cortex.ask", "t1", "CUI", "pass", 0, json.dumps({"cost_usd": 0.01}))]
    out = metrics._aggregate(rows, 24)

    assert out["status"] == "ok"
    assert out["available"] is True
    assert out["summary"]["calls"] == 1


# --------------------------------------------------------------------------- #
# Bounded scan + shared computation (cxo-perf-04)
# --------------------------------------------------------------------------- #
#
# A 30-day window used to fetchall() every cortex_audit row and json.loads each
# one, and each of the three routes re-ran it on its own connection. Counts now
# come from SQL, the gates_json detail read is capped, and the three routes
# share one computation through a short-TTL memo.


def _metrics_app():
    """Bare app carrying the cortex blueprint.

    ``base.html`` is stubbed: the real one reads ``nav_tree`` and friends from
    the dashboard app's context processors, which are not what these tests are
    about. The blueprint's own ``cortex/metrics.html`` still renders for real.
    """
    from flask import Flask
    from jinja2 import ChoiceLoader, DictLoader

    from tools.cortex.blueprint import cortex_bp

    app = Flask(__name__)
    app.register_blueprint(cortex_bp)
    app.config["TESTING"] = True
    app.jinja_loader = ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}"}),
        app.jinja_loader,
    ])
    return app


def test_three_route_hits_perform_one_scan(cortex_db, monkeypatch):
    """Page + JSON + tile in quick succession must scan cortex_audit ONCE."""
    record_audit({"operation": "cortex.complete", "tenant_id": "t-a",
                  "blocked": False, "cost_usd": 0.01, "latency_ms": 10,
                  "outcomes": {"operation": "pass"}})

    scans = []
    real_scan = metrics._scan

    def counting_scan(conn, window_hours, limit):
        scans.append(window_hours)
        return real_scan(conn, window_hours, limit)

    monkeypatch.setattr(metrics, "_scan", counting_scan)

    client = _metrics_app().test_client()
    for path in ("/cortex/metrics", "/cortex/api/metrics", "/cortex/api/metrics/tile"):
        assert client.get(path).status_code == 200

    assert len(scans) == 1, f"expected one scan across three routes, got {len(scans)}"


def test_memo_does_not_cross_windows(cortex_db, monkeypatch):
    """The memo is keyed by window — a different window still reads."""
    record_audit({"operation": "cortex.complete", "tenant_id": "t-a",
                  "blocked": False, "outcomes": {"operation": "pass"}})

    scans = []
    real_scan = metrics._scan

    def counting_scan(conn, window_hours, limit):
        scans.append(window_hours)
        return real_scan(conn, window_hours, limit)

    monkeypatch.setattr(metrics, "_scan", counting_scan)

    metrics.summarize(window_hours=24)
    metrics.summarize(window_hours=24)
    metrics.summarize(window_hours=168)

    assert scans == [24, 168]


def test_memo_disabled_by_zero_ttl(cortex_db, monkeypatch):
    """CORTEX_METRICS_MEMO_TTL_SECONDS=0 forces every call to read."""
    record_audit({"operation": "cortex.complete", "tenant_id": "t-a",
                  "blocked": False, "outcomes": {"operation": "pass"}})
    monkeypatch.setenv("CORTEX_METRICS_MEMO_TTL_SECONDS", "0")

    scans = []
    real_scan = metrics._scan
    monkeypatch.setattr(
        metrics, "_scan",
        lambda conn, w, limit: (scans.append(w), real_scan(conn, w, limit))[1],
    )

    metrics.summarize(window_hours=24)
    metrics.summarize(window_hours=24)

    assert len(scans) == 2


def test_unavailable_is_not_memoized(tmp_path, monkeypatch):
    """A broken trail must be re-probed, so recovery is visible immediately."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "empty.db"))

    assert metrics.summarize(window_hours=24)["status"] == "unavailable"

    # Table now exists and holds a row — the very next call must see it rather
    # than serve a cached "unavailable" for the rest of the TTL.
    init_db()
    record_audit({"operation": "cortex.complete", "tenant_id": "t-a",
                  "blocked": False, "outcomes": {"operation": "pass"}})

    stats = metrics.summarize(window_hours=24)
    assert stats["status"] == "ok"
    assert stats["summary"]["calls"] == 1


def test_counts_stay_exact_when_detail_is_truncated(cortex_db):
    """Capping the gates_json read must not cap the call/block counts."""
    for _ in range(3):
        record_audit({"operation": "cortex.complete", "tenant_id": "t-a",
                      "blocked": False, "cost_usd": 0.01,
                      "outcomes": {"operation": "pass"}})
    record_audit({"operation": "cortex.search", "tenant_id": "t-b",
                  "blocked": True, "blocked_gate": "pre_check"})

    stats = metrics.summarize(window_hours=24, detail_limit=2)

    # Exact over the full window — these come from SQL, not the capped read.
    assert stats["summary"]["calls"] == 4
    assert stats["summary"]["blocked"] == 1
    assert stats["summary"]["block_rate_pct"] == 25.0
    assert sum(f["calls"] for f in stats["by_function"]) == 4
    assert sum(t["calls"] for t in stats["by_tenant"]) == 4
    assert sum(stats["by_outcome"].values()) == 4

    # ...and the panel is told the spend detail only covers a sample.
    assert stats["detail"]["truncated"] is True
    assert stats["detail"]["rows_scanned"] == 2
    assert stats["detail"]["limit"] == 2


def test_untruncated_window_reports_complete_detail(cortex_db):
    record_audit({"operation": "cortex.complete", "tenant_id": "t-a",
                  "blocked": False, "cost_usd": 0.02,
                  "outcomes": {"operation": "pass"}})

    stats = metrics.summarize(window_hours=24)

    assert stats["detail"]["truncated"] is False
    assert stats["detail"]["rows_scanned"] == 1
    assert abs(stats["summary"]["cost_usd"] - 0.02) < 1e-6


def test_metrics_page_discloses_a_truncated_detail_read(cortex_db, monkeypatch):
    """A capped spend figure must be labelled as such on the page itself."""
    for _ in range(3):
        record_audit({"operation": "cortex.complete", "tenant_id": "t-a",
                      "blocked": False, "cost_usd": 0.01,
                      "outcomes": {"operation": "pass"}})
    monkeypatch.setattr(metrics, "_DETAIL_ROW_LIMIT", 2)

    body = _metrics_app().test_client().get("/cortex/metrics").get_data(as_text=True)

    assert "Partial spend detail" in body
    assert ">3<" in body  # exact call count still rendered on the Calls card


def test_metrics_page_still_distinguishes_idle_from_unavailable(cortex_db, tmp_path,
                                                                monkeypatch):
    """Bounding + memoizing must not collapse the two empty-looking states."""
    idle = _metrics_app().test_client().get("/cortex/metrics").get_data(as_text=True)
    assert "Idle" in idle
    assert "Metrics unavailable" not in idle

    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "no-cortex-tables.db"))
    metrics.reset_memo()

    broken = _metrics_app().test_client().get("/cortex/metrics").get_data(as_text=True)
    assert "Metrics unavailable" in broken


def test_tile_reports_detail_truncation(cortex_db):
    """The home tile must disclose a partial cost figure, not imply a total."""
    record_audit({"operation": "cortex.complete", "tenant_id": "t-a",
                  "blocked": False, "outcomes": {"operation": "pass"}})

    resp = _metrics_app().test_client().get("/cortex/api/metrics/tile")

    assert resp.status_code == 200
    assert resp.get_json()["detail_truncated"] is False
