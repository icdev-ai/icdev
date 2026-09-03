# CUI // SP-CTI
"""Every door out of the router redacts -- invoke_for_role included.

THE GAP. ``LLMRouter._pre_invoke_redaction`` (D-RDT-4/5, trust-mask-01) ran
from ``invoke`` and ``invoke_streaming`` and from nowhere else. ``invoke_for_role``
-- the door every Chain-of-Debate / Chain-of-Thought / council role call leaves
through (``ChainOrchestrator`` calls nothing else) -- went straight to
``_provider_invoke`` with the caller's raw text. So ``rfi_workbench._generate_draft``
sent the JUDGMENT sections of an RFI draft to the provider unredacted while the
single-shot sections next to them were masked. The whitepaper TRUST-rails card
described this as "the sanitizer is wired only into response_drafter"; the truth
was one level down, in the router, where a second door had no gate.

These tests use a config-file router (no live provider) and the real
GovConSanitizer, whose regex/deny-list detectors mask an email and a contract
number without the NER backend (tests/test_redaction_scope.py relies on the
same). ``_provider_invoke`` is replaced by a recorder, so what it "sends" is
exactly what a provider would have received.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.llm import router as router_mod  # noqa: E402
from tools.llm.provider import LLMRequest  # noqa: E402
from tools.llm.router import LLMRouter  # noqa: E402

# Shaped to trip the regex/deny-list detectors with no NER backend in CI.
EMAIL = "john.smith@example-agency.gov"
CONTRACT = "W91CRB-24-C-0001"
SAMPLE_CUI_TEXT = f"Please contact John Smith at {EMAIL} regarding Contract {CONTRACT}."

PROVIDERS = {
    "ollama": {"type": "ollama", "base_url": "http://localhost:11434"},
    "anthropic": {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
}
MODELS = {
    "qwen3-local": {"provider": "ollama", "model_id": "qwen3.5:latest"},
    "claude-sonnet": {"provider": "anthropic", "model_id": "claude-sonnet-4"},
}
ROUTING = {
    # the FUNCTION chain is local; the ROLE chain is cloud -- the shape that
    # makes "judge locality on the function's chain" a false skip
    "rfi_writer_drafting": {"chain": ["qwen3-local"]},
    "document_qna": {"chain": ["claude-sonnet"]},
    "pulse_draft": {"chain": ["claude-sonnet"]},
    "cod_judge": {"chain": ["claude-sonnet"]},
    "cot_reasoner": {"chain": ["qwen3-local"]},
}


@pytest.fixture
def router(tmp_path, monkeypatch):
    cfg = {"providers": PROVIDERS, "models": MODELS, "routing": ROUTING, "settings": {}}
    path = tmp_path / "llm_config.yaml"
    path.write_text(yaml.dump(cfg), encoding="utf-8")
    r = LLMRouter(config_path=str(path))
    sent: list = []

    def _provider_invoke(provider, request, model_id, model_cfg, function=""):
        content = request.messages[0]["content"]
        sent.append({"function": function, "model_id": model_id, "content": content})
        # echo what it received, so de-anonymisation has something to restore
        return SimpleNamespace(content=f"ack: {content}", text=None)

    monkeypatch.setattr(r, "_check_model_available", lambda name: True)
    monkeypatch.setattr(r, "_get_provider", lambda name: object())
    monkeypatch.setattr(r, "_provider_invoke", _provider_invoke)
    r._sent = sent
    return r


def _req():
    return LLMRequest(messages=[{"role": "user", "content": SAMPLE_CUI_TEXT}], max_tokens=64)


# --------------------------------------------------------------------------- #
# 1. THE GAP: a role call carries no raw PII / contract number to the provider
# --------------------------------------------------------------------------- #
def test_invoke_for_role_redacts_before_the_provider_sees_it(router):
    router.invoke_for_role("cod_judge", "rfi_writer_drafting", _req())
    assert router._sent, "the fake provider was never reached"
    sent = router._sent[0]["content"]
    assert sent != SAMPLE_CUI_TEXT, "invoke_for_role sent the caller's raw text"
    assert EMAIL not in sent, f"email reached the provider: {sent!r}"
    assert CONTRACT not in sent, f"contract number reached the provider: {sent!r}"


def test_the_response_goes_back_through_deanonymisation(router, monkeypatch):
    """The same round trip `invoke` performs. The regex detectors emit
    REDACTION tokens ([EMAIL_ADDRESS], [DOD_CONTRACT]) which are not
    reversible, so what is pinned is the WIRING: the response is handed to
    _post_invoke_deanonymize with the session the gate opened."""
    handed: list = []
    original = router._post_invoke_deanonymize

    def _recorder(response, session):
        handed.append(session)
        return original(response, session)

    monkeypatch.setattr(router, "_post_invoke_deanonymize", _recorder)
    router.invoke_for_role("cod_judge", "rfi_writer_drafting", _req())
    assert handed and handed[0], "response returned without passing the de-anonymisation seam"


def test_the_legacy_direct_door_redacts_when_it_is_the_entry(router):
    """ChainOrchestrator's second door: a DIRECT model name (not a routing key)
    calls router._invoke_model_direct. It had no gate either."""
    resp = router._invoke_model_direct("claude-sonnet", _req(), function="rfi_writer_drafting")
    assert resp is not None and router._sent
    sent = router._sent[0]["content"]
    assert EMAIL not in sent and CONTRACT not in sent, sent


def test_a_request_invoke_already_redacted_is_not_redacted_twice(router, monkeypatch):
    """`invoke` hands an already-redacted request to two-tier, which calls
    _invoke_model_direct. One gate, one audit row: the mark on the request
    is what stops the second door redacting again."""
    calls: list = []
    original = router._pre_invoke_redaction

    def _recorder(function, request, **kw):
        calls.append(function)
        return original(function, request, **kw)

    monkeypatch.setattr(router, "_pre_invoke_redaction", _recorder)
    req = _req()
    router.invoke_for_role("cod_judge", "rfi_writer_drafting", req)
    assert getattr(req, "_redaction_session", None), "the gate did not mark the request it redacted"
    router._invoke_model_direct("claude-sonnet", req, function="rfi_writer_drafting")
    assert len(calls) == 1, f"redacted {len(calls)} times for one request"


# --------------------------------------------------------------------------- #
# 2. locality is judged on the chain that EGRESSES -- the role's, not the function's
# --------------------------------------------------------------------------- #
def test_local_only_skip_is_judged_on_the_role_chain(router, monkeypatch):
    seen: list = []
    original = router_mod._chain_is_local_only

    def _recorder(chain, models, providers):
        seen.append(list(chain))
        return original(chain, models, providers)

    monkeypatch.setattr(router_mod, "_chain_is_local_only", _recorder)
    router.invoke_for_role("cod_judge", "document_qna", _req())
    assert seen, "locality was never assessed"
    # The CLI bridge may prepend its own model to any chain on a host where it
    # is armed, so membership is asserted, not equality.
    assert "claude-sonnet" in seen[0] and "qwen3-local" not in seen[0], (
        f"locality judged on {seen[0]} -- the FUNCTION chain -- while the bytes "
        "left through the cod_judge chain"
    )


# --------------------------------------------------------------------------- #
# 3. it is the SAME gate, not a second sanitizer: the skip rules carry over
# --------------------------------------------------------------------------- #
def test_an_excluded_function_is_not_redacted_on_this_door_either(router, monkeypatch):
    """`excluded_functions` (D-RDT-5: Pulse posts are public, surrogates leak into
    published prose) must mean the same thing at every door."""
    router._config.setdefault("redaction", {})["excluded_functions"] = ["pulse_draft"]
    router.invoke_for_role("cod_judge", "pulse_draft", _req())
    assert router._sent[0]["content"] == SAMPLE_CUI_TEXT


# --------------------------------------------------------------------------- #
# 4. structural: the orchestrator has no other way out
# --------------------------------------------------------------------------- #
def test_chain_orchestrator_egresses_only_through_gated_router_doors():
    """ChainOrchestrator has exactly two ways out -- invoke_for_role for a
    routing key, _invoke_model_direct for a direct model name -- and both are
    gated. If a future edit gives it a provider call of its own, the
    router-seam fix stops covering it. Pin the shape."""
    from tools.llm import chain_orchestrator

    src = inspect.getsource(chain_orchestrator)
    assert "router.invoke_for_role(" in src and "router._invoke_model_direct(" in src
    for forbidden in ("_provider_invoke(", "provider.complete(", "provider.invoke(", "_get_provider("):
        assert forbidden not in src, f"ChainOrchestrator bypasses the router doors: {forbidden}"


def test_every_router_door_runs_the_redaction_gate():
    """Every method that reaches _provider_invoke itself must call
    _pre_invoke_redaction. (invoke_chain_of_* delegate to the orchestrator,
    which comes back through the last two.)"""
    for door in ("invoke", "invoke_streaming", "invoke_for_role", "_invoke_model_direct"):
        src = inspect.getsource(getattr(LLMRouter, door))
        assert "_pre_invoke_redaction(" in src, f"LLMRouter.{door} reaches a provider without the redaction gate"
