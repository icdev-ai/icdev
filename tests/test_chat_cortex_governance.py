# CUI // SP-CTI
"""Chat turns run through Cortex's TRUST chain.

Chat called LLMRouter directly, so none of Cortex's gates applied to it — no
injection screen on the single largest injection surface in the platform, and
no audit row, which is why /cortex/metrics reported no traffic for weeks while
chat was busy.

The first version of this change wrapped `router.invoke` rather than calling
`cortex_api.complete()`, on the rationale that `complete()` took a bare prompt
and would drop the ~180 lines `_process_message` assembles — RICOAS
constitution, corrections, RAG, KG, live compliance, hybrid memory,
compression — along with `ctx.agent_model`.

**That rationale expired before it was written.** Commit 9ec85e124 (2026-07-12)
added `history=` and `model=` to `complete()` expressly as the chat enabler and
deferred the adoption itself only to avoid a merge conflict in this file; the
comment describing the limitation landed on 2026-07-31, nineteen days later.
cxo-adopt-01 completes the deferred half: plain turns now call the facade with
the assembly threaded through `history=`, while the reasoned-codegen branch
keeps the wrapper because `cortex.reason` is not equivalent to
`generate_reasoned_code`.

So the properties under test are: the gates run, the *governed* text is what
reaches the model, the assembled context survives the move, a refusal is
rendered as a refusal rather than swallowed into the echo fallback, and a
failure in the governance layer degrades to an ungoverned call rather than
taking chat down.
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
    """The assembly must survive the move to the facade (cxo-adopt-01).

    This test used to assert `cortex_api.complete` was ABSENT, on the rationale
    that calling it would drop ~180 lines of assembly. That rationale expired:
    9ec85e124 added history= and model= to complete() expressly as the chat
    enabler, and deferred this adoption only to avoid a merge conflict in this
    file. The markers below are the real value of the test and still hold — the
    assembly is untouched, it is now threaded through `history=` instead of an
    LLMRequest built here.
    """
    import inspect

    source = inspect.getsource(cm.ChatManager._process_message)

    for marker in ("_rag_retrieve", "_kg_context_retrieve", "_compress_history",
                   "agent_model", "reasoned_codegen"):
        assert marker in source, f"{marker} lost from _process_message"

    # Plain turns go through the facade; reasoned codegen keeps the wrapper.
    assert "complete_via_cortex" in source, "plain branch no longer uses the facade"
    assert "governed_chat_invoke" in source, "reasoned branch lost its governance wrapper"


def test_assembled_context_is_threaded_into_history():
    """The assembly is worthless if it is not actually passed to the model."""
    import inspect

    source = inspect.getsource(cm.complete_via_cortex)
    assert "history=" in source, "assembled conversation is not passed as history"
    assert "model=" in source, "per-session model pin is not passed"
    assert 'function="chat_response"' in source, "routing key changed"


def test_history_splits_at_the_current_user_turn():
    """history must be everything BEFORE the current turn, not the whole list."""
    conversation = [
        {"role": "system", "content": "constitution"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "current"},
    ]
    history = cm._split_history(conversation, "current")
    assert history == conversation[:3]
    assert all(m["content"] != "current" for m in history), (
        "the current turn must not appear twice — complete() re-appends the "
        "governed prompt as the final user message"
    )


def test_history_split_tolerates_a_trailing_non_user_message():
    """Splitting at the LAST user turn, not at [:-1]."""
    conversation = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "current"},
        {"role": "assistant", "content": "trailing"},
    ]
    assert cm._split_history(conversation, "current") == conversation[:1]


def test_plain_branch_is_not_double_governed():
    """complete() governs internally; wrapping it too would double-audit."""
    import inspect

    source = inspect.getsource(cm.ChatManager._process_message)
    plain = source[source.index('if reasoning_mode == "off"'):]
    plain = plain[: plain.index("else:")]
    assert "governed_chat_invoke" not in plain, (
        "the plain branch is wrapped AND uses the facade — two audit rows per "
        "turn, doubled cost on /cortex/metrics, redaction over masked text"
    )


def test_governance_refusal_precedes_the_echo_fallback():
    """A blocked turn must not be rendered as a normal acknowledgement.

    _process_message's catch-all is `except (ImportError, Exception)` returning
    "[Agent x] Acknowledged: ...". If GovernanceBlockedError reached it, a
    refusal would be invisible to the user and to the transcript.
    """
    import inspect

    source = inspect.getsource(cm.ChatManager._process_message)
    blocked = source.index("except GovernanceBlockedError")
    catchall = source.index("except (ImportError, Exception)")
    assert blocked < catchall, (
        "GovernanceBlockedError is caught after the catch-all, so refusals are "
        "swallowed into the echo fallback"
    )
    assert "_CHAT_BLOCKED_TEMPLATE" in source[blocked:catchall], (
        "the refusal must use the shared template so both branches read alike"
    )


def test_chat_is_attributable_in_the_audit_trail():
    """agent_id must be explicit once the operation label becomes cortex.complete."""
    import inspect

    source = inspect.getsource(cm._build_chat_cortex_ctx)
    assert 'agent_id="chat"' in source, (
        "without an explicit agent_id, _build_request derives cortex:<tenant> "
        "and chat traffic is indistinguishable from any other caller"
    )


# ---------------------------------------------------------------------------
# Behavioural: what complete() actually receives
# ---------------------------------------------------------------------------


class _Ctx:
    tenant_id = "t1"
    user_id = "u1"
    context_id = "ctx-42"
    agent_model = "sonnet"
    project_id = "p1"
    reasoning_mode = "off"


def test_complete_receives_the_assembled_context(monkeypatch):
    """The decisive test: real kwargs, not a source grep."""
    import tools.cortex.api as cortex_api

    seen = {}

    class _Result:
        text = "answer"

    def _fake_complete(prompt, function=None, ctx=None, history=None, model=None, **kw):
        seen.update(prompt=prompt, function=function, ctx=ctx, history=history, model=model)
        return _Result()

    monkeypatch.setattr(cortex_api, "complete", _fake_complete)

    conversation = [
        {"role": "system", "content": "RICOAS constitution + RAG + KG"},
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "earlier reply"},
        {"role": "user", "content": "current question"},
    ]

    out = cm.complete_via_cortex(
        conversation, "current question", _Ctx(),
        lambda p: pytest.fail("fell back despite Cortex being available"),
    )

    assert out == "answer"
    assert seen["prompt"] == "current question"
    assert seen["function"] == "chat_response"
    assert seen["model"] == "sonnet"
    # The assembly is preserved and the current turn is not duplicated.
    assert seen["history"] == conversation[:3]
    assert seen["history"][0]["content"].startswith("RICOAS constitution")
    # Identity is carried for the audit trail.
    assert seen["ctx"].agent_id == "chat"
    assert seen["ctx"].session_id == "ctx-42"
    assert seen["ctx"].fail_closed is False


def test_blocked_completion_propagates_for_the_caller_to_render(monkeypatch):
    """complete_via_cortex must not swallow a refusal into the fallback."""
    import tools.cortex.api as cortex_api
    from tools.cortex.governance import GovernanceBlockedError

    def _blocked(*a, **k):
        raise GovernanceBlockedError("pre_check", "prompt injection", None)

    monkeypatch.setattr(cortex_api, "complete", _blocked)

    with pytest.raises(GovernanceBlockedError):
        cm.complete_via_cortex(
            [{"role": "user", "content": "x"}], "x", _Ctx(),
            lambda p: "fallback must not be used",
        )


def test_missing_cortex_degrades_to_the_ungoverned_call(monkeypatch):
    """Cortex is optional; its absence must not take chat down."""
    import builtins

    real_import = builtins.__import__

    def _no_cortex(name, *a, **k):
        if name == "tools.cortex" or name.startswith("tools.cortex."):
            raise ImportError("cortex not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_cortex)

    out = cm.complete_via_cortex(
        [{"role": "user", "content": "x"}], "x", _Ctx(), lambda p: f"ungoverned:{p}",
    )
    assert out == "ungoverned:x"
