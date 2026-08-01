# CUI // SP-CTI
"""Tests for gateway agent-mode (sag-gw-01).

Focus: agent-mode routes a bound user's free-text to the SAG runtime WITHOUT
bypassing the 8-gate chain (it rewrites to a synthetic 'agent' command validated
by the same allowlist path) and WITHOUT bypassing the IL response filter.
Hermetic: the runtime, session mapping, and response filter are faked.
"""
from __future__ import annotations

import pytest

import tools.agent_runtime.runtime as rt_mod
import tools.agent_runtime.sessions as sess_mod
import tools.gateway.agent_mode as am
import tools.gateway.command_router as cr_mod
import tools.gateway.response_filter as rf_mod
from tools.gateway.event_envelope import CommandEnvelope


def _env(command="", raw_text="", channel="internal_chat"):
    return CommandEnvelope(
        channel=channel,
        channel_user_id="u-1",
        channel_thread_id="thread-1",
        raw_text=raw_text or command,
        command=command,
    )


def _cfg(enabled=True, channels="internal_chat", **over):
    base = {
        "agent_mode": {
            "enabled": enabled,
            "channels": channels,
            "category": "execute",
            "max_il": "IL5",
        },
        "channels": {"internal_chat": {"max_il": "IL6"}},
        "gateway": {"response": {"max_length": 4000}},
    }
    base["agent_mode"].update(over)
    return base


# ---------------------------------------------------------------------------
# enablement
# ---------------------------------------------------------------------------


def test_enabled_for_listed_channel():
    assert am.is_agent_mode_enabled(_cfg(), "internal_chat") is True


def test_disabled_master_switch():
    assert am.is_agent_mode_enabled(_cfg(enabled=False), "internal_chat") is False


def test_disabled_for_unlisted_channel():
    assert am.is_agent_mode_enabled(_cfg(channels="slack"), "internal_chat") is False


def test_wildcard_channels():
    assert am.is_agent_mode_enabled(_cfg(channels="*"), "telegram") is True


def test_default_config_off():
    # No agent_mode key at all → off.
    assert am.is_agent_mode_enabled({}, "internal_chat") is False


# ---------------------------------------------------------------------------
# prepare_agent_envelope — the security-preservation seam
# ---------------------------------------------------------------------------


def test_structured_command_untouched(monkeypatch):
    monkeypatch.setattr(
        cr_mod, "is_command_allowed",
        lambda cmd, ch, al: (True, {"command": cmd}),
    )
    env = _env(command="icdev-status", raw_text="icdev-status")
    allowlist = [{"command": "icdev-status"}]
    eff, is_agent = am.prepare_agent_envelope(env, "internal_chat", _cfg(), allowlist)
    assert is_agent is False
    assert eff is allowlist  # unchanged
    assert env.command == "icdev-status"


def test_freetext_becomes_agent_command(monkeypatch):
    monkeypatch.setattr(
        cr_mod, "is_command_allowed",
        lambda cmd, ch, al: (False, None),
    )
    env = _env(command="what", raw_text="what is our ATO status?")
    eff, is_agent = am.prepare_agent_envelope(env, "internal_chat", _cfg(), [])
    assert is_agent is True
    assert env.command == am.AGENT_COMMAND
    assert env.args["agent_prompt"] == "what is our ATO status?"
    # a synthetic 'agent' allowlist entry was appended so the 8 gates still run
    entry = next(e for e in eff if e["command"] == am.AGENT_COMMAND)
    assert entry["category"] == "execute"
    assert entry["max_il"] == "IL5"
    assert entry["channels"] == "internal_chat"


def test_freetext_ignored_when_disabled(monkeypatch):
    monkeypatch.setattr(
        cr_mod, "is_command_allowed",
        lambda cmd, ch, al: (False, None),
    )
    env = _env(command="hello", raw_text="hello there")
    eff, is_agent = am.prepare_agent_envelope(
        env, "internal_chat", _cfg(enabled=False), []
    )
    assert is_agent is False
    assert env.command == "hello"  # not rewritten


# ---------------------------------------------------------------------------
# handle_agent_message — runs a turn, filters the reply, never leaks raw
# ---------------------------------------------------------------------------


class _FakeSession:
    context_id = "ctx-remote-1"


class _FakeResult:
    final_content = "Your ATO expires in 90 days."


class _FakeRuntime:
    resumed = None
    used = None

    def __init__(self, **kwargs):
        self.session = _FakeSession()
        _FakeRuntime.kwargs = kwargs

    def resume_session(self, ctx):
        _FakeRuntime.resumed = ctx

    def use_toolset(self, bundles):
        _FakeRuntime.used = list(bundles)

    def run_turn(self, text):
        self._text = text
        return _FakeResult()

    def dispatch_command(self, text):
        return True, f"dispatched {text}", False


