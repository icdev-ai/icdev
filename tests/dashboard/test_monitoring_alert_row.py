# CUI // SP-CTI
"""The firing-alerts row must show what it knows and offer only what can work.

Measured 2026-08-10 while 12 HITL alerts were cleared by hand. The panel showed
WATCHCON / Severity / Title / Source / Project / Auto-Healed / Created and
offered Rebase / Requeue / Dismiss. It never rendered `description`, so the
cause, the resume budget and the PR link were invisible; and Rebase was offered
for the 10 alerts whose cause was `merge_conflict`, which `rebase_and_push`
refuses by design.

These tests render the real template rather than asserting on its source: a
column can be present in the file and still not reach the page.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

jinja2 = pytest.importorskip("jinja2")

view = importlib.import_module("tools.kanban.hitl_alert_view")

TEMPLATE = Path("tools/dashboard/templates/monitoring/overview.html")

CONFLICT = ("resume cap reached (5/5) after merge_conflict. "
            "PR: https://github.com/icdev-ai/icdev/pull/1479")
CI_FAILED = ("resume cap reached (5/5) after ci_failed. "
             "PR: https://github.com/icdev-ai/icdev/pull/1483")


def _alert(**over):
    row = {
        "id": 1, "severity": "warning", "title": "agov-det-06 needs a human",
        "source": "pr_watcher:hitl:agov-det-06", "description": CONFLICT,
        "created_at": "2026-08-10 09:18:01", "auto_healed": False,
        "project_id": None, "watchcon_tier": 4,
        "refire_count": 27, "first_seen": "2026-08-10 01:22:13",
        "task_status": "merge_conflict", "is_stale": False,
    }
    row.update(over)
    parsed = view.parse_alert(row)
    row["hitl"] = parsed
    row["rebase_refusal"] = view.rebase_refusal((parsed or {}).get("cause"))
    return row


def _render(alerts):
    """Render just the firing-alerts block, with the page's own markup."""
    env = jinja2.Environment(autoescape=True)
    src = TEMPLATE.read_text(encoding="utf-8")
    start = src.index('<div class="table-container" id="firing-alerts">')
    end = src.index('<!-- Recent Alerts Table', start)
    return env.from_string(src[start:end]).render(
        firing_alerts=alerts, firing_count=len(alerts))


# ── it says what it knows (alrt-cause-01) ───────────────────────────────────
def test_the_cause_reaches_the_page():
    html = _render([_alert()])
    assert "merge conflict" in html, "the cause was only ever in `description`"


def test_the_resume_budget_reaches_the_page():
    assert "5/5" in _render([_alert()])


def test_the_pr_is_a_LINK_not_bare_text():
    html = _render([_alert()])
    assert 'href="https://github.com/icdev-ai/icdev/pull/1479"' in html
    assert "#1479" in html


def test_an_unparseable_alert_still_renders():
    """The description is prose from another module. If it drifts the row must
    still appear — a blank panel hides every alert, not just the odd one."""
    html = _render([_alert(description="something new")])
    assert "needs a human" in html
    assert "unknown" in html


# ── it offers only what can work (alrt-verb-01) ─────────────────────────────
def test_rebase_is_DISABLED_for_a_real_conflict():
    html = _render([_alert()])
    i = html.index('data-act="rebase"')
    assert "disabled" in html[i:i + 120], (
        "rebase_and_push refuses a true conflict; offering the button teaches "
        "people the controls do not work")
    assert "cannot clear a real conflict" in html


def test_rebase_is_ENABLED_for_a_ci_failure():
    """The cheap recovery this button exists for — do not over-block it."""
    html = _render([_alert(description=CI_FAILED)])
    i = html.index('data-act="rebase"')
    assert "disabled" not in html[i:i + 120]


def test_dismiss_is_never_taken_away():
    for desc in (CONFLICT, CI_FAILED):
        html = _render([_alert(description=desc)])
        i = html.index('data-act="dismiss"')
        assert "disabled" not in html[i:i + 120]


# ── it shows churn and staleness (alrt-flap-01) ─────────────────────────────
def test_refire_churn_is_visible():
    """#1513 wrote 27 rows for one task in a day and the panel could not show
    it — the churn was only visible as a GROUP BY."""
    html = _render([_alert()])
    assert "re-fired 27" in html
    assert "2026-08-10 01:22:13" in html, "first-seen gives the count a period"


def test_a_single_firing_alert_is_not_dressed_up_as_churn():
    assert "re-fired" not in _render([_alert(refire_count=1)])


def test_a_stale_row_is_marked():
    """An alert whose task is done describes work that is over. The sweep in
    pr_watcher clears these; this is the window before the next poll."""
    html = _render([_alert(is_stale=True, task_status="done")])
    assert "stale" in html
    assert "alert-stale" in html


def test_a_live_row_is_not_marked_stale():
    assert "alert-stale" not in _render([_alert()])


# ── the API must reach the same verdict as the button ───────────────────────
def test_the_api_refuses_the_rebase_the_template_disables():
    """A disabled button is a courtesy; the API is what a script, a retry or a
    stale page actually hits."""
    allowed, why = view.action_is_available("rebase", "merge_conflict")
    assert allowed is False and why
    src = importlib.import_module("tools.dashboard.api.kanban")
    import inspect
    handler = inspect.getsource(src.hitl_alert_action)
    assert "action_is_available" in handler, (
        "the endpoint must apply the same cause rule as the template")
