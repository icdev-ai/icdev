# CUI // SP-CTI
"""Cache Savings Blueprint — prompt/context caching metrics dashboard.

Mounted at /cache-savings by tools/dashboard/app.py.

Dashboard gate components (CLAUDE.md 8-point checklist):
  1. template: tools/dashboard/templates/cache_savings/page.html
  2. icdev/ mirror: icdev/tools/dashboard/templates/cache_savings/page.html (companion sync)
  3. route: @bp.route("/cache-savings")
  4. backing module: tools/cache_savings/savings.py (get_savings_stats)
  5. constants: (none required — all constants inline in savings.py)
  6. DB: reads from llm_response_cache (existing table — no new migration needed)
  7. nav link: base.html via LLM section (added separately)
  8. IQE: (lightweight page — IQE integration deferred to next sprint)

NIST 800-53: SC-28, AU-12, SA-11
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request as flask_request

logger = logging.getLogger(__name__)

bp = Blueprint("cache_savings", __name__)


@bp.route("/cache-savings")
def cache_savings():
    """Cache savings dashboard — hit rate, tokens saved, cost savings."""
    from tools.cache_savings.savings import get_savings_stats

    since_hours = int(flask_request.args.get("since_hours", 168))
    stats = get_savings_stats(since_hours=since_hours)
    return render_template("cache_savings/page.html", stats=stats)


@bp.route("/api/cache-savings/stats")
def api_cache_savings_stats():
    """JSON API — cache savings statistics for dashboards and monitoring."""
    from tools.cache_savings.savings import get_savings_stats

    since_hours = int(flask_request.args.get("since_hours", 168))
    stats = get_savings_stats(since_hours=since_hours)
    return jsonify(stats)
