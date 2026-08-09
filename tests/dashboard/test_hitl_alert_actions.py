# CUI // SP-CTI
"""The HITL alert buttons must act, refuse, and never become a shell.

A firing `pr_watcher:hitl:<task>` alert means every automatic recovery is spent.
Seeing it is necessary but not sufficient — these endpoints are what turn the
alert into an action without dropping into a terminal.
"""
from __future__ import annotations

import importlib

import pytest

api = importlib.import_module("tools.dashboard.api.kanban")


@pytest.mark.parametrize("source,expected", [
    ("pr_watcher:hitl:sbx-gov-02", "sbx-gov-02"),
    ("pr_watcher:hitl:  spaced  ", "spaced"),
    ("self_monitor:gap::tool_not_in_manifest", ""),
    ("", ""),
    (None, ""),
])
def test_task_id_is_taken_only_from_a_hitl_source(source, expected):
    assert api._hitl_task_id(source) == expected


def test_the_action_verb_set_is_closed():
    """A button that can run anything is a remote shell with a nicer icon."""
    assert set(api.HITL_ACTIONS) == {"rebase", "requeue", "dismiss"}
    for verb, why in api.HITL_ACTIONS.items():
        assert why.strip(), f"{verb} must explain itself in the UI"


def test_every_action_is_reachable_from_the_ui():
    """The template renders one button per verb — drift here means a dead verb
    or, worse, a verb nobody can reach."""
    from pathlib import Path
    html = Path("tools/dashboard/templates/monitoring/overview.html").read_text(
        encoding="utf-8")
    for verb in api.HITL_ACTIONS:
        assert f'data-act="{verb}"' in html, f"no button for {verb}"


def test_buttons_are_scoped_to_hitl_alerts_only():
    """Other alert sources name no task, so the verbs cannot apply to them."""
    from pathlib import Path
    html = Path("tools/dashboard/templates/monitoring/overview.html").read_text(
        encoding="utf-8")
    assert "startswith('pr_watcher:hitl:')" in html
