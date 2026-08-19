# CUI // SP-CTI
"""hcx-live-gate-01: removing a point must not be able to break a deployment.

THE GATE'S PREMISE, CHECKED. `args/extension_liveness.yaml` held four points
back from deletion with this reasoning, and the kanban card repeated it:

    "removing the member turns that file into an AttributeError at import — a
     hard startup failure for that deployment, not a warning."

That is not what the loaders do, and it was worth measuring before acting on it
rather than after. Both discovery paths resolve a point by VALUE inside
`try/except ValueError`, and the whole per-file load sits inside
`except Exception` with a log line. A site-local drop-in naming a removed point
therefore fails to LOAD — the extension stops working and says so — while the
platform starts normally.

The risk was real but smaller than recorded: a silently disabled extension, not
a dead deployment. That is a release-note matter, not a reason to keep four
public names with no behaviour behind them.

This file is the standing proof. It is not about the four points that were
removed; it is about the property that made removing them safe, which must hold
for the next removal too.
"""
from __future__ import annotations

import pytest

from tools.extensions.extension_manager import ExtensionManager, ExtensionPoint

#: Removed 2026-08-18. Named here so the test states what a site-local drop-in
#: might still be carrying, rather than inventing a name that never existed.
REMOVED = ("memory_save_before", "memory_save_after",
           "compliance_check_before", "compliance_check_after")

_HOOK_DROPIN = "\n".join((
    "def handle(context):",
    "    return context",
    "EXTENSION_HOOKS = {'memory_save_before': {'handler': handle,",
    "    'name': 'site_local', 'enabled': True}}",
    "",
))

_ATTR_DROPIN = "\n".join((
    "from tools.extensions.extension_manager import ExtensionPoint",
    "POINT = ExtensionPoint.MEMORY_SAVE_BEFORE",   # AttributeError at import
    "",
))


def test_the_four_points_are_gone():
    values = {p.value for p in ExtensionPoint}
    assert values.isdisjoint(REMOVED), f"still declared: {values & set(REMOVED)}"
    for name in ("MEMORY_SAVE_BEFORE", "COMPLIANCE_CHECK_BEFORE"):
        assert not hasattr(ExtensionPoint, name)


def test_the_survivors_are_untouched():
    """The removal must take exactly what was decided and nothing adjacent."""
    assert {p.value for p in ExtensionPoint} == {
        "tool_execute_before", "tool_execute_after",
        "chat_message_before", "chat_message_after",
        "agent_start", "agent_end",
    }


@pytest.mark.parametrize("removed", REMOVED)
def test_resolving_a_removed_point_by_value_raises_ValueError(removed):
    """The distinction the gate turned on. ValueError is what both loaders
    already catch; AttributeError is what the gate feared, and it is a different
    exception raised on a different line."""
    with pytest.raises(ValueError):
        ExtensionPoint(removed)


def test_a_dropin_declaring_a_removed_hook_is_SKIPPED_not_fatal(tmp_path, caplog):
    """The deployment-level claim, through the REAL public loader.

    A tenant drop-in naming a removed point is invisible to any grep of this
    checkout — `scan_directories` includes a project-root `extensions/` that is
    not in this repository — so this is the case nobody could enumerate, and the
    one the gate existed to protect. It must degrade to a log line.
    """
    hook_dir = tmp_path / "memory_save_before"
    hook_dir.mkdir()
    (hook_dir / "010_site_local.py").write_text(_HOOK_DROPIN, encoding="utf-8")

    mgr = ExtensionManager()
    with caplog.at_level("WARNING"):
        loaded = mgr.load_extensions_from_directory(tmp_path)

    assert loaded == 0, "a drop-in for a removed point must register nothing"
    assert not any(
        h.hook_point.value in REMOVED
        for hs in getattr(mgr, "_handlers", {}).values() for h in hs), \
        "a removed point must never end up with a registered handler"


def test_a_module_touching_the_removed_ATTRIBUTE_is_contained(tmp_path, caplog):
    """The exact shape the gate named: `ExtensionPoint.MEMORY_SAVE_BEFORE` in a
    drop-in's module body, which raises AttributeError at import.

    CONTAINMENT IS THE PUBLIC LOADER'S PROPERTY, NOT `_load_file`'s — an earlier
    draft asserted it of `_load_file` and rightly failed. That method re-raises;
    every CALLER wraps the whole per-file load in `except Exception` and logs. So
    the guarantee is real but belongs one level up, and asserting it at the wrong
    level would have proved nothing about the path a deployment actually takes.
    """
    hook_dir = tmp_path / "agent_start"       # a LIVE point, so the directory
    hook_dir.mkdir()                          # name itself still resolves
    (hook_dir / "010_bad.py").write_text(_ATTR_DROPIN, encoding="utf-8")

    mgr = ExtensionManager()
    with caplog.at_level("ERROR"):
        loaded = mgr.load_extensions_from_directory(tmp_path)

    assert loaded == 0
    assert len(list(ExtensionPoint)) == 6, (
        "a skipped drop-in must not poison the process it was skipped in")


def test_the_loaders_still_catch_by_value_resolution():
    """Structural backstop. The containment above is a property of two
    `try/except ValueError` sites plus the per-file `except Exception`; if any is
    ever unwrapped, a removed point becomes fatal again and these tests would be
    the only warning.

    The module comes from `sys.modules`, NOT from `import ... as`:
    `tools/extensions/__init__.py` exports a singleton named `extension_manager`
    which SHADOWS the submodule of the same name, so the obvious import hands
    back an ExtensionManager instance and `inspect.getsource` raises TypeError.
    """
    import inspect
    import sys

    import tools.extensions.extension_manager  # noqa: F401 — populate sys.modules

    em = sys.modules["tools.extensions.extension_manager"]
    src = inspect.getsource(em)
    assert src.count("except ValueError") >= 2, (
        "both by-value point lookups must stay inside except ValueError")
    assert "Unknown hook point" in src, (
        "an unknown hook name must be NAMED in the log, not silently dropped")
