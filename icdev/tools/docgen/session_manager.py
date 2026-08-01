# CUI // SP-CTI
"""IDR Session Manager — CRUD for idr_sessions and idr_uploads."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection


# ─── Sessions ────────────────────────────────────────────────────────────────

def create_session(
    title: str,
    domain: str,
    doc_type: str = "runbook",
    template_id: str | None = None,
    created_by: str | None = None,
    tenant_id: str | None = None,
    classification: str = "CUI",
) -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO idr_sessions
              (id, title, domain, doc_type, template_id, stage, status,
               created_by, tenant_id, classification, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,0,'setup',%s,%s,%s,%s,%s)
            """,
            (session_id, title, domain, doc_type, template_id,
             created_by, tenant_id, classification, now, now),
        )
        conn.commit()
    return get_session(session_id)


def get_session(session_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM idr_sessions WHERE id = %s", (session_id,)
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def list_sessions(
    domain: str | None = None,
    tenant_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM idr_sessions WHERE 1=1"
    params: list[Any] = []
    if domain:
        sql += " AND domain = %s"
        params.append(domain)
    if tenant_id:
        sql += " AND tenant_id = %s"
        params.append(tenant_id)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def advance_stage(session_id: str, new_stage: int, new_status: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE idr_sessions SET stage=%s, status=%s, updated_at=%s WHERE id=%s",
            (new_stage, new_status, now, session_id),
        )
        conn.commit()
    return cur.rowcount == 1


def set_field(session_id: str, **kwargs: Any) -> bool:
    """Update arbitrary fields on a session row."""
    if not kwargs:
        return False
    now = datetime.now(timezone.utc).isoformat()
    kwargs["updated_at"] = now
    set_clause = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values()) + [session_id]
    with get_connection() as conn:
        cur = conn.execute(
            f"UPDATE idr_sessions SET {set_clause} WHERE id = %s", values
        )
        conn.commit()
    return cur.rowcount == 1


def fail_session(session_id: str, error_msg: str | None = None) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE idr_sessions SET status='failed', updated_at=%s WHERE id=%s",
            (now, session_id),
        )
        conn.commit()
    return cur.rowcount == 1


# ─── Uploads ─────────────────────────────────────────────────────────────────

def add_upload(
    session_id: str,
    filename: str,
    upload_type: str,
    file_path: str | None = None,
    file_hash: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    upload_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    # cnr-doc-04: single insert matching the canonical idr_uploads schema
    # (migration 211) on BOTH backends. The previous _IS_PG branch inserted
    # upload_id/collection_id/filepath columns that no migration ever creates
    # (the "DIC canvas creates the table" comment was false) — so every PG upload
    # raised UndefinedColumn. Those columns were write-only and read nowhere.
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO idr_uploads
              (id, session_id, filename, upload_type, file_path, file_hash,
               status, tenant_id, uploaded_at)
            VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s)
            """,
            (upload_id, session_id, filename, upload_type,
             file_path, file_hash, tenant_id, now),
        )
        conn.commit()
    return get_upload(upload_id)


def get_upload(upload_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM idr_uploads WHERE id = %s", (upload_id,)
        ).fetchone()
    return dict(row) if row else None


def list_uploads(session_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM idr_uploads WHERE session_id = %s ORDER BY uploaded_at",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_upload_status(
    upload_id: str,
    status: str,
    dic_doc_id: str | None = None,
    error_msg: str | None = None,
) -> bool:
    updates = {"status": status}
    if dic_doc_id is not None:
        updates["dic_doc_id"] = dic_doc_id
    if error_msg is not None:
        updates["error_msg"] = error_msg
    set_clause = ", ".join(f"{k}=%s" for k in updates)
    values = list(updates.values()) + [upload_id]
    with get_connection() as conn:
        cur = conn.execute(
            f"UPDATE idr_uploads SET {set_clause} WHERE id=%s", values
        )
        conn.commit()
    return cur.rowcount == 1


# ─── Analyses ────────────────────────────────────────────────────────────────

def add_analysis(
    session_id: str,
    upload_id: str,
    analysis_type: str,
    result_ref_id: str,
    tenant_id: str | None = None,
    result_json: str | None = None,
) -> dict[str, Any]:
    analysis_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO idr_analyses
              (id, session_id, upload_id, analysis_type, result_ref_id,
               status, tenant_id, created_at, result_json)
            VALUES (%s,%s,%s,%s,%s,'done',%s,%s,%s)
            """,
            (analysis_id, session_id, upload_id, analysis_type,
             result_ref_id, tenant_id, now, result_json),
        )
        conn.commit()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM idr_analyses WHERE id=%s", (analysis_id,)
        ).fetchone()
    return dict(row)


