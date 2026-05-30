# CUI // SP-CTI
"""Phase 2 chat integration: per-session reasoned codegen.

Tests the code-request detector, the per-turn mode resolver, and ChatContext
persistence of reasoning_mode. No real LLM/DB calls.
"""

from unittest.mock import MagicMock, patch

from tools.dashboard import chat_manager as cm
from tools.dashboard.chat_manager import ChatContext


def _router(section_on=True):
    r = MagicMock()
    r._config = {"reasoned_codegen": {"enabled": section_on}}
    return r


# ── code-request detection ─────────────────────────────────────────────────
def test_is_code_request_positive():
    assert cm._is_code_request("please write a python function to parse logs")
    assert cm._is_code_request("implement an api endpoint for users")
    assert cm._is_code_request("here is a snippet ```def x(): pass```")


def test_is_code_request_negative():
    assert not cm._is_code_request("how are you today?")
    assert not cm._is_code_request("what is the deployment timeline?")


# ── per-turn mode resolution ───────────────────────────────────────────────
def test_resolve_off_mode():
    assert cm._resolve_chat_reasoning_mode("off", "write a function", _router()) == "off"


def test_resolve_auto_noncode_is_off():
    assert cm._resolve_chat_reasoning_mode("auto", "hello there", _router()) == "off"


def test_resolve_auto_code_uses_advisor():
    with patch("tools.llm.reasoned_codegen_advisor.recommend",
               return_value={"mode": "cod", "rationale": "risky"}):
        out = cm._resolve_chat_reasoning_mode("auto", "implement a secure auth endpoint", _router())
    assert out == "cod"


def test_resolve_on_code_never_off():
    with patch("tools.llm.reasoned_codegen_advisor.recommend",
               return_value={"mode": "off", "rationale": "trivial"}):
        out = cm._resolve_chat_reasoning_mode("on", "write a function to add two numbers", _router())
    assert out == "cot"


def test_resolve_killswitch_forces_off():
    with patch("tools.llm.reasoned_codegen_advisor.recommend",
               return_value={"mode": "cod"}):
        out = cm._resolve_chat_reasoning_mode("on", "implement an api endpoint", _router(section_on=False))
    assert out == "off"


# ── ChatContext persistence of the setting ─────────────────────────────────
def test_chatcontext_reasoning_mode_default_and_validation():
    ctx = ChatContext("ctx-1", "u1")
    assert ctx.reasoning_mode == "off"
    assert ctx.to_dict()["reasoning_mode"] == "off"
    ctx2 = ChatContext("ctx-2", "u1", reasoning_mode="auto")
    assert ctx2.reasoning_mode == "auto"
    # invalid value coerced to off
    ctx3 = ChatContext("ctx-3", "u1", reasoning_mode="bogus")
    assert ctx3.reasoning_mode == "off"
