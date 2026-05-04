# CUI // SP-CTI
"""Quality Scores API — PE/NAV mispricing dashboard endpoints.

Routes (all inline, registered via _mount_inline):
  GET /api/quality-scores/universe       — all tickers with quality + pe_nav metrics
  GET /api/quality-scores/mispricing     — fundamental_mispricing oracle predictions
  GET /api/quality-scores/ticker/<sym>   — live quality score for a single ticker
  GET /api/quality-scores/stats          — aggregate summary stats
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

quality_scores_api = Blueprint("quality_scores_api", __name__)

_QUADRANT_LABELS = {
    "deep_value":                  "Deep Value",
    "efficient_compounder":        "Efficient Compounder",
    "asset_rich_earnings_poor":    "Asset-Rich / Earnings-Poor",
    "fully_priced":                "Fully Priced",
    "unknown":                     "Unknown",
}


# ---------------------------------------------------------------------------
# Universe endpoint
# ---------------------------------------------------------------------------

@quality_scores_api.route("/api/quality-scores/universe", methods=["GET"])
def api_universe():
    """Return all tickers from ad_quality_scores joined with ad_fundamental_metrics.

    Optionally filtered by:
      ?quadrant=deep_value | efficient_compounder | asset_rich_earnings_poor | fully_priced
      ?tier=1 | 2 | 3
      ?min_score=0-100
      ?limit=N  (default 200)
    """
    quadrant_filter = request.args.get("quadrant", "").strip().lower() or None
    tier_filter     = request.args.get("tier", "").strip() or None
    min_score       = request.args.get("min_score", "").strip() or None
    try:
        limit = min(int(request.args.get("limit", 200)), 500)
    except (ValueError, TypeError):
        limit = 200

    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT
                    q.ticker,
                    q.as_of_date,
                    q.composite_quality_score,
                    q.value_quality,
                    q.profitability_quality,
                    q.growth_quality,
                    q.balance_sheet_quality,
                    q.capital_allocation_quality,
                    q.moat_score,
                    q.pe_nav_score,
                    q.implied_roe,
                    q.roe_gap,
                    q.nav_quadrant,
                    q.quality_label,
                    f.pe_ratio,
                    f.pb_ratio,
                    f.roe,
                    f.roa,
                    f.gross_margin,
                    f.net_margin,
                    f.eps_growth_3y,
                    f.fcf_yield,
                    f.debt_to_equity,
                    f.dividend_yield,
                    f.sector_tier
                FROM ad_quality_scores q
                LEFT JOIN ad_fundamental_metrics f
                    ON q.ticker = f.ticker
                    AND f.as_of_date = (
                        SELECT MAX(as_of_date) FROM ad_fundamental_metrics
                        WHERE ticker = q.ticker
                    )
                WHERE q.as_of_date = (
                    SELECT MAX(as_of_date) FROM ad_quality_scores
                    WHERE ticker = q.ticker
                )
                ORDER BY q.composite_quality_score DESC NULLS LAST
                """
            ).fetchall()
        finally:
            conn.close()

        tickers = []
        for r in rows:
            d = dict(r)
            # Compute pe_nav metrics inline if not stored (migration may not have run)
            if d.get("nav_quadrant") is None and d.get("pe_ratio") and d.get("pb_ratio"):
                try:
                    from tools.trading.analysts.quality import compute_pe_nav_metrics
                    pnm = compute_pe_nav_metrics(d)
                    d.update({
                        "nav_quadrant": pnm["nav_quadrant"],
                        "implied_roe":  pnm["implied_roe"],
                        "roe_gap":      pnm["roe_gap"],
                        "pe_nav_score": pnm["pe_nav_score"],
                        "sector_tier":  pnm["sector_tier"],
                    })
                except Exception:
                    pass
            tickers.append(d)

        # Apply filters
        if quadrant_filter:
            tickers = [t for t in tickers if (t.get("nav_quadrant") or "") == quadrant_filter]
        if tier_filter:
            try:
                tier_int = int(tier_filter)
                tickers = [t for t in tickers if t.get("sector_tier") == tier_int]
            except ValueError:
                pass
        if min_score:
            try:
                ms = float(min_score)
                tickers = [t for t in tickers if (t.get("composite_quality_score") or 0) >= ms]
            except ValueError:
                pass

        tickers = tickers[:limit]
        return jsonify({"tickers": tickers, "count": len(tickers)})

    except Exception as exc:
        return jsonify({"tickers": [], "count": 0, "error": str(exc)}), 200


