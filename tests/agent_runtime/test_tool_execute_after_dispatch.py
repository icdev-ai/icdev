# CUI // SP-CTI
"""TOOL_EXECUTE_AFTER had a handler and no dispatcher (autonomy-wire-01).

`tools/awareness/hooks.py` has subscribed a handler to `TOOL_EXECUTE_AFTER`
since Phase 1e — the auto-reindex that refreshes `kg_nodes` after every
Edit/Write — and NOTHING IN THE TREE DISPATCHED IT. Measured 2026-08-21: of the
six enabled hook points, `TOOL_EXECUTE_BEFORE`, `AGENT_START` and `AGENT_END`
had dispatchers; `TOOL_EXECUTE_AFTER` had only a subscriber; and
`CHAT_MESSAGE_BEFORE`/`AFTER` had no call site at all. `capability_consumption`
reported the class 6 declared / 0 consumed, and that zero was the only trace
that a shipped feature had never once run.

THE TWO PROPERTIES THAT MAKE THIS SAFE, and both fail GREEN if broken:

  1. It is OBSERVE-ONLY. `args/extension_config.yaml` sets
     `allow_modification: false`, so whatever a handler returns is DISCARDED. A
     post-hook that could rewrite the output would be a second, unaudited place
     to edit tool results, reachable by dropping in a file.
  2. It fires only when the tool ACTUALLY RAN. A call blocked by an extension or
     by the safety gate never executed, so telling a handler an Edit happened
     would be a lie it would act on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib  # noqa: E402

# NOT `from tools.agent_runtime import dispatch`: the package __init__ exports a
# FUNCTION called `dispatch`, which shadows the submodule of the same name, and
# the import silently binds the function. Every monkeypatch below would then
# target an attribute that does not exist.
d = importlib.import_module("tools.agent_runtime.dispatch")  # noqa: E402
from tools.agent_runtime.discovery import ToolSpec  # noqa: E402


class _Points:
    TOOL_EXECUTE_BEFORE = "tool_execute_before"
    TOOL_EXECUTE_AFTER = "tool_execute_after"


class _Manager:
    def __init__(self, result=None, raises=False):
        self.calls = []
        self._result = result
        self._raises = raises

    def dispatch(self, point, context):
        self.calls.append((point, context))
        if self._raises:
            raise RuntimeError("a handler exploded")
        return self._result


def _spec(name="write_file", read_only=False):
    # The REAL ToolSpec, never a stand-in: the dispatcher reads `name`,
    # `read_only` and `source` off it, and a hand-rolled double can satisfy this
    # test while diverging from what dispatch actually receives.
    return ToolSpec(
        name=name, schema={}, module="m", handler="h",
        source="builtin", read_only=read_only,
    )


@pytest.fixture()
def manager(monkeypatch):
    mgr = _Manager()
    monkeypatch.setattr(d, "_extension_point", lambda: (mgr, _Points))
    return mgr


# --------------------------------------------------------------------------- #
# 1. It is dispatched at all — the defect
# --------------------------------------------------------------------------- #
def test_the_after_point_is_dispatched(manager):
    d._dispatch_after(_spec(), {"path": "x.py"}, "wrote 3 lines", "task-1")
    points = [c[0] for c in manager.calls]
    assert _Points.TOOL_EXECUTE_AFTER in points, (
        "nothing dispatched TOOL_EXECUTE_AFTER — the subscribed auto-reindex "
        "handler would never fire"
    )


def test_the_context_carries_what_a_handler_needs(manager):
    d._dispatch_after(_spec(name="edit_file"), {"path": "a.py"}, "ok", "task-9")
    _point, ctx = manager.calls[0]
    assert ctx["tool_name"] == "edit_file"
    assert ctx["tool_input"] == {"path": "a.py"}
    assert ctx["output"] == "ok"
    assert ctx["task_id"] == "task-9"


def test_the_tool_input_is_copied_not_shared(manager):
    """A handler that mutates the context must not reach back into the call."""
    original = {"path": "a.py"}
    d._dispatch_after(_spec(), original, "ok", None)
    _point, ctx = manager.calls[0]
    ctx["tool_input"]["path"] = "hacked"
    assert original["path"] == "a.py"


# --------------------------------------------------------------------------- #
# 2. Observe-only
# --------------------------------------------------------------------------- #
def test_it_returns_nothing_so_a_handler_cannot_rewrite_the_output(monkeypatch):
    """`allow_modification: false`. A post-hook able to change the result would
    be a second, unaudited place to edit tool output."""
    mgr = _Manager(result={"output": "REWRITTEN", "deny": True,
                           "deny_reason": "nope"})
    monkeypatch.setattr(d, "_extension_point", lambda: (mgr, _Points))

    assert d._dispatch_after(_spec(), {}, "the real output", None) is None


def test_a_raising_handler_never_fails_the_call(monkeypatch):
    """The tool already succeeded. An observer must not turn that into an
    error — extensions are a layer, not a dependency."""
    mgr = _Manager(raises=True)
    monkeypatch.setattr(d, "_extension_point", lambda: (mgr, _Points))

    d._dispatch_after(_spec(), {}, "ok", None)   # must not raise


def test_no_extension_manager_is_a_no_op(monkeypatch):
    monkeypatch.setattr(d, "_extension_point", lambda: None)
    d._dispatch_after(_spec(), {}, "ok", None)   # must not raise


# --------------------------------------------------------------------------- #
# 3. The loader serves BOTH points
# --------------------------------------------------------------------------- #
def test_the_loader_caches_the_enum_not_one_member():
    """Caching a single member is exactly how AFTER ended up with no
    dispatcher: the loader could only ever hand back BEFORE."""
    import inspect

    src = inspect.getsource(d._extension_point)
    assert "ExtensionPoint.TOOL_EXECUTE_BEFORE" not in src, (
        "the loader is pinned to one point again"
    )
    assert "(extension_manager, ExtensionPoint)" in src


def test_before_still_dispatches_its_own_point(manager):
    """Widening the loader must not have changed which point BEFORE uses."""
    d._dispatch_before(_spec(), {"a": 1}, "t")
    assert manager.calls[0][0] == _Points.TOOL_EXECUTE_BEFORE


# --------------------------------------------------------------------------- #
# 4. It fires only for a tool that RAN
# --------------------------------------------------------------------------- #
def test_the_run_path_dispatches_after_only_past_the_gate():
    """Structural, and narrow: it pins that the call sits after the gate's
    early returns. A blocked call has no "after", and firing there would tell a
    handler an Edit happened when nothing was written."""
    import inspect

    src = inspect.getsource(d.build_handlers) if hasattr(d, "build_handlers") else ""
    if "_dispatch_after" not in src:
        src = inspect.getsource(d)
    after = src.index("_dispatch_after(spec, tool_input, out, task_id)")
    gate = src.index("allowed, reason = gate(")
    assert after > gate, "TOOL_EXECUTE_AFTER is dispatched before the safety gate"


def test_the_disabled_chat_points_have_no_call_site():
    """They were disabled BECAUSE nothing reaches them. If a dispatch is added
    later, re-enable them in the SAME change — a point is declared when
    something calls it."""
    import subprocess  # nosec B404 — grep over the repo, fixed argv

    hits = subprocess.run(  # nosec B603 B607
        ["git", "grep", "-l", "CHAT_MESSAGE_BEFORE", "--", "tools/"],
        cwd=str(ROOT), capture_output=True, text=True, check=False,
    ).stdout.strip().splitlines()
    real = [h for h in hits if "extension_manager.py" not in h and "test" not in h]
    assert not real, f"a call site appeared; re-enable the point in config: {real}"
