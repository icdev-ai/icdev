# CUI // SP-CTI
"""IQE Software Integrity & Provenance Assessor (SIPA) collection adapters.

Registers four read-only collections on the module-level Executor, each returning
REAL rows from the RLS-aware integrity_* tables (every row carries tenant_id +
classification, so reads through ``get_connection()`` are filtered to the caller):

  integrity.assessments   — staged sources + verdict/status   (integrity_assessments)
  integrity.capabilities  — detected capability manifest        (integrity_capabilities)
  integrity.findings      — scanner findings (SAST/secrets/...)  (integrity_findings)
  integrity.verdicts      — recorded disposition decisions       (integrity_verdicts)

Mirrors the ``tools/iqe/adapters/dic.py`` pattern: each adapter opens an RLS-aware
connection (when none is supplied), runs a parameter-free SELECT (works on both
SQLite and PostgreSQL), and degrades to an empty list if the schema is not yet
present (migration 179 not run on this DB).
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415

        conn = get_connection()
    return conn


def assessments_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, source_type, source_ref, mode, project_id, session_id, "
            "dir_digest, status, verdict, risk_score, created_by, created_at, updated_at "
            "FROM integrity_assessments ORDER BY id DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def capabilities_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, assessment_id, file_path, function_name, capability_type, "
            "evidence, line_start, line_end, risk_weight, created_at "
            "FROM integrity_capabilities ORDER BY risk_weight DESC, id DESC LIMIT 1000"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def findings_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, assessment_id, source_scanner, finding_type, severity, "
            "file_path, line, detail, created_at "
            "FROM integrity_findings ORDER BY id DESC LIMIT 1000"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def verdicts_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, assessment_id, verdict, risk_score, rationale, decided_by, created_at "
            "FROM integrity_verdicts ORDER BY id DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


register_collection("integrity.assessments", assessments_adapter)
register_collection("integrity.capabilities", capabilities_adapter)
register_collection("integrity.findings", findings_adapter)
register_collection("integrity.verdicts", verdicts_adapter)
