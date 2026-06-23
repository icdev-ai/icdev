# CUI // SP-CTI
"""Tests for ecr-demo-01: demo seed command + ICDEV_DEMO_MODE flag.

Coverage:
  - test_demo_seed_provisions_tenant     — seeder creates tenant, seeds 6 canvases, demo_mode=True
  - test_demo_seed_returns_metadata      — result dict has all required keys
  - test_demo_seed_custom_canvases       — custom canvas list is respected
  - test_demo_seed_upgrades_settings     — _apply_demo_settings writes ICDEV_DEMO_MODE into tenant settings
  - test_is_demo_mode_env_var            — is_demo_mode() returns True when ICDEV_DEMO_MODE=true
  - test_is_demo_mode_env_var_false      — is_demo_mode() returns False when env var is absent/empty
  - test_is_demo_mode_settings           — is_demo_mode() reads from tenant settings dict
  - test_synthetic_data_engine_domains   — SyntheticDataEngine.generate() works for all 6 domains
  - test_synthetic_data_engine_count     — generate(domain, records=N) returns exactly N records
  - test_synthetic_data_engine_unknown   — raises ValueError for unknown domain
  - test_synthetic_data_engine_ids       — every record has an 'id' field
  - test_synthetic_data_engine_deterministic — same seed yields same output
  - test_cli_demo_seed_dispatches        — 'icdev demo seed --tenant demo-acme' routes to seeder
  - test_cli_demo_seed_custom_canvases   — --canvases flag parsed and forwarded
  - test_cli_demo_no_subcommand          — bare 'icdev demo' prints help, exit 1
"""
from __future__ import annotations

import json
import os
import sqlite3
from unittest.mock import MagicMock, call, patch

import pytest

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_MOCK_TENANT_ID = "tenant-demo123abc456"

_MOCK_CREATE_RESULT = {
    "tenant": {
        "id": _MOCK_TENANT_ID,
        "name": "Demo Acme",
        "slug": "demo-acme",
        "status": "active",
        "tier": "professional",
        "impact_level": "IL4",
        "db_name": "demo-acme.db",
        "k8s_namespace": "icdev-tenant-demo-acme",
    },
    "user": {
        "id": "user-abc",
        "email": "demo@demo-acme.icdev.example",
        "display_name": "Demo Admin",
        "role": "tenant_admin",
    },
    "api_key": {
        "id": "key-001",
        "key": "icdev_testkey0001",
        "prefix": "testkey0",
        "note": "Save this key now. It cannot be retrieved later.",
    },
}


