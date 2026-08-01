# CUI // SP-CTI
"""Reflex registry integrity (rri).

The daemon schedules reflexes by importing `tools.genesis.reflexes.<name>` and
calling its `run(args, ctx)`. When the module is missing — or exists but has no
`run` — the dispatcher silently marks the entry `missing`/`is_stub` and skips it
every cycle. A registered capability that never fires, invisible unless someone
reads the schedule. Same class as a TOOL_REGISTRY entry with no handler
(oss-fix-01).

This card found FIVE such entries: `oracle`, `goal_learner`, `remediation_lens`
(dead, removed), `daily_briefing_reflex` (engine existed, wired), and `wiki_lint`
(had run_reflex() but no run(), shimmed). The gate below is what keeps the class
from recurring.
"""
from __future__ import annotations

import importlib
import importlib.util

from tools.genesis.daemon import REFLEX_NAMES
from tools.workflow.coherence_checker import check_reflex_registry


# ── The live invariant ───────────────────────────────────────────────────────


def test_every_registered_reflex_dispatches():
    """The invariant the daemon relies on: every scheduled reflex is runnable.

    Not parametrised over a frozen list — it walks the LIVE REFLEX_NAMES, so a
    new entry added without a module fails on arrival rather than waiting for
    someone to notice it never ran.
    """
    broken = []
    for name in REFLEX_NAMES:
        spec = importlib.util.find_spec(f"tools.genesis.reflexes.{name}")
        if spec is None:
            broken.append((name, "no module"))
            continue
        mod = importlib.import_module(f"tools.genesis.reflexes.{name}")
        if not callable(getattr(mod, "run", None)):
            broken.append((name, "no callable run()"))
    assert not broken, f"undispatchable REFLEX_NAMES entries: {broken}"


def test_the_coherence_gate_passes_on_current_main():
    result = check_reflex_registry()
    assert result.status == "pass", result.message
    assert not result.missing


def test_gate_reports_the_full_count():
    result = check_reflex_registry()
    assert str(len(REFLEX_NAMES)) in result.actual[0]


# ── The gate catches the defect it exists for ────────────────────────────────


def test_gate_fails_on_a_registered_but_missing_reflex(monkeypatch):
    """A dead schedule entry must FAIL the gate, not pass silently."""
    from tools.genesis import daemon

    monkeypatch.setattr(
        daemon, "REFLEX_NAMES", list(REFLEX_NAMES) + ["a_reflex_that_does_not_exist"]
    )
    result = check_reflex_registry()
    assert result.status == "fail"
    assert "a_reflex_that_does_not_exist" in result.missing


def test_gate_fails_on_a_module_without_run(monkeypatch):
    """A module that imports but lacks run() is undispatchable too — the exact
    wiki_lint case this card found.

    `__init__` is a real module in the reflexes package that imports cleanly and
    has no run(), so it exercises the "module exists, no callable run()" branch
    without needing a fixture module on disk.
    """
    from tools.genesis import daemon

    monkeypatch.setattr(daemon, "REFLEX_NAMES", list(REFLEX_NAMES) + ["__init__"])
    result = check_reflex_registry()
    assert result.status == "fail"
    assert "__init__" in result.missing


# ── The four resolutions ─────────────────────────────────────────────────────


def test_dead_entries_were_removed():
    """oracle / goal_learner / remediation_lens had no module and are gone.

    (oracle_triage is the real triage reflex and runs via the harness path, not
    this loop — adding it here would double-dispatch it.)
    """
    for dead in ("oracle", "goal_learner", "remediation_lens"):
        assert dead not in REFLEX_NAMES, f"{dead} is a dead entry and should be removed"


def test_daily_briefing_reflex_was_wired():
    """Registered by second-brain #64, engine existed, wrapper was missing."""
    assert "daily_briefing_reflex" in REFLEX_NAMES
    mod = importlib.import_module("tools.genesis.reflexes.daily_briefing_reflex")
    assert callable(mod.run)
    assert getattr(mod, "IMPLEMENTATION_STATUS", "") == "full"


def test_daily_briefing_reflex_degrades_without_second_brain(monkeypatch):
    """Matches every reflex's degrade-not-crash posture."""
    import builtins

    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name == "tools.second_brain.briefing":
            raise ImportError("second_brain absent")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)
    from tools.genesis.reflexes import daily_briefing_reflex

    out = daily_briefing_reflex.run({}, None)
    assert out["skipped"] is True


def test_wiki_lint_now_has_the_dispatch_shim():
    """It had run_reflex() but no run() — the daemon calls run()."""
    mod = importlib.import_module("tools.genesis.reflexes.wiki_lint")
    assert callable(getattr(mod, "run", None))
    assert callable(getattr(mod, "run_reflex", None)), "the body must still exist"
