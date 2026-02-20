# CUI // SP-CTI
"""Tests for the usage tracking API blueprint (tools/dashboard/api/usage.py)."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools.dashboard.api.usage import (
    DEFAULT_COST_PER_1K,
    _estimate_cost,
    usage_api,
)
from tools.db.init_icdev_db import DASHBOARD_AUTH_ALTER_SQL, SCHEMA_SQL

try:
    from flask import Flask, g
except ImportError:
    pytest.skip("Flask not installed", allow_module_level=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path):
    """Create a temporary ICDEV database with the full schema."""
    path = tmp_path / "test_icdev.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA_SQL)
    for sql in DASHBOARD_AUTH_ALTER_SQL:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # Column already exists — idempotent
    conn.commit()
    conn.close()
    return path


def _seed_usage(db_path, rows):
    """Insert test token-usage rows into the database.

    Each *row* is a dict with keys matching agent_token_usage columns.
    Defaults are applied for any missing columns.
    """
    conn = sqlite3.connect(str(db_path))
    defaults = {
        "agent_id": "builder-agent",
        "project_id": "proj-test",
        "task_id": "task-1",
        "model_id": "claude-sonnet-4-6",
        "input_tokens": 100,
        "output_tokens": 50,
        "thinking_tokens": 10,
        "duration_ms": 500,
        "cost_estimate_usd": 0.001,
        "classification": "CUI",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        "user_id": None,
        "api_key_source": "config",
    }
    for row in rows:
        merged = {**defaults, **row}
        cols = list(merged.keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        conn.execute(
            f"INSERT INTO agent_token_usage ({col_names}) VALUES ({placeholders})",
            [merged[c] for c in cols],
        )
    conn.commit()
    conn.close()


@pytest.fixture()
def app(db_path, monkeypatch):
    """Create a Flask test app with the usage_api blueprint registered."""
    monkeypatch.setattr("tools.dashboard.api.usage.DB_PATH", str(db_path))

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(usage_api)
    return flask_app


@pytest.fixture()
def admin_user():
    return {"id": "admin-user", "role": "admin", "email": "admin@test.mil"}


@pytest.fixture()
def regular_user():
    return {"id": "user-alice", "role": "developer", "email": "alice@test.mil"}


@pytest.fixture()
def client_admin(app, admin_user):
    """Test client with admin auth context."""

    @app.before_request
    def _set_admin():
        g.current_user = admin_user

    return app.test_client()


@pytest.fixture()
def client_user(app, regular_user):
    """Test client with non-admin auth context."""

    @app.before_request
    def _set_user():
        g.current_user = regular_user

    return app.test_client()


@pytest.fixture()
def client_unauth(app):
    """Test client with no auth context (unauthenticated)."""
    return app.test_client()


# ---------------------------------------------------------------------------
# 1. Totals endpoint — aggregated stats
# ---------------------------------------------------------------------------


def test_totals_returns_aggregated_stats(db_path, client_admin):
    _seed_usage(db_path, [
        {"input_tokens": 1000, "output_tokens": 500, "thinking_tokens": 100,
         "cost_estimate_usd": 0.05, "user_id": "admin-user"},
        {"input_tokens": 2000, "output_tokens": 1000, "thinking_tokens": 200,
         "cost_estimate_usd": 0.10, "user_id": "admin-user"},
    ])
    resp = client_admin.get("/api/usage/totals")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total_input"] == 3000
    assert data["total_output"] == 1500
    assert data["total_thinking"] == 300
    assert data["total_requests"] == 2


# ---------------------------------------------------------------------------
# 2. Summary endpoint — per-user breakdown (admin view)
# ---------------------------------------------------------------------------


def test_summary_admin_sees_per_user_breakdown(db_path, client_admin):
    _seed_usage(db_path, [
        {"user_id": "user-alice", "input_tokens": 500, "output_tokens": 200,
         "cost_estimate_usd": 0.02},
        {"user_id": "user-bob", "input_tokens": 800, "output_tokens": 400,
         "cost_estimate_usd": 0.04},
        {"user_id": "user-alice", "input_tokens": 300, "output_tokens": 100,
         "cost_estimate_usd": 0.01},
    ])
    resp = client_admin.get("/api/usage/summary")
    assert resp.status_code == 200
    data = resp.get_json()
    usage = data["usage"]
    # Admin sees grouped rows for each user
    user_ids = {row["user_id"] for row in usage}
    assert "user-alice" in user_ids
    assert "user-bob" in user_ids
    # Alice has 2 rows aggregated
    alice = next(r for r in usage if r["user_id"] == "user-alice")
    assert alice["total_input"] == 800
    assert alice["request_count"] == 2


# ---------------------------------------------------------------------------
# 3. Summary endpoint — non-admin sees only own data
# ---------------------------------------------------------------------------


def test_summary_non_admin_sees_only_own(db_path, client_user):
    _seed_usage(db_path, [
        {"user_id": "user-alice", "input_tokens": 500, "output_tokens": 200,
         "cost_estimate_usd": 0.02},
        {"user_id": "user-bob", "input_tokens": 800, "output_tokens": 400,
         "cost_estimate_usd": 0.04},
    ])
    resp = client_user.get("/api/usage/summary")
    assert resp.status_code == 200
    data = resp.get_json()
    usage = data["usage"]
    # Non-admin only sees own data (user-alice)
    assert len(usage) == 1
    assert usage[0]["user_id"] == "user-alice"
    assert usage[0]["total_input"] == 500


# ---------------------------------------------------------------------------
# 4. By-provider — per-model breakdown
# ---------------------------------------------------------------------------


def test_by_provider_returns_per_model_breakdown(db_path, client_admin):
    _seed_usage(db_path, [
        {"model_id": "claude-opus-4-6", "input_tokens": 1000,
         "output_tokens": 500, "cost_estimate_usd": 0.05,
         "user_id": "admin-user", "api_key_source": "config"},
        {"model_id": "gpt-4o", "input_tokens": 2000,
         "output_tokens": 1000, "cost_estimate_usd": 0.10,
         "user_id": "admin-user", "api_key_source": "byok"},
        {"model_id": "claude-opus-4-6", "input_tokens": 500,
         "output_tokens": 250, "cost_estimate_usd": 0.025,
         "user_id": "admin-user", "api_key_source": "config"},
    ])
    resp = client_admin.get("/api/usage/by-provider")
    assert resp.status_code == 200
    data = resp.get_json()
    providers = data["providers"]
    model_ids = {row["model_id"] for row in providers}
    assert "claude-opus-4-6" in model_ids
    assert "gpt-4o" in model_ids
    # The two opus rows share the same key_source so they aggregate together
    opus = next(r for r in providers
                if r["model_id"] == "claude-opus-4-6" and r["key_source"] == "config")
    assert opus["total_input"] == 1500
    assert opus["request_count"] == 2


# ---------------------------------------------------------------------------
# 5. Time-series — daily data
# ---------------------------------------------------------------------------


def test_time_series_returns_daily_data(db_path, client_admin):
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    _seed_usage(db_path, [
        {"created_at": f"{today} 10:00:00", "input_tokens": 100,
         "output_tokens": 50, "cost_estimate_usd": 0.01, "user_id": "admin-user"},
        {"created_at": f"{today} 14:00:00", "input_tokens": 200,
         "output_tokens": 100, "cost_estimate_usd": 0.02, "user_id": "admin-user"},
        {"created_at": f"{yesterday} 09:00:00", "input_tokens": 300,
         "output_tokens": 150, "cost_estimate_usd": 0.03, "user_id": "admin-user"},
    ])
    resp = client_admin.get("/api/usage/time-series")
    assert resp.status_code == 200
    data = resp.get_json()
    series = data["series"]
    # Two distinct days
    assert len(series) == 2
    days = [row["day"] for row in series]
    assert yesterday in days
    assert today in days
    # Today has 2 rows aggregated
    today_row = next(r for r in series if r["day"] == today)
    assert today_row["input_tokens"] == 300
    assert today_row["requests"] == 2


# ---------------------------------------------------------------------------
# 6. Time-series — respects days parameter
# ---------------------------------------------------------------------------


def test_time_series_respects_days_param(db_path, client_admin):
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    old_date = (datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%d")
    _seed_usage(db_path, [
        {"created_at": f"{today} 12:00:00", "input_tokens": 100,
         "output_tokens": 50, "cost_estimate_usd": 0.01, "user_id": "admin-user"},
        {"created_at": f"{old_date} 12:00:00", "input_tokens": 500,
         "output_tokens": 250, "cost_estimate_usd": 0.05, "user_id": "admin-user"},
    ])
    # Request only last 5 days — the old_date row should be excluded
    resp = client_admin.get("/api/usage/time-series?days=5")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["days"] == 5
    series = data["series"]
    assert len(series) == 1
    assert series[0]["day"] == today


# ---------------------------------------------------------------------------
# 7. Empty database returns zeros / empty lists
# ---------------------------------------------------------------------------


def test_totals_empty_db(client_admin):
    resp = client_admin.get("/api/usage/totals")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total_input"] is None or data["total_input"] == 0
    assert data["total_requests"] == 0


def test_summary_empty_db(client_admin):
    resp = client_admin.get("/api/usage/summary")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["usage"] == []


def test_by_provider_empty_db(client_admin):
    resp = client_admin.get("/api/usage/by-provider")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["providers"] == []


def test_time_series_empty_db(client_admin):
    resp = client_admin.get("/api/usage/time-series")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["series"] == []


# ---------------------------------------------------------------------------
# 8. Cost estimation function
# ---------------------------------------------------------------------------


def test_estimate_cost_known_model():
    # claude-opus-4-6: input=0.015/1K, output=0.075/1K
    cost = _estimate_cost("claude-opus-4-6", 2000, 1000)
    expected = (2000 / 1000.0) * 0.015 + (1000 / 1000.0) * 0.075
    assert cost == round(expected, 6)


def test_estimate_cost_unknown_model_uses_default():
    # Unknown model falls back to input=0.003, output=0.015
    cost = _estimate_cost("unknown-model-xyz", 1000, 1000)
    expected = (1000 / 1000.0) * 0.003 + (1000 / 1000.0) * 0.015
    assert cost == round(expected, 6)


def test_estimate_cost_zero_tokens():
    cost = _estimate_cost("claude-opus-4-6", 0, 0)
    assert cost == 0.0


def test_estimate_cost_gpt4o_mini():
    # gpt-4o-mini: input=0.00015/1K, output=0.0006/1K
    cost = _estimate_cost("gpt-4o-mini", 10000, 5000)
    expected = (10000 / 1000.0) * 0.00015 + (5000 / 1000.0) * 0.0006
    assert cost == round(expected, 6)


# ---------------------------------------------------------------------------
# 9. Admin sees all users' data
# ---------------------------------------------------------------------------


def test_admin_totals_includes_all_users(db_path, client_admin):
    _seed_usage(db_path, [
        {"user_id": "user-alice", "input_tokens": 100, "output_tokens": 50,
         "cost_estimate_usd": 0.01},
        {"user_id": "user-bob", "input_tokens": 200, "output_tokens": 100,
         "cost_estimate_usd": 0.02},
        {"user_id": "user-charlie", "input_tokens": 300, "output_tokens": 150,
         "cost_estimate_usd": 0.03},
    ])
    resp = client_admin.get("/api/usage/totals")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total_input"] == 600
    assert data["total_output"] == 300
    assert data["total_requests"] == 3
    assert data["unique_users"] == 3


def test_admin_by_provider_includes_all_users(db_path, client_admin):
    _seed_usage(db_path, [
        {"user_id": "user-alice", "model_id": "claude-opus-4-6",
         "input_tokens": 100, "output_tokens": 50, "cost_estimate_usd": 0.01},
        {"user_id": "user-bob", "model_id": "claude-opus-4-6",
         "input_tokens": 200, "output_tokens": 100, "cost_estimate_usd": 0.02},
    ])
    resp = client_admin.get("/api/usage/by-provider")
    assert resp.status_code == 200
    data = resp.get_json()
    providers = data["providers"]
    assert len(providers) >= 1
    opus = next(r for r in providers if r["model_id"] == "claude-opus-4-6")
    assert opus["total_input"] == 300
    assert opus["request_count"] == 2


# ---------------------------------------------------------------------------
# 10. Non-admin filtered to own user_id
# ---------------------------------------------------------------------------


def test_non_admin_totals_filtered(db_path, client_user):
    _seed_usage(db_path, [
        {"user_id": "user-alice", "input_tokens": 100, "output_tokens": 50,
         "cost_estimate_usd": 0.01},
        {"user_id": "user-bob", "input_tokens": 900, "output_tokens": 450,
         "cost_estimate_usd": 0.09},
    ])
    resp = client_user.get("/api/usage/totals")
    assert resp.status_code == 200
    data = resp.get_json()
    # Non-admin (user-alice) should only see their own 100 input tokens
    assert data["total_input"] == 100
    assert data["total_output"] == 50
    assert data["total_requests"] == 1


def test_non_admin_by_provider_filtered(db_path, client_user):
    _seed_usage(db_path, [
        {"user_id": "user-alice", "model_id": "claude-opus-4-6",
         "input_tokens": 100, "output_tokens": 50, "cost_estimate_usd": 0.01},
        {"user_id": "user-bob", "model_id": "gpt-4o",
         "input_tokens": 900, "output_tokens": 450, "cost_estimate_usd": 0.09},
    ])
    resp = client_user.get("/api/usage/by-provider")
    assert resp.status_code == 200
    data = resp.get_json()
    providers = data["providers"]
    # Only user-alice's model should appear
    assert len(providers) == 1
    assert providers[0]["model_id"] == "claude-opus-4-6"
    assert providers[0]["total_input"] == 100


def test_non_admin_time_series_filtered(db_path, client_user):
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    _seed_usage(db_path, [
        {"user_id": "user-alice", "created_at": f"{today} 10:00:00",
         "input_tokens": 100, "output_tokens": 50, "cost_estimate_usd": 0.01},
        {"user_id": "user-bob", "created_at": f"{today} 11:00:00",
         "input_tokens": 900, "output_tokens": 450, "cost_estimate_usd": 0.09},
    ])
    resp = client_user.get("/api/usage/time-series")
    assert resp.status_code == 200
    data = resp.get_json()
    series = data["series"]
    assert len(series) == 1
    assert series[0]["input_tokens"] == 100


# ---------------------------------------------------------------------------
# 11. Unauthenticated returns 401
# ---------------------------------------------------------------------------


def test_totals_unauthenticated(client_unauth):
    resp = client_unauth.get("/api/usage/totals")
    assert resp.status_code == 401
    data = resp.get_json()
    assert "error" in data


def test_summary_unauthenticated(client_unauth):
    resp = client_unauth.get("/api/usage/summary")
    assert resp.status_code == 401


def test_by_provider_unauthenticated(client_unauth):
    resp = client_unauth.get("/api/usage/by-provider")
    assert resp.status_code == 401


def test_time_series_unauthenticated(client_unauth):
    resp = client_unauth.get("/api/usage/time-series")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


def test_time_series_caps_days_at_90(db_path, client_admin):
    """Requesting days > 90 should be clamped to 90."""
    resp = client_admin.get("/api/usage/time-series?days=200")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["days"] == 90


def test_summary_admin_can_filter_by_user_id(db_path, client_admin):
    """Admin can pass ?user_id= to view a specific user's summary."""
    _seed_usage(db_path, [
        {"user_id": "user-alice", "input_tokens": 500, "output_tokens": 200,
         "cost_estimate_usd": 0.02},
        {"user_id": "user-bob", "input_tokens": 800, "output_tokens": 400,
         "cost_estimate_usd": 0.04},
    ])
    resp = client_admin.get("/api/usage/summary?user_id=user-bob")
    assert resp.status_code == 200
    data = resp.get_json()
    usage = data["usage"]
    assert len(usage) == 1
    assert usage[0]["user_id"] == "user-bob"
    assert usage[0]["total_input"] == 800


def test_cost_rates_dict_has_expected_models():
    """Verify the DEFAULT_COST_PER_1K dict contains key models."""
    assert "claude-opus-4-6" in DEFAULT_COST_PER_1K
    assert "gpt-4o" in DEFAULT_COST_PER_1K
    assert "gpt-4o-mini" in DEFAULT_COST_PER_1K
    for model, rates in DEFAULT_COST_PER_1K.items():
        assert "input" in rates, f"{model} missing 'input' rate"
        assert "output" in rates, f"{model} missing 'output' rate"
        assert rates["input"] >= 0
        assert rates["output"] >= 0
