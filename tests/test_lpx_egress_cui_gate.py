# CUI // SP-CTI
"""lpx-egress-02 — the proxy must NEVER become a cloud path for classified content.

Proves, at two layers:

* the pure predicate ``proxy_gateway.proxy_egress_classification_block`` refuses
  CUI (and above) whenever the proxy WOULD carry a cloud provider, honours the
  ollama/bedrock LOCAL-ONLY carve-out, is fail-closed for unknown labels, and is
  configurable via ``ICDEV_LLM_PROXY_MAX_CLASSIFICATION``;
* the router's invoke-time egress gate (``_enforce_routing_policy``) turns that
  refusal into a ``ForceLocalViolation`` so a CUI request cannot silently traverse
  the proxy to a cloud provider — with the proxy OFF the gate is a no-op.

Shim-aware: patch via importlib module objects, not string-form.
"""

from __future__ import annotations

import importlib

import pytest

pg = importlib.import_module("tools.llm.proxy_gateway")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("ICDEV_LLM_PROXY_ENABLED", "ICDEV_LLM_PROXY_BASE_URL",
                "ICDEV_LLM_PROXY_VIRTUAL_KEY", "ICDEV_LLM_LOCAL_COPY",
                "ICDEV_LLM_PROXY_MAX_CLASSIFICATION"):
        monkeypatch.delenv(var, raising=False)
    yield


# ── Pure predicate ───────────────────────────────────────────────────────────

def test_gate_noop_when_proxy_off():
    cfg = {"type": "anthropic"}
    # Proxy off → no proxy path exists → gate is a no-op even for SECRET.
    assert pg.proxy_egress_classification_block(cfg, "SECRET") == ""


def test_cui_blocked_when_proxy_on(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    cfg = {"type": "anthropic"}
    reason = pg.proxy_egress_classification_block(cfg, "CUI")
    assert reason
    assert "proxy" in reason.lower()


def test_default_unlabelled_treated_as_cui_and_blocked(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    cfg = {"type": "openai"}
    assert pg.proxy_egress_classification_block(cfg, None)  # unlabelled → CUI → blocked


def test_public_allowed_when_proxy_on(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    cfg = {"type": "anthropic"}
    assert pg.proxy_egress_classification_block(cfg, "PUBLIC") == ""
    assert pg.proxy_egress_classification_block(cfg, "UNCLASSIFIED") == ""


def test_unknown_label_fails_closed(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    cfg = {"type": "anthropic"}
    assert pg.proxy_egress_classification_block(cfg, "ZZZ-GARBLED")


def test_banner_suffix_tolerated(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    cfg = {"type": "anthropic"}
    # "CUI // SP-CTI" is still CUI → blocked; "SECRET//NOFORN" → blocked.
    assert pg.proxy_egress_classification_block(cfg, "CUI // SP-CTI")
    assert pg.proxy_egress_classification_block(cfg, "SECRET//NOFORN")


def test_local_and_govcloud_types_never_gated(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    for ptype in ("ollama", "bedrock"):
        # These are never redirected to the proxy, so the gate never fires.
        assert pg.proxy_egress_classification_block({"type": ptype}, "SECRET") == ""


def test_ceiling_is_configurable(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    monkeypatch.setenv("ICDEV_LLM_PROXY_MAX_CLASSIFICATION", "CUI")
    cfg = {"type": "anthropic"}
    # Operator raised the ceiling to CUI → CUI now permitted, but ECI+ still blocked.
    assert pg.proxy_egress_classification_block(cfg, "CUI") == ""
    assert pg.proxy_egress_classification_block(cfg, "SECRET")


def test_local_copy_mode_also_gated(monkeypatch):
    # local-copy redirects even without ICDEV_LLM_PROXY_ENABLED; the gate applies.
    monkeypatch.setenv("ICDEV_LLM_LOCAL_COPY", "true")
    assert pg.proxy_egress_classification_block({"type": "anthropic"}, "CUI")


def test_bad_ceiling_value_fails_closed(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    monkeypatch.setenv("ICDEV_LLM_PROXY_MAX_CLASSIFICATION", "not-a-level")
    # Unrecognised ceiling → default (UNCLASSIFIED) → CUI blocked.
    assert pg.proxy_egress_classification_block({"type": "anthropic"}, "CUI")


# ── Router invoke-time enforcement ───────────────────────────────────────────

def _make_request(classification):
    from tools.llm.provider import LLMRequest

    return LLMRequest(messages=[{"role": "user", "content": "hi"}],
                      classification=classification)


def test_router_refuses_cui_over_proxy(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    from tools.llm.router import LLMRouter, ForceLocalViolation

    router = LLMRouter()
    req = _make_request("CUI")
    model_cfg = {"provider": "anthropic", "model_id": "claude-x"}
    with pytest.raises(ForceLocalViolation) as exc:
        router._enforce_routing_policy("code_generation", req, "claude-x", model_cfg)
    assert "proxy" in str(exc.value).lower()


class _CloudDecision:
    """Stub RoutingDecision: general policy permits cloud (isolates the proxy gate
    from airgap auto-detection / threshold variability in CI)."""
    local_only = False
    reason = "test: cloud permitted"
    rule = "default"


def _force_cloud_policy(monkeypatch):
    rp = importlib.import_module("tools.llm.routing_policy")
    monkeypatch.setattr(rp, "resolve", lambda *a, **k: _CloudDecision(), raising=True)


def test_router_allows_public_over_proxy_when_policy_permits(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    _force_cloud_policy(monkeypatch)
    from tools.llm.router import LLMRouter

    router = LLMRouter()
    req = _make_request("PUBLIC")
    model_cfg = {"provider": "anthropic", "model_id": "claude-x"}
    # PUBLIC is below the proxy ceiling → the proxy egress gate does not refuse.
    router._enforce_routing_policy("code_generation", req, "claude-x", model_cfg)


def test_router_noop_when_proxy_off(monkeypatch):
    for var in ("ICDEV_LLM_PROXY_ENABLED", "ICDEV_LLM_LOCAL_COPY"):
        monkeypatch.delenv(var, raising=False)
    _force_cloud_policy(monkeypatch)
    from tools.llm.router import LLMRouter

    router = LLMRouter()
    req = _make_request("CUI")
    model_cfg = {"provider": "anthropic", "model_id": "claude-x"}
    # Proxy off: the CUI proxy gate must not fire even for CUI content.
    router._enforce_routing_policy("code_generation", req, "claude-x", model_cfg)
