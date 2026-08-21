#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Compliance Debt Burndown."""

import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists, sql_placeholder  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"

compliance_debt_api = Blueprint("compliance_debt_api", __name__, url_prefix="/api/compliance-debt")

# ── Severity weights ──────────────────────────────────────────
POAM_WEIGHTS = {"critical": 10, "high": 5, "moderate": 2, "low": 1}
STIG_WEIGHTS = {"CAT1": 15, "CAT2": 5, "CAT3": 1}
CONTROL_WEIGHT = 3

# SLA days by POAM severity
SLA_DAYS = {"critical": 30, "high": 90, "moderate": 180, "low": 365}


def _is_pg(conn):
    """Check if connection is PostgreSQL."""
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists (works for both SQLite and PostgreSQL)."""
    return table_exists(conn, table_name)


# ── 1. Summary ────────────────────────────────────────────────


@compliance_debt_api.route("/summary", methods=["GET"])
def debt_summary():
    """Overall compliance debt score, optionally filtered by project_id."""
    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        project_id = request.args.get("project_id")

        # --- POAM debt ---
        poam_debt = 0
        if _table_exists(conn, "poam_items"):
            q = "SELECT severity, COUNT(*) AS cnt FROM poam_items WHERE status NOT IN ('completed','accepted_risk')"
            params = []
            if project_id:
                q += f" AND project_id = {ph}"
                params.append(project_id)
            q += " GROUP BY severity"
            for row in conn.execute(q, params).fetchall():
                sev = (row["severity"] or "low").lower()
                poam_debt += row["cnt"] * POAM_WEIGHTS.get(sev, 1)

        # --- Control debt ---
        control_debt = 0
        if _table_exists(conn, "project_controls"):
            q = (
                "SELECT COUNT(*) AS cnt FROM project_controls "
                "WHERE implementation_status NOT IN ('implemented','not_applicable')"
            )
            params = []
            if project_id:
                q += f" AND project_id = {ph}"
                params.append(project_id)
            row = conn.execute(q, params).fetchone()
            control_debt = (row["cnt"] if row else 0) * CONTROL_WEIGHT

        # --- STIG debt ---
        stig_debt = 0
        if _table_exists(conn, "stig_findings"):
            q = "SELECT severity, COUNT(*) AS cnt FROM stig_findings WHERE status = 'Open'"
            params = []
            if project_id:
                q += f" AND project_id = {ph}"
                params.append(project_id)
            q += " GROUP BY severity"
            for row in conn.execute(q, params).fetchall():
                sev = row["severity"] or "CAT3"
                stig_debt += row["cnt"] * STIG_WEIGHTS.get(sev, 1)

        # --- ATO risk ---
        ato_debt = 0
        if _table_exists(conn, "cssp_certifications"):
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if _is_pg(conn):
                date_expr = "(CURRENT_DATE + INTERVAL '90 days')"
                q = (
                    "SELECT COUNT(*) AS cnt FROM cssp_certifications "  # nosec B608 -- table/column names are internal constants, not user input
                    f"WHERE expiration_date IS NOT NULL AND expiration_date::date <= {date_expr}"
                )
                params = []
            else:
                q = (
                    "SELECT COUNT(*) AS cnt FROM cssp_certifications "
                    "WHERE expiration_date IS NOT NULL AND expiration_date <= date(?, '+90 days')"
                )
                params = [today]
            if project_id:
                q += f" AND project_id = {ph}"
                params.append(project_id)
            row = conn.execute(q, params).fetchone()
            ato_debt = (row["cnt"] if row else 0) * 10

        total = poam_debt + control_debt + stig_debt + ato_debt

        # Simple trend: compare open POAMs created in last 30 days vs completed
        trend = "stable"
        if _table_exists(conn, "poam_items"):
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            base_filter = ""
            if _is_pg(conn):
                date_30_expr = "(CURRENT_DATE - INTERVAL '30 days')"
                t_params_opened = []
                t_params_closed = []
            else:
                date_30_expr = "date(?, '-30 days')"
                t_params_opened = [today]
                t_params_closed = [today]
            if project_id:
                base_filter = f" AND project_id = {ph}"
                t_params_opened.append(project_id)
                t_params_closed.append(project_id)

            opened = conn.execute(
                "SELECT COUNT(*) AS cnt FROM poam_items "  # nosec B608 -- table/column names are internal constants, not user input
                f"WHERE created_at >= {date_30_expr}" + base_filter,
                t_params_opened,
            ).fetchone()
            closed = conn.execute(
                "SELECT COUNT(*) AS cnt FROM poam_items "  # nosec B608 -- table/column names are internal constants, not user input
                f"WHERE completion_date >= {date_30_expr}" + base_filter,
                t_params_closed,
            ).fetchone()
            o = opened["cnt"] if opened else 0
            c = closed["cnt"] if closed else 0
            if c > o:
                trend = "improving"
            elif o > c:
                trend = "worsening"

        return jsonify(
            {
                "total_debt_points": total,
                "debt_by_category": {
                    "poam": poam_debt,
                    "controls": control_debt,
                    "stig": stig_debt,
                    "ato": ato_debt,
                },
                "debt_trend": trend,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── 2. Burndown ───────────────────────────────────────────────


@compliance_debt_api.route("/burndown", methods=["GET"])
def debt_burndown():
    """Monthly burndown chart data for POAMs."""
    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        project_id = request.args.get("project_id")
        months = int(request.args.get("months", 6))

        if not _table_exists(conn, "poam_items"):
            return jsonify({"months": [], "projected": []})

        today = datetime.now(timezone.utc)
        base_filter = ""
        params_base = []
        if project_id:
            base_filter = f" AND project_id = {ph}"
            params_base = [project_id]

        # Opened per month
        if _is_pg(conn):
            month_expr = "to_char(created_at::timestamp, 'YYYY-MM')"
            month_expr_comp = "to_char(completion_date::timestamp, 'YYYY-MM')"
            date_window = f"(CURRENT_DATE - INTERVAL '{months} months')"
            window_params_opened = [] + params_base
            window_params_closed = [] + params_base
            window_params_start = [] + params_base
        else:
            month_expr = "strftime('%%Y-%%m', created_at)"
            month_expr_comp = "strftime('%%Y-%%m', completion_date)"
            date_window = "date(?, '-' || ? || ' months')"
            window_params_opened = [today.strftime("%Y-%m-%d"), str(months)] + params_base
            window_params_closed = [today.strftime("%Y-%m-%d"), str(months)] + params_base
            window_params_start = [today.strftime("%Y-%m-%d"), str(months)] + params_base

        q_opened = (
            f"SELECT {month_expr} AS month, COUNT(*) AS cnt "  # nosec B608 -- table/column names are internal constants, not user input
            f"FROM poam_items WHERE created_at >= {date_window}" + base_filter + " GROUP BY month ORDER BY month"
        )
        opened_rows = conn.execute(q_opened, window_params_opened).fetchall()
        opened_map = {r["month"]: r["cnt"] for r in opened_rows}

        # Closed per month
        q_closed = (
            f"SELECT {month_expr_comp} AS month, COUNT(*) AS cnt "  # nosec B608 -- table/column names are internal constants, not user input
            f"FROM poam_items WHERE completion_date >= {date_window}" + base_filter + " GROUP BY month ORDER BY month"
        )
        closed_rows = conn.execute(q_closed, window_params_closed).fetchall()
        closed_map = {r["month"]: r["cnt"] for r in closed_rows}

        # Total open at start of window
        q_start = (
            "SELECT COUNT(*) AS cnt FROM poam_items "  # nosec B608 -- table/column names are internal constants, not user input
            "WHERE status NOT IN ('completed','accepted_risk') "
            f"AND created_at < {date_window}" + base_filter
        )
        start_row = conn.execute(q_start, window_params_start).fetchone()
        cumulative = start_row["cnt"] if start_row else 0

        # Build month list
        result_months = []
        all_months = sorted(set(list(opened_map.keys()) + list(closed_map.keys())))
        for m in all_months:
            o = opened_map.get(m, 0)
            c = closed_map.get(m, 0)
            cumulative += o - c
            result_months.append(
                {
                    "month": m,
                    "opened": o,
                    "closed": c,
                    "cumulative_open": max(cumulative, 0),
                }
            )

        # Project future months based on avg close rate
        total_closed = sum(closed_map.values()) if closed_map else 0
        total_opened = sum(opened_map.values()) if opened_map else 0
        num_months = max(len(all_months), 1)
        avg_net = (total_closed - total_opened) / num_months  # positive = burning down

        projected = []
        proj_cum = cumulative
        for i in range(1, 7):
            y = today.year + (today.month + i - 1) // 12
            mo = (today.month + i - 1) % 12 + 1
            proj_cum = max(proj_cum - avg_net, 0)
            projected.append(
                {
                    "month": f"{y:04d}-{mo:02d}",
                    "projected_open": round(proj_cum),
                }
            )

        return jsonify({"months": result_months, "projected": projected})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── 3. POAMs ──────────────────────────────────────────────────


@compliance_debt_api.route("/poams", methods=["GET"])
def debt_poams():
    """POAM breakdown with filters and pagination."""
    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        if not _table_exists(conn, "poam_items"):
            return jsonify({"poam_items": [], "total": 0, "page": 1, "per_page": 25})

        project_id = request.args.get("project_id")
        severity = request.args.get("severity")
        status = request.args.get("status")
        overdue = request.args.get("overdue")
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 25))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if _is_pg(conn):
            days_overdue_expr = (
                "CASE WHEN milestone_date IS NOT NULL AND milestone_date < CURRENT_DATE "
                "AND status NOT IN ('completed','accepted_risk') "
                "THEN CAST(EXTRACT(EPOCH FROM "
                "(NOW() - milestone_date::timestamp)) / 86400 AS INTEGER) "
                "ELSE 0 END"
            )
            q = f"SELECT *, {days_overdue_expr} AS days_overdue FROM poam_items WHERE 1=1"  # nosec B608 -- table/column names are internal constants, not user input
            params = []
        else:
            q = "SELECT *, "
            q += (
                "CASE WHEN milestone_date IS NOT NULL AND milestone_date < ? "
                "AND status NOT IN ('completed','accepted_risk') "
                "THEN CAST(julianday(?) - julianday(milestone_date) AS INTEGER) "
                "ELSE 0 END AS days_overdue "
                "FROM poam_items WHERE 1=1"
            )
            params = [today, today]

        if project_id:
            q += f" AND project_id = {ph}"
            params.append(project_id)
        if severity:
            q += f" AND severity = {ph}"
            params.append(severity)
        if status:
            q += f" AND status = {ph}"
            params.append(status)
        if overdue and overdue.lower() in ("true", "1", "yes"):
            q += (
                f" AND milestone_date IS NOT NULL AND milestone_date < {ph} AND status NOT IN ('completed','accepted_risk')"
            )
            params.append(today)

        # Count total
        count_q = "SELECT COUNT(*) AS cnt FROM (" + q + ")"  # nosec B608 -- table/column names are internal constants, not user input
        total = conn.execute(count_q, params).fetchone()["cnt"]

        # Sort and paginate
        q += " ORDER BY days_overdue DESC, severity, created_at DESC"
        q += f" LIMIT {ph} OFFSET {ph}"
        params.extend([per_page, (page - 1) * per_page])

        rows = conn.execute(q, params).fetchall()

        return jsonify(
            {
                "poam_items": [dict(r) for r in rows],
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── 4. Controls ───────────────────────────────────────────────


@compliance_debt_api.route("/controls", methods=["GET"])
def debt_controls():
    """Unassessed/unimplemented controls grouped by family."""
    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        if not _table_exists(conn, "project_controls"):
            return jsonify({"families": [], "total_needing_attention": 0})

        project_id = request.args.get("project_id")

        # Join with compliance_controls if available for family info
        has_cc = _table_exists(conn, "compliance_controls")

        if has_cc:
            q = (
                "SELECT pc.control_id, pc.implementation_status, pc.last_assessed, "
                "COALESCE(cc.family, SUBSTR(pc.control_id, 1, 2)) AS family, "
                "COALESCE(cc.title, pc.control_id) AS title "
                "FROM project_controls pc "
                "LEFT JOIN compliance_controls cc ON pc.control_id = cc.id "
                "WHERE 1=1"
            )
        else:
            q = (
                "SELECT control_id, implementation_status, last_assessed, "
                "SUBSTR(control_id, 1, 2) AS family, control_id AS title "
                "FROM project_controls WHERE 1=1"
            )

        params = []
        if project_id:
            q += f" AND pc.project_id = {ph}" if has_cc else f" AND project_id = {ph}"
            params.append(project_id)

        rows = conn.execute(q, params).fetchall()

        # Group by family
        families = {}
        for r in rows:
            fam = r["family"]
            if fam not in families:
                families[fam] = {
                    "family": fam,
                    "implemented": 0,
                    "partially_implemented": 0,
                    "planned": 0,
                    "compensating": 0,
                    "not_applicable": 0,
                    "total": 0,
                    "controls": [],
                }
            families[fam]["total"] += 1
            st = r["implementation_status"] or "planned"
            if st in families[fam]:
                families[fam][st] += 1
            # Include controls needing attention
            if st not in ("implemented", "not_applicable"):
                families[fam]["controls"].append(
                    {
                        "control_id": r["control_id"],
                        "title": r["title"],
                        "status": st,
                        "last_assessed": r["last_assessed"],
                    }
                )

        family_list = sorted(families.values(), key=lambda f: f["family"])
        needing = sum(f["planned"] + f["partially_implemented"] + f["compensating"] for f in family_list)

        return jsonify(
            {
                "families": family_list,
                "total_needing_attention": needing,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── 5. Expiring ATOs ─────────────────────────────────────────


@compliance_debt_api.route("/expiring-atos", methods=["GET"])
def expiring_atos():
    """ATOs expiring within N days."""
    conn = _get_db()
    try:
        if not _table_exists(conn, "cssp_certifications"):
            return jsonify({"expiring": [], "total": 0})

        days = int(request.args.get("days", 90))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if _is_pg(conn):
            days_expr = "CAST(EXTRACT(EPOCH FROM (expiration_date::timestamp - NOW())) / 86400 AS INTEGER)"
            date_upper = f"(CURRENT_DATE + INTERVAL '{days} days')"
            q = (
                f"SELECT *, {days_expr} AS days_remaining "  # nosec B608 -- table/column names are internal constants, not user input
                "FROM cssp_certifications "
                "WHERE expiration_date IS NOT NULL "
                f"AND expiration_date::date >= CURRENT_DATE "
                f"AND expiration_date::date <= {date_upper} "
                "ORDER BY expiration_date ASC"
            )
            rows = conn.execute(q, []).fetchall()
        else:
            q = (
                "SELECT *, "
                "CAST(julianday(expiration_date) - julianday(?) AS INTEGER) AS days_remaining "
                "FROM cssp_certifications "
                "WHERE expiration_date IS NOT NULL "
                "AND expiration_date >= ? "
                "AND expiration_date <= date(?, '+' || ? || ' days') "
                "ORDER BY expiration_date ASC"
            )
            rows = conn.execute(q, [today, today, today, str(days)]).fetchall()

        return jsonify(
            {
                "expiring": [dict(r) for r in rows],
                "total": len(rows),
                "window_days": days,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── 6. SLA Compliance ────────────────────────────────────────


@compliance_debt_api.route("/sla", methods=["GET"])
def sla_compliance():
    """SLA compliance for POAM remediation by severity."""
    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        if not _table_exists(conn, "poam_items"):
            return jsonify({"sla": []})

        project_id = request.args.get("project_id")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        results = []
        for sev, max_days in SLA_DAYS.items():
            base_filter = ""
            params = [today, str(max_days)]
            if project_id:
                base_filter = f" AND project_id = {ph}"
                params.append(project_id)

            # Completed POAMs for this severity
            proj_params = params[2:] if project_id else []
            completed_q = (
                "SELECT COUNT(*) AS cnt FROM poam_items "  # nosec B608 -- table/column names are internal constants, not user input
                f"WHERE severity = {ph} AND status = 'completed'" + base_filter
            )
            if _is_pg(conn):
                days_diff_expr = (
                    "CAST(EXTRACT(EPOCH FROM (completion_date::timestamp - created_at::timestamp)) / 86400 AS INTEGER)"
                )
                within_sla_q = (
                    "SELECT COUNT(*) AS cnt FROM poam_items "  # nosec B608 -- table/column names are internal constants, not user input
                    f"WHERE severity = {ph} AND status = 'completed' "
                    "AND completion_date IS NOT NULL AND created_at IS NOT NULL "
                    f"AND {days_diff_expr} <= {ph}" + base_filter
                )
                open_days_expr = "CAST(EXTRACT(EPOCH FROM (NOW() - created_at::timestamp)) / 86400 AS INTEGER)"
                open_past_q = (
                    "SELECT COUNT(*) AS cnt FROM poam_items "  # nosec B608 -- table/column names are internal constants, not user input
                    f"WHERE severity = {ph} AND status NOT IN ('completed','accepted_risk') "
                    "AND created_at IS NOT NULL "
                    f"AND {open_days_expr} > {ph}" + base_filter
                )
            else:
                within_sla_q = (
                    "SELECT COUNT(*) AS cnt FROM poam_items "  # nosec B608 -- table/column names are internal constants, not user input
                    f"WHERE severity = {ph} AND status = 'completed' "
                    "AND completion_date IS NOT NULL AND created_at IS NOT NULL "
                    f"AND CAST(julianday(completion_date) - julianday(created_at) AS INTEGER) <= {ph}" + base_filter
                )
                open_past_q = (
                    "SELECT COUNT(*) AS cnt FROM poam_items "  # nosec B608 -- table/column names are internal constants, not user input
                    f"WHERE severity = {ph} AND status NOT IN ('completed','accepted_risk') "
                    "AND created_at IS NOT NULL "
                    f"AND CAST(julianday({ph}) - julianday(created_at) AS INTEGER) > {ph}" + base_filter
                )

            total_completed = conn.execute(completed_q, [sev] + proj_params).fetchone()["cnt"]
            within = conn.execute(within_sla_q, [sev, max_days] + proj_params).fetchone()["cnt"]

            # Also count open ones that are already past SLA
            if _is_pg(conn):
                open_past = conn.execute(open_past_q, [sev, max_days] + proj_params).fetchone()["cnt"]
            else:
                open_past = conn.execute(open_past_q, [sev, today, max_days] + proj_params).fetchone()["cnt"]

            total_relevant = total_completed + open_past
            # NOT ASSESSED, never 100.0 (rem-hyg-13). `total_relevant` is the
            # number of POA&M items of this severity that a deadline can be
            # judged against. Zero of them means nobody has filed one — which
            # is a statement about the BOARD, not about remediation speed — and
            # the old `else 100.0` drew a full green SLA bar for a severity
            # nothing had ever been tracked at.
            pct = round(within / total_relevant * 100, 1) if total_relevant > 0 else None

            results.append(
                {
                    "severity": sev,
                    "sla_days": max_days,
                    "total_completed": total_completed,
                    "within_sla": within,
                    "open_past_sla": open_past,
                    "compliance_pct": pct,
                }
            )

        return jsonify({"sla": results})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()
