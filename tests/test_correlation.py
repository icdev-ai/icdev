# [TEMPLATE: CUI // SP-CTI]
"""Tests for tools.resilience.correlation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import re
import threading

import pytest
from flask import Flask, g

from tools.resilience.correlation import (
    CORRELATION_HEADER,
    CorrelationLogFilter,
    clear_correlation_id,
    generate_correlation_id,
    get_correlation_id,
    register_correlation_middleware,
    set_correlation_id,
    _thread_local,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clear_thread_local():
    """Ensure thread-local state is clean before and after each test."""
    _thread_local.correlation_id = None
    yield
    _thread_local.correlation_id = None


@pytest.fixture()
def flask_app():
    """Create a minimal Flask app with correlation middleware registered."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    register_correlation_middleware(app)

    @app.route("/test")
    def test_route():
        return {"cid": g.correlation_id}

    @app.route("/echo-header")
    def echo_header():
        from flask import request
        return {"incoming": request.headers.get(CORRELATION_HEADER, "none")}

    return app


# ---------------------------------------------------------------------------
# generate_correlation_id
# ---------------------------------------------------------------------------
class TestGenerateCorrelationId:
    """Tests for generate_correlation_id()."""

    def test_returns_12_char_hex_string(self):
        cid = generate_correlation_id()
        assert len(cid) == 12
        assert re.fullmatch(r"[0-9a-f]{12}", cid), f"Not hex: {cid}"

    def test_returns_unique_values(self):
        ids = {generate_correlation_id() for _ in range(200)}
        # With 12 hex chars (48 bits), collisions in 200 samples are astronomically unlikely
        assert len(ids) == 200


# ---------------------------------------------------------------------------
# Thread-local set / get / clear
# ---------------------------------------------------------------------------
class TestThreadLocal:
    """Tests for set_correlation_id / get_correlation_id / clear_correlation_id."""

    def test_set_and_get(self):
        set_correlation_id("abc123def456")
        assert get_correlation_id() == "abc123def456"

    def test_get_returns_none_when_nothing_set(self):
        assert get_correlation_id() is None

    def test_clear_removes_id(self):
        set_correlation_id("will-be-cleared")
        clear_correlation_id()
        assert get_correlation_id() is None

    def test_thread_isolation(self):
        """IDs set in one thread must not leak into another."""
        results = {}
        barrier = threading.Barrier(2)

        def worker(name, cid):
            set_correlation_id(cid)
            barrier.wait()  # ensure both threads have set their value
            results[name] = get_correlation_id()

        t1 = threading.Thread(target=worker, args=("t1", "aaa"))
        t2 = threading.Thread(target=worker, args=("t2", "bbb"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results["t1"] == "aaa"
        assert results["t2"] == "bbb"


# ---------------------------------------------------------------------------
# Flask middleware
# ---------------------------------------------------------------------------
class TestCorrelationMiddleware:
    """Tests for register_correlation_middleware on a Flask app."""

    def test_registers_hooks(self, flask_app):
        # before_request and after_request each add one function;
        # teardown_request adds one function. Just verify the app runs.
        client = flask_app.test_client()
        resp = client.get("/test")
        assert resp.status_code == 200

    def test_generates_new_id_when_no_header(self, flask_app):
        client = flask_app.test_client()
        resp = client.get("/test")
        data = resp.get_json()
        cid = data["cid"]
        assert cid is not None
        assert len(cid) == 12
        assert re.fullmatch(r"[0-9a-f]{12}", cid)

    def test_uses_incoming_header(self, flask_app):
        client = flask_app.test_client()
        resp = client.get(
            "/test",
            headers={CORRELATION_HEADER: "custom123456"},
        )
        data = resp.get_json()
        assert data["cid"] == "custom123456"

    def test_adds_header_to_response(self, flask_app):
        client = flask_app.test_client()
        resp = client.get("/test")
        assert CORRELATION_HEADER in resp.headers
        resp_cid = resp.headers[CORRELATION_HEADER]
        assert len(resp_cid) == 12

    def test_response_header_matches_request_generated_id(self, flask_app):
        client = flask_app.test_client()
        resp = client.get("/test")
        body_cid = resp.get_json()["cid"]
        header_cid = resp.headers[CORRELATION_HEADER]
        assert body_cid == header_cid

    def test_preserves_incoming_header_in_response(self, flask_app):
        client = flask_app.test_client()
        resp = client.get(
            "/test",
            headers={CORRELATION_HEADER: "inbound12345"},
        )
        assert resp.headers[CORRELATION_HEADER] == "inbound12345"

    def test_clears_thread_local_on_teardown(self, flask_app):
        client = flask_app.test_client()
        client.get("/test")
        # After the request completes, thread-local should be cleared
        assert _thread_local.correlation_id is None


# ---------------------------------------------------------------------------
# CorrelationLogFilter
# ---------------------------------------------------------------------------
class TestCorrelationLogFilter:
    """Tests for the CorrelationLogFilter logging.Filter."""

    def test_sets_correlation_id_on_record(self):
        set_correlation_id("log-cid-abc1")
        log_filter = CorrelationLogFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        result = log_filter.filter(record)
        assert result is True
        assert record.correlation_id == "log-cid-abc1"

    def test_sets_dash_when_no_id(self):
        # Thread-local is cleared by autouse fixture
        log_filter = CorrelationLogFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        log_filter.filter(record)
        assert record.correlation_id == "-"
