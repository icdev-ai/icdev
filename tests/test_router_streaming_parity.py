#!/usr/bin/env python3
# CUI // SP-CTI
"""LLMRouter.invoke_streaming at parity with invoke (owui-api-02).

``invoke_streaming`` applied input redaction but never restored the surrogates
in what it streamed back and never wrote ``ai_telemetry`` -- so a streamed call
returned pseudonymised text, unaudited. This pins both halves.

The provider, sanitizer and telemetry sink are stubbed; the router's own
chain walk, redaction session handling and stream wrapper are real.
"""

import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.llm.provider import LLMRequest  # noqa: E402
from tools.llm.router import LLMRouter  # noqa: E402

SURROGATE, ORIGINAL = "PERSON_7f3a", "Jane Doe"


class FakeProvider:
    def __init__(self, chunks):
        self.chunks = chunks

    def invoke_streaming(self, request, model_id, model_config):
        yield from self.chunks


class FakeSanitizer:
    def de_anonymize_response(self, text):
        return text.replace(SURROGATE, ORIGINAL)

    def active_surrogates(self):
        return [SURROGATE.lower()]


class OpaqueSanitizer(FakeSanitizer):
    def active_surrogates(self):
        raise AttributeError("no registry")


@pytest.fixture
def router(monkeypatch):
    r = LLMRouter.__new__(LLMRouter)  # skip provider discovery; wire only what streaming uses
    r._config = {"routing": {"f": {"chain": ["m"]}}, "redaction": {"deanonymize_response": True}}
    r._availability_cache = {}
    telemetry = []
    monkeypatch.setattr(r, "is_no_llm_mode", lambda: False, raising=False)
    monkeypatch.setattr(r, "_apply_prompt_layers", lambda f, req: req, raising=False)
    monkeypatch.setattr(r, "get_effort", lambda f: "medium", raising=False)
    monkeypatch.setattr(r, "_get_chain_for_function", lambda f: ["m"], raising=False)
    monkeypatch.setattr(r, "_get_model_config", lambda m: {"provider": "fake", "model_id": "fake-1"}, raising=False)
    monkeypatch.setattr(r, "_get_sanitizer", lambda: FakeSanitizer(), raising=False)
    monkeypatch.setattr(r, "_log_telemetry", lambda **kw: telemetry.append(kw), raising=False)
    monkeypatch.setattr(r, "_provider_invoke_streaming",
                        lambda provider, request, model_id, model_cfg, function="": provider.invoke_streaming(
                            request, model_id, model_cfg), raising=False)
    r._test_telemetry = telemetry
    return r


def _wire(monkeypatch, router, chunks, redaction_session):
    monkeypatch.setattr(router, "_get_provider", lambda name: FakeProvider(chunks), raising=False)
    monkeypatch.setattr(router, "_pre_invoke_redaction", lambda f, req: redaction_session, raising=False)


def _text(events):
    return "".join(e.get("text", "") for e in events if e.get("type") == "text")


def test_redacted_stream_is_de_anonymized_even_when_a_surrogate_is_split(monkeypatch, router):
    chunks = [{"type": "text", "text": "Hello PERSON_"}, {"type": "text", "text": "7f3a, welcome."},
              {"type": "message_stop", "model_id": "fake-1", "duration_ms": 5}]
    _wire(monkeypatch, router, chunks, redaction_session="sess-1")
    events = list(router.invoke_streaming("f", LLMRequest(messages=[{"role": "user", "content": "hi"}])))
    assert _text(events) == "Hello Jane Doe, welcome."
    assert SURROGATE not in _text(events)
    assert events[-1]["type"] == "message_stop"


def test_unredacted_stream_passes_chunks_through_incrementally(monkeypatch, router):
    chunks = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"},
              {"type": "message_stop", "model_id": "fake-1"}]
    _wire(monkeypatch, router, chunks, redaction_session=None)
    events = list(router.invoke_streaming("f", LLMRequest(messages=[{"role": "user", "content": "hi"}])))
    assert [e.get("text") for e in events if e["type"] == "text"] == ["a", "b"]