def _platform_conn_with_tenant() -> sqlite3.Connection:
    """In-memory platform DB with a provisioned demo tenant row."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE tenants (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            slug            TEXT NOT NULL UNIQUE,
            status          TEXT NOT NULL DEFAULT 'pending',
            tier            TEXT NOT NULL DEFAULT 'starter',
            impact_level    TEXT NOT NULL DEFAULT 'IL4',
            billing_status  TEXT NOT NULL DEFAULT 'unknown',
            settings        TEXT DEFAULT '{}',
            db_host         TEXT,
            db_name         TEXT,
            k8s_namespace   TEXT,
            created_at      TEXT,
            updated_at      TEXT
        );
        """
    )
    conn.execute(
        """INSERT INTO tenants
           (id, name, slug, status, tier, billing_status, settings, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            _MOCK_TENANT_ID, "Demo Acme", "demo-acme", "active",
            "professional", "unknown", "{}", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        ),
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# SyntheticDataEngine — pure unit tests (no I/O)
# ---------------------------------------------------------------------------

def test_synthetic_data_engine_domains():
    """generate() returns non-empty lists for all 6 default domains."""
    from icdev.tools.showcase.synthetic_data_engine import DOMAINS, SyntheticDataEngine

    engine = SyntheticDataEngine(seed=0)
    for domain in DOMAINS:
        records = engine.generate(domain, records=5)
        assert len(records) == 5, f"Expected 5 records for domain {domain!r}"
        assert all(isinstance(r, dict) for r in records), f"Non-dict record in domain {domain!r}"


def test_synthetic_data_engine_count():
    """generate(domain, records=N) returns exactly N records."""
    from icdev.tools.showcase.synthetic_data_engine import SyntheticDataEngine

    engine = SyntheticDataEngine(seed=1)
    assert len(engine.generate("cyber", records=20)) == 20
    assert len(engine.generate("network", records=1)) == 1
    assert len(engine.generate("knowledge", records=0)) == 0


def test_synthetic_data_engine_unknown():
    """generate() raises ValueError for an unknown domain."""
    from icdev.tools.showcase.synthetic_data_engine import SyntheticDataEngine

    engine = SyntheticDataEngine()
    with pytest.raises(ValueError, match="Unknown domain"):
        engine.generate("unicorn_canvas", records=5)


def test_synthetic_data_engine_ids():
    """Every generated record has a non-empty 'id' field."""
    from icdev.tools.showcase.synthetic_data_engine import DOMAINS, SyntheticDataEngine

    engine = SyntheticDataEngine(seed=7)
    for domain in DOMAINS:
        for record in engine.generate(domain, records=3):
            assert record.get("id"), f"Missing 'id' in {domain!r} record: {record}"


def test_synthetic_data_engine_deterministic():
    """Same seed produces identical output on repeated calls."""
    from icdev.tools.showcase.synthetic_data_engine import SyntheticDataEngine

    r1 = SyntheticDataEngine(seed=99).generate("cyber", records=10)
    r2 = SyntheticDataEngine(seed=99).generate("cyber", records=10)
    assert r1 == r2


# ---------------------------------------------------------------------------
# is_demo_mode tests
# ---------------------------------------------------------------------------

def test_is_demo_mode_env_var(monkeypatch):
    """is_demo_mode() returns True when ICDEV_DEMO_MODE env var is truthy."""
    import importlib
    import icdev.tools.demo.seeder as _mod
    importlib.reload(_mod)

    monkeypatch.setenv("ICDEV_DEMO_MODE", "true")
    assert _mod.is_demo_mode() is True

    monkeypatch.setenv("ICDEV_DEMO_MODE", "1")
    assert _mod.is_demo_mode() is True

    monkeypatch.setenv("ICDEV_DEMO_MODE", "yes")
    assert _mod.is_demo_mode() is True


def test_is_demo_mode_env_var_false(monkeypatch):
    """is_demo_mode() returns False when env var is absent or empty."""
    import icdev.tools.demo.seeder as _mod

    monkeypatch.delenv("ICDEV_DEMO_MODE", raising=False)
    assert _mod.is_demo_mode() is False

    monkeypatch.setenv("ICDEV_DEMO_MODE", "false")
    assert _mod.is_demo_mode() is False

    monkeypatch.setenv("ICDEV_DEMO_MODE", "0")
    assert _mod.is_demo_mode() is False


def test_is_demo_mode_settings(monkeypatch):
    """is_demo_mode() returns True/False from tenant settings dict."""
    import icdev.tools.demo.seeder as _mod

    monkeypatch.delenv("ICDEV_DEMO_MODE", raising=False)
    assert _mod.is_demo_mode({"ICDEV_DEMO_MODE": True}) is True
    assert _mod.is_demo_mode({"ICDEV_DEMO_MODE": False}) is False
    assert _mod.is_demo_mode({}) is False
    assert _mod.is_demo_mode(None) is False


# ---------------------------------------------------------------------------
# seed_demo_tenant — orchestration tests (helpers patched)
# ---------------------------------------------------------------------------

def test_demo_seed_provisions_tenant():
    """seed_demo_tenant creates a tenant, seeds 6 canvases, returns demo_mode=True.

    This is the primary acceptance test for ecr-demo-01.
    """
    with (
        patch("icdev.tools.demo.seeder.create_tenant", return_value=_MOCK_CREATE_RESULT) as mock_create,
        patch("icdev.tools.demo.seeder._apply_demo_settings") as mock_apply,
        patch("icdev.tools.demo.seeder._seed_canvas_records") as mock_seed,
    ):
        from icdev.tools.demo.seeder import seed_demo_tenant

        result = seed_demo_tenant("demo-acme")

    assert result["demo_mode"] is True
    assert result["canvases_seeded"] == 6
    assert result["slug"] == "demo-acme"
    assert result["tenant_id"] == _MOCK_TENANT_ID

    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args
    assert call_kwargs.kwargs.get("impact_level") == "IL4" or call_kwargs.args[1] == "IL4"

    mock_apply.assert_called_once_with(_MOCK_TENANT_ID, "demo-acme")
    assert mock_seed.call_count == 6


def test_demo_seed_returns_metadata():
    """Result dict has all required keys."""
    with (
        patch("icdev.tools.demo.seeder.create_tenant", return_value=_MOCK_CREATE_RESULT),
        patch("icdev.tools.demo.seeder._apply_demo_settings"),
        patch("icdev.tools.demo.seeder._seed_canvas_records"),
    ):
        from icdev.tools.demo.seeder import seed_demo_tenant

        result = seed_demo_tenant("demo-acme")

    required_keys = {"tenant_id", "slug", "canvases_seeded", "canvases", "demo_mode"}
    assert required_keys <= set(result.keys())
    assert isinstance(result["canvases"], list)
    assert len(result["canvases"]) == 6


def test_demo_seed_custom_canvases():
    """Custom canvas list is respected: only those canvases are seeded."""
    custom = ["cyber", "network"]
    with (
        patch("icdev.tools.demo.seeder.create_tenant", return_value=_MOCK_CREATE_RESULT),
        patch("icdev.tools.demo.seeder._apply_demo_settings"),
        patch("icdev.tools.demo.seeder._seed_canvas_records") as mock_seed,
    ):
        from icdev.tools.demo.seeder import seed_demo_tenant

        result = seed_demo_tenant("demo-acme", canvases=custom)

    assert result["canvases_seeded"] == 2
    assert result["canvases"] == custom
    assert mock_seed.call_count == 2


def test_demo_seed_upgrades_settings(tmp_path):
    """_apply_demo_settings writes ICDEV_DEMO_MODE=true + enterprise tier into the tenant row."""
    db_path = tmp_path / "platform.db"

    def _make_conn():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    # Seed the tenant row
    seed_conn = _make_conn()
    seed_conn.execute(
        """CREATE TABLE tenants (
            id TEXT PRIMARY KEY, name TEXT, slug TEXT, status TEXT,
            tier TEXT, impact_level TEXT, billing_status TEXT DEFAULT 'unknown',
            settings TEXT DEFAULT '{}', db_host TEXT, db_name TEXT,
            k8s_namespace TEXT, created_at TEXT, updated_at TEXT
        )"""
    )
    seed_conn.execute(
        """INSERT INTO tenants
           (id, name, slug, status, tier, billing_status, settings, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            _MOCK_TENANT_ID, "Demo Acme", "demo-acme", "active",
            "professional", "unknown", "{}", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        ),
    )
    seed_conn.commit()
    seed_conn.close()

    # Each call to get_platform_connection returns a fresh file-based connection
    with patch(
        "icdev.tools.demo.seeder.get_platform_connection",
        side_effect=_make_conn,
    ):
        from icdev.tools.demo.seeder import _apply_demo_settings

        _apply_demo_settings(_MOCK_TENANT_ID, "demo-acme")

    verify_conn = _make_conn()
    row = verify_conn.execute(
        "SELECT tier, billing_status, settings FROM tenants WHERE id = ?",
        (_MOCK_TENANT_ID,),
    ).fetchone()
    verify_conn.close()

    assert row is not None
    assert row["tier"] == "enterprise"
    assert row["billing_status"] == "active"

    settings = json.loads(row["settings"])
    assert settings.get("ICDEV_DEMO_MODE") is True
    assert settings.get("banner_mode") == "custom"
    assert "demo" in settings.get("billing_status", "")


