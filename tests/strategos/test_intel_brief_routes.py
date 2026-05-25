#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Strategos Intel Brief routes — GET /strategos/intel-brief
and POST /api/strategos/intel-brief/run.

RED phase: these tests are written before the routes exist.
GREEN phase: routes added to tools/strategos/blueprint.py.

Page-route tests patch flask.render_template to avoid the full app context
processor dependency (ROLE_VIEWS etc.) required by base.html.  API route tests
run without patching render_template since they return JSON directly.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BRIEF_ROW = {
    "id": "test-brief-001",
    "theater": "global",
    "sio_composite_score": 42.5,
    "iw_triggered": 0,
    "threat_tier": "MEDIUM",
    "signal_count_24h": 17,
    "conflict_event_count": 3,
    "signal_velocity": 5.2,
    "p_war_posterior": 0.12,
    "goldstein_avg": -1.5,
    "dti_score": 36.0,
    "forecast_24h_json": json.dumps({"wri": 44.0, "tier": "MEDIUM", "p_war": 0.13, "trend": "stable", "confidence": 0.72}),
    "forecast_72h_json": json.dumps({"wri": 47.0, "tier": "MEDIUM", "p_war": 0.15, "trend": "escalating", "confidence": 0.65}),
    "forecast_7d_json": json.dumps({"wri": 50.0, "tier": "HIGH", "p_war": 0.18, "trend": "escalating", "confidence": 0.55}),
    "narrative_md": "## Summary\n\nTest narrative.",
    "generated_at": "2026-05-17T08:00:00+00:00",
    "classification": "CUI // SP-CTI",
}

BRIEF_GENERATE_RESULT = {
    "brief_id": "test-brief-gen-001",
    "theater": "global",
    "classification": "CUI // SP-CTI",
    "sio_composite_score": 4.2,
    "iw_triggered": False,
    "threat_tier": "MEDIUM",
    "threat_tier_color": "#d97706",
    "signal_count_24h": 20,
    "conflict_event_count": 5,
    "signal_velocity": 0.08,
    "p_war": 0.14,
    "p_war_posterior": 0.14,
    "forecasts": {
        "24h": {"wri": 43.0, "tier": "MEDIUM", "p_war": 0.14, "trend": "stable", "confidence": 0.70},
        "72h": {"wri": 46.0, "tier": "MEDIUM", "p_war": 0.16, "trend": "escalating", "confidence": 0.62},
        "7d":  {"wri": 49.0, "tier": "HIGH",   "p_war": 0.19, "trend": "escalating", "confidence": 0.52},
    },
    "narrative_md": "## Intelligence Briefing\n\nGenerated brief.",
    "lenses": {},
    "generated_at": "2026-05-17T09:00:00+00:00",
    "latency_ms": 123,
}


@pytest.fixture()
def flask_app():
    """Create a minimal Flask test app with the strategos blueprint."""
    from flask import Flask
    from tools.strategos.blueprint import create_strategos_blueprint

    app = Flask(__name__, template_folder=str(BASE_DIR / "tools" / "dashboard" / "templates"))
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test"
    bp = create_strategos_blueprint()
    app.register_blueprint(bp)
    return app


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


# ---------------------------------------------------------------------------
# GET /strategos/intel-brief
# ---------------------------------------------------------------------------


_FAKE_HTML = b"<html><body>CUI // SP-CTI Generate Brief No leadership briefs generated yet UKRAINE</body></html>"


