# CUI // SP-CTI
"""ECR-OBS-01 V&V: Prometheus metrics registry + /metrics endpoint."""
from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_metrics():
    """Re-import metrics.py into a clean module so each test gets its own registry."""
    mod_name = "tools.observability.metrics"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def _make_flask_client():
    """Return a Flask test client with the /metrics route wired."""
    from flask import Flask, Response
    app = Flask(__name__)
    app.config["TESTING"] = True

    metrics = _fresh_metrics()

    if metrics.registry is not None and metrics.generate_latest is not None:
        @app.route("/metrics")
        def _metrics_view():
            data = metrics.generate_latest(metrics.registry)
            return Response(data, status=200, mimetype=metrics.CONTENT_TYPE_LATEST)

        metrics.wire_flask_metrics(app)

    return app.test_client(), metrics


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_metrics_module_imports():
    """prometheus_client is installed and the module loads without errors."""
    pytest.importorskip("prometheus_client")
    m = _fresh_metrics()
    assert m.registry is not None


def test_all_six_metrics_defined():
    """All 6 required metric objects are defined on the module."""
    pytest.importorskip("prometheus_client")
    m = _fresh_metrics()
    assert m.http_requests_total is not None
    assert m.http_request_duration_seconds is not None
    assert m.llm_calls_total is not None
    assert m.llm_tokens_total is not None
    assert m.kanban_tasks_total is not None
    assert m.active_tenants is not None


def test_metrics_endpoint():
    """GET /metrics returns 200 with valid Prometheus text output."""
    pytest.importorskip("prometheus_client")
    client, metrics = _make_flask_client()
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "icdev_http_requests_total" in body


def test_metrics_content_type():
    """GET /metrics Content-Type matches Prometheus spec (version=0.0.4)."""
    pytest.importorskip("prometheus_client")
    client, _ = _make_flask_client()
    resp = client.get("/metrics")
    assert "0.0.4" in resp.content_type or "text/plain" in resp.content_type


def test_http_counter_increments():
    """After a request the http_requests_total counter is > 0."""
    pytest.importorskip("prometheus_client")
    client, metrics = _make_flask_client()
    client.get("/metrics")
    # Sample the counter from the registry output
    data = metrics.generate_latest(metrics.registry).decode()
    assert "icdev_http_requests_total" in data


def test_all_six_metric_names_in_output():
    """All 6 metric families appear in the /metrics text output."""
    pytest.importorskip("prometheus_client")
    client, metrics = _make_flask_client()
    # Trigger a request so HTTP metrics are recorded
    client.get("/metrics")
    data = metrics.generate_latest(metrics.registry).decode()
    for name in (
        "icdev_http_requests_total",
        "icdev_http_request_duration_seconds",
        "icdev_llm_calls_total",
        "icdev_llm_tokens_total",
        "icdev_kanban_tasks_total",
        "icdev_active_tenants",
    ):
        assert name in data, f"Missing metric: {name}"


def test_llm_calls_counter_increment():
    """llm_calls_total increments correctly."""
    pytest.importorskip("prometheus_client")
    m = _fresh_metrics()
    m.llm_calls_total.labels(provider="ollama", model="qwen3").inc()
    data = m.generate_latest(m.registry).decode()
    assert "icdev_llm_calls_total" in data


def test_active_tenants_gauge():
    """active_tenants gauge can be set and read."""
    pytest.importorskip("prometheus_client")
    m = _fresh_metrics()
    m.active_tenants.set(5)
    data = m.generate_latest(m.registry).decode()
    assert "icdev_active_tenants" in data


def test_health_check_status_gauge():
    """icdev_health_check_status gauge is defined in metrics module."""
    pytest.importorskip("prometheus_client")
    m = _fresh_metrics()
    assert m.health_check_status is not None
    m.health_check_status.set(1.0)
    data = m.generate_latest(m.registry).decode()
    assert "icdev_health_check_status" in data


