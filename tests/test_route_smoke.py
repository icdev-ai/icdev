# CUI // SP-CTI
"""Regression tests for tools.testing.route_smoke API endpoint checks.

These pin the three false failures the route smoker used to report against a
perfectly healthy dashboard:

  1. A POST-only route (``/api/iqe-query``) answers 405 to the GET probe. That
     still proves the route is registered, so ``skip_405`` must pass it.
  2. ``_smoke_api_endpoint`` truncated the body at 64KB before ``json.loads``,
     which splits a large-but-valid document mid-string and reports a bogus
     "Unterminated string". ``/api/kanban/tasks`` is ~580KB, so it failed every
     run. The read must be unbounded.
  3. Two collection routes were probed at paths that do not exist — the real
     ones live under their blueprint's ``url_prefix``.

The endpoints are exercised against a real loopback HTTP server rather than a
mocked ``urllib``, because the truncation bug lived in the socket read itself
and a mock that returns a whole string cannot reproduce it.
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tools.testing.route_smoke import (
    API_ENDPOINTS,
    _routes_for_changed_files,
    _smoke_api_endpoint,
    run_api_smoke,
    run_smoke,
)

# Comfortably past the old 65536-byte read cap, as one unbroken string so a
# truncated read lands inside it and json.loads raises "Unterminated string".
_BIG_STRING = "x" * 200_000


class _SmokeHandler(BaseHTTPRequestHandler):
    """Minimal stand-in for the dashboard's API surface."""

    def _send(self, status: int, body: str, content_type: str = "application/json") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/health":
            self._send(200, '{"status": "healthy"}')
        elif self.path == "/api/big":
            self._send(200, json.dumps({"tasks": [{"blob": _BIG_STRING}]}))
        elif self.path == "/api/small":
            self._send(200, '{"ok": true}')
        elif self.path == "/api/post-only":
            self._send(405, "Method Not Allowed", content_type="text/plain")
        elif self.path == "/api/absent":
            self._send(404, "gone", content_type="text/plain")
        elif self.path == "/api/html":
            self._send(200, "<html><body>hello</body></html>", content_type="text/html")
        else:
            self._send(404, "gone", content_type="text/plain")

    def log_message(self, *args) -> None:  # silence per-request stderr noise
        return


@pytest.fixture(scope="module")
def smoke_base():
    """Run a throwaway HTTP server and yield its base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SmokeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _free_port() -> int:
    """A port with nothing listening on it, for the server-down paths."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── Fix 1: 405 on a POST-only route ──────────────────────────────────────────

def test_405_passes_when_skip_405_is_set(smoke_base):
    result = _smoke_api_endpoint(
        smoke_base,
        {"route": "/api/post-only", "expect_json": False, "skip_404": True, "skip_405": True},
    )
    assert result["status"] == 405
    assert result["ok"] is True
    assert result["error"] is None


def test_405_still_fails_without_skip_405(smoke_base):
    """skip_405 must be opt-in — an unexpected 405 is still a real failure."""
    result = _smoke_api_endpoint(smoke_base, {"route": "/api/post-only", "expect_json": False})
    assert result["status"] == 405
    assert result["ok"] is False


def test_404_passes_only_when_skip_404_is_set(smoke_base):
    assert _smoke_api_endpoint(
        smoke_base, {"route": "/api/absent", "expect_json": False, "skip_404": True}
    )["ok"] is True
    assert _smoke_api_endpoint(
        smoke_base, {"route": "/api/absent", "expect_json": False}
    )["ok"] is False


# ── Fix 2: full-body read ────────────────────────────────────────────────────

def test_large_json_body_is_read_in_full(smoke_base):
    """A >64KB JSON document must parse — the old truncated read failed here."""
    result = _smoke_api_endpoint(smoke_base, {"route": "/api/big", "expect_json": True})
    assert result["status"] == 200
    assert result["ok"] is True, result["error"]
    assert result["error"] is None


def test_small_json_body_still_passes(smoke_base):
    result = _smoke_api_endpoint(smoke_base, {"route": "/api/small", "expect_json": True})
    assert result["ok"] is True, result["error"]


def test_non_json_body_fails_when_json_expected(smoke_base):
    """The full read must not turn the JSON check into a rubber stamp."""
    result = _smoke_api_endpoint(smoke_base, {"route": "/api/html", "expect_json": True})
    assert result["ok"] is False
    assert "Non-JSON response" in str(result["error"])


# ── Fix 3: the corrected endpoint table ──────────────────────────────────────

def test_api_endpoints_use_prefixed_collection_routes():
    routes = {str(ep["route"]) for ep in API_ENDPOINTS}
    assert "/api/proposals/opportunities" in routes
    assert "/api/govcon/sam/opportunities" in routes
    # The bare paths never existed — probing them reported a healthy app as broken.
    assert "/api/proposals" not in routes
    assert "/api/govcon/opportunities" not in routes


def test_iqe_query_endpoint_tolerates_method_not_allowed():
    iqe = [ep for ep in API_ENDPOINTS if str(ep["route"]) == "/api/iqe-query"]
    assert iqe, "/api/iqe-query should still be smoked"
    assert iqe[0].get("skip_405") is True


def test_every_api_endpoint_declares_a_route():
    for ep in API_ENDPOINTS:
        assert str(ep["route"]).startswith("/")


# ── Graceful skip when no server is listening ────────────────────────────────

def test_api_smoke_skips_when_server_is_down():
    passed, results = run_api_smoke(base=f"http://127.0.0.1:{_free_port()}", verbose=False)
    assert passed is True
    assert results == []


def test_route_smoke_skips_when_server_is_down():
    passed, results = run_smoke(["/"], base=f"http://127.0.0.1:{_free_port()}", verbose=False)
    assert passed is True
    assert results == []


def test_api_smoke_reports_failures_against_a_live_server(smoke_base):
    passed, results = run_api_smoke(
        endpoints=[
            {"route": "/api/small", "expect_json": True},
            {"route": "/api/html", "expect_json": True},
        ],
        base=smoke_base,
        verbose=False,
    )
    assert passed is False
    assert [r["ok"] for r in results] == [True, False]


# ── Changed-file → route mapping ─────────────────────────────────────────────

def test_blueprint_change_triggers_full_smoke():
    assert len(_routes_for_changed_files(["tools/govcon/blueprint.py"])) > 1


def test_unrelated_change_maps_to_no_routes():
    assert _routes_for_changed_files(["README.md"]) == []
# CUI // SP-CTI
