# CUI // SP-CTI
"""Tests for tools.chat.cli_bridge ACE coworker helpers.

These exercise get_coworker_instances() against the REAL ace_instances schema
(id PK, trigger_ref nested in config_json, team in ace_coworkers) rather than
the columns the original draft assumed — guarding against the silent empty-list
failure that schema drift would otherwise hide.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone

import pytest

cli_bridge = importlib.import_module("tools.chat.cli_bridge")
from icdev.tools.ace.db.init_db import init as init_ace_db  # noqa: E402
from tools.db.storage import get_canvas_connection, sql_placeholder  # noqa: E402


def _insert_instance(instance_id: str, trigger_ref: str, coworkers: list[tuple]) -> None:
    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    try:
        ph = sql_placeholder(conn)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            f"INSERT INTO ace_instances "
            f"(id, name, role_id, state, trust_tier, config_json, created_at, updated_at) "
            f"VALUES ({ph}, {ph}, {ph}, 'active', 'yellow', {ph}, {ph}, {ph})",
            (instance_id, f"ace:chat:{trigger_ref}", "ai_developer",
             json.dumps({"trigger_ref": trigger_ref, "trigger_source": "chat"}), now, now),
        )
        for cw_id, role_id, display in coworkers:
            conn.execute(
                f"INSERT INTO ace_coworkers "
                f"(id, instance_id, role_id, display_name, state, trust_tier, created_at) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, 'idle', 'yellow', {ph})",
                (cw_id, instance_id, role_id, display, now),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def ace_db():
    init_ace_db()
    # Clear tables before each test to ensure isolation across runs
    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    try:
        conn.execute("DELETE FROM ace_coworkers")
        conn.execute("DELETE FROM ace_instances")
        conn.commit()
    finally:
        conn.close()
    yield


def test_returns_matching_instance_with_team(ace_db):
    _insert_instance(
        "ace-inst-match", "ctx-target",
        [("cw-1", "ai_developer", "AI Developer"),
         ("cw-2", "qa_manager", "QA Manager")],
    )
    _insert_instance("ace-inst-other", "ctx-different", [("cw-3", "ai_developer", "AI Developer")])

    result = cli_bridge.get_coworker_instances("ctx-target")

    assert len(result) == 1
    inst = result[0]
    assert inst["instance_id"] == "ace-inst-match"
    assert inst["state"] == "active"
    assert inst["created_at"]
    roles = {c["role_id"] for c in inst["team_manifest"]}
    assert roles == {"ai_developer", "qa_manager"}


def test_returns_empty_for_unknown_context(ace_db):
    assert cli_bridge.get_coworker_instances("ctx-nonexistent") == []


def test_returns_empty_when_table_missing(monkeypatch):
    # No init_ace_db here; force a DB error path -> graceful empty list.
    def _boom(*_a, **_k):
        raise RuntimeError("no db")

    import tools.db.storage as storage
    monkeypatch.setattr(storage, "get_canvas_connection", _boom)
    assert cli_bridge.get_coworker_instances("ctx-anything") == []


def test_dashboard_url_coworker():
    url = cli_bridge.dashboard_url_coworker("ace-inst-xyz")
    assert url.endswith("/coworker/ace-inst-xyz")
    assert url.startswith("http")
