"""Tests for dashboard app compliance aggregation endpoints."""
import pytest


@pytest.fixture
def client(icdev_db, monkeypatch):
    """Yield an AUTHENTICATED Flask test client for the dashboard app.

    The /api/charts/* endpoints require a logged-in session. This fixture never
    established one, so all three tests here asserted 200 and got 401 - they had
    been failing on their own merits, not on anything about charts. It stayed
    invisible because tests/test_app.py is not in args/ci_test_files/core.txt,
    so CI never ran it.

    Wiring follows the convention conftest already documents next to the
    dashboard_users seed ("route tests set session[\"user_id\"]=\"test-admin\"")
    and matches tests/cortex/test_blueprint_routes.py: point auth and storage at
    the temp DB that carries the seeded user, then log the session in.

    Reads the shared app singleton but never registers on it, so it cannot hit
    the setup-lock failure that affected the blueprint-registering fixtures.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))

    import tools.dashboard.auth as _auth

    monkeypatch.setattr(_auth, "DB_PATH", str(icdev_db))

    from tools.dashboard.app import app

    app.config["TESTING"] = True
    with app.test_client() as tc:
        with tc.session_transaction() as sess:
            sess["user_id"] = "test-admin"
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
