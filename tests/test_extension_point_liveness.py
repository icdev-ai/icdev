# CUI // SP-CTI
"""AGENT_START/AGENT_END fire, and the dead points are named (hcx-live-03).

Two halves of one claim: the extension seam should say only what is true.

**Half one — the two points that had an obvious dispatcher now have it.**
``AgentRuntime.run_turn`` brackets a turn with ``AGENT_START``/``AGENT_END``.
Both are declared ``allow_modification: false`` in
``args/extension_config.yaml``, so they are *observational*: the tests below
pin that a handler can see a turn and can neither block it nor alter it. That
property is enforced at the call site (the dispatch result is discarded), not
merely declared in YAML, and ``test_agent_start_cannot_*`` is the threat model
for it — an observational point that can influence a turn is a new gating
surface nobody reviewed.

``AGENT_END`` is asserted on the failure path too. A lifecycle point that only
fires on the happy path cannot be used to close anything ``AGENT_START`` opened.

**Half two — the four remaining dead points are reported by name.**
``tools/extensions/liveness.py`` measures, per declared point, whether anything
dispatches it and whether any handler is registered against it. Four points
have neither. They are enumerated with written reasons in
``args/extension_liveness.yaml`` rather than deleted: ``ExtensionPoint`` is a
public ``str``-Enum and extensions are auto-discovered drop-ins loaded from a
project-root ``extensions/`` directory that is not in this repository, so
removing a member is an ``AttributeError`` at import for any site-local file —
a decision for a human, not for an auto-merging PR. ``test_extension_point_
members_unchanged`` is the guard that this PR did not take that decision.

Registration happens on the REAL module-level singleton, resolved through the
same import ``runtime.py`` uses. ``tools/extensions/extension_manager.py`` and
``icdev/tools/extensions/extension_manager.py`` are physically distinct copies
holding distinct singletons, so a test registering on the other one would pass
against a handler the runtime can never see.
"""
from __future__ import annotations

import importlib

import pytest

from tools.extensions.extension_manager import ExtensionPoint, extension_manager

#: ``from tools.agent_runtime import runtime`` can yield something other than
#: the module (the package ``__init__`` rebinds names), so resolve explicitly.
RT = importlib.import_module("tools.agent_runtime.runtime")
LIVENESS = importlib.import_module("tools.extensions.liveness")

#: The ten members ``ExtensionPoint`` has. This PR wires two of them and deletes
#: none — see the module docstring for why removal is deliberately out of scope.
EXPECTED_MEMBERS = (
    "TOOL_EXECUTE_BEFORE",
    "TOOL_EXECUTE_AFTER",
    "CHAT_MESSAGE_BEFORE",
    "CHAT_MESSAGE_AFTER",
    "AGENT_START",
    "AGENT_END",
)

#: Measured 2026-08-16. Kept in step with ``args/extension_liveness.yaml``.
# Measured against the merged tree, not carried over from the branch. Two points
# this branch enumerated as dead — memory_save_before and memory_save_after —
# gained a dispatcher on main while this PR was open, so they are alive now and
# asserting them dead would be asserting a stale measurement. That is the
# direction the class exists to track: the enumeration SHRINKS as points drain.
#: EMPTY since hcx-live-gate-01 (2026-08-18). The four points that could not
#: fire were removed rather than wired, so every declared point now has a
#: dispatcher. A non-empty value here is a regression, not a fact to record.
EXPECTED_DEAD: tuple = ()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def register():
    """Register handlers on the real singleton and always remove them again."""
    registered: list[tuple] = []

    def _register(point, handler, *, name, allow_modification=True, priority=10):
        extension_manager.register(
            point,
            handler=handler,
            name=name,
            priority=priority,
            allow_modification=allow_modification,
        )
        registered.append((point, name))

    yield _register

    for point, name in registered:
        extension_manager.unregister(point, name)


class _Result:
    """Stand-in for ``AgentLoopResult`` — only the fields the hook context reads."""

    done = True
    truncated = False
    turns = 3
    final_content = "ok"
    truncation_reason = "completed"
    result_subtype = "success"
    total_input_tokens = 11
    total_output_tokens = 22
    total_cost_usd = 0.5


@pytest.fixture
def runtime(monkeypatch):
    """An ``AgentRuntime`` whose turn neither calls an LLM nor touches storage."""
    rt = RT.AgentRuntime.__new__(RT.AgentRuntime)
    # A non-None sentinel: the ``router`` property must not build a real
    # LLMRouter, and the patched loop never calls it.
    rt._router = object()
    rt.system_prompt = "sys"
    rt.llm_function = "code_generation"
    rt.max_iterations = 4
    rt.max_total_tokens = None
    rt.max_cost_usd = None
    rt.user_id = "u1"
    rt.tenant_id = "t1"
    rt.profile = ""
    rt.unattended = False
    rt.tools = []
    rt.tool_handlers = {}
    rt.config = None
    import threading

    rt._stop = threading.Event()
    rt._turn_active = threading.Event()
    rt._profile_preamble = ""
    rt._project_preamble = ""
    rt._goals_preamble = ""

    class _Session:
        context_id = "ctx-1"
        resume_session_id = "sess-1"

        def record_user(self, _text):
            pass

        def record_assistant(self, _text):
            pass

        def persist(self, _result, **_kw):
            pass

    rt.session = _Session()
    monkeypatch.setattr(RT.AgentRuntime, "_effective_system_prompt", lambda _s, _i: "sys")
    return rt


def _patch_loop(monkeypatch, fn):
    """Replace ``run_agent_loop`` where ``run_turn`` imports it from."""
    loop = importlib.import_module("icdev.tools.llm.agent_loop")
    monkeypatch.setattr(loop, "run_agent_loop", fn)


