# CUI // SP-CTI
"""The ACE event dispatcher recommends by default, and auto-launches only under a live grant (icx-ace-01).

Owner, 2026-11-15: every ACE event becomes a recommendation in the launch
workflow (``decl-wft-ace-run-launch``); a person decides it at ``/decisions`` and
only then does a team launch, once, for exactly what was recommended. A role and
topic run on auto only under a live grant: declared eligible, trust trusted or
above, granted by a person, not revoked, expired, over today's limit or stopped.
Trust falling suspends the grant by itself.

The dispatcher, the event log, the grants and the declared-step ledger run for
real; only the team launch is recorded instead of run (tests/ace/conftest.py).
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest

from tests.ace._dispatch_support import DEV, PM, TOPIC, grant_auto

LAUNCH = ("decl-wft-ace-run-launch", "ace_api.api_launch")


def _mods():
    return (importlib.import_module("icdev.tools.ace.event_log"),
            importlib.import_module("icdev.tools.ace.auto_dispatch"),
            importlib.import_module("icdev.tools.workflow_hitl.declared"))


def _emit(env, content="the run finished") -> int:
    log, _, _ = _mods()
    return log.emit(TOPIC, {"content": content}, source_canvas="ace", source_id="inst-0",
                    roles=env.roles)


def _results(status=None):
    log, _, _ = _mods()
    rows = log.get_recent_results(limit=200)
    return [r for r in rows if status is None or r["status"] == status]


def _grant_events(grant_id):
    _, ad, _ = _mods()
    return [e["event"] for e in ad._events() if e["grant_id"] == grant_id]


def test_no_grant_means_a_pending_recommendation_and_no_launch(ace_env):
    _, _, declared = _mods()
    event_id = _emit(ace_env)
    counts = ace_env.dispatcher._process_pending()
    assert counts == {"recommended": 2}
    assert ace_env.launches == []
    rows = _results("recommended")
    assert sorted(r["role_id"] for r in rows) == ["evt_auto", "evt_plain"]
    for row in rows:
        assert row["event_id"] == event_id and row["instance_id"] == ""
        state = declared.step_state(row["step_id"])
        assert state["status"] == "pending"
    pending = {s["step_id"]: s for s in declared.list_steps(status="pending")}
    step = pending[rows[0]["step_id"]]
    assert (step["template_id"], step["surface"]) == LAUNCH
    assert step["proposed_by"] == "ace-dispatcher"
    # The event is processed: a second cycle recommends nothing more.
    assert ace_env.dispatcher._process_pending() == {}


def test_a_decided_recommendation_launches_exactly_once(ace_env):
    _, _, declared = _mods()
    _emit(ace_env, content="please reflect")
    ace_env.dispatcher._process_pending()
    row = next(r for r in _results("recommended") if r["role_id"] == "evt_plain")
    declared.decide(row["step_id"], decision="launch", actor=PM)

    counts = ace_env.dispatcher._process_pending()
    assert counts == {"launched": 1}
    assert len(ace_env.launches) == 1
    launch = ace_env.launches[0]
    assert launch["role_ids"] == ["evt_plain"]
    assert launch["trigger_source"] == f"ace_event:{TOPIC}"
    assert "please reflect" in launch["problem_text"]
    done = next(r for r in _results("launched"))
    assert (done["instance_id"], done["reason"]) == ("inst-1", "decided by u-pm")
    assert declared.step_state(row["step_id"])["status"] == "spent"

    ace_env.dispatcher._process_pending()
    assert len(ace_env.launches) == 1


def test_a_declined_recommendation_launches_nothing(ace_env):
    _, _, declared = _mods()
    _emit(ace_env)
    ace_env.dispatcher._process_pending()
    row = next(r for r in _results("recommended") if r["role_id"] == "evt_plain")
    with pytest.raises(declared.StepRefused):
        declared.decide(row["step_id"], decision="decline", actor=PM)  # a reason is required
    declared.decide(row["step_id"], decision="decline", reason="not needed now", actor=PM)
    assert ace_env.dispatcher._process_pending() == {"declined": 1}
    assert ace_env.launches == []
    assert next(r for r in _results("declined"))["reason"] == "declined by u-pm"


def test_a_withdrawn_recommendation_launches_nothing(ace_env):
    _, _, declared = _mods()
    _emit(ace_env)
    ace_env.dispatcher._process_pending()
    row = next(r for r in _results("recommended") if r["role_id"] == "evt_plain")
    declared.withdraw(row["step_id"], "superseded")
    assert ace_env.dispatcher._process_pending() == {"withdrawn": 1}
    assert ace_env.launches == []


def test_the_dispatcher_cannot_decide_its_own_recommendation(ace_env):
    _, _, declared = _mods()
    from icdev.tools.ace.event_dispatcher import DISPATCHER_ACTOR

    _emit(ace_env)
    ace_env.dispatcher._process_pending()
    row = _results("recommended")[0]
    with pytest.raises(declared.StepRefused) as refused:
        declared.decide(row["step_id"], decision="launch", actor=dict(DISPATCHER_ACTOR))
    assert refused.value.code == "not_a_person"
    ace_env.dispatcher._process_pending()
    assert ace_env.launches == []


def test_a_recommendation_for_what_changed_is_withdrawn_not_launched(ace_env):
    _, _, declared = _mods()
    from tests.ace._dispatch_support import make_role

    _emit(ace_env)
    ace_env.dispatcher._process_pending()
    row = next(r for r in _results("recommended") if r["role_id"] == "evt_plain")
    declared.decide(row["step_id"], decision="launch", actor=PM)
    # The role's steps changed after the person decided: the launch would differ.
    ace_env.roles[1] = make_role("evt_plain", listen=(TOPIC,))
    ace_env.roles[1].steps.pop()
    assert ace_env.dispatcher._process_pending() == {"withdrawn": 1}
    assert ace_env.launches == []
    assert "differs from what the person decided" in _results("withdrawn")[0]["reason"]


def test_an_undecided_recommendation_lapses(ace_env, monkeypatch):
    _, ad, declared = _mods()
    _emit(ace_env)
    ace_env.dispatcher._process_pending()
    real = ad.load_config
    monkeypatch.setattr(ad, "load_config", lambda path=None: {**real(path),
                                                              "recommendation_expiry_hours": 0})
    assert ace_env.dispatcher._process_pending() == {"withdrawn": 2}
    assert all(declared.step_state(r["step_id"])["status"] == "withdrawn"
               for r in _results("withdrawn"))
    assert ace_env.launches == []


def test_under_a_live_grant_the_role_launches_on_auto(ace_env):
    grant = grant_auto("evt_auto")
    _emit(ace_env)
    counts = ace_env.dispatcher._process_pending()
    assert counts == {"auto_launched": 1, "recommended": 1}
    assert [launch["role_ids"] for launch in ace_env.launches] == [["evt_auto"]]
    auto = _results("auto_launched")[0]
    assert (auto["role_id"], auto["grant_id"], auto["instance_id"]) == (
        "evt_auto", grant["grant_id"], "inst-1")
    assert auto["reason"] == f"auto, under grant {grant['grant_id']}"
    # The role with no grant is still only recommended.
    assert [r["role_id"] for r in _results("recommended")] == ["evt_plain"]


def test_a_revoked_grant_recommends_again(ace_env):
    _, ad, _ = _mods()
    grant = grant_auto("evt_auto")
    ad.revoke(grant["grant_id"], actor=DEV, reason="noisy")
    _emit(ace_env)
    assert ace_env.dispatcher._process_pending() == {"recommended": 2}
    assert ace_env.launches == []


def test_stop_all_recommends_again_until_resumed(ace_env):
    _, ad, declared = _mods()
    grant_auto("evt_auto")
    ad.stop_all(actor=DEV, reason="incident")
    _emit(ace_env, content="one")
    assert ace_env.dispatcher._process_pending() == {"recommended": 2}
    assert ace_env.launches == []

    step = declared.take_step(ad.TEMPLATE_ID, surface=ad.RESUME_SURFACE, decision="resume",
                              subject="stop-all", reason="incident closed", actor=PM)
    ad.record_resume(actor=PM, reason="incident closed", step_id=step["step_id"])
    ace_env.dispatcher._recent.clear()
    _emit(ace_env, content="two")
    assert ace_env.dispatcher._process_pending() == {"auto_launched": 1, "recommended": 1}


def test_an_expired_grant_recommends_and_records_its_expiry(ace_env):
    past = datetime.now(timezone.utc) - timedelta(days=10)
    grant = grant_auto("evt_auto", now=past, days=2)
    _emit(ace_env)
    assert ace_env.dispatcher._process_pending() == {"recommended": 2}
    assert ace_env.launches == []
    assert _grant_events(grant["grant_id"]) == ["granted", "expired"]


def test_over_max_per_day_recommends(ace_env):
    grant = grant_auto("evt_auto", max_per_day=1)
    _emit(ace_env, content="first")
    assert ace_env.dispatcher._process_pending()["auto_launched"] == 1
    ace_env.dispatcher._recent.clear()
    _emit(ace_env, content="second")
    assert ace_env.dispatcher._process_pending() == {"recommended": 2}
    assert len(ace_env.launches) == 1
    _, ad, _ = _mods()
    live = next(g for g in ad.grants() if g["grant_id"] == grant["grant_id"])
    assert (live["state"], live["launched_today"], live["at_limit"]) == ("live", 1, True)


def test_falling_trust_suspends_the_grant_for_good(ace_env):
    grant = grant_auto("evt_auto")
    ace_env.trust["evt_auto"] = 0.45  # supervised
    _emit(ace_env)
    assert ace_env.dispatcher._process_pending() == {"recommended": 2}
    assert ace_env.launches == []
    assert _grant_events(grant["grant_id"]) == ["granted", "suspended"]

    ace_env.trust["evt_auto"] = 0.9  # recovered: a person must grant again
    ace_env.dispatcher._recent.clear()
    _emit(ace_env, content="later")
    assert ace_env.dispatcher._process_pending() == {"recommended": 2}
    assert ace_env.launches == []


def test_the_fanout_cap_and_dedup_window_hold(ace_env, monkeypatch):
    _, ad, _ = _mods()
    real = ad.load_config
    monkeypatch.setattr(ad, "load_config", lambda path=None: {**real(path),
                                                              "max_roles_per_event": 1})
    _emit(ace_env, content="a")
    assert ace_env.dispatcher._process_pending() == {"recommended": 1}
    _emit(ace_env, content="b")
    # The role acted on is inside the dedup window; the other role is next.
    assert ace_env.dispatcher._process_pending() == {"recommended": 1}
    assert sorted(r["role_id"] for r in _results("recommended")) == ["evt_auto", "evt_plain"]
