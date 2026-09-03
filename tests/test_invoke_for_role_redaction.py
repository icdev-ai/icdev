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


def test_the_caller_gets_its_originals_back(router):
    """Round trip: surrogates out, originals restored on the response -- the
    same de-anonymisation `invoke` performs, so a CoD judge's verdict names the
    real contract, not [CONTRACT_1]."""
    resp = router.invoke_for_role("cod_judge", "rfi_writer_drafting", _req())
    assert EMAIL in resp.content and CONTRACT in resp.content, resp.content


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
    assert seen[0] == ["claude-sonnet"], (
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
def test_chain_orchestrator_egresses_only_through_invoke_for_role():
    """If a future edit gives ChainOrchestrator a direct provider call, this
    router-seam fix stops covering it. Pin the shape."""
    from tools.llm import chain_orchestrator

    src = inspect.getsource(chain_orchestrator)
    assert "router.invoke_for_role(" in src
    for forbidden in ("_provider_invoke(", "_invoke_model_direct(", "provider.complete(", "provider.invoke("):
        assert forbidden not in src, f"ChainOrchestrator bypasses the router door: {forbidden}"


def test_every_router_door_runs_the_redaction_gate():
    """The three public egress methods that reach a provider themselves must
    each call _pre_invoke_redaction. (invoke_chain_of_* delegate to the
    orchestrator, which comes back through invoke_for_role.)"""
    for door in ("invoke", "invoke_streaming", "invoke_for_role"):
        src = inspect.getsource(getattr(LLMRouter, door))
        assert "_pre_invoke_redaction(" in src, f"LLMRouter.{door} reaches a provider without the redaction gate"
