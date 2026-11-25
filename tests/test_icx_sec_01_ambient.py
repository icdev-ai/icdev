# CUI // SP-CTI
"""Thread oracles narrowed, on whatever the platform database is (icx-sec-01).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: the resolver's SELECTs that now
also read a row's tenant and marking (``cortex_audit``, ``studio_workflows``, ``audit_trail``), a declared step's
rows in ``wf_decision_steps``, and a publisher's own ``dashboard_users`` row read fresh at a factory build's export.
In the normal suite it is the conftest SQLite database (``wf_db``). Fresh ids per run; nothing assumes a table empty.
"""
from __future__ import annotations

import contextlib
import json
import os
import uuid

import pytest


def _pg() -> bool:
    return os.environ.get("ICDEV_PYTEST_PG", "").lower() in ("1", "true", "yes")


@pytest.fixture(autouse=True)
def platform_db(request):
    if _pg():
        return None
    return request.getfixturevalue("wf_db")


def _same(a: dict, b: dict) -> bool:
    def strip(d):
        return json.dumps({k: v for k, v in d.items() if k != "thread_id"}, sort_keys=True, default=str)
    return strip(a) == strip(b)


def _exec(conn_kind: str, sql: str, params: tuple):
    from tools.twin_core import governance

    conn = governance._thread_conn(conn_kind)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def test_rows_with_a_tenant_or_a_marking_and_steps_on_the_platform_database():
    from tools.twin_core import governance
    from tools.workflow_hitl import declared

    run = uuid.uuid4().hex[:8]
    org = f"syn-sec01-amb-{run}"
    cid, wid = f"cgov-sec01-amb-{run}", f"wf-sec01-amb-{run}"
    _exec("rls", "INSERT INTO cortex_audit (id, session_id, tenant_id, classification, function, outcome) "
                 "VALUES (%s,%s,%s,%s,%s,%s)", (cid, f"s-{run}", org, "CUI", "cortex", "pass"))
    _exec("core", "INSERT INTO studio_workflows (workflow_id, name, template_yaml, created_by, tenant_id, "
                  "classification) VALUES (%s,%s,%s,%s,%s,%s)",
          (wid, "SYNTHETIC tenant workflow", "name: synthetic\n", "u-sec01-amb", org, "CUI"))
    marker = f"sec01.amb.{run}"
    _exec("core", "INSERT INTO audit_trail (event_type, actor, action, details, classification) "
                  "VALUES (%s,%s,%s,%s,%s)", ("approval_denied", "u-sec01-amb", marker, "{}", "SECRET"))
    conn = governance._thread_conn("core")
    try:
        aid = conn.execute("SELECT id FROM audit_trail WHERE action = %s", (marker,)).fetchone()[0]
    finally:
        conn.close()
    tmpl = declared.load_templates()["decl-wft-cortex-award"]
    hidden_step = declared.propose("decl-wft-cortex-award", surface=tmpl["decides"][0], subject=f"bid:{run}",
                                   payload={"bid": run}, actor={"id": f"u-sec01-amb-{run}", "role": "pm"},
                                   note="SYNTHETIC")["step_id"]
    tmpl = declared.load_templates()["decl-wft-coe-standard-review"]
    open_step = declared.propose("decl-wft-coe-standard-review", surface=tmpl["decides"][0],
                                 subject=f"standard:{run}", payload={},
                                 actor={"id": f"u-sec01-amb-{run}", "role": "developer"})["step_id"]

    tenant = {"id": f"u-sec01-amb-{run}", "role": "isso", "clearance": "CUI", "org": org}
    other = dict(tenant, org=f"{org}-other")
    secret = dict(tenant, clearance="SECRET")
    agent = governance.agent_reader("sec01-amb")
    cases = ((f"cortex_audit:{cid}", "cortex_audit:cgov-none", tenant, [other, agent, None]),
             (f"studio_workflow:{wid}", "studio_workflow:wf-none", tenant, [other, agent, None]),
             (f"audit_trail:{aid}", "audit_trail:999999999", secret, [tenant, agent, None]),
             (f"wf_step:{hidden_step}", "wf_step:step-" + "0" * 16, None, [secret, agent, None]))
    for thread, unknown, who, hidden in cases:
        if who is not None:
            res = governance.resolve_thread(thread, reader=who)
            assert res["resolved"], (thread, res)
        for r in hidden:
            got, want = governance.resolve_thread(thread, reader=r), governance.resolve_thread(unknown, reader=r)
            assert not got["resolved"] and _same(got, want), (thread, r, got, want)
    assert governance.resolve_thread(f"wf_step:{open_step}", reader=agent)["resolved"]


def test_the_publisher_s_own_row_is_read_fresh_at_export():
    """``_publisher_still_eligible`` reads ``dashboard_users`` itself: a real row on PostgreSQL (tenant_id,
    clearance_level, status), the conftest SQLite table's lookalike otherwise."""
    from tools.factory_builds import service

    run = uuid.uuid4().hex[:8]
    org = f"syn-sec01-amb-{run}"
    t = {"id": f"syn-sec01-{run}", "org": org, "marking": "CUI", "synthetic": True}
    uid = f"u-sec01-amb-{run}-publisher"
    if not _pg():
        from tools.dashboard import auth

        rows = {uid: {"id": uid, "role": "isso", "tenant_id": org, "clearance_level": "CUI", "status": "active"}}
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(auth, "get_user_by_id", lambda u, tenant_id=None: rows.get(str(u)))
            assert service._publisher_still_eligible({"actor": uid}, t)
            rows[uid]["tenant_id"] = "another-org"
            assert not service._publisher_still_eligible({"actor": uid}, t)
        return
    from tools.db.storage import get_connection

    c = get_connection()
    try:
        c.execute("INSERT INTO dashboard_users (id, email, display_name, role, status, tenant_id, clearance_level) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s)", (uid, f"{uid}@test.invalid", "SYNTHETIC test person (icx-sec-01)",
                                                    "isso", "active", org, "CUI"))
        c.commit()
        assert service._publisher_still_eligible({"actor": uid}, t)
        assert not service._publisher_still_eligible({"actor": uid}, dict(t, org="another-org"))
        assert not service._publisher_still_eligible({"actor": uid}, dict(t, marking="SECRET"))
        c.execute("UPDATE dashboard_users SET status = 'suspended' WHERE id = %s", (uid,))
        c.commit()
        assert not service._publisher_still_eligible({"actor": uid}, t)
        assert not service._publisher_still_eligible({"actor": f"{uid}-nobody"}, t)
    finally:
        with contextlib.suppress(Exception):
            c.rollback()
        c.execute("DELETE FROM dashboard_users WHERE id = %s", (uid,))
        c.commit()
        c.close()
