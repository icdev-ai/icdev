# CUI // SP-CTI
"""The ACE event log and auto-dispatch grants on whatever the process database is (icx-ace-01).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is
PostgreSQL: placeholders, RETURNING, ADD COLUMN on the table the consolidated
snapshot made (migration 20261115114944) and its PL/pgSQL append-only trigger.
In the normal suite it is the conftest SQLite database, with init_db's triggers.

No fixture here redirects the backend or opens a raw connection. The database
may be shared and the grants log is append-only, so every assertion is scoped to
ids this file mints.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(autouse=True)
def _ace_tables():
    from icdev.tools.ace.db.init_db import init

    init()


def _rows(sql, params=()):
    from tools.db.storage import get_canvas_connection

    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    try:
        return [tuple(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def test_the_results_columns_and_the_grants_table_exist():
    from tools.db.storage import get_canvas_connection, is_pg

    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    pg = is_pg(conn)
    conn.close()
    if pg:
        cols = {r[0] for r in _rows(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            ("ace_event_results",))}
        assert _rows("SELECT to_regclass('public.ace_dispatch_grants') IS NOT NULL") == [(True,)]
    else:
        cols = {r[1] for r in _rows("PRAGMA table_info(ace_event_results)")}
        assert _rows("SELECT name FROM sqlite_master WHERE name = %s",
                     ("ace_dispatch_grants",)) == [("ace_dispatch_grants",)]
    assert {"step_id", "grant_id", "reason", "updated_at"} <= cols


def test_emit_store_and_read_back_on_pg():
    from icdev.tools.ace import event_log

    source = f"pg-{uuid.uuid4().hex[:8]}"
    event_id = event_log.emit("task.completed", {"content": "pg"}, source_canvas="pgtest",
                              source_id=source)
    assert isinstance(event_id, int)
    result_id = event_log.store_result(event_id, "reflexion_loop", status="recommended",
                                       step_id=f"step-{source}")
    event_log.update_result(result_id, "launched", instance_id=f"inst-{source}", reason="pg")
    row = next(r for r in event_log.get_recent_results(limit=200) if r["id"] == result_id)
    assert (row["status"], row["instance_id"], row["topic"]) == (
        "launched", f"inst-{source}", "task.completed")
    event_log.mark_processed(event_id)
    assert all(e["id"] != event_id for e in event_log.get_pending(limit=500))


def test_the_grants_log_is_append_only_on_pg():
    from icdev.tools.ace import auto_dispatch

    grant_id = f"adg-pg{uuid.uuid4().hex[:8]}"
    auto_dispatch._append("granted", grant_id, role_id="pg_role", topic="task.completed",
                          expires_at="2999-01-01 00:00:00", max_per_day=2, reason="pg",
                          actor={"id": "pg-u-pm", "role": "pm"})
    auto_dispatch._append("revoked", grant_id, role_id="pg_role", topic="task.completed",
                          reason="pg stop", actor={"id": "pg-u-pm", "role": "pm"})
    mine = [g for g in auto_dispatch.grants() if g["grant_id"] == grant_id]
    assert [(g["state"], g["closed_reason"]) for g in mine] == [("revoked", "pg stop")]

    from tools.db.storage import get_canvas_connection

    for sql in ("UPDATE ace_dispatch_grants SET reason = 'x' WHERE grant_id = %s",
                "DELETE FROM ace_dispatch_grants WHERE grant_id = %s"):
        conn = get_canvas_connection("ICDEV_ACE_DB_URL")
        try:
            with pytest.raises(Exception, match="append-only"):
                conn.execute(sql, (grant_id,))
        finally:
            conn.rollback()
            conn.close()


def test_a_signed_in_request_can_read_the_decision_ledger():
    """/decisions read and decided nothing on PostgreSQL: the row predicate named
    ``classification`` on wf_decision_steps, which has no such column. The ledger is
    exempt (args/schema_ownership_rules.yaml); the wf_ engine tables are not."""
    from tools.security.row_security import inject_row_predicate

    for sql in ("SELECT step_id, event FROM wf_decision_steps WHERE step_id = %s",
                # NOVA trust, read by the auto-dispatch grant route inside a request.
                "SELECT new_score FROM ace_trust_ledger WHERE role_id = %s",
                "SELECT trust_score FROM ace_coworkers WHERE role_id = %s"):
        assert inject_row_predicate(sql, tenant_id="default", classifications={"CUI"},
                                    placeholder="%s") == (sql, (), 0)
    engine_sql, extra, _ = inject_row_predicate(
        "SELECT id FROM wf_instances WHERE id = %s", tenant_id="default",
        classifications={"CUI"}, placeholder="%s")
    assert "classification" in engine_sql and extra
