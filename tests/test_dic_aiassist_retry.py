# CUI // SP-CTI
"""Tests: DIC AI-Assist resilience to transient empty LLM completions.

Cloud providers (e.g. Kimi) intermittently return an empty completion. A single
empty answer must NOT silently abstain a section — ``_llm_generate`` retries a
bounded number of times on *empty* output (but not on timeout, which would only
double the wait). Reproduces the observed "AI Assist → empty section" defect
where attempt 1 abstained and attempt 2 succeeded.
"""

import importlib

dg = importlib.import_module("tools.document_intelligence.doc_generator")
import icdev.tools.llm.router as _irouter  # patched import source used by _llm_generate


class _Resp:
    def __init__(self, content):
        self.content = content


def _fake_router_factory(sequence):
    """Build a fake LLMRouter class whose ``invoke`` yields ``sequence`` in order."""
    state = {"i": 0}

    class FakeRouter:
        def __init__(self, *a, **k):
            pass

        def is_no_llm_mode(self):
            return False

        def invoke(self, function, req):
            i = state["i"]
            state["i"] += 1
            return _Resp(sequence[min(i, len(sequence) - 1)])

    FakeRouter.calls = state
    return FakeRouter


def test_retries_on_empty_then_succeeds(monkeypatch):
    fake = _fake_router_factory(["", "Grounded prose [source: chunk 1]"])
    monkeypatch.setattr(_irouter, "LLMRouter", fake)
    monkeypatch.setattr(dg, "_LLM_EMPTY_RETRIES", 2)
    out = dg._llm_generate("write a section")
    assert out == "Grounded prose [source: chunk 1]"
    assert fake.calls["i"] == 2  # one empty, then a successful retry


def test_all_empty_returns_none_after_bounded_retries(monkeypatch):
    fake = _fake_router_factory(["", "", "", ""])
    monkeypatch.setattr(_irouter, "LLMRouter", fake)
    monkeypatch.setattr(dg, "_LLM_EMPTY_RETRIES", 2)
    out = dg._llm_generate("write a section")
    assert out is None
    assert fake.calls["i"] == 3  # 1 initial + 2 retries, then give up


def test_first_answer_wins_no_wasted_retry(monkeypatch):
    fake = _fake_router_factory(["First good answer", "should not be used"])
    monkeypatch.setattr(_irouter, "LLMRouter", fake)
    monkeypatch.setattr(dg, "_LLM_EMPTY_RETRIES", 2)
    out = dg._llm_generate("write a section")
    assert out == "First good answer"
    assert fake.calls["i"] == 1  # returned immediately, no retry