def test_a_streamed_call_writes_exactly_one_telemetry_row(monkeypatch, router):
    chunks = [{"type": "text", "text": "Hello PERSON_7f3a"},
              {"type": "message_stop", "model_id": "fake-1", "input_tokens": 3, "output_tokens": 4}]
    _wire(monkeypatch, router, chunks, redaction_session="sess-1")
    list(router.invoke_streaming("f", LLMRequest(messages=[{"role": "user", "content": "hi"}])))
    (row,) = router._test_telemetry
    assert row["function"] == "f"
    assert row["provider_name"] == "fake"
    assert row["response"].content == "Hello Jane Doe"
    assert (row["response"].input_tokens, row["response"].output_tokens) == (3, 4)


def test_an_abandoned_stream_still_writes_its_row(monkeypatch, router):
    chunks = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"},
              {"type": "message_stop", "model_id": "fake-1"}]
    _wire(monkeypatch, router, chunks, redaction_session=None)
    gen = router.invoke_streaming("f", LLMRequest(messages=[{"role": "user", "content": "hi"}]))
    next(gen)
    gen.close()
    assert len(router._test_telemetry) == 1


# ---------------------------------------------------------------------------
# Incremental release under redaction (owui-api-06)
# ---------------------------------------------------------------------------
def test_redacted_stream_is_incremental(monkeypatch, router):
    chunks = [{"type": "text", "text": "one "}, {"type": "text", "text": "two "},
              {"type": "text", "text": "three"}, {"type": "message_stop", "model_id": "fake-1"}]
    _wire(monkeypatch, router, chunks, redaction_session="sess-1")
    events = list(router.invoke_streaming("f", LLMRequest(messages=[{"role": "user", "content": "hi"}])))
    assert len([e for e in events if e["type"] == "text"]) >= 3
    assert _text(events) == "one two three"


def test_random_splits_never_leak_a_surrogate_fragment(monkeypatch, router):
    import random

    text = "Hello PERSON_7f3a, and again PERSON_7F3A here. person_ is fine alone."
    expected = text.replace(SURROGATE, ORIGINAL).replace("PERSON_7F3A", ORIGINAL)
    monkeypatch.setattr(router, "_get_sanitizer", lambda: type("S", (FakeSanitizer,), {
        "de_anonymize_response": lambda self, t: __import__("re").sub("(?i)person_7f3a", ORIGINAL, t)})())
    rng = random.Random(7)
    for _ in range(200):
        cuts = sorted(rng.sample(range(1, len(text)), rng.randint(1, 12)))
        pieces = [text[a:b] for a, b in zip([0] + cuts, cuts + [len(text)])]
        chunks = [{"type": "text", "text": p} for p in pieces] + [{"type": "message_stop"}]
        _wire(monkeypatch, router, chunks, redaction_session="sess-1")
        events = list(router.invoke_streaming("f", LLMRequest(messages=[{"role": "user", "content": "hi"}])))
        assert _text(events) == expected
        for e in events:
            if e["type"] == "text":
                assert "7f3a" not in e["text"].lower(), pieces


def test_unknowable_surrogate_set_falls_back_to_buffering(monkeypatch, router):
    monkeypatch.setattr(router, "_get_sanitizer", lambda: OpaqueSanitizer())
    chunks = [{"type": "text", "text": "Hello PERSON_"}, {"type": "text", "text": "7f3a"},
              {"type": "message_stop"}]
    _wire(monkeypatch, router, chunks, redaction_session="sess-1")
    events = list(router.invoke_streaming("f", LLMRequest(messages=[{"role": "user", "content": "hi"}])))
    assert [e["text"] for e in events if e["type"] == "text"] == ["Hello Jane Doe"]