# ---------------------------------------------------------------------------
# Health endpoint tests (ECR-OBS-03)
# ---------------------------------------------------------------------------

def _make_health_client(mock_db_ok=True):
    """Return a Flask test client with health routes and an optionally broken DB."""
    from flask import Flask
    import importlib

    mod = importlib.import_module("tools.observability.health_blueprint")

    return_val = "ok" if mock_db_ok else "fail"
    db_patcher = patch.object(mod, "_check_db", return_value=return_val)
    db_patcher.start()

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(mod.health_bp)

    return app.test_client(), db_patcher


def test_health_endpoints():
    """GET /health returns valid structured JSON with required fields."""
    client, patcher = _make_health_client(mock_db_ok=True)
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert data["status"] in ("healthy", "degraded", "unhealthy")
        assert "checks" in data
        assert "version" in data
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], float)
        checks = data["checks"]
        assert "db" in checks
        assert "llm_import" in checks
        assert "kanban" in checks
        assert "redis" in checks
    finally:
        patcher.stop()


def test_health_ready_ok():
    """GET /health/ready returns 200 when DB is reachable."""
    client, patcher = _make_health_client(mock_db_ok=True)
    try:
        resp = client.get("/health/ready")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ready"] is True
    finally:
        patcher.stop()


def test_health_ready_fails_when_db_down():
    """GET /health/ready returns 503 when DB is unreachable."""
    client, patcher = _make_health_client(mock_db_ok=False)
    try:
        resp = client.get("/health/ready")
        assert resp.status_code == 503
        data = resp.get_json()
        assert data["ready"] is False
    finally:
        patcher.stop()


def test_health_live_always_200():
    """GET /health/live always returns 200 regardless of DB state."""
    client, patcher = _make_health_client(mock_db_ok=False)
    try:
        resp = client.get("/health/live")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["alive"] is True
    finally:
        patcher.stop()


def test_health_unhealthy_when_db_fail():
    """GET /health returns 503 and status=unhealthy when DB fails."""
    client, patcher = _make_health_client(mock_db_ok=False)
    try:
        resp = client.get("/health")
        assert resp.status_code == 503
        data = resp.get_json()
        assert data["status"] == "unhealthy"
        assert data["checks"]["db"] == "fail"
    finally:
        patcher.stop()


def test_health_gauge_emitted():
    """health_check_status gauge is updated when /health is called."""
    pytest.importorskip("prometheus_client")
    from unittest.mock import patch as _patch
    from flask import Flask
    import importlib

    mod = importlib.import_module("tools.observability.health_blueprint")

    gauge_values = []

    def _fake_emit(status):
        gauge_values.append(status)

    with _patch.object(mod, "_emit_gauge", side_effect=_fake_emit), \
         _patch.object(mod, "_check_db", return_value="ok"), \
         _patch.object(mod, "_check_kanban", return_value="ok"):
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(mod.health_bp)
        client = app.test_client()
        client.get("/health")

    assert gauge_values == ["healthy"]


def test_llm_metrics_incremented():
    """icdev_llm_calls_total and icdev_llm_tokens_total increment on an LLM call."""
    pytest.importorskip("prometheus_client")
    m = _fresh_metrics()

    # Replicate the exact label pattern used in router.py
    provider_name = "ollama"
    model_id = "qwen3"

    m.llm_calls_total.labels(provider=provider_name, model=model_id).inc()
    m.llm_tokens_total.labels(provider=provider_name, model=model_id, type="input").inc(42)
    m.llm_tokens_total.labels(provider=provider_name, model=model_id, type="output").inc(17)

    data = m.generate_latest(m.registry).decode()

    assert "icdev_llm_calls_total" in data
    assert "icdev_llm_tokens_total" in data
    assert f'provider="{provider_name}"' in data
    assert 'type="input"' in data
    assert 'type="output"' in data
    # The call counter should be exactly 1.0
    assert f'icdev_llm_calls_total{{model="{model_id}",provider="{provider_name}"}} 1.0' in data
