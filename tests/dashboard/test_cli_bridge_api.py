# CUI // SP-CTI
"""Tests for the whole-page CLI bridge toggle middleware (ucb-be-04).

Covers:
- parse_toggle tri-state mapping
- apply_cli_bridge_override chain rewriting (None / True / False)
- cli_bridge_enabled() honoring the context override over ICDEV_CLI_BRIDGE
- the Flask before_request/teardown_request hooks: a request carrying
  cookie icdev_cli_bridge=off bypasses the bridge for every LLMRouter.invoke()
  during that request, and the override does NOT leak into the next request.
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from flask import Flask, jsonify

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.dashboard.api.cli_bridge_api import (  # noqa: E402
    COOKIE_NAME,
    HEADER_NAME,
    parse_toggle,
    register_cli_bridge,
    run_cli_bridge_prompt,
)
from tools.llm.cli_bridge.activate import (  # noqa: E402
    CLI_MODEL_NAME,
    apply_cli_bridge_override,
    cli_bridge_enabled,
    cli_bridge_override,
    get_cli_bridge_override,
    reset_cli_bridge_override,
)


# ────────────────────────────────────────────────────────────────────────────
# parse_toggle
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["on", "true", "1", "yes", "ON", " True "])
def test_parse_toggle_truthy(raw):
    assert parse_toggle(raw) is True


@pytest.mark.parametrize("raw", ["off", "false", "0", "no", "OFF", " False "])
def test_parse_toggle_falsey(raw):
    assert parse_toggle(raw) is False


@pytest.mark.parametrize("raw", [None, "", "  ", "maybe", "auto"])
def test_parse_toggle_none(raw):
    assert parse_toggle(raw) is None


# ────────────────────────────────────────────────────────────────────────────
# apply_cli_bridge_override
# ────────────────────────────────────────────────────────────────────────────


def test_apply_override_none_is_passthrough():
    chain = [CLI_MODEL_NAME, "gpt-4o", "claude-3-5-sonnet"]
    token = cli_bridge_override(None)
    try:
        out = apply_cli_bridge_override(chain)
        assert out == chain
        assert out is not chain  # returns a copy, never mutates input
    finally:
        reset_cli_bridge_override(token)


def test_apply_override_false_strips_cli_model():
    chain = [CLI_MODEL_NAME, "gpt-4o", "claude-3-5-sonnet"]
    token = cli_bridge_override(False)
    try:
        out = apply_cli_bridge_override(chain)
        assert CLI_MODEL_NAME not in out
        assert out == ["gpt-4o", "claude-3-5-sonnet"]
    finally:
        reset_cli_bridge_override(token)


def test_apply_override_true_prepends_cli_model():
    chain = ["gpt-4o", "claude-3-5-sonnet"]
    token = cli_bridge_override(True)
    try:
        out = apply_cli_bridge_override(chain)
        assert out[0] == CLI_MODEL_NAME
        assert out == [CLI_MODEL_NAME, "gpt-4o", "claude-3-5-sonnet"]
    finally:
        reset_cli_bridge_override(token)


def test_apply_override_true_idempotent_when_already_first():
    chain = [CLI_MODEL_NAME, "gpt-4o"]
    token = cli_bridge_override(True)
    try:
        assert apply_cli_bridge_override(chain) == [CLI_MODEL_NAME, "gpt-4o"]
    finally:
        reset_cli_bridge_override(token)


# ────────────────────────────────────────────────────────────────────────────
# cli_bridge_enabled override precedence
# ────────────────────────────────────────────────────────────────────────────


def test_override_beats_env_force_enable(monkeypatch):
    """Override=False bypasses the bridge even with ICDEV_CLI_BRIDGE=1."""
    monkeypatch.setenv("ICDEV_CLI_BRIDGE", "1")
    assert cli_bridge_enabled() is True  # env force-enable, no override
    token = cli_bridge_override(False)
    try:
        assert cli_bridge_enabled() is False
    finally:
        reset_cli_bridge_override(token)
    assert cli_bridge_enabled() is True  # restored after reset


def test_override_beats_env_force_disable(monkeypatch):
    monkeypatch.setenv("ICDEV_CLI_BRIDGE", "0")
    assert cli_bridge_enabled() is False
    token = cli_bridge_override(True)
    try:
        assert cli_bridge_enabled() is True
    finally:
        reset_cli_bridge_override(token)


# ────────────────────────────────────────────────────────────────────────────
# Flask middleware — request-scoped, no leakage
# ────────────────────────────────────────────────────────────────────────────


def _make_app(monkeypatch):
    """Minimal Flask app with the hook + a temporary probe route.

    The probe route reports, from inside the request, what the CLI bridge
    sees: the override value, the resolved enabled flag, and the chain that
    LLMRouter.invoke() would walk (claude-cli stripped/kept accordingly).
    """
    monkeypatch.setenv("ICDEV_CLI_BRIDGE", "1")  # bridge force-ENABLED globally
    app = Flask(__name__)
    register_cli_bridge(app)

    @app.route("/_probe")
    def _probe():
        base_chain = [CLI_MODEL_NAME, "gpt-4o", "claude-3-5-sonnet"]
        return jsonify(
            override=get_cli_bridge_override(),
            enabled=cli_bridge_enabled(),
            chain=apply_cli_bridge_override(base_chain),
        )

    return app


def test_cookie_off_bypasses_bridge_for_request(monkeypatch):
    app = _make_app(monkeypatch)
    client = app.test_client()
    client.set_cookie(COOKIE_NAME, "off", domain="localhost")
    resp = client.get("/_probe")
    data = resp.get_json()
    assert data["override"] is False
    assert data["enabled"] is False  # bypassed despite ICDEV_CLI_BRIDGE=1
    assert CLI_MODEL_NAME not in data["chain"]


def test_header_overrides_cookie(monkeypatch):
    app = _make_app(monkeypatch)
    client = app.test_client()
    client.set_cookie(COOKIE_NAME, "off", domain="localhost")
    # Header says ON → wins over the cookie's OFF.
    resp = client.get("/_probe", headers={HEADER_NAME: "on"})
    data = resp.get_json()
    assert data["override"] is True
    assert data["enabled"] is True
    assert data["chain"][0] == CLI_MODEL_NAME


def test_no_cookie_defers_to_env(monkeypatch):
    app = _make_app(monkeypatch)
    client = app.test_client()
    resp = client.get("/_probe")
    data = resp.get_json()
    assert data["override"] is None  # no toggle → no override
    assert data["enabled"] is True  # defers to ICDEV_CLI_BRIDGE=1
    assert data["chain"][0] == CLI_MODEL_NAME


def test_override_does_not_leak_to_next_request(monkeypatch):
    app = _make_app(monkeypatch)
    client = app.test_client()

    # First request: cookie off → bypassed.
    client.set_cookie(COOKIE_NAME, "off", domain="localhost")
    first = client.get("/_probe").get_json()
    assert first["override"] is False

    # Second request on the SAME app/thread, no cookie → state must reset.
    client.delete_cookie(COOKIE_NAME, domain="localhost")
    second = client.get("/_probe").get_json()
    assert second["override"] is None
    assert second["enabled"] is True
    assert second["chain"][0] == CLI_MODEL_NAME

    # And the module-level ContextVar is clean outside any request.
    assert get_cli_bridge_override() is None


# ────────────────────────────────────────────────────────────────────────────
# run_cli_bridge_prompt — interactive prompt panel endpoint (ucb-widget-02)
# ────────────────────────────────────────────────────────────────────────────


def test_prompt_empty_returns_error():
    out = run_cli_bridge_prompt({"prompt": "   "})
    assert out["error"]
    assert out["content"] == ""


class _FakeResp:
    content = "  Hello from the bridge.  "
    provider = "anthropic"
    model_id = "claude-cli"
    duration_ms = 1234


def _set_router(monkeypatch, cls):
    """Patch LLMRouter on every alias of the router module.

    ``tools.llm.router`` and ``icdev.tools.llm.router`` are distinct module
    objects under the back-compat shim, and a from-import inside the endpoint
    resolves to the icdev one — so we patch both to be safe.
    """
    import importlib

    for name in ("tools.llm.router", "icdev.tools.llm.router"):
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        monkeypatch.setattr(mod, "LLMRouter", cls, raising=False)


def _patch_router(monkeypatch, captured):
    """Patch LLMRouter so invoke() records args and returns a fake response."""

    class _FakeRouter:
        def __init__(self, *a, **k):
            pass

        def invoke(self, function, request):
            captured["function"] = function
            captured["override"] = get_cli_bridge_override()
            return _FakeResp()

    _set_router(monkeypatch, _FakeRouter)


def test_prompt_success_returns_provider_footer_fields(monkeypatch):
    captured: dict = {}
    _patch_router(monkeypatch, captured)
    out = run_cli_bridge_prompt({"prompt": "what does this page do?"})
    assert out["content"] == "Hello from the bridge."  # stripped
    assert out["provider"] == "anthropic"
    assert out["model"] == "claude-cli"
    assert out["duration_ms"] == 1234
    # Empty function defaults to the codebase_query routing chain.
    assert captured["function"] == "codebase_query"
    # No force_bridge → no override seeded during invoke.
    assert captured["override"] is None


def test_prompt_force_bridge_seeds_override_during_invoke(monkeypatch):
    captured: dict = {}
    _patch_router(monkeypatch, captured)
    run_cli_bridge_prompt({"prompt": "hi", "function": "pulse", "force_bridge": "off"})
    # The override is visible to invoke()…
    assert captured["override"] is False
    assert captured["function"] == "pulse"
    # …and is reset once the call returns (no leak).
    assert get_cli_bridge_override() is None


def test_prompt_force_bridge_accepts_bool(monkeypatch):
    captured: dict = {}
    _patch_router(monkeypatch, captured)
    run_cli_bridge_prompt({"prompt": "hi", "force_bridge": True})
    assert captured["override"] is True
    assert get_cli_bridge_override() is None


def test_prompt_router_failure_degrades_gracefully(monkeypatch):
    class _BoomRouter:
        def __init__(self, *a, **k):
            pass

        def invoke(self, function, request):
            raise RuntimeError("no provider available")

    _set_router(monkeypatch, _BoomRouter)
    out = run_cli_bridge_prompt({"prompt": "hi", "force_bridge": True})
    assert out["error"]
    assert out["content"] == ""
    # Override must still be cleaned up even when invoke raises.
    assert get_cli_bridge_override() is None
