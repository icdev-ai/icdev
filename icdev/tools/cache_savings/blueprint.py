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
    # cch-obs-01: the per-provider view. Best-effort — a failure here must not
    # take down the page that already works, but it says so rather than
    # rendering an empty table that reads like "no providers cache anything".
    try:
        from tools.cache_savings.by_provider import get_provider_effectiveness
        by_provider = get_provider_effectiveness()
    except Exception as exc:  # noqa: BLE001
        logger.warning("per-provider cache effectiveness unavailable: %s", exc)
        by_provider = {
            "measurable": False,
            "unmeasurable_reason": f"per-provider view unavailable: {exc}",
            "providers": [], "totals": {},
            "window_days": 0, "window_start": "", "window_end": "",
        }
    return render_template("cache_savings/page.html", stats=stats, by_provider=by_provider)


@bp.route("/api/cache-savings/by-provider")
def api_cache_savings_by_provider():
    """GET /api/cache-savings/by-provider — per-provider cache effectiveness (cch-obs-01).

    Optional ``?window_days=N`` overrides the configured window.
    """
    from tools.cache_savings.by_provider import get_provider_effectiveness
    raw = flask_request.args.get("window_days")
    try:
        window_days = int(raw) if raw else None
    except ValueError:
        return jsonify({"error": "window_days must be an integer"}), 400
    return jsonify(get_provider_effectiveness(window_days=window_days))


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
    # cch-obs-01: the tile carried ONE hit rate over every provider. It keeps
    # doing that for the RESPONSE cache (avoided calls, which is genuinely one
    # number), and now also carries a per-provider PREFIX-cache breakdown —
    # counts by state, never a blended rate. `no data`, `not reported` and a
    # measured 0% are three different states and the tile shows all three.
    prefix = {"measurable": False, "caching": 0, "no_cache_hits": 0,
              "unreported": 0, "no_data": 0, "usd_saved": None, "top": []}
    try:
        from tools.cache_savings.by_provider import get_provider_effectiveness
        bp_stats = get_provider_effectiveness()
        t = bp_stats.get("totals") or {}
        prefix = {
            "measurable":    bp_stats.get("measurable", False),
            "window_days":   bp_stats.get("window_days", 0),
            "caching":       t.get("providers_caching", 0),
            "no_cache_hits": t.get("providers_no_cache_hits", 0),
            "unreported":    t.get("providers_unreported", 0),
            "no_data":       t.get("providers_no_data", 0),
            "usd_saved":     t.get("usd_saved_total"),
            # The busiest few, so the tile names providers instead of implying
            # a platform-wide rate that no single number can honestly carry.
            "top": [
                {"provider": p["provider"], "status": p["status"],
                 "cached_share_pct": p["cached_share_pct"], "calls": p["calls"]}
                for p in (bp_stats.get("providers") or [])[:3]
            ],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("per-provider cache tile block unavailable: %s", exc)

    return jsonify({
        "by_provider":   prefix,
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

    collections = ["cache.stats", "cache.entries", "cache.by_provider"]
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
