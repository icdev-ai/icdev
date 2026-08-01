# CUI // SP-CTI
"""Cortex client SDK — never-raise / degradation contract (ctx-expose-06).

Runs a real local HTTP server so urllib exercises actual sockets:
  * 2xx JSON -> parsed dict
  * 4xx JSON (validation / blocked / unanswerable) -> body returned (an answer)
  * 5xx / refused connection / garbage JSON -> None (unavailable), never raises
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tools.cortex.client import CortexClient


class _Handler(BaseHTTPRequestHandler):
    """Routes chosen by path suffix so one server covers every scenario."""

    def _reply(self, status: int, body: str, content_type: str = "application/json"):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        if self.path.endswith("/health"):
            self._reply(200, json.dumps({"ok": True, "status": "healthy"}))
        else:
            self._reply(404, json.dumps({"error": "not found"}))

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        data = json.loads(raw) if raw else {}
        query = data.get("query") or data.get("question") or data.get("prompt") or ""

        if query == "blocked":
            self._reply(403, json.dumps({
                "error": "injection detected", "blocked": True,
                "governance": {"blocked": True, "blocked_reason": "injection"},
            }))
        elif query == "invalid":
            self._reply(400, json.dumps({"error": "'query' is required"}))
        elif query == "boom":
            self._reply(500, json.dumps({"error": "internal error"}))
        elif query == "garbage":
            self._reply(200, "this is not json{{{")
        else:
            self._reply(200, json.dumps({
                "results": [{"content": "hit", "score": 0.9, "backend": "rag"}],
                "count": 1,
                "auth": self.headers.get("Authorization", ""),
            }))

    def log_message(self, *args):  # silence test output
        pass


@pytest.fixture(scope="module")
def server_url():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def client(server_url):
    return CortexClient(base_url=server_url, api_key="icdev_ctx_testkey")


def test_success_returns_parsed_dict_with_bearer(client):
    result = client.search("hello")
    assert result is not None
    assert result["count"] == 1
    assert result["auth"] == "Bearer icdev_ctx_testkey"


def test_blocked_403_body_returned(client):
    result = client.search("blocked")
    assert result is not None
    assert result["blocked"] is True
    assert result["governance"]["blocked_reason"] == "injection"
    assert result["http_status"] == 403


def test_validation_400_body_returned(client):
    result = client.search("invalid")
    assert result is not None
    assert "required" in result["error"]
    assert result["http_status"] == 400


def test_5xx_returns_none(client):
    assert client.search("boom") is None


def test_garbage_json_returns_none(client):
    assert client.search("garbage") is None


def test_connection_refused_returns_none():
    client = CortexClient(base_url="http://127.0.0.1:9", api_key="icdev_ctx_x", timeout=2)
    assert client.search("hello") is None
    assert client.is_available() is False


def test_disabled_or_unconfigured_returns_none():
    assert CortexClient(base_url="", api_key="x").search("q") is None
    client = CortexClient(base_url="http://127.0.0.1:9", api_key="x", enabled=False)
    assert client.ask("q") is None


def test_health_and_is_available(client):
    health = client.health()
    assert health["ok"] is True
    assert client.is_available() is True


def test_never_raises_on_unserializable_payload(client):
    # json.dumps failure inside the client must degrade to None, not raise.
    assert client.extract("text", {"bad": object()}) is None
