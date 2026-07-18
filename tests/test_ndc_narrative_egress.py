# CUI // SP-CTI
"""Tests for the TFW narrative classification egress gate (ndc-gov-02).

Gov/DoD fail-closed posture: restricted classifications (CUI/IL4/IL5/IL6/SIPR)
may only egress narrative content (node/flow details) to a LOCAL/air-gapped LLM
provider. When the resolved provider for ``tfw_narrative`` is cloud-hosted under a
restricted classification, ``_invoke_llm`` must NOT call the router; the caller
falls back to the deterministic NARRATIVE_TEMPLATES path.

Covers:
  (a) restricted (SIPR/IL6) + cloud provider  -> router.invoke NOT called, None,
      warning logged;
  (b) restricted + local provider             -> router.invoke IS called;
  (c) NIPR (unrestricted)                      -> router.invoke called, unchanged;
  (d) router.invoke raising                    -> logged + template fallback, no crash.

The LLMRouter reference is monkeypatched at its module import site
(tools.network.narrative_generator.LLMRouter); the local/cloud decision uses the
REAL locality primitive over a realistic ``_config.providers`` on the fake router.
"""

from __future__ import annotations

import logging
import types
from unittest.mock import MagicMock

import pytest

import tools.network.narrative_generator as ng


# ── provider specs (mirror args/llm_config.yaml semantics) ────────────────────
# Local: ollama-type provider with NO api_key_env. Cloud: has api_key_env.
_LOCAL_PROVIDER = {"type": "ollama", "base_url": "http://localhost:11434"}
_CLOUD_PROVIDER = {
    "type": "openai_compatible",
    "api_key_env": "KIMI_API_KEY",
    "base_url": "https://api.moonshot.cn/v1",
}


class _Resp:
    """Minimal stand-in for LLMResponse (only ``.content`` is read)."""

    def __init__(self, content: str) -> None:
        self.content = content


def _fake_router(
    provider_name: str,
    provider_spec: dict,
    *,
    invoke_result=None,
    invoke_exc: Exception | None = None,
) -> types.SimpleNamespace:
    """Build a stand-in LLMRouter whose resolved tfw_narrative provider is
    ``provider_name`` (configured by ``provider_spec`` under _config.providers)."""
    invoke = MagicMock()
    if invoke_exc is not None:
        invoke.side_effect = invoke_exc
    else:
        invoke.return_value = invoke_result
    return types.SimpleNamespace(
        has_any_llm=lambda refresh=False: True,
        get_provider_for_function=lambda fn: (object(), "model-id", {"provider": provider_name}),
        invoke=invoke,
        _config={"providers": {provider_name: provider_spec}},
    )


@pytest.fixture
def _capture_ng_warnings(monkeypatch, caplog):
    """Enable propagation so caplog captures the (propagate=False) icdev logger."""
    monkeypatch.setattr(ng.logger, "propagate", True)
    caplog.set_level(logging.WARNING, logger=ng.logger.name)
    return caplog


# ── (a) restricted + cloud provider -> blocked ────────────────────────────────


@pytest.mark.parametrize("classification", ["SIPR", "IL6"])
def test_restricted_cloud_provider_blocks_egress(
    monkeypatch, _capture_ng_warnings, classification
):
    router = _fake_router("kimi", _CLOUD_PROVIDER, invoke_result=_Resp("LEAKED"))
    monkeypatch.setattr(ng, "LLMRouter", lambda *a, **k: router)

    out = ng._invoke_llm("sys prompt", "user content",
                         classification=classification, persona_id="seceng")

    assert out is None, "restricted + cloud must fall back (return None)"
    router.invoke.assert_not_called()
    blocked = [r for r in _capture_ng_warnings.records if "BLOCKED" in r.getMessage()]
    assert blocked, "expected a fail-closed BLOCKED warning"
    msg = blocked[0].getMessage()
    assert classification in msg and "seceng" in msg and "kimi" in msg


# ── (b) restricted + local provider -> router invoked ─────────────────────────


def test_restricted_local_provider_allows_egress(monkeypatch):
    router = _fake_router("ollama", _LOCAL_PROVIDER, invoke_result=_Resp("local narrative"))
    monkeypatch.setattr(ng, "LLMRouter", lambda *a, **k: router)

    out = ng._invoke_llm("sys prompt", "user content",
                         classification="IL5", persona_id="seceng")

    assert out == "local narrative"
    router.invoke.assert_called_once()
    assert router.invoke.call_args.args[0] == "tfw_narrative"


# ── (c) NIPR (unrestricted) -> byte-identical behavior, router invoked ────────


@pytest.mark.parametrize("classification", ["NIPR", "unclassified", ""])
def test_unrestricted_classification_invokes_router(monkeypatch, classification):
    # Even a cloud-resolved provider is permitted for unrestricted content.
    router = _fake_router("kimi", _CLOUD_PROVIDER, invoke_result=_Resp("cloud narrative"))
    monkeypatch.setattr(ng, "LLMRouter", lambda *a, **k: router)

    out = ng._invoke_llm("sys prompt", "user content",
                         classification=classification, persona_id="ciso")

    assert out == "cloud narrative"
    router.invoke.assert_called_once()
    assert router.invoke.call_args.args[0] == "tfw_narrative"


# ── (d) router raises -> logged, template fallback, no crash ───────────────────


def test_router_exception_is_logged_and_falls_back(monkeypatch, _capture_ng_warnings):
    router = _fake_router(
        "ollama", _LOCAL_PROVIDER, invoke_exc=RuntimeError("provider exploded")
    )
    monkeypatch.setattr(ng, "LLMRouter", lambda *a, **k: router)

    # Must not raise — returns None so callers use NARRATIVE_TEMPLATES.
    out = ng._invoke_llm("sys prompt", "user content",
                         classification="NIPR", persona_id="neteng")

    assert out is None
    failed = [r for r in _capture_ng_warnings.records
              if "tfw narrative LLM call failed" in r.getMessage()]
    assert failed, "router exception must be logged (silent swallow removed)"
    assert "provider exploded" in failed[0].getMessage()


def test_exception_fallback_reaches_templates(monkeypatch):
    """generate_for_persona still returns a deterministic narrative when the LLM
    call raises — end-to-end proof that availability is preserved."""
    router = _fake_router(
        "ollama", _LOCAL_PROVIDER, invoke_exc=RuntimeError("boom")
    )
    monkeypatch.setattr(ng, "LLMRouter", lambda *a, **k: router)

    step = {
        "step_number": 1,
        "node_id": "fw1",
        "node_label": "Firewall",
        "action_type": "authenticate",
        "security_detail": {},
        "network_detail": {},
    }
    node = {"id": "fw1", "type": "firewall", "label": "Firewall", "config": {}}
    flow = {"id": "f1", "classification": "IL5", "app_type": "web", "topology_id": "t1"}

    result = ng.generate_for_persona(
        step=step, node=node, persona_id="seceng", flow=flow, classification="IL5",
    )
    assert result["narrative"], "must fall back to a non-empty deterministic narrative"


# ── unit: restricted-set constant + predicate ─────────────────────────────────


def test_restricted_classification_predicate():
    for cls in ("CUI", "IL4", "IL5", "IL6", "SIPR", "il5", " sipr "):
        assert ng._is_restricted_classification(cls), cls
    for cls in ("NIPR", "unclassified", "", "PUBLIC", "nipr"):
        assert not ng._is_restricted_classification(cls), cls
