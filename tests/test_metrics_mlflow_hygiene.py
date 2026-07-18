#!/usr/bin/env python3
# CUI // SP-CTI
"""Hygiene tests for observability metrics wiring + MLflow export (obx-trc-04).

Two defects guarded here:

  1. Prometheus metrics wiring was invoked twice on the Flask app
     (tools/dashboard/app.py called _wire_metrics(app) before and after the
     /metrics route). Each call registered its own before/after_request hooks,
     so icdev_http_requests_total incremented twice per request and the latency
     histogram double-observed — corrupting SLO math. wire_flask_metrics is now
     idempotent via a sentinel on app.extensions.

  2. MLflowExporter._read_unexported_spans was an unbounded
     "SELECT * FROM otel_spans ORDER BY start_time" with no export tracking, so
     every export_pending() re-created MLflow runs for the same spans. A
     persisted high-watermark (mlflow_export_state) now filters
     WHERE start_time > watermark, making repeat exports idempotent.

Shim-aware patching: the exporter does `from tools.db.storage import
get_connection`, so the bound name lives in the module's own namespace. We
patch that name on the exact module object via importlib + monkeypatch.setattr.
`tools.*` and `icdev.tools.*` are distinct module objects; the code-under-test
imports the `tools.*` variant, so we patch that one.
"""

import importlib
import sqlite3

import pytest

from tools.db.storage import StorageConnection

MLFLOW_MOD = "tools.observability.mlflow_exporter"


# ---------------------------------------------------------------------------
# (1) idempotent Flask metrics wiring
# ---------------------------------------------------------------------------


def _counter_total(registry) -> float:
    """Sum every icdev_http_requests_total counter sample in the registry."""
    total = 0.0
    for metric in registry.collect():
        for sample in metric.samples:
            if sample.name.startswith("icdev_http_requests_total") and sample.name.endswith(
                "_total"
            ):
                total += sample.value
    return total


def test_wire_flask_metrics_is_idempotent_hooks_registered_once():
    """Wiring twice registers the after_request hook exactly once."""
    flask = pytest.importorskip("flask")
    metrics = importlib.import_module("tools.observability.metrics")
    if not metrics._PROMETHEUS_AVAILABLE:
        pytest.skip("prometheus_client not installed")

    app = flask.Flask(__name__)

    before_zero = len(app.after_request_funcs.get(None, []))
    metrics.wire_flask_metrics(app)
    metrics.wire_flask_metrics(app)  # second call must be a no-op
    after = len(app.after_request_funcs.get(None, []))

    assert after - before_zero == 1, (
        "wire_flask_metrics registered the after_request hook more than once — "
        "double-counting would corrupt SLO math"
    )
    assert app.extensions.get("icdev_metrics_wired") is True


def test_wire_flask_metrics_twice_counts_request_once():
    """A single request increments icdev_http_requests_total by exactly one."""
    flask = pytest.importorskip("flask")
    metrics = importlib.import_module("tools.observability.metrics")
    if not metrics._PROMETHEUS_AVAILABLE:
        pytest.skip("prometheus_client not installed")

    app = flask.Flask(__name__)

    @app.route("/ping")
    def _ping():
        return "pong"

    metrics.wire_flask_metrics(app)
    metrics.wire_flask_metrics(app)  # redundant wiring must not double-count

    client = app.test_client()

    before = _counter_total(metrics.registry)
    resp = client.get("/ping")
    assert resp.status_code == 200
    after = _counter_total(metrics.registry)

    assert after - before == 1.0, (
        f"request counter moved by {after - before}, expected exactly 1 — "
        "hooks fired more than once"
    )


# ---------------------------------------------------------------------------
# MLflow export watermark helpers
# ---------------------------------------------------------------------------


def _make_shared_conn():
    """In-memory SQLite conn with otel_spans (watermark table is auto-created)."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE otel_spans (
            id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            parent_span_id TEXT,
            name TEXT NOT NULL,
            kind TEXT DEFAULT 'INTERNAL',
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_ms INTEGER DEFAULT 0,
            status_code TEXT DEFAULT 'UNSET',
            status_message TEXT,
            attributes TEXT,
            events TEXT,
            agent_id TEXT,
            project_id TEXT,
            classification TEXT DEFAULT 'CUI'
        );
        """
    )
    conn.commit()
    return conn


