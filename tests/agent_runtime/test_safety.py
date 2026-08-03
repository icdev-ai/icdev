# CUI // SP-CTI
"""Unit tests for the SAG command-approval safety layer (sag-safe-01).

DB-independent: run_pre_tool_check and the append-only audit sink (store_event)
are faked via shim-aware monkeypatch, so no chat/hook tables are touched.
"""
from __future__ import annotations

import importlib

import pytest

from tools.agent_runtime.safety import (
    ApprovalRequest,
    MODE_MANUAL,
    MODE_OFF,
    MODE_SMART,
    _heuristic_risk,
    _map_to_hook,
    assess_risk,
    build_safety_gate,
    console_approver,
    deny_all_approver,
    resolve_mode,
)

_HOOK = "tools.airgap.hook_compat"


@pytest.fixture(autouse=True)
def _fake_hook(monkeypatch):
    """Fake run_pre_tool_check (allow) + store_event (capture) — no DB."""
    hc = importlib.import_module(_HOOK)
    audits = []
    monkeypatch.setattr(hc, "run_pre_tool_check",
                        lambda tool, inp: {"allowed": True, "reason": "ok"})
    monkeypatch.setattr(hc, "store_event",
                        lambda *a, **k: audits.append((a, k)) or 1)
    monkeypatch.setattr(hc, "get_session_id", lambda: "sess-test")
    return audits


# ---------------------------------------------------------------------------
# resolve_mode
# ---------------------------------------------------------------------------
def test_resolve_mode_default(monkeypatch):
    monkeypatch.delenv("ICDEV_SAG_APPROVAL_MODE", raising=False)
    assert resolve_mode() == MODE_MANUAL


def test_resolve_mode_from_env(monkeypatch):
    monkeypatch.setenv("ICDEV_SAG_APPROVAL_MODE", "smart")
    assert resolve_mode() == MODE_SMART


def test_resolve_mode_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("ICDEV_SAG_APPROVAL_MODE", "smart")
    assert resolve_mode("off") == MODE_OFF


def test_resolve_mode_invalid_falls_back_to_manual():
    assert resolve_mode("bogus") == MODE_MANUAL


# ---------------------------------------------------------------------------
# mapping + heuristic risk
# ---------------------------------------------------------------------------
def test_map_to_hook_run_command():
    tool, inp = _map_to_hook("run_command", {"command": "python tools/x.py"})
    assert tool == "Bash"
    assert inp == {"command": "python tools/x.py"}


def test_map_to_hook_write_file():
    tool, inp = _map_to_hook("write_file", {"path": "a.txt", "content": "hi"})
    assert tool == "Write"
    assert inp["content"] == "hi"
    assert inp["path"] == "a.txt"


def test_heuristic_risk_high_on_destructive():
    assert _heuristic_risk("run_command", {"command": "rm -rf build"}) == "high"


def test_heuristic_risk_write_is_medium():
    assert _heuristic_risk("write_file", {"path": "x.txt", "content": "y"}) == "medium"


def test_heuristic_risk_low_for_an_enumerated_reversible_tool():
    assert _heuristic_risk("read_file", {"path": "tools/status.py"}) == "low"


def test_heuristic_risk_no_longer_low_by_default():
    """ars-appr-01: an unprovable command is not "low" just because it looks tame.

    This previously asserted that ``run_command`` with ``python tools/status.py``
    was low risk, because ``_heuristic_risk`` ended in ``return "low"`` for
    anything that was not ``write_file`` and carried no destructive keyword. But
    that is an arbitrary interpreter invocation through a generic shell —
    ``python tools/deploy.py`` is the same shape — so "low" was an assertion
    about the string's appearance, not about what it does. In ``smart`` mode
    that auto-approved it.

    The heuristic now defers to the reversibility classifier, whose default tier
    for anything unenumerated is ``unknown``. Escalating is the recoverable
    error here; auto-approving is not.
    """
    assert _heuristic_risk("run_command", {"command": "python tools/status.py"}) == "high"
    # A command the policy CAN prove recoverable is still not escalated.
    #
    # NB `git commit`, not `git add` — the pre-existing `_HIGH_RISK_KEYWORDS`
    # entry `"dd "` (for the `dd` disk utility) substring-matches inside
    # `"git add "`, so that command short-circuits to "high" before the
    # classifier is ever consulted. Left alone deliberately: the false positive
    # over-escalates, which fails safe, and narrowing a security keyword list is
    # not this card's scope.
    assert _heuristic_risk("run_command", {"command": "git commit -m x"}) == "medium"


# ---------------------------------------------------------------------------
# assess_risk (LLM path)
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, content):
        self.content = content


