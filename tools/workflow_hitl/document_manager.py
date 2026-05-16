# CUI // SP-CTI
"""Document template CRUD, submission gate, and AI standards lookup."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Template CRUD ─────────────────────────────────────────────────────────────

def get_template(doc_template_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM wf_document_templates WHERE id=%s", (doc_template_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_templates(
    canvas_type: str | None = None,
    doc_type: str | None = None,
) -> list:
    conn = get_connection()
    try:
        clauses, params = [], []
        if canvas_type:
            clauses.append("(canvas_type=%s OR canvas_type IS NULL)")
            params.append(canvas_type)
        if doc_type:
            clauses.append("doc_type=%s")
            params.append(doc_type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM wf_document_templates {where} ORDER BY is_system DESC, name",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_template(
    name: str,
    doc_type: str,
    schema_json: dict | list | str,
    canvas_type: str | None = None,
    stage_scope: str | None = None,
    is_ai_reference: bool = False,
    is_human_required: bool = False,
    version: str = "1",
    created_by: str | None = None,
) -> str:
    doc_id = f"wfdt-{uuid.uuid4().hex[:12]}"
    schema = schema_json if isinstance(schema_json, str) else json.dumps(schema_json)
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO wf_document_templates
               (id, name, doc_type, schema_json, canvas_type, stage_scope,
                is_ai_reference, is_human_required, version, is_system, created_by, created_at)
               VALUES (%s,?,?,?,?,?,?,?,?,0,?,%s)""",
            (doc_id, name, doc_type, schema, canvas_type, stage_scope,
             int(is_ai_reference), int(is_human_required), version, created_by, _now()),
        )
        try:
            conn.commit()
        except Exception:
            pass
        return doc_id
    finally:
        conn.close()


# ── Submission ────────────────────────────────────────────────────────────────

def submit_document(
    approval_id: str,
    instance_id: str,
    doc_template_id: str,
    stage: str,
    submitted_by: str,
    submission_json: dict | list | str,
) -> str:
    sub_id = f"wfds-{uuid.uuid4().hex[:12]}"
    data = submission_json if isinstance(submission_json, str) else json.dumps(submission_json)
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO wf_document_submissions
               (id, approval_id, instance_id, doc_template_id, stage, submitted_by, submission_json, submitted_at)
               VALUES (%s,?,?,?,?,?,?,%s)""",
            (sub_id, approval_id, instance_id, doc_template_id, stage, submitted_by, data, _now()),
        )
        try:
            conn.commit()
        except Exception:
            pass
        return sub_id
    finally:
        conn.close()


def all_required_submitted(approval_id: str, stage_config: dict) -> bool:
    """Return True if all required_docs for this stage have been submitted for this approval."""
    required_docs = stage_config.get("required_docs", [])
    required_ids = {d["doc_template_id"] for d in required_docs if d.get("required", False)}
    if not required_ids:
        return True
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT doc_template_id FROM wf_document_submissions WHERE approval_id=%s",
            (approval_id,),
        ).fetchall()
        submitted_ids = {r[0] for r in rows}
        return required_ids.issubset(submitted_ids)
    finally:
        conn.close()


def get_missing_docs(approval_id: str, stage_config: dict) -> list[str]:
    """Return list of doc_template_ids that are required but not yet submitted."""
    required_docs = stage_config.get("required_docs", [])
    required_ids = {d["doc_template_id"] for d in required_docs if d.get("required", False)}
    if not required_ids:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT doc_template_id FROM wf_document_submissions WHERE approval_id=%s",
            (approval_id,),
        ).fetchall()
        submitted_ids = {r[0] for r in rows}
        return list(required_ids - submitted_ids)
    finally:
        conn.close()


def get_submissions(approval_id: str) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM wf_document_submissions WHERE approval_id=%s ORDER BY submitted_at",
            (approval_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── AI Standards Lookup ───────────────────────────────────────────────────────

def get_applicable_standards(
    canvas_type: str | None = None,
    stage: str | None = None,
) -> list:
    """Return all AI-reference standard templates applicable to a canvas/stage.

    ICDEV's automated steps call this before generating solutions so the LLM
    can apply the standards as constraints and cite them in the output.
    """
    conn = get_connection()
    try:
        clauses = ["is_ai_reference=1", "doc_type='standard'"]
        params: list = []
        if canvas_type:
            clauses.append("(canvas_type=%s OR canvas_type IS NULL)")
            params.append(canvas_type)
        else:
            clauses.append("canvas_type IS NULL")
        where = "WHERE " + " AND ".join(clauses)
        rows = conn.execute(
            f"SELECT * FROM wf_document_templates {where} ORDER BY name",
            params,
        ).fetchall()
        standards = []
        for r in rows:
            s = dict(r)
            # Filter by stage_scope if set
            if stage and s.get("stage_scope"):
                scopes = [x.strip() for x in s["stage_scope"].split(",")]
                if stage not in scopes:
                    continue
            try:
                s["schema"] = json.loads(s["schema_json"])
            except Exception:
                s["schema"] = {}
            standards.append(s)
        return standards
    finally:
        conn.close()
