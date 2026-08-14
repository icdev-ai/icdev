# CUI // SP-CTI
"""Cache Savings Blueprint — prompt/context caching metrics dashboard.

Mounted at /cache-savings by tools/dashboard/app.py.

8-point completeness checklist (CLAUDE.md):
  1. template        tools/dashboard/templates/cache_savings/page.html  ✓
  2. icdev mirror    icdev/tools/dashboard/templates/cache_savings/page.html ✓
  3. route           @bp.route("/cache-savings")                        ✓
  4. backing module  tools/cache_savings/savings.py                     ✓
  5. constants       tools/cache_savings/constants.py                   ✓
  6. DB migration    reads llm_response_cache (existing table)          ✓
  7. nav link        base.html Ops ▾ → Monitor section                  ✓
  8. IQE integration tools/iqe/adapters/cache_savings.py                ✓

NIST 800-53: SC-28, AU-12, SA-11
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger


from flask import Blueprint, jsonify, render_template, request as flask_request

logger = get_logger(__name__)

bp = Blueprint("cache_savings", __name__)


@bp.route("/cache-savings")
def cache_savings_page():
    """GET /cache-savings — cache savings analytics dashboard."""
    from tools.cache_savings.savings import get_savings_stats
    stats = get_savings_stats()
    return render_template("cache_savings/page.html", stats=stats)


@bp.route("/api/cache-savings/stats")
def api_cache_savings_stats():
    """GET /api/cache-savings/stats — JSON cache savings metrics."""
    from tools.cache_savings.savings import get_savings_stats
    stats = get_savings_stats()
    return jsonify(stats)


@bp.route("/api/cache-savings/tile")
def api_cache_savings_tile():
    """GET /api/cache-savings/tile — compact summary for home page monitor card."""
    from tools.cache_savings.savings import get_savings_stats
    stats = get_savings_stats()
    s = stats["summary"]
    return jsonify({
        "enabled":       stats["enabled"],
        "hit_rate_pct":  s["hit_rate_pct"],
        "total_entries": s["total_entries"],
        "tokens_saved":  s["cache_read_tokens"],
        "cost_usd_saved": s["total_usd_saved"],
        "backend":       stats["backend"],
        # Carried so the tile can say WHY it is zero. Without these it renders a
        # cold cache and a broken one identically.
        "state":          stats.get("state", ""),
        "state_detail":   stats.get("state_detail", ""),
        "stored_entries": stats.get("stored_entries", 0),
    })


@bp.route("/api/cache-savings/iqe-query", methods=["POST"])
def api_cache_iqe_query():
    """POST /api/cache-savings/iqe-query — IQE natural language query for cache stats."""
    try:
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import parse as _parse, IQESyntaxError
        from tools.iqe.executor import execute_query
        import importlib
        importlib.import_module("tools.iqe.adapters.cache_savings")
    except ImportError as exc:
        return jsonify({"ok": False, "error": f"IQE unavailable: {exc}"}), 503

    data = flask_request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    collections = ["cache.stats", "cache.entries"]
    try:
        result = nl_to_iqe(question, collections)
        iqe_str = result.get("iqe", "")
        explanation = result.get("explanation", "")
        try:
            ast = _parse(iqe_str)
            rows = execute_query(ast, conn=None)
        except IQESyntaxError:
            rows = []
        return jsonify({"ok": True, "canvas": "cache_savings", "iqe": iqe_str,
                        "explanation": explanation, "results": rows, "row_count": len(rows)})
    except Exception as exc:
        logger.warning("cache IQE query failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500