class TestIntelBriefPage:
    """Tests for the intelligence brief page route.

    render_template is patched to return minimal HTML so the tests are not
    coupled to base.html's context-processor requirements (ROLE_VIEWS etc.).
    The assertions focus on HTTP behaviour and route-level logic.
    """

    def test_page_returns_200(self, client):
        """GET /strategos/intel-brief returns HTTP 200."""
        with patch("tools.strategos.blueprint.render_template", return_value="<html>CUI</html>"), \
             patch("tools.strategos.predictive_intel_engine.list_leadership_briefs", return_value=[]):
            resp = client.get("/strategos/intel-brief")
        assert resp.status_code == 200

    def test_page_sets_classification_header(self, client):
        """X-Classification response header is set to CUI."""
        with patch("tools.strategos.blueprint.render_template", return_value="<html>ok</html>"), \
             patch("tools.strategos.predictive_intel_engine.list_leadership_briefs", return_value=[]):
            resp = client.get("/strategos/intel-brief")
        assert resp.headers.get("X-Classification") == "CUI"

    def test_page_accepts_theater_query_param(self, client):
        """Theater query param is forwarded to list_leadership_briefs."""
        with patch("tools.strategos.blueprint.render_template", return_value="<html>ok</html>"), \
             patch("tools.strategos.predictive_intel_engine.list_leadership_briefs", return_value=[]) as mock_list:
            client.get("/strategos/intel-brief?theater=pacific")
        mock_list.assert_called_once()
        call_kwargs = mock_list.call_args
        assert "pacific" in str(call_kwargs)

    def test_page_passes_latest_brief_none_when_no_briefs(self, client):
        """latest_brief template var is None when no briefs exist."""
        captured = {}

        def _capture(template_name, **ctx):
            captured.update(ctx)
            return "<html>ok</html>"

        with patch("tools.strategos.blueprint.render_template", side_effect=_capture), \
             patch("tools.strategos.predictive_intel_engine.list_leadership_briefs", return_value=[]):
            client.get("/strategos/intel-brief")
        assert captured.get("latest_brief") is None

    def test_page_passes_latest_brief_when_briefs_exist(self, client):
        """latest_brief is set to the first brief in the list."""
        brief_list = [{
            "id": "b1", "theater": "ukraine",
            "sio_composite_score": 65.0, "iw_triggered": 1,
            "threat_tier": "HIGH", "signal_count_24h": 30,
            "signal_velocity": 8.0, "p_war_posterior": 0.35,
            "goldstein_avg": -3.2, "dti_score": 48.0,
            "forecast_24h_json": {}, "forecast_72h_json": {}, "forecast_7d_json": {},
            "narrative_md": "# Test", "generated_at": "2026-05-17T07:00:00+00:00",
            "classification": "CUI // SP-CTI",
        }]
        captured = {}

        def _capture(template_name, **ctx):
            captured.update(ctx)
            return "<html>ok</html>"

        with patch("tools.strategos.blueprint.render_template", side_effect=_capture), \
             patch("tools.strategos.predictive_intel_engine.list_leadership_briefs", return_value=brief_list):
            client.get("/strategos/intel-brief")
        assert captured.get("latest_brief") is not None
        assert captured["latest_brief"]["theater"] == "ukraine"

    def test_page_renders_intel_brief_template(self, client):
        """Route renders the strategos/intel_brief.html template."""
        rendered_templates = []

        def _capture(template_name, **ctx):
            rendered_templates.append(template_name)
            return "<html>ok</html>"

        with patch("tools.strategos.blueprint.render_template", side_effect=_capture), \
             patch("tools.strategos.predictive_intel_engine.list_leadership_briefs", return_value=[]):
            client.get("/strategos/intel-brief")
        assert any("intel_brief" in t for t in rendered_templates)

    def test_page_provides_briefs_json_for_js(self, client):
        """briefs_json context var is a valid JSON array string."""
        captured = {}

        def _capture(template_name, **ctx):
            captured.update(ctx)
            return "<html>ok</html>"

        with patch("tools.strategos.blueprint.render_template", side_effect=_capture), \
             patch("tools.strategos.predictive_intel_engine.list_leadership_briefs", return_value=[]):
            client.get("/strategos/intel-brief")
        briefs_json = captured.get("briefs_json", "null")
        parsed = json.loads(briefs_json)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# POST /api/strategos/intel-brief/run
# ---------------------------------------------------------------------------


