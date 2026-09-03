# CUI // SP-CTI
"""rmf-wp-02 -- proposal prose passes through sanitize_for_llm on EVERY path.

THE CARD SAID `govcon_sanitizer.sanitize_for_llm` was wired only into
response_drafter. Half right. `LLMRouter.invoke` has run `_pre_invoke_redaction`
on every call since D-RDT-1 and `cortex.complete` reaches `invoke`, so the
single-shot drafting paths in rfi_workbench and doc_generator were covered.
What was NOT covered was `invoke_for_role` -- the method ChainOrchestrator
hands every Chain-of-Thought / Chain-of-Debate step to -- and the orchestrator's
legacy direct-model branch. rfi_workbench's `_generate_draft` (CoD for the
judgment sections) and doc_generator's `_cot_generate` / `_cod_compress` all go
that way, so a debater or a reasoner received the raw prompt.

These tests pin the seam, not the detector: a fake sanitizer proves the
provider RECEIVED the redacted text and the caller GOT the de-anonymized reply.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

router_mod = importlib.import_module("tools.llm.router")
provider_mod = importlib.import_module("tools.llm.provider")
LLMRouter = router_mod.LLMRouter
LLMRequest = provider_mod.LLMRequest
LLMResponse = provider_mod.LLMResponse

REPO_ROOT = Path(__file__).resolve().parents[2]

SECRET = "Alice Example"
SURROGATE = "[PERSON_1]"


class _FakeSanitizer:
    session_id = "sess-1"

    def __init__(self):
        self.seen: list[str] = []

    def sanitize_for_llm(self, text, function_name="", impact_level="IL4", is_local_only=False):
        self.seen.append(text)
        return text.replace(SECRET, SURROGATE), {"skipped": False, "is_local_only": is_local_only}

    def de_anonymize_response(self, text):
        return text.replace(SURROGATE, SECRET)


class _RL:
    def rank_models(self, key, chain):
        return chain

    def record_outcome(self, *a, **k):
        pass


@pytest.fixture()
def wired():
    """A router whose provider hop is a capture, and whose chain lookup records
    which KEY it was asked for."""
    r = LLMRouter()
    r._config = {"redaction": {"enabled": True, "fail_closed": True, "deanonymize_response": True},
                 "models": {}, "providers": {}}
    sanitizer = _FakeSanitizer()
    asked: list[str] = []
    received: list[str] = []

    r._get_sanitizer = lambda: sanitizer
    r._get_chain_for_function = lambda key: (asked.append(key) or ["m1"])
    r._get_rl_router = lambda: _RL()
    r._get_model_config = lambda name: {"provider": "p", "model_id": "mid"}
    r._check_model_available = lambda name: True
    r._get_provider = lambda name: object()
    r._log_telemetry = lambda *a, **k: None

    def _provider_invoke(provider, request, model_id, model_cfg, function=""):
        received.append(request.messages[0]["content"])
        return LLMResponse(content=f"Dear {SURROGATE}, noted.", model_id=model_id)

    r._provider_invoke = _provider_invoke
    r._sanitizer = sanitizer
    r._asked = asked
    r._received = received
    return r


def _req(text=f"Draft the past performance for {SECRET}."):
    return LLMRequest(messages=[{"role": "user", "content": text}], classification="IL4")


def test_invoke_for_role_redacts_before_the_provider_and_restores_after(wired):
    resp = wired.invoke_for_role("cot_reasoner", "rfi_writer_drafting", _req())
    assert wired._received == [f"Draft the past performance for {SURROGATE}."]
    assert SECRET not in wired._received[0]
    assert resp.content == f"Dear {SECRET}, noted."


def test_local_only_decision_reads_the_role_chain_not_the_function_chain(wired):
    wired.invoke_for_role("cod_judge", "proposal_drafting", _req())
    # The chain the request TRAVELS is the role's; the sanitizer's local-only
    # skip must be decided on that one. Asked once for routing, once for locality.
    assert wired._asked.count("cod_judge") >= 2
    assert "proposal_drafting" not in wired._asked


def test_fail_closed_blocks_a_role_invoke_when_the_sanitizer_is_missing(wired):
    wired._get_sanitizer = lambda: None
    with pytest.raises(router_mod.RedactionUnavailableError):
        wired.invoke_for_role("cot_reasoner", "rfi_writer_drafting", _req())
    assert wired._received == [], "nothing may reach the provider unredacted"


def test_chain_orchestrator_legacy_direct_branch_redacts_too(wired, monkeypatch):
    """A direct model name (no routing key) takes _invoke_model_direct; that
    branch now sanitizes first and de-anonymizes after, exactly once."""
    orch_mod = importlib.import_module("tools.llm.chain_orchestrator")
    wired._config["routing"] = {}
    wired._config["chain_orchestration"] = {}
    direct_seen: list[str] = []

    def _direct(model_name, request, function=""):
        direct_seen.append(request.messages[0]["content"])
        return LLMResponse(content=f"{SURROGATE} approved.", model_id="mid")

    wired._invoke_model_direct = _direct
    orch = orch_mod.ChainOrchestrator(router=wired)
    resp, _elapsed = orch._invoke_model("qwen3-local", _req(), "document_qna", timeout=30)
    assert direct_seen == [f"Draft the past performance for {SURROGATE}."]
    assert resp.content == f"{SECRET} approved."
    assert len(wired._sanitizer.seen) == 1


# ── every LLM dispatch in the two named modules reaches a redacting seam ──────

_GOVERNED = {
    # LLMRouter.invoke / invoke_streaming run _pre_invoke_redaction themselves.
    "invoke",
    # cortex.complete -> router.invoke.
    "complete",
    # ChainOrchestrator -> invoke_for_role (now redacting) / legacy branch (now redacting).
    "invoke_chain_of_thought", "invoke_chain_of_debate", "invoke_council",
}
_BYPASSES = {"_invoke_model_direct", "_provider_invoke", "_invoke_chain", "invoke_for_role"}


@pytest.mark.parametrize("relpath", [
    "tools/govcon/rfi_workbench.py",
    "tools/document_intelligence/doc_generator.py",
])
def test_named_modules_dispatch_only_through_redacting_seams(relpath):
    tree = ast.parse((REPO_ROOT / relpath).read_text(encoding="utf-8"))
    dispatches = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _GOVERNED | _BYPASSES:
                dispatches.append(node.func.attr)
    assert dispatches, f"{relpath} makes no LLM call at all?"
    assert not (set(dispatches) & _BYPASSES), sorted(set(dispatches) & _BYPASSES)


def test_invoke_for_role_source_carries_the_pre_and_post_hooks():
    """Structural belt for the behavioural test above: the pair is in the
    method body, so a refactor that drops one is visible without a router."""
    src = ast.get_source_segment(
        Path(router_mod.__file__).read_text(encoding="utf-8"),
        next(n for n in ast.walk(ast.parse(Path(router_mod.__file__).read_text(encoding="utf-8")))
             if isinstance(n, ast.FunctionDef) and n.name == "invoke_for_role"),
    )
    assert "_pre_invoke_redaction(" in src
    assert "_post_invoke_deanonymize(" in src
