# CUI // SP-CTI
"""Workflow template CRUD — policy-as-data stored in wf_templates."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection
from tools.workflow_hitl.constants import ApprovalPolicy, DEFAULT_STAGES, DEFAULT_ROLES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_default(canvas_type: str | None = None):
    """Return the is_default system template for a canvas type, or global default."""
    conn = get_connection()
    try:
        if canvas_type:
            row = conn.execute(
                "SELECT * FROM wf_templates WHERE canvas_type=%s AND is_default=1 AND is_system=1 LIMIT 1",
                (canvas_type,),
            ).fetchone()
            if row:
                return dict(row)
        row = conn.execute(
            "SELECT * FROM wf_templates WHERE is_default=1 AND is_system=1 AND canvas_type IS NULL LIMIT 1",
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get(template_id: str):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM wf_templates WHERE id=%s", (template_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_templates(canvas_type: str | None = None) -> list:
    conn = get_connection()
    try:
        if canvas_type:
            rows = conn.execute(
                "SELECT * FROM wf_templates WHERE canvas_type=%s OR canvas_type IS NULL ORDER BY is_system DESC, name",
                (canvas_type,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM wf_templates ORDER BY is_system DESC, name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create(
    name: str,
    canvas_type: str | None = None,
    stages_json: list | str | None = None,
    roles_json: dict | str | None = None,
    approval_policy: str = ApprovalPolicy.ANY_ONE,
    kickback_limit: int = 3,
    created_by: str | None = None,
) -> str:
    template_id = f"wft-{uuid.uuid4().hex[:12]}"
    stages = stages_json if isinstance(stages_json, str) else json.dumps(stages_json or DEFAULT_STAGES)
    roles  = roles_json  if isinstance(roles_json,  str) else json.dumps(roles_json  or DEFAULT_ROLES)
    now = _now()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO wf_templates
               (id, name, canvas_type, stages_json, roles_json, approval_policy,
                kickback_limit, is_default, is_system, created_by, created_at, updated_at)
               VALUES (%s,?,?,?,?,?,?,0,0,?,?,%s)""",
            (template_id, name, canvas_type, stages, roles, approval_policy,
             kickback_limit, created_by, now, now),
        )
        try:
            conn.commit()
        except Exception:
            pass
        return template_id
    finally:
        conn.close()


def update(template_id: str, **kwargs) -> bool:
    tmpl = get(template_id)
    if not tmpl:
        return False
    if tmpl.get("is_system"):
        raise PermissionError("System templates cannot be modified.")
    allowed = {"name", "stages_json", "roles_json", "approval_policy", "kickback_limit"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k}=%s" for k in updates)
    vals = list(updates.values()) + [template_id]
    conn = get_connection()
    try:
        conn.execute(f"UPDATE wf_templates SET {set_clause} WHERE id=%s", vals)
        try:
            conn.commit()
        except Exception:
            pass
        return True
    finally:
        conn.close()


def fork(template_id: str, new_name: str, created_by: str | None = None) -> str:
    """Create an editable copy of any template (including system templates)."""
    tmpl = get(template_id)
    if not tmpl:
        raise ValueError(f"Template {template_id!r} not found")
    return create(
        name=new_name,
        canvas_type=tmpl.get("canvas_type"),
        stages_json=tmpl["stages_json"],
        roles_json=tmpl["roles_json"],
        approval_policy=tmpl["approval_policy"],
        kickback_limit=tmpl["kickback_limit"],
        created_by=created_by,
    )


def seed_system_templates():
    """Idempotent: seed one system default template per canvas type + global default."""
    import json
    from tools.workflow_hitl.constants import CANVAS_ROLE_DEFAULTS

    canvas_types = list(CANVAS_ROLE_DEFAULTS.keys()) + [None]
    conn = get_connection()
    try:
        for ct in canvas_types:
            if ct is None:
                existing = conn.execute(
                    "SELECT id FROM wf_templates WHERE canvas_type IS NULL AND is_system=1 AND is_default=1",
                ).fetchone()
            else:
                existing = conn.execute(
                    "SELECT id FROM wf_templates WHERE canvas_type=%s AND is_system=1 AND is_default=1",
                    (ct,),
                ).fetchone()
            if existing:
                continue
            tid = f"sys-wft-{ct.lower() if ct else 'global'}"
            stages = [
                {"name": "build",   "step_type": "automated"},
                {"name": "review",  "step_type": "manual",  "role": CANVAS_ROLE_DEFAULTS.get(ct, "reviewer") if ct else "reviewer",
                 "required_docs": [{"doc_template_id": "sys-dt-peer-review-checklist", "required": True}]},
                {"name": "approve", "step_type": "manual",  "role": "manager",
                 "required_docs": [{"doc_template_id": "sys-dt-security-sign-off", "required": False}]},
            ]
            roles = {
                "build":   CANVAS_ROLE_DEFAULTS.get(ct, "engineer") if ct else "engineer",
                "review":  "reviewer",
                "approve": "manager",
            }
            now = _now()
            conn.execute(
                """INSERT OR IGNORE INTO wf_templates
                   (id, name, canvas_type, stages_json, roles_json, approval_policy,
                    kickback_limit, is_default, is_system, created_by, created_at, updated_at)
                   VALUES (%s,?,?,?,?,?,?,1,1,'system',?,%s)""",
                (tid, f"Default {'Global' if ct is None else ct} Workflow",
                 ct, json.dumps(stages), json.dumps(roles),
                 ApprovalPolicy.ANY_ONE, 3, now, now),
            )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()