def _seed_spans(conn, specs):
    """specs: list of (id, trace_id, start_time)."""
    for span_id, trace_id, start_time in specs:
        conn.execute(
            "INSERT INTO otel_spans (id, trace_id, name, start_time, attributes, events) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (span_id, trace_id, f"op-{span_id}", start_time, "{}", "[]"),
        )
    conn.commit()


def _connection_factory(shared):
    """get_connection replacement backed by `shared` (close() is a no-op)."""

    class _Persistent(StorageConnection):
        def close(self):  # keep the shared in-memory DB alive
            pass

    def factory(*_args, **_kwargs):
        return _Persistent(shared, "sqlite")

    return factory


class _FakeRun:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeMlflow:
    """Minimal MLflow seam that records how many runs were started."""

    def __init__(self):
        self.runs_started = 0

    def set_tracking_uri(self, uri):
        pass

    def set_experiment(self, name):
        pass

    def start_run(self, run_name=None):
        self.runs_started += 1
        return _FakeRun()

    def log_param(self, *a, **k):
        pass

    def log_metric(self, *a, **k):
        pass


def _wire_exporter(monkeypatch, shared, fake_mlflow):
    """Patch the mlflow_exporter module seams and return the module."""
    mod = importlib.import_module(MLFLOW_MOD)
    monkeypatch.setattr(mod, "get_connection", _connection_factory(shared))
    monkeypatch.setattr(mod, "HAS_MLFLOW", True)
    monkeypatch.setattr(mod, "mlflow", fake_mlflow)
    return mod


# ---------------------------------------------------------------------------
# (2) export_pending is idempotent — second call exports zero
# ---------------------------------------------------------------------------


def test_export_pending_twice_second_exports_zero(monkeypatch):
    """Second export_pending() re-reads nothing (watermark advanced)."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")  # skip .db file gate
    shared = _make_shared_conn()
    _seed_spans(
        shared,
        [
            ("s1", "t1", "2026-07-18T00:00:01"),
            ("s2", "t1", "2026-07-18T00:00:02"),
            ("s3", "t2", "2026-07-18T00:00:03"),
        ],
    )
    fake = _FakeMlflow()
    mod = _wire_exporter(monkeypatch, shared, fake)

    exporter = mod.MLflowExporter(tracking_uri="http://fake:5001", db_path=None)

    first = exporter.export_pending()
    assert first["exported"] == 3, first
    assert fake.runs_started == 2, "one run per trace expected"

    fake.runs_started = 0
    second = exporter.export_pending()
    assert second["exported"] == 0, second
    assert fake.runs_started == 0, "second export must not re-create MLflow runs"

    shared.close()


# ---------------------------------------------------------------------------
# (3) watermark survives across exporter instances (state table)
# ---------------------------------------------------------------------------


def test_watermark_survives_across_exporter_instances(monkeypatch):
    """A fresh exporter instance reads the persisted watermark and skips spans."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
    shared = _make_shared_conn()
    _seed_spans(
        shared,
        [
            ("s1", "t1", "2026-07-18T00:00:01"),
            ("s2", "t2", "2026-07-18T00:00:02"),
        ],
    )
    fake = _FakeMlflow()
    mod = _wire_exporter(monkeypatch, shared, fake)

    first = mod.MLflowExporter(tracking_uri="http://fake:5001")
    assert first.export_pending()["exported"] == 2

    # Watermark row persisted in the shared DB.
    wm_row = shared.execute(
        "SELECT last_start_time FROM mlflow_export_state WHERE id = ?",
        ("otel_spans",),
    ).fetchone()
    assert wm_row is not None
    assert wm_row["last_start_time"] == "2026-07-18T00:00:02"

    # A brand-new instance must honour the persisted watermark.
    second = mod.MLflowExporter(tracking_uri="http://fake:5001")
    assert second._get_watermark() == "2026-07-18T00:00:02"
    fake.runs_started = 0
    assert second.export_pending()["exported"] == 0
    assert fake.runs_started == 0

    # A new span past the watermark is picked up by the next pass.
    _seed_spans(shared, [("s3", "t3", "2026-07-18T00:00:05")])
    third = second.export_pending()
    assert third["exported"] == 1
    assert fake.runs_started == 1

    shared.close()
