# CUI // SP-CTI
"""Oracle API — anticipatory intelligence predictions and remediation history."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

oracle_api = Blueprint("oracle_api", __name__)

# Display threshold for the "high-confidence pending" home-widget tile. This is a
# DISPLAY notion — "how many notably confident predictions are still queued" —
# and is deliberately NOT the promotion gate (awareness_config.yaml
# promotion_threshold: 0.7). The two answer different questions: the gate decides
# what becomes a task; this tile highlights the strongest queued signals. They
# were previously the same magic number in two places with no link between them,
# which invited "fixing" one to match the other. Named and separated so that
# choice is explicit. (Confidence in the awareness lens is a per-rule constant,
# so this counts rules at/above 0.85, not a calibrated probability.)
_HIGH_CONF_DISPLAY_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# Predictions
# ---------------------------------------------------------------------------
@oracle_api.route("/api/oracle/predictions", methods=["GET"])
def list_predictions():
    """List Oracle predictions."""
    try:
        from tools.oracle.lenses.lens_quality import QualityLens

        lens = QualityLens()
        predictions = lens.run()
        return jsonify({"predictions": [p.to_dict() for p in predictions], "count": len(predictions)})
    except Exception as exc:
        return jsonify({"predictions": [], "count": 0, "error": str(exc)})


@oracle_api.route("/api/oracle/summary", methods=["GET"])
def oracle_summary():
    """Aggregate stats for the home-dashboard Oracle Insights widget.

    Returns totals over the last 24h, high-confidence pending count,
    unresolved convergence events, median horizon (days), and a severity
    histogram. Powers the 4-tile + severity-bar widget on index.html.
    """
    out: dict = {
        "total_24h": 0,
        "high_conf_pending": 0,
        "unresolved_convergence": 0,
        "median_horizon_days": None,
        "severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
    }
    try:
        cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        cutoff_30d = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        with get_connection() as conn:
            # Oracle summary aggregates counts — not raw rows — so RLS
            # classification filtering would exclude CTI-marked predictions
            # from admins who hold plain CUI clearance.  Clear the security
            # context so inject_row_predicate doesn't add a classification
            # WHERE clause that silently zeroes out the stats.
            conn.set_security_context(None)  # rls-bypass: cross-tenant aggregate stats query; tenant filter would zero out results
            r = conn.execute(
                "SELECT COUNT(*) AS n FROM oracle_predictions WHERE created_at >= %s",
                (cutoff_24h,),
            ).fetchone()
            out["total_24h"] = int((r.get("n", 0) if r else 0) or 0)

            r = conn.execute(
                "SELECT COUNT(*) AS n FROM oracle_predictions "
                "WHERE confidence >= %s AND (outcome IS NULL OR outcome = 'pending')",
                (_HIGH_CONF_DISPLAY_THRESHOLD,),
            ).fetchone()
            out["high_conf_pending"] = int((r.get("n", 0) if r else 0) or 0)

            for row in conn.execute(
                "SELECT severity, COUNT(*) AS n FROM oracle_predictions "
                "WHERE created_at >= %s GROUP BY severity",
                (cutoff_24h,),
            ).fetchall():
                sev = (row.get("severity") or "").lower()
                if sev in out["severity"]:
                    out["severity"][sev] = int((row.get("n", 0) or 0))

            r = conn.execute(
                "SELECT COUNT(*) AS n FROM oracle_predictions "
                "WHERE (outcome IS NULL OR outcome = 'pending') "
                "AND scoring_weights::text ILIKE %s",
                ("%convergence%",),
            ).fetchone()
            out["unresolved_convergence"] = int((r.get("n", 0) if r else 0) or 0)

            r = conn.execute(
                "SELECT AVG(horizon_days) AS avg_h FROM oracle_predictions "
                "WHERE horizon_days IS NOT NULL AND created_at >= %s",
                (cutoff_30d,),
            ).fetchone()
            avg_h = r.get("avg_h") if r else None
            if avg_h is not None:
                out["median_horizon_days"] = round(float(avg_h), 1)
    except Exception as exc:
        out["error"] = str(exc)
    return jsonify(out)


# ---------------------------------------------------------------------------
# Shared: parse audit_trail rows into proposal/history items
# ---------------------------------------------------------------------------
_REMEDIATION_ACTION_PATTERNS = (
    "poam.%remediat%",
    "canvas.%remediat%",
    "poam.close%",
    "kanban.%",  # bulk-close + ship events
)


def _parse_details(raw: Any) -> dict:
    """Audit_trail.details is TEXT, often double-JSON-encoded. Return a dict
    regardless of input shape (empty dict on failure)."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    # Double-encoded: outer json.loads returned another JSON string
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except Exception:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _row_to_item(row: dict) -> dict:
    """Map an audit_trail row to the UI item shape expected by index.html."""
    details = _parse_details(row.get("details"))
    event_type = row.get("event_type") or ""
    action = row.get("action") or ""
    verified = bool(details.get("verified_gone"))

    if event_type == "vulnerability_resolved":
        status = "approved"
    elif event_type == "decision_made" and action.startswith("poam."):
        status = "pending"
    else:
        status = "approved" if verified else "pending"

    rule_id = details.get("rule_id") or details.get("task_id") or action
    diff = details.get("diff") or details.get("decision") or ""
    first_line = str(diff).splitlines()[0] if diff else ""
    title = f"{rule_id}: {first_line}"[:160] if first_line else str(rule_id)[:160]

    canvas = (details.get("canvas") or "").lower()
    current_score = details.get("old_avg_score")
    projected_score = details.get("projected_score")
    new_score = details.get("new_avg_score")

    created = row.get("created_at")
    created_iso = created.isoformat() if hasattr(created, "isoformat") else str(created or "")

    item = {
        "id": str(row.get("id") or ""),
        "status": status,
        "title": title or f"{canvas or 'audit'} event",
        "canvas": canvas,
        "canvas_label": canvas,
        "created_at": created_iso,
        "action": action,
        "actor": row.get("actor") or "",
    }
    if isinstance(current_score, (int, float)):
        item["current_score"] = float(current_score)
    if isinstance(projected_score, (int, float)):
        item["projected_score"] = float(projected_score)
    if isinstance(new_score, (int, float)) and status == "approved":
        item["actual_new_score"] = float(new_score)
    return item


