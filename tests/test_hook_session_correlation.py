# CUI // SP-CTI
"""Every hook must key its event on the session id Claude Code passes on stdin.

``send_event.get_session_id()`` returns ``str(uuid.uuid4())`` whenever
``CLAUDE_SESSION_ID`` is unset, and each hook runs as a fresh interpreter per
tool call. A hook that calls it directly therefore mints a NEW session for every
event it writes, and nothing keyed on a session can work: AGOV sequence rules
need >=2 events in one session to fire, and the CASE timeline/bundle is defined
per session. Measured on the live board 2026-08-11, before the fix: 9,803 of
9,816 sessions in ``hook_events`` held exactly one event.

These tests pin the precedence (stdin payload first) for the hooks that write a
session-keyed event, so the regression cannot come back silently.
"""
from __future__ import annotations

import importlib
import io
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[1] / ".claude" / "hooks"

# Hooks that write a session-keyed event from a stdin payload, and the minimal
# payload each needs. pre_compact/user_prompt_submit take the same shape but
# reach the DB through other paths; the four here are the store_event callers.
HOOK_PAYLOADS = {
    "post_tool_use": {"tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_output": "ok"},
    "notification": {"message": "hello"},
    "stop": {"reason": "end_turn"},
    "subagent_stop": {"reason": "done"},
}

PAYLOAD_SID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _hooks_on_path():
    added = False
    if str(HOOKS_DIR) not in sys.path:
        sys.path.insert(0, str(HOOKS_DIR))
        added = True
    yield
    if added:
        try:
            sys.path.remove(str(HOOKS_DIR))
        except ValueError:
            pass


def _run_hook(name: str, payload: dict, monkeypatch) -> list:
    """Run a hook's main() against a stdin payload, capturing store_event calls."""
    import send_event

    seen: list = []

    def fake_store_event(session_id, hook_type=None, tool_name=None,
                         payload=None, classification="CUI"):
        seen.append(session_id)
        return 1

    # The hooks do `from send_event import ...` INSIDE main(), so patching the
    # module attribute is what the late import actually resolves.
    monkeypatch.setattr(send_event, "store_event", fake_store_event)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    # CLAUDE_SESSION_ID unset is the condition that exposes the uuid4 fallback.
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

    mod = importlib.import_module(name)
    # Side effects beyond the event write are not under test and can be slow.
    for extra in ("dispatch_extension_hook", "capture_transcript"):
        if hasattr(mod, extra):
            monkeypatch.setattr(mod, extra, lambda *a, **k: None)

    with pytest.raises(SystemExit):
        mod.main()
    return seen


@pytest.mark.parametrize("hook_name", sorted(HOOK_PAYLOADS))
def test_hook_uses_payload_session_id(hook_name, monkeypatch):
    """The stdin session_id is what lands on the event — not a fresh uuid4."""
    payload = dict(HOOK_PAYLOADS[hook_name], session_id=PAYLOAD_SID)
    seen = _run_hook(hook_name, payload, monkeypatch)

    assert seen, f"{hook_name} wrote no event"
    assert seen[0] == PAYLOAD_SID, (
        f"{hook_name} keyed its event on {seen[0]!r} instead of the session id "
        f"Claude Code supplied ({PAYLOAD_SID!r}) — every tool call would open a "
        f"new session and session-keyed detection would go inert"
    )


@pytest.mark.parametrize("hook_name", sorted(HOOK_PAYLOADS))
def test_two_calls_in_one_session_share_a_session_id(hook_name, monkeypatch):
    """Two events in one session must correlate — this is what sequence rules need."""
    payload = dict(HOOK_PAYLOADS[hook_name], session_id=PAYLOAD_SID)
    first = _run_hook(hook_name, payload, monkeypatch)
    second = _run_hook(hook_name, payload, monkeypatch)

    assert first[0] == second[0] == PAYLOAD_SID


@pytest.mark.parametrize("hook_name", sorted(HOOK_PAYLOADS))
def test_falls_back_when_payload_has_no_session_id(hook_name, monkeypatch):
    """No session id on stdin is still tolerated — the hook must never crash."""
    seen = _run_hook(hook_name, dict(HOOK_PAYLOADS[hook_name]), monkeypatch)

    assert seen, f"{hook_name} wrote no event without a payload session id"
    assert seen[0], f"{hook_name} wrote an empty session id"
