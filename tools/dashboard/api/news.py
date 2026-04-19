# CUI // SP-CTI
"""FathomDesk News API — category summary, listing, clusters, and export.

Inline-route blueprint (routes are hardcoded with /api/news/... prefix).
Register via _mount_inline(news_api) in tools/dashboard/api/__init__.py.

Key endpoint (cat-02):
  GET /api/news/category-summary/<category>
    Returns 7-day net-sentiment sparkline, 24h item count, and pattern
    chip count (placeholder '—' until task 7.11-pattern-04 lands).
"""

from __future__ import annotations

import csv
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.trading.news.db import FATHOMDESK_DB, get_db  # noqa: E402

news_api = Blueprint("news_api", __name__)

VALID_CATEGORIES = frozenset(
    ["all", "macro", "geopolitical", "earnings", "regulatory", "sector", "corporate"]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _conn():
    return get_db(str(FATHOMDESK_DB))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# GET /api/news
# ---------------------------------------------------------------------------

@news_api.route("/api/news", methods=["GET"])
def list_news():
    """Return up to 200 news items, optionally filtered by ?category=."""
    category = request.args.get("category", "").strip().lower() or None
    limit = min(int(request.args.get("limit", 200)), 500)
    try:
        conn = _conn()
        ph = "?" if "sqlite" in type(conn).__module__ else "%s"
        order = (
            "ORDER BY CASE impact_level "
            "WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
            "published_at DESC"
        )
        try:
            if category and category != "all":
                rows = conn.execute(
                    f"SELECT * FROM ad_news_items WHERE category = {ph} {order} LIMIT {ph}",  # nosec B608
                    (category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM ad_news_items {order} LIMIT {ph}",  # nosec B608
                    (limit,),
                ).fetchall()
            items = [dict(r) for r in rows]
        finally:
            conn.close()
        return jsonify(items)
    except Exception as exc:
        return jsonify({"error": str(exc), "items": []}), 500


# ---------------------------------------------------------------------------
# GET /api/news/category-summary/<category>  (cat-02)
# ---------------------------------------------------------------------------

@news_api.route("/api/news/category-summary/<category>", methods=["GET"])
def category_summary(category: str):
    """Return 7-day net-sentiment sparkline + 24h count + pattern placeholder.

    Response schema:
      {
        "category": str,
        "sparkline": [{"date": "YYYY-MM-DD", "net": int}, ...],   // 7 entries, oldest→newest
        "count_24h": int,
        "pattern_count": null    // placeholder until 7.11-pattern-04 lands
      }
    """
    category = category.strip().lower()
    if category not in VALID_CATEGORIES:
        return jsonify({"error": f"Unknown category: {category}"}), 400

    now = _utcnow()
    cutoff_24h = now - timedelta(hours=24)

    # Build date list for last 7 days (oldest → newest, up to yesterday + today)
    dates = [(now - timedelta(days=i)).date() for i in range(6, -1, -1)]

    try:
        conn = _conn()
        from tools.db.storage import sql_placeholder
        ph = sql_placeholder(conn)
        try:
            # ── 7-day sparkline ──────────────────────────────────────────────
            # Pull all items from the last 7 days for the given category.
            # Use ingested_at as the reliable timestamp (published_at can be null).
            cutoff_7d = (now - timedelta(days=7)).isoformat()

            if category == "all":
                rows = conn.execute(
                    f"SELECT ingested_at, net_direction FROM ad_news_items "  # nosec B608
                    f"WHERE ingested_at >= {ph}",
                    (cutoff_7d,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT ingested_at, net_direction FROM ad_news_items "  # nosec B608
                    f"WHERE ingested_at >= {ph} AND category = {ph}",
                    (cutoff_7d, category),
                ).fetchall()

            # Aggregate net per day
            day_counts: dict[str, dict[str, int]] = {
                str(d): {"bullish": 0, "bearish": 0} for d in dates
            }
            for row in rows:
                ts = row[0] if not hasattr(row, "keys") else row["ingested_at"]
                direction = row[1] if not hasattr(row, "keys") else row["net_direction"]
                if not ts:
                    continue
                try:
                    day_key = ts[:10]  # "YYYY-MM-DD"
                except (TypeError, IndexError):
                    continue
                if day_key in day_counts:
                    if direction == "bullish":
                        day_counts[day_key]["bullish"] += 1
                    elif direction == "bearish":
                        day_counts[day_key]["bearish"] += 1

            sparkline = [
                {"date": str(d), "net": day_counts[str(d)]["bullish"] - day_counts[str(d)]["bearish"]}
                for d in dates
            ]

            # ── 24h count ────────────────────────────────────────────────────
            cutoff_24h_iso = cutoff_24h.isoformat()
            if category == "all":
                count_row = conn.execute(
                    f"SELECT COUNT(*) FROM ad_news_items WHERE ingested_at >= {ph}",  # nosec B608
                    (cutoff_24h_iso,),
                ).fetchone()
            else:
                count_row = conn.execute(
                    f"SELECT COUNT(*) FROM ad_news_items "  # nosec B608
                    f"WHERE ingested_at >= {ph} AND category = {ph}",
                    (cutoff_24h_iso, category),
                ).fetchone()
            count_24h = count_row[0] if count_row else 0

        finally:
            conn.close()

        return jsonify({
            "category": category,
            "sparkline": sparkline,
            "count_24h": count_24h,
            "pattern_count": None,  # placeholder until 7.11-pattern-04 lands
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /api/news/reading
# ---------------------------------------------------------------------------

@news_api.route("/api/news/reading", methods=["GET"])
def news_reading():
    """Aggregate sentiment reading across all recent news items."""
    try:
        conn = _conn()
        from tools.db.storage import sql_placeholder
        ph = sql_placeholder(conn)
        cutoff = (_utcnow() - timedelta(hours=24)).isoformat()
        try:
            rows = conn.execute(
                f"SELECT net_direction FROM ad_news_items WHERE ingested_at >= {ph}",  # nosec B608
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()

        counts = {"bullish": 0, "bearish": 0, "neutral": 0}
        for r in rows:
            d = (r[0] if not hasattr(r, "keys") else r["net_direction"]) or "neutral"
            if d in counts:
                counts[d] += 1

        total = sum(counts.values()) or 1
        bull_pct = round(counts["bullish"] / total * 100)
        bear_pct = round(counts["bearish"] / total * 100)

        if bull_pct > 55:
            mood = "bullish"
            summary = f"News flow leans bullish ({bull_pct}% of recent items)."
        elif bear_pct > 55:
            mood = "bearish"
            summary = f"News flow leans bearish ({bear_pct}% of recent items)."
        else:
            mood = "neutral"
            summary = f"Mixed signals — {bull_pct}% bullish, {bear_pct}% bearish across {total} recent items."

        return jsonify({"mood": mood, "summary": summary, "counts": counts, "total": total})
    except Exception as exc:
        return jsonify({"mood": "neutral", "summary": "Unable to compute reading.", "error": str(exc)})


# ---------------------------------------------------------------------------
# GET /api/news/clusters
# ---------------------------------------------------------------------------

@news_api.route("/api/news/clusters", methods=["GET"])
def list_clusters():
    """Return active news clusters."""
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT * FROM ad_news_clusters ORDER BY last_seen DESC LIMIT 50"
            ).fetchall()
            clusters = [dict(r) for r in rows]
        finally:
            conn.close()
        return jsonify({"clusters": clusters})
    except Exception as exc:
        return jsonify({"clusters": [], "error": str(exc)})


# ---------------------------------------------------------------------------
# GET /api/news/divergences
# ---------------------------------------------------------------------------

@news_api.route("/api/news/divergences", methods=["GET"])
def list_divergences():
    """Return cross-signal divergences (stub — returns empty until analyzer lands)."""
    return jsonify({"divergences": []})


# ---------------------------------------------------------------------------
# GET /api/news/export.csv
# ---------------------------------------------------------------------------

@news_api.route("/api/news/export.csv", methods=["GET"])
def export_csv():
    """Download news items as CSV; ?category= filters by category."""
    category = request.args.get("category", "").strip().lower() or None
    try:
        conn = _conn()
        from tools.db.storage import sql_placeholder
        ph = sql_placeholder(conn)
        try:
            if category and category != "all":
                rows = conn.execute(
                    f"SELECT id, source, title, link, published_at, category, "  # nosec B608
                    f"impact_level, net_direction, mentioned_tickers "
                    f"FROM ad_news_items WHERE category = {ph} "
                    f"ORDER BY published_at DESC LIMIT 2000",
                    (category,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, source, title, link, published_at, category, "
                    "impact_level, net_direction, mentioned_tickers "
                    "FROM ad_news_items ORDER BY published_at DESC LIMIT 2000"
                ).fetchall()
        finally:
            conn.close()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "source", "title", "link", "published_at",
                          "category", "impact_level", "net_direction", "mentioned_tickers"])
        for r in rows:
            row = list(r) if not hasattr(r, "keys") else [r[k] for k in r.keys()]
            writer.writerow(row)

        filename = f"news_{category or 'all'}.csv"
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /api/news/<news_id>
# ---------------------------------------------------------------------------

@news_api.route("/api/news/<news_id>", methods=["GET"])
def get_news_item(news_id: str):
    """Return a single news item by ID."""
    try:
        conn = _conn()
        from tools.db.storage import sql_placeholder
        ph = sql_placeholder(conn)
        try:
            row = conn.execute(
                f"SELECT * FROM ad_news_items WHERE id = {ph}",  # nosec B608
                (news_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify(dict(row))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# POST /api/news/<news_id>/analyze
# ---------------------------------------------------------------------------

@news_api.route("/api/news/<news_id>/analyze", methods=["POST"])
def analyze_item(news_id: str):
    """INTaaS analysis stub — full implementation pending INTaaS wiring."""
    return jsonify({"error": "INTaaS analysis not yet wired for this item."}), 501


# ---------------------------------------------------------------------------
# GET /api/news/<news_id>/supply-chain-impact
# ---------------------------------------------------------------------------

@news_api.route("/api/news/<news_id>/supply-chain-impact", methods=["GET"])
def supply_chain_impact(news_id: str):
    """Supply-chain impact trace stub — returns empty structure."""
    return jsonify({
        "news_id": news_id,
        "entities": {},
        "aggregate": {"winners": [], "losers": [], "total_tickers_affected": 0},
        "per_subject": [],
    })