def list_analyses(session_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM idr_analyses WHERE session_id=%s ORDER BY created_at",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Conflicts ───────────────────────────────────────────────────────────────

def add_conflict(
    session_id: str,
    node_label: str,
    conflict_type: str,
    source_a: str,
    source_b: str,
    source_a_value: Any = None,
    source_b_value: Any = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    conflict_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO idr_conflicts
              (id, session_id, node_label, conflict_type,
               source_a, source_a_value, source_b, source_b_value,
               tenant_id, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                conflict_id, session_id, node_label, conflict_type,
                source_a,
                json.dumps(source_a_value) if source_a_value is not None else None,
                source_b,
                json.dumps(source_b_value) if source_b_value is not None else None,
                tenant_id, now,
            ),
        )
        conn.commit()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM idr_conflicts WHERE id=%s", (conflict_id,)
        ).fetchone()
    return dict(row)


def resolve_conflict(
    conflict_id: str,
    resolution: str,
    resolved_by: str,
    resolution_notes: str | None = None,
) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE idr_conflicts
            SET resolution=%s, resolved_by=%s, resolution_notes=%s, resolved_at=%s
            WHERE id=%s AND resolved_at IS NULL
            """,
            (resolution, resolved_by, resolution_notes, now, conflict_id),
        )
        conn.commit()
    return cur.rowcount == 1


def list_conflicts(session_id: str, pending_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM idr_conflicts WHERE session_id=%s"
    params: list[Any] = [session_id]
    if pending_only:
        sql += " AND resolved_at IS NULL"
    sql += " ORDER BY created_at"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def pending_conflict_count(session_id: str) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM idr_conflicts WHERE session_id=%s AND resolved_at IS NULL",
            (session_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


# ─── Artifacts ───────────────────────────────────────────────────────────────

def add_artifact(
    session_id: str,
    fmt: str,
    file_path: str | None = None,
    dic_doc_id: str | None = None,
    dic_version_id: str | None = None,
    wg_result_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    artifact_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO idr_artifacts
              (id, session_id, dic_doc_id, dic_version_id, format,
               file_path, wg_result_id, published_at, tenant_id, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                artifact_id, session_id, dic_doc_id, dic_version_id,
                fmt, file_path, wg_result_id, now, tenant_id, now,
            ),
        )
        conn.commit()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM idr_artifacts WHERE id=%s", (artifact_id,)
        ).fetchone()
    return dict(row)


# ─── Publish gate audit (cnr-doc-01, append-only) ────────────────────────────

def record_publish_audit(
    session_id: str,
    gate: str,
    reviewer: str,
    findings: Any,
    tenant_id: str | None = None,
) -> str | None:
    """Persist a HITL force-override of the TRUST publish gate (append-only).

    Called when an operator publishes past a ``citation_guard`` / ``placeholder_guard``
    defect via ``force_*``. Best-effort: never raises (a failed audit write must not
    block the operator), but returns the row id when it succeeds so callers can assert.
    """
    audit_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        detail = findings if isinstance(findings, str) else json.dumps(findings)
    except (TypeError, ValueError):
        detail = str(findings)
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO idr_publish_audit
                  (id, session_id, gate, reviewer, findings, tenant_id, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (audit_id, session_id, gate, reviewer, detail, tenant_id, now),
            )
            conn.commit()
        return audit_id
    except Exception:
        return None


def list_publish_audit(session_id: str) -> list[dict[str, Any]]:
    """Return append-only publish-gate override rows for a session (newest first)."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM idr_publish_audit WHERE session_id=%s ORDER BY created_at DESC",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM idr_artifacts WHERE id=%s", (artifact_id,)
        ).fetchone()
    return dict(row) if row else None


def list_artifacts(session_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM idr_artifacts WHERE session_id=%s ORDER BY created_at",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]
