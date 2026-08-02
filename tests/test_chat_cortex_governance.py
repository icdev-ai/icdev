# CUI // SP-CTI
"""Chat turns run through Cortex's TRUST chain.

Chat called LLMRouter directly, so none of Cortex's gates applied to it — no
injection screen on the single largest injection surface in the platform, and
no audit row, which is why /cortex/metrics reported no traffic for weeks while
chat was busy.

The change wraps the existing `router.invoke` rather than replacing it with
`cortex_api.complete()`. By the time `_process_message` reaches the model it has
assembled ~180 lines of context — RICOAS constitution, corrections, RAG, KG,
live compliance, hybrid memory, compression — plus a reasoned-codegen branch and
the CLIJobDeferred protocol. `complete()` takes a prompt and would drop all of
it, along with `ctx.agent_model`.

So the properties under test are: the gates run, the *governed* text is what
reaches the model, and a failure in the governance layer degrades to an
ungoverned call rather than taking chat down.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools.dashboard import chat_manager as cm


@pytest.fixture
def ctx():
    return SimpleNamespace(
        context_id="ctx-1", tenant_id="t1", user_id="u1", project_id="p1",
    )


@pytest.fixture(autouse=True)
def _governance_on(monkeypatch):
    monkeypatch.delenv("ICDEV_CHAT_GOVERNANCE", raising=False)


# ---------------------------------------------------------------------------
# The kill switch
# ---------------------------------------------------------------------------


def test_governance_is_on_by_default():
    """Ungoverned chat is the gap this closes; opt-out, not opt-in."""
    assert cm._chat_governance_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "off", "no", "FALSE"])
def test_kill_switch_disables_it(monkeypatch, value):
    """A change touching every turn needs one obvious way to disable it."""
    monkeypatch.setenv("ICDEV_CHAT_GOVERNANCE", value)
    assert cm._chat_governance_enabled() is False


def test_disabled_still_calls_the_model(monkeypatch, ctx):
    monkeypatch.setenv("ICDEV_CHAT_GOVERNANCE", "false")
    reply, blocked = cm.governed_chat_invoke(lambda t: f"answered:{t}", "hello", ctx)

    assert reply == "answered:hello"
    assert blocked is False


# ---------------------------------------------------------------------------
# The gates actually run
# ---------------------------------------------------------------------------


def test_the_call_goes_through_the_pipeline(ctx):
    """Not a plain router.invoke — that is the whole point."""
    with patch("tools.cortex.governance.GovernancePipeline") as pipeline:
        pipeline.return_value.wrap.return_value = ("governed reply", MagicMock())
        reply, blocked = cm.governed_chat_invoke(lambda t: "raw", "hello", ctx)

    pipeline.assert_called_once_with(operation=cm.CHAT_GOVERNANCE_OPERATION)
    assert reply == "governed reply"
    assert blocked is False


def test_grounding_gates_do_not_score_an_ungrounded_turn(ctx):
    """A conversational reply has no citable sources.

    Letting the grounding gates run against nothing would manufacture a defect
    every turn and make governance look broken.
    """
    with patch("tools.cortex.governance.GovernancePipeline") as pipeline:
        pipeline.return_value.wrap.return_value = ("x", MagicMock())
        cm.governed_chat_invoke(lambda t: "x", "hello", ctx)

    assert pipeline.return_value.wrap.call_args.kwargs["retrieval"] is False
    assert pipeline.return_value.wrap.call_args.kwargs["attach"] is False


def test_chat_context_does_not_fail_closed(ctx):
    """Blocking a conversation on a grounding warn would feel like a fault.

    Pre-check (injection) still blocks regardless — that is the gate that
    matters on this surface.
    """
    cortex_ctx = cm._build_chat_cortex_ctx(ctx)
    assert cortex_ctx.fail_closed is False
    assert cortex_ctx.session_id == "ctx-1"
    assert cortex_ctx.tenant_id == "t1"


# ---------------------------------------------------------------------------
# The governed text is what reaches the model
# ---------------------------------------------------------------------------


def test_governed_text_is_passed_to_the_invoker(ctx):
    """Redaction must take effect, not merely be recorded.

    The pipeline hands `wrap`'s callable the screened/redacted prompt; if the
    caller ignored it and sent the original, input redaction would be theatre.
    """
    seen = {}

    def _invoke(text):
        seen["text"] = text
        return "ok"

    with patch("tools.cortex.governance.GovernancePipeline") as pipeline:
        # Emulate the real contract: wrap calls fn(governed_prompt).
        pipeline.return_value.wrap.side_effect = (
            lambda fn, c, **kw: (fn("<REDACTED> hello"), MagicMock())
        )
        cm.governed_chat_invoke(_invoke, "my ssn is 123-45-6789 hello", ctx)

    assert seen["text"] == "<REDACTED> hello"


# ---------------------------------------------------------------------------
# A blocked turn refuses; it does not crash
# ---------------------------------------------------------------------------


def test_blocked_turn_returns_a_refusal(ctx):
    from tools.cortex.governance import GovernanceBlockedError

    with patch("tools.cortex.governance.GovernancePipeline") as pipeline:
        pipeline.return_value.wrap.side_effect = GovernanceBlockedError(
            "pre_check", "prompt injection detected", MagicMock()
        )
        reply, blocked = cm.governed_chat_invoke(lambda t: "should not run", "evil", ctx)

    assert blocked is True
    assert "blocked" in reply.lower()
    assert "audit" in reply.lower()


def test_blocked_turn_never_calls_the_model(ctx):
    from tools.cortex.governance import GovernanceBlockedError

    called = []
    with patch("tools.cortex.governance.GovernancePipeline") as pipeline:
        pipeline.return_value.wrap.side_effect = GovernanceBlockedError(
            "pre_check", "injection", MagicMock()
        )
        cm.governed_chat_invoke(lambda t: called.append(t), "evil", ctx)

    assert called == []


# ---------------------------------------------------------------------------
# Governance failing must not take chat down
# ---------------------------------------------------------------------------


def test_pipeline_error_degrades_to_an_ungoverned_call(ctx):
    """An outage in the governance layer must not break every conversation."""
    with patch("tools.cortex.governance.GovernancePipeline") as pipeline:
        pipeline.return_value.wrap.side_effect = RuntimeError("cortex is down")
        reply, blocked = cm.governed_chat_invoke(lambda t: f"answered:{t}", "hello", ctx)

    assert reply == "answered:hello"
    assert blocked is False


def test_missing_cortex_degrades(monkeypatch, ctx):
    """Cortex is optional; an ImportError must not be fatal to chat."""
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *a, **k):
        if name == "tools.cortex.governance":
            raise ImportError("simulated: cortex absent")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    reply, blocked = cm.governed_chat_invoke(lambda t: "answered", "hello", ctx)

    assert reply == "answered"
    assert blocked is False


def test_missing_cortex_schemas_degrades(monkeypatch, ctx):
    monkeypatch.setattr(cm, "_build_chat_cortex_ctx", lambda c: None)
    reply, blocked = cm.governed_chat_invoke(lambda t: "answered", "hello", ctx)

    assert reply == "answered"
    assert blocked is False


# ---------------------------------------------------------------------------
# The context assembly is not disturbed
# ---------------------------------------------------------------------------


def test_process_message_still_assembles_its_own_context():
    """cortex_api.complete() would have dropped ~180 lines of assembly.

    Guards against a future 'simplification' that swaps the wrapped invoke for
    a facade call and silently loses RAG, KG, memory and the model selection.
    """
    import inspect

    source = inspect.getsource(cm.ChatManager._process_message)

    for marker in ("_rag_retrieve", "_kg_context_retrieve", "_compress_history",
                   "agent_model", "reasoned_codegen"):
        assert marker in source, f"{marker} lost from _process_message"

    # The model call itself is governed, not bypassed.
    assert "governed_chat_invoke" in source
    assert 'cortex_api.complete' not in source
