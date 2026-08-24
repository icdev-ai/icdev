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
    """Each field enumerated in the audit must travel with the marker — OR be real.

    Born red (task-det-2f16251ac7, born_red_survey finding 2f16251ac7401913): the
    original assertion demanded ``simulated is True`` whenever the field NAME was
    present. But ``_handle_ai_inventory`` reads the real ``ai_use_case_inventory``
    table and returns ``systems_found`` with ``simulated=False`` and its source
    named — the honest REAL branch, which is the whole point of the fix. So the
    case passed only where that table was unreachable (an empty worktree or CI
    database, where the handler falls back to the simulation) and failed on any
    host whose board holds inventory rows — the live board's runner, where the
    born_red reflex observes. It measured the observer's database, not the
    handler. The field being present is a defect only when it arrives with NO
    provenance: neither a simulation marker nor a named real source.
    """
    res = configurator.dispatch_configure({"action": action, "config": {}})
    present = [k for k in invented if k in res or k in (res.get("result") or {})]
    if not present:
        return  # the handler dropped the invented field entirely — also acceptable
    assert _is_honestly_sourced(res), (
        f"{action} still reports {present} with neither a simulation marker "
        f"nor a real source: simulated={res.get('simulated')!r} "
        f"source={res.get('source')!r}"
    )


def _is_honestly_sourced(res: dict) -> bool:
    """The contract every handler must meet: REAL with provenance, or MARKED fake."""
    if res.get("simulated") is True:
        return bool(res.get("note"))
    return res.get("simulated") is False and res.get("source") not in (
        None, "", "hardcoded", "teaching-simulation",
    )


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """Answers the one query _handle_ai_inventory makes, with a fixed count."""

    def __init__(self, total):
        self.total = total
        self.queries = []

    def execute(self, sql, *args):
        self.queries.append(sql)
        return _FakeCursor((self.total,))


def test_ai_inventory_reports_the_live_count_unsimulated(monkeypatch):
    """Both branches of ai_inventory are pinned HERE, deterministically, so the
    parametrized contract test above never again depends on whichever database
    the observer happens to be running against."""
    import tools.db.storage as storage

    conn = _FakeConn(8)
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    res = configurator.dispatch_configure({"action": "ai_inventory", "config": {}})
    assert res["status"] == "ok"
    assert res["simulated"] is False
    assert res["source"] == "ai_use_case_inventory"
    assert res["result"]["systems_found"] == 8
    assert any("ai_use_case_inventory" in q for q in conn.queries)
    assert _is_honestly_sourced(res)


def test_ai_inventory_falls_back_to_a_marked_simulation_when_unreachable(monkeypatch):
    import tools.db.storage as storage

    def _unreachable(*a, **k):
        raise RuntimeError("no such table: ai_use_case_inventory")

    monkeypatch.setattr(storage, "get_connection", _unreachable)
    res = configurator.dispatch_configure({"action": "ai_inventory", "config": {}})
    assert res["status"] == "ok"
    assert res["simulated"] is True
    assert res["note"] and res["real_tool"]
    # No invented total: the fallback must not fabricate a count for the estate.
    assert res["result"]["systems_found"] is None
    assert _is_honestly_sourced(res)


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
