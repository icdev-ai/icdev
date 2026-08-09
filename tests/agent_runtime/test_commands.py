# CUI // SP-CTI
"""Unit tests for the SAG slash-command registry (sag-rt-02).

The registry is exercised against a lightweight fake runtime so the tests are
DB-independent (matching the shared conftest schema, which does not provision
chat tables). Verifies data-driven dispatch, aliases, the sag-mem-01 /
sag-safe-02 handlers, and the AgentRuntime command-handler wiring.

Also guards the module docstring's claims ABOUT ITSELF (hgx-doc-02): the parity
test it cites must exist, and no shipped command may be described as a stub.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import tools.agent_runtime.commands as cmds
from tools.agent_runtime.commands import command_names, dispatch


class _FakeSession:
    def __init__(self) -> None:
        self.context_id = "ctx-1"
        self.title = "Untitled session"
        self._msgs: list[dict[str, Any]] = []

    def set_title(self, t: str) -> None:
        self.title = t

    def messages(self, limit: int = 50):
        return self._msgs[-limit:]

    def usage(self):
        return {
            "turns": 2,
            "input_tokens": 100,
            "output_tokens": 40,
            "total_tokens": 140,
            "cost_usd": 0.0012,
            "session_id": "sess-9",
            "context_id": self.context_id,
        }


class _FakeRuntime:
    def __init__(self) -> None:
        self.session = _FakeSession()
        self._ctr = 1

    def new_session(self, title: str = "Untitled session"):
        self._ctr += 1
        self.session = _FakeSession()
        self.session.context_id = f"ctx-{self._ctr}"
        self.session.title = title
        return self.session

    def tool_names(self):
        return ["health_check", "read_file", "search_files"]


def test_dispatch_unknown_is_handled_not_llm():
    handled, resp, exit_ = dispatch(_FakeRuntime(), "/bogus")
    assert handled is True and exit_ is False
    assert "Unknown command" in resp


def test_new_and_clear():
    rt = _FakeRuntime()
    old = rt.session.context_id
    _h, resp, _e = dispatch(rt, "/new My Session")
    assert rt.session.context_id != old
    assert rt.session.title == "My Session"
    assert "Started new session" in resp

    rt.session.title = "Keep Me"
    _h, resp, _e = dispatch(rt, "/clear")
    assert rt.session.title == "Keep Me"
    assert "Cleared" in resp


def test_title_show_and_set():
    rt = _FakeRuntime()
    _h, resp, _e = dispatch(rt, "/title")
    assert "Current title" in resp
    _h, resp, _e = dispatch(rt, "/title New Name")
    assert rt.session.title == "New Name"
    assert "Title set to: New Name" in resp


def test_tools_lists_registered():
    _h, resp, _e = dispatch(_FakeRuntime(), "/tools")
    assert "read_file" in resp and "3 tools" in resp


def test_usage_formats_stats():
    _h, resp, _e = dispatch(_FakeRuntime(), "/usage")
    assert "140 tokens" in resp and "sess-9" in resp


def test_memory_lists_facts_or_reports_empty():
    # sag-mem-01: /memory is implemented. With no stored facts it reports empty
    # and points at the remember/auto-capture path.
    _h, resp, _e = dispatch(_FakeRuntime(), "/memory")
    assert "remembered facts" in resp.lower()


def test_memory_forget_requires_argument():
    _h, resp, _e = dispatch(_FakeRuntime(), "/memory forget")
    assert "Usage:" in resp


def test_rollback_reports_no_checkpoints_when_empty(monkeypatch):
    # sag-safe-02: /rollback is now implemented. With no checkpoints it says so.
    import tools.agent_runtime.commands as cmds_mod
    import tools.agent_runtime.checkpoints as cp_mod

    monkeypatch.setattr(cp_mod, "list_checkpoints", lambda: [])
    _h, resp, _e = dispatch(_FakeRuntime(), "/rollback")
    assert "No checkpoints" in resp
    assert cmds_mod  # module import used


def test_snapshot_requires_a_path():
    _h, resp, _e = dispatch(_FakeRuntime(), "/snapshot")
    assert "Usage:" in resp


def test_exit_signals_shutdown():
    _h, resp, exit_ = dispatch(_FakeRuntime(), "/exit")
    assert exit_ is True and "Goodbye" in resp
    # /quit is an alias for /exit
    _h, _r, exit2 = dispatch(_FakeRuntime(), "/quit")
    assert exit2 is True


def test_help_lists_commands_without_duplicating_aliases():
    _h, resp, _e = dispatch(_FakeRuntime(), "/help")
    assert "/new" in resp and "/usage" in resp
    # /? is an alias of /help — help body should list /help once, not /? too.
    assert resp.count("List available commands") == 1


def test_skills_lists_or_reports(monkeypatch):
    # Patch the resolved module object (the tools.* shim redirects to
    # icdev.tools.*, so a string-form target is unreliable — see
    # feedback_shim_aware_monkeypatch).
    import importlib

    mod = importlib.import_module("tools.skills.registry")
    monkeypatch.setattr(
        mod,
        "load_registry",
        lambda *a, **k: {"skills": {"icdev-build": {}, "icdev-status": {}}},
    )
    _h, resp, _e = dispatch(_FakeRuntime(), "/skills")
    assert "icdev-build" in resp and "2 skills" in resp


def test_command_names_include_aliases():
    names = command_names()
    for expected in ("/new", "/clear", "/title", "/tools", "/skills", "/memory",
                     "/usage", "/rollback", "/help", "/?", "/exit", "/quit"):
        assert expected in names


def test_handler_exception_is_caught(monkeypatch):
    def boom(_rt, _arg):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(cmds.REGISTRY, "/boom", cmds.Command(boom, "boom"))
    handled, resp, exit_ = dispatch(_FakeRuntime(), "/boom")
    assert handled is True and exit_ is False
    assert "error running /boom" in resp and "kaboom" in resp


def test_build_runtime_wires_dispatch(monkeypatch):
    captured = {}

    class _FakeAR:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr("tools.agent_runtime.runtime.AgentRuntime", _FakeAR)
    cmds.build_runtime(router=object())
    assert captured["command_handler"] is dispatch


# ---------------------------------------------------------------------------
# The docstring's claims about itself (hgx-doc-02)
#
# `test_goal_commands.py::test_docstring_matches_registry` asserts every
# REGISTRY key appears in the docstring. Nothing asserted that what the
# docstring SAYS is true — which is how it spent several phases citing a test
# module that does not enforce it, and how a shipped command can keep being
# described as unbuilt.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_docstring_cites_a_parity_test_that_exists():
    """Every `tests/...py` path named in the docstring resolves to a real file."""
    doc = cmds.__doc__ or ""
    cited = re.findall(r"tests/[\w/]+\.py", doc)
    assert cited, "docstring names no parity test — drift has no named guard"
    for ref in cited:
        assert (_REPO_ROOT / ref).is_file(), f"docstring cites missing test file {ref}"


def test_no_registered_command_is_documented_as_a_stub():
    """A shipped command described as a stub sends readers to build it again."""
    doc = cmds.__doc__ or ""
    for line in doc.splitlines():
        if line.lstrip().startswith("- ``/"):
            assert "stub" not in line.lower(), f"shipped command documented as a stub: {line.strip()}"


def test_every_registered_command_has_a_callable_handler():
    """No REGISTRY entry is a placeholder — each dispatches to a real callable."""
    for name, command in cmds.REGISTRY.items():
        assert callable(command.handler), f"{name} has no callable handler"
        assert command.help.strip(), f"{name} ships no help text"
