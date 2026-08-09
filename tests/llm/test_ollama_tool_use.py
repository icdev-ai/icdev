# CUI // SP-CTI
"""The Ollama provider must ADVERTISE tools, not just parse them back.

Found by the hgx-exec-04 executor parity benchmark. ``invoke()`` built the
``/api/chat`` payload with model, messages, options and think — and never
``tools`` — while the response side already normalised ``message.tool_calls``.
Half the wiring. The model was therefore never told any tool existed, could not
emit a call, and every agent loop over Ollama returned prose on turn 1 with
``done=True`` and zero tool calls.

Nothing failed visibly. ``args/llm_config.yaml`` declares ``supports_tools: true``
for these models, so ``AgentLoopUnsupported`` was never raised either: the loop
degraded into a chat completion that looked exactly like a completed agent run.
Measured consequence — the owned executor (`local_agent`) could not edit a single
file, and reported success anyway.

No network: ``_http_request`` is replaced and the payload is asserted directly.
"""
from __future__ import annotations

import importlib

import pytest

mod = importlib.import_module("tools.llm.ollama_provider")
provider_mod = importlib.import_module("tools.llm.provider")

LLMRequest = provider_mod.LLMRequest

TOOLS = [
    {
        "name": "write_file",
        "description": "Write a file to disk",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    }
]


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture
def captured(monkeypatch):
    """Capture the payload the provider would POST, without a network call."""
    box = {}

    def _fake_request(method, url, json=None, headers=None, timeout=None, **kw):
        box["method"] = method
        box["url"] = url
        box["payload"] = json
        return _Resp(
            {
                "message": {"role": "assistant", "content": "ok"},
                "done": True,
                "prompt_eval_count": 10,
                "eval_count": 2,
            }
        )

    monkeypatch.setattr(mod, "_http_request", _fake_request)
    monkeypatch.setattr(mod, "HAS_REQUESTS", True)
    return box


def _invoke(captured, *, tools, supports_tools):
    provider = mod.OllamaProvider(base_url="http://localhost:11434")
    request = LLMRequest(
        messages=[{"role": "user", "content": "do the thing"}],
        tools=tools,
        max_tokens=512,
    )
    provider.invoke(
        request,
        "qwen3.5:latest",
        {"max_output_tokens": 4096, "supports_tools": supports_tools},
    )
    return captured["payload"]


def test_tools_are_sent_in_the_openai_function_shape(captured):
    payload = _invoke(captured, tools=TOOLS, supports_tools=True)

    assert "tools" in payload, "the model was never told the tool exists"
    assert payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write a file to disk",
                "parameters": TOOLS[0]["input_schema"],
            },
        }
    ]


def test_tools_are_withheld_from_a_model_that_cannot_use_them(captured):
    payload = _invoke(captured, tools=TOOLS, supports_tools=False)
    assert "tools" not in payload


def test_a_toolless_request_is_byte_unchanged(captured):
    payload = _invoke(captured, tools=None, supports_tools=True)
    assert "tools" not in payload
    assert payload["model"] == "qwen3.5:latest"
    assert payload["stream"] is False


def test_streaming_advertises_tools_too(captured, monkeypatch):
    """The same omission existed in invoke_streaming; both halves are fixed."""

    class _StreamResp(_Resp):
        def iter_lines(self):
            return iter([])

    def _fake_request(method, url, json=None, **kw):
        captured["payload"] = json
        return _StreamResp({})

    monkeypatch.setattr(mod, "_http_request", _fake_request)
    provider = mod.OllamaProvider()
    request = LLMRequest(
        messages=[{"role": "user", "content": "x"}], tools=TOOLS, max_tokens=256
    )
    list(
        provider.invoke_streaming(
            request, "qwen3.5:latest", {"supports_tools": True, "max_output_tokens": 4096}
        )
    )

    assert captured["payload"]["tools"][0]["function"]["name"] == "write_file"


def test_the_response_side_still_normalises_tool_calls(monkeypatch):
    """The half that already worked must keep working: Ollama's
    {function:{name,arguments}} becomes the cross-provider {id,name,input}."""

    def _fake_request(method, url, json=None, **kw):
        return _Resp(
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {
                                "name": "write_file",
                                "arguments": '{"path": "a.txt", "content": "hi"}',
                            },
                        }
                    ],
                },
                "done": True,
            }
        )

    monkeypatch.setattr(mod, "_http_request", _fake_request)
    monkeypatch.setattr(mod, "HAS_REQUESTS", True)

    response = mod.OllamaProvider().invoke(
        LLMRequest(messages=[{"role": "user", "content": "x"}], tools=TOOLS, max_tokens=256),
        "qwen3.5:latest",
        {"supports_tools": True, "max_output_tokens": 4096},
    )

    assert response.tool_calls == [
        {"id": "c1", "name": "write_file", "input": {"path": "a.txt", "content": "hi"}}
    ]
    assert response.stop_reason == "tool_use"


def test_mirrored_to_the_icdev_package():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    canonical = root / "tools" / "llm" / "ollama_provider.py"
    mirror = root / "icdev" / "tools" / "llm" / "ollama_provider.py"
    assert canonical.read_bytes() == mirror.read_bytes()
