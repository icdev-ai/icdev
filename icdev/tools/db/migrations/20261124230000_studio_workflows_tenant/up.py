# CUI // SP-CTI
"""Migration 20261124230000 — give studio workflows with no tenant their creator's tenant (icx-sec-01).

Until icx-sec-01 the five write paths into ``studio_workflows`` (tools/studio/workflow_editor.py create and save,
tools/studio/nl_app_builder.py, tools/workflow_canvas/blueprint.py, tools/workflow_canvas/observed_processify.py)
never set ``tenant_id``. The twin's thread resolver now holds a workflow to its tenant and fails closed on a row with
none, so a row whose creator is a person with a tenant gets that tenant. A row whose creator is no person with a
tenant (``studio``, ``processify``, an unknown id) is left NULL: it is no tenant's and resolves for no one.

``studio_workflows`` is a mutable table (update_workflow edits it). ``idp_rule_exemptions`` is NOT backfilled: it is
append-only and hash-chained, and ``tenant_id`` is inside each row's hash, so its old NULL rows stay NULL and fail
closed (except the CoE bootstrap migration's own rows, which are the platform's).

One UPDATE, the same SQL on SQLite and PostgreSQL; a re-run converges (it only touches NULL rows). A database whose
``dashboard_users`` has no ``tenant_id`` column (a minimal SQLite) has nothing to read it from and is skipped.
"""
from __future__ import annotations

_TAG = "[20261124230000_studio_workflows_tenant]"

BACKFILL = (
    "UPDATE studio_workflows SET tenant_id = (SELECT u.tenant_id FROM dashboard_users u "
    "WHERE u.id = studio_workflows.created_by) "
    "WHERE (tenant_id IS NULL OR tenant_id = '') AND EXISTS (SELECT 1 FROM dashboard_users u "
    "WHERE u.id = studio_workflows.created_by AND u.tenant_id IS NOT NULL AND u.tenant_id <> '')"
)


def up(conn) -> dict:
    try:
        conn.execute("SELECT tenant_id FROM dashboard_users WHERE 1 = 0").fetchall()
        conn.execute("SELECT tenant_id, created_by FROM studio_workflows WHERE 1 = 0").fetchall()
    except Exception as exc:  # noqa: BLE001 — no column to read from: nothing to backfill
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        print(f"{_TAG} skipped: {type(exc).__name__}")
        return {"status": "skipped", "reason": "dashboard_users.tenant_id or studio_workflows absent"}
    cur = conn.execute(BACKFILL)
    conn.commit()
    n = getattr(cur, "rowcount", None)
    print(f"{_TAG} studio_workflows rows given their creator's tenant: {n}")
    return {"status": "applied", "rows": n}
