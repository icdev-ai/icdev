# CUI // SP-CTI
"""ARR — Agent Runtime Resilience.

The SAG dispatcher used to collapse every tool failure to
``error executing <tool>: <exc>``, so a network blip, an absent library, a
denied permission and a genuine bug were indistinguishable to the model. These
tests pin the taxonomy, the single safe retry, the refusal to install anything,
and the refusal to replay a mutating tool.
"""
from __future__ import annotations

import threading

import pytest

from tools.agent_runtime.discovery import ToolSpec
from tools.agent_runtime.dispatch import make_handler
from tools.agent_runtime.error_recovery import (
    DEGRADE,
    ESCALATE,
    RETRY_SAFE,
    TERMINAL,
    Classification,
    ToolResult,
    classify,
)


def _spec(name: str, *, read_only: bool = True) -> ToolSpec:
    return ToolSpec(
        name=name,
        schema={"type": "function", "function": {"name": name}},
        source="builtin",
        read_only=read_only,
    )


def _allow_all(_name, _inp, _ro):
    return True, ""


def _patch_create_tasks(monkeypatch, fn):
    """Patch task_factory.create_tasks shim-safely.

    ``tools.*`` re-exports ``icdev.tools.*``; a string-form monkeypatch target
    resolves through the shim and can bind a different module object than the
    one ``error_recovery`` imports. Patch the resolved module directly.
    """
    import importlib

    mod = importlib.import_module("tools.kanban.task_factory")
    monkeypatch.setattr(mod, "create_tasks", fn)
    return mod


# ---------------------------------------------------------------------------
# Taxonomy (arr-tax-01)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "exc,expected",
    [
        (ModuleNotFoundError("No module named 'fitz'"), DEGRADE),
        (ImportError("cannot import name 'x'"), DEGRADE),
        (ConnectionRefusedError("refused"), RETRY_SAFE),
        (TimeoutError("slow"), RETRY_SAFE),
        (ConnectionResetError("reset"), RETRY_SAFE),
        (PermissionError("denied"), ESCALATE),
        (MemoryError("oom"), ESCALATE),
        (ValueError("bad argument"), TERMINAL),
        (AttributeError("no attribute"), TERMINAL),
        (KeyError("missing"), TERMINAL),
    ],
)
def test_classification(exc, expected):
    assert classify(exc).disposition == expected


def test_permission_error_escalates_even_though_it_is_an_oserror():
    """PermissionError subclasses OSError, which is in the environmental set.

    Ordering matters: a denied permission must never be treated as transient and
    silently retried.
    """
    assert issubclass(PermissionError, OSError)
    assert classify(PermissionError("denied")).disposition == ESCALATE


def test_explicit_retryable_contract_beats_type_inference():
    """tools/resilience/errors.py raisers declare their own retryability."""
    from tools.resilience.errors import ICDevPermanentError, ICDevTransientError

    assert classify(ICDevTransientError("throttled")).disposition == RETRY_SAFE
    assert classify(ICDevPermanentError("bad config")).disposition == ESCALATE


def test_missing_module_name_is_extracted():
    c = classify(ModuleNotFoundError("No module named 'pymupdf'"))
    assert c.missing_capability == "pymupdf"
    assert c.disposition == DEGRADE


def test_environmental_set_is_shared_with_self_debug():
    """Two lists of 'environmental' exceptions would drift apart and disagree."""
    from tools.agent_runtime import error_recovery
    from tools.workflow.self_debug import _QUARANTINE_EXCEPTIONS

    error_recovery._environmental_cache = None
    assert error_recovery._environmental_exceptions() == frozenset(_QUARANTINE_EXCEPTIONS)


def test_classify_never_raises_on_odd_input():
    class Weird(Exception):
        def __str__(self):
            raise RuntimeError("unstringifiable")

    # Must not propagate — classification is on the failure path already.
    try:
        classify(Weird())
    except RuntimeError:
        pytest.fail("classify() must never raise")


# ---------------------------------------------------------------------------
# Structured result (arr-res-01)
# ---------------------------------------------------------------------------

def test_result_render_leads_with_the_machine_checkable_fields():
    r = ToolResult(
        success=False, output="boom", error_type="TimeoutError",
        disposition=RETRY_SAFE, remediation_hint="try again", retried=True,
    )
    text = r.render()
    assert text.startswith("error [retry_safe]")
    assert "type=TimeoutError" in text
    assert "retried=1" in text
    assert "try again" in text


def test_successful_result_renders_only_the_output():
    assert ToolResult(success=True, output="42").render() == "42"