@pytest.fixture
def patch_runtime(monkeypatch):
    monkeypatch.setattr(rt_mod, "AgentRuntime", _FakeRuntime)
    monkeypatch.setattr(sess_mod, "ensure_chat_tables", lambda: True)
    monkeypatch.setattr(am, "store_session", lambda *a, **k: None)
    monkeypatch.setattr(am, "touch_session", lambda *a, **k: None)
    monkeypatch.setattr(am, "_audit_agent_turn", lambda *a, **k: None)
    # Response filter: record that it was invoked with the channel max_il.
    calls = {}

    def _filter(text, max_il, eid):
        calls["max_il"] = max_il
        calls["text"] = text
        return (f"[filtered]{text}", True, "IL4")

    monkeypatch.setattr(rf_mod, "filter_response", _filter)
    monkeypatch.setattr(rf_mod, "truncate_response", lambda t, m: t)
    _FakeRuntime.resumed = None
    _FakeRuntime.used = None
    return calls


def test_agent_turn_new_session_filters_reply(patch_runtime, monkeypatch):
    monkeypatch.setattr(am, "lookup_session", lambda ch, cid: None)
    env = _env(command=am.AGENT_COMMAND, raw_text="how long until ATO expires?")
    env.args = {"agent_prompt": "how long until ATO expires?"}
    env.icdev_user_id = "analyst@enclave.mil"

    result = am.handle_agent_message(env, {"max_il": "IL4"}, _cfg())

    assert result["success"] is True
    # reply went through the IL filter against the channel max_il
    assert patch_runtime["max_il"] == "IL4"
    assert result["output"].startswith("[filtered]")
    assert result["filtered"] is True
    assert result["context_id"] == "ctx-remote-1"
    assert _FakeRuntime.resumed is None  # new session, not resumed


def test_agent_turn_resumes_existing(patch_runtime, monkeypatch):
    monkeypatch.setattr(am, "lookup_session", lambda ch, cid: "ctx-existing")
    env = _env(command=am.AGENT_COMMAND, raw_text="continue")
    env.args = {"agent_prompt": "continue"}
    env.icdev_user_id = "analyst@enclave.mil"

    am.handle_agent_message(env, {"max_il": "IL5"}, _cfg())
    assert _FakeRuntime.resumed == "ctx-existing"


def test_agent_slash_command_dispatched(patch_runtime, monkeypatch):
    monkeypatch.setattr(am, "lookup_session", lambda ch, cid: None)
    env = _env(command=am.AGENT_COMMAND, raw_text="/tools")
    env.args = {"agent_prompt": "/tools"}
    env.icdev_user_id = "u"

    result = am.handle_agent_message(env, {"max_il": "IL5"}, _cfg())
    assert "dispatched /tools" in result["output"]


def test_agent_toolset_restriction_applied(patch_runtime, monkeypatch):
    monkeypatch.setattr(am, "lookup_session", lambda ch, cid: None)
    env = _env(command=am.AGENT_COMMAND, raw_text="hi")
    env.args = {"agent_prompt": "hi"}
    env.icdev_user_id = "u"
    cfg = _cfg(toolsets=["file"])
    am.handle_agent_message(env, {"max_il": "IL5"}, cfg)
    assert _FakeRuntime.used == ["file"]


def test_agent_turn_error_is_contained(patch_runtime, monkeypatch):
    monkeypatch.setattr(am, "lookup_session", lambda ch, cid: None)

    class _Boom(_FakeRuntime):
        def run_turn(self, text):
            raise RuntimeError("llm down")

    monkeypatch.setattr(rt_mod, "AgentRuntime", _Boom)
    env = _env(command=am.AGENT_COMMAND, raw_text="hi")
    env.args = {"agent_prompt": "hi"}
    env.icdev_user_id = "u"
    result = am.handle_agent_message(env, {"max_il": "IL5"}, _cfg())
    assert result["success"] is False
    assert "Agent error" in result["output"]


# ---------------------------------------------------------------------------
# session mapping round-trip (opportunistic — skips if backend unavailable)
# ---------------------------------------------------------------------------


def test_session_mapping_roundtrip():
    try:
        am.store_session("internal_chat", "chat-xyz", "u-1", "", "ctx-42")
        got = am.lookup_session("internal_chat", "chat-xyz")
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"storage backend unavailable: {exc}")
    assert got == "ctx-42"