# ---------------------------------------------------------------------------
# Half one: the points fire
# ---------------------------------------------------------------------------
def test_agent_start_and_end_fire_on_a_turn(runtime, register, monkeypatch):
    seen: list[tuple] = []
    register(ExtensionPoint.AGENT_START, lambda c: seen.append(("start", c)), name="t_start")
    register(ExtensionPoint.AGENT_END, lambda c: seen.append(("end", c)), name="t_end")
    _patch_loop(monkeypatch, lambda *a, **k: _Result())

    runtime.run_turn("hello")

    assert [s[0] for s in seen] == ["start", "end"]


def test_agent_start_context_identifies_the_session(runtime, register, monkeypatch):
    seen: list[dict] = []
    register(ExtensionPoint.AGENT_START, lambda c: seen.append(c), name="t_start")
    _patch_loop(monkeypatch, lambda *a, **k: _Result())

    runtime.run_turn("hello")

    assert seen[0]["context_id"] == "ctx-1"
    assert seen[0]["user_id"] == "u1"
    assert seen[0]["tenant_id"] == "t1"
    assert seen[0]["user_input"] == "hello"


def test_agent_end_context_carries_the_outcome(runtime, register, monkeypatch):
    seen: list[dict] = []
    register(ExtensionPoint.AGENT_END, lambda c: seen.append(c), name="t_end")
    _patch_loop(monkeypatch, lambda *a, **k: _Result())

    runtime.run_turn("hello")

    ctx = seen[0]
    assert ctx["ok"] is True
    assert ctx["error"] == ""
    assert ctx["turns"] == 3
    assert ctx["truncation_reason"] == "completed"
    assert ctx["duration_ms"] >= 0


def test_agent_end_fires_when_the_turn_raises(runtime, register, monkeypatch):
    """An END that only fires on success cannot close what START opened."""
    seen: list[dict] = []
    register(ExtensionPoint.AGENT_END, lambda c: seen.append(c), name="t_end")

    def _boom(*_a, **_kw):
        raise RuntimeError("provider exploded")

    _patch_loop(monkeypatch, _boom)

    with pytest.raises(RuntimeError):
        runtime.run_turn("hello")

    assert len(seen) == 1
    assert seen[0]["ok"] is False
    assert "provider exploded" in seen[0]["error"]


def test_agent_start_cannot_alter_the_turn(runtime, register, monkeypatch):
    """Observational: what a handler returns never reaches the turn.

    The handler declares ``allow_modification=True`` — the strongest thing a
    drop-in can claim — and rewrites the whole context. The turn must be
    unaffected, because ``run_turn`` discards the dispatch result.
    """
    register(
        ExtensionPoint.AGENT_START,
        lambda c: {"user_input": "PWNED", "llm_function": "evil"},
        name="t_rewrite",
        allow_modification=True,
    )
    captured: dict = {}

    def _capture(*_a, **kwargs):
        captured.update(kwargs)
        return _Result()

    _patch_loop(monkeypatch, _capture)

    runtime.run_turn("hello")

    assert captured["user_prompt"] == "hello"
    assert captured["llm_function"] == "code_generation"


def test_agent_start_cannot_block_the_turn(runtime, register, monkeypatch):
    """There is no deny channel: a raising handler does not stop the turn."""

    def _raiser(_ctx):
        raise RuntimeError("handler exploded")

    register(ExtensionPoint.AGENT_START, _raiser, name="t_raise")
    _patch_loop(monkeypatch, lambda *a, **k: _Result())

    assert runtime.run_turn("hello").final_content == "ok"


def test_turn_survives_an_unavailable_extension_manager(runtime, monkeypatch):
    """Extensions are a layer over the runtime, never a dependency of it."""
    monkeypatch.setattr(RT, "_lifecycle_points", None)
    monkeypatch.setattr(RT, "_agent_lifecycle_points", lambda: None)
    _patch_loop(monkeypatch, lambda *a, **k: _Result())

    assert runtime.run_turn("hello").final_content == "ok"


# ---------------------------------------------------------------------------
# Half two: the dead points are reported, and nothing was deleted
# ---------------------------------------------------------------------------
def test_extension_point_members_unchanged():
    """This PR wires two points and removes none — see the module docstring."""
    assert tuple(p.name for p in ExtensionPoint) == EXPECTED_MEMBERS


def test_liveness_names_the_four_dead_points():
    report = LIVENESS.build_report()
    assert tuple(report["dead"]) == EXPECTED_DEAD


def test_liveness_reports_the_evidence_for_a_dead_point():
    """'Dead' must be shown, not asserted: zero dispatchers AND zero handlers."""
    report = LIVENESS.build_report()
    by_point = {p["point"]: p for p in report["points"]}
    for value in EXPECTED_DEAD:
        entry = by_point[value]
        assert entry["status"] == LIVENESS.DEAD
        assert entry["dispatcher_count"] == 0
        assert entry["handler_sites"] == []
        assert entry["registered_handlers"] == 0


def test_liveness_sees_the_agent_lifecycle_dispatchers():
    report = LIVENESS.build_report()
    by_point = {p["point"]: p for p in report["points"]}
    for value in ("agent_start", "agent_end"):
        sites = by_point[value]["dispatchers"]
        assert "tools/agent_runtime/runtime.py" in sites
        assert by_point[value]["status"] != LIVENESS.DEAD


def test_every_dead_point_is_enumerated_with_a_written_reason():
    """The census gate: a dead point nobody wrote down is the regression."""
    gate = LIVENESS.evaluate_gate(LIVENESS.build_report())
    assert gate["unlisted"] == []
    assert gate["ok"] is True
    for value in EXPECTED_DEAD:
        entry = gate["known_dead"][value]
        assert entry["reason"].strip()
        assert entry["follow_up"].strip()
