# CUI // SP-CTI
"""A UI change must map to an E2E spec that actually covers it.

The kanban agent has a wall-clock budget (1800s default, 3600s on the pytest
tier) and the full Playwright suite is ~15 minutes — 65 specs at ~14s each — so
it never runs the whole suite. It runs the spec(s) mapped to the changed files.

That is the right design; the hole was in the mapping. Two cases resolved to no
spec at all, and the caller then fell through to a Selenium fallback that runs
one kanban-depends-on test touching none of the changed UI. The task reported
"E2E verification" having exercised nothing relevant:

  * too-broad changes (app.py, base.html) — deliberately bailed out
  * unmatched slugs — templates/index.html -> "index", no index*.spec.ts exists

Measured against the last 30 merges to main, the second case was half the UI
commits sampled.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.workflow.validated_commit import (  # noqa: E402
    _BROAD_UI_SMOKE_SPECS,
    _playwright_specs_for_changed_files as specs_for,
)

SPECS_DIR = REPO_ROOT / "tests" / "e2e"


def _names(paths):
    return [Path(p).name for p in paths]


# ---------------------------------------------------------------------------
# The budget constraint that shapes the whole design

def test_never_returns_more_than_two_specs():
    """The full suite is ~15 min; the caller's Playwright budget is 120s."""
    many = [f"tools/dashboard/templates/{s}/page.html" for s in
            ("chat", "genesis", "research", "kanban", "monitoring")]
    assert len(specs_for(many)) <= 2


def test_backend_only_change_maps_to_nothing():
    """No UI touched — E2E should not run at all, not even smoke."""
    assert specs_for(["tools/db/storage.py", "tests/test_x.py", "README.md"]) == []


def test_no_files_maps_to_nothing():
    assert specs_for([]) == []
    assert specs_for(None) == []


# ---------------------------------------------------------------------------
# Direct slug mapping still works

def test_canvas_template_maps_to_its_own_spec():
    got = _names(specs_for(["tools/dashboard/templates/chat/page.html"]))
    assert got, "a canvas template must map to its own spec"
    assert any("chat" in n for n in got), got


def test_a_specific_spec_beats_the_broad_smoke():
    """Precision first: smoke is the fallback, never the preference."""
    got = _names(specs_for(["tools/dashboard/templates/kanban/page.html"]))
    assert got
    assert not set(got) & set(_BROAD_UI_SMOKE_SPECS), (
        f"a slug with its own spec must not fall back to smoke: {got}"
    )


# ---------------------------------------------------------------------------
# The hole: UI changed, nothing matched

@pytest.mark.parametrize("changed", [
    ["tools/dashboard/templates/index.html"],          # slug "index" — no such spec
    ["tools/dashboard/app.py"],                        # deliberately too broad
    ["tools/dashboard/templates/base.html"],           # affects every page
    ["tools/dashboard/static/js/unmatched_widget.js"],  # no spec by that name
])
def test_unmapped_ui_change_falls_back_to_smoke_not_selenium(changed):
    got = _names(specs_for(changed))
    assert got, (
        f"{changed} mapped to NO spec — the caller then runs one unrelated "
        f"Selenium test and reports it as E2E verification"
    )
    assert set(got) <= set(_BROAD_UI_SMOKE_SPECS), got


def test_the_broad_smoke_specs_exist():
    """A fallback naming a spec that does not exist is not a fallback."""
    missing = [n for n in _BROAD_UI_SMOKE_SPECS if not (SPECS_DIR / n).is_file()]
    assert not missing, f"fallback specs missing from tests/e2e: {missing}"


def test_broad_smoke_specs_are_cheap():
    """They run inside the caller's 120s Playwright budget.

    Chosen for being the two cheapest whole-app specs; if either grows into a
    long suite the fallback stops fitting the budget it exists to respect.
    """
    for name in _BROAD_UI_SMOKE_SPECS:
        p = SPECS_DIR / name
        if not p.is_file():
            continue
        n_tests = p.read_text(encoding="utf-8", errors="replace").count("test(")
        assert n_tests <= 15, f"{name} has {n_tests} tests — too heavy for a 120s fallback"


def test_broad_change_plus_specific_change_still_runs_the_specific_spec():
    """A diff touching app.py AND a canvas must not lose the canvas spec.

    The bail-out used to `return []` on the first broad file, discarding
    everything else in the diff.
    """
    got = _names(specs_for([
        "tools/dashboard/app.py",
        "tools/dashboard/templates/chat/page.html",
    ]))
    assert any("chat" in n for n in got), got
