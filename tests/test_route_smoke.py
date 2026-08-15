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
    _smoke_route,
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
        elif self.path == "/api/prose":
            # A real kanban payload: user-authored text that *discusses* errors.
            self._send(200, json.dumps({"tasks": [
                {"description": "The ImportError is swallowed by except Exception"},
                {"description": "guard catches ImportError but not AttributeError"},
                {"description": "the query dies with no such table: widgets"},
            ]}))
        elif self.path == "/api/empty-state":
            self._send(200, json.dumps({"error": "No scan data found", "has_data": False}))
        elif self.path == "/api/broken":
            self._send(200, json.dumps({"error": "no such table: sbom_records"}))
        elif self.path == "/page-traceback":
            self._send(
                200,
                "<html><body>Traceback (most recent call last)</body></html>",
                content_type="text/html",
            )
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


# ── Fix 4: error signals are for pages, not for API payloads ─────────────────
#
# ERROR_SIGNALS used to be substring-scanned across the whole body of every
# response. /api/kanban/tasks serves kanban task descriptions, two of which
# discuss a swallowed ImportError — so the gate failed permanently on its own
# content. The signals now apply to rendered pages, and to the top-level
# "error"/"traceback" field of a JSON document, but never to nested payload text.

def test_api_payload_discussing_errors_is_not_a_failure(smoke_base):
    """Task descriptions that mention ImportError are data, not defects."""
    result = _smoke_api_endpoint(smoke_base, {"route": "/api/prose", "expect_json": True})
    assert result["ok"] is True, result["error"]
    assert result["error_signal"] is None


def test_semantic_empty_state_is_not_a_failure(smoke_base):
    """{"error": "No scan data found"} means the route served fine with no data."""
    result = _smoke_api_endpoint(smoke_base, {"route": "/api/empty-state", "expect_json": True})
    assert result["ok"] is True, result["error"]


def test_json_error_envelope_naming_a_signal_still_fails(smoke_base):
    """The narrowing must not become a rubber stamp: a real crash still trips."""
    result = _smoke_api_endpoint(smoke_base, {"route": "/api/broken", "expect_json": True})
    assert result["ok"] is False
    assert result["error_signal"] == "json_error"
    assert "no such table" in str(result["error"])


def test_html_page_leaking_a_traceback_still_fails(smoke_base):
    """Substring scanning is retained where it belongs — rendered pages."""
    result = _smoke_route(smoke_base, "/page-traceback")
    assert result["ok"] is False
    assert result["error_signal"] == "Traceback (most recent call last)"


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


# ── fli-smk-01: the bound must be visible, and a timeout must not read as a pass ──
#
# SURVEYED 2026-08-15 before changing anything. _routes_for_changed_files is
# all-or-nothing: any blueprint.py / app.py / templates/ path returns the FULL
# 79-route nav list, anything else usually returns zero. The full list takes
# ~212s against a warm dashboard (2-3s per page). The hook's timeout was 120s and
# its timeout branch returned True. So every run that actually reached the
# subprocess died on the timeout and was reported as a pass — route smoke had
# never gated a commit, while printing as though it might.


def _run_cli(base, *args):
    """Run route_smoke.py --json exactly as the hook does, against a LIVE server.

    A live base is required, not incidental: run_smoke short-circuits to zero
    results when the server is unreachable, so pointing this at a dead port would
    make `total` 0 in every case and the coverage assertions below would pass
    without the cap doing anything.
    """
    import subprocess as _sp
    import sys as _sys
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1]
    proc = _sp.run(
        [_sys.executable, str(root / "tools" / "testing" / "route_smoke.py"),
         "--json", "--timeout", "5", "--base", base, *args],
        capture_output=True, text=True, cwd=str(root), timeout=300,
    )
    return json.loads(proc.stdout)


def test_max_routes_names_every_route_it_skipped(smoke_base):
    """A cap you cannot see reads as "covered everything"."""
    report = _run_cli(smoke_base, "--routes", "/a,/b,/c,/d,/e", "--max-routes", "2")

    assert report["total"] == 2, "only the bounded number may be attempted"
    assert report["skipped_by_cap"] == ["/c", "/d", "/e"], (
        "the dropped routes must be NAMED, not counted — a number cannot tell a "
        "reader which pages went unchecked"
    )


def test_no_cap_means_nothing_is_silently_dropped(smoke_base):
    report = _run_cli(smoke_base, "--routes", "/a,/b,/c")
    assert report["total"] == 3
    assert report["skipped_by_cap"] == []


def test_a_cap_larger_than_the_route_set_drops_nothing(smoke_base):
    report = _run_cli(smoke_base, "--routes", "/a,/b", "--max-routes", "50")
    assert report["total"] == 2
    assert report["skipped_by_cap"] == []


def test_the_hook_reports_a_timeout_as_NOT_RUN_rather_than_OK(monkeypatch, capsys):
    """The defect itself: `return True` on TimeoutExpired, printed as a warning.

    The commit is still allowed — route smoke needs a live dashboard and runs
    nowhere else, so blocking on it would earn the hook a --no-verify — but the
    output must not let a reader believe anything was verified.
    """
    import subprocess as _sp

    from tools.testing import pre_commit_check as pcc

    from tools.testing import route_smoke as rs

    monkeypatch.setattr(pcc, "_is_dashboard_change", lambda files: True)
    # Both guards the hook consults before spawning: routes affected, server up.
    # It imports them from route_smoke inside the function, so the patch lands
    # there rather than on pre_commit_check.
    monkeypatch.setattr(rs, "_routes_for_changed_files", lambda files: ["/x", "/y"])
    monkeypatch.setattr(rs, "_server_up", lambda *a, **kw: True)
    monkeypatch.setattr(
        pcc.subprocess, "run",
        lambda *a, **kw: (_ for _ in ()).throw(_sp.TimeoutExpired(cmd="route_smoke", timeout=120)))

    allowed = pcc._run_route_smoke(["tools/dashboard/templates/base.html"])
    out = capsys.readouterr().out

    assert allowed is True, "a slow gate must not block the commit outright"
    assert "DID NOT RUN" in out, (
        f"a timeout must not read as a pass; got {out!r}. This is the `|| true` "
        "shape: nominally enforcing, actually inert, nothing red."
    )
    assert "OK" not in out.replace("DID NOT RUN", "")
    assert "UNCHECKED" in out


class _DummySock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
