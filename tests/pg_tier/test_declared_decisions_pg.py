# CUI // SP-CTI
"""Declared decision steps on LIVE PostgreSQL (icx-cnf-02).

SQLite hid a PL/pgSQL bug in migration 20261114162849 (a RAISE format string
the storage layer rewrote). This runs the real paths on the ambient PG: seeding
wf_templates, a person's step through the engine (wf_instances, wf_approvals,
wf_feedback), propose / decide / spend, the append-only triggers, and a Cortex
finding's adjudication. The DB is shared and the tables are append-only, so every
assertion is scoped to ids this test minted.

Skips outside the tier (normal SQLite suite).
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ICDEV_PYTEST_PG", "").lower() not in ("1", "true", "yes"),
    reason="PG tier only — set ICDEV_PYTEST_PG=1 with a live PostgreSQL service",
)

PM = {"id": "pg-u-pm", "role": "pm"}
CO = {"id": "pg-u-co", "role": "co"}


def _one(sql, params):
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        return [tuple(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def test_the_backend_is_postgresql():
    from tools.db.storage import get_backend

    assert get_backend() == "postgresql"


def test_seed_converges():
    from tools.workflow_hitl import declared

    declared.seed_declared_templates()
    assert declared.seed_declared_templates() == {"seeded": [], "updated": []}
    rows = _one("SELECT COUNT(*) FROM wf_templates WHERE id LIKE %s AND is_default = 0",
                ("decl-wft-%",))
    assert rows == [(8,)]


def test_a_persons_step_runs_through_the_engine():
    from tools.workflow_hitl import declared

    subject = f"pg-{uuid.uuid4().hex[:8]}"
    ok = declared.take_step("decl-wft-ace-run-launch", surface="ace_api.api_launch",
                            decision="launch", subject=subject, actor=PM)
    no = declared.take_step("decl-wft-ace-hitl-resolve", surface="ace_api.api_hitl_resolve",
                            decision="reject", reason="pg", subject=subject, actor=PM)
    assert _one("SELECT status FROM wf_instances WHERE id = %s",
                (ok["instance_id"],)) == [("approved",)]
    assert _one("SELECT status FROM wf_instances WHERE id = %s",
                (no["instance_id"],)) == [("rejected",)]
    assert _one("SELECT decision, submitted_by FROM wf_feedback WHERE instance_id = %s",
                (no["instance_id"],)) == [("kickback", "pg-u-pm")]


def test_propose_decide_spend_once():
    from tools.workflow_hitl import declared

    payload = {"goal": f"pg-{uuid.uuid4().hex[:8]}"}
    prop = declared.propose("decl-wft-cortex-agent-launch", surface="cortex.api_v1_agent",
                            payload=payload, actor={"id": "svc", "role": "service"})
    declared.decide(prop["step_id"], decision="launch", actor=PM)
    with pytest.raises(declared.StepRefused):
        declared.decide(prop["step_id"], decision="launch", actor=CO)
    kw = {"template_id": "decl-wft-cortex-agent-launch", "surface": "cortex.api_v1_agent"}
    assert declared.spend(prop["step_id"], payload=payload, **kw)["status"] == "spent"
    with pytest.raises(declared.StepRefused) as err:
        declared.spend(prop["step_id"], payload=payload, **kw)
    assert err.value.code == "spent"
    steps = {s["step_id"]: s for s in declared.list_steps(limit=2000)}
    assert steps[prop["step_id"]]["status"] == "spent"


def test_the_ledger_is_append_only_on_postgresql():
    from tools.db.storage import get_connection
    from tools.workflow_hitl import declared

    step = declared.take_step("decl-wft-ace-run-launch", surface="ace_api.api_launch",
                              decision="launch", actor=PM)
    for sql in ("UPDATE wf_decision_steps SET decision = 'x' WHERE step_id = %s",
                "DELETE FROM wf_decision_steps WHERE step_id = %s"):
        conn = get_connection()
        try:
            with pytest.raises(Exception, match="append-only"):
                conn.execute(sql, (step["step_id"],))
        finally:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            conn.close()


def test_a_finding_is_adjudicated_once():
    from tools.cortex import finding_store
    from tools.db.storage import get_connection
    from tools.workflow_hitl import declared

    tenant = f"pg-{uuid.uuid4().hex[:8]}"
    row = finding_store.conflict_row(
        {"entity_key": "e1", "entity_label": "Widget", "kind": "status",
         "values": ["active", "retired"], "sides": []}, tenant, "CUI", "Widget", "unknown")
    conn = get_connection()
    try:
        finding_store._upsert(conn, row)
        conn.commit()
    finally:
        conn.close()
    fid = row["finding_id"]
    assert finding_store.get_finding(tenant, fid)["state"] == "open"
    step = declared.take_step("decl-wft-cortex-finding-adjudicate",
                              surface="dic.api_explorer_cortex_finding_adjudicate",
                              decision="accept", reason="pg", subject=fid, actor=PM)
    ruling = finding_store.adjudicate(tenant, "CUI", fid, "accept", "pg", step)
    assert ruling["adjudicated_by"] == "pg-u-pm"
    listed = finding_store.list_findings(tenant)
    assert listed[0]["state"] == "adjudicated"
    with pytest.raises(finding_store.AlreadyAdjudicated):
        finding_store.adjudicate(tenant, "CUI", fid, "reject", "again", step)
