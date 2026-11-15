# CUI // SP-CTI
"""Test: acw-nova-02, as icx-ace-01 left it — an ACE completion is a recommendation for reflexion_loop.

When an ACE instance completes:
1. ``ACEController._emit_completion_event`` records ``task.completed`` in the ACE
   event log (``event_log.py``; it used to import ``emit`` from an ``event_bus``
   that no longer defined it, and swallowed the ImportError);
2. the reflexion_loop role listens to that topic;
3. the dispatcher turns the event into a RECOMMENDATION: a pending
   ``decl-wft-ace-run-launch`` step for reflexion_loop, and no run (owner,
   2026-11-15: nothing launches from an event by default);
4. once a person decides it, the dispatcher spends the step and launches
   reflexion_loop once.

The old version expected an ``agent_execution_traces`` row from a direct
``_launch_genesis_reflex`` that no longer exists; it lived in the CI backlog.
Real roles (``args/ace/roles``), real event log and real declared steps on the
SQLite test DB; only the team launch is recorded instead of run.
"""
from __future__ import annotations

import pytest

PM = {"id": "u-pm", "role": "pm"}


@pytest.fixture()
def reflexion_env(wf_db, tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_ACE_DB_URL", str(tmp_path / "ace.db"))
    from icdev.tools.ace.controller import ACEController
    from icdev.tools.ace.db.init_db import init as init_ace_db

    init_ace_db()
    launches: list[dict] = []

    class _Controller:
        def launch(self, **kwargs):
            launches.append(kwargs)
            return f"inst-reflex-{len(launches)}"

    monkeypatch.setattr(ACEController, "get_instance", classmethod(lambda cls: _Controller()))
    return launches


def test_emit_completion_event_writes_ace_event(reflexion_env):
    from icdev.tools.ace import event_log
    from icdev.tools.ace.controller import ACEController

    ACEController._emit_completion_event("inst-done-1")
    events = event_log.get_pending()
    assert [(e["topic"], e["source_canvas"], e["source_id"]) for e in events] == [
        ("task.completed", "ace", "inst-done-1")]
    assert events[0]["payload"] == {"trigger": "instance_complete", "instance_id": "inst-done-1"}


def test_reflexion_loop_role_loaded():
    """reflexion_loop is loadable, listens to task.completed, and is not auto-eligible."""
    from icdev.tools.ace.role_loader import RoleLoader

    role = RoleLoader().get_role("reflexion_loop")
    assert role is not None, "reflexion_loop role not found by RoleLoader"
    assert "task.completed" in (role.communication.get("listen_topics") or [])
    assert role.genesis_reflex == "reflexion_loop"
    # Auto-dispatch is a reviewed, explicit opt-in; reflexion_loop has none.
    assert role.auto_dispatch == {}


def test_completion_is_a_recommendation_not_a_run(reflexion_env):
    from icdev.tools.ace import event_log
    from icdev.tools.ace.controller import ACEController
    from icdev.tools.ace.event_dispatcher import ACEEventDispatcher
    from icdev.tools.workflow_hitl import declared

    ACEController._emit_completion_event("inst-done-2")
    ACEEventDispatcher()._process_pending()

    assert reflexion_env == []
    rows = [r for r in event_log.get_recent_results() if r["role_id"] == "reflexion_loop"]
    assert [r["status"] for r in rows] == ["recommended"]
    step = declared.step_state(rows[0]["step_id"])
    assert step["status"] == "pending"
    pending = {s["step_id"]: s for s in declared.list_steps(status="pending")}[rows[0]["step_id"]]
    assert (pending["template_id"], pending["surface"], pending["subject"]) == (
        "decl-wft-ace-run-launch", "ace_api.api_launch", "reflexion_loop")
    assert event_log.pending_count() == 0


def test_full_round_trip_emit_recommend_decide_launch(reflexion_env):
    from icdev.tools.ace import event_log
    from icdev.tools.ace.controller import ACEController
    from icdev.tools.ace.event_dispatcher import ACEEventDispatcher
    from icdev.tools.workflow_hitl import declared

    dispatcher = ACEEventDispatcher()
    ACEController._emit_completion_event("inst-done-3")
    dispatcher._process_pending()
    row = next(r for r in event_log.get_recent_results() if r["role_id"] == "reflexion_loop")

    declared.decide(row["step_id"], decision="launch", actor=PM)
    dispatcher._process_pending()

    reflex = [launch for launch in reflexion_env if launch["role_ids"] == ["reflexion_loop"]]
    assert len(reflex) == 1
    assert reflex[0]["trigger_source"] == "ace_event:task.completed"
    assert reflex[0]["trigger_ref"] == str(row["event_id"])
    done = next(r for r in event_log.get_recent_results() if r["id"] == row["id"])
    assert (done["status"], done["reason"]) == ("launched", "decided by u-pm")
    assert done["instance_id"].startswith("inst-reflex-")