def _fetch_remediation_rows(limit: int, canvas: str | None, since_iso: str | None) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    like_clause = " OR ".join(["action LIKE %s"] * len(_REMEDIATION_ACTION_PATTERNS))
    sql = f"""
        SELECT id, event_type, actor, action, details, created_at
        FROM audit_trail
        WHERE (event_type = 'vulnerability_resolved'
               OR (event_type = 'decision_made' AND action LIKE 'poam.%%')
               OR {like_clause})
          AND created_at >= %s
        ORDER BY created_at DESC, id DESC
        LIMIT %s
    """  # nosec B608 — only literal %s placeholders; values bound via params
    since_dt = _parse_since(since_iso)
    params: tuple = tuple(_REMEDIATION_ACTION_PATTERNS) + (since_dt, limit * 4 if canvas else limit)
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    items = [_row_to_item(r) for r in rows]
    if canvas:
        items = [it for it in items if it.get("canvas") == canvas.lower()]
    return items[:limit]


def _parse_since(since_iso: str | None) -> datetime:
    """Accept ISO date/time. Default: 30 days ago."""
    if since_iso:
        try:
            return datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
        except Exception:
            pass
    return datetime.now(timezone.utc) - timedelta(days=30)


# ---------------------------------------------------------------------------
# /api/oracle/proposals/history
# ---------------------------------------------------------------------------
@oracle_api.route("/api/oracle/proposals/history", methods=["GET"])
def oracle_proposals_history():
    """Remediation history — reads from audit_trail.

    Query params:
      limit  (int, default 10, cap 100)
      canvas (str, optional — case-insensitive match on details.canvas)
      since  (ISO date, default: 30 days ago)
    """
    try:
        limit = max(1, min(int(request.args.get("limit", 10)), 100))
    except (TypeError, ValueError):
        limit = 10
    canvas = request.args.get("canvas")
    since_iso = request.args.get("since")

    try:
        items = _fetch_remediation_rows(limit, canvas, since_iso)
        return jsonify({"history": items, "total": len(items)})
    except Exception as exc:
        return jsonify({"history": [], "total": 0, "error": str(exc)})


# ---------------------------------------------------------------------------
# /api/oracle/proposals/stats
# ---------------------------------------------------------------------------
@oracle_api.route("/api/oracle/proposals/stats", methods=["GET"])
def oracle_proposals_stats():
    """Counts of proposal statuses derived from audit_trail."""
    try:
        items = _fetch_remediation_rows(limit=1000, canvas=None, since_iso=None)
        counts = {"total": len(items), "pending": 0, "approved": 0, "rejected": 0, "failed": 0}
        for it in items:
            s = it.get("status", "")
            if s in counts:
                counts[s] += 1
        return jsonify(counts)
    except Exception as exc:
        return jsonify({"total": 0, "pending": 0, "approved": 0, "rejected": 0, "failed": 0, "error": str(exc)})


# ---------------------------------------------------------------------------
# /api/oracle/proposals/unified
# ---------------------------------------------------------------------------
@oracle_api.route("/api/oracle/proposals/unified", methods=["GET"])
def oracle_proposals_unified():
    """Unified recent-proposals view — same source as /history with a different
    envelope. TODO: extend with oracle_predictions join when lens pipeline
    produces proposal-ready artifacts."""
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 200))
    except (TypeError, ValueError):
        limit = 20
    try:
        items = _fetch_remediation_rows(limit, None, None)
        return jsonify({"proposals": items, "total": len(items)})
    except Exception as exc:
        return jsonify({"proposals": [], "total": 0, "error": str(exc)})
