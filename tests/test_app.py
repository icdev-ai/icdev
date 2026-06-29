"""Tests for dashboard app compliance aggregation endpoints."""
import pytest


@pytest.fixture
def client():
    """Yield a Flask test client for the dashboard app."""
    from tools.dashboard.app import app

    app.config["TESTING"] = True
    with app.test_client() as tc:
        yield tc


def test_api_charts_overview_returns_compliance_canvases(client):
    """The overview endpoint should return a compliance canvases list."""
    from sqlite3 import OperationalError

    try:
        resp = client.get("/api/charts/overview")
    except OperationalError as exc:
        pytest.skip(f"SQLite test DB lacks platform schema for overview: {exc}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "compliance" in data
    canvases = data["compliance"]["canvases"]
    assert isinstance(canvases, list)
    names = {c["name"] for c in canvases}
    assert "Security" in names
    assert "Data" in names
    assert "overall_score" in data["compliance"]


def test_api_charts_compliance_trend_returns_canvases(client):
    """The trend endpoint should return per-canvas score series."""
    resp = client.get("/api/charts/compliance-trend")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "canvases" in data
    for entry in data["canvases"]:
        assert "name" in entry
        assert "scores" in entry
        assert "direction" in entry
        assert "delta" in entry


def test_api_charts_compliance_trend_security_has_scores_or_empty(client):
    """Security trend data should be present even when no rows exist."""
    resp = client.get("/api/charts/compliance-trend")
    data = resp.get_json()
    security = next((c for c in data["canvases"] if c["name"] == "Security"), None)
    assert security is not None
    assert isinstance(security["scores"], list)
