# CUI // SP-CTI
"""nav-plat-04 — macro/intelligence outage must NOT masquerade as NEUTRAL.

Before this fix, ``GET /api/macro/intelligence`` returned
``{"qeqt_phase": "NEUTRAL", "credit_stress": "NEUTRAL", ...}`` with HTTP 200 on
ANY exception. To a FathomDesk analyst a data outage was indistinguishable from a
genuinely neutral market regime.

The endpoint now returns an explicit error state (HTTP 503,
``{"status": "error", "detail": ...}``) with NULL badges when the upstream macro
fetch fails. The healthy path is unchanged. The consumer (``analysis.html``)
renders "Macro intelligence unavailable" instead of NEUTRAL badges.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def macro_client():
    try:
        from tools.fathomdesk.blueprint import fathomdesk_api
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"fathomdesk blueprint not importable: {exc}")

    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app.register_blueprint(fathomdesk_api)
    with app.test_client() as c:
        yield c


def _fake_macro_module(ctx):
    mod = types.ModuleType("tools.trading.data.macro_data")

    def fetch_macro_context():
        return ctx

    mod.fetch_macro_context = fetch_macro_context
    return mod


# ── forced-failure path → explicit error state, NEVER "NEUTRAL" ───────────────

def test_macro_failure_returns_error_state_not_neutral(macro_client):
    # Setting the module to None forces the in-function
    # ``from tools.trading.data.macro_data import fetch_macro_context`` to raise
    # ImportError, exactly as a broken/unavailable upstream would at runtime.
    with patch.dict(sys.modules, {"tools.trading.data.macro_data": None}):
        resp = macro_client.get("/api/macro/intelligence")

    assert resp.status_code == 503, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["status"] == "error"
    assert data.get("detail")

    # The masking regime labels must NOT be presented as real values.
    assert data["qeqt_phase"] is None
    assert data["credit_stress"] is None
    assert data["rotation_signal"] is None

    # "NEUTRAL" must not appear as a fabricated regime anywhere in the body.
    assert "NEUTRAL" not in resp.get_data(as_text=True)


def test_macro_failure_via_raising_fetch(macro_client):
    def _boom():
        raise RuntimeError("macro provider timeout")

    mod = types.ModuleType("tools.trading.data.macro_data")
    mod.fetch_macro_context = _boom

    with patch.dict(sys.modules, {"tools.trading.data.macro_data": mod}):
        resp = macro_client.get("/api/macro/intelligence")

    assert resp.status_code == 503
    data = resp.get_json()
    assert data["status"] == "error"
    assert "macro provider timeout" in data["detail"]
    assert data["qeqt_phase"] is None


# ── healthy path → real badges pass through unchanged, HTTP 200 ───────────────

def test_macro_healthy_passes_through(macro_client):
    ctx = {
        "qeqt_phase": "EXPANDING",
        "credit_impulse": {"label": "ACCELERATING"},
        "regime": "EXPANSION",
        "macro_score": 72,
        "summary": "Liquidity expanding.",
        "fetched_at": "2026-01-02T03:04:05+00:00",
    }
    with patch.dict(sys.modules,
                    {"tools.trading.data.macro_data": _fake_macro_module(ctx)}):
        resp = macro_client.get("/api/macro/intelligence")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["qeqt_phase"] == "EXPANDING"
    assert data["credit_stress"] == "ACCELERATING"
    assert data["rotation_signal"] == "EXPANSION"
    assert data["macro_score"] == 72
    assert data.get("status") != "error"


# ── consumer source scan → renders the unavailable state, no NEUTRAL fall-through

@pytest.mark.parametrize(
    "template",
    [
        REPO_ROOT / "tools/dashboard/templates/analysis.html",
        REPO_ROOT / "icdev/tools/dashboard/templates/analysis.html",
    ],
)
def test_consumer_renders_unavailable_state(template):
    if not template.exists():  # pragma: no cover
        pytest.skip(f"template missing: {template}")
    src = template.read_text(encoding="utf-8")

    # The consumer must handle the outage explicitly.
    assert "Macro intelligence unavailable" in src
    # It must branch on the error signal (HTTP not-ok / status:"error").
    assert 'status === "error"' in src or "status === 'error'" in src
    assert "r.ok" in src