# ---------------------------------------------------------------------------
# CLI dispatch tests
# ---------------------------------------------------------------------------

def test_cli_demo_seed_dispatches():
    """'icdev demo seed --tenant demo-acme' routes to seed_demo_tenant."""
    seed_result = {
        "tenant_id": _MOCK_TENANT_ID,
        "slug": "demo-acme",
        "canvases_seeded": 6,
        "canvases": list(range(6)),
        "demo_mode": True,
    }
    with patch("icdev.tools.demo.seeder.seed_demo_tenant", return_value=seed_result) as mock_seed:
        from icdev.tools.cli.__main__ import main

        rc = main(["demo", "seed", "--tenant", "demo-acme"])

    assert rc == 0
    mock_seed.assert_called_once_with("demo-acme", canvases=None)


def test_cli_demo_seed_custom_canvases():
    """--canvases flag is parsed and forwarded as a list."""
    seed_result = {
        "tenant_id": _MOCK_TENANT_ID,
        "slug": "demo-acme",
        "canvases_seeded": 2,
        "canvases": ["cyber", "network"],
        "demo_mode": True,
    }
    with patch("icdev.tools.demo.seeder.seed_demo_tenant", return_value=seed_result) as mock_seed:
        from icdev.tools.cli.__main__ import main

        rc = main(["demo", "seed", "--tenant", "demo-acme", "--canvases", "cyber,network"])

    assert rc == 0
    mock_seed.assert_called_once_with("demo-acme", canvases=["cyber", "network"])


def test_cli_demo_no_subcommand(capsys):
    """Bare 'icdev demo' prints help and returns exit code 1."""
    from icdev.tools.cli.__main__ import main

    rc = main(["demo"])
    assert rc == 1


# ---------------------------------------------------------------------------
# ECR-DEMO-03: Demo mode read-only enforcement + demo banner
# ---------------------------------------------------------------------------

