# CUI // SP-CTI
"""Tests for tools/agent_toolkit/_composer.py — create_agent + Agent.invoke.

These tests mock LLMRouter so they don't hit any real provider. The
composer's tool-calling loop is the thing under test; the LLM itself
is treated as an oracle.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock, patch


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agent_toolkit import _composer
from tools.agent_toolkit._composer import (
    Agent,
    AgentResult,
    create_agent,
    list_default_tools,
)


def _mock_llm_response(content: str = "", tool_calls=None, stop_reason: str = ""):
    """Build a mock LLMResponse object."""
    r = MagicMock()
    r.content = content
    r.tool_calls = tool_calls or []
    r.stop_reason = stop_reason
    r.model_id = "mock-model"
    r.provider = "mock"
    r.input_tokens = 10
    r.output_tokens = 20
    r.duration_ms = 5
    return r


def test_create_agent_with_defaults():
    agent = create_agent(name="test", system_prompt="hello")
    assert isinstance(agent, Agent)
    assert agent.name == "test"
    assert agent.system_prompt == "hello"
    # Default tool catalog has 10 tools
    assert len(agent.tools) == 10
    assert "read_file" in agent.tools
    assert "execute_shell" in agent.tools


def test_create_agent_no_defaults():
    agent = create_agent(
        name="bare",
        system_prompt="x",
        include_defaults=False,
    )
    assert agent.tools == {}


def test_create_agent_replace_tools():
    def custom_tool(**kwargs):
        return "custom"

    agent = create_agent(
        name="custom",
        system_prompt="x",
        tools={"custom": custom_tool},
    )
    assert list(agent.tools.keys()) == ["custom"]


def test_create_agent_extra_tools():
    def extra(**kwargs):
        return "extra"

    agent = create_agent(
        name="mixed",
        system_prompt="x",
        extra_tools={"extra": extra},
    )
    # Defaults (10) + extra (1) = 11
    assert len(agent.tools) == 11
    assert "extra" in agent.tools
    assert "read_file" in agent.tools


def test_list_default_tools():
    tools = list_default_tools()
    assert isinstance(tools, list)
    assert len(tools) == 10
    for expected in ("read_file", "write_file", "grep", "execute_shell", "spawn_subagent"):
        assert expected in tools


def test_invoke_single_shot_no_tool_calls():
    agent = create_agent(name="t", system_prompt="x")
    with patch("tools.llm.router.LLMRouter") as mock_router_cls:
        mock_router = MagicMock()
        mock_router.invoke.return_value = _mock_llm_response(
            content="hello back",
            stop_reason="end_turn",
        )
        mock_router_cls.return_value = mock_router

        result = agent.invoke([{"role": "user", "content": "hi"}])

    assert isinstance(result, AgentResult)
    assert result.error is None
    assert result.iterations == 1
    assert result.tool_calls_made == 0
    assert result.stop_reason == "end_turn"
    assert result.final_content == "hello back"
    # user msg + assistant msg
    assert len(result.messages) == 2
    assert result.messages[-1]["role"] == "assistant"
    assert result.messages[-1]["content"] == "hello back"


def test_invoke_tool_call_then_final_response(tmp_path):
    target = tmp_path / "hello.txt"
    target.write_text("inside the file")

    agent = create_agent(name="reader", system_prompt="x")

    # Round 1: LLM asks to read the file
    # Round 2: LLM returns a final summary
    responses = [
        _mock_llm_response(
            content="",
            tool_calls=[{
                "id": "call_1",
                "name": "read_file",
                "arguments": {"path": str(target)},
            }],
            stop_reason="tool_use",
        ),
        _mock_llm_response(
            content="the file says 'inside the file'",
            stop_reason="end_turn",
        ),
    ]

    with patch("tools.llm.router.LLMRouter") as mock_router_cls:
        mock_router = MagicMock()
        mock_router.invoke.side_effect = responses
        mock_router_cls.return_value = mock_router

        result = agent.invoke([{"role": "user", "content": "what's in the file?"}])

    assert result.error is None
    assert result.tool_calls_made == 1
    assert result.iterations == 2
    assert result.final_content == "the file says 'inside the file'"
    # user + assistant(tool_use) + tool + assistant(final)
    assert len(result.messages) == 4
    assert result.messages[2]["role"] == "tool"
    assert result.messages[2]["name"] == "read_file"
    assert "inside the file" in result.messages[2]["content"]


def test_invoke_unknown_tool_yields_error_in_tool_msg():
    agent = create_agent(name="oops", system_prompt="x")

    responses = [
        _mock_llm_response(
            tool_calls=[{
                "id": "call_1",
                "name": "does_not_exist",
                "arguments": {},
            }],
            stop_reason="tool_use",
        ),
        _mock_llm_response(content="sorry", stop_reason="end_turn"),
    ]

    with patch("tools.llm.router.LLMRouter") as mock_router_cls:
        mock_router = MagicMock()
        mock_router.invoke.side_effect = responses
        mock_router_cls.return_value = mock_router

        result = agent.invoke([{"role": "user", "content": "x"}])

    assert result.error is None
    tool_msg = result.messages[2]
    assert tool_msg["role"] == "tool"
    assert "unknown tool 'does_not_exist'" in tool_msg["content"]


def test_invoke_tool_exception_captured():
    def broken(**kwargs):
        raise RuntimeError("kaboom")

    agent = create_agent(
        name="err",
        system_prompt="x",
        extra_tools={"broken": broken},
    )

    responses = [
        _mock_llm_response(
            tool_calls=[{"id": "c1", "name": "broken", "arguments": {}}],
            stop_reason="tool_use",
        ),
        _mock_llm_response(content="done", stop_reason="end_turn"),
    ]

    with patch("tools.llm.router.LLMRouter") as mock_router_cls:
        mock_router = MagicMock()
        mock_router.invoke.side_effect = responses
        mock_router_cls.return_value = mock_router

        result = agent.invoke([{"role": "user", "content": "do it"}])

    assert result.error is None
    tool_msg = result.messages[2]
    assert "RuntimeError" in tool_msg["content"]
    assert "kaboom" in tool_msg["content"]


def test_invoke_llm_exception_surfaces_in_error():
    agent = create_agent(name="err", system_prompt="x")

    with patch("tools.llm.router.LLMRouter") as mock_router_cls:
        mock_router = MagicMock()
        mock_router.invoke.side_effect = RuntimeError("provider down")
        mock_router_cls.return_value = mock_router

        result = agent.invoke([{"role": "user", "content": "hi"}])

    assert result.error is not None
    assert "provider down" in result.error
    assert result.stop_reason == "llm_error"


def test_invoke_hits_max_iterations():
    agent = create_agent(name="loop", system_prompt="x", max_iterations=3)

    # Every turn returns a tool_call, no terminating response
    tool_response = _mock_llm_response(
        tool_calls=[{"id": "c1", "name": "read_file", "arguments": {"path": "/nope"}}],
        stop_reason="tool_use",
    )

    with patch("tools.llm.router.LLMRouter") as mock_router_cls:
        mock_router = MagicMock()
        mock_router.invoke.return_value = tool_response
        mock_router_cls.return_value = mock_router

        result = agent.invoke([{"role": "user", "content": "loop"}])

    assert result.iterations == 3
    assert result.stop_reason == "max_iterations"


def test_agent_result_to_dict():
    r = AgentResult(iterations=2, tool_calls_made=1, final_content="hi")
    d = r.to_dict()
    assert d["iterations"] == 2
    assert d["tool_calls_made"] == 1
    assert d["final_content"] == "hi"
    assert "messages" in d


def test_safe_json_truncates_large_output():
    big = "x" * 20000
    out = _composer._safe_json({"payload": big}, max_len=100)
    assert len(out) < 200  # truncated
    assert "truncated" in out
