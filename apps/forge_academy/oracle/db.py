# CUI // SP-CTI
"""FORGE Academy Oracle DB layer — fa_oracle_predictions + fa_oracle_convergence_events."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

# fa_oracle_* tables are internal academy analytics with no tenant/classification
# scoping in their schema (apps/forge_academy/db.py). The stray `classification`
# column auto-added by a blanket migration makes get_connection()'s RLS predicate
# inject `classification = ?`, which mis-binds non-string params (e.g. LIMIT 200)
# and raises `character varying = integer` on PostgreSQL. Use the RLS-disabled
# canvas connection — the documented remedy for non-tenant-scoped tables.
from tools.db.storage import get_canvas_connection as get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "fao") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Predictions
# ---------------------------------------------------------------------------

def prediction_exists(lens_id: str, subject_id: str, prediction_type: str,
                      window_hours: int = 24) -> bool:
    """Return True if an identical prediction was inserted within the window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM fa_oracle_predictions "
        "WHERE lens_id=%s AND subject_id=%s AND prediction_type=%s AND created_at>%s",
        (lens_id, subject_id, prediction_type, cutoff),
    ).fetchone()
    return row is not None


def insert_prediction(
    lens_id: str,
    lens_name: str,
    subject_type: str,
    subject_id: str,
    prediction_type: str,
    prediction_text: str,
    confidence: float,
    severity: str = "info",
    horizon_days: int = 7,
    evidence: dict[str, Any] | None = None,
    dedup_window_hours: int = 24,
) -> str | None:
    """Insert a prediction row; returns the new id or None if deduped."""
    if prediction_exists(lens_id, subject_id, prediction_type, dedup_window_hours):
        return None
    row_id = _new_id()
    conn = get_connection()
    conn.execute(
        "INSERT INTO fa_oracle_predictions "
        "(id,lens_id,lens_name,subject_type,subject_id,prediction_type,"
        "prediction_text,confidence,severity,horizon_days,evidence_json,created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            row_id, lens_id, lens_name, subject_type, subject_id,
            prediction_type, prediction_text, confidence, severity,
            horizon_days, json.dumps(evidence or {}), _utcnow(),
        ),
    )
    conn.commit()
    return row_id


def list_predictions(
    lens_id: str | None = None,
    severity: str | None = None,
    outcome: str | None = None,
    limit: int = 100,
    conn=None,
) -> list[dict]:
    """Return recent predictions, optionally filtered.

    Accepts an optional ``conn`` so a caller can share one connection across
    several reads.  Each ``get_canvas_connection()`` costs ~2s in the dashboard
    (new PG connection), so the page handler reuses a single connection.
    """
    conn = conn or get_connection()
    clauses: list[str] = []
    params: list[Any] = []
    if lens_id:
        clauses.append("lens_id=?")
        params.append(lens_id)
    if severity:
        clauses.append("severity=?")
        params.append(severity)
    if outcome:
        clauses.append("outcome=?")
        params.append(outcome)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM fa_oracle_predictions {where} ORDER BY created_at DESC LIMIT %s",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def update_prediction_outcome(pred_id: str, outcome: str) -> None:
    conn = get_connection()
    conn.execute("UPDATE fa_oracle_predictions SET outcome=%s WHERE id=%s", (outcome, pred_id))
    conn.commit()


def summary_stats(conn=None) -> dict:
    """Return aggregate counts for the Oracle summary bar.

    Accepts an optional shared ``conn`` (see ``list_predictions``).
    """
    conn = conn or get_connection()
    total = conn.execute(
        "SELECT COUNT(*) FROM fa_oracle_predictions WHERE outcome='pending'"
    ).fetchone()[0]
    critical = conn.execute(
        "SELECT COUNT(*) FROM fa_oracle_predictions WHERE severity='critical' AND outcome='pending'"
    ).fetchone()[0]
    warning = conn.execute(
        "SELECT COUNT(*) FROM fa_oracle_predictions WHERE severity='warning' AND outcome='pending'"
    ).fetchone()[0]
    convergence = conn.execute(
        "SELECT COUNT(*) FROM fa_oracle_convergence_events WHERE resolved_at IS NULL"
    ).fetchone()[0]
    last_row = conn.execute(
        "SELECT created_at FROM fa_oracle_predictions ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return {
        "total_pending": total,
        "critical": critical,
        "warning": warning,
        "convergence_events": convergence,
        "last_run_at": last_row[0] if last_row else None,
    }


def page_payload(prediction_limit: int = 200, convergence_limit: int = 20) -> dict:
    """Fetch all Oracle page data over a single shared connection.

    Latency fix: ``oracle_page`` previously opened three separate canvas
    connections (one per read).  In the dashboard each new connection costs
    ~2s, so the page took ~6s of pure connection overhead.  Acquiring one
    connection and reusing it for all three reads cuts that to ~2s.
    """
    conn = get_connection()
    return {
        "stats": summary_stats(conn=conn),
        "predictions": list_predictions(limit=prediction_limit, conn=conn),
        "convergence": list_convergence_events(limit=convergence_limit, conn=conn),
    }


# ---------------------------------------------------------------------------
# Convergence Events
# ---------------------------------------------------------------------------

def insert_convergence(
    subject_type: str,
    subject_id: str,
    lens_count: int,
    consensus_score: float,
    severity: str,
    summary: str,
    recommended_action: str | None = None,
) -> str:
    row_id = _new_id("fac")
    conn = get_connection()
    conn.execute(
        "INSERT INTO fa_oracle_convergence_events "
        "(id,subject_type,subject_id,lens_count,consensus_score,severity,summary,recommended_action,created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (row_id, subject_type, subject_id, lens_count, consensus_score,
         severity, summary, recommended_action, _utcnow()),
    )
    conn.commit()
    return row_id


def list_convergence_events(limit: int = 20, conn=None) -> list[dict]:
    conn = conn or get_connection()
    rows = conn.execute(
        "SELECT * FROM fa_oracle_convergence_events WHERE resolved_at IS NULL "
        "ORDER BY created_at DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
