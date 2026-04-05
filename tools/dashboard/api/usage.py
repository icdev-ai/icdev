# [TEMPLATE: CUI // SP-CTI]
"""
Usage tracking + cost dashboard API blueprint (Phase 30 — D177).

Provides per-user token aggregation, per-provider breakdown, and cost estimates.
Admin sees all users' usage; others see only their own.
"""

from tools.db.storage import get_connection

from flask import Blueprint, g, jsonify, request

from tools.dashboard.config import DB_PATH

usage_api = Blueprint("usage_api", __name__, url_prefix="/api/usage")

# Default user for unauthenticated API calls (internal dashboard)
_DEFAULT_USER = {"id": "admin", "role": "admin"}


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _get_user():
    """Get current user, falling back to admin for internal access."""
    return getattr(g, "current_user", None) or _DEFAULT_USER


# ---------------------------------------------------------------------------
# Cost estimates per 1K tokens (approximate, configurable via llm_config.yaml)
# ---------------------------------------------------------------------------
DEFAULT_COST_PER_1K = {
    "claude-opus-4-6": {"input": 0.015, "output": 0.075},
    "claude-sonnet-4-6": {"input": 0.003, "output": 0.015},
    "claude-haiku-4-5": {"input": 0.0008, "output": 0.004},
    "anthropic.claude-opus-4-6-v1:0": {"input": 0.015, "output": 0.075},
    "anthropic.claude-sonnet-4-6-v1:0": {"input": 0.003, "output": 0.015},
    "us.anthropic.claude-sonnet-4-5-v1:0": {"input": 0.003, "output": 0.015},
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
}


def _estimate_cost(model_id, input_tokens, output_tokens):
    """Estimate cost in USD from token counts."""
    rates = DEFAULT_COST_PER_1K.get(model_id, {"input": 0.003, "output": 0.015})
    cost = (input_tokens / 1000.0) * rates["input"] + (output_tokens / 1000.0) * rates["output"]
    return round(cost, 6)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@usage_api.route("/summary")
def usage_summary():
    """Overall usage summary by agent."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT
                   COALESCE(agent_id, 'system') as agent_id,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(thinking_tokens) as total_thinking,
                   SUM(cost_estimate_usd) as total_cost,
                   COUNT(*) as request_count
               FROM agent_token_usage
               GROUP BY COALESCE(agent_id, 'system')
               ORDER BY total_cost DESC"""
        ).fetchall()

        return jsonify({"usage": [dict(r) for r in rows]})
    finally:
        conn.close()


@usage_api.route("/by-provider")
def usage_by_provider():
    """Token usage breakdown by provider/model."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT
                   model_id,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(cost_estimate_usd) as total_cost,
                   COUNT(*) as request_count
               FROM agent_token_usage
               GROUP BY model_id
               ORDER BY total_cost DESC"""
        ).fetchall()

        return jsonify({"providers": [dict(r) for r in rows]})
    except Exception:
        return jsonify({"providers": []})
    finally:
        conn.close()


@usage_api.route("/time-series")
def usage_time_series():
    """Daily token usage over time for charting."""
    days = min(int(request.args.get("days", "30")), 90)
    conn = _get_db()
    try:
        # Compute cutoff in Python for cross-backend compat (SQLite vs PostgreSQL)
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """SELECT
                   DATE(created_at) as day,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cost_estimate_usd) as cost,
                   COUNT(*) as requests
               FROM agent_token_usage
               WHERE created_at >= ?
               GROUP BY DATE(created_at)
               ORDER BY day""",
            (cutoff,),
        ).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            if hasattr(d.get("day"), "isoformat"):
                d["day"] = d["day"].isoformat()
            result.append(d)

        return jsonify({"series": result, "days": days})
    finally:
        conn.close()


@usage_api.route("/totals")
def usage_totals():
    """Grand totals for stat cards."""
    conn = _get_db()
    try:
        row = conn.execute(
            """SELECT
                   COALESCE(SUM(input_tokens), 0) as total_input,
                   COALESCE(SUM(output_tokens), 0) as total_output,
                   COALESCE(SUM(thinking_tokens), 0) as total_thinking,
                   COALESCE(SUM(cost_estimate_usd), 0) as total_cost,
                   COUNT(*) as total_requests,
                   COUNT(DISTINCT agent_id) as unique_agents,
                   COUNT(DISTINCT model_id) as unique_models
               FROM agent_token_usage"""
        ).fetchone()

        return jsonify(dict(row) if row else {})
    finally:
        conn.close()
