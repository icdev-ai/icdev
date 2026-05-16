# CUI // SP-CTI
"""Tests for the IL5 API route /api/v1/il5.

Acceptance criterion: The endpoint /api/v1/il5 exists, calls fetch_il5_data,
passes it to render_il5_ui, and returns the result within the enforced time
limit or a 504 error.

NIST 800-53: AU-2, AC-3, SC-28, SI-12.

Run: pytest tests/test_il5_api_routes.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Flask
from tools.il5.api_routes import il5_api, fetch_il5_data


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    """Minimal Flask app with the il5_api blueprint mounted at /api/v1/il5."""
    application = Flask(__name__)
    application.config["TESTING"] = True
    application.register_blueprint(il5_api, url_prefix="/api/v1/il5")
    return application


@pytest.fixture
def client(app):
    return app.test_client()


_SAMPLE_EVENTS = [
    {
        "id": "evt-001",
        "source_id": "src-a",
        "classification": "CUI",
        "impact_level": "IL5",
        "ingested_at": "2026-05-16T03:00:00+00:00",
        "source_published_at": "2026-05-16T02:59:45+00:00",
        "display_latency_s": 15.0,
        "sla_met": 1,
        "metadata": "{}",
    }
]


# ═══════════════════════════════════════════════════════════════════════
# TestEndpointExists
# ═══════════════════════════════════════════════════════════════════════


class TestEndpointExists:
    def test_get_il5_returns_200(self, client):
        with patch("tools.il5.api_routes.fetch_il5_data", return_value=_SAMPLE_EVENTS):
            resp = client.get("/api/v1/il5/")
        assert resp.status_code == 200

    def test_response_is_json(self, client):
        with patch("tools.il5.api_routes.fetch_il5_data", return_value=_SAMPLE_EVENTS):
            resp = client.get("/api/v1/il5/")
        assert resp.content_type == "application/json"

    def test_response_body_parseable(self, client):
        with patch("tools.il5.api_routes.fetch_il5_data", return_value=_SAMPLE_EVENTS):
            resp = client.get("/api/v1/il5/")
        body = json.loads(resp.data)
        assert isinstance(body, dict)


# ═══════════════════════════════════════════════════════════════════════
# TestFetchAndRender
# ═══════════════════════════════════════════════════════════════════════


class TestFetchAndRender:
    def test_calls_fetch_il5_data(self, client):
        with patch("tools.il5.api_routes.fetch_il5_data", return_value=[]) as mock_fetch:
            client.get("/api/v1/il5/")
        mock_fetch.assert_called_once()

    def test_passes_result_to_render_il5_ui(self, client):
        with patch("tools.il5.api_routes.fetch_il5_data", return_value=_SAMPLE_EVENTS):
            with patch("tools.il5.api_routes.render_il5_ui", return_value='{"ok":true}') as mock_render:
                client.get("/api/v1/il5/")
        mock_render.assert_called_once_with(_SAMPLE_EVENTS, component="table")

    def test_since_param_forwarded(self, client):
        with patch("tools.il5.api_routes.fetch_il5_data", return_value=[]) as mock_fetch:
            client.get("/api/v1/il5/?since=2026-05-16T00:00:00Z")
        _, kwargs = mock_fetch.call_args
        assert kwargs["since"] == "2026-05-16T00:00:00Z"

    def test_limit_param_forwarded(self, client):
        with patch("tools.il5.api_routes.fetch_il5_data", return_value=[]) as mock_fetch:
            client.get("/api/v1/il5/?limit=10")
        _, kwargs = mock_fetch.call_args
        assert kwargs["limit"] == 10

    def test_component_chart_forwarded(self, client):
        with patch("tools.il5.api_routes.fetch_il5_data", return_value=_SAMPLE_EVENTS):
            with patch("tools.il5.api_routes.render_il5_ui", return_value='{"chart":{}}') as mock_render:
                client.get("/api/v1/il5/?component=chart")
        _, kwargs = mock_render.call_args
        assert kwargs["component"] == "chart"

    def test_invalid_limit_defaults_to_25(self, client):
        with patch("tools.il5.api_routes.fetch_il5_data", return_value=[]) as mock_fetch:
            client.get("/api/v1/il5/?limit=notanumber")
        _, kwargs = mock_fetch.call_args
        assert kwargs["limit"] == 25

    def test_response_contains_classification(self, client):
        with patch("tools.il5.api_routes.fetch_il5_data", return_value=_SAMPLE_EVENTS):
            resp = client.get("/api/v1/il5/")
        body = json.loads(resp.data)
        assert body.get("classification") == "CUI"

    def test_response_contains_impact_level(self, client):
        with patch("tools.il5.api_routes.fetch_il5_data", return_value=_SAMPLE_EVENTS):
            resp = client.get("/api/v1/il5/")
        body = json.loads(resp.data)
        assert body.get("impact_level") == "IL5"

    def test_response_contains_sla_threshold(self, client):
        with patch("tools.il5.api_routes.fetch_il5_data", return_value=_SAMPLE_EVENTS):
            resp = client.get("/api/v1/il5/")
        body = json.loads(resp.data)
        assert body.get("sla_threshold_s") == 30


# ═══════════════════════════════════════════════════════════════════════
# TestSLATimeout
# ═══════════════════════════════════════════════════════════════════════


class TestSLATimeout:
    def test_timeout_returns_504(self, client):
        def _slow_fetch(**_kwargs):
            raise TimeoutError("IL5 SLA violation: ingest+display exceeded 30s")

        with patch("tools.il5.api_routes.il5_timeout_context") as mock_ctx:
            # Make the context manager raise TimeoutError on exit
            import contextlib

            @contextlib.contextmanager
            def _raising_ctx(_label):
                raise TimeoutError("IL5 SLA violation: ingest+display exceeded 30s")
                yield  # noqa: unreachable

            mock_ctx.side_effect = _raising_ctx
            resp = client.get("/api/v1/il5/")

        assert resp.status_code == 504

    def test_timeout_body_has_error_key(self, client):
        import contextlib

        @contextlib.contextmanager
        def _raising_ctx(_label):
            raise TimeoutError("SLA exceeded")
            yield  # noqa: unreachable

        with patch("tools.il5.api_routes.il5_timeout_context", side_effect=_raising_ctx):
            resp = client.get("/api/v1/il5/")

        body = json.loads(resp.data)
        assert body["error"] == "il5_sla_timeout"

    def test_timeout_body_has_classification(self, client):
        import contextlib

        @contextlib.contextmanager
        def _raising_ctx(_label):
            raise TimeoutError("SLA exceeded")
            yield  # noqa: unreachable

        with patch("tools.il5.api_routes.il5_timeout_context", side_effect=_raising_ctx):
            resp = client.get("/api/v1/il5/")

        body = json.loads(resp.data)
        assert body["classification"] == "CUI"
        assert body["impact_level"] == "IL5"


# ═══════════════════════════════════════════════════════════════════════
# TestFetchIL5DataUnit
# ═══════════════════════════════════════════════════════════════════════


class TestFetchIL5DataUnit:
    def test_returns_list(self):
        with patch("tools.il5.api_routes.get_il5_events", return_value=[]):
            result = fetch_il5_data()
        assert result == []

    def test_forwards_since(self):
        with patch("tools.il5.api_routes.get_il5_events", return_value=[]) as mock_get:
            fetch_il5_data(since="2026-05-01T00:00:00Z", limit=5)
        mock_get.assert_called_once_with(since="2026-05-01T00:00:00Z", limit=5)

    def test_defaults_limit_25(self):
        with patch("tools.il5.api_routes.get_il5_events", return_value=[]) as mock_get:
            fetch_il5_data()
        _, kwargs = mock_get.call_args
        assert kwargs["limit"] == 25
