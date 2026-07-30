# CUI // SP-CTI
"""Configure steps must not present invented numbers as their own run's output.

configurator.py had five of seven handlers return hardcoded constants that ignored
the learner's input entirely, and every one returned a bare status='ok':

  _handle_ai_inventory   -> systems_found 7, omb_compliant 5, two invented gaps
  _handle_govcon_scan    -> opportunities 12, "Advanced AI Integration Services -
                            DoD - $4.2M - closes in 18 days"
  _handle_ato_timeline   -> automation_coverage "68%", risk_score "Medium"
  _handle_stig_triage    -> a fixed remediation recommendation
  _handle_poam_draft     -> poam_items = len(findings) or 3

The learner was shown fiction labelled as the result of the action they just ran,
across 42 configure steps. This is the same defect class fga-fix-02 removed from
watch steps — see the comment at _step_watch.html:8-12, "a fabricated example
presented to a learner as this step's actual output" — and the honest pattern was
already in this very file: _handle_rag_search tries the real retriever and, when
unavailable, tags its fallback note='Live RAG unavailable - demo mode'.

It survived because tests/test_fga_academy_defects.py::
test_no_fabricated_demo_output_is_rendered checks TEMPLATES only. These check the
handlers.
"""
from __future__ import annotations

import inspect

import pytest

from apps.forge_academy import configurator

# Every action the dispatcher accepts.
ACTIONS = [
    "deploy_pattern", "stig_triage", "rag_search", "poam_draft",
    "ato_timeline", "ai_inventory", "govcon_scan",
]


def test_dispatcher_still_covers_every_action():
    for action in ACTIONS:
        res = configurator.dispatch_configure({"action": action, "config": {}})
        assert res.get("status") != "error" or "Unknown action" not in res.get("message", ""), \
            f"{action} disappeared from the dispatcher"


@pytest.mark.parametrize("action", ACTIONS)
def test_no_handler_returns_unmarked_synthetic_data(action):
    """A response the learner sees must either be real or say that it is not."""
    res = configurator.dispatch_configure({"action": action, "config": {}})
    if res.get("status") == "error":
        return  # an honest failure is fine
    if res.get("simulated"):
        assert res.get("note"), f"{action} is simulated but carries no explanation"
        return
    # Not marked simulated -> it must have come from a real subsystem.
    assert res.get("source") not in (None, "", "hardcoded"), (
        f"{action} returned data with no provenance and no simulated marker"
    )


@pytest.mark.parametrize(
    "action,invented",
    [
        ("ai_inventory", ("systems_found", "omb_compliant")),
        ("govcon_scan", ("opportunities", "top_match")),
        ("ato_timeline", ("automation_coverage", "risk_score")),
        ("stig_triage", ("recommendation",)),
        ("poam_draft", ("poam_items",)),
    ],
)
def test_the_specific_invented_fields_are_marked(action, invented):
    """Each field enumerated in the audit must now travel with the marker."""
    res = configurator.dispatch_configure({"action": action, "config": {}})
    present = [k for k in invented if k in res or k in (res.get("result") or {})]
    if not present:
        return  # the handler dropped the invented field entirely — also acceptable
    assert res.get("simulated") is True, (
        f"{action} still reports {present} without declaring itself a simulation"
    )


def test_a_simulated_response_names_the_real_tool_it_stands_in_for():
    """So a reader knows what wiring it up would mean, not just that it is fake."""
    for action in ("ai_inventory", "govcon_scan", "ato_timeline", "stig_triage",
                   "poam_draft"):
        res = configurator.dispatch_configure({"action": action, "config": {}})
        if res.get("simulated"):
            assert res.get("real_tool"), f"{action} does not name its real counterpart"


def test_no_handler_hardcodes_a_dollar_amount_or_percentage_in_prose():
    """The govcon '$4.2M closes in 18 days' string was the worst offender."""
    src = inspect.getsource(configurator)
    assert "$4.2M" not in src, "invented dollar figure still present"
    assert "closes in 18 days" not in src, "invented deadline still present"


def test_rag_search_keeps_its_honest_fallback():
    """The pattern the other handlers now follow — do not regress it."""
    res = configurator.dispatch_configure({"action": "rag_search", "config": {"query": "x"}})
    assert res.get("status") == "ok"
    if res.get("simulated"):
        assert res.get("note")


def test_the_configure_template_surfaces_the_simulation_marker():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    html = (root / "tools" / "dashboard" / "templates" / "forge_academy" / "partials"
            / "_step_configure.html").read_text(encoding="utf-8")
    assert "simulated" in html, (
        "the marker exists in the payload but the learner never sees it"
    )


def test_unknown_action_still_reports_honestly():
    res = configurator.dispatch_configure({"action": "no_such_action"})
    assert res["status"] == "error"
    assert "available" in res
