# CUI // SP-CTI
"""The ACE event log, its feed routes and its read-only MCP tools (icx-ace-01).

``GET /api/ace/events`` answered 500 on every call: commit 36d611a31 turned
``event_bus.py`` into the SSE queue and dropped the log's six functions while the
route swallowed the ImportError. They live in ``event_log.py`` now. This pins:

* emit -> pending -> result -> recent -> processed, on a real canvas DB;
* topics are derived from the roles, and a topic no role declares is refused;
* the feed routes answer 200 with rows, and 503 (never a 500 with a traceback
  string) when the log cannot be read; ``/events/topics`` answers 200;
* the MCP tools ``ace_events`` and ``ace_auto_dispatch_grants`` are declared
  read-only and read the same rows.
"""
from __future__ import annotations

import importlib

import pytest

from tests.ace._dispatch_support import PM, TOPIC, isolated_client, make_role


def _log():
    return importlib.import_module("icdev.tools.ace.event_log")


def test_emit_pending_result_recent_processed(ace_env):
    log = _log()
    event_id = log.emit(TOPIC, {"content": "run finished"}, source_canvas="ace",
                        source_id="inst-9", roles=ace_env.roles)
    assert isinstance(event_id, int)
    pending = log.get_pending()
    assert [(e["id"], e["topic"], e["source_id"]) for e in pending] == [(event_id, TOPIC, "inst-9")]
    assert pending[0]["payload"] == {"content": "run finished"}
    assert log.pending_count() == 1

    result_id = log.store_result(event_id, "evt_plain", status="recommended", step_id="step-x")
    log.mark_processed(event_id)
    assert log.pending_count() == 0 and log.get_pending() == []
    recent = log.get_recent_results()
    assert recent[0]["id"] == result_id
    assert (recent[0]["topic"], recent[0]["source_canvas"], recent[0]["status"],
            recent[0]["step_id"], recent[0]["payload_preview"]) == (
        TOPIC, "ace", "recommended", "step-x", "run finished")

    log.update_result(result_id, "launched", instance_id="inst-1", reason="decided by u-pm")
    assert log.results_with_status("recommended") == []
    row = log.results_with_status("launched")[0]
    assert (row["instance_id"], row["reason"]) == ("inst-1", "decided by u-pm")


def test_a_result_status_must_be_declared(ace_env):
    log = _log()
    event_id = log.emit(TOPIC, {}, roles=ace_env.roles)
    with pytest.raises(ValueError):
        log.store_result(event_id, "evt_plain", status="dispatched")


def test_topics_are_derived_from_the_roles():
    log = _log()
    roles = [make_role("r_a", listen=("doc.ready", "task.completed")),
             make_role("r_b", listen=("task.completed",))]
    topics = log.dispatch_topics(roles)
    assert topics["task.completed"] == ["r_a", "r_b"]
    assert topics["doc.ready"] == ["r_a"]
    # An emitted-only topic is declared, with no listener.
    assert topics["status.updated"] == []


def test_the_shipped_topics_include_task_completed_and_wildcards_as_declared():
    topics = _log().dispatch_topics()
    assert "reflexion_loop" in topics["task.completed"]
    assert any(t.endswith(".*") for t in topics)


def test_an_undeclared_topic_is_refused(ace_env):
    log = _log()
    assert log.emit("no.role.declares.this", {"content": "x"}, roles=ace_env.roles) is None
    assert log.pending_count() == 0


def test_the_events_route_answers_200_with_rows(ace_env):
    log = _log()
    bp = importlib.import_module("icdev.tools.ace.blueprint")
    event_id = log.emit(TOPIC, {"content": "c"}, source_canvas="ace", roles=ace_env.roles)
    log.store_result(event_id, "evt_plain", status="recommended", step_id="step-1")
    resp = isolated_client(PM, bp.ace_api_bp).get("/api/ace/events?limit=10")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True and body["pending"] == 1
    assert [(r["event_id"], r["status"]) for r in body["results"]] == [(event_id, "recommended")]


def test_the_events_route_answers_503_when_the_log_cannot_be_read(ace_env, monkeypatch):
    log = _log()
    bp = importlib.import_module("icdev.tools.ace.blueprint")

    def _broken():
        raise RuntimeError("relation ace_events does not exist")

    monkeypatch.setattr(log, "pending_count", _broken)
    resp = isolated_client(PM, bp.ace_api_bp).get("/api/ace/events")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body == {"ok": False, "error": "unavailable", "message": "the event log cannot be read"}
    assert "relation" not in resp.get_data(as_text=True)


def test_the_topics_route_answers_200_with_the_derived_map(ace_env):
    bp = importlib.import_module("icdev.tools.ace.blueprint")
    resp = isolated_client(PM, bp.ace_api_bp).get("/api/ace/events/topics")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["topics"] == _log().dispatch_topics()


def test_the_completion_event_reaches_the_log(ace_env):
    from icdev.tools.ace.controller import ACEController

    ACEController._emit_completion_event("inst-done")
    pending = _log().get_pending()
    assert [(e["topic"], e["source_canvas"], e["source_id"]) for e in pending] == [
        (TOPIC, "ace", "inst-done")]


def test_the_mcp_tools_are_declared_read_only_and_read_the_log(ace_env):
    from tools.mcp import gap_handlers
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    for name in ("ace_events", "ace_auto_dispatch_grants"):
        assert name in list_tools()
        assert READ_ONLY_DECLARATIONS[name] is True
        assert tool_authorization(name)["tier"] == "read"

    log = _log()
    event_id = log.emit(TOPIC, {}, roles=ace_env.roles)
    log.store_result(event_id, "evt_auto", status="recommended", step_id="step-2")
    out = gap_handlers.handle_ace_events({"limit": 5})
    assert out["pending"] == 1 and out["count"] == 1
    assert out["results"][0]["role_id"] == "evt_auto"
    assert "task.completed" in out["topics"]

    grants = gap_handlers.handle_ace_auto_dispatch_grants({})
    assert grants["grants"] == [] and grants["stop_all"]["stopped"] is False
    assert [(e["role_id"], e["topic"]) for e in grants["eligible"]] == [("evt_auto", TOPIC)]
