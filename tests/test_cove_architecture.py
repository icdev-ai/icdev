# CUI // SP-CTI
"""Tests for the Chain-of-Verification architecture (agx-verify-01).

The load-bearing property is verification INDEPENDENCE: the question-answering
step must not see the baseline, so the model cannot rubber-stamp its own draft.
``test_verifier_prompt_excludes_baseline`` asserts exactly that.
"""
from __future__ import annotations

import json

from tools.llm.architectures import registry
from tools.llm.architectures.cove import (
    VERDICT_VOCAB,
    chain_of_verification,
    classify_verdict,
    compose_verification,
)


# A distinctive baseline sentinel we can search for in captured prompts.
_BASELINE_SENTINEL = "ZEBRA_BASELINE_TOKEN_42 the sky is plaid on Tuesdays."


class _FakeResp:
    def __init__(self, content, model_id="fake-model"):
        self.content = content
        self.model_id = model_id


class _RecordingRouter:
    """Fake router that records every (function, prompt) it was asked to invoke."""

    def __init__(self, verdict="supported"):
        self.calls = []  # list of (function, prompt)
        self.verdict = verdict

    def invoke(self, function, request, **kwargs):
        prompt = ""
        for m in request.messages or []:
            if m.get("role") == "user":
                prompt = m["content"]
        self.calls.append((function, prompt))
        # Route by content of the prompt to a plausible structured reply.
        if "verification question" in prompt or '"questions"' in prompt:
            return _FakeResp(json.dumps({"questions": ["Is the sky plaid?", "Is it Tuesday?"]}))
        if '"verdict"' in prompt:
            return _FakeResp(json.dumps({"answer": "no", "verdict": self.verdict}))
        if "Revise the DRAFT" in prompt:
            return _FakeResp("REVISED: the sky is blue.")
        return _FakeResp(_BASELINE_SENTINEL)


# ── composition (deterministic-picker) ──────────────────────────────────────

def test_compose_all_supported_passes():
    d = compose_verification(["supported", "supported"])
    assert d["passed"] is True and d["needs_revision"] is False
    assert d["support_score"] == 1.0


def test_compose_any_contradicted_triggers_revision():
    d = compose_verification(["supported", "contradicted"])
    assert d["passed"] is False and d["needs_revision"] is True


def test_compose_unsupported_triggers_revision():
    d = compose_verification(["supported", "unsupported"])
    assert d["needs_revision"] is True


def test_classify_verdict_fails_closed_on_unknown():
    assert classify_verdict("banana") == "unsupported"
    assert classify_verdict("") == "unsupported"
    assert classify_verdict(" SUPPORTED ") == "supported"


def test_vocab_is_small_and_bounded():
    assert set(VERDICT_VOCAB.values()) <= {0.0, 0.5, 1.0}
    assert len(VERDICT_VOCAB) <= 5


# ── independence: the load-bearing property ─────────────────────────────────

def test_verifier_prompt_excludes_baseline():
    router = _RecordingRouter(verdict="supported")
    chain_of_verification(
        "Tell me about the sky.",
        router=router,
        baseline=_BASELINE_SENTINEL,
        max_questions=2,
    )
    verify_calls = [p for fn, p in router.calls if fn == "cove_verify"]
    assert verify_calls, "expected at least one independent verification call"
    for prompt in verify_calls:
        assert _BASELINE_SENTINEL not in prompt, (
            "verification step must be independent of the baseline"
        )


def test_verify_step_uses_cheap_verify_function():
    router = _RecordingRouter(verdict="supported")
    chain_of_verification("q", router=router, baseline=_BASELINE_SENTINEL, max_questions=2)
    functions_used = {fn for fn, _ in router.calls}
    assert "cove_verify" in functions_used


# ── end-to-end flow ─────────────────────────────────────────────────────────

def test_contradiction_produces_revised_output():
    router = _RecordingRouter(verdict="contradicted")
    result = chain_of_verification("q", router=router, baseline=_BASELINE_SENTINEL, max_questions=2)
    assert result.stop_reason == "revised"
    assert result.output.startswith("REVISED")
    assert result.metadata["decision"]["needs_revision"] is True


def test_all_supported_returns_verified_unrevised():
    router = _RecordingRouter(verdict="supported")
    result = chain_of_verification("q", router=router, baseline=_BASELINE_SENTINEL, max_questions=2)
    assert result.stop_reason == "verified"
    assert result.metadata["revised"] is False


def test_registered_in_registry():
    assert registry.is_registered("chain_of_verification")
    fn = registry.get("chain_of_verification")
    assert fn is chain_of_verification


def test_empty_baseline_degrades_honestly():
    router = _RecordingRouter()
    # A whitespace-only baseline must degrade honestly, not fake a pass.
    result2 = chain_of_verification("q", router=router, baseline="   ")
    assert result2.degraded is True and result2.stop_reason == "empty_baseline"


# ── guard integration ───────────────────────────────────────────────────────

def test_cove_guard_blocks_on_contradiction_and_force_overrides():
    from tools.quality.cove_guard import cove_guard
    router = _RecordingRouter(verdict="contradicted")
    text = "The system is authorized. [source: KB-1]"
    sources = {"KB-1": "The system authorization is pending."}
    blocked = cove_guard(text, available_sources=sources, router=router)
    assert blocked["blocked"] is True and blocked["needs_revision"] is True
    forced = cove_guard(text, available_sources=sources, router=router, force_override=True)
    assert forced["blocked"] is False and forced["forced"] is True


def test_cove_guard_passes_clean_text():
    from tools.quality.cove_guard import cove_guard
    router = _RecordingRouter(verdict="supported")
    out = cove_guard("A verified claim. [source: KB-1]",
                     available_sources={"KB-1": "evidence"}, router=router)
    assert out["passed"] is True and out["blocked"] is False