def _make_demo_flask_app(demo_mode: bool):
    """Minimal Flask app with demo mode guard wired up (mirrors create_app logic)."""
    from flask import Flask, jsonify, request as req

    app = Flask(__name__)
    app.config["TESTING"] = True

    if demo_mode:
        @app.before_request
        def _demo_mode_guard():
            if req.method not in ("POST", "PUT", "DELETE", "PATCH"):
                return None
            path = req.path
            if not path.startswith("/api/"):
                return None
            if path.startswith("/api/onboarding/") or path == "/api/iqe-query":
                return None
            return jsonify({"error": "Demo mode: read-only", "upgrade_url": ""}), 403

    @app.route("/api/components/enable", methods=["POST"])
    def components_enable():
        return jsonify({"ok": True})

    @app.route("/api/iqe-query", methods=["POST"])
    def iqe_query():
        return jsonify({"results": []})

    @app.route("/api/onboarding/step", methods=["POST"])
    def onboarding_step():
        return jsonify({"ok": True})

    @app.route("/api/data", methods=["GET"])
    def data_get():
        return jsonify({"data": []})

    return app


def test_demo_mode_blocks_writes(monkeypatch):
    """POST /api/components/enable returns 403 in demo mode."""
    monkeypatch.setenv("ICDEV_DEMO_MODE", "true")
    app = _make_demo_flask_app(demo_mode=True)

    with app.test_client() as client:
        resp = client.post("/api/components/enable", json={"canvas": "network"})
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"
        body = resp.get_json()
        assert body["error"] == "Demo mode: read-only"
        assert "upgrade_url" in body


def test_demo_mode_allows_iqe(monkeypatch):
    """POST /api/iqe-query is allowed through in demo mode."""
    monkeypatch.setenv("ICDEV_DEMO_MODE", "true")
    app = _make_demo_flask_app(demo_mode=True)

    with app.test_client() as client:
        resp = client.post("/api/iqe-query", json={"query": "find threats"})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"


def test_demo_mode_allows_onboarding(monkeypatch):
    """POST /api/onboarding/* is allowed through in demo mode."""
    monkeypatch.setenv("ICDEV_DEMO_MODE", "true")
    app = _make_demo_flask_app(demo_mode=True)

    with app.test_client() as client:
        resp = client.post("/api/onboarding/step", json={"step": 1})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"


def test_demo_mode_allows_get(monkeypatch):
    """GET requests to /api/* are allowed through in demo mode."""
    monkeypatch.setenv("ICDEV_DEMO_MODE", "true")
    app = _make_demo_flask_app(demo_mode=True)

    with app.test_client() as client:
        resp = client.get("/api/data")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"


def test_demo_mode_off_allows_writes(monkeypatch):
    """POST /api/* is allowed when ICDEV_DEMO_MODE is not set."""
    monkeypatch.delenv("ICDEV_DEMO_MODE", raising=False)
    app = _make_demo_flask_app(demo_mode=False)

    with app.test_client() as client:
        resp = client.post("/api/components/enable", json={"canvas": "network"})
        assert resp.status_code == 200, f"Expected 200 when demo mode off, got {resp.status_code}"


def test_demo_banner_set_when_demo_mode(monkeypatch):
    """get_banner() returns demo banner when ICDEV_DEMO_MODE=true and no explicit banner mode."""
    import importlib
    import tools.dashboard.brand as brand_mod

    monkeypatch.setenv("ICDEV_DEMO_MODE", "true")
    monkeypatch.delenv("ICDEV_BANNER_MODE", raising=False)
    monkeypatch.delenv("ICDEV_CUI_BANNER_ENABLED", raising=False)

    banner_mod = importlib.import_module("tools.dashboard.brand")
    banner_mod._banner_cache = None  # force reload

    banner = banner_mod.get_banner(reload=True)
    assert banner["mode"] == "demo"
    assert banner["enabled"] is True
    assert "Demo" in banner["text"] or "DEMO" in banner["text"]
    assert banner["background_color"] == "#1a472a"


def test_demo_banner_not_set_when_explicit_mode(monkeypatch):
    """Explicit ICDEV_BANNER_MODE overrides the demo fallback."""
    import importlib

    monkeypatch.setenv("ICDEV_DEMO_MODE", "true")
    monkeypatch.setenv("ICDEV_BANNER_MODE", "none")

    banner_mod = importlib.import_module("tools.dashboard.brand")
    banner_mod._banner_cache = None

    banner = banner_mod.get_banner(reload=True)
    assert banner["mode"] == "none"
    assert banner["enabled"] is False
