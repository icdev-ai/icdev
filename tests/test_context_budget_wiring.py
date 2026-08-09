# CUI // SP-CTI
"""Agent-loop compaction must see tool traffic and the REAL context window.

Two compounding bugs made long agentic runs degrade silently (hgx-ctxw-01):

  1. ``_estimate_message_tokens`` counted only ``block["text"]``. A ``tool_use``
     block carries its payload under ``input`` and a ``tool_result`` block nests
     its text under ``content``, so both contributed ZERO — and in a tool-heavy
     run they ARE the conversation.
  2. The compaction trigger used a single static
     ``agent_loop.budgets.context_window_tokens`` for every function and every
     model, while ``tools/llm/context_budget.py`` already knew the real
     per-model windows and could take the minimum across a routed fallback
     chain.
"""
from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any

import pytest

from icdev.tools.llm import context_budget
from icdev.tools.llm.agent_loop import (
    _estimate_block_tokens,
    _estimate_message_tokens,
    _estimate_text_tokens,
    _resolve_context_window_tokens,
    _serialize_payload,
    run_agent_loop,
)
from icdev.tools.llm.provider import LLMResponse

CONFIGURED_WINDOW = 64000


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeProvider:
    provider_name: str = "anthropic"


class OneShotRouter:
    """Router that answers the first turn with final text and no tool calls."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def get_provider_for_function(self, function: str):
        return FakeProvider(), "fake-model", {"supports_tools": True}

    def invoke(self, function: str, request: Any) -> LLMResponse:
        self.calls.append(request)
        return LLMResponse(content="done", stop_reason="end_turn", provider="fake")


def _tool(name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def _tool_exchange(index: int, chars: int) -> list[dict[str, Any]]:
    """One tool_use / tool_result pair carrying *chars* of payload each, no text blocks."""
    return [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": f"call-{index}",
                    "name": "write_file",
                    "input": {"path": f"mod_{index}.py", "content": "x" * chars},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": f"call-{index}",
                    "name": "write_file",
                    "content": [{"type": "text", "text": "y" * chars}],
                }
            ],
        },
    ]


def _history(pairs: int, chars: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(pairs):
        out.extend(_tool_exchange(i, chars))
    return out


def _patch_chain(monkeypatch, chain: list[str], windows: dict[str, int | None]) -> None:
    """Point context_budget at a synthetic routing chain.

    ``floor_window_for_function`` resolves ``chain_for_function`` and
    ``model_windows`` as module globals, so patching both is enough.
    """
    monkeypatch.setattr(context_budget, "chain_for_function", lambda fn: list(chain))
    monkeypatch.setattr(context_budget, "model_windows", lambda: dict(windows))


# ---------------------------------------------------------------------------
# Bug 1 — tool traffic is counted
# ---------------------------------------------------------------------------


class TestToolTrafficIsCounted:
    def test_tool_only_conversation_is_not_zero(self):
        messages = _history(pairs=3, chars=4000)
        assert not any(
            block.get("type") == "text"
            for msg in messages
            for block in msg["content"]
        )
        total = sum(_estimate_message_tokens(m) for m in messages)
        # 6 blocks * 4000 chars / 4 chars-per-token = ~6000, floored generously.
        assert total > 5000

    def test_every_tool_block_contributes(self):
        for msg in _history(pairs=1, chars=1200):
            assert _estimate_message_tokens(msg) > 0

    def test_estimate_is_proportional_to_payload_size(self):
        one = sum(_estimate_message_tokens(m) for m in _history(pairs=1, chars=4000))
        four = sum(_estimate_message_tokens(m) for m in _history(pairs=4, chars=4000))
        assert one > 0
        # Payload dominates; per-pair overhead is a few tokens of JSON punctuation.
        assert 3.8 <= four / one <= 4.2

    def test_tool_use_input_counted(self):
        block = {"type": "tool_use", "id": "c1", "name": "run", "input": {"cmd": "z" * 800}}
        assert _estimate_block_tokens(block) >= _estimate_text_tokens("z" * 800)

    def test_tool_result_string_content_counted(self):
        block = {"type": "tool_result", "tool_use_id": "c1", "content": "z" * 800}
        assert _estimate_block_tokens(block) >= _estimate_text_tokens("z" * 800)

    def test_tool_result_nested_text_counted(self):
        block = {
            "type": "tool_result",
            "tool_use_id": "c1",
            "content": [{"type": "text", "text": "z" * 800}],
        }
        assert _estimate_block_tokens(block) >= _estimate_text_tokens("z" * 800)

    def test_plain_text_blocks_still_counted(self):
        msg = {"role": "assistant", "content": [{"type": "text", "text": "w" * 400}]}
        assert _estimate_message_tokens(msg) == _estimate_text_tokens("w" * 400)

    def test_string_content_still_counted(self):
        msg = {"role": "user", "content": "w" * 400}
        assert _estimate_message_tokens(msg) == _estimate_text_tokens("w" * 400)

    def test_empty_blocks_are_free(self):
        msg = {"role": "assistant", "content": [{"type": "tool_use", "id": "c", "input": {}}]}
        assert _estimate_message_tokens(msg) == 0

    def test_serialize_payload_survives_unserializable_input(self):
        assert _serialize_payload(object()) != ""


# ---------------------------------------------------------------------------
# Bug 2 — the threshold comes from the routed chain, not a static number
# ---------------------------------------------------------------------------


class TestWindowResolution:
    def test_uses_chain_minimum(self, monkeypatch):
        _patch_chain(monkeypatch, ["big", "small"], {"big": 200000, "small": 32768})
        assert _resolve_context_window_tokens("code_generation", CONFIGURED_WINDOW) == 32768

    def test_large_window_chain_beats_the_static_config(self, monkeypatch):
        _patch_chain(monkeypatch, ["big", "bigger"], {"big": 200000, "bigger": 1000000})
        assert _resolve_context_window_tokens("code_generation", CONFIGURED_WINDOW) == 200000

    def test_falls_back_to_config_when_chain_declares_no_window(self, monkeypatch):
        _patch_chain(monkeypatch, ["mystery"], {"mystery": None})
        assert _resolve_context_window_tokens("code_generation", CONFIGURED_WINDOW) == CONFIGURED_WINDOW

    def test_falls_back_to_config_when_chain_is_empty(self, monkeypatch):
        _patch_chain(monkeypatch, [], {})
        assert _resolve_context_window_tokens("nope", CONFIGURED_WINDOW) == CONFIGURED_WINDOW

    def test_degrades_to_config_when_budget_module_raises(self, monkeypatch):
        def boom(fn):
            raise RuntimeError("config unreadable")

        monkeypatch.setattr(context_budget, "chain_for_function", boom)
        assert _resolve_context_window_tokens("code_generation", CONFIGURED_WINDOW) == CONFIGURED_WINDOW

    def test_real_config_resolves_a_declared_window(self):
        """The shipped config must produce a real number, not the 8192 placeholder."""
        resolved = _resolve_context_window_tokens("code_generation", CONFIGURED_WINDOW)
        assert resolved is not None
        assert resolved != context_budget.DEFAULT_WINDOW_TOKENS
        assert resolved == context_budget.floor_window_for_function("code_generation")


# ---------------------------------------------------------------------------
# End to end — both fixes together decide whether compaction fires
# ---------------------------------------------------------------------------


@pytest.fixture()
def compressions(monkeypatch):
    """Record every compress_messages call instead of really compressing."""
    seen: list[dict[str, Any]] = []

    @dataclass
    class FakeCompressed:
        messages: list[dict[str, Any]]
        original_tokens: int
        compressed_tokens: int
        compression_ratio: float
        method: str

    def fake_compress(messages, *, budget_tokens, content_type):
        seen.append({"budget_tokens": budget_tokens, "messages": len(messages)})
        return FakeCompressed(
            messages=list(messages),
            original_tokens=0,
            compressed_tokens=0,
            compression_ratio=1.0,
            method="fake",
        )

    monkeypatch.setattr("icdev.tools.llm.context_compressor.compress_messages", fake_compress)
    return seen


def _run(history: list[dict[str, Any]]) -> OneShotRouter:
    router = OneShotRouter()
    run_agent_loop(
        router,
        system_prompt="s",
        user_prompt="u",
        tools=[_tool("write_file")],
        tool_handlers={"write_file": lambda inp, stop: "ok"},
        max_iterations=1,
        initial_messages=history,
        memory_enabled=False,
    )
    return router


class TestCompactionTrigger:
    def test_does_not_fire_at_64k_when_the_chain_is_large_window(self, monkeypatch, compressions):
        _patch_chain(monkeypatch, ["big"], {"big": 200000})
        # ~70k tokens of pure tool traffic: over the static 64000 config value,
        # far under the chain's real window.
        history = _history(pairs=35, chars=4000)
        estimate = sum(_estimate_message_tokens(m) for m in history)
        assert estimate > CONFIGURED_WINDOW
        _run(history)
        assert compressions == []

    def test_fires_at_32k_when_the_chain_minimum_is_32k(self, monkeypatch, compressions):
        _patch_chain(monkeypatch, ["big", "small"], {"big": 200000, "small": 32768})
        # ~40k tokens: UNDER the static 64000 config value, over the chain minimum.
        history = _history(pairs=20, chars=4000)
        estimate = sum(_estimate_message_tokens(m) for m in history)
        assert 32768 < estimate < CONFIGURED_WINDOW
        _run(history)
        assert len(compressions) == 1
        # The configured 48000 target would sit above the resolved window.
        assert compressions[0]["budget_tokens"] <= 32768

    def test_tool_traffic_alone_can_trigger_compaction(self, monkeypatch, compressions):
        """Pre-fix this history estimated at ZERO tokens and never compacted."""
        _patch_chain(monkeypatch, ["small"], {"small": 32768})
        history = _history(pairs=20, chars=4000)
        assert all(
            block.get("type") != "text"
            for msg in history
            for block in msg["content"]
        )
        _run(history)
        assert compressions

    def test_explicit_caller_value_still_wins(self, monkeypatch, compressions):
        _patch_chain(monkeypatch, ["big"], {"big": 1000000})
        history = _history(pairs=2, chars=4000)
        router = OneShotRouter()
        run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("write_file")],
            tool_handlers={"write_file": lambda inp, stop: "ok"},
            max_iterations=1,
            initial_messages=history,
            context_window_tokens=100,
            memory_enabled=False,
        )
        assert compressions


# ---------------------------------------------------------------------------
# LLM-agnostic — the chain minimum is the whole point
# ---------------------------------------------------------------------------


class TestNoModelIdentifiers:
    #: Vendor, family and model-id fragments that must not steer this logic.
    FORBIDDEN = re.compile(
        r"claude|sonnet|opus|haiku|anthropic|gpt|openai|gemini|google|llama|mistral"
        r"|codestral|qwen|kimi|ollama|bedrock|deepseek|grok",
        re.IGNORECASE,
    )

    @pytest.mark.parametrize(
        "func",
        [
            _serialize_payload,
            _estimate_block_tokens,
            _estimate_message_tokens,
            _resolve_context_window_tokens,
        ],
    )
    def test_no_model_family_in_changed_helpers(self, func):
        assert not self.FORBIDDEN.search(inspect.getsource(func))

    def test_no_model_family_in_the_window_wiring(self):
        src = inspect.getsource(run_agent_loop)
        window_block = src[src.index("_caller_context_window"):src.index("tool_timeout_seconds is None")]
        assert not self.FORBIDDEN.search(window_block)


# ---------------------------------------------------------------------------
# Shim parity — tools.llm.agent_loop must expose the same objects
# ---------------------------------------------------------------------------


def test_shim_reexports_the_same_estimator():
    from tools.llm.agent_loop import _estimate_message_tokens as shim_estimate

    assert shim_estimate is _estimate_message_tokens