class TestIntelBriefRunApi:
    """Tests for the POST run endpoint."""

    def test_run_returns_200_on_success(self, client):
        """POST /api/strategos/intel-brief/run returns 200 with ok=True."""
        with patch(
            "tools.strategos.predictive_intel_engine.generate_leadership_brief",
            return_value=BRIEF_GENERATE_RESULT,
        ):
            resp = client.post(
                "/api/strategos/intel-brief/run",
                json={"theater": "global"},
                content_type="application/json",
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_run_response_contains_wri(self, client):
        """Run response includes wri field from sio_composite_score."""
        with patch(
            "tools.strategos.predictive_intel_engine.generate_leadership_brief",
            return_value=BRIEF_GENERATE_RESULT,
        ):
            resp = client.post(
                "/api/strategos/intel-brief/run",
                json={"theater": "global"},
                content_type="application/json",
            )
        data = resp.get_json()
        assert "wri" in data
        assert data["wri"] == pytest.approx(4.2)

    def test_run_response_contains_threat_tier(self, client):
        """Run response includes threat_tier field."""
        with patch(
            "tools.strategos.predictive_intel_engine.generate_leadership_brief",
            return_value=BRIEF_GENERATE_RESULT,
        ):
            resp = client.post(
                "/api/strategos/intel-brief/run",
                json={"theater": "global"},
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["threat_tier"] == "MEDIUM"

    def test_run_response_contains_narrative_md(self, client):
        """Run response includes narrative_md for the modal."""
        with patch(
            "tools.strategos.predictive_intel_engine.generate_leadership_brief",
            return_value=BRIEF_GENERATE_RESULT,
        ):
            resp = client.post(
                "/api/strategos/intel-brief/run",
                json={"theater": "global"},
                content_type="application/json",
            )
        data = resp.get_json()
        assert "narrative_md" in data
        assert len(data["narrative_md"]) > 0

    def test_run_response_contains_p_war_posterior(self, client):
        """Run response includes p_war_posterior for JS display."""
        with patch(
            "tools.strategos.predictive_intel_engine.generate_leadership_brief",
            return_value=BRIEF_GENERATE_RESULT,
        ):
            resp = client.post(
                "/api/strategos/intel-brief/run",
                json={"theater": "global"},
                content_type="application/json",
            )
        data = resp.get_json()
        assert "p_war_posterior" in data

    def test_run_defaults_theater_to_global(self, client):
        """When no theater supplied, defaults to global."""
        with patch(
            "tools.strategos.predictive_intel_engine.generate_leadership_brief",
            return_value=BRIEF_GENERATE_RESULT,
        ) as mock_gen:
            client.post(
                "/api/strategos/intel-brief/run",
                json={},
                content_type="application/json",
            )
        mock_gen.assert_called_once()
        call_kwargs = mock_gen.call_args
        assert "global" in str(call_kwargs)

    def test_run_sets_classification_header(self, client):
        """X-Classification header is CUI on run response."""
        with patch(
            "tools.strategos.predictive_intel_engine.generate_leadership_brief",
            return_value=BRIEF_GENERATE_RESULT,
        ):
            resp = client.post(
                "/api/strategos/intel-brief/run",
                json={"theater": "global"},
                content_type="application/json",
            )
        assert resp.headers.get("X-Classification") == "CUI"

    def test_run_handles_engine_error_gracefully(self, client):
        """If generate_leadership_brief raises, endpoint returns ok=False."""
        with patch(
            "tools.strategos.predictive_intel_engine.generate_leadership_brief",
            side_effect=RuntimeError("OSINT mesh unavailable"),
        ):
            resp = client.post(
                "/api/strategos/intel-brief/run",
                json={"theater": "global"},
                content_type="application/json",
            )
        data = resp.get_json()
        assert data["ok"] is False
        assert "error" in data


# ---------------------------------------------------------------------------
# GET /strategos/briefs  (all briefs listing page)
# ---------------------------------------------------------------------------


class TestBriefListingPage:
    """Tests for the /strategos/briefs listing page.

    This page may be served by other blueprint routes; we simply verify
    the URL returns a non-404 status (the blueprint exists and is reachable).
    """

    def test_briefs_page_is_reachable(self, client):
        """GET /strategos/briefs returns HTTP 200."""
        with patch("tools.strategos.blueprint.render_template", return_value="<html>CUI</html>"), \
             patch("tools.strategos.predictive_intel_engine.list_leadership_briefs", return_value=[]):
            resp = client.get("/strategos/briefs")
        assert resp.status_code == 200

    def test_intel_brief_page_slug_accessible(self, client):
        """Canonical /strategos/intel-brief URL is accessible."""
        with patch("tools.strategos.blueprint.render_template", return_value="<html>ok</html>"), \
             patch("tools.strategos.predictive_intel_engine.list_leadership_briefs", return_value=[]):
            resp = client.get("/strategos/intel-brief")
        assert resp.status_code == 200
