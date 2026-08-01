#!/usr/bin/env python3
# CUI // SP-CTI
"""prem-p0-05 (enforcement half) — does the provider ACTUALLY never get called?

test_routing_policy.py proves the resolver returns the right DECISION. That is not
the same as proving no bytes leave. These tests assert on the thing that actually
matters: a spy in the provider's seat, and `provider.invoke` was never called.

The three paths below all reach a provider, and each one bypasses a different
guard, which is why the enforcement lives at the provider call rather than in
chain selection:

  * ``invoke``              — the ordinary chain walk.
  * ``_invoke_model_direct``— two_tier AND ChainOrchestrator (CoT/CoD). Consults no
                              chain at all, and used to SWALLOW exceptions into
                              ``return None`` — turning a security refusal into a
                              "try the next model" fallback.
  * ``invoke_streaming``    — a completely separate chokepoint from _provider_invoke.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.llm.provider import LLMRequest  # noqa: E402
from tools.llm.router import ForceLocalViolation, LLMRouter  # noqa: E402

PROVIDERS = {
    "ollama": {"type": "ollama", "base_url": "http://localhost:11434"},
    "anthropic": {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
}
MODELS = {
    "qwen3-local": {"provider": "ollama", "model_id": "qwen3:latest"},
    "claude-sonnet": {"provider": "anthropic", "model_id": "claude-sonnet-4"},
}


def _router(tmp_path, routing) -> LLMRouter:
    cfg = {
        "providers": PROVIDERS,
        "models": MODELS,
        "routing": routing,
        "routing_policy": {"enabled": True, "local_threshold": "SECRET", "aggregation_guard": True},
        "two_tier": {"enabled": False},
    }
    p = tmp_path / "llm_config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return LLMRouter(config_path=str(p))


@pytest.fixture(autouse=True)
def _not_airgapped():
    with patch("tools.llm.routing_policy._is_airgap", return_value=False):
        yield


@pytest.fixture
def cloud_spy():
    """A provider that records any call. If this is ever called, CUI left the box."""
    spy = MagicMock()
    spy.invoke.return_value = MagicMock(content="LEAKED", model_id="claude-sonnet-4")
    return spy


def _secret() -> LLMRequest:
    return LLMRequest(messages=[{"role": "user", "content": "payload specs"}], classification="SECRET")


# ---------------------------------------------------------------------------


def test_chain_walk_never_calls_a_cloud_provider_with_secret_content(tmp_path, cloud_spy):
    """The chain is cloud-first. The content is SECRET. The cloud provider must not
    be invoked — and because no local model exists in this chain, the call must
    RAISE, not silently succeed against the cloud one."""
    r = _router(tmp_path, {"requirement_extraction": {"chain": ["claude-sonnet"]}})

    with patch.object(r, "_get_provider", return_value=cloud_spy):
        with pytest.raises(ForceLocalViolation) as ei:
            r.invoke("requirement_extraction", _secret())

    cloud_spy.invoke.assert_not_called()
    assert ei.value.rule == "declared_classification"


def test_chain_walk_falls_through_to_the_LOCAL_model_instead_of_the_cloud_one(tmp_path, cloud_spy):
    """The graceful path: a cloud-first chain with a local fallback should SERVE the
    request locally, not fail. Refusing the cloud model must not break the call."""
    r = _router(tmp_path, {"requirement_extraction": {"chain": ["claude-sonnet", "qwen3-local"]}})

    local = MagicMock()
    local.invoke.return_value = MagicMock(content="ok", model_id="qwen3:latest")

    def _provider_for(name):
        return cloud_spy if name == "anthropic" else local

    with patch.object(r, "_get_provider", side_effect=_provider_for):
        resp = r.invoke("requirement_extraction", _secret())

    cloud_spy.invoke.assert_not_called()   # the cloud model was skipped...
    local.invoke.assert_called_once()      # ...and the local one served it
    assert resp.content == "ok"


def test_invoke_model_direct_refusal_is_not_swallowed_into_a_fallback(tmp_path, cloud_spy):
    """The two_tier / CoT / CoD path.

    It consults no chain, and it used to catch Exception and `return None`, which
    would convert a security refusal into "this model failed, try another" — the
    router would then walk the chain hunting for a model that IS allowed to leak.
    """
    r = _router(tmp_path, {"default": {"chain": ["claude-sonnet"]}})

    with patch.object(r, "_get_provider", return_value=cloud_spy):
        with pytest.raises(ForceLocalViolation):
            r._invoke_model_direct("claude-sonnet", _secret(), function="requirement_extraction")

    cloud_spy.invoke.assert_not_called()


def test_streaming_is_guarded_too(tmp_path, cloud_spy):
    """Streaming is a SEPARATE chokepoint. Guarding only the non-streaming one
    would leave a fully-open cloud egress path for identical content."""
    r = _router(tmp_path, {"requirement_extraction": {"chain": ["claude-sonnet"]}})

    with patch.object(r, "_get_provider", return_value=cloud_spy):
        with pytest.raises(ForceLocalViolation):
            list(r.invoke_streaming("requirement_extraction", _secret()))

    cloud_spy.invoke.assert_not_called()
    cloud_spy.invoke_streaming.assert_not_called()


def test_ordinary_content_still_reaches_the_cloud(tmp_path, cloud_spy):
    """Guard against 'fixing' the leak by breaking the product.

    Unclassified content on a non-force_local function must still route to cloud —
    otherwise the policy is just an outage with good intentions.
    """
    r = _router(tmp_path, {"requirement_extraction": {"chain": ["claude-sonnet"]}})
    req = LLMRequest(messages=[{"role": "user", "content": "Summarise Section L."}], classification="PUBLIC")

    with patch.object(r, "_get_provider", return_value=cloud_spy):
        resp = r.invoke("requirement_extraction", req)

    cloud_spy.invoke.assert_called_once()
    assert resp.content == "LEAKED"  # i.e. it really did reach the provider