class _FakeRouter:
    def __init__(self, content):
        self._content = content
        self.calls = 0

    def invoke(self, function, request):
        self.calls += 1
        return _FakeResp(self._content)


def test_assess_risk_uses_llm_when_router_given():
    router = _FakeRouter("high")
    assert assess_risk("write_file", {"path": "x"}, router=router) == "high"
    assert router.calls == 1


def test_assess_risk_falls_back_to_heuristic_on_bad_llm():
    router = _FakeRouter("gibberish-not-a-level")
    # LLM returns no recognisable level -> heuristic (write_file => medium)
    assert assess_risk("write_file", {"path": "x", "content": "y"}, router=router) == "medium"


# ---------------------------------------------------------------------------
# build_safety_gate — decision matrix
# ---------------------------------------------------------------------------
def test_gate_allows_read_only():
    gate = build_safety_gate(mode=MODE_MANUAL, approver=deny_all_approver)
    allowed, reason = gate("anything", {}, True)
    assert allowed is True
    assert reason == ""


def test_gate_hard_blocks_when_precheck_denies(monkeypatch, _fake_hook):
    hc = importlib.import_module(_HOOK)
    monkeypatch.setattr(hc, "run_pre_tool_check",
                        lambda tool, inp: {"allowed": False, "reason": "BLOCKED: git danger"})
    gate = build_safety_gate(mode=MODE_OFF)  # even yolo cannot override a hard block
    allowed, reason = gate("run_command", {"command": "git push --force"}, False)
    assert allowed is False
    assert "BLOCKED" in reason
    # a denial was audited
    assert any("sag_approval" in str(a) for a, _ in _fake_hook)


def test_gate_off_mode_auto_approves(_fake_hook):
    gate = build_safety_gate(mode=MODE_OFF)
    allowed, _ = gate("write_file", {"path": "x.txt", "content": "y"}, False)
    assert allowed is True
    assert any("approved" in str(a) for a, _ in _fake_hook)


def test_gate_manual_prompts_and_approves():
    seen = {}

    def approver(req: ApprovalRequest) -> bool:
        seen["req"] = req
        return True

    gate = build_safety_gate(mode=MODE_MANUAL, approver=approver)
    allowed, _ = gate("write_file", {"path": "x.txt", "content": "y"}, False)
    assert allowed is True
    assert seen["req"].tool_name == "write_file"


def test_gate_manual_denies_when_operator_says_no():
    gate = build_safety_gate(mode=MODE_MANUAL, approver=deny_all_approver)
    allowed, reason = gate("run_command", {"command": "python tools/x.py"}, False)
    assert allowed is False
    assert "denied" in reason


def test_gate_smart_auto_approves_low_risk():
    # low-risk command + no approver call expected
    def approver(_req):
        raise AssertionError("approver must not be called for low-risk smart mode")

    gate = build_safety_gate(mode=MODE_SMART, approver=approver,
                             router=_FakeRouter("low"))
    allowed, _ = gate("run_command", {"command": "python tools/status.py"}, False)
    assert allowed is True


def test_gate_smart_prompts_on_high_risk():
    calls = {"n": 0}

    def approver(_req):
        calls["n"] += 1
        return True

    gate = build_safety_gate(mode=MODE_SMART, approver=approver,
                             router=_FakeRouter("high"))
    allowed, _ = gate("run_command", {"command": "rm -rf /"}, False)
    assert allowed is True
    assert calls["n"] == 1


def test_gate_denies_when_approver_raises():
    def broken(_req):
        raise RuntimeError("boom")

    gate = build_safety_gate(mode=MODE_MANUAL, approver=broken)
    allowed, _ = gate("write_file", {"path": "x", "content": "y"}, False)
    assert allowed is False


# ---------------------------------------------------------------------------
# console_approver + ApprovalRequest
# ---------------------------------------------------------------------------
def test_console_approver_yes(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _p: "y")
    req = ApprovalRequest("write_file", {"path": "x"}, "medium", "")
    assert console_approver(req) is True


def test_console_approver_no(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _p: "")
    req = ApprovalRequest("write_file", {"path": "x"}, "medium", "")
    assert console_approver(req) is False


def test_console_approver_eof_denies(monkeypatch):
    def _raise(_p):
        raise EOFError

    monkeypatch.setattr("builtins.input", _raise)
    req = ApprovalRequest("run_command", {"command": "x"}, "high", "")
    assert console_approver(req) is False


def test_approval_request_summary_includes_risk_and_preview():
    req = ApprovalRequest("run_command", {"command": "rm -rf x"}, "high", "danger")
    s = req.summary()
    assert "HIGH RISK" in s
    assert "rm -rf x" in s