def test_result_to_dict_round_trips_the_disposition():
    d = ToolResult(success=False, disposition=DEGRADE, missing_capability="fitz").to_dict()
    assert d["disposition"] == DEGRADE
    assert d["missing_capability"] == "fitz"
    assert d["success"] is False


# ---------------------------------------------------------------------------
# Single safe retry (arr-res-02)
# ---------------------------------------------------------------------------

def test_transient_failure_on_a_read_only_tool_retries_once_and_succeeds(monkeypatch):
    monkeypatch.setattr("tools.agent_runtime.error_recovery.retry_delay_seconds", lambda *_: 0.0)
    calls = {"n": 0}

    def flaky(_inp, _stop):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionResetError("first attempt fails")
        return "recovered"

    h = make_handler(_spec("flaky"), gate=_allow_all, builtin_handlers={"flaky": flaky})
    assert h({}, None) == "recovered"
    assert calls["n"] == 2, "should have retried exactly once"


def test_retry_is_attempted_at_most_once(monkeypatch):
    monkeypatch.setattr("tools.agent_runtime.error_recovery.retry_delay_seconds", lambda *_: 0.0)
    calls = {"n": 0}

    def always_flaky(_inp, _stop):
        calls["n"] += 1
        raise TimeoutError("still down")

    h = make_handler(_spec("t"), gate=_allow_all, builtin_handlers={"t": always_flaky})
    out = h({}, None)
    assert calls["n"] == 2, "one retry, not a loop"
    assert "retried=1" in out
    assert "[retry_safe]" in out


def test_a_mutating_tool_is_never_replayed(monkeypatch):
    """The safety property.

    A mutating tool that failed part-way may already have applied its side
    effect. Transience is not a licence to duplicate a mutation, so a mutating
    tool is classified and reported but never re-executed.
    """
    monkeypatch.setattr("tools.agent_runtime.error_recovery.retry_delay_seconds", lambda *_: 0.0)
    calls = {"n": 0}

    def writer(_inp, _stop):
        calls["n"] += 1
        raise ConnectionResetError("dropped mid-write")

    h = make_handler(
        _spec("write_file", read_only=False),
        gate=_allow_all,
        builtin_handlers={"write_file": writer},
    )
    out = h({}, None)
    assert calls["n"] == 1, "a mutating tool must not be replayed"
    assert "retried=1" not in out


def test_no_retry_when_the_stop_event_is_set(monkeypatch):
    monkeypatch.setattr("tools.agent_runtime.error_recovery.retry_delay_seconds", lambda *_: 0.0)
    calls = {"n": 0}

    def flaky(_inp, _stop):
        calls["n"] += 1
        raise TimeoutError("down")

    stop = threading.Event()
    stop.set()
    h = make_handler(_spec("t"), gate=_allow_all, builtin_handlers={"t": flaky})
    h({}, stop)
    assert calls["n"] == 1, "a cancelled run must not start a retry"


def test_terminal_failures_are_not_retried(monkeypatch):
    monkeypatch.setattr("tools.agent_runtime.error_recovery.retry_delay_seconds", lambda *_: 0.0)
    calls = {"n": 0}

    def bad(_inp, _stop):
        calls["n"] += 1
        raise ValueError("wrong argument")

    h = make_handler(_spec("b"), gate=_allow_all, builtin_handlers={"b": bad})
    out = h({}, None)
    assert calls["n"] == 1
    assert "[terminal]" in out


# ---------------------------------------------------------------------------
# Declare and degrade — never install (arr-deg-01)
# ---------------------------------------------------------------------------

def test_missing_capability_degrades_and_refuses_to_install():
    def needs_fitz(_inp, _stop):
        raise ModuleNotFoundError("No module named 'fitz'")

    h = make_handler(_spec("pdf"), gate=_allow_all, builtin_handlers={"pdf": needs_fitz})
    out = h({}, None)

    assert "[degrade]" in out
    assert "missing=fitz" in out
    assert "will NOT install" in out
    assert "wheel_vendor" in out, "must point at the vendored-wheel path"
    assert "Do not retry" in out


def test_no_install_machinery_exists_anywhere_in_the_runtime():
    """Regression guard for the recommendation this card deliberately rejected.

    The source audit proposed an ``install_dependency`` tool running pip inside
    the agent loop. ICDEV targets air-gapped IL4-IL6 with SBOM/SLSA gates; an
    agent that mutates its own runtime invalidates the attestation chain.

    Looks for machinery that would *execute* an install, not for the string
    "pip install" — ``safety.py`` legitimately lists it as a high-risk keyword
    so the risk assessor flags an agent that tries to run one.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    # Executable install forms, not mentions.
    patterns = (
        re.compile(r"\binstall_dependency\b"),
        re.compile(r"""["']-m["']\s*,\s*["']pip["']"""),
        re.compile(r"\bpip_main\b|\bpip\.main\b"),
        re.compile(r"""subprocess\.[\w]+\([^)]*["']pip["']"""),
    )
    offenders = []
    for py in (root / "tools" / "agent_runtime").rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        for pat in patterns:
            if pat.search(text):
                offenders.append(f"{py.name}: {pat.pattern}")
    assert not offenders, f"runtime self-installation machinery found: {offenders}"


