# CUI // SP-CTI
"""Chat action cards must reach every surface, now and for future canvases.

In this repo a shared Jinja include does not spread by being useful — it spreads
only if something fails when it is missing:

    includes/iqe_query_widget.html   ~157 templates   (gated by coherence_checker)
    includes/classification_macros    56 templates    (convention only)
    includes/twin_snapshot_panel       1 — itself     (ungated)
    includes/_canvas_shell.html        1 — itself     (ungated)

So the renderer's reach is guarded by a coherence check rather than by hope.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def check():
    from tools.workflow.coherence_checker import check_chat_card_inheritance

    return check_chat_card_inheritance


# ---------------------------------------------------------------------------
# The invariant holds today
# ---------------------------------------------------------------------------


def test_gate_passes_on_the_current_tree(check):
    result = check()
    assert result.status == "pass", result.missing


def test_renderer_exists_in_both_mirrors():
    rel = "tools/dashboard/templates/includes/_chat_action_card.html"
    assert (REPO / rel).exists()
    assert (REPO / "icdev" / rel).exists()


def test_include_is_not_path_gated():
    """The Strategos panel reaches one route because of exactly this pattern."""
    text = (REPO / "tools/dashboard/templates/base.html").read_text(encoding="utf-8")
    lines = text.splitlines()
    idx = next(i for i, ln in enumerate(lines) if "includes/_chat_action_card.html" in ln)
    window = "\n".join(lines[max(0, idx - 3):idx])
    assert "request.path" not in window


# ---------------------------------------------------------------------------
# The gate actually fails when the invariant is broken.
# A gate that has never been seen to fail is not a gate.
# ---------------------------------------------------------------------------


@pytest.fixture
def base_html():
    path = REPO / "tools/dashboard/templates/base.html"
    original = path.read_text(encoding="utf-8")
    yield path, original
    path.write_text(original, encoding="utf-8")


def test_gate_fails_when_the_include_is_removed(check, base_html):
    path, original = base_html
    line = '    {% include "includes/_chat_action_card.html" ignore missing %}\n'
    path.write_text(original.replace(line, "", 1), encoding="utf-8")

    result = check()
    assert result.status == "fail"
    assert any("does not include" in m for m in result.missing)


def test_gate_fails_when_the_include_is_path_gated(check, base_html):
    path, original = base_html
    line = '    {% include "includes/_chat_action_card.html" ignore missing %}'
    gated = original.replace(
        line,
        "    {% if '/chat' in request.path %}\n" + line + "\n    {% endif %}",
        1,
    )
    path.write_text(gated, encoding="utf-8")

    result = check()
    assert result.status == "fail"
    assert any("request.path conditional" in m for m in result.missing)


def test_gate_is_registered_in_the_check_registry():
    """An unregistered check never runs, so it protects nothing."""
    from tools.workflow.coherence_checker import CHECK_REGISTRY

    assert "chat_card_inheritance" in CHECK_REGISTRY


# ---------------------------------------------------------------------------
# Future canvases and child apps
# ---------------------------------------------------------------------------


def test_scaffolded_canvases_inherit_via_base_html():
    """Neither scaffold needs its own include — both chain to base.html.

    minimal extends base.html directly; standard extends _canvas_shell.html,
    which itself extends base.html. Adding a per-page include would be
    cargo-culting the IQE widget pattern, which needs one only because it has a
    per-canvas adapter to bind.
    """
    minimal = (REPO / "data/templates/canvases/minimal/page.html.j2").read_text(encoding="utf-8")
    standard = (REPO / "data/templates/canvases/standard/page.html.j2").read_text(encoding="utf-8")
    shell = (REPO / "tools/dashboard/templates/includes/_canvas_shell.html").read_text(encoding="utf-8")

    assert 'extends "base.html"' in minimal
    assert 'extends "includes/_canvas_shell.html"' in standard
    assert 'extends "base.html"' in shell


def test_child_apps_can_opt_into_coworkers():
    """ACE is opt-in, not bundled into every generated app.

    It is a large subsystem (engine, ~90 role YAMLs, its own canvas DB); an app
    that never spawns co-workers should not carry it.
    """
    from tools.builder.child_app_generator import CONDITIONAL_DIRS, DIRECTORY_TREE

    assert "coworker" in CONDITIONAL_DIRS
    assert "tools/ace" in CONDITIONAL_DIRS["coworker"]
    assert "args/ace/roles" in CONDITIONAL_DIRS["coworker"]
    assert not any("tools/ace" in d for d in DIRECTORY_TREE), "ACE should not be always-on"


def test_dashboard_capability_carries_the_renderer():
    """A child app with a dashboard gets the card renderer for free."""
    from tools.builder.child_app_generator import CONDITIONAL_DIRS

    assert "tools/dashboard/templates" in CONDITIONAL_DIRS["dashboard"]
