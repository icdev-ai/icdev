#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Migration Cost Estimator."""

import sys
from pathlib import Path
from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"

migration_cost_api = Blueprint("migration_cost_api", __name__, url_prefix="/api/migration-cost")

# ---------------------------------------------------------------------------
# Cost constants
# ---------------------------------------------------------------------------

# Average hourly rates by role (blended rates for federal contracts)
HOURLY_RATES = {
    "developer": 175,
    "security_engineer": 195,
    "cloud_architect": 210,
    "project_manager": 160,
    "compliance_analyst": 165,
}

# Compliance cost multipliers by ATO impact
COMPLIANCE_COST_MULTIPLIER = {
    "none": 1.0,
    "low": 1.15,
    "medium": 1.35,
    "high": 1.60,
    "critical": 2.0,
}

# Strategy base cost multipliers (relative to rehost=1.0)
STRATEGY_COST_FACTOR = {
    "rehost": 1.0,
    "replatform": 1.5,
    "refactor": 2.5,
    "rearchitect": 3.5,
    "repurchase": 0.8,
    "retire": 0.3,
    "retain": 0.1,
}

# Blended hourly rate across all roles
BLENDED_RATE = round(sum(HOURLY_RATES.values()) / len(HOURLY_RATES), 2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _PGCompatConn:
    """Silently pre-translate ? → %s for PG so translate_sql never warns."""
    def __init__(self, conn):
        self._conn = conn
        self._pg = getattr(conn, "_backend", "sqlite") == "postgresql"
    def _fix(self, sql):
        return sql.replace("?", "%s") if self._pg and "?" in sql else sql
    def execute(self, sql, params=()):
        return self._conn.execute(self._fix(sql), params)
    def executemany(self, sql, seq):
        return self._conn.executemany(self._fix(sql), seq)
    def commit(self): return self._conn.commit()
    def rollback(self): return self._conn.rollback()
    def close(self): return self._conn.close()
    def __getattr__(self, name): return getattr(self._conn, name)


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return _PGCompatConn(conn)


def _table_exists(conn, name):
    """Check if a table exists (works for both SQLite and PostgreSQL)."""
    return table_exists(conn, name)


def _calc_migration_cost(hours, strategy, ato_impact):
    """Calculate total migration cost for a single app."""
    strategy_factor = STRATEGY_COST_FACTOR.get(strategy, 1.0)
    compliance_mult = COMPLIANCE_COST_MULTIPLIER.get(ato_impact, 1.0)
    base_cost = hours * BLENDED_RATE
    total = base_cost * strategy_factor * compliance_mult
    return {
        "base_hours": hours,
        "blended_rate": BLENDED_RATE,
        "strategy": strategy,
        "strategy_factor": strategy_factor,
        "ato_impact": ato_impact,
        "compliance_multiplier": compliance_mult,
        "base_cost": round(base_cost, 2),
        "total_cost": round(total, 2),
    }


# ---------------------------------------------------------------------------
# GET /api/migration-cost/estimate
# ---------------------------------------------------------------------------
@migration_cost_api.route("/estimate", methods=["GET"])
def cost_estimate():
    """Full cost estimate for a project — per-app breakdown + total."""
    conn = None
    try:
        conn = _get_db()
        if not _table_exists(conn, "migration_assessments"):
            return jsonify(
                {
                    "apps": [],
                    "total_cost": 0,
                    "total_hours": 0,
                    "app_count": 0,
                    "roi_timeline_weeks": 0,
                }
            )

        project_id = request.args.get("project_id")
        where_sql = ""
        params = []
        if project_id:
            where_sql = " WHERE project_id = ?"
            params = [project_id]

        rows = conn.execute(
            "SELECT id, legacy_app_id, recommended_strategy, "  # nosec B608 -- table/column names are internal constants, not user input
            "cost_estimate_hours, risk_score, timeline_weeks, ato_impact "
            f"FROM migration_assessments{where_sql} "
            "ORDER BY legacy_app_id",
            params,
        ).fetchall()

        apps = []
        total_cost = 0.0
        total_hours = 0
        max_timeline = 0

        for r in rows:
            hours = r[3] or 0
            strategy = r[2] or "rehost"
            ato_impact = r[6] or "none"
            cost_info = _calc_migration_cost(hours, strategy, ato_impact)
            cost_info["id"] = r[0]
            cost_info["legacy_app_id"] = r[1]
            cost_info["risk_score"] = r[4]
            cost_info["timeline_weeks"] = r[5] or 0
            apps.append(cost_info)
            total_cost += cost_info["total_cost"]
            total_hours += hours
            if (r[5] or 0) > max_timeline:
                max_timeline = r[5] or 0

        return jsonify(
            {
                "apps": apps,
                "total_cost": round(total_cost, 2),
                "total_hours": total_hours,
                "app_count": len(apps),
                "roi_timeline_weeks": max_timeline,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# GET /api/migration-cost/comparison
# ---------------------------------------------------------------------------
@migration_cost_api.route("/comparison", methods=["GET"])
def strategy_comparison():
    """Compare all 7R strategies for a single application."""
    conn = None
    try:
        conn = _get_db()
        legacy_app_id = request.args.get("legacy_app_id", "")

        hours = 0
        ato_impact = "none"
        risk_score = 0.0
        timeline_weeks = 0

        if legacy_app_id and _table_exists(conn, "migration_assessments"):
            row = conn.execute(
                "SELECT cost_estimate_hours, ato_impact, risk_score, timeline_weeks "
                "FROM migration_assessments WHERE legacy_app_id = %s "
                "ORDER BY assessed_at DESC LIMIT 1",
                (legacy_app_id,),
            ).fetchone()
            if row:
                hours = row[0] or 0
                ato_impact = row[1] or "none"
                risk_score = row[2] or 0.0
                timeline_weeks = row[3] or 0

        comparisons = []
        for strategy, factor in STRATEGY_COST_FACTOR.items():
            compliance_mult = COMPLIANCE_COST_MULTIPLIER.get(ato_impact, 1.0)
            base_cost = hours * BLENDED_RATE
            total = base_cost * factor * compliance_mult
            # Timeline scales roughly with strategy factor
            est_timeline = round(timeline_weeks * factor, 1) if timeline_weeks else 0
            comparisons.append(
                {
                    "strategy": strategy,
                    "strategy_factor": factor,
                    "compliance_multiplier": compliance_mult,
                    "base_cost": round(base_cost, 2),
                    "total_cost": round(total, 2),
                    "estimated_timeline_weeks": est_timeline,
                    "risk_score": risk_score,
                    "ato_impact": ato_impact,
                }
            )

        return jsonify(
            {
                "legacy_app_id": legacy_app_id,
                "base_hours": hours,
                "blended_rate": BLENDED_RATE,
                "comparisons": comparisons,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# GET /api/migration-cost/portfolio
# ---------------------------------------------------------------------------
@migration_cost_api.route("/portfolio", methods=["GET"])
def portfolio_summary():
    """Portfolio-level cost summary with strategy breakdown."""
    conn = None
    try:
        conn = _get_db()
        result = {
            "total_estimated_cost": 0.0,
            "total_actual_cost": 0.0,
            "variance": 0.0,
            "compliance_overhead": 0.0,
            "total_apps": 0,
            "avg_roi_weeks": 0,
            "strategy_breakdown": {},
        }

        if not _table_exists(conn, "migration_assessments"):
            return jsonify(result)

        rows = conn.execute(
            "SELECT recommended_strategy, cost_estimate_hours, ato_impact, timeline_weeks FROM migration_assessments"
        ).fetchall()

        strategy_totals = {}
        total_est = 0.0
        total_base = 0.0
        total_timeline = 0
        count = 0

        for r in rows:
            strategy = r[0] or "rehost"
            hours = r[1] or 0
            ato_impact = r[2] or "none"
            timeline = r[3] or 0

            cost_info = _calc_migration_cost(hours, strategy, ato_impact)
            total_est += cost_info["total_cost"]
            total_base += cost_info["base_cost"]
            total_timeline += timeline
            count += 1

            if strategy not in strategy_totals:
                strategy_totals[strategy] = {
                    "count": 0,
                    "total_cost": 0.0,
                    "total_hours": 0,
                }
            strategy_totals[strategy]["count"] += 1
            strategy_totals[strategy]["total_cost"] += cost_info["total_cost"]
            strategy_totals[strategy]["total_hours"] += hours

        # Round strategy totals
        for s in strategy_totals:
            strategy_totals[s]["total_cost"] = round(strategy_totals[s]["total_cost"], 2)

        # Actual cost from plans if available
        total_actual = 0.0
        if _table_exists(conn, "migration_plans"):
            row = conn.execute("SELECT COALESCE(SUM(actual_hours), 0) FROM migration_plans").fetchone()
            total_actual = round((row[0] or 0) * BLENDED_RATE, 2)

        compliance_overhead = round(total_est - total_base, 2) if total_est > 0 else 0.0

        result["total_estimated_cost"] = round(total_est, 2)
        result["total_actual_cost"] = total_actual
        result["variance"] = round(total_actual - total_est, 2)
        result["compliance_overhead"] = compliance_overhead
        result["total_apps"] = count
        result["avg_roi_weeks"] = round(total_timeline / count, 1) if count else 0
        result["strategy_breakdown"] = strategy_totals

        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# GET /api/migration-cost/roi
# ---------------------------------------------------------------------------
@migration_cost_api.route("/roi", methods=["GET"])
def roi_projection():
    """ROI projection over N years with breakeven analysis."""
    conn = None
    try:
        conn = _get_db()
        years = min(max(int(request.args.get("years", 5)), 1), 20)

        total_migration_cost = 0.0
        annual_maintenance_savings = 0.0

        if _table_exists(conn, "migration_assessments"):
            project_id = request.args.get("project_id")
            where_sql = ""
            params = []
            if project_id:
                where_sql = " WHERE project_id = ?"
                params = [project_id]

            rows = conn.execute(
                "SELECT recommended_strategy, cost_estimate_hours, ato_impact "  # nosec B608 -- table/column names are internal constants, not user input
                f"FROM migration_assessments{where_sql}",
                params,
            ).fetchall()

            for r in rows:
                strategy = r[0] or "rehost"
                hours = r[1] or 0
                ato_impact = r[2] or "none"
                cost_info = _calc_migration_cost(hours, strategy, ato_impact)
                total_migration_cost += cost_info["total_cost"]
                # Estimated annual savings: ~25% of migration cost per year
                # (reduced maintenance, modernized infrastructure)
                annual_maintenance_savings += cost_info["total_cost"] * 0.25

        annual_maintenance_savings = round(annual_maintenance_savings, 2)
        total_migration_cost = round(total_migration_cost, 2)

        # Year-by-year projection
        projections = []
        cumulative_savings = 0.0
        breakeven_year = None

        for year in range(1, years + 1):
            cumulative_savings += annual_maintenance_savings
            net = cumulative_savings - total_migration_cost
            if net >= 0 and breakeven_year is None:
                breakeven_year = year
            projections.append(
                {
                    "year": year,
                    "cumulative_savings": round(cumulative_savings, 2),
                    "migration_cost": total_migration_cost,
                    "net_value": round(net, 2),
                    "roi_pct": round((net / total_migration_cost) * 100, 1) if total_migration_cost > 0 else 0,
                }
            )

        return jsonify(
            {
                "migration_cost": total_migration_cost,
                "annual_savings": annual_maintenance_savings,
                "breakeven_year": breakeven_year,
                "projection_years": years,
                "projections": projections,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# POST /api/migration-cost/calculate
# ---------------------------------------------------------------------------
@migration_cost_api.route("/calculate", methods=["POST"])
def custom_calculate():
    """Custom cost calculation from user-provided parameters."""
    try:
        data = request.get_json(silent=True) or {}
        hours = max(float(data.get("hours", 0)), 0)
        strategy = data.get("strategy", "rehost")
        ato_impact = data.get("ato_impact", "none")
        team_size = max(int(data.get("team_size", 1)), 1)

        if strategy not in STRATEGY_COST_FACTOR:
            return jsonify({"error": f"Invalid strategy: {strategy}"}), 400
        if ato_impact not in COMPLIANCE_COST_MULTIPLIER:
            return jsonify({"error": f"Invalid ato_impact: {ato_impact}"}), 400

        strategy_factor = STRATEGY_COST_FACTOR[strategy]
        compliance_mult = COMPLIANCE_COST_MULTIPLIER[ato_impact]

        # Itemized breakdown by role
        role_breakdown = {}
        # Distribute hours across roles proportionally
        role_pcts = {
            "developer": 0.40,
            "security_engineer": 0.15,
            "cloud_architect": 0.15,
            "project_manager": 0.15,
            "compliance_analyst": 0.15,
        }

        total_base = 0.0
        for role, pct in role_pcts.items():
            role_hours = round(hours * pct, 1)
            rate = HOURLY_RATES[role]
            role_cost = role_hours * rate * strategy_factor * compliance_mult
            role_breakdown[role] = {
                "hours": role_hours,
                "rate": rate,
                "cost": round(role_cost, 2),
            }
            total_base += role_cost

        total_cost = round(total_base, 2)
        base_cost_no_mult = round(hours * BLENDED_RATE, 2)
        compliance_overhead = round(total_cost - (hours * BLENDED_RATE * strategy_factor), 2)
        strategy_overhead = round((hours * BLENDED_RATE * strategy_factor) - base_cost_no_mult, 2)

        # Timeline estimate (weeks) based on hours and team size
        working_hours_per_week = 40
        timeline_weeks = round(hours / (team_size * working_hours_per_week), 1)

        return jsonify(
            {
                "input": {
                    "hours": hours,
                    "strategy": strategy,
                    "ato_impact": ato_impact,
                    "team_size": team_size,
                },
                "breakdown": {
                    "role_costs": role_breakdown,
                    "blended_rate": BLENDED_RATE,
                    "strategy_factor": strategy_factor,
                    "compliance_multiplier": compliance_mult,
                    "base_cost": base_cost_no_mult,
                    "strategy_overhead": strategy_overhead,
                    "compliance_overhead": compliance_overhead,
                    "total_cost": total_cost,
                },
                "timeline_weeks": timeline_weeks,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
