#!/usr/bin/env python3
# CUI // SP-CTI
"""Governed OpenAI-compatible endpoint on the SaaS gateway (owui-api-01).

``tools/saas/openai_compat.py`` must answer OpenAI clients ONLY through
``LLMRouter.invoke``. These tests stub the router so they assert what the
blueprint asks of it (function, classification, tenant) and how it maps the
router's refusals onto OpenAI error bodies -- not what a provider says.

As in ``tests/test_exa_policy_05_saas_mcp_authz.py``, the principal is set by a
stub ``before_request``; one test registers the REAL auth middleware to prove
the route is not public.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Flask, g, request  # noqa: E402

from tools.llm.provider import LLMResponse  # noqa: E402
from tools.llm.router import (  # noqa: E402
    ForceLocalViolation,
    LLMUnavailableError,
    RedactionUnavailableError,
)
from tools.saas import openai_compat  # noqa: E402


class FakeRouter:
    """Records each invoke; returns ``reply`` or raises ``error``."""

    def __init__(self, reply="governed answer", error=None):
        self.calls = []
        self.reply = reply
        self.error = error
        # The routing table the real router would load from the repo config.
        self._config = yaml.safe_load(
            (BASE_DIR / "args" / "llm_config.yaml").read_text(encoding="utf-8"))

    def invoke(self, function, req):
        self.calls.append((function, req))
        if self.error is not None:
            raise self.error
        return LLMResponse(content=self.reply, model_id="qwen3.5:latest", provider="ollama",
                           input_tokens=11, output_tokens=7, classification=req.classification)


@pytest.fixture
def router(monkeypatch):
    fake = FakeRouter()
    monkeypatch.setattr(openai_compat, "_router", lambda: fake)
    return fake


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.before_request
    def _set_principal():
        g.tenant_id = request.headers.get("X-Test-Tenant", "tenant-1")
        g.user_id = "user-1"

    app.register_blueprint(openai_compat.openai_bp)
    return app.test_client()


def _chat(client, model="icdev/general", headers=None, **extra):
    body = {"model": model, "messages": [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
    ], **extra}
    return client.post("/v1/chat/completions", json=body, headers=headers or {})


# ---------------------------------------------------------------------------
# Config contract: every capability is a DECLARED router function
# ---------------------------------------------------------------------------
def test_every_capability_maps_to_a_declared_router_function():
    cfg = openai_compat.load_config()
    routing = yaml.safe_load((BASE_DIR / "args" / "llm_config.yaml").read_text(encoding="utf-8"))["routing"]
    models = cfg["models"]
    assert models, "openai_compat.yaml exposes no models"
    for m in models:
        assert m["llm_function"] in routing, (
            f"{m['id']} -> {m['llm_function']} is undeclared and would silently use routing.default")


def test_local_only_capability_is_force_local():
    cfg = openai_compat.load_config()
    routing = yaml.safe_load((BASE_DIR / "args" / "llm_config.yaml").read_text(encoding="utf-8"))["routing"]
    local = next(m for m in cfg["models"] if m["id"] == "icdev/local-only")
    assert routing[local["llm_function"]].get("force_local") is True


# ---------------------------------------------------------------------------
# /v1/models
# ---------------------------------------------------------------------------
def test_models_lists_only_configured_capabilities(client, router):
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.get_json()["data"]]
    assert ids == [m["id"] for m in openai_compat.load_config()["models"]]
    assert all(i.startswith("icdev/") for i in ids)


# ---------------------------------------------------------------------------
# /v1/chat/completions
# ---------------------------------------------------------------------------
def test_completion_goes_through_router_invoke(client, router):
    resp = _chat(client)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "icdev/general"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "governed answer"}
    assert body["usage"] == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}

    (function, req), = router.calls
    assert function == "openai_compat_general"
    assert req.system_prompt == "be brief"
    assert req.messages == [{"role": "user", "content": "hello"}]
    assert req.tenant_id == "tenant-1"
    assert req.classification == "CUI"  # config default, not the router's silently
    assert resp.headers["X-ICDEV-Classification"] == "CUI"


def test_classification_header_reaches_the_router(client, router):
    resp = _chat(client, model="icdev/local-only", headers={"X-ICDEV-Classification": "secret"})
    assert resp.status_code == 200
    (function, req), = router.calls
    assert function == "openai_compat_local"
    assert req.classification == "SECRET"


def test_unknown_classification_is_refused_not_downgraded(client, router):
    resp = _chat(client, headers={"X-ICDEV-Classification": "TOP-SECRET-ISH"})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "bad_classification"
    assert router.calls == []


def test_stream_returns_sse_ending_in_done(client, router):
    resp = _chat(client, stream=True)
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    frames = [f for f in resp.get_data(as_text=True).split("\n\n") if f]
    assert frames[-1] == "data: [DONE]"
    first = json.loads(frames[0][len("data: "):])
    assert first["object"] == "chat.completion.chunk"
    assert first["choices"][0]["delta"]["content"] == "governed answer"


def test_unknown_model_is_404(client, router):
    resp = _chat(client, model="gpt-4o")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "model_not_found"
    assert router.calls == []


def test_native_tools_are_refused_not_ignored(client, router):
    resp = _chat(client, tools=[{"type": "function", "function": {"name": "x"}}])
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "tools_unsupported"
    assert router.calls == []


def test_max_tokens_is_capped(client, router):
    _chat(client, max_tokens=10_000_000)
    (_, req), = router.calls
    assert req.max_tokens == openai_compat.load_config()["max_tokens_cap"]


@pytest.mark.parametrize("error, status, code", [
    (ForceLocalViolation("cloud egress refused"), 403, "routing_policy"),
    (RedactionUnavailableError("sanitizer down"), 503, "redaction_unavailable"),
    (LLMUnavailableError("no providers"), 503, "llm_unavailable"),
])
def test_router_refusals_map_to_openai_errors(client, router, error, status, code):
    router.error = error
    resp = _chat(client)
    assert resp.status_code == status
    assert resp.get_json()["error"]["code"] == code


def test_capability_undeclared_in_live_config_is_hidden_and_refused(client, router):
    # ICDEV_LLM_CONFIG can point the router at a file without these routes; the
    # router would then serve the cloud-first routing.default. Fail closed instead.
    del router._config["routing"]["openai_compat_local"]
    ids = [m["id"] for m in client.get("/v1/models").get_json()["data"]]
    assert "icdev/local-only" not in ids
    resp = _chat(client, model="icdev/local-only")
    assert resp.status_code == 503
    assert resp.get_json()["error"]["code"] == "capability_unrouted"
    assert router.calls == []


def test_missing_principal_is_refused(router):
    app = Flask(__name__)
    app.register_blueprint(openai_compat.openai_bp)
    c = app.test_client()
    assert c.get("/v1/models").status_code == 401
    assert _chat(c).status_code == 401
    assert router.calls == []


def test_real_auth_middleware_guards_v1(monkeypatch, router):
    from tools.saas.auth import middleware

    monkeypatch.setattr(middleware, "_log_auth_event", lambda *a, **k: None)
    app = Flask(__name__)
    middleware.register_auth_middleware(app)
    app.register_blueprint(openai_compat.openai_bp)
    resp = app.test_client().post("/v1/chat/completions", json={"model": "icdev/general"})
    assert resp.status_code == 401
    assert router.calls == []


def test_gateway_registers_the_blueprint():
    src = (BASE_DIR / "tools" / "saas" / "api_gateway.py").read_text(encoding="utf-8")
    assert "from tools.saas.openai_compat import openai_bp" in src
