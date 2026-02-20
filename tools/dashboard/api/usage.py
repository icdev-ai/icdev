# CUI // SP-CTI
"""
Usage tracking + cost dashboard API blueprint (Phase 30 — D177).

Provides per-user token aggregation, per-provider breakdown, and cost estimates.
Admin sees all users' usage; others see only their own.
"""

import sqlite3

from flask import Blueprint, g, jsonify, render_template, request

from tools.dashboard.config import DB_PATH

usage_api = Blueprint("usage_api", __name__, url_prefix="/api/usage")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
    """Overall usage summary for the current user (or all users for admin)."""
    user = getattr(g, "current_user", None)
    if not user:
        return jsonify({"error": "Not authenticated"}), 401

    conn = _get_db()
    try:
        is_admin = user.get("role") == "admin"
        view_user_id = request.args.get("user_id")

        # Admin can view any user or all users
        if is_admin and not view_user_id:
            # All users
            rows = conn.execute(
                """SELECT
                       COALESCE(user_id, 'system') as user_id,
                       SUM(input_tokens) as total_input,
                       SUM(output_tokens) as total_output,
                       SUM(thinking_tokens) as total_thinking,
                       SUM(cost_estimate_usd) as total_cost,
                       COUNT(*) as request_count
                   FROM agent_token_usage
                   GROUP BY COALESCE(user_id, 'system')
                   ORDER BY total_cost DESC"""
            ).fetchall()
        else:
            # Single user
            target_id = view_user_id if (is_admin and view_user_id) else user["id"]
            rows = conn.execute(
                """SELECT
                       COALESCE(user_id, 'system') as user_id,
                       SUM(input_tokens) as total_input,
                       SUM(output_tokens) as total_output,
                       SUM(thinking_tokens) as total_thinking,
                       SUM(cost_estimate_usd) as total_cost,
                       COUNT(*) as request_count
                   FROM agent_token_usage
                   WHERE user_id = ? OR (user_id IS NULL AND ? = 'system')
                   GROUP BY COALESCE(user_id, 'system')""",
                (target_id, target_id),
            ).fetchall()

        return jsonify({"usage": [dict(r) for r in rows]})
    finally:
        conn.close()


@usage_api.route("/by-provider")
def usage_by_provider():
    """Token usage breakdown by provider/model."""
    user = getattr(g, "current_user", None)
    if not user:
        return jsonify({"error": "Not authenticated"}), 401

    conn = _get_db()
    try:
        is_admin = user.get("role") == "admin"

        if is_admin:
            rows = conn.execute(
                """SELECT
                       model_id,
                       COALESCE(api_key_source, 'config') as key_source,
                       SUM(input_tokens) as total_input,
                       SUM(output_tokens) as total_output,
                       SUM(cost_estimate_usd) as total_cost,
                       COUNT(*) as request_count
                   FROM agent_token_usage
                   GROUP BY model_id, COALESCE(api_key_source, 'config')
                   ORDER BY total_cost DESC"""
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT
                       model_id,
                       COALESCE(api_key_source, 'config') as key_source,
                       SUM(input_tokens) as total_input,
                       SUM(output_tokens) as total_output,
                       SUM(cost_estimate_usd) as total_cost,
                       COUNT(*) as request_count
                   FROM agent_token_usage
                   WHERE user_id = ?
                   GROUP BY model_id, COALESCE(api_key_source, 'config')
                   ORDER BY total_cost DESC""",
                (user["id"],),
            ).fetchall()

        return jsonify({"providers": [dict(r) for r in rows]})
    except sqlite3.OperationalError:
        # api_key_source column might not exist yet
        return jsonify({"providers": []})
    finally:
        conn.close()


@usage_api.route("/time-series")
def usage_time_series():
    """Daily token usage over time for charting."""
    user = getattr(g, "current_user", None)
    if not user:
        return jsonify({"error": "Not authenticated"}), 401

    days = min(int(request.args.get("days", "30")), 90)
    conn = _get_db()
    try:
        is_admin = user.get("role") == "admin"

        if is_admin:
            rows = conn.execute(
                """SELECT
                       DATE(created_at) as day,
                       SUM(input_tokens) as input_tokens,
                       SUM(output_tokens) as output_tokens,
                       SUM(cost_estimate_usd) as cost,
                       COUNT(*) as requests
                   FROM agent_token_usage
                   WHERE created_at >= DATE('now', ?)
                   GROUP BY DATE(created_at)
                   ORDER BY day""",
                (f"-{days} days",),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT
                       DATE(created_at) as day,
                       SUM(input_tokens) as input_tokens,
                       SUM(output_tokens) as output_tokens,
                       SUM(cost_estimate_usd) as cost,
                       COUNT(*) as requests
                   FROM agent_token_usage
                   WHERE user_id = ? AND created_at >= DATE('now', ?)
                   GROUP BY DATE(created_at)
                   ORDER BY day""",
                (user["id"], f"-{days} days"),
            ).fetchall()

        return jsonify({"series": [dict(r) for r in rows], "days": days})
    finally:
        conn.close()


@usage_api.route("/totals")
def usage_totals():
    """Grand totals for stat cards."""
    user = getattr(g, "current_user", None)
    if not user:
        return jsonify({"error": "Not authenticated"}), 401

    conn = _get_db()
    try:
        is_admin = user.get("role") == "admin"

        if is_admin:
            row = conn.execute(
                """SELECT
                       SUM(input_tokens) as total_input,
                       SUM(output_tokens) as total_output,
                       SUM(thinking_tokens) as total_thinking,
                       SUM(cost_estimate_usd) as total_cost,
                       COUNT(*) as total_requests,
                       COUNT(DISTINCT user_id) as unique_users,
                       COUNT(DISTINCT model_id) as unique_models
                   FROM agent_token_usage"""
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT
                       SUM(input_tokens) as total_input,
                       SUM(output_tokens) as total_output,
                       SUM(thinking_tokens) as total_thinking,
                       SUM(cost_estimate_usd) as total_cost,
                       COUNT(*) as total_requests,
                       1 as unique_users,
                       COUNT(DISTINCT model_id) as unique_models
                   FROM agent_token_usage
                   WHERE user_id = ?""",
                (user["id"],),
            ).fetchone()

        return jsonify(dict(row) if row else {})
    finally:
        conn.close()