# ---------------------------------------------------------------------------
# Mispricing predictions endpoint
# ---------------------------------------------------------------------------

@quality_scores_api.route("/api/quality-scores/mispricing", methods=["GET"])
def api_mispricing():
    """Return fundamental_mispricing oracle predictions (pending outcome only).

    ?limit=N  (default 50)
    """
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except (ValueError, TypeError):
        limit = 50

    try:
        conn = get_connection()
        ph   = "%s" if getattr(conn, "_dialect", "sqlite") == "postgresql" else "?"
        try:
            rows = conn.execute(
                f"""
                SELECT id, lens_name, subject_type, subject_id,
                       prediction_type, prediction_text,
                       confidence, severity, horizon_days,
                       evidence_json, outcome, created_at
                FROM ad_trading_predictions
                WHERE lens_name = 'fundamental_mispricing'
                  AND outcome = 'pending'
                ORDER BY confidence DESC, created_at DESC
                LIMIT {ph}
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()

        predictions = []
        for r in rows:
            d = dict(r)
            ev = {}
            if d.get("evidence_json"):
                try:
                    ev = json.loads(d["evidence_json"])
                except Exception:
                    pass
            predictions.append({
                "id":               d["id"],
                "ticker":           d["subject_id"],
                "type":             d["prediction_type"],
                "confidence":       d["confidence"],
                "severity":         d["severity"],
                "horizon_days":     d["horizon_days"],
                "description":      d["prediction_text"],
                "outcome":          d["outcome"],
                "created_at":       d["created_at"],
                "roe_gap":          ev.get("roe_gap"),
                "nav_quadrant":     ev.get("nav_quadrant"),
                "pe_ratio":         ev.get("pe_ratio"),
                "pb_ratio":         ev.get("pb_ratio"),
                "roe":              ev.get("roe"),
                "implied_roe":      ev.get("implied_roe"),
                "sector_tier":      ev.get("sector_tier"),
                "recommended_action": ev.get("recommended_action", ev.get("title", "")),
            })

        return jsonify({"predictions": predictions, "count": len(predictions)})

    except Exception as exc:
        return jsonify({"predictions": [], "count": 0, "error": str(exc)}), 200


# ---------------------------------------------------------------------------
# Single-ticker live quality score
# ---------------------------------------------------------------------------

@quality_scores_api.route("/api/quality-scores/ticker/<ticker>", methods=["GET"])
def api_ticker_detail(ticker: str):
    """Return a live quality score for a single ticker.

    Loads fundamentals from ad_fundamental_metrics (latest row) and runs
    score_quality() + compute_pe_nav_metrics() fresh. Falls back to sample
    data if no DB row exists when ?demo=1.
    """
    sym  = ticker.strip().upper()
    demo = request.args.get("demo", "0") in ("1", "true", "yes")

    try:
        from tools.trading.analysts.quality import (
            load_fundamentals,
            score_quality,
            _build_sample_fundamentals,
        )

        if demo:
            fundamentals = _build_sample_fundamentals(sym)
        else:
            fundamentals = load_fundamentals(sym)

        if not fundamentals:
            return jsonify({"ok": False, "error": f"No fundamental data for {sym}"}), 404

        result = score_quality(sym, fundamentals)
        return jsonify({"ok": True, "ticker": sym, "result": result})

    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 200


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

@quality_scores_api.route("/api/quality-scores/stats", methods=["GET"])
def api_stats():
    """Return aggregate PE/NAV stats across the universe."""
    try:
        conn = get_connection()
        try:
            # Total scored tickers
            total = conn.execute(
                "SELECT COUNT(DISTINCT ticker) AS n FROM ad_quality_scores"
            ).fetchone()
            total_n = int((dict(total) if total else {}).get("n", 0) or 0)

            # Quality label distribution
            labels = conn.execute(
                """
                SELECT quality_label, COUNT(*) AS n
                FROM (
                    SELECT ticker, quality_label
                    FROM ad_quality_scores
                    WHERE as_of_date = (
                        SELECT MAX(as_of_date) FROM ad_quality_scores AS q2
                        WHERE q2.ticker = ad_quality_scores.ticker
                    )
                )
                GROUP BY quality_label
                """
            ).fetchall()
            label_dist = {dict(r)["quality_label"]: int(dict(r)["n"]) for r in labels}

            # Quadrant distribution
            quads = conn.execute(
                """
                SELECT nav_quadrant, COUNT(*) AS n
                FROM (
                    SELECT ticker, nav_quadrant
                    FROM ad_quality_scores
                    WHERE nav_quadrant IS NOT NULL
                    AND as_of_date = (
                        SELECT MAX(as_of_date) FROM ad_quality_scores AS q2
                        WHERE q2.ticker = ad_quality_scores.ticker
                    )
                )
                GROUP BY nav_quadrant
                """
            ).fetchall()
            quad_dist = {dict(r)["nav_quadrant"]: int(dict(r)["n"]) for r in quads}

            # Pending mispricing predictions
            mp = conn.execute(
                "SELECT COUNT(*) AS n FROM ad_trading_predictions "
                "WHERE lens_name='fundamental_mispricing' AND outcome='pending'"
            ).fetchone()
            mispricing_pending = int((dict(mp) if mp else {}).get("n", 0) or 0)

            # Undervalued / overvalued counts (roe_gap thresholds)
            uv = conn.execute(
                "SELECT COUNT(*) AS n FROM ad_quality_scores "
                "WHERE roe_gap > 0.04 AND as_of_date = ("
                "  SELECT MAX(as_of_date) FROM ad_quality_scores AS q2 "
                "  WHERE q2.ticker = ad_quality_scores.ticker)"
            ).fetchone()
            undervalued_n = int((dict(uv) if uv else {}).get("n", 0) or 0)

            ov = conn.execute(
                "SELECT COUNT(*) AS n FROM ad_quality_scores "
                "WHERE roe_gap < -0.04 AND as_of_date = ("
                "  SELECT MAX(as_of_date) FROM ad_quality_scores AS q2 "
                "  WHERE q2.ticker = ad_quality_scores.ticker)"
            ).fetchone()
            overvalued_n = int((dict(ov) if ov else {}).get("n", 0) or 0)

        finally:
            conn.close()

        return jsonify({
            "total_tickers":       total_n,
            "mispricing_pending":  mispricing_pending,
            "undervalued":         undervalued_n,
            "overvalued":          overvalued_n,
            "quality_labels":      label_dist,
            "quadrant_counts":     quad_dist,
        })

    except Exception as exc:
        return jsonify({
            "total_tickers": 0, "mispricing_pending": 0,
            "undervalued": 0, "overvalued": 0,
            "quality_labels": {}, "quadrant_counts": {},
            "error": str(exc),
        }), 200


# ---------------------------------------------------------------------------
# Durable Compounders scanner
# ---------------------------------------------------------------------------

@quality_scores_api.route("/api/quality-scores/durable-compounders", methods=["GET"])
def api_durable_compounders():
    """Run the Durable Compounders scan (cached, hash-gated).

    ?force=1       — bypass cache, recompute all
    ?min_score=N   — minimum durable score (0-100)
    ?tier=fortress|solid|watchlist
    ?limit=N       (default 200, max 500)
    """
    force = request.args.get("force", "0") in ("1", "true", "yes")
    tier_filter = request.args.get("tier", "").strip().lower() or None
    try:
        min_score = float(request.args.get("min_score", 0))
    except (ValueError, TypeError):
        min_score = 0.0
    try:
        limit = min(int(request.args.get("limit", 200)), 500)
    except (ValueError, TypeError):
        limit = 200

    try:
        from tools.trading.analysts.durable_compounders import scan
        result = scan(limit=limit, min_score=min_score, tier_filter=tier_filter, force=force)
        return jsonify(result)
    except Exception as exc:
        return jsonify({
            "results": [], "count": 0,
            "cache_hits": 0, "recomputed": 0, "drifted": 0,
            "scan_time_ms": 0,
            "error": str(exc),
        }), 200


@quality_scores_api.route("/api/quality-scores/durable-compounders/drift", methods=["GET"])
def api_durable_drift():
    """Return tickers with recently detected fundamental drift.

    ?limit=N  (default 50)
    """
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except (ValueError, TypeError):
        limit = 50

    try:
        from tools.trading.analysts.durable_compounders import get_drift_events
        events = get_drift_events(limit=limit)
        return jsonify({"drift_events": events, "count": len(events)})
    except Exception as exc:
        return jsonify({"drift_events": [], "count": 0, "error": str(exc)}), 200
