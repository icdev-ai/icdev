# [TEMPLATE: CUI // SP-CTI]
"""
Usage tracking + cost dashboard API blueprint (Phase 30 — D177).

Provides per-user token aggregation, per-provider breakdown, and cost estimates.
Admin sees all users' usage; non-admin users see only their own.

Authorization (nav-sec-03 / issue #137)
---------------------------------------
Every endpoint requires an authenticated user resolved by the dashboard auth
middleware (tools/dashboard/auth.py sets ``g.current_user``). There is NO admin
fallback for unresolvable users — an unauthenticated caller receives ``401``.

  * ``admin``     — sees org-wide rows (all users). May inspect a single user
                    via ``?user_id=`` on ``/summary``.
  * non-admin     — filtered to rows whose ``user_id`` matches their own id
                    (``agent_token_usage.user_id``, added by
                    ``DASHBOARD_AUTH_ALTER_SQL``). A query param can never widen
                    a non-admin's scope.

This matches the "others see only their own" contract that the ``/usage``
template and the usage test suite already assume.
"""

from tools.db.storage import get_connection

from flask import Blueprint, g, jsonify, request

from tools.dashboard.config import DB_PATH

usage_api = Blueprint("usage_api", __name__, url_prefix="/api/usage")


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _user_field(user, field, default=None):
    """Read a field from a user that may be a dict or a DB Row."""
    if user is None:
        return default
    if isinstance(user, dict):
        return user.get(field, default)
    try:
        return user[field]
    except (KeyError, IndexError, TypeError):
        return default


def _require_user():
    """Resolve the authenticated user or return a 401 JSON response.

    Returns ``(user, None)`` on success or ``(None, response)`` when the caller
    is unauthenticated. There is deliberately no admin fallback: an unresolvable
    user is unauthenticated, not an implicit admin (nav-sec-03 / issue #137).
    """
    user = getattr(g, "current_user", None)
    if not user:
        return None, (jsonify({"error": "authentication required"}), 401)
    return user, None


def _scope(user):
    """Return ``(is_admin, user_id)`` used to scope row visibility."""
    return _user_field(user, "role", "") == "admin", _user_field(user, "id")


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
    """Per-user usage summary.

    Admin sees a row per user (optionally narrowed by ``?user_id=``); a
    non-admin caller only ever sees their own aggregated row.
    """
    user, err = _require_user()
    if err:
        return err
    is_admin, uid = _scope(user)

    # Non-admin is pinned to their own id; admin may optionally narrow.
    filter_user = request.args.get("user_id") if is_admin else uid

    conn = _get_db()
    try:
        where = ""
        params = []
        if filter_user is not None:
            where = "WHERE user_id = %s"
            params.append(filter_user)
        rows = conn.execute(
            f"""SELECT
                   user_id,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(thinking_tokens) as total_thinking,
                   SUM(cost_estimate_usd) as total_cost,
                   COUNT(*) as request_count
               FROM agent_token_usage
               {where}
               GROUP BY user_id
               ORDER BY total_cost DESC""",  # nosec B608 - {where} is a fixed literal; user id is parameterized
            params,
        ).fetchall()

        return jsonify({"usage": [dict(r) for r in rows]})
    finally:
        conn.close()


@usage_api.route("/by-provider")
def usage_by_provider():
    """Token usage breakdown by provider/model and API-key source.

    Non-admin callers are filtered to their own rows.
    """
    user, err = _require_user()
    if err:
        return err
    is_admin, uid = _scope(user)

    conn = _get_db()
    try:
        where = ""
        params = []
        if not is_admin:
            where = "WHERE user_id = %s"
            params.append(uid)
        rows = conn.execute(
            f"""SELECT
                   model_id,
                   api_key_source as key_source,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(cost_estimate_usd) as total_cost,
                   COUNT(*) as request_count
               FROM agent_token_usage
               {where}
               GROUP BY model_id, api_key_source
               ORDER BY total_cost DESC""",  # nosec B608 - {where} is a fixed literal; user id is parameterized
            params,
        ).fetchall()

        return jsonify({"providers": [dict(r) for r in rows]})
    except Exception:
        return jsonify({"providers": []})
    finally:
        conn.close()


@usage_api.route("/time-series")
def usage_time_series():
    """Daily token usage over time for charting.

    Non-admin callers are filtered to their own rows.
    """
    user, err = _require_user()
    if err:
        return err
    is_admin, uid = _scope(user)

    days = min(int(request.args.get("days", "30")), 90)
    conn = _get_db()
    try:
        # Compute cutoff in Python for cross-backend compat (SQLite vs PostgreSQL)
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        where = "WHERE created_at >= %s"
        params = [cutoff]
        if not is_admin:
            where += " AND user_id = %s"
            params.append(uid)
        rows = conn.execute(
            f"""SELECT
                   DATE(created_at) as day,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cost_estimate_usd) as cost,
                   COUNT(*) as requests
               FROM agent_token_usage
               {where}
               GROUP BY DATE(created_at)
               ORDER BY day""",  # nosec B608 - {where} is a fixed literal; values are parameterized
            params,
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
    """Grand totals for stat cards.

    Non-admin callers are filtered to their own rows.
    """
    user, err = _require_user()
    if err:
        return err
    is_admin, uid = _scope(user)

    conn = _get_db()
    try:
        where = ""
        params = []
        if not is_admin:
            where = "WHERE user_id = %s"
            params.append(uid)
        row = conn.execute(
            f"""SELECT
                   COALESCE(SUM(input_tokens), 0) as total_input,
                   COALESCE(SUM(output_tokens), 0) as total_output,
                   COALESCE(SUM(thinking_tokens), 0) as total_thinking,
                   COALESCE(SUM(cost_estimate_usd), 0) as total_cost,
                   COUNT(*) as total_requests,
                   COUNT(DISTINCT user_id) as unique_users,
                   COUNT(DISTINCT agent_id) as unique_agents,
                   COUNT(DISTINCT model_id) as unique_models
               FROM agent_token_usage
               {where}""",  # nosec B608 - {where} is a fixed literal; user id is parameterized
            params,
        ).fetchone()

        return jsonify(dict(row) if row else {})
    finally:
        conn.close()