def test_safety_layer_already_treats_pip_install_as_high_risk():
    """Corroborates the declare-and-degrade decision from the other direction.

    The agent's terminal tool is already gated: attempting `pip install` through
    `run_command` is scored high-risk by the approval layer. Building an
    install tool would have routed around a control the platform already has.
    """
    from tools.agent_runtime.safety import _HIGH_RISK_KEYWORDS

    assert "pip install" in _HIGH_RISK_KEYWORDS


# ---------------------------------------------------------------------------
# Escalation (arr-esc-01)
# ---------------------------------------------------------------------------

def test_escalation_files_a_card_through_task_factory(monkeypatch):
    from tools.agent_runtime import error_recovery

    error_recovery._escalated.clear()
    seen: list[dict] = []
    _patch_create_tasks(
        monkeypatch, lambda specs: (seen.extend(specs), [s["id"] for s in specs])[1]
    )

    card = error_recovery.file_escalation_card(
        tool_name="run_command",
        classification=Classification(ESCALATE, "PermissionError", "denied"),
        error_message="access is denied",
        tool_input={"command": "rm -rf /", "cwd": "/"},
    )

    assert card and card.startswith("arr-esc-")
    assert len(seen) == 1
    assert seen[0]["status"] == "suggested", "must not be runner-dispatchable"
    assert seen[0]["priority"] == "high"


def test_escalation_card_records_argument_names_but_not_values(monkeypatch):
    """A tool input can carry CUI and the card is board-visible."""
    from tools.agent_runtime import error_recovery

    error_recovery._escalated.clear()
    seen: list[dict] = []
    _patch_create_tasks(
        monkeypatch, lambda specs: (seen.extend(specs), [s["id"] for s in specs])[1]
    )

    error_recovery.file_escalation_card(
        tool_name="t",
        classification=Classification(ESCALATE, "PermissionError", "denied"),
        error_message="nope",
        tool_input={"secret_token": "hunter2-SUPERSECRET", "path": "/etc/shadow"},
    )
    body = seen[0]["description"]
    assert "secret_token" in body
    assert "hunter2-SUPERSECRET" not in body
    assert "/etc/shadow" not in body


def test_repeated_failures_do_not_paper_the_board(monkeypatch):
    from tools.agent_runtime import error_recovery

    error_recovery._escalated.clear()
    calls: list[dict] = []
    _patch_create_tasks(
        monkeypatch, lambda specs: (calls.extend(specs), [s["id"] for s in specs])[1]
    )

    for _ in range(5):
        error_recovery.file_escalation_card(
            tool_name="t",
            classification=Classification(ESCALATE, "PermissionError", "denied"),
            error_message="nope",
        )
    assert len(calls) == 1, "one card per tool+error per process"


def test_escalation_id_is_stable_so_reruns_deduplicate():
    from tools.agent_runtime.error_recovery import _escalation_id

    assert _escalation_id("t", "PermissionError") == _escalation_id("t", "PermissionError")
    assert _escalation_id("t", "PermissionError") != _escalation_id("t", "TimeoutError")


def test_escalation_never_raises_when_the_board_is_unreachable(monkeypatch):
    from tools.agent_runtime import error_recovery

    error_recovery._escalated.clear()

    def _boom(_specs):
        raise RuntimeError("database is down")

    _patch_create_tasks(monkeypatch, _boom)
    assert error_recovery.file_escalation_card(
        tool_name="t",
        classification=Classification(ESCALATE, "PermissionError", "denied"),
        error_message="nope",
    ) is None


# ---------------------------------------------------------------------------
# Composition guarantees (ADR D384)
# ---------------------------------------------------------------------------

def test_blocked_tools_still_report_through_the_safety_gate():
    """The gate short-circuits before the taxonomy — unchanged behaviour."""
    h = make_handler(
        _spec("w", read_only=False),
        gate=lambda *_: (False, "not approved"),
        builtin_handlers={},
    )
    assert h({}, None).startswith("blocked: ")


def test_successful_calls_are_untouched():
    h = make_handler(
        _spec("ok"), gate=_allow_all, builtin_handlers={"ok": lambda _i, _s: "fine"}
    )
    assert h({}, None) == "fine"
