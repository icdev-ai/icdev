# CUI // SP-CTI
"""The ACE governance twin observes event dispatch and auto-dispatch grants (icx-ace-01).

Findings, each with the event or grant as its thread, and the observation replays:

* ``ace.event.unprocessed``       an event the dispatcher has not processed in time;
* ``ace.recommendation.undecided`` a recommendation nobody decided in time;
* ``ace.auto.failed``             an auto-launch refused, or whose run failed;
* ``ace.grant.at_limit``          a live grant at today's max_per_day;
* ``ace.grant.expiring``          a live grant expiring within the warning window;
* ``ace.grant.suspended``         a grant its role's falling trust suspended.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

from tools.twin_core import governance as gov
from tools.twin_core.registry import TwinRegistry


def _at(**ago) -> str:
    return (datetime.now(timezone.utc) - timedelta(**ago)).strftime("%Y-%m-%d %H:%M:%S")


def _ahead(**ahead) -> str:
    return (datetime.now(timezone.utc) + timedelta(**ahead)).strftime("%Y-%m-%d %H:%M:%S")


def _exec(sql, params=()):
    """Run one statement on the ACE canvas DB; returns the first row it gives back."""
    from tools.db.storage import get_canvas_connection

    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    try:
        cur = conn.execute(sql, params)
        row = cur.fetchone() if "RETURNING" in sql else None
        conn.commit()
        return row
    finally:
        conn.close()


def _event(topic, at, processed=0) -> int:
    row = _exec("INSERT INTO ace_events (topic, source_canvas, source_id, payload_json, processed, "
                "created_at) VALUES (%s, 'ace', 's', '{}', %s, %s) RETURNING id",
                (topic, processed, at))
    return int(row[0])


def _result(event_id, role, status, at, *, instance="", grant=""):
    _exec("INSERT INTO ace_event_results (event_id, role_id, instance_id, status, step_id, "
          "grant_id, reason, created_at, updated_at) VALUES (%s,%s,%s,%s,'',%s,'',%s,%s)",
          (event_id, role, instance, status, grant, at, at))


_SEQ = itertools.count(1)


def _grant_event(grant, event, at, *, expires="", per_day=0):
    n = next(_SEQ)
    _exec("INSERT INTO ace_dispatch_grants (event_id, grant_id, event, role_id, topic, expires_at, "
          "max_per_day, reason, actor, actor_role, step_id, created_at) "
          "VALUES (%s,%s,%s,'evt_auto','task.completed',%s,%s,'r','u-pm','pm','',%s)",
          (f"adge-t{n}", grant, event, expires, per_day, at))


def test_the_twin_flags_dispatch_and_grants_with_their_threads(ace_env):
    TwinRegistry.discover(force=True)
    stale = _event("task.completed", _at(minutes=40))
    _event("task.completed", _at(minutes=1))  # fresh: not a finding
    old = _event("task.completed", _at(hours=30), processed=1)
    _result(old, "evt_plain", "recommended", _at(hours=30))
    refused = _event("task.completed", _at(hours=2), processed=1)
    _result(refused, "evt_auto", "auto_refused", _at(hours=2), grant="g-limit")
    failed = _event("task.completed", _at(hours=3), processed=1)
    _exec("INSERT INTO ace_instances (id, name, role_id, state, created_at, updated_at) "
          "VALUES ('inst-f', 'f', 'evt_auto', 'failed', %s, %s)", (_at(hours=3), _at(hours=2)))
    _result(failed, "evt_auto", "auto_launched", _at(hours=3), instance="inst-f", grant="g-old")
    today = _event("task.completed", _at(), processed=1)
    _result(today, "evt_auto", "auto_launched", _at(), instance="inst-ok", grant="g-limit")

    _grant_event("g-limit", "granted", _at(days=1), expires=_ahead(days=20), per_day=1)
    _grant_event("g-exp", "granted", _at(days=5), expires=_ahead(hours=20), per_day=5)
    _grant_event("g-sus", "granted", _at(days=5), expires=_ahead(days=20), per_day=5)
    _grant_event("g-sus", "suspended", _at(days=1))
    _grant_event("g-rev", "granted", _at(days=5), expires=_ahead(days=20), per_day=5)
    _grant_event("g-rev", "revoked", _at(days=1))

    env = gov.observe(gov.adapter_for("ace"), record=True)
    got = {(v["rule_id"], v["thread_id"]): v["severity"] for v in env["violations"]}
    assert got[("ace.event.unprocessed", f"ace_event:{stale}")] == "medium"
    assert [k for k in got if k[0] == "ace.event.unprocessed"] == [
        ("ace.event.unprocessed", f"ace_event:{stale}")]
    assert got[("ace.recommendation.undecided", f"ace_event:{old}")] == "medium"
    assert got[("ace.auto.failed", f"ace_event:{refused}")] == "high"
    assert got[("ace.auto.failed", f"ace_event:{failed}")] == "high"
    assert got[("ace.grant.at_limit", "ace_grant:g-limit")] == "medium"
    assert got[("ace.grant.expiring", "ace_grant:g-exp")] == "low"
    assert got[("ace.grant.suspended", "ace_grant:g-sus")] == "medium"
    assert not [k for k in got if k[1] == "ace_grant:g-rev"]
    summary = env["extra"]["summary"]
    assert (summary["events_unprocessed"], summary["recommendations_pending"],
            summary["auto_failed"], summary["grants_live"]) == (2, 1, 2, 2)

    for v in env["violations"]:
        if v["thread_id"].startswith(("ace_event:", "ace_grant:")):
            assert gov.resolve_thread(v["thread_id"])["resolved"], v["thread_id"]
    sus = gov.resolve_thread("ace_grant:g-sus")
    assert sus["record"]["event"] == "granted" and sus["events"] == ["granted", "suspended"]
    assert gov.adapter_for("ace").replay(env["extra"]["observation_id"])["match"]


def test_the_dispatch_checks_are_unverified_when_the_tables_are_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "empty.db"))
    monkeypatch.delenv("ICDEV_ACE_DB_URL", raising=False)
    TwinRegistry.discover(force=True)
    env = gov.observe(gov.adapter_for("ace"), record=False)
    checks = {c["id"]: c["status"] for c in env["extra"]["checks"]}
    for cid in ("ace.events", "ace.recommendations", "ace.grants"):
        assert checks[cid] == gov.CHECK_UNVERIFIED
    assert env["verdict"] != "pass"
