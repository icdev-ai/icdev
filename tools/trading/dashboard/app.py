#!/usr/bin/env python3
from __future__ import annotations

import os
from tools.logging.icdev_logger import get_logger
"""FathomDesk Trading Dashboard — separate Flask app on port 5100.

Reads all data from data/fathomdesk.db. Supports:
- Full analysis lifecycle (run → signal → approve/reject)
- Portfolio and position tracking
- Signal queue with actions
- Analysis run history
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import time as _time

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Load .env so get_connection() uses PostgreSQL (where ad_users lives)
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(BASE_DIR / ".env")
except Exception:
    pass

try:
    from tools.db.storage import get_connection
except Exception:
    get_connection = None  # type: ignore[assignment]

_log = get_logger(__name__)

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
# Server-side session secret — used by Flask itself for signed cookies.
# In Phase 2A we use server-stored sessions (ad_user_sessions), but Flask
# still needs SECRET_KEY for things like flash() and any future signed payloads.
import os as _os
app.secret_key = _os.environ.get("ICDEV_SECRET_KEY") or _os.environ.get("FLASK_SECRET_KEY") or "fathomdesk-dev-secret-CHANGE-IN-PROD"

# Phase 2A — install auth middleware (gates non-public routes; loads
# g.current_user from the ad_session cookie).
try:
    from tools.trading.auth import middleware as _auth_mw
    from tools.trading.auth import db as _auth_db
    from tools.trading.auth import oauth as _auth_oauth
    from tools.trading.auth import reset as _auth_reset
    from tools.trading.auth import mfa as _auth_mfa
    from tools.trading.auth import webauthn as _auth_wa
    from tools.trading.credentials import db as _cred_db
    from tools.trading.tenancy import db as _tenant_db
    _auth_db.ensure_tables()
    _auth_reset.ensure_tables()
    _auth_mfa.ensure_tables()
    _auth_wa.ensure_tables()
    _cred_db.ensure_tables()
    _tenant_db.ensure_tables()
    _tenant_db.ensure_default_tenant()
    # Phase 4 R2 — target allocations table
    try:
        from tools.trading.analytics import rebalance as _rb
        _rb.ensure_tables()
    except Exception:
        pass
    # Phase 4 R3 — share-token table
    try:
        from tools.trading.share import tokens as _st
        _st.ensure_tables()
    except Exception:
        pass
    # Phase 4 R4 — personal API tokens table
    try:
        from tools.trading.auth import api_tokens as _apit
        _apit.ensure_tables()
    except Exception:
        pass
    # Phase 4 R5 — lesson progress table
    try:
        from tools.trading.lessons import catalog as _lc
        _lc.ensure_tables()
    except Exception:
        pass
    # Phase 5A — warm-load the tier catalog (YAML cache populates on first call)
    try:
        from tools.trading.billing import tiers as _bt
        _bt.load()
    except Exception:
        pass
    # Phase 3.2: backfill memberships for existing users (idempotent)
    try:
        _tenant_db.backfill_memberships_from_users()
    except Exception:
        pass
    _auth_oauth.init_app(app)
    _auth_mw.install(app)
except Exception as _e:
    # Auth not yet bootstrapped (e.g., during initial schema migration).
    # Fail open so existing tooling can still run; log and move on.
    import sys as _sys
    print(f"[auth] middleware install failed: {_e}", file=_sys.stderr)


# ---------------------------------------------------------------------------
# Phase 1 — inject the active user's profile into every template render so
# Jinja can conditionally render sidebar links / cards / Reading variants.
# ---------------------------------------------------------------------------
@app.context_processor
def _inject_profile_context():
    try:
        from flask import g
        from tools.trading.profile import db as pdb
        from tools.trading.profile import presets as pp
        # Phase 2A: key profile lookup off authenticated user when present.
        # Pre-auth (signup/login pages) falls back to 'default' so the modal
        # picker is consistent for new visitors.
        uid = g.current_user["id"] if getattr(g, "current_user", None) else "default"
        prof = pdb.get_profile(uid) or pp.ensure_default_profile(uid) or {}
    except Exception:
        prof = {}
    # Provide both the dict and convenient predicates for template conditionals
    hidden_pages = set(prof.get("hidden_pages") or [])
    hidden_cards = set(prof.get("hidden_cards") or [])
    # Phase 3.1: also expose the active tenant so templates can show
    # tenant name, role badge, branding (3.3 will use it for white-label).
    try:
        from flask import g
        tenant = getattr(g, "current_tenant", None) or {}
    except Exception:
        tenant = {}
    # Phase 5C: expose tier + feature matrix so templates can gate panels
    # and show upgrade CTAs. `plan_features[x]` → bool; `plan_quotas[x]` →
    # int|None; `plan_tier` → slug; `plan_tier_display` → human label.
    plan_tier = "free"
    plan_features: dict = {}
    plan_quotas: dict = {}
    plan_tier_display = "Free"
    try:
        from tools.trading.billing import tiers as _bt
        tid = tenant.get("id") if tenant else None
        if tid:
            plan_tier = _bt.tier_for_tenant(tid)
        info = _bt.tier_info(plan_tier) or {}
        plan_features = dict(info.get("features") or {})
        plan_quotas = dict(info.get("quotas") or {})
        plan_tier_display = info.get("display_name") or plan_tier.title()
    except Exception:
        pass
    return {
        "profile": prof,
        "profile_hidden_pages": hidden_pages,
        "profile_hidden_cards": hidden_cards,
        "profile_persona": prof.get("persona") or "",
        "profile_beginner_mode": bool(prof.get("beginner_mode_enabled")),
        "profile_theme": prof.get("theme") or "dark",
        "tenant": tenant,
        "plan_tier": plan_tier,
        "plan_tier_display": plan_tier_display,
        "plan_features": plan_features,
        "plan_quotas": plan_quotas,
    }


# ---------------------------------------------------------------------------
# Simple TTL cache for expensive API responses (file-backed for debug mode)
# ---------------------------------------------------------------------------
_CACHE_DIR = Path(__file__).resolve().parent.parent.parent.parent / ".tmp" / "api_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cached_response(key: str, ttl_seconds: int = 60):
    """Return cached Flask Response (pre-serialized JSON) if fresh, else None."""
    cache_file = _CACHE_DIR / f"{key}.json"
    if not cache_file.exists():
        return None
    age = _time.time() - cache_file.stat().st_mtime
    if age >= ttl_seconds:
        return None
    try:
        from flask import Response

        raw = cache_file.read_bytes()
        return Response(raw, mimetype="application/json")
    except Exception:
        return None


def _cached(key: str, ttl_seconds: int = 60):
    """Return cached dict if fresh, else None."""
    cache_file = _CACHE_DIR / f"{key}.json"
    if not cache_file.exists():
        return None
    age = _time.time() - cache_file.stat().st_mtime
    if age >= ttl_seconds:
        return None
    try:
        return json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def _set_cache(key: str, value):
    """Store value in cache (serializes once, serves raw bytes on hit)."""
    cache_file = _CACHE_DIR / f"{key}.json"
    try:
        cache_file.write_text(json.dumps(value, default=str), encoding="utf-8", newline="")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.route("/")
def page_index():
    return render_template("index.html")


@app.route("/portfolio")
def page_portfolio():
    return render_template("portfolio.html")


@app.route("/analysis")
def page_analysis():
    return render_template("analysis.html")


@app.route("/signals")
def page_signals():
    return render_template("signals.html")


@app.route("/orders")
def page_orders():
    return render_template("orders.html")


@app.route("/risk")
def page_risk():
    return render_template("risk.html")


@app.route("/settings")
def page_settings():
    import json as _json
    from tools.trading.profile import db as pdb
    prof = pdb.get_profile(_active_uid()) or {}
    vo = prof.get("voice_overrides") or {}
    if not isinstance(vo, str):
        vo = _json.dumps(vo)
    return render_template("settings.html", profile_voice_overrides=vo)


@app.route("/hotkeys")
def page_hotkeys():
    from flask import abort
    from tools.trading.profile import db as _pdb
    uid = g.current_user["id"] if getattr(g, "current_user", None) else None
    if not uid:
        abort(403)
    prof = _pdb.get_profile(uid) or {}
    persona = prof.get("persona") or ""
    allowed = {"day_trader", "pro_trader"}
    if persona not in allowed:
        flags = prof.get("flags") or {}
        if not flags.get("keyboard_shortcuts_visible") and not flags.get("requires_realtime"):
            abort(403)
    return render_template("hotkeys.html")


@app.route("/evolution")
def page_evolution():
    return render_template("evolution.html")


@app.route("/market")
def page_market():
    return render_template("market.html")


@app.route("/news")
def page_news():
    return render_template("news.html")


@app.route("/oracle")
def page_oracle():
    return render_template("oracle.html")


@app.route("/graph")
def page_graph():
    return render_template("graph.html")


@app.route("/radar")
def page_radar():
    """Systemic Risk & Opportunity Radar page."""
    return render_template("radar.html")


@app.route("/breadth")
def page_breadth():
    """Market Breadth Dashboard — % above 200 EMA, 52W hi/lo, sector heatmap."""
    persona = session.get("persona", "retail")
    tier = "beginner" if persona in ("student", "passive") else "advanced" if persona in ("quant", "pro_trader", "day_trader") else "intermediate"
    return render_template("breadth.html", persona_tier=tier)


@app.route("/value")
def page_value():
    """Value Compass — Fear & Greed index, Buffett Indicator, quality screener."""
    persona = session.get("persona", "retail")
    tier = "beginner" if persona in ("student", "passive") else "advanced" if persona in ("quant", "pro_trader", "day_trader") else "intermediate"
    return render_template("value.html", persona_tier=tier)


# ---------------------------------------------------------------------------
# API — Systemic Radar (SROR)
# ---------------------------------------------------------------------------
@app.route("/api/radar/latest")
def api_radar_latest():
    """Fetch latest SROR snapshot with composite scores, regime, family breakdown."""
    from tools.trading.market_intel.systemic_radar import get_latest_snapshot

    snapshot = get_latest_snapshot()
    if not snapshot:
        return jsonify({"error": "no_snapshot", "message": "No SROR snapshot available yet"}), 404
    return jsonify(snapshot)


@app.route("/api/radar/history")
def api_radar_history():
    """Fetch historical SROR snapshots for charting."""
    from tools.trading.market_intel.systemic_radar import get_history

    days = int(request.args.get("days", 30))
    history = get_history(days=days)
    return jsonify(history)


@app.route("/api/radar/alerts")
def api_radar_alerts():
    """Fetch active (unacknowledged) SROR alerts."""
    from tools.trading.market_intel.systemic_radar import get_active_alerts

    limit = int(request.args.get("limit", 50))
    alerts = get_active_alerts(limit=limit)
    return jsonify(alerts)


@app.route("/api/radar/indicators/<family>")
def api_radar_indicators(family):
    """Fetch detailed indicators for a family within a snapshot."""
    from tools.trading.market_intel.systemic_radar import get_family_indicators, get_latest_snapshot

    snapshot_id = request.args.get("snapshot")
    if not snapshot_id:
        latest = get_latest_snapshot()
        if not latest:
            return jsonify([])
        snapshot_id = latest["snapshot_id"]
    indicators = get_family_indicators(snapshot_id, family)
    return jsonify(indicators)


@app.route("/api/radar/compute", methods=["POST"])
def api_radar_compute():
    """Manually trigger SROR computation."""
    from tools.trading.market_intel.systemic_radar import compute_and_store

    result = compute_and_store()
    return jsonify(result)


_HELP_TOOLTIPS_CACHE: dict = {"mtime": 0.0, "data": {}}


_SEARCH_ENTITIES_CACHE: dict = {"mtime": 0.0, "data": None}


@app.route("/api/ticker/news/<ticker>")
def api_ticker_news(ticker):
    """Recent news mentioning a ticker, enriched with the ticker's latest
    aggregated news impact score so the UI can show 'this news moved NVDA +2.4'
    inline.

    Match strategy (union):
      1. ad_news_items.mentioned_tickers JSON array contains the ticker
      2. title or summary substring-matches the ticker
      3. news_id appears in ad_news_impact_traces for this ticker
    """
    t = ticker.upper().strip()
    if not t:
        return jsonify({"error": "empty ticker"}), 400
    limit = int(request.args.get("limit", 25))
    try:
        from tools.db.storage import get_connection
        conn = get_connection()

        # Path 1: items where KG impact traces this ticker — the strongest
        # signal for "this news moved this name"
        traced_ids = [
            r[0] if not hasattr(r, "keys") else r["news_id"]
            for r in conn.execute(
                "SELECT news_id FROM ad_news_impact_traces "
                "WHERE ticker = %s "
                "GROUP BY news_id ORDER BY MAX(traced_at) DESC LIMIT %s",
                (t, limit * 2),
            ).fetchall()
        ]

        # Path 2: explicit mentioned_tickers / text match. Cast mentioned_tickers
        # to text so it works under both SQLite (TEXT JSON) and PG (JSONB).
        like_ticker = f'%"{t}"%'
        like_text = f'%{t}%'
        text_rows = conn.execute(
            "SELECT id FROM ad_news_items "
            "WHERE mentioned_tickers::text LIKE %s "
            "   OR title ILIKE %s OR summary ILIKE %s "
            "ORDER BY ingested_at DESC LIMIT %s",
            (like_ticker, like_text, like_text, limit * 2),
        ).fetchall() if True else []
        text_ids = [r[0] if not hasattr(r, "keys") else r["id"] for r in text_rows]

        # Union, preserving traced-first ordering
        seen = set()
        ordered_ids = []
        for nid in traced_ids + text_ids:
            if nid not in seen:
                seen.add(nid)
                ordered_ids.append(nid)
        ordered_ids = ordered_ids[:limit]

        if not ordered_ids:
            conn.close()
            return jsonify({"ticker": t, "items": [], "count": 0})

        # Fetch the news items for the union
        placeholders = ",".join(["?"] * len(ordered_ids))
        rows = conn.execute(
            f"SELECT id, source, title, link, published_at, ingested_at, "
            f"summary, category, impact_level, net_direction, mentioned_tickers "
            f"FROM ad_news_items WHERE id IN ({placeholders})",
            tuple(ordered_ids),
        ).fetchall()
        id_to_item = {
            (r[0] if not hasattr(r, "keys") else r["id"]): dict(r) if hasattr(r, "keys") else {
                "id": r[0], "source": r[1], "title": r[2], "link": r[3],
                "published_at": r[4], "ingested_at": r[5], "summary": r[6],
                "category": r[7], "impact_level": r[8], "net_direction": r[9],
                "mentioned_tickers": r[10],
            }
            for r in rows
        }

        # Per-news impact score for this ticker (last 7 days)
        impact_rows = conn.execute(
            "SELECT news_id, impact_score FROM ad_news_impact_traces "
            "WHERE ticker = %s AND traced_at >= %s ",
            (t, (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()),
        ).fetchall() if ordered_ids else []
        news_impact = {}
        for r in impact_rows:
            d = dict(r) if hasattr(r, "keys") else {"news_id": r[0], "impact_score": r[1]}
            news_impact[d["news_id"]] = float(d["impact_score"] or 0)

        conn.close()

        items = []
        for nid in ordered_ids:
            item = id_to_item.get(nid)
            if not item:
                continue
            item["impact_score"] = news_impact.get(nid)
            # Don't return the raw mentioned_tickers blob — parse for UI
            mt = item.get("mentioned_tickers")
            if isinstance(mt, str):
                try:
                    item["mentioned_tickers"] = json.loads(mt)
                except (json.JSONDecodeError, TypeError):
                    item["mentioned_tickers"] = []
            items.append(item)

        return jsonify({"ticker": t, "count": len(items), "items": items})
    except Exception as e:
        return jsonify({"error": str(e), "items": []}), 500


_CHART_CACHE: dict = {}          # (ticker, period, interval) -> (expires_at, payload)


@app.route("/api/ticker/chart/<ticker>")
def api_ticker_chart(ticker):
    """OHLCV + technical indicators for a ticker.

    Query params:
      period   — 1mo | 3mo | 6mo | 1y | 2y | 5y  (default 6mo)
      interval — 1d | 1wk                        (default 1d)

    Response shape:
      { ticker, period, interval, bars: [{t,o,h,l,c,v}, ...],
        indicators: { sma20, sma50, sma200, rsi14, macd, macd_signal, macd_hist } }
      Each indicator is an array aligned by index with `bars`. Null values
      appear at the head where there aren't enough prior bars to compute.
    """
    import time as _t
    t = ticker.upper().strip()
    period = request.args.get("period", "6mo")
    interval = request.args.get("interval", "1d")
    if period not in ("1mo", "3mo", "6mo", "1y", "2y", "5y"):
        period = "6mo"
    if interval not in ("1d", "1wk"):
        interval = "1d"
    cache_key = (t, period, interval)
    hit = _CHART_CACHE.get(cache_key)
    # Intraday TTL could be shorter; for daily bars 10m is plenty
    if hit and hit[0] > _t.time():
        return jsonify(hit[1])

    try:
        import yfinance as yf
        hist = yf.Ticker(t).history(period=period, interval=interval, auto_adjust=False)
    except Exception as e:
        return jsonify({"ticker": t, "error": f"yfinance unavailable: {e}"})

    if hist is None or hist.empty:
        return jsonify({"ticker": t, "error": f"no data for {t}/{period}/{interval}"})

    # yfinance occasionally returns a phantom first row with NaN O/H/L/C and
    # v=0 at timezone boundaries. Drop any row with NaN price data up-front —
    # otherwise int(NaN) raises ValueError in the bar-dict comprehension
    # below and the whole endpoint 500s (silent blank chart on the UI).
    hist = hist.dropna(subset=["Open", "High", "Low", "Close"])
    if hist.empty:
        return jsonify({"ticker": t, "error": f"all rows NaN for {t}/{period}/{interval}"})

    # Dataframe → arrays
    closes = hist["Close"].astype(float).tolist()
    opens = hist["Open"].astype(float).tolist()
    highs = hist["High"].astype(float).tolist()
    lows = hist["Low"].astype(float).tolist()
    vols = hist["Volume"].fillna(0).astype(float).tolist()
    times = [d.strftime("%Y-%m-%d") for d in hist.index]

    def _sma(series, n):
        out = [None] * len(series)
        if len(series) < n:
            return out
        window_sum = sum(series[:n])
        out[n - 1] = window_sum / n
        for i in range(n, len(series)):
            window_sum += series[i] - series[i - n]
            out[i] = window_sum / n
        return out

    def _ema(series, n):
        out = [None] * len(series)
        if len(series) < n:
            return out
        k = 2.0 / (n + 1)
        # Seed EMA with the SMA of the first n values
        seed = sum(series[:n]) / n
        out[n - 1] = seed
        prev = seed
        for i in range(n, len(series)):
            cur = series[i] * k + prev * (1 - k)
            out[i] = cur
            prev = cur
        return out

    def _rsi(series, n=14):
        out = [None] * len(series)
        if len(series) <= n:
            return out
        gains = 0.0
        losses = 0.0
        for i in range(1, n + 1):
            change = series[i] - series[i - 1]
            if change >= 0: gains += change
            else: losses -= change
        avg_gain = gains / n
        avg_loss = losses / n
        rs = (avg_gain / avg_loss) if avg_loss > 0 else float("inf")
        out[n] = 100 - (100 / (1 + rs)) if avg_loss > 0 else 100
        for i in range(n + 1, len(series)):
            change = series[i] - series[i - 1]
            gain = change if change > 0 else 0.0
            loss = -change if change < 0 else 0.0
            avg_gain = (avg_gain * (n - 1) + gain) / n
            avg_loss = (avg_loss * (n - 1) + loss) / n
            if avg_loss == 0:
                out[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                out[i] = round(100 - (100 / (1 + rs)), 2)
        return out

    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [
        (a - b) if (a is not None and b is not None) else None
        for a, b in zip(ema12, ema26)
    ]
    # Signal line = 9-EMA of MACD; compute from the non-None slice
    first_idx = next((i for i, v in enumerate(macd_line) if v is not None), None)
    if first_idx is not None:
        seed_slice = [v for v in macd_line[first_idx:] if v is not None]
        sig_slice = _ema(seed_slice, 9)
        macd_signal = [None] * len(macd_line)
        for j, v in enumerate(sig_slice):
            macd_signal[first_idx + j] = v
    else:
        macd_signal = [None] * len(macd_line)
    macd_hist = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, macd_signal)
    ]

    indicators = {
        "sma20":  [None if v is None else round(v, 4) for v in _sma(closes, 20)],
        "sma50":  [None if v is None else round(v, 4) for v in _sma(closes, 50)],
        "sma200": [None if v is None else round(v, 4) for v in _sma(closes, 200)],
        "rsi14":  _rsi(closes, 14),
        "macd":        [None if v is None else round(v, 4) for v in macd_line],
        "macd_signal": [None if v is None else round(v, 4) for v in macd_signal],
        "macd_hist":   [None if v is None else round(v, 4) for v in macd_hist],
    }

    bars = [
        {"t": ts, "o": round(o, 4), "h": round(h, 4),
         "l": round(l, 4), "c": round(c, 4), "v": int(v)}
        for ts, o, h, l, c, v in zip(times, opens, highs, lows, closes, vols)
    ]
    payload = {
        "ticker": t, "period": period, "interval": interval,
        "bars": bars, "indicators": indicators,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    _CHART_CACHE[cache_key] = (_t.time() + 600, payload)  # 10m TTL
    return jsonify(payload)


_FUNDAMENTALS_CACHE: dict = {}   # ticker -> (expires_at, payload)


@app.route("/api/ticker/fundamentals/<ticker>")
def api_ticker_fundamentals(ticker):
    """Return fundamental metrics for a ticker: P/E, P/B, EV/EBITDA, FCF yield,
    dividend yield, market cap, short interest, 52w range, analyst targets.

    Sourced from yfinance. Cached per-ticker for 15 minutes — these change
    slowly and the upstream API is rate-limited.
    """
    import time as _time
    t = ticker.upper().strip()
    if not t:
        return jsonify({"error": "empty ticker"}), 400

    hit = _FUNDAMENTALS_CACHE.get(t)
    if hit and hit[0] > _time.time():
        return jsonify(hit[1])

    payload = {"ticker": t, "fetched_at": datetime.now(timezone.utc).isoformat()}
    try:
        import yfinance as yf
        info = yf.Ticker(t).info or {}
    except Exception as e:
        return jsonify({"ticker": t, "error": f"yfinance unavailable: {e}"})

    def _pct(v):  return None if v is None else round(float(v) * 100, 2)
    def _num(v, nd=2):
        if v is None: return None
        try: return round(float(v), nd)
        except Exception: return None
    def _big(v):  # $ in billions
        if v is None: return None
        try: return round(float(v) / 1e9, 2)
        except Exception: return None

    payload["metrics"] = {
        # Valuation
        "pe_trailing":     _num(info.get("trailingPE")),
        "pe_forward":      _num(info.get("forwardPE")),
        "peg_ratio":       _num(info.get("pegRatio")),
        "pb_ratio":        _num(info.get("priceToBook")),
        "ps_ratio":        _num(info.get("priceToSalesTrailing12Months")),
        "ev_ebitda":       _num(info.get("enterpriseToEbitda")),
        "ev_revenue":      _num(info.get("enterpriseToRevenue")),
        # Size / capital structure
        "market_cap_b":    _big(info.get("marketCap")),
        "enterprise_value_b": _big(info.get("enterpriseValue")),
        "total_debt_b":    _big(info.get("totalDebt")),
        "total_cash_b":    _big(info.get("totalCash")),
        "debt_to_equity":  _num(info.get("debtToEquity")),
        # Profitability
        "profit_margin":   _pct(info.get("profitMargins")),
        "operating_margin": _pct(info.get("operatingMargins")),
        "roe":             _pct(info.get("returnOnEquity")),
        "roa":             _pct(info.get("returnOnAssets")),
        # Cash flow / dividends
        "free_cashflow_b": _big(info.get("freeCashflow")),
        "operating_cashflow_b": _big(info.get("operatingCashflow")),
        "dividend_yield":  _pct(info.get("dividendYield")),
        "payout_ratio":    _pct(info.get("payoutRatio")),
        # Growth
        "earnings_growth": _pct(info.get("earningsGrowth")),
        "revenue_growth":  _pct(info.get("revenueGrowth")),
        # Risk & positioning
        "beta":            _num(info.get("beta")),
        "short_pct_float": _pct(info.get("shortPercentOfFloat")),
        "short_ratio":     _num(info.get("shortRatio")),
        "held_by_insiders": _pct(info.get("heldPercentInsiders")),
        "held_by_institutions": _pct(info.get("heldPercentInstitutions")),
        # Price context
        "price":           _num(info.get("currentPrice") or info.get("regularMarketPrice")),
        "52w_low":         _num(info.get("fiftyTwoWeekLow")),
        "52w_high":        _num(info.get("fiftyTwoWeekHigh")),
        "50d_avg":         _num(info.get("fiftyDayAverage")),
        "200d_avg":        _num(info.get("twoHundredDayAverage")),
        # Analyst coverage
        "target_mean":     _num(info.get("targetMeanPrice")),
        "target_high":     _num(info.get("targetHighPrice")),
        "target_low":      _num(info.get("targetLowPrice")),
        "analyst_count":   info.get("numberOfAnalystOpinions"),
        "recommendation":  info.get("recommendationKey"),
    }
    payload["sector"] = info.get("sector")
    payload["industry"] = info.get("industry")
    payload["long_name"] = info.get("longName") or info.get("shortName")

    # Upside vs analyst mean target
    metrics = payload["metrics"]
    if metrics.get("price") and metrics.get("target_mean"):
        metrics["upside_to_target"] = round(
            (metrics["target_mean"] - metrics["price"]) / metrics["price"] * 100, 1
        )
    # Position in 52w range (0 = low, 100 = high)
    if metrics.get("price") and metrics.get("52w_low") is not None and metrics.get("52w_high") is not None:
        rng = metrics["52w_high"] - metrics["52w_low"]
        if rng > 0:
            metrics["pct_52w_range"] = round(
                (metrics["price"] - metrics["52w_low"]) / rng * 100, 1
            )

    _FUNDAMENTALS_CACHE[t] = (_time.time() + 900, payload)  # 15m TTL
    return jsonify(payload)


@app.route("/api/search/entities")
def api_search_entities():
    """Return every searchable entity in the KG for the global command palette.

    Categories: ticker, etf, commodity, country, private_company, sector.
    Cached in-process and invalidated by kg_nodes row count + max created_at
    — cheap to compute, avoids re-querying on every palette keystroke.
    """
    try:
        from tools.trading.db import get_conn as get_connection
        conn = get_connection()
        # Cache key: number of nodes + latest created_at. If either changes,
        # reload. Use MAX(created_at)::text so PG can emit NULL safely when
        # the table is empty.
        cache_key_row = conn.execute(
            "SELECT COUNT(*) AS cnt, MAX(created_at)::text AS mx FROM kg_nodes"
        ).fetchone()
        if hasattr(cache_key_row, "keys"):
            ck = (cache_key_row["cnt"], cache_key_row["mx"] or "")
        else:
            ck = (cache_key_row[0], cache_key_row[1] or "")
        if _SEARCH_ENTITIES_CACHE.get("key") == ck and _SEARCH_ENTITIES_CACHE.get("data"):
            conn.close()
            return jsonify(_SEARCH_ENTITIES_CACHE["data"])

        rows = conn.execute(
            "SELECT label, entity_type, properties FROM kg_nodes "
            "WHERE entity_type IN ('ticker','etf','commodity','country','private_company','sector')"
        ).fetchall()
        conn.close()
        out = []
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else {
                "label": r[0], "entity_type": r[1], "properties": r[2],
            }
            props = d.get("properties") or {}
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except (json.JSONDecodeError, TypeError):
                    props = {}
            out.append({
                "label": d["label"],
                "type": d["entity_type"],
                "synonyms": props.get("synonyms") or [],
                "sector": props.get("sector"),
            })
        payload = {"entities": out, "count": len(out)}
        _SEARCH_ENTITIES_CACHE["key"] = ck
        _SEARCH_ENTITIES_CACHE["data"] = payload
        return jsonify(payload)
    except Exception as e:
        return jsonify({"entities": [], "count": 0, "error": str(e)})


@app.route("/api/help/tooltips")
def api_help_tooltips():
    """Serve the help_tooltips.yaml registry as JSON for the UI decorator.

    Cache invalidates on file mtime — edits take effect on the next request
    without a dashboard restart.
    """
    import yaml
    path = Path(__file__).resolve().parents[3] / "args" / "help_tooltips.yaml"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return jsonify({"_error": f"help_tooltips.yaml not found at {path}"})

    if mtime > _HELP_TOOLTIPS_CACHE["mtime"]:
        try:
            _HELP_TOOLTIPS_CACHE["data"] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            _HELP_TOOLTIPS_CACHE["mtime"] = mtime
        except Exception as e:
            return jsonify({"_error": f"failed to parse {path}: {e}"})

    return jsonify(_HELP_TOOLTIPS_CACHE["data"])


@app.route("/api/regime")
def api_regime():
    """Unified regime endpoint — returns SROR + news + alert summary in one call.

    Used by every dashboard page that wants the regime banner without
    making 3 separate API calls.
    """
    from tools.trading.market_intel.regime_lens import get_regime_context
    from tools.trading.market_intel.systemic_radar import get_active_alerts

    ctx = get_regime_context()
    payload = ctx.to_dict()

    # Bolt on top-3 active alerts (already sorted by severity)
    try:
        alerts = get_active_alerts(limit=3)
    except Exception:
        alerts = []
    payload["top_alerts"] = alerts

    # Data source warning — surface if macro is running on sample data
    ds = payload.get("data_source", "")
    payload["sample_data_warning"] = ds == "sample" or ds == "default"

    # Top regime-tier news clusters for the banner tooltip
    try:
        from tools.trading.news.db import list_active_clusters
        clusters = list_active_clusters() or []
        regime_clusters = sorted(
            [c for c in clusters if (c.get("status") or "").lower() == "regime"],
            key=lambda c: float(c.get("cumulative_score") or 0),
            reverse=True,
        )[:3]
        payload["top_news_clusters"] = [
            {
                "scenario_key": c.get("scenario_key"),
                "category": c.get("category"),
                "score": c.get("cumulative_score"),
            }
            for c in regime_clusters
        ]
    except Exception:
        payload["top_news_clusters"] = []

    return jsonify(payload)


# ---------------------------------------------------------------------------
# API — Overview (command center aggregator)
# ---------------------------------------------------------------------------
@app.route("/api/overview")
def api_overview():
    """Aggregate overview from all subsystems. Cached 60s."""
    hit = _cached_response("overview_agg", 60)
    if hit:
        return hit

    overview = {}

    # 1. Portfolio (Phase 3+ tenant-scoped)
    try:
        from tools.trading.db import get_conn
        uid = _active_uid()
        tid = _active_tenant_id()
        conn = get_conn()
        portfolio = conn.execute(
            "SELECT * FROM ad_portfolios WHERE user_id=%s AND tenant_id=%s LIMIT 1",
            (uid, tid),
        ).fetchone()
        positions = conn.execute(
            "SELECT * FROM ad_positions WHERE portfolio_id=%s AND qty > 0",
            (portfolio["id"],),
        ).fetchall()
        total_value = (dict(portfolio)["cash_balance"] if portfolio else 100000) + sum(dict(p)["market_value"] for p in positions)
        overview["portfolio"] = {
            "cash": dict(portfolio)["cash_balance"] if portfolio else 100000,
            "total_value": round(total_value, 2),
            "positions": len(positions),
            "daily_pnl": round(sum(dict(p).get("unrealized_pnl", 0) for p in positions), 2),
        }
        conn.close()
    except Exception:
        overview["portfolio"] = {"cash": 0, "total_value": 0, "positions": 0, "daily_pnl": 0}

    # 2. Signals
    try:
        from tools.trading.db import get_conn
        conn = get_conn()
        pending = conn.execute("SELECT COUNT(*) FROM ad_signals WHERE status='pending'").fetchone()[0]
        approved = conn.execute("SELECT COUNT(*) FROM ad_signals WHERE status='approved'").fetchone()[0]
        recent = conn.execute(
            "SELECT ticker, direction, composite_score, confidence FROM ad_signals "
            "ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        overview["signals"] = {
            "pending": pending,
            "approved": approved,
            "recent": [dict(r) for r in recent],
        }
        conn.close()
    except Exception:
        overview["signals"] = {"pending": 0, "approved": 0, "recent": []}

    # 3. Risk
    try:
        from tools.trading.db import get_conn
        conn = get_conn()
        ks = conn.execute("SELECT * FROM ad_kill_switch ORDER BY created_at DESC LIMIT 1").fetchone()
        overview["risk"] = {
            "kill_switch": dict(ks).get("status", "OFF") if ks else "OFF",
        }
        conn.close()
    except Exception:
        overview["risk"] = {"kill_switch": "OFF"}

    # 4. Daemon
    try:
        from tools.trading.market_intel.daemon import get_daemon_status
        ds = get_daemon_status()
        reflexes = ds.get("reflexes", {})
        active = sum(1 for r in reflexes.values() if r.get("enabled"))
        total_r = len(reflexes)
        last_runs = [r.get("last_run_at", "") for r in reflexes.values() if r.get("last_run_at")]
        last_run = max(last_runs) if last_runs else None
        overview["daemon"] = {"active": active, "total": total_r, "last_run": last_run}
    except Exception:
        overview["daemon"] = {"active": 0, "total": 0, "last_run": None}

    # 5. Market Pulse
    try:
        from tools.trading.db import get_conn
        conn = get_conn()
        rows = conn.execute(
            "SELECT ticker, direction, composite_score, confidence, sector "
            "FROM ad_market_snapshot ORDER BY composite_score DESC"
        ).fetchall()
        latest_ts_row = conn.execute(
            "SELECT MAX(created_at) FROM ad_market_snapshot"
        ).fetchone()
        conn.close()
        sigs = [dict(r) for r in rows]
        from collections import Counter
        dir_counts = Counter(s.get("direction", "HOLD") for s in sigs)
        top_bull = [s for s in sigs if s.get("direction") == "BUY"][:3]
        top_bear = sorted(
            [s for s in sigs if s.get("direction") in ("SELL", "SHORT")],
            key=lambda x: x.get("composite_score", 50)
        )[:3]
        snapshot_as_of = latest_ts_row[0] if latest_ts_row and latest_ts_row[0] else None
        overview["market"] = {
            "universe": len(sigs),
            "buy": dir_counts.get("BUY", 0),
            "sell": dir_counts.get("SELL", 0),
            "short": dir_counts.get("SHORT", 0),
            "hold": dir_counts.get("HOLD", 0),
            "top_bullish": [{"ticker": s["ticker"], "score": s.get("composite_score", 0)} for s in top_bull],
            "top_bearish": [{"ticker": s["ticker"], "score": s.get("composite_score", 0)} for s in top_bear],
            "as_of": snapshot_as_of,
            "is_delayed": True,
        }
    except Exception:
        overview["market"] = {"universe": 0, "buy": 0, "sell": 0, "short": 0, "hold": 0, "top_bullish": [], "top_bearish": []}

    # 6. Oracle
    try:
        from tools.trading.oracle.db import list_predictions, list_convergence_events
        preds = list_predictions(outcome="pending", limit=5)
        convs = list_convergence_events(limit=5)
        overview["oracle"] = {
            "predictions": len(preds),
            "top_predictions": preds[:3],
            "convergence_events": len(convs),
            "top_convergence": convs[:2],
        }
    except Exception:
        overview["oracle"] = {"predictions": 0, "top_predictions": [], "convergence_events": 0, "top_convergence": []}

    # 7. News Intelligence
    try:
        from tools.trading.news.db import list_active_clusters
        from tools.trading.news.news_reasoner import detect_divergences
        from tools.trading.data.macro_data import fetch_macro_context
        clusters = list_active_clusters()
        macro = fetch_macro_context()
        divs = detect_divergences(macro, clusters)
        from collections import Counter
        status_counts = Counter(c.get("status", "?") for c in clusters)
        cat_counts = Counter(c.get("category", "?") for c in clusters)
        dominant_cat = cat_counts.most_common(1)[0] if cat_counts else ("none", 0)
        overview["news"] = {
            "regime_clusters": status_counts.get("regime", 0),
            "emerging_clusters": status_counts.get("emerging", 0),
            "divergences": len(divs),
            "divergence_list": divs[:3],
            "dominant_category": dominant_cat[0],
        }
    except Exception:
        overview["news"] = {"regime_clusters": 0, "emerging_clusters": 0, "divergences": 0, "divergence_list": [], "dominant_category": "—"}

    # 8. Expert Consensus
    try:
        from tools.trading.market_intel.expert_agents import get_latest_recommendations
        recs = get_latest_recommendations(limit=5)
        overview["expert"] = {"recommendations": recs}
    except Exception:
        try:
            from tools.trading.db import get_conn
            conn = get_conn()
            rows = conn.execute(
                "SELECT ticker, final_direction AS direction, final_conviction AS conviction "
                "FROM ad_cis_recommendations ORDER BY created_at DESC LIMIT 5"
            ).fetchall()
            conn.close()
            overview["expert"] = {"recommendations": [dict(r) for r in rows]}
        except Exception:
            overview["expert"] = {"recommendations": []}

    # 9. Strategy
    try:
        from tools.trading.db import get_conn
        conn = get_conn()
        strat = conn.execute(
            "SELECT id, regime, created_at FROM ad_strategy_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        holdings = []
        if strat:
            holdings = conn.execute(
                "SELECT tier, COUNT(*) as cnt FROM ad_strategy_holdings WHERE run_id=%s GROUP BY tier",
                (dict(strat)["id"],)
            ).fetchall()
        conn.close()
        tier_counts = {dict(h)["tier"]: dict(h)["cnt"] for h in holdings} if holdings else {}
        overview["strategy"] = {
            "last_run": dict(strat)["created_at"] if strat else None,
            "regime": dict(strat)["regime"] if strat else None,
            "core": tier_counts.get("core", 0),
            "tactical": tier_counts.get("tactical", 0),
            "opportunistic": tier_counts.get("opportunistic", 0),
            "hedge": tier_counts.get("hedge", 0),
        }
    except Exception:
        overview["strategy"] = {"last_run": None, "regime": None, "core": 0, "tactical": 0, "opportunistic": 0, "hedge": 0}

    _set_cache("overview_agg", overview)
    return jsonify(overview)


# ---------------------------------------------------------------------------
# API — Portfolio
# ---------------------------------------------------------------------------
@app.route("/api/portfolio")
def api_portfolio():
    from tools.trading.db import get_conn
    uid = _active_uid()
    tid = _active_tenant_id()

    conn = get_conn()
    # Phase 3+ multi-tenant: scope to active user + tenant
    portfolio = conn.execute(
        "SELECT * FROM ad_portfolios WHERE user_id=%s AND tenant_id=%s LIMIT 1",
        (uid, tid),
    ).fetchone()
    if not portfolio:
        conn.execute(
            "INSERT INTO ad_portfolios (id, user_id, tenant_id, name, cash_balance) "
            "VALUES (%s, %s, %s, 'Default', 100000.0)",
            (f"pf-{uid[:10]}", uid, tid),
        )
        conn.commit()
        portfolio = conn.execute(
            "SELECT * FROM ad_portfolios WHERE user_id=%s AND tenant_id=%s LIMIT 1",
            (uid, tid),
        ).fetchone()

    positions = conn.execute(
        "SELECT * FROM ad_positions WHERE portfolio_id=%s AND qty > 0",
        (portfolio["id"],),
    ).fetchall()

    total_value = portfolio["cash_balance"] + sum(p["market_value"] for p in positions)
    unrealized = sum(p["unrealized_pnl"] for p in positions)
    conn.close()

    return jsonify(
        {
            "cash_balance": portfolio["cash_balance"],
            "total_value": total_value,
            "positions": [dict(p) for p in positions],
            "daily_pnl": unrealized,
            "daily_pnl_pct": (unrealized / total_value * 100 if total_value > 0 else 0),
        }
    )


# ---------------------------------------------------------------------------
# Portfolio analytics — live state, SPY benchmark simulation, risk metrics
# ---------------------------------------------------------------------------

_PORTFOLIO_HIST_CACHE: dict = {}  # period -> (expires_at, payload)


def _ensure_portfolio_snapshots_table():
    """DDL for the daily-snapshot timeseries used by /api/portfolio/history.

    Uses `ad_pf_daily_snapshots` — a different legacy table
    `ad_pf_daily_snapshots` already exists with a run-id-keyed shape for
    expert-advisor runs, so we avoid the name collision.
    """
    from tools.trading.db import get_conn
    conn = get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ad_pf_daily_snapshots (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'default',
                tenant_id TEXT NOT NULL DEFAULT 'default',
                snapshot_date TEXT NOT NULL,
                cash_balance REAL NOT NULL,
                positions_value REAL NOT NULL,
                total_value REAL NOT NULL,
                positions_json TEXT,
                spy_price REAL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_pfdaily_date "
            "ON ad_pf_daily_snapshots(snapshot_date)"
        )
        conn.commit()
    finally:
        conn.close()


def _snapshot_portfolio_now() -> dict:
    """Write a snapshot row for today. Upserts on snapshot_date so re-runs
    during the same day overwrite rather than duplicate.

    Pulls SPY close alongside so the history endpoint has a matched benchmark
    without needing a separate download."""
    import uuid as _uuid
    _ensure_portfolio_snapshots_table()

    cash, positions, total = _current_portfolio()
    positions_value = round(total - cash, 2)

    # Today's SPY close (best-effort)
    spy_price = None
    try:
        import yfinance as yf
        h = yf.download("SPY", period="5d", progress=False, auto_adjust=False)
        closes = h["Close"] if hasattr(h, "get") else None
        if closes is not None and hasattr(closes, "dropna"):
            s = closes.dropna()
            if len(s):
                spy_price = round(float(s.iloc[-1]), 2)
    except Exception:
        pass

    # Trim position data down to what we need to reconstruct later
    positions_slim = [
        {"ticker": p.get("ticker"), "qty": p.get("qty"),
         "avg_cost": p.get("avg_cost"), "last_price": p.get("last_price"),
         "market_value": p.get("market_value")}
        for p in (positions or [])
    ]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from tools.trading.db import get_conn
    uid = _active_uid()
    tid = _active_tenant_id()
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM ad_pf_daily_snapshots "
            "WHERE user_id=%s AND tenant_id=%s AND snapshot_date=%s",
            (uid, tid, today),
        ).fetchone()
        if existing:
            sid = existing[0] if not hasattr(existing, "keys") else existing["id"]
            conn.execute(
                "UPDATE ad_pf_daily_snapshots SET cash_balance=%s, positions_value=%s, "
                "total_value=%s, positions_json=%s, spy_price=%s, created_at=%s "
                "WHERE id=%s AND user_id=%s AND tenant_id=%s",
                (round(cash, 2), positions_value, round(total, 2),
                 json.dumps(positions_slim), spy_price,
                 datetime.now(timezone.utc).isoformat(), sid, uid, tid),
            )
        else:
            conn.execute(
                "INSERT INTO ad_pf_daily_snapshots "
                "(id, user_id, tenant_id, snapshot_date, cash_balance, positions_value, "
                "total_value, positions_json, spy_price, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (f"pfs-{_uuid.uuid4().hex[:12]}", uid, tid, today, round(cash, 2),
                 positions_value, round(total, 2),
                 json.dumps(positions_slim), spy_price,
                 datetime.now(timezone.utc).isoformat()),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "snapshot_date": today, "cash": round(cash, 2),
        "positions_value": positions_value, "total_value": round(total, 2),
        "spy_price": spy_price, "positions_count": len(positions_slim),
    }


@app.route("/api/portfolio/snapshot/now", methods=["POST"])
def api_portfolio_snapshot_now():
    """Trigger a portfolio snapshot right now. The daily reflex also calls this."""
    try:
        return jsonify(_snapshot_portfolio_now())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _fetch_snapshot_series(period: str):
    """Return (dates, portfolio_values, spy_values) from ad_pf_daily_snapshots
    for the requested period. Returns None when there aren't enough rows."""
    _ensure_portfolio_snapshots_table()
    period_days = {"1mo": 32, "3mo": 95, "6mo": 190, "1y": 370, "2y": 740}.get(period, 190)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=period_days)).strftime("%Y-%m-%d")
    from tools.trading.db import get_conn
    uid = _active_uid()
    tid = _active_tenant_id()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT snapshot_date, total_value, spy_price "
            "FROM ad_pf_daily_snapshots WHERE user_id=%s AND tenant_id=%s AND snapshot_date >= %s "
            "ORDER BY snapshot_date ASC",
            (uid, tid, cutoff),
        ).fetchall()
    finally:
        conn.close()
    if len(rows) < 5:
        return None  # not enough history yet — caller will fall back to simulation
    dates, pv, spy = [], [], []
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else {"snapshot_date": r[0], "total_value": r[1], "spy_price": r[2]}
        dates.append(d["snapshot_date"])
        pv.append(float(d["total_value"]))
        if d.get("spy_price") is not None:
            spy.append(float(d["spy_price"]))
    # If SPY is missing for some rows, drop the whole series — history endpoint needs both
    if len(spy) != len(pv):
        return None
    return dates, pv, spy


def _current_portfolio():
    """Return (cash_balance, positions_list, total_value) live from the DB.

    Positions are re-marked at the latest available price (falls back to
    the stored market_value if yfinance can't answer)."""
    from tools.trading.db import get_conn
    uid = _active_uid()
    tid = _active_tenant_id()
    conn = get_conn()
    pf = conn.execute(
        "SELECT * FROM ad_portfolios WHERE user_id=%s AND tenant_id=%s LIMIT 1",
        (uid, tid),
    ).fetchone()
    if not pf:
        conn.execute(
            "INSERT INTO ad_portfolios (id, user_id, tenant_id, name, cash_balance) "
            "VALUES (%s, %s, %s, 'Default', 100000.0)",
            (f"pf-{uid[:10]}", uid, tid),
        )
        conn.commit()
        pf = conn.execute(
            "SELECT * FROM ad_portfolios WHERE user_id=%s AND tenant_id=%s LIMIT 1",
            (uid, tid),
        ).fetchone()
    rows = conn.execute(
        "SELECT * FROM ad_positions WHERE portfolio_id=%s AND qty > 0",
        (pf["id"],),
    ).fetchall()
    conn.close()

    positions = [dict(r) for r in rows]
    # Mark-to-market with live quotes where possible
    if positions:
        try:
            import yfinance as yf
            tickers = [p["ticker"] for p in positions]
            q = yf.download(tickers, period="5d", progress=False, auto_adjust=False)
            if hasattr(q.columns, "get_level_values") and "Close" in q.columns.get_level_values(0):
                closes = q["Close"]
            else:
                closes = q.get("Close") if hasattr(q, "get") else None
        except Exception:
            closes = None
        for p in positions:
            try:
                tkr = p["ticker"]
                last = None
                if closes is not None:
                    if hasattr(closes, "columns") and tkr in closes.columns:
                        s = closes[tkr].dropna()
                        last = float(s.iloc[-1]) if len(s) else None
                    elif hasattr(closes, "dropna"):
                        s = closes.dropna()
                        last = float(s.iloc[-1]) if len(s) else None
                if last is not None and last > 0:
                    p["last_price"] = round(last, 2)
                    p["market_value"] = round(p["qty"] * last, 2)
                    p["unrealized_pnl"] = round(p["qty"] * (last - p["avg_cost"]), 2)
                    p["unrealized_pnl_pct"] = round((last / p["avg_cost"] - 1) * 100, 2) if p["avg_cost"] else 0
            except Exception:
                pass

    cash = float(pf["cash_balance"] or 0)
    positions_value = sum(float(p.get("market_value") or 0) for p in positions)
    total = cash + positions_value
    return cash, positions, total


@app.route("/api/portfolio/held-tickers")
def api_portfolio_held_tickers():
    """Return {TICKER: true} for every open position. Used by pages to badge held tickers."""
    try:
        cash, positions, total = _current_portfolio()
        return jsonify({p["ticker"]: True for p in positions if (p.get("qty") or 0) != 0})
    except Exception:
        return jsonify({})


@app.route("/api/portfolio/state")
def api_portfolio_state():
    """Live portfolio state: cash, positions (marked to current prices),
    total value, daily P&L, weights. Drives the /portfolio top cards + table.
    """
    try:
        cash, positions, total = _current_portfolio()
        unrealized = sum(float(p.get("unrealized_pnl") or 0) for p in positions)
        # Weights by market value
        for p in positions:
            mv = float(p.get("market_value") or 0)
            p["weight_pct"] = round(mv / total * 100, 2) if total > 0 else 0
        # Largest concentration
        max_weight = max([p.get("weight_pct", 0) for p in positions], default=0)
        return jsonify({
            "cash_balance": round(cash, 2),
            "positions_value": round(total - cash, 2),
            "total_value": round(total, 2),
            "positions": positions,
            "position_count": len(positions),
            "unrealized_pnl": round(unrealized, 2),
            "unrealized_pnl_pct": round(unrealized / (total - unrealized) * 100, 2)
                if (total - unrealized) > 0 else 0,
            "max_concentration_pct": round(max_weight, 2),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _compute_portfolio_history(period: str):
    """Walk the current positions forward over a historical window, computing
    daily total portfolio value. Returns (dates, portfolio_values, spy_values)
    as aligned arrays, plus the current weights used.

    Approach:
      1. Fetch close-price history for all held tickers + SPY via yf.download.
      2. For each trading day in the window, portfolio_value = cash +
         Σ(qty_i × close_i). Uses *current* quantities (backward-looking
         simulation) because we don't have historical-position snapshots yet.
    """
    cash, positions, total = _current_portfolio()
    if not positions:
        return None, "no positions held"

    import yfinance as yf
    tickers = [p["ticker"] for p in positions] + ["SPY"]
    try:
        data = yf.download(tickers, period=period, progress=False, auto_adjust=False)
    except Exception as e:
        return None, f"yfinance download failed: {e}"
    if data is None or data.empty:
        return None, "no historical price data"

    closes = data["Close"] if "Close" in data.columns.get_level_values(0) else data
    # Single-ticker case returns a Series, multi-ticker returns a DataFrame
    if not hasattr(closes, "columns"):
        # Only one ticker held + SPY — download returns multiindex; normalize
        pass

    # Drop rows where any required ticker is NaN
    closes = closes.dropna()
    if closes.empty:
        return None, "no overlapping price history"

    dates = [d.strftime("%Y-%m-%d") for d in closes.index]
    portfolio_values = []
    for i in range(len(closes)):
        val = cash
        for p in positions:
            try:
                price = float(closes[p["ticker"]].iloc[i])
                val += p["qty"] * price
            except Exception:
                # Position ticker missing in download — assume stored value
                val += float(p.get("market_value") or 0)
        portfolio_values.append(round(val, 2))

    try:
        spy_values = [round(float(closes["SPY"].iloc[i]), 2) for i in range(len(closes))]
    except Exception:
        spy_values = []

    return {
        "dates": dates,
        "portfolio_values": portfolio_values,
        "spy_values": spy_values,
        "current_cash": round(cash, 2),
        "current_total": round(total, 2),
        "positions": [{"ticker": p["ticker"], "qty": p["qty"],
                       "weight_pct": round((float(p.get("market_value") or 0)) / total * 100, 2) if total > 0 else 0}
                      for p in positions],
    }, None


def _compute_metrics(portfolio_values, spy_values):
    """Derive Sharpe, Sortino, Calmar, max DD, alpha, beta from a daily
    portfolio value series and matching SPY series."""
    n = len(portfolio_values)
    if n < 5:
        return {"error": f"need at least 5 bars of history (have {n})"}

    # Daily simple returns
    def _returns(series):
        return [(series[i] / series[i-1] - 1) for i in range(1, len(series))]
    p_ret = _returns(portfolio_values)
    s_ret = _returns(spy_values) if spy_values and len(spy_values) == n else []

    import math
    def _mean(arr):  return sum(arr) / len(arr) if arr else 0.0
    def _stdev(arr):
        if len(arr) < 2: return 0.0
        m = _mean(arr)
        return math.sqrt(sum((x - m) ** 2 for x in arr) / (len(arr) - 1))
    def _cov(a, b):
        if len(a) != len(b) or len(a) < 2: return 0.0
        ma, mb = _mean(a), _mean(b)
        return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (len(a) - 1)

    rf_annual = 0.04                        # 4% risk-free assumption
    rf_daily = rf_annual / 252

    mean_p = _mean(p_ret)
    std_p = _stdev(p_ret)
    sharpe = ((mean_p - rf_daily) / std_p * math.sqrt(252)) if std_p > 0 else None

    downside = [r for r in p_ret if r < 0]
    dd_stdev = _stdev(downside)
    sortino = ((mean_p - rf_daily) / dd_stdev * math.sqrt(252)) if dd_stdev > 0 else None

    # Max drawdown
    peak = portfolio_values[0]; max_dd = 0.0
    for v in portfolio_values:
        if v > peak: peak = v
        dd = (v - peak) / peak
        if dd < max_dd: max_dd = dd

    # Annualized return + Calmar
    total_return = portfolio_values[-1] / portfolio_values[0] - 1
    years = n / 252
    annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else total_return
    calmar = (annual_return / abs(max_dd)) if max_dd < 0 else None

    # Beta / alpha vs SPY
    beta = None; alpha_annual = None; spy_total = None
    if s_ret:
        var_s = _stdev(s_ret) ** 2
        if var_s > 0:
            beta = _cov(p_ret, s_ret) / var_s
            mean_s = _mean(s_ret)
            alpha_daily = mean_p - beta * mean_s
            alpha_annual = alpha_daily * 252
        spy_total = spy_values[-1] / spy_values[0] - 1

    def _round(v, nd=3): return None if v is None else round(v, nd)
    return {
        "bars": n,
        "total_return_pct":   _round(total_return * 100, 2),
        "spy_return_pct":     _round(spy_total * 100, 2) if spy_total is not None else None,
        "outperformance_pct": _round((total_return - (spy_total or 0)) * 100, 2) if spy_total is not None else None,
        "annual_return_pct":  _round(annual_return * 100, 2),
        "sharpe":             _round(sharpe),
        "sortino":            _round(sortino),
        "calmar":             _round(calmar),
        "max_drawdown_pct":   _round(max_dd * 100, 2),
        "volatility_annual_pct": _round(std_p * math.sqrt(252) * 100, 2),
        "beta":               _round(beta),
        "alpha_annual_pct":   _round(alpha_annual * 100 if alpha_annual is not None else None, 2),
    }


def _ensure_watchlist_table():
    from tools.trading.db import get_conn
    conn = get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ad_watchlists (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'default',
                ticker TEXT NOT NULL,
                notes TEXT,
                added_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlist_user_ticker "
            "ON ad_watchlists(user_id, ticker)"
        )
        conn.commit()
    finally:
        conn.close()


@app.route("/api/watchlist", methods=["GET"])
def api_watchlist_list():
    """List starred tickers with live enrichment: latest signal + 24h news impact.

    Joins ad_watchlists × ad_market_snapshot × ad_news_impact_traces, so one
    call returns everything the /watchlist page needs.
    """
    _ensure_watchlist_table()
    try:
        from tools.trading.db import get_conn
        conn = get_conn()
        rows = conn.execute(
            "SELECT w.ticker, w.notes, w.added_at, "
            "       s.direction, s.composite_score, s.confidence, s.regime, "
            "       s.kg_supply_chain, s.kg_competitors, s.kg_centrality, s.sector "
            "FROM ad_watchlists w "
            "LEFT JOIN ad_market_snapshot s ON s.ticker = w.ticker "
            "WHERE w.user_id = 'default' "
            "ORDER BY w.added_at DESC"
        ).fetchall()
        conn.close()
    except Exception as e:
        return jsonify({"error": str(e), "items": []}), 500

    items = []
    tickers = []
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else {}
        if not d:
            continue
        items.append(d)
        tickers.append(d["ticker"])

    # News-impact rollup (last 24h) per ticker
    try:
        from tools.trading.news.impact_store import get_recent_impact
        for it in items:
            ni = get_recent_impact(it["ticker"], hours=24)
            it["news_impact_24h"] = ni.get("total_score", 0)
            it["news_trace_count_24h"] = ni.get("trace_count", 0)
    except Exception:
        for it in items:
            it["news_impact_24h"] = None

    # Live prices via yfinance (best-effort, single batched call)
    if tickers:
        try:
            import yfinance as yf
            h = yf.download(tickers, period="5d", progress=False, auto_adjust=False)
            closes = h["Close"] if hasattr(h.columns, "get_level_values") and "Close" in h.columns.get_level_values(0) else h.get("Close")
            for it in items:
                try:
                    tkr = it["ticker"]
                    if closes is not None and hasattr(closes, "columns") and tkr in closes.columns:
                        s = closes[tkr].dropna()
                    elif closes is not None and hasattr(closes, "dropna"):
                        s = closes.dropna()
                    else:
                        continue
                    if len(s) >= 2:
                        it["last_price"] = round(float(s.iloc[-1]), 2)
                        it["prev_close"] = round(float(s.iloc[-2]), 2)
                        it["price_change_pct"] = round(
                            (it["last_price"] - it["prev_close"]) / it["prev_close"] * 100, 2
                        ) if it["prev_close"] else 0
                except Exception:
                    pass
        except Exception:
            pass

    return jsonify({"items": items, "count": len(items)})


@app.route("/api/watchlist/<ticker>", methods=["POST"])
def api_watchlist_add(ticker):
    """Add a ticker to the watchlist. Upsert by (user, ticker)."""
    _ensure_watchlist_table()
    t = (ticker or "").upper().strip()
    if not t:
        return jsonify({"error": "empty ticker"}), 400
    body = request.get_json(silent=True) or {}
    notes = body.get("notes") or ""
    try:
        import uuid
        from tools.trading.db import get_conn
        conn = get_conn()
        # Upsert: if it exists, just update notes + added_at
        exists = conn.execute(
            "SELECT id FROM ad_watchlists WHERE user_id='default' AND ticker=%s LIMIT 1",
            (t,),
        ).fetchone()
        if exists:
            wid = exists[0] if not hasattr(exists, "keys") else exists["id"]
            conn.execute(
                "UPDATE ad_watchlists SET notes=%s, added_at=%s WHERE id=%s",
                (notes, datetime.now(timezone.utc).isoformat(), wid),
            )
        else:
            conn.execute(
                "INSERT INTO ad_watchlists (id, user_id, ticker, notes, added_at) "
                "VALUES (%s, 'default', %s, %s, %s)",
                (f"wl-{uuid.uuid4().hex[:12]}", t, notes, datetime.now(timezone.utc).isoformat()),
            )
        conn.commit()
        conn.close()
        return jsonify({"status": "ok", "ticker": t, "starred": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/watchlist/<ticker>", methods=["DELETE"])
def api_watchlist_remove(ticker):
    _ensure_watchlist_table()
    t = (ticker or "").upper().strip()
    if not t:
        return jsonify({"error": "empty ticker"}), 400
    try:
        from tools.trading.db import get_conn
        conn = get_conn()
        conn.execute(
            "DELETE FROM ad_watchlists WHERE user_id='default' AND ticker=%s",
            (t,),
        )
        conn.commit()
        conn.close()
        return jsonify({"status": "ok", "ticker": t, "starred": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/watchlist/check")
def api_watchlist_check():
    """Batch check: ?tickers=AAPL,NVDA,TSLA → {AAPL:true, NVDA:false, TSLA:true}"""
    _ensure_watchlist_table()
    raw = (request.args.get("tickers") or "").strip()
    if not raw:
        return jsonify({})
    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
    try:
        from tools.trading.db import get_conn
        conn = get_conn()
        placeholders = ",".join(["?"] * len(tickers))
        rows = conn.execute(
            f"SELECT ticker FROM ad_watchlists WHERE user_id='default' AND ticker IN ({placeholders})",
            tuple(tickers),
        ).fetchall()
        starred = {r[0] if not hasattr(r, "keys") else r["ticker"] for r in rows}
        conn.close()
        return jsonify({t: (t in starred) for t in tickers})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/watchlist/reading")
def api_watchlist_reading():
    """Generate a PM-style reading of the watchlist: overall mood, notable
    movers, news-driven pressure. Uses the same reading pattern as other pages.
    """
    _ensure_watchlist_table()
    try:
        # Inline the fetch so we don't recurse through the HTTP layer
        from tools.trading.db import get_conn
        from tools.trading.news.impact_store import get_recent_impact
        conn = get_conn()
        rows = conn.execute(
            "SELECT w.ticker, w.notes, s.direction, s.composite_score, s.confidence "
            "FROM ad_watchlists w LEFT JOIN ad_market_snapshot s ON s.ticker = w.ticker "
            "WHERE w.user_id='default'"
        ).fetchall()
        conn.close()
        items = [dict(r) if hasattr(r, "keys") else {} for r in rows]
        for it in items:
            ni = get_recent_impact(it.get("ticker",""), hours=24)
            it["news_impact"] = ni.get("total_score", 0)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    from tools.trading.analytics.reading_config import cfg as _cfg, regime_set as _rset
    W = _cfg("watchlist")
    mood_t = _cfg("mood")
    risk_off = _rset("risk_off")
    risk_on = _rset("risk_on")
    bp_t = W["bull_pct"]
    avg_t = W["avg_score"]
    ni_t = W["news_impact"]

    observations = []
    considerations = []
    if not items:
        return jsonify({
            "profile": "Watchlist empty — no tickers starred yet",
            "summary": "Add tickers by clicking ⭐ on any ticker appearance.",
            "observations": [],
            "considerations": [
                "Use Cmd+K to search and star tickers quickly",
                "On /market, click the ⭐ on any heatmap cell to add it",
            ],
            "tone_counts": {"bullish": 0, "bearish": 0, "neutral": 0, "caution": 0},
            "regime": {},
        })

    scored = [it for it in items if it.get("composite_score") is not None]
    bull = sum(1 for it in scored if it.get("direction") == "BUY")
    bear = sum(1 for it in scored if it.get("direction") in ("SELL", "SHORT"))
    avg_score = (sum(float(it.get("composite_score") or 50) for it in scored) / len(scored)) if scored else 50
    news_positives = [it for it in items if (it.get("news_impact") or 0) > ni_t["significant"]]
    news_negatives = [it for it in items if (it.get("news_impact") or 0) < -ni_t["significant"]]

    # --- Observations
    if scored:
        bull_pct = bull / len(scored) * 100
        if bull_pct >= bp_t["bullish_min"]:
            observations.append({"text": f"Watchlist skewed bullish: {bull}/{len(scored)} BUY signals ({bull_pct:.0f}%)", "tone": "bullish", "metric": "bull_pct", "value": bull_pct})
        elif bull_pct <= bp_t["bearish_max"]:
            observations.append({"text": f"Watchlist skewed bearish: only {bull_pct:.0f}% BUY", "tone": "caution", "metric": "bull_pct", "value": bull_pct})
        else:
            observations.append({"text": f"Watchlist balanced: {bull} BUY · {bear} SELL/SHORT across {len(scored)} scored", "tone": "neutral", "metric": "bull_pct", "value": bull_pct})

    if avg_score >= avg_t["strong_min"]:
        observations.append({"text": f"Avg composite score {avg_score:.1f} — watchlist is currently strong", "tone": "bullish", "metric": "avg_score", "value": avg_score})
    elif avg_score <= avg_t["weak_max"]:
        observations.append({"text": f"Avg composite score {avg_score:.1f} — watchlist is currently weak", "tone": "bearish", "metric": "avg_score", "value": avg_score})

    if news_positives:
        top = max(news_positives, key=lambda x: x.get("news_impact") or 0)
        observations.append({"text": f"{top['ticker']} strongest news tailwind: Σ {top['news_impact']:+.2f} over 24h", "tone": "bullish", "metric": "top_news_winner", "value": top.get("news_impact")})
    if news_negatives:
        worst = min(news_negatives, key=lambda x: x.get("news_impact") or 0)
        observations.append({"text": f"{worst['ticker']} worst news headwind: Σ {worst['news_impact']:+.2f} over 24h", "tone": "bearish", "metric": "top_news_loser", "value": worst.get("news_impact")})
        if abs(worst.get("news_impact") or 0) >= ni_t["severe"]:
            considerations.append(f"Review {worst['ticker']} exposure — 24h news-flow impact is severe")

    unscored = len(items) - len(scored)
    if unscored > 0:
        observations.append({"text": f"{unscored} watchlisted ticker(s) not yet scanned by market_scanner", "tone": "neutral", "metric": "unscored", "value": unscored})
        considerations.append("Trigger a market_scanner run to populate signals for all watchlisted names")

    # Regime alignment
    regime_block = {}
    try:
        from tools.trading.data.macro_data import fetch_macro_context
        regime = (fetch_macro_context().get("regime") or "").upper()
        if regime:
            bull_pct = bull / len(scored) * 100 if scored else 50
            if regime in risk_off and bull_pct >= bp_t["bullish_min"] - 10:
                alignment = "mismatch"
                considerations.append(f"Regime {regime} is risk-off but watchlist is bullish — filter to defensive BUYs only")
            elif regime in risk_on and bull_pct <= avg_t["weak_max"]:
                alignment = "mismatch"
                considerations.append(f"Regime {regime} is risk-on but watchlist is bearish — scan for growth/cyclical adds")
            else:
                alignment = "match" if bull_pct >= 50 else "neutral"
            regime_block = {"label": regime, "alignment": alignment}
    except Exception:
        pass

    # Profile
    if len(items) <= 3:
        profile = f"{len(items)} starred · focused list"
    elif len(items) <= 10:
        profile = f"{len(items)} starred · focused coverage"
    else:
        profile = f"{len(items)} starred · broad coverage"
    headwind_threshold = ni_t["significant"] * 2
    if news_negatives and max(abs(it.get('news_impact') or 0) for it in news_negatives) >= headwind_threshold:
        profile += " · news headwinds active"
    elif news_positives and max(it.get('news_impact') or 0 for it in news_positives) >= headwind_threshold:
        profile += " · news tailwinds active"

    # Tone counts
    tc = {"bullish": 0, "bearish": 0, "neutral": 0, "caution": 0}
    for o in observations: tc[o.get("tone", "neutral")] = tc.get(o.get("tone", "neutral"), 0) + 1
    net = tc["bullish"] - tc["bearish"] - tc["caution"] * 0.5
    mood = ("constructive" if net >= mood_t["constructive_min"]
            else "cautious" if net >= mood_t["cautious_min"]
            else "concerning")
    summary = (f"{profile}. Reading is {mood}: {tc['bullish']} positives, "
               f"{tc['bearish']} negatives, {tc['caution']} cautions across {len(observations)} observations.")

    payload = {
        "profile": profile,
        "summary": summary,
        "observations": observations,
        "considerations": considerations,
        "tone_counts": tc,
        "regime": regime_block,
    }
    from tools.trading.analytics.reading_voice import apply_page_voice
    from tools.trading.profile import db as pdb
    return jsonify(apply_page_voice(payload, "watchlist", pdb.get_profile(_active_uid()) or {}))


@app.route("/watchlist")
def page_watchlist():
    return render_template("watchlist.html")


@app.route("/api/market/reading")
def api_market_reading():
    try:
        from tools.trading.analytics.page_readings import generate_market_reading
        from tools.trading.analytics.reading_voice import apply_page_voice
        from tools.trading.profile import db as pdb
        r = generate_market_reading()
        r = apply_page_voice(r, "market", pdb.get_profile(_active_uid()) or {})
        r["generated_at"] = datetime.now(timezone.utc).isoformat()
        return jsonify(r)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/signals/reading")
def api_signals_reading():
    try:
        from tools.trading.analytics.page_readings import generate_signals_reading
        from tools.trading.analytics.reading_voice import apply_page_voice
        from tools.trading.profile import db as pdb
        r = generate_signals_reading()
        r = apply_page_voice(r, "signals", pdb.get_profile(_active_uid()) or {})
        r["generated_at"] = datetime.now(timezone.utc).isoformat()
        return jsonify(r)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/news/reading")
def api_news_reading():
    try:
        from tools.trading.analytics.page_readings import generate_news_reading
        from tools.trading.analytics.reading_voice import apply_page_voice
        from tools.trading.profile import db as pdb
        r = generate_news_reading()
        r = apply_page_voice(r, "news", pdb.get_profile(_active_uid()) or {})
        r["generated_at"] = datetime.now(timezone.utc).isoformat()
        return jsonify(r)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/radar/reading")
def api_radar_reading():
    try:
        from tools.trading.analytics.page_readings import generate_radar_reading
        from tools.trading.analytics.reading_voice import apply_page_voice
        from tools.trading.profile import db as pdb
        r = generate_radar_reading()
        r = apply_page_voice(r, "radar", pdb.get_profile(_active_uid()) or {})
        r["generated_at"] = datetime.now(timezone.utc).isoformat()
        return jsonify(r)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/oracle/reading")
def api_oracle_reading():
    try:
        from tools.trading.analytics.page_readings import generate_oracle_reading
        from tools.trading.analytics.reading_voice import apply_page_voice
        from tools.trading.profile import db as pdb
        r = generate_oracle_reading()
        r = apply_page_voice(r, "oracle", pdb.get_profile(_active_uid()) or {})
        r["generated_at"] = datetime.now(timezone.utc).isoformat()
        return jsonify(r)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/evolution/reading/<ticker>")
def api_evolution_reading(ticker):
    try:
        from tools.trading.analytics.page_readings import generate_evolution_reading
        from tools.trading.analytics.reading_voice import apply_page_voice
        from tools.trading.profile import db as pdb
        r = generate_evolution_reading(ticker)
        r = apply_page_voice(r, "evolution", pdb.get_profile(_active_uid()) or {})
        r["generated_at"] = datetime.now(timezone.utc).isoformat()
        return jsonify(r)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/advisor/reading")
def api_advisor_reading():
    try:
        from tools.trading.analytics.page_readings import generate_advisor_reading
        from tools.trading.analytics.reading_voice import apply_page_voice
        from tools.trading.profile import db as pdb
        r = generate_advisor_reading()
        r = apply_page_voice(r, "advisor", pdb.get_profile(_active_uid()) or {})
        r["generated_at"] = datetime.now(timezone.utc).isoformat()
        return jsonify(r)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/strategist/reading")
def api_strategist_reading():
    try:
        from tools.trading.analytics.page_readings import generate_strategist_reading
        from tools.trading.analytics.reading_voice import apply_page_voice
        from tools.trading.profile import db as pdb
        r = generate_strategist_reading()
        r = apply_page_voice(r, "strategist", pdb.get_profile(_active_uid()) or {})
        r["generated_at"] = datetime.now(timezone.utc).isoformat()
        return jsonify(r)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/portfolio/reading")
def api_portfolio_reading():
    """PM-style narrative reading of the portfolio: profile + observations +
    considerations + regime alignment. Derived deterministically from the
    metrics in /api/portfolio/history and the current macro regime, so the
    same inputs always yield the same reading.
    """
    period = request.args.get("period", "6mo")
    # Reuse the history+metrics pipeline (hot-cached)
    state_resp = api_portfolio_state().get_json()
    if state_resp.get("error"):
        return jsonify({"error": state_resp["error"]}), 500

    hist_result, err = _compute_portfolio_history(period)
    if err or not hist_result:
        return jsonify({
            "profile": "No holdings — no reading",
            "observations": [],
            "considerations": [
                "Run an analysis on a ticker and approve a high-conviction "
                "BUY signal to put capital to work."
            ],
            "summary": "Empty book; nothing to read.",
            "regime": {},
            "period": period,
            "error": err,
        })
    metrics = _compute_metrics(hist_result["portfolio_values"],
                                hist_result["spy_values"])

    # Current macro regime
    regime_label = None
    try:
        from tools.trading.data.macro_data import fetch_macro_context
        m = fetch_macro_context()
        regime_label = m.get("regime")
    except Exception:
        pass

    from tools.trading.analytics.portfolio_reading import generate_reading
    from tools.trading.analytics.reading_voice import apply_page_voice
    from tools.trading.profile import db as pdb
    reading = generate_reading(state_resp, metrics, regime_label)
    reading = apply_page_voice(reading, "portfolio", pdb.get_profile(_active_uid()) or {})
    reading["period"] = period
    reading["generated_at"] = datetime.now(timezone.utc).isoformat()
    return jsonify(reading)


_pma_bg_lock = __import__("threading").Lock()
_pma_bg_running = False


def _pma_bg_run():
    global _pma_bg_running
    try:
        from tools.trading.agents.portfolio_manager import run as pma_run
        pma_run()
    except Exception as exc:
        get_logger(__name__).warning("PMA background run failed: %s", exc)
    finally:
        _pma_bg_running = False


@app.route("/api/portfolio/recommendations")
def api_portfolio_recommendations():
    """Portfolio Manager Agent recommendations — unified multi-signal conviction engine.

    GET /api/portfolio/recommendations          → latest pending recs
    GET /api/portfolio/recommendations?run=1    → kick off background PMA cycle,
                                                  return latest recs immediately
    GET /api/portfolio/recommendations?status=1 → running status + latest recs
    """
    global _pma_bg_running
    try:
        from tools.trading.agents.portfolio_manager import latest_recommendations
        if request.args.get("run"):
            with _pma_bg_lock:
                if not _pma_bg_running:
                    _pma_bg_running = True
                    import threading
                    threading.Thread(target=_pma_bg_run, daemon=True).start()
            recs = latest_recommendations(limit=50)
            actionable = [r for r in recs if r.get("action") in ("BUY", "ADD", "SELL", "REBALANCE")]
            return jsonify({
                "running": _pma_bg_running,
                "recommendations": actionable,
                "total": len(recs),
            })
        else:
            recs = latest_recommendations(limit=50)
            actionable = [r for r in recs if r.get("action") in ("BUY", "ADD", "SELL", "REBALANCE")]
            return jsonify({
                "running": _pma_bg_running,
                "recommendations": actionable,
                "total": len(recs),
            })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/intelligence/coverage")
def api_intelligence_coverage():
    """Intelligence coverage status across all data-sharing tables."""
    try:
        from tools.trading.agents.data_sync import coverage_status
        return jsonify(coverage_status())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/intelligence/sync", methods=["POST"])
def api_intelligence_sync():
    """Trigger a full intelligence data sync cycle.

    POST /api/intelligence/sync
    Body (optional): {"ops": ["macro_refresh","quality_backfill","pma_to_signals","approve_pma_buys"]}
    """
    try:
        from tools.trading.agents.data_sync import run as sync_run
        body = request.get_json(silent=True) or {}
        ops = body.get("ops") or None
        result = sync_run(ops=ops)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/portfolio/brief.pdf")
def api_portfolio_brief_pdf():
    """Render today's Portfolio Brief as a downloadable PDF.

    Pulls the same data the dashboard uses (state + history metrics +
    reading + macro regime), then renders a single-flowing-page PDF via
    ReportLab. Deterministic output — same inputs, same PDF.
    """
    from flask import Response

    period = request.args.get("period", "6mo")
    state = api_portfolio_state().get_json() or {}
    if state.get("error"):
        return jsonify({"error": state["error"]}), 500

    hist_result, err = _compute_portfolio_history(period)
    metrics = {}
    if not err and hist_result:
        metrics = _compute_metrics(hist_result["portfolio_values"],
                                    hist_result["spy_values"])

    regime_label = None
    try:
        from tools.trading.data.macro_data import fetch_macro_context
        regime_label = (fetch_macro_context() or {}).get("regime")
    except Exception:
        pass

    from tools.trading.analytics.portfolio_reading import generate_reading
    from tools.trading.analytics.portfolio_pdf import render_brief
    reading = generate_reading(state, metrics, regime_label)
    # Phase 3.3: pass tenant for white-label branding
    from flask import g
    tenant = getattr(g, "current_tenant", None)
    pdf_bytes = render_brief(state, metrics, reading,
                              regime=regime_label, period=period,
                              tenant=tenant)
    # File name uses tenant slug when white-labeled
    if tenant and tenant.get("white_label_enabled") and tenant.get("slug"):
        prefix = tenant["slug"]
    else:
        prefix = "fathomdesk"
    fname = f"{prefix}-portfolio-brief-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@app.route("/api/portfolio/history")
def api_portfolio_history():
    """Portfolio value time-series with SPY benchmark + risk metrics.

    Prefers REAL snapshots from `ad_pf_daily_snapshots` when there are at
    least 5 data points for the requested period. Falls back to the
    backward-looking simulation (current holdings × historical prices) when
    insufficient snapshot history exists yet.

    Response includes `source: 'real' | 'simulated'` so the UI can label it.

    Query params: period = 1mo | 3mo | 6mo | 1y | 2y (default 6mo).
    Cached 10 minutes per period.
    """
    import time as _time
    period = request.args.get("period", "6mo")
    if period not in ("1mo", "3mo", "6mo", "1y", "2y"):
        period = "6mo"
    hit = _PORTFOLIO_HIST_CACHE.get(period)
    if hit and hit[0] > _time.time():
        return jsonify(hit[1])

    # 1) Try real snapshots first
    snap = _fetch_snapshot_series(period)
    if snap is not None:
        dates, pv, spy = snap
        metrics = _compute_metrics(pv, spy)
        payload = {
            "dates": dates,
            "portfolio_values": pv,
            "spy_values": spy,
            "metrics": metrics,
            "period": period,
            "source": "real",
            "snapshot_count": len(pv),
            "current_total": pv[-1],
            "positions": [],  # snapshot series doesn't need per-position breakdown
        }
        _PORTFOLIO_HIST_CACHE[period] = (_time.time() + 600, payload)
        return jsonify(payload)

    # 2) Fall back to backward-looking simulation
    result, err = _compute_portfolio_history(period)
    if err:
        return jsonify({"error": err, "period": period, "source": "unavailable"})
    metrics = _compute_metrics(result["portfolio_values"], result["spy_values"])
    payload = {**result, "metrics": metrics, "period": period, "source": "simulated"}
    _PORTFOLIO_HIST_CACHE[period] = (_time.time() + 600, payload)
    return jsonify(payload)


# ---------------------------------------------------------------------------
# API — Signals (with approve/reject)
# ---------------------------------------------------------------------------
@app.route("/api/signals")
def api_signals():
    from tools.trading.db import get_signals

    status_filter = request.args.get("status")
    signals = get_signals(status=status_filter)
    pending = [s for s in signals if s["status"] == "pending"]

    # Attach latest CIS advisor recommendation per ticker
    advisor_map = _get_advisor_map()
    for s in signals:
        adv = advisor_map.get(s.get("ticker"))
        if adv:
            s["advisor_direction"] = adv["direction"]
            s["advisor_conviction"] = adv["conviction"]
            s["advisor_agreement"] = adv["agreement"]
        else:
            s["advisor_direction"] = None
            s["advisor_conviction"] = None
            s["advisor_agreement"] = None

    return jsonify(
        {
            "signals": signals,
            "pending_count": len(pending),
            "total_count": len(signals),
        }
    )


def _get_advisor_map() -> dict:
    """Get latest CIS recommendation per ticker."""
    try:
        from tools.trading.db import get_conn as get_icdev_conn

        conn = get_icdev_conn()
        rows = conn.execute(
            "SELECT r.ticker, r.final_direction, r.final_conviction, r.expert_votes "
            "FROM ad_cis_recommendations r "
            "INNER JOIN ("
            "  SELECT ticker, MAX(created_at) as mx "
            "  FROM ad_cis_recommendations GROUP BY ticker"
            ") latest ON r.ticker = latest.ticker AND r.created_at = latest.mx"
        ).fetchall()
        conn.close()

        result = {}
        for row in rows:
            r = dict(row)
            ticker = r.get("ticker") or r.get(0)
            direction = r.get("final_direction") or r.get(1)
            conviction = r.get("final_conviction") or r.get(2)
            expert_votes_raw = r.get("expert_votes") or r.get(3)
            votes = {}
            if expert_votes_raw:
                try:
                    parsed = json.loads(expert_votes_raw) if isinstance(expert_votes_raw, str) else expert_votes_raw
                    if isinstance(parsed, list):
                        votes = {v.get("expert_key", str(i)): v for i, v in enumerate(parsed)}
                    elif isinstance(parsed, dict):
                        votes = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            agree_count = sum(1 for v in votes.values() if v.get("direction") == direction)
            if ticker:
                result[ticker] = {
                    "direction": direction,
                    "conviction": conviction,
                    "agreement": f"{agree_count}/{len(votes)}" if votes else "—",
                }
        return result
    except Exception as e:
        app.logger.warning(f"_get_advisor_map error: {e}")
        return {}


@app.route("/api/signals/<signal_id>/approve", methods=["POST"])
def api_approve_signal(signal_id):
    from flask import g
    from tools.trading.db import update_signal_status

    update_signal_status(signal_id, "approved")
    _progression_grant_safe(
        user=getattr(g, "current_user", None),
        reason="signal_approved",
        dedup_key=f"signal_approved:{signal_id}",
        context={"signal_id": signal_id},
    )
    return jsonify({"status": "ok", "signal_id": signal_id, "action": "approved"})


@app.route("/api/signals/<signal_id>/reject", methods=["POST"])
def api_reject_signal(signal_id):
    from flask import g
    from tools.trading.db import update_signal_status

    update_signal_status(signal_id, "rejected")
    _progression_grant_safe(
        user=getattr(g, "current_user", None),
        reason="signal_rejected",
        dedup_key=f"signal_rejected:{signal_id}",
        context={"signal_id": signal_id},
    )
    return jsonify({"status": "ok", "signal_id": signal_id, "action": "rejected"})


# ---------------------------------------------------------------------------
# API — Trap events
# ---------------------------------------------------------------------------

@app.route("/api/traps")
def api_list_traps():
    ticker = request.args.get("ticker") or None
    pattern = request.args.get("pattern") or None
    try:
        min_conf = float(request.args.get("min_confidence") or 0)
    except (TypeError, ValueError):
        min_conf = 0.0
    try:
        limit = min(int(request.args.get("limit") or 50), 200)
    except (TypeError, ValueError):
        limit = 50
    from tools.trading.ta.trap_db import list_traps, ensure_tables
    try:
        ensure_tables()
        traps = list_traps(ticker=ticker, pattern=pattern,
                           min_confidence=min_conf, limit=limit)
        return jsonify({"traps": traps, "count": len(traps)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API — Analysis
# ---------------------------------------------------------------------------
@app.route("/api/analysis/run", methods=["POST"])
def api_run_analysis():
    data = request.get_json(silent=True) or {}
    ticker = data.get("ticker", "AAPL").upper()
    try:
        from tools.trading.runner import run_analysis

        result = run_analysis(ticker)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analysis/runs")
def api_analysis_runs():
    from flask import g
    from tools.trading.db import get_conn, _scope_clause

    _cu = getattr(g, "current_user", None) or {}
    _sc_outer, _sp_outer = _scope_clause(_cu.get("id"), _cu.get("tenant_id"), prefix="AND")
    _sc_inner, _sp_inner = _scope_clause(_cu.get("id"), _cu.get("tenant_id"), prefix="AND")

    # Return the latest run per ticker (deduped at DB level)
    conn = get_conn()
    rows = conn.execute(
        f"SELECT r.* FROM ad_analysis_runs r "  # nosec B608
        f"INNER JOIN ("
        f"  SELECT ticker, MAX(created_at) as max_date "
        f"  FROM ad_analysis_runs WHERE status = 'completed' {_sc_inner}"
        f"  GROUP BY ticker"
        f") latest ON r.ticker = latest.ticker "
        f"AND r.created_at = latest.max_date {_sc_outer}"
        f"ORDER BY r.created_at DESC",
        _sp_inner + _sp_outer,
    ).fetchall()
    conn.close()

    runs = []
    advisor_map = _get_advisor_map()
    for row in rows:
        r = dict(row)
        if r.get("result_json"):
            try:
                r["result"] = json.loads(r["result_json"])
            except (json.JSONDecodeError, TypeError):
                r["result"] = None
            del r["result_json"]
        # Attach advisor recommendation
        adv = advisor_map.get(r.get("ticker"))
        if adv:
            r["advisor"] = adv
        runs.append(r)
    return jsonify({"runs": runs, "total_count": len(runs)})


@app.route("/api/analysis/runs/<run_id>")
def api_analysis_run_detail(run_id):
    """Get full analysis detail for a single run (for modal)."""
    from flask import g
    from tools.trading.db import get_conn

    conn = get_conn()
    run = conn.execute(
        "SELECT * FROM ad_analysis_runs WHERE id=%s",
        (run_id,),
    ).fetchone()
    if not run:
        conn.close()
        return jsonify({"error": "Run not found"}), 404
    _cu = getattr(g, "current_user", None) or {}
    _uid = _cu.get("id") or "default"
    _tid = _cu.get("tenant_id") or "default"
    _run = dict(run)
    if _run.get("user_id", "default") != _uid and _run.get("tenant_id", "default") != _tid:
        conn.close()
        return jsonify({"error": "Run not found"}), 404

    result = {}
    if run["result_json"]:
        try:
            result = json.loads(run["result_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    # Fetch analyst reports
    reports = conn.execute(
        "SELECT * FROM ad_analyst_reports WHERE run_id=%s",
        (run_id,),
    ).fetchall()

    # Fetch debate record
    debate = conn.execute(
        "SELECT * FROM ad_debate_records WHERE run_id=%s",
        (run_id,),
    ).fetchone()

    # Fetch signal
    signal = conn.execute(
        "SELECT * FROM ad_signals WHERE run_id=%s",
        (run_id,),
    ).fetchone()

    # Fetch risk assessment
    risk = conn.execute(
        "SELECT * FROM ad_risk_assessments WHERE run_id=%s",
        (run_id,),
    ).fetchone()

    # Fetch trade decision
    decision = conn.execute(
        "SELECT * FROM ad_trade_decisions WHERE run_id=%s",
        (run_id,),
    ).fetchone()

    # Fetch macro context
    macro = conn.execute(
        "SELECT * FROM ad_macro_context WHERE run_id=%s",
        (run_id,),
    ).fetchone()

    # Extract per-run indicators from macro context_json (not global reference table)
    macro_indicators = []
    if macro and macro.get("context_json"):
        try:
            ctx = json.loads(macro["context_json"]) if isinstance(macro["context_json"], str) else macro["context_json"]
            macro_indicators = ctx.get("indicators", [])
        except (json.JSONDecodeError, TypeError):
            pass

    macro_sectors = conn.execute(
        "SELECT * FROM ad_macro_sector_impact WHERE run_id=%s",
        (run_id,),
    ).fetchall()

    # Fetch Pulse article
    article = conn.execute(
        "SELECT * FROM ad_pulse_articles WHERE run_id=%s",
        (run_id,),
    ).fetchone()

    # Generate Pulse article content if exists
    pulse_markdown = ""
    if article and result:
        try:
            from tools.trading.pulse.article_generator import (
                generate_article,
            )

            art = generate_article(result)
            pulse_markdown = art.get("body_markdown", "")
        except Exception:
            pass

    conn.close()

    # Parse JSON fields
    def _parse(row, *fields):
        if not row:
            return None
        d = dict(row)
        for f in fields:
            if f in d and isinstance(d[f], str):
                try:
                    d[f] = json.loads(d[f])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    # Parse macro context_json
    macro_data = None
    if macro:
        macro_data = _parse(macro, "context_json")

    return jsonify(
        {
            "run": dict(run),
            "result": result,
            "reports": [_parse(r, "findings_json") for r in reports],
            "debate": _parse(
                debate,
                "bull_thesis",
                "bear_thesis",
            ),
            "signal": dict(signal) if signal else None,
            "risk": _parse(risk, "checks_json", "warnings"),
            "decision": dict(decision) if decision else None,
            "article": dict(article) if article else None,
            "pulse_markdown": pulse_markdown,
            "macro": macro_data,
            "macro_indicators": [dict(i) if hasattr(i, 'keys') else i for i in macro_indicators],
            "macro_sectors": [dict(s) for s in macro_sectors],
        }
    )


# ---------------------------------------------------------------------------
# API — Evolution (PM time-series views)
# ---------------------------------------------------------------------------
@app.route("/api/evolution/tickers")
def api_evolution_tickers():
    """Get tickers for the evolution dropdown.

    Returns auto-approved tickers first, then all other analyzed tickers.
    """
    from tools.trading.db import get_conn

    conn = get_conn()
    # Auto-approved tickers (BUY with approved status)
    approved = conn.execute(
        "SELECT DISTINCT ticker FROM ad_signals WHERE status = 'approved' ORDER BY ticker"
    ).fetchall()
    approved_tickers = [r[0] for r in approved]

    # All analyzed tickers
    all_tickers = conn.execute(
        "SELECT DISTINCT ticker FROM ad_analysis_runs WHERE status = 'completed' ORDER BY ticker"
    ).fetchall()
    other_tickers = [r[0] for r in all_tickers if r[0] not in set(approved_tickers)]
    conn.close()

    return jsonify(
        {
            "approved": approved_tickers,
            "other": other_tickers,
            "total": len(approved_tickers) + len(other_tickers),
        }
    )


@app.route("/api/evolution/signals/<ticker>")
def api_signal_history(ticker):
    from tools.trading.db import get_signal_history

    limit = request.args.get("limit", 30, type=int)
    signals = get_signal_history(ticker.upper(), limit=limit)
    return jsonify({"ticker": ticker.upper(), "signals": signals})


@app.route("/api/evolution/analysts/<ticker>")
def api_analyst_contribution(ticker):
    from tools.trading.db import get_analyst_contribution_history

    limit = request.args.get("limit", 20, type=int)
    reports = get_analyst_contribution_history(ticker.upper(), limit=limit)

    # Group by run_id for chart-friendly format
    runs = {}
    for r in reports:
        rid = r["run_id"]
        if rid not in runs:
            runs[rid] = {"run_id": rid, "created_at": r["created_at"], "analysts": {}}
        runs[rid]["analysts"][r["analyst_type"]] = r["score"]

    return jsonify(
        {
            "ticker": ticker.upper(),
            "runs": list(runs.values()),
            "raw_reports": reports,
        }
    )


@app.route("/api/evolution/macro")
def api_macro_regime_history():
    from tools.trading.db import get_macro_regime_history

    limit = request.args.get("limit", 30, type=int)
    regimes = get_macro_regime_history(limit=limit)
    return jsonify({"regimes": regimes})


@app.route("/api/evolution/debate/<ticker>")
def api_debate_history(ticker):
    from tools.trading.db import get_debate_history

    limit = request.args.get("limit", 20, type=int)
    debates = get_debate_history(ticker.upper(), limit=limit)
    return jsonify({"ticker": ticker.upper(), "debates": debates})


@app.route("/api/evolution/compare/<run_id_a>/<run_id_b>")
def api_compare_runs(run_id_a, run_id_b):
    """Side-by-side comparison of two analysis runs."""
    from tools.trading.db import get_conn

    conn = get_conn()
    results = {}
    for run_id in (run_id_a, run_id_b):
        run = conn.execute(
            "SELECT * FROM ad_analysis_runs WHERE id=%s",
            (run_id,),
        ).fetchone()
        if not run:
            conn.close()
            return jsonify({"error": f"Run {run_id} not found"}), 404

        result = {}
        if run["result_json"]:
            try:
                result = json.loads(run["result_json"])
            except (json.JSONDecodeError, TypeError):
                pass

        signal = conn.execute(
            "SELECT * FROM ad_signals WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        macro = conn.execute(
            "SELECT * FROM ad_macro_context WHERE run_id=%s",
            (run_id,),
        ).fetchone()

        results[run_id] = {
            "run": dict(run),
            "signal": dict(signal) if signal else None,
            "macro": dict(macro) if macro else None,
            "result": result,
        }
    conn.close()

    # Compute deltas
    sig_a = results[run_id_a].get("signal") or {}
    sig_b = results[run_id_b].get("signal") or {}
    mac_a = results[run_id_a].get("macro") or {}
    mac_b = results[run_id_b].get("macro") or {}

    deltas = {
        "composite_score": ((sig_b.get("composite_score") or 0) - (sig_a.get("composite_score") or 0)),
        "confidence": ((sig_b.get("confidence") or 0) - (sig_a.get("confidence") or 0)),
        "direction_changed": (sig_a.get("direction") != sig_b.get("direction")),
        "macro_score": ((mac_b.get("macro_score") or 0) - (mac_a.get("macro_score") or 0)),
        "regime_changed": (mac_a.get("regime") != mac_b.get("regime")),
    }

    return jsonify(
        {
            "run_a": results[run_id_a],
            "run_b": results[run_id_b],
            "deltas": deltas,
        }
    )


@app.route("/api/evolution/portfolio")
def api_portfolio_timeline():
    from tools.trading.db import get_portfolio_timeline

    limit = request.args.get("limit", 50, type=int)
    snapshots = get_portfolio_timeline(limit=limit)
    return jsonify({"snapshots": snapshots})


# ---------------------------------------------------------------------------
# API — Market Intelligence
# ---------------------------------------------------------------------------
@app.route("/api/market/scan", methods=["POST"])
def api_market_scan():
    """Trigger a batch scan (async-friendly — returns immediately with status)."""
    data = request.get_json(silent=True) or {}
    sector = data.get("sector")
    workers = data.get("workers", 4)
    try:
        from tools.trading.market_intel.batch_scanner import (
            scan_universe,
            compute_breadth,
            compute_sector_summary,
        )

        scan = scan_universe(sector=sector, max_workers=workers)
        scan["breadth"] = compute_breadth(scan["results"])
        scan["sector_summary"] = compute_sector_summary(scan["results"])
        return jsonify(scan)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/market/latest")
def api_market_latest():
    """Get latest signal for every ticker in the universe.

    Reads from the materialized ad_market_snapshot table (refreshed by the
    snapshot_refresher daemon reflex). If the table is empty — e.g. daemon
    has never run — the first request triggers an inline rebuild so the page
    still loads; subsequent requests are a single SELECT.
    """
    from tools.trading.db import get_conn
    from tools.trading.market_intel.snapshot_builder import rebuild_market_snapshot

    conn = get_conn()
    rows = conn.execute(
        "SELECT ticker, sector, direction, composite_score, confidence, "
        "signal_created_at, signal_run_id, regime, "
        "kg_centrality, kg_supply_chain, kg_competitors "
        "FROM ad_market_snapshot"
    ).fetchall()

    if not rows or request.args.get("refresh") == "true":
        conn.close()
        rebuild_market_snapshot()
        conn = get_conn()
        rows = conn.execute(
            "SELECT ticker, sector, direction, composite_score, confidence, "
            "signal_created_at, signal_run_id, regime, "
            "kg_centrality, kg_supply_chain, kg_competitors "
            "FROM ad_market_snapshot"
        ).fetchall()
    conn.close()

    signals = []
    analyzed = 0
    current_regime = "YELLOW"
    for r in rows:
        d = dict(r)
        has_signal = d["composite_score"] is not None
        if has_signal:
            analyzed += 1
        if d.get("regime"):
            current_regime = d["regime"]
        signals.append(
            {
                "ticker": d["ticker"],
                "sector": d["sector"],
                "direction": d["direction"] or "—",
                "composite_score": d["composite_score"],
                "confidence": d["confidence"],
                "created_at": d["signal_created_at"],
                "run_id": d["signal_run_id"],
                "regime": d.get("regime") or current_regime,
                "kg_centrality": d["kg_centrality"] or 0,
                "kg_supply_chain": d["kg_supply_chain"] or 0,
                "kg_competitors": d["kg_competitors"] or 0,
            }
        )

    signals.sort(
        key=lambda s: (
            s["composite_score"] is not None,
            s.get("composite_score") or 0,
        ),
        reverse=True,
    )

    # Macro composite block — 60s cached to avoid hitting FRED per page load.
    macro_block = _cached("market_macro_block", 60)
    if not macro_block:
        macro_block = {}
        try:
            from tools.trading.data.macro_data import fetch_macro_context
            m = fetch_macro_context()
            raw = m.get("raw_values", {}) or {}
            macro_block = {
                "regime": m.get("regime"),
                "summary": m.get("summary"),
                "stagflation_risk": m.get("stagflation_risk"),
                "deflation_risk": m.get("deflation_risk"),
                "breakeven_5y5y": raw.get("breakeven_5y5y"),
                "breakeven_10y": raw.get("breakeven_10y"),
                "m2_yoy_pct": raw.get("m2_yoy_pct"),
                "cpi_yoy": raw.get("cpi_yoy"),
                "gdp_growth_q": raw.get("gdp_growth_q"),
                "data_source": m.get("data_source"),
            }
            _set_cache("market_macro_block", macro_block)
        except Exception:
            macro_block = {}

    # Prefer the composite-aware regime label over the snapshot cache if present.
    regime_out = macro_block.get("regime") or current_regime

    return jsonify(
        {
            "signals": signals,
            "total": len(signals),
            "analyzed": analyzed,
            "universe_size": len(signals),
            "regime": regime_out,
            "macro": macro_block,
        }
    )


def _load_kg_data() -> dict:
    """Load KG centrality and neighbor counts for all tickers. Cached 120s."""
    cached = _cached("kg_data_internal", 120)
    if cached:
        return cached
    try:
        from tools.trading.db import get_conn

        conn = get_conn()
        # Get centrality per ticker node
        nodes = conn.execute(
            "SELECT label, centrality FROM kg_nodes "
            "WHERE entity_type IN ('ticker', 'etf') "
            "AND graph_id IN ("
            "  SELECT id FROM kg_graphs WHERE name = 'fathomdesk-market'"
            ")"
        ).fetchall()

        kg = {}
        for label, centrality in nodes:
            kg[label] = {"centrality": round(centrality or 0, 4)}

        # Supply chain neighbors (SUPPLIES_TO edges)
        supply = conn.execute(
            "SELECT n.label, COUNT(DISTINCT e.target_id) as cnt "
            "FROM kg_edges e "
            "JOIN kg_nodes n ON e.source_id = n.id "
            "WHERE e.relationship = 'SUPPLIES_TO' "
            "AND n.entity_type IN ('ticker', 'etf') "
            "GROUP BY n.label"
        ).fetchall()
        for label, cnt in supply:
            kg.setdefault(label, {})["supply_chain_count"] = cnt

        # Also count reverse (who supplies TO this ticker)
        supply_rev = conn.execute(
            "SELECT n.label, COUNT(DISTINCT e.source_id) as cnt "
            "FROM kg_edges e "
            "JOIN kg_nodes n ON e.target_id = n.id "
            "WHERE e.relationship = 'SUPPLIES_TO' "
            "AND n.entity_type IN ('ticker', 'etf') "
            "GROUP BY n.label"
        ).fetchall()
        for label, cnt in supply_rev:
            existing = kg.get(label, {}).get("supply_chain_count", 0)
            kg.setdefault(label, {})["supply_chain_count"] = existing + cnt

        # Competitor count
        comps = conn.execute(
            "SELECT n.label, COUNT(DISTINCT e.target_id) as cnt "
            "FROM kg_edges e "
            "JOIN kg_nodes n ON e.source_id = n.id "
            "WHERE e.relationship = 'COMPETES_WITH' "
            "AND n.entity_type IN ('ticker', 'etf') "
            "GROUP BY n.label"
        ).fetchall()
        for label, cnt in comps:
            kg.setdefault(label, {})["competitor_count"] = cnt

        conn.close()
        _set_cache("kg_data_internal", kg)
        return kg
    except Exception:
        return {}


def _compute_ticker_performance(ticker: str) -> dict:
    """Compute total return % for 1yr/5yr/10yr/20yr using deterministic sample bars.

    Uses the same LCG seed as market_data._generate_sample_bars so values are
    consistent with the rest of the trading engine.

    Generates 20yr worth of daily bars (5040 steps).  The final bar is "today".
    Each period's start price is the price N trading-days before the final bar.
    """
    import hashlib

    # Trading days per period
    PERIOD_DAYS = {"p1y": 252, "p5y": 1260, "p10y": 2520, "p20y": 5040}
    MAX_DAYS = 5040  # 20 years

    seed = int(hashlib.sha256(ticker.encode("utf-8")).hexdigest()[:8], 16)
    base_price = 100.0 + (seed % 400)
    price = base_price

    # We need the price at days: (MAX_DAYS - period) from start, i.e. N days before "today"
    # For p20y the start is base_price (day 0)
    lookback_days = {MAX_DAYS - d: key for key, d in PERIOD_DAYS.items() if d < MAX_DAYS}
    # p20y start = base_price, recorded separately
    prices_at = {}

    for day in range(1, MAX_DAYS + 1):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        change_pct = ((seed % 1000) - 500) / 10000.0
        price *= 1 + change_pct
        if day in lookback_days:
            prices_at[lookback_days[day]] = price

    end_price = price  # "today"

    result = {}
    for key, period_days in PERIOD_DAYS.items():
        if period_days == MAX_DAYS:
            start = base_price  # 20yr start = very first bar price
        else:
            start = prices_at.get(key)
        if start and start > 0:
            result[key] = round((end_price - start) / start * 100, 1)
        else:
            result[key] = None
    return result


@app.route("/api/market/performance")
def api_market_performance():
    """Get 1yr/5yr/10yr/20yr total return % for every universe ticker.

    Reads from ad_ticker_performance. Values are deterministic per ticker and
    built once by snapshot_builder.rebuild_ticker_performance(). If the table
    is empty, the first request rebuilds inline; subsequent requests are a
    single SELECT.
    """
    from tools.trading.db import get_conn
    from tools.trading.market_intel.snapshot_builder import rebuild_ticker_performance

    conn = get_conn()
    rows = conn.execute(
        "SELECT ticker, p1y, p5y, p10y, p20y FROM ad_ticker_performance"
    ).fetchall()
    if not rows:
        conn.close()
        rebuild_ticker_performance()
        conn = get_conn()
        rows = conn.execute(
            "SELECT ticker, p1y, p5y, p10y, p20y FROM ad_ticker_performance"
        ).fetchall()
    conn.close()

    perf = {}
    for r in rows:
        d = dict(r)
        perf[d["ticker"]] = {
            "p1y": d["p1y"],
            "p5y": d["p5y"],
            "p10y": d["p10y"],
            "p20y": d["p20y"],
        }
    return jsonify({"performance": perf, "tickers": len(perf)})


@app.route("/api/market/alerts")
def api_market_alerts():
    """Get current alerts with KG supply chain propagation.

    Auto-propagates:
    - Direction flips through KG supply chain
    - All auto-approved BUY signals (confidence >= 67%) through KG
    Cached for 60 seconds.
    """
    hit = _cached_response("market_alerts", 60)
    if hit:
        return hit

    try:
        from tools.trading.market_intel.alert_engine import (
            detect_alerts,
            propagate_supply_chain_alert,
        )
        from tools.trading.db import get_conn

        alerts = detect_alerts()
        propagated = []

        # Auto-propagate direction flips through KG supply chain
        for a in alerts:
            if a["type"] == "direction_flip":
                event = (
                    "earnings_beat"
                    if a["curr"] == "BUY"
                    else "supply_disruption"
                    if a["curr"] == "SHORT"
                    else "earnings_miss"
                )
                chain_alerts = propagate_supply_chain_alert(
                    a["ticker"],
                    event,
                )
                propagated.extend(chain_alerts)

        # Auto-propagate all approved BUY signals through KG
        conn = get_conn()
        approved_buys = conn.execute(
            "SELECT DISTINCT ticker FROM ad_signals WHERE status = 'approved' AND direction = 'BUY'"
        ).fetchall()
        conn.close()

        seen_propagated = {a["ticker"] for a in propagated}
        for row in approved_buys:
            ticker = row[0]
            if ticker not in seen_propagated:
                chain_alerts = propagate_supply_chain_alert(
                    ticker,
                    "earnings_beat",
                )
                for ca in chain_alerts:
                    ca["type"] = "auto_approved_propagation"
                    ca["message"] = (
                        f"{ticker} auto-approved BUY → "
                        f"{ca['ticker']} ({ca['relationship'].lower()}, "
                        f"weight={ca.get('weight', 0):.2f})"
                    )
                propagated.extend(chain_alerts)

        # High-impact news events — surface any ticker whose aggregated
        # news impact over the last 24h is strong enough to warrant
        # operator attention. Lives alongside direction-flip + KG-propagated
        # alerts so everything lands in one pane.
        try:
            from tools.trading.news.impact_store import top_tickers_by_impact
            news_impact_rows = top_tickers_by_impact(
                hours=24, direction="both", min_abs_score=1.5, limit=8,
            )
            for r in news_impact_rows:
                score = float(r.get("total_score") or 0)
                sev = "critical" if abs(score) >= 3.0 else "high" if abs(score) >= 2.0 else "medium"
                impact = "positive" if score > 0 else "negative"
                propagated.append({
                    "type": "news_impact",
                    "severity": sev,
                    "ticker": r["ticker"],
                    "impact": impact,
                    "impact_score": score,
                    "trace_count": r.get("trace_count"),
                    "message": (
                        f"News-driven supply-chain impact on {r['ticker']}: "
                        f"Σ {score:+.2f} across {r.get('trace_count', 0)} trace rows "
                        f"(last 24h). Click ticker for drill-down."
                    ),
                })
        except Exception:
            pass

        all_alerts = alerts + propagated
        result = {"alerts": all_alerts, "count": len(all_alerts)}
        _set_cache("market_alerts", result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"alerts": [], "count": 0, "error": str(e)})


# ---------------------------------------------------------------------------
# API — News Pipeline (FathomDesk /news)
# ---------------------------------------------------------------------------

@app.route("/api/news")
def api_news_list():
    """List news items with optional filters. Cached 60s."""
    hit = _cached_response("news_list_" + request.query_string.decode(), 60)
    if hit:
        return hit
    try:
        from tools.trading.news.db import list_news
        from tools.trading.news.classifier import is_market_relevant
        category = request.args.get("category")
        show_all = request.args.get("all") == "true"
        limit = int(request.args.get("limit", 200))
        raw = list_news(category=category, limit=limit)
        # Filter to market-relevant items unless ?all=true
        if not show_all:
            result = [
                item for item in raw
                if is_market_relevant(
                    item.get("title", ""),
                    item.get("summary", ""),
                    item.get("category", "general"),
                )
            ]
        else:
            result = raw
        # Deduplicate by title (keep first/newest — list is sorted by date)
        seen_titles = set()
        deduped = []
        for item in result:
            title = (item.get("title") or "").strip().lower()
            if title and title in seen_titles:
                continue
            seen_titles.add(title)
            deduped.append(item)
        result = deduped
        _set_cache("news_list_" + request.query_string.decode(), result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "items": []})


@app.route("/api/news/<news_id>")
def api_news_detail(news_id):
    """Get a single news item with matched scenarios."""
    try:
        from tools.trading.news.db import get_news_by_id
        item = get_news_by_id(news_id)
        if not item:
            return jsonify({"error": "not found"}), 404
        return jsonify(item)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/news/<news_id>/supply-chain-impact")
@app.route("/api/news/supply-chain-impact", methods=["GET", "POST"])
def api_news_supply_chain_impact(news_id=None):
    """Run an integrated supply-chain impact analysis on a news item.

    Two usage modes:
      1. /api/news/<id>/supply-chain-impact — pulls headline+summary from DB.
      2. /api/news/supply-chain-impact?text=... (or POST {"text": "..."}) —
         runs on arbitrary free-form text for ad-hoc "what if" analysis.

    Flow:
      • Extract entities (countries, commodities, tickers) + event hints
      • Propagate each detected subject through the KG
      • Aggregate per-ticker impact across all subjects
      • Return {entities, per_subject, aggregate} ranked by |impact|
    """
    from tools.trading.news.entity_extractor import extract_entities, pick_primary_event
    from tools.trading.market_intel.alert_engine import propagate_subject

    # Resolve text
    text = ""
    title = ""
    if news_id:
        try:
            from tools.trading.news.db import get_news_by_id
            item = get_news_by_id(news_id)
            if not item:
                return jsonify({"error": f"news item {news_id} not found"}), 404
            title = item.get("title", "") or ""
            text = f"{title} {item.get('summary', '') or ''}".strip()
        except Exception as e:
            return jsonify({"error": f"DB lookup failed: {e}"}), 500
    else:
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            text = (body.get("text") or "").strip()
        else:
            text = (request.args.get("text") or "").strip()
        title = text[:120]
    if not text:
        return jsonify({"error": "no text provided"}), 400

    entities = extract_entities(text)
    event_hints = entities.get("event_hints", [])

    per_subject: list[dict] = []
    aggregate: dict[str, dict] = {}  # ticker -> {ticker, total_score, contributions[]}

    def _merge(alerts, subject, subject_type, event):
        for a in alerts:
            if "error" in a: continue
            t = a.get("ticker")
            if not t: continue
            score = float(a.get("impact_score", 0) or 0)
            row = aggregate.setdefault(t, {"ticker": t, "total_score": 0.0, "contributions": []})
            row["total_score"] += score
            row["contributions"].append({
                "subject": subject, "subject_type": subject_type,
                "event": event, "score": round(score, 3),
                "role": a.get("role"), "via": a.get("via"),
            })

    # Run each detected subject; pick event hint per subject type
    for c in entities["countries"]:
        event = pick_primary_event(event_hints, "country")
        res = propagate_subject(c["label"], event, "country")
        _merge(res.get("alerts", []), c["label"], "country", event)
        per_subject.append({
            "subject": c["label"], "subject_type": "country", "event": event,
            "mentions": c["mentions"], "alert_count": res.get("count", 0),
            "alerts": res.get("alerts", [])[:10],
        })

    for c in entities["commodities"]:
        # Skip if already covered by a country cascade for this commodity —
        # per-ticker aggregate will just reinforce, which is fine, but we
        # don't duplicate the subject card unless useful.
        event = pick_primary_event(event_hints, "commodity")
        res = propagate_subject(c["label"], event, "commodity")
        _merge(res.get("alerts", []), c["label"], "commodity", event)
        per_subject.append({
            "subject": c["label"], "subject_type": "commodity", "event": event,
            "mentions": c["mentions"], "alert_count": res.get("count", 0),
            "alerts": res.get("alerts", [])[:10],
        })

    for t in entities["tickers"]:
        event = pick_primary_event(event_hints, "ticker")
        res = propagate_subject(t["label"], event, "ticker")
        _merge(res.get("alerts", []), t["label"], "ticker", event)
        per_subject.append({
            "subject": t["label"], "subject_type": "ticker", "event": event,
            "mentions": t["mentions"], "alert_count": res.get("count", 0),
            "alerts": res.get("alerts", [])[:10],
        })

    for p in entities.get("private_companies", []):
        event = pick_primary_event(event_hints, "private_company")
        res = propagate_subject(p["label"], event, "private_company")
        _merge(res.get("alerts", []), p["label"], "private_company", event)
        per_subject.append({
            "subject": p["label"], "subject_type": "private_company", "event": event,
            "mentions": p["mentions"], "alert_count": res.get("count", 0),
            "alerts": res.get("alerts", [])[:10],
        })

    # Rank aggregate by absolute total score
    agg_list = sorted(
        ({**v, "total_score": round(v["total_score"], 3)} for v in aggregate.values()),
        key=lambda r: abs(r["total_score"]),
        reverse=True,
    )
    winners = [r for r in agg_list if r["total_score"] > 0]
    losers = [r for r in agg_list if r["total_score"] < 0]

    aggregate_payload = {
        "winners": winners[:15],
        "losers": losers[:15],
        "total_tickers_affected": len(agg_list),
    }

    # Persist trace rows keyed by news_id so downstream systems (Oracle lens,
    # expert agents, SROR, /analysis ticker page) can query. For ad-hoc text
    # calls (no news_id), we synthesize a transient id.
    trace_key = news_id or f"adhoc-{abs(hash(text)) % (10**12):012d}"
    try:
        from tools.trading.news.impact_store import store_traces
        # Include the full agg_list so we don't drop tickers below the top-15 cutoff
        store_traces(trace_key, {
            "winners": winners, "losers": losers,
            "total_tickers_affected": len(agg_list),
        })
    except Exception:
        pass  # persistence is best-effort; don't fail the request

    return jsonify({
        "title": title,
        "text_length": len(text),
        "news_id": trace_key,
        "entities": entities,
        "per_subject": per_subject,
        "aggregate": aggregate_payload,
    })


# ---------------------------------------------------------------------------
# News impact query endpoints — consumed by /analysis, /market alerts, lenses
# ---------------------------------------------------------------------------

@app.route("/api/news/impact/recent")
def api_news_impact_recent():
    """Rollup of news-driven impact across all tickers over a time window.

    Query params:
      hours     — lookback window (default 24)
      direction — positive | negative | both (default both)
      min_score — filter threshold on |Σ score| (default 0.5)
      limit     — row limit (default 25)
    """
    try:
        from tools.trading.news.impact_store import top_tickers_by_impact, geopolitical_danger_score
        hours = int(request.args.get("hours", 24))
        direction = request.args.get("direction", "both")
        min_score = float(request.args.get("min_score", 0.5))
        limit = int(request.args.get("limit", 25))
        return jsonify({
            "hours": hours,
            "rows": top_tickers_by_impact(hours=hours, direction=direction,
                                          min_abs_score=min_score, limit=limit),
            "geopolitical_danger": geopolitical_danger_score(hours=hours),
        })
    except Exception as e:
        return jsonify({"error": str(e), "rows": []}), 500


@app.route("/api/news/impact/ticker/<ticker>")
def api_news_impact_ticker(ticker):
    """Per-ticker news impact — used by /analysis to show what news is
    currently shaping the stock's thesis.

    Returns summary rollup + the individual contributing news items with
    their cascade breakdown.
    """
    try:
        from tools.trading.news.impact_store import get_recent_impact, get_impact_contributions
        from tools.trading.news.db import get_news_by_id
        hours = int(request.args.get("hours", 24))
        rollup = get_recent_impact(ticker, hours=hours)
        contributions = get_impact_contributions(ticker, hours=hours, limit=20)
        # Enrich with news titles so the UI doesn't need a second round-trip
        for c in contributions:
            try:
                item = get_news_by_id(c["news_id"])
                c["news_title"] = (item or {}).get("title")
                c["news_link"] = (item or {}).get("link")
            except Exception:
                pass
        return jsonify({
            "ticker": ticker.upper(),
            "hours": hours,
            "rollup": rollup,
            "contributions": contributions,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/news/<news_id>/run-scenario", methods=["POST"])
def api_news_run_scenario(news_id):
    """Match and run a scenario for a news item."""
    try:
        from tools.trading.news.scenario_matcher import match_and_run
        result = match_and_run(news_id)
        if not result:
            return jsonify({"error": "no scenario match", "news_id": news_id}), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/news/clusters")
def api_news_clusters():
    """Get active news clusters."""
    try:
        from tools.trading.news.db import list_active_clusters
        status = request.args.get("status")
        clusters = list_active_clusters(status=status)
        return jsonify(clusters)
    except Exception as e:
        return jsonify({"error": str(e), "clusters": []})


@app.route("/api/news/<news_id>/analyze", methods=["POST"])
def api_news_analyze(news_id):
    """INTaaS multiperspective analysis on a single news item. Cached 15m."""
    cache_key = f"news_intel_{news_id}"
    hit = _cached_response(cache_key, 900)  # 15 min TTL
    if hit:
        return hit
    try:
        from tools.trading.news.db import get_news_by_id, list_active_clusters
        from tools.trading.news.news_reasoner import reason_item, detect_divergences

        item = get_news_by_id(news_id)
        if not item:
            return jsonify({"error": "not found"}), 404

        # Load macro context
        macro = {}
        try:
            from tools.trading.data.macro_data import fetch_macro_context
            macro = fetch_macro_context()
        except Exception:
            pass

        clusters = list_active_clusters()
        intelligence = reason_item(item, macro, clusters)
        divergences = detect_divergences(macro, clusters)
        intelligence["divergence_signals"] = divergences
        intelligence["news_id"] = news_id
        intelligence["title"] = item.get("title", "")
        _set_cache(cache_key, intelligence)
        return jsonify(intelligence)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/news/category-summary/<cat>")
def api_news_category_summary(cat: str):
    """Aggregate mood/headline for a news category — used by category tab summary cards."""
    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT impact_level, net_direction, title
               FROM ad_news_items
               WHERE category = %s AND published_at >= datetime('now', '-24 hours')
               ORDER BY published_at DESC LIMIT 50""",
            (cat,),
        ).fetchall()
        if not rows:
            return jsonify({"mood": "neutral", "headline": "", "item_count": 0})

        bullish = sum(1 for r in rows if r[1] == "bullish")
        bearish = sum(1 for r in rows if r[1] == "bearish")
        total = len(rows)
        if bullish > bearish * 1.5:
            mood = "bullish"
        elif bearish > bullish * 1.5:
            mood = "bearish"
        else:
            mood = "neutral"

        high_impact = [r[2] for r in rows if r[0] == "high"]
        headline = high_impact[0] if high_impact else (rows[0][2] if rows else "")
        if headline and len(headline) > 120:
            headline = headline[:117] + "…"

        return jsonify({"mood": mood, "headline": headline, "item_count": total})
    except Exception as e:
        return jsonify({"error": str(e), "mood": "neutral", "headline": "", "item_count": 0})


@app.route("/api/news/divergences")
def api_news_divergences():
    """Get current cross-signal divergences."""
    try:
        from tools.trading.news.news_reasoner import detect_divergences
        from tools.trading.news.db import list_active_clusters

        macro = {}
        try:
            from tools.trading.data.macro_data import fetch_macro_context
            macro = fetch_macro_context()
        except Exception:
            pass

        clusters = list_active_clusters()
        divergences = detect_divergences(macro, clusters)
        return jsonify({"divergences": divergences, "count": len(divergences)})
    except Exception as e:
        return jsonify({"error": str(e), "divergences": []})


# ---------------------------------------------------------------------------
# API — Trading Oracle
# ---------------------------------------------------------------------------

@app.route("/api/oracle/predictions")
def api_oracle_predictions():
    """List pending oracle predictions."""
    try:
        from tools.trading.oracle.db import list_predictions
        preds = list_predictions(outcome="pending", limit=50)
        return jsonify(preds)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/oracle/convergence")
def api_oracle_convergence():
    """List convergence events."""
    try:
        from tools.trading.oracle.db import list_convergence_events
        events = list_convergence_events(limit=20)
        return jsonify(events)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/oracle/sweep", methods=["POST"])
def api_oracle_sweep():
    """Run a full oracle sweep."""
    try:
        from tools.trading.oracle.runner import sweep
        result = sweep()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/market/breadth")
def api_market_breadth():
    """Latest 200-EMA breadth snapshot + 30-day history for divergence chart."""
    try:
        import json as _json
        from tools.trading.db import get_conn
        conn = get_conn()

        # Latest snapshot
        row = conn.execute(
            "SELECT pct_above_200ema, net_hi_lo_ratio, signal, above_count, below_count, "
            "near_high_count, near_low_count, universe_count, sector_json, ticker_json, created_at "
            "FROM ad_breadth_snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

        if not row:
            conn.close()
            return jsonify({"status": "no_data", "message": "Breadth sweep hasn't run yet. Check back after the next market_scanner cycle."})

        latest = {
            "pct_above_200ema": row[0],
            "net_hi_lo_ratio":  row[1],
            "signal":           row[2],
            "above_count":      row[3],
            "below_count":      row[4],
            "near_high_count":  row[5],
            "near_low_count":   row[6],
            "universe_count":   row[7],
            "by_sector":        _json.loads(row[8] or "[]"),
            "per_ticker":       _json.loads(row[9] or "[]"),
            "created_at":       row[10],
        }

        # 30-day history for divergence chart
        history_rows = conn.execute(
            "SELECT pct_above_200ema, net_hi_lo_ratio, signal, created_at "
            "FROM ad_breadth_snapshots ORDER BY created_at DESC LIMIT 720"  # ~30d × 24 ticks
        ).fetchall()
        conn.close()

        history = [
            {"pct_above_200ema": r[0], "net_hi_lo_ratio": r[1], "signal": r[2], "ts": r[3]}
            for r in reversed(history_rows)
        ]

        return jsonify({"latest": latest, "history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/value/fear-greed")
def api_value_fear_greed():
    """Latest Fear & Greed snapshot + 90-day history."""
    try:
        import json as _json
        from tools.trading.db import get_conn
        conn = get_conn()

        row = conn.execute(
            "SELECT composite_score, label, components_json, entry_exit_signal, created_at "
            "FROM ad_fear_greed_snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

        if not row:
            conn.close()
            return jsonify({"status": "no_data", "message": "Fear & Greed sweep hasn't run yet."})

        latest = {
            "composite_score":    row[0],
            "label":              row[1],
            "components":         _json.loads(row[2] or "{}"),
            "entry_exit_signal":  row[3],
            "created_at":         row[4],
        }

        history_rows = conn.execute(
            "SELECT composite_score, label, created_at "
            "FROM ad_fear_greed_snapshots ORDER BY created_at DESC LIMIT 2160"  # ~90d × 24 ticks
        ).fetchall()
        conn.close()

        history = [
            {"composite_score": r[0], "label": r[1], "ts": r[2]}
            for r in reversed(history_rows)
        ]

        return jsonify({"latest": latest, "history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/value/buffett-indicator")
def api_value_buffett():
    """Latest Buffett Indicator snapshot + 5-year monthly history."""
    try:
        from tools.trading.db import get_conn
        conn = get_conn()

        row = conn.execute(
            "SELECT ratio_pct, wilshire_trn, gdp_trn, signal, created_at "
            "FROM ad_buffett_snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

        if not row:
            conn.close()
            return jsonify({"status": "no_data", "message": "Buffett Indicator sweep hasn't run yet."})

        latest = {
            "ratio_pct":    row[0],
            "wilshire_trn": row[1],
            "gdp_trn":      row[2],
            "signal":       row[3],
            "created_at":   row[4],
        }

        history_rows = conn.execute(
            "SELECT ratio_pct, signal, created_at "
            "FROM ad_buffett_snapshots ORDER BY created_at DESC LIMIT 60"  # ~5y monthly
        ).fetchall()
        conn.close()

        history = [
            {"ratio_pct": r[0], "signal": r[1], "ts": r[2]}
            for r in reversed(history_rows)
        ]

        return jsonify({"latest": latest, "history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/value/screener")
def api_value_screener():
    """Value screener — quality stocks sorted by composite score.

    Query params: max_pe, max_pb, min_fcf_yield, min_roe, max_de (all optional floats).
    """
    try:
        from tools.trading.db import get_conn
        max_pe     = request.args.get("max_pe",      type=float, default=35.0)
        max_pb     = request.args.get("max_pb",      type=float, default=5.0)
        min_fcf    = request.args.get("min_fcf_yield", type=float, default=0.0)
        min_roe    = request.args.get("min_roe",     type=float, default=0.0)
        max_de     = request.args.get("max_de",      type=float, default=5.0)
        limit      = request.args.get("limit",       type=int,   default=50)

        conn = get_conn()

        # Short-circuit: if quality sweep hasn't run yet, return empty gracefully
        qs_count = conn.execute("SELECT COUNT(*) FROM ad_quality_scores").fetchone()[0]
        if qs_count == 0:
            conn.close()
            return jsonify({"results": [], "count": 0, "status": "no_data",
                            "message": "Quality sweep hasn't run yet — trigger via Genesis or wait for the daily reflex."})

        rows = conn.execute(
            """
            SELECT q.ticker, q.composite_quality_score,
                   f.pe_ratio, f.price_to_book, f.fcf_yield, f.roe, f.debt_to_equity,
                   f.roic, f.gross_margin, f.eps,
                   f.piotroski_score, q.quality_label,
                   s.direction, s.composite_score AS signal_score
            FROM   ad_quality_scores q
            LEFT JOIN ad_fundamental_metrics f ON f.ticker = q.ticker
            LEFT JOIN (
                SELECT DISTINCT ON (ticker) ticker, direction, composite_score
                FROM   ad_signals
                ORDER  BY ticker, run_id DESC
            ) s ON s.ticker = q.ticker
            WHERE  (f.pe_ratio       IS NULL OR f.pe_ratio       <= %s)
              AND  (f.price_to_book  IS NULL OR f.price_to_book  <= %s)
              AND  (f.fcf_yield      IS NULL OR f.fcf_yield      >= %s)
              AND  (f.roe            IS NULL OR f.roe             >= %s)
              AND  (f.debt_to_equity IS NULL OR f.debt_to_equity <= %s)
            ORDER BY q.composite_quality_score DESC
            LIMIT  %s
            """,
            (max_pe, max_pb, min_fcf, min_roe, max_de, limit),
        ).fetchall()
        conn.close()

        results = [
            {
                "ticker":        r[0],
                "composite_score": round(r[1] or 0, 1),
                "pe":            r[2],
                "pb":            r[3],
                "fcf_yield":     r[4],
                "roe":           r[5],
                "de":            r[6],
                "roic":          r[7],
                "gross_margin":  r[8],
                "eps":           r[9],
                "piotroski":     r[10],
                "quality_label": r[11],
                "direction":     r[13] or "-",
                "signal_score":  r[13],
            }
            for r in rows
        ]
        return jsonify({"results": results, "count": len(results)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/value/pe-nav")
def api_value_pe_nav():
    """PE/NAV universe — implied ROE gap (actual ROE − P/B÷PE), sorted by pe_nav_score."""
    import logging as _log
    try:
        from tools.trading.db import get_conn
        from tools.trading.analysts.quality import compute_pe_nav_metrics

        limit    = request.args.get("limit",    type=int, default=60)
        quadrant = request.args.get("quadrant", default=None) or None

        conn = get_conn()
        rows = conn.execute(
            """
            SELECT ticker, pe_ratio, price_to_book, roe,
                   gross_margin, roic, debt_to_equity, fcf_yield, sector_tier
            FROM   ad_fundamental_metrics
            WHERE  pe_ratio IS NOT NULL
              AND  roe IS NOT NULL
              AND  pe_ratio > 0
            ORDER BY pe_ratio
            LIMIT  %s
            """,
            (limit * 3,),
        ).fetchall()
        conn.close()

        results = []
        for r in rows:
            ticker = r[0]
            pe     = r[1]
            pb     = r[2]   # price_to_book — may be None
            roe    = r[3]
            f_dict = {
                "pe_ratio": pe,
                "pb_ratio": pb if pb else 0.0,
                "roe":      roe,
                "sector":   None,
            }
            nav = compute_pe_nav_metrics(f_dict)
            if not pb:
                nav["implied_roe"]  = None
                nav["roe_gap"]      = None
                nav["pe_nav_score"] = 50.0
                nav["nav_quadrant"] = "unknown"
            nav_q = nav["nav_quadrant"]
            if quadrant and nav_q != quadrant:
                continue
            results.append({
                "ticker":        ticker,
                "pe":            round(pe, 2),
                "pb":            round(pb, 2) if pb else None,
                "roe":           round(roe * 100, 2),
                "implied_roe":   round(nav["implied_roe"] * 100, 2) if nav["implied_roe"] is not None else None,
                "roe_gap":       round(nav["roe_gap"] * 100, 2)     if nav["roe_gap"]     is not None else None,
                "pe_nav_score":  nav["pe_nav_score"],
                "nav_quadrant":  nav_q,
                "sector_tier":   r[8] or nav["sector_tier"],
                "quality_score": None,
                "quality_label": None,
            })

        results.sort(key=lambda x: x["pe_nav_score"], reverse=True)
        return jsonify({"results": results[:limit], "count": len(results[:limit])})
    except Exception as e:
        _log.exception("pe-nav endpoint error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/value/pe-nav/refresh", methods=["POST"])
def api_pe_nav_refresh():
    """Re-fetch yfinance fundamentals for all tracked tickers and update ad_fundamental_metrics."""
    try:
        from tools.trading.analysts.fundamentals_sweep import run_sweep
        extra = request.json or {}
        tickers = extra.get("tickers") or None   # optional override
        result  = run_sweep(tickers=tickers)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/value/durable-compounders")
def api_durable_compounders():
    """Durable Compounders universe — fortress balance sheet + loyal customers + cycle resilience.

    Query params:
      tier   — filter to "fortress" | "solid" | "watchlist"
      limit  — max results (default 50)
    """
    try:
        from tools.trading.analysts.durable_compounders import scan
        tier  = request.args.get("tier")  or None
        limit = request.args.get("limit", type=int, default=50)
        result = scan(limit=limit, tier_filter=tier)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/value/durable-compounders/scan", methods=["POST"])
def api_durable_compounders_scan():
    """Force-rescore all tickers, bypassing the 7-day cache. Returns full scan result."""
    try:
        from tools.trading.analysts.durable_compounders import scan
        body  = request.json or {}
        limit = body.get("limit", 200)
        result = scan(limit=limit, force=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/market/daemon")
def api_daemon_status():
    """Get Trading Daemon status. Cached 30s (daemon state changes on reflex tick)."""
    if request.args.get("refresh") != "true":
        hit = _cached_response("daemon_status", 30)
        if hit:
            return hit
    try:
        from tools.trading.market_intel.daemon import get_daemon_status

        result = get_daemon_status()
        _set_cache("daemon_status", result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "daemon": "not_available"})


@app.route("/api/market/daemon/run/<reflex>", methods=["POST"])
def api_daemon_run_reflex(reflex):
    """Trigger a single reflex run."""
    try:
        from tools.trading.market_intel.daemon import TradingDaemon

        config = TradingDaemon.load_config()
        daemon = TradingDaemon(config)
        daemon.ensure_tables()
        daemon._init_states()
        result = daemon.run_reflex(reflex)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/advisor")
def page_advisor():
    return render_template("advisor.html")


# ---------------------------------------------------------------------------
# API — Expert Advisory
# ---------------------------------------------------------------------------
@app.route("/api/advisor/analyze", methods=["POST"])
def api_advisor_analyze():
    data = request.get_json(silent=True) or {}
    ticker = data.get("ticker", "AAPL").upper()
    try:
        from tools.trading.market_intel.judge import run_socratic_analysis

        result = run_socratic_analysis(ticker)

        # Attach forecast data for chart
        try:
            from tools.trading.market_intel.forecaster import forecast_ticker

            forecast = forecast_ticker(ticker)
            if not forecast.get("error"):
                result["forecast"] = forecast
        except Exception:
            pass

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/advisor/brief")
def api_advisor_brief():
    """Evening daily brief. Cached 300s — brief is regenerated by daemon's
    daily_brief reflex at 01:00 UTC."""
    if request.args.get("refresh") != "true":
        hit = _cached_response("advisor_brief", 300)
        if hit:
            return hit
    try:
        from tools.trading.market_intel.expert_agents import generate_daily_brief

        result = generate_daily_brief()
        _set_cache("advisor_brief", result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/advisor/experts")
def api_advisor_experts():
    from tools.trading.market_intel.expert_agents import EXPERT_AGENTS

    return jsonify(
        {
            k: {
                "name": v["name"],
                "style": v["style"],
                "icon": v["icon"],
                "philosophy": v["philosophy"],
                "risk_profile": v["risk_profile"],
            }
            for k, v in EXPERT_AGENTS.items()
        }
    )


@app.route("/api/advisor/recommendations")
def api_advisor_recommendations():
    from flask import g
    from tools.trading.market_intel.expert_agents import _ensure_tables
    from tools.trading.db import get_conn, _scope_clause

    _ensure_tables()
    _cu = getattr(g, "current_user", None) or {}
    _sc, _sp = _scope_clause(_cu.get("id"), _cu.get("tenant_id"))
    conn = get_conn()
    rows = conn.execute(
        f"SELECT * FROM ad_cis_recommendations {_sc} ORDER BY created_at DESC LIMIT 20",  # nosec B608
        _sp,
    ).fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        for field in ("expert_votes", "synthesis"):
            if d.get(field) and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        # Attach a split summary so the UI can render "4 BUY / 2 SELL" at a glance
        # without needing the full per-expert drill-down.
        votes = d.get("expert_votes") or []
        if isinstance(votes, list):
            from collections import Counter
            split = Counter(v.get("direction") for v in votes if isinstance(v, dict) and v.get("direction"))
            d["vote_split"] = dict(split)
        results.append(d)
    return jsonify({"recommendations": results})


@app.route("/api/advisor/recommendations/<rec_id>/details")
def api_advisor_recommendation_details(rec_id: str):
    """Phase 7.x — per-recommendation drill-down. Returns the CIS row
    joined with the 6 ad_expert_opinions rows produced in the same
    analysis run (matched on ticker + ±60s around created_at)."""
    from tools.trading.market_intel.expert_agents import _ensure_tables
    from tools.trading.db import get_conn
    from datetime import datetime, timedelta

    _ensure_tables()
    conn = get_conn()
    try:
        rec_row = conn.execute(
            "SELECT * FROM ad_cis_recommendations WHERE id = %s", (rec_id,),
        ).fetchone()
        if not rec_row:
            return jsonify({"error": "not found"}), 404
        rec = dict(rec_row)
        for field in ("expert_votes", "synthesis"):
            if rec.get(field) and isinstance(rec[field], str):
                try:
                    rec[field] = json.loads(rec[field])
                except (json.JSONDecodeError, TypeError):
                    pass

        # Join opinions by ticker + time window (CIS + 6 opinions are
        # written within ~1s of each other).
        created = rec.get("created_at") or ""
        try:
            t0 = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            low = (t0 - timedelta(seconds=60)).isoformat()
            high = (t0 + timedelta(seconds=60)).isoformat()
        except Exception:
            low, high = "", "\uffff"

        op_rows = conn.execute(
            "SELECT expert_key, expert_name, direction, conviction, reasoning, "
            "risk_profile, created_at FROM ad_expert_opinions "
            "WHERE ticker = %s AND created_at >= %s AND created_at <= %s "
            "ORDER BY created_at ASC",
            (rec["ticker"], low, high),
        ).fetchall()
        opinions = [dict(r) for r in op_rows]
    finally:
        conn.close()

    # Merge weights from expert_votes into opinions (reasoning lives in
    # opinions; weight lives in votes).
    votes = rec.get("expert_votes") or []
    if isinstance(votes, list):
        weight_by_key = {v.get("expert_key"): v.get("weight") for v in votes
                          if isinstance(v, dict)}
        for op in opinions:
            op["weight"] = weight_by_key.get(op.get("expert_key"))

    return jsonify({"recommendation": rec, "opinions": opinions})


@app.route("/api/advisor/risk-profiles")
def api_risk_profiles():
    from tools.trading.market_intel.expert_agents import get_risk_profiles

    return jsonify({"profiles": get_risk_profiles()})


@app.route("/api/advisor/auto-trade", methods=["POST"])
def api_auto_trade():
    """Execute pending auto-trades."""
    try:
        from tools.trading.market_intel.auto_trader import execute_pending_trades

        return jsonify(execute_pending_trades())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/advisor/trading-status")
def api_trading_status():
    from tools.trading.market_intel.auto_trader import get_trading_status

    return jsonify(get_trading_status())


@app.route("/api/options/auto-trade", methods=["POST"])
def api_options_auto_trade():
    """Execute pending options auto-trades (paper portfolio)."""
    try:
        from tools.trading.options.auto_trade_options import (
            ensure_audit_table, execute_pending_option_trades,
        )
        ensure_audit_table()
        return jsonify(execute_pending_option_trades())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/options/auto-trade/status")
def api_options_auto_trade_status():
    """Status summary for options auto-trade activity."""
    try:
        from tools.trading.options.auto_trade_options import (
            ensure_audit_table, get_options_auto_trade_status,
        )
        ensure_audit_table()
        return jsonify(get_options_auto_trade_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/advisor/recommendations/<rec_id>/toggle-options-autotrade", methods=["POST"])
def api_toggle_options_autotrade(rec_id):
    """Toggle auto_trade_options for a single recommendation."""
    try:
        from tools.trading.db import get_conn as _gc
        conn = _gc()
        row = conn.execute(
            "SELECT id, auto_trade_options FROM ad_cis_recommendations WHERE id = %s",
            (rec_id,),
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "recommendation not found"}), 404
        new_val = 0 if row["auto_trade_options"] else 1
        conn.execute(
            "UPDATE ad_cis_recommendations SET auto_trade_options = %s WHERE id = %s",
            (new_val, rec_id),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "id": rec_id, "auto_trade_options": new_val})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/advisor/risk-profiles/activate", methods=["POST"])
def api_activate_risk_profile():
    data = request.get_json(silent=True) or {}
    name = data.get("profile_name", "moderate")
    from tools.trading.market_intel.expert_agents import set_active_risk_profile

    return jsonify(set_active_risk_profile(name))


@app.route("/scenarios")
def page_scenarios():
    return render_template("scenarios.html")


# ---------------------------------------------------------------------------
# API — Scenarios
# ---------------------------------------------------------------------------
@app.route("/api/scenarios/templates")
def api_scenario_templates():
    from tools.trading.market_intel.scenario_engine import (
        SCENARIO_TEMPLATES,
        ECONOMIC_EVENTS,
    )

    templates = {
        k: {"name": v["name"], "category": v["category"], "description": v.get("description", "")}
        for k, v in SCENARIO_TEMPLATES.items()
    }
    return jsonify(
        {
            "templates": templates,
            "events": ECONOMIC_EVENTS,
        }
    )


@app.route("/api/scenarios/run", methods=["POST"])
def api_scenario_run():
    from flask import g
    data = request.get_json(silent=True) or {}
    scenario_key = data.get("scenario_key", "")
    prolonged = data.get("prolonged", False)
    custom_text = data.get("custom_text")
    try:
        from tools.trading.market_intel.scenario_engine import run_scenario

        if custom_text:
            result = run_scenario("custom", custom_text=custom_text, prolonged=prolonged)
        else:
            result = run_scenario(scenario_key, prolonged=prolonged)
        run_id = (result or {}).get("run_id")
        _progression_grant_safe(
            user=getattr(g, "current_user", None),
            reason="scenario_run",
            dedup_key=f"scenario_run:{run_id}" if run_id else None,
            context={"scenario": scenario_key or "custom"},
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fathomdesk/kill-switch", methods=["GET"])
def api_fathomdesk_kill_status():
    from tools.trading.risk.kill_switch import is_killed

    return jsonify(is_killed())


@app.route("/api/fathomdesk/kill-switch/trip", methods=["POST"])
def api_fathomdesk_kill_trip():
    from tools.trading.audit.trade_audit import record as audit_record
    from tools.trading.risk.kill_switch import trip

    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "manual_dashboard_trip").strip()
    by = (data.get("by") or "dashboard_user").strip()
    out = trip(reason, by)
    audit_record("killswitch_tripped", actor=by, payload={"reason": reason})
    return jsonify(out)


@app.route("/api/fathomdesk/kill-switch/clear", methods=["POST"])
def api_fathomdesk_kill_clear():
    from tools.trading.audit.trade_audit import record as audit_record
    from tools.trading.risk.kill_switch import clear

    data = request.get_json(silent=True) or {}
    by = (data.get("by") or "dashboard_user").strip()
    out = clear(by)
    audit_record("killswitch_cleared", actor=by)
    return jsonify(out)


@app.route("/api/fathomdesk/audit")
def api_fathomdesk_audit():
    from tools.trading.audit.trade_audit import query

    rows = query(
        ticker=request.args.get("ticker"),
        event_type=request.args.get("event"),
        limit=int(request.args.get("limit", 50)),
    )
    return jsonify({"count": len(rows), "events": rows})


@app.route("/api/fathomdesk/ml-health")
def api_fathomdesk_ml_health():
    from tools.trading.ml.model_registry import health

    return jsonify(health())


@app.route("/api/fathomdesk/ml-train", methods=["POST"])
def api_fathomdesk_ml_train():
    """Train one or all ML models. Body: {"model": "all"|"pillar_weights"|"fill_quality"|"regime_hmm"}"""
    import numpy as np

    data = request.get_json(silent=True) or {}
    model = (data.get("model") or "all").strip().lower()

    results = {}

    if model in ("all", "pillar_weights"):
        try:
            from tools.trading.ml.pillar_weight_learner import train as train_pillar
            results["pillar_weights"] = train_pillar()
        except Exception as exc:
            results["pillar_weights"] = {"ok": False, "error": str(exc)}

    if model in ("all", "fill_quality"):
        try:
            from tools.trading.ml.fill_quality_model import train as train_fill
            results["fill_quality"] = train_fill()
        except Exception as exc:
            results["fill_quality"] = {"ok": False, "error": str(exc)}

    if model in ("all", "regime_hmm"):
        try:
            from tools.trading.ml.regime_hmm import train as train_hmm
            rng = np.random.default_rng(42)
            n = 300
            vix = 18 + 6 * np.sin(np.linspace(0, 12, n)) + rng.normal(0, 1, n)
            slope = -0.05 + rng.normal(0, 0.02, n)
            rv = vix * 0.8 + rng.normal(0, 1, n)
            spread = 0.3 + rng.normal(0, 0.2, n)
            dxy = 103 + rng.normal(0, 1, n)
            obs = np.column_stack([vix, slope, rv, spread, dxy]).tolist()
            results["regime_hmm"] = train_hmm(obs)
        except Exception as exc:
            results["regime_hmm"] = {"ok": False, "error": str(exc)}

    from tools.trading.ml.model_registry import health
    return jsonify({"results": results, "health": health()})


@app.route("/api/fathomdesk/replay/<signal_id>/narrate", methods=["GET", "POST"])
def api_fathomdesk_replay_narrate(signal_id):
    from tools.trading.llm.signal_explainer import narrate

    refresh = request.args.get("refresh") == "1" or request.method == "POST"
    return jsonify(narrate(signal_id, use_cache=not refresh))


@app.route("/api/fathomdesk/replay/<signal_id>")
def api_fathomdesk_replay(signal_id):
    from tools.trading.audit.decision_replay import replay

    return jsonify(replay(signal_id))


@app.route("/api/fathomdesk/snapshots")
def api_fathomdesk_snapshots():
    from tools.trading.audit.decision_snapshot import list_recent

    return jsonify({"snapshots": list_recent(request.args.get("ticker"), int(request.args.get("limit", 50)))})


@app.route("/api/fathomdesk/confluence/<ticker>")
def api_fathomdesk_confluence(ticker):
    from tools.trading.analysis.confluence_scorer import evaluate_for_ticker, is_enabled

    res = evaluate_for_ticker(ticker)
    return jsonify({"enabled": is_enabled()["enabled"], **res.to_dict()})


@app.route("/api/fathomdesk/confluence-toggle", methods=["GET", "POST"])
def api_fathomdesk_confluence_toggle():
    from tools.trading.analysis.confluence_scorer import is_enabled, set_enabled

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        want = bool(data.get("enabled", True))
        by = (data.get("by") or "dashboard_user").strip()
        return jsonify(set_enabled(want, by=by))
    return jsonify(is_enabled())


@app.route("/api/fathomdesk/slippage")
def api_fathomdesk_slippage():
    from tools.trading.analytics.slippage_tracker import summary

    return jsonify(summary(days=int(request.args.get("days", 30)), alert_bps=float(request.args.get("alert_bps", 30))))


@app.route("/api/fathomdesk/attribution")
def api_fathomdesk_attribution():
    from tools.trading.analytics.strategy_attribution import attribution

    return jsonify(attribution(days=int(request.args.get("days", 30))))


@app.route("/api/fathomdesk/vol-divergence")
def api_fathomdesk_vol_divergence():
    ticker = request.args.get("ticker", "SPY")
    window = int(request.args.get("window", 20))
    key = f"vol_div_{ticker}_{window}"
    if request.args.get("refresh") != "true":
        hit = _cached_response(key, 60)
        if hit:
            return hit
    from tools.trading.analysis.vol_divergence import analyze

    result = analyze(ticker, window=window)
    _set_cache(key, result)
    return jsonify(result)


@app.route("/api/fathomdesk/hedge-recommendation")
def api_fathomdesk_hedge():
    equity = request.args.get("equity")
    key = f"hedge_{equity or 'default'}"
    if request.args.get("refresh") != "true":
        hit = _cached_response(key, 60)
        if hit:
            return hit
    from tools.trading.risk.hedge_recommender import recommend

    result = recommend(portfolio_equity_usd=float(equity) if equity else None)
    _set_cache(key, result)
    return jsonify(result)


@app.route("/api/fathomdesk/vix-term-structure")
def api_fathomdesk_vix_term():
    from tools.trading.risk.vix_term_structure import snapshot

    return jsonify(snapshot().to_dict())


@app.route("/api/fathomdesk/vix-sizing")
def api_fathomdesk_vix_sizing():
    vix_override = request.args.get("vix")
    key = f"vix_sizing_{vix_override or 'live'}"
    if request.args.get("refresh") != "true":
        hit = _cached_response(key, 60)
        if hit:
            return hit
    from tools.trading.risk.vix_sizing import current_vix, scale_for_vix

    vix = float(vix_override) if vix_override else current_vix()
    result = scale_for_vix(vix).to_dict()
    _set_cache(key, result)
    return jsonify(result)


@app.route("/api/fathomdesk/market-session")
def api_fathomdesk_market_session():
    from tools.trading.calendar.market_calendar import session_for

    return jsonify(session_for().to_dict())


@app.route("/api/fathomdesk/earnings-blackout")
def api_fathomdesk_earnings_blackout():
    from tools.trading.calendar.earnings_calendar import in_blackout

    ticker = request.args.get("ticker", "")
    if not ticker:
        return jsonify({"error": "ticker required"}), 400
    return jsonify(in_blackout(ticker))


@app.route("/api/fathomdesk/exits/execute", methods=["POST"])
def api_fathomdesk_exits_execute():
    from tools.trading.execution.exit_executor import run_once

    dry = request.args.get("dry_run") == "1"
    return jsonify(run_once(dry_run=dry))


@app.route("/api/fathomdesk/exits/active")
def api_fathomdesk_exits_active():
    from tools.trading.execution.exit_manager import list_active

    return jsonify({"active": list_active(request.args.get("ticker"))})


@app.route("/api/fathomdesk/exits/evaluate", methods=["POST"])
def api_fathomdesk_exits_evaluate():
    from tools.trading.execution.exit_manager import evaluate_exits

    return jsonify(evaluate_exits())


@app.route("/api/fathomdesk/poll-orders", methods=["POST"])
def api_fathomdesk_poll_orders():
    from tools.trading.execution.order_poller import poll_open_orders

    return jsonify(poll_open_orders(limit=int(request.args.get("limit", 200))))


@app.route("/api/fathomdesk/reconcile-positions", methods=["GET", "POST"])
def api_fathomdesk_reconcile_positions():
    from tools.trading.execution.position_reconciler import reconcile

    auto_fix = request.method == "POST" and (request.args.get("auto_fix") == "1")
    return jsonify(reconcile(auto_fix=auto_fix))


@app.route("/api/fathomdesk/risk-status")
def api_fathomdesk_risk_status():
    """Combined snapshot for risk-card UI: kill, drawdown, PDT."""
    from tools.trading.risk.drawdown_monitor import check as dd_check
    from tools.trading.risk.kill_switch import is_killed
    from tools.trading.risk.pdt_tracker import evaluate as pdt_eval

    return jsonify(
        {
            "kill_switch": is_killed(),
            "drawdown": dd_check(),
            "pdt": pdt_eval(),
        }
    )


@app.route("/api/fathomdesk/election-phase")
def api_fathomdesk_election_phase():
    """US presidential cycle phase + historical averages for FathomDesk widget."""
    from tools.trading.factors.election_phase import (
        HISTORICAL_AVG_RETURN_PCT,
        PHASE_PREMIUM_MULTIPLIER,
        PHASES,
        classify,
    )

    as_of = request.args.get("date")
    info = classify(as_of)
    return jsonify(
        {
            "current": info.to_dict(),
            "phases": [
                {
                    "phase": p,
                    "avg_annual_return_pct": HISTORICAL_AVG_RETURN_PCT[p],
                    "premium_multiplier": PHASE_PREMIUM_MULTIPLIER[p],
                }
                for p in PHASES
            ],
            "sweet_spot_window": "Oct 1 midterm year -> Jun 30 pre-election year",
            "source": "Stock Trader's Almanac, 1896+",
        }
    )


@app.route("/api/scenarios/calendar")
def api_scenario_calendar():
    days = request.args.get("days", 60, type=int)
    from tools.trading.market_intel.scenario_engine import get_upcoming_events

    events = get_upcoming_events(days_ahead=days)
    return jsonify({"events": events, "count": len(events)})


@app.route("/api/scenarios/history")
def api_scenario_history():
    from tools.trading.market_intel.scenario_engine import get_scenario_history

    limit = request.args.get("limit", 20, type=int)
    return jsonify({"runs": get_scenario_history(limit)})


@app.route("/api/scenarios/<run_id>")
def api_scenario_detail(run_id):
    from tools.trading.market_intel.scenario_engine import get_scenario_detail

    return jsonify(get_scenario_detail(run_id))


@app.route("/api/scenarios/<run_id>/send-to-signals", methods=["POST"])
def api_scenario_send_to_signals(run_id):
    data = request.get_json(silent=True) or {}
    action = data.get("action", "winners")  # winners, losers, all, or ticker name
    from tools.trading.market_intel.scenario_engine import send_scenario_to_signals

    return jsonify(send_scenario_to_signals(run_id, action))


@app.route("/api/scenarios/cascade", methods=["POST"])
def api_cascade():
    data = request.get_json(silent=True) or {}
    trigger = data.get("trigger")
    custom = data.get("custom_text")
    depth = data.get("depth", 10)
    width = data.get("width", 10)
    try:
        from tools.trading.market_intel.cascade_engine import run_cascade

        result = run_cascade(
            trigger or "custom",
            depth,
            width,
            custom_text=custom if not trigger else None,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scenarios/cascade/send-to-signals", methods=["POST"])
def api_cascade_send_to_signals():
    data = request.get_json(silent=True) or {}
    trigger = data.get("trigger")
    custom = data.get("custom_text")
    action = data.get("action", "positive")
    try:
        from tools.trading.market_intel.cascade_engine import send_cascade_to_signals

        result = send_cascade_to_signals(
            trigger or "custom",
            action=action,
            custom_text=custom if not trigger else None,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scenarios/cascade/triggers")
def api_cascade_triggers():
    from tools.trading.market_intel.cascade_engine import list_triggers

    return jsonify(list_triggers())


# ---------------------------------------------------------------------------
# Supply Chain Correlation & Monetary Catalyst API
# ---------------------------------------------------------------------------

@app.route("/api/correlation/clusters")
def api_correlation_clusters():
    """All supply chain cluster states with monetary catalyst overlay."""
    try:
        from tools.trading.market_intel.correlation_engine import score_cluster_states
        result = score_cluster_states()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "clusters": {}})


@app.route("/api/correlation/clusters/<cluster_id>")
def api_correlation_cluster_detail(cluster_id):
    """Detail for a single correlation cluster."""
    try:
        from tools.trading.market_intel.correlation_engine import (
            SUPPLY_CHAIN_CLUSTERS, score_cluster_states,
        )
        if cluster_id not in SUPPLY_CHAIN_CLUSTERS:
            return jsonify({"error": "cluster not found"}), 404
        result = score_cluster_states()
        cluster_def = SUPPLY_CHAIN_CLUSTERS[cluster_id]
        state = result["clusters"].get(cluster_id, {})
        return jsonify({
            "cluster_id": cluster_id,
            "definition": {
                "name": cluster_def["name"],
                "description": cluster_def["description"],
                "correlation_type": cluster_def["correlation_type"],
                "lead_lag_weeks": cluster_def["lead_lag_weeks"],
                "tickers": cluster_def["tickers"],
                "sectors": cluster_def["sectors"],
                "monetary_sensitivity": cluster_def["monetary_sensitivity"],
            },
            "state": state,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/correlation/ticker/<ticker>")
def api_correlation_ticker(ticker):
    """Which clusters a ticker belongs to + correlated peers."""
    try:
        from tools.trading.market_intel.correlation_engine import (
            get_clusters_for_ticker, get_correlated_peers,
        )
        clusters = get_clusters_for_ticker(ticker.upper())
        peers = get_correlated_peers(ticker.upper())
        return jsonify({
            "ticker": ticker.upper(),
            "clusters": clusters,
            "correlated_peers": peers,
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/correlation/monetary-catalysts")
def api_monetary_catalysts():
    """Active monetary catalyst signals (cheap money, buybacks, QE, TINA)."""
    try:
        from tools.trading.market_intel.correlation_engine import score_monetary_catalysts
        result = score_monetary_catalysts()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "catalysts": {}})


@app.route("/api/correlation/portfolio-concentration")
def api_portfolio_concentration():
    """Correlation concentration risk for the active portfolio."""
    try:
        from tools.trading.market_intel.correlation_engine import (
            score_portfolio_correlation_concentration,
        )
        from tools.trading.db import get_conn
        conn = get_conn()
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM ad_positions WHERE quantity > 0 LIMIT 100"
        ).fetchall()
        conn.close()
        tickers = [r[0] for r in rows]
        result = score_portfolio_correlation_concentration(tickers)
        result["portfolio_tickers"] = tickers
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/correlation/scan", methods=["POST"])
def api_correlation_scan():
    """Run a full correlation scan (clusters + monetary catalysts)."""
    try:
        from tools.trading.market_intel.correlation_engine import run_full_scan
        result = run_full_scan()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/graph/data")
def api_graph_data():
    """Get full KG graph data for D3.js visualization + gaps.

    Cached — returns instantly if KG hasn't changed.
    Pass ?refresh=true to force recompute.
    """
    try:
        from tools.trading.market_intel.gap_detector import run_full_analysis

        force = request.args.get("refresh", "false").lower() == "true"
        result = run_full_analysis(force_refresh=force)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})


@app.route("/api/graph/gaps")
def api_graph_gaps():
    """Get structural gaps only — uses run_full_analysis() cache.

    Previously bypassed the cache and recomputed communities + gaps +
    questions per request (~120 s on 286-node KG). Now slices cached
    result; cold recompute still possible when KG fingerprint changes.
    """
    try:
        from tools.trading.market_intel.gap_detector import run_full_analysis

        force = request.args.get("refresh", "false").lower() == "true"
        result = run_full_analysis(force_refresh=force)
        return jsonify(
            {
                "gaps": result.get("gaps", []),
                "questions": result.get("questions", []),
                "cached": result.get("cached", False),
            }
        )
    except Exception as e:
        return jsonify({"gaps": [], "error": str(e)})


@app.route("/api/market/kg/status")
def api_kg_status():
    """Get Knowledge Graph status. Cached for 120 seconds."""
    hit = _cached_response("kg_status", 120)
    if hit:
        return hit
    try:
        from tools.trading.market_intel.kg_seeder import get_status

        result = get_status()
        _set_cache("kg_status", result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})


@app.route("/api/market/kg/propagate/<ticker>")
def api_kg_propagate(ticker):
    """Legacy ticker-only endpoint (kept for backward compat)."""
    event = request.args.get("event", "earnings_beat")
    try:
        from tools.trading.market_intel.alert_engine import (
            propagate_supply_chain_alert,
        )

        alerts = propagate_supply_chain_alert(ticker.upper(), event)
        return jsonify(
            {
                "source": ticker.upper(),
                "event": event,
                "alerts": alerts,
                "count": len(alerts),
            }
        )
    except Exception as e:
        return jsonify({"alerts": [], "error": str(e)})


@app.route("/api/market/kg/propagate")
def api_kg_propagate_unified():
    """Unified propagation — accepts ticker, commodity, or any KG subject.

    Query params:
      subject       — label (e.g. "NVDA", "copper", "gold"). Required.
      event         — event type. Ticker: earnings_beat/miss/guidance_raise/
                                   supply_disruption. Commodity: price_up/
                                   price_down/supply_disruption/demand_spike.
      subject_type  — optional hint: ticker | commodity. Auto-detected from KG
                      when omitted.
    """
    subject = (request.args.get("subject") or "").strip()
    event = request.args.get("event") or "earnings_beat"
    subject_type = request.args.get("subject_type") or None
    if not subject:
        return jsonify({"error": "missing ?subject=..."}), 400
    try:
        from tools.trading.market_intel.alert_engine import propagate_subject
        return jsonify(propagate_subject(subject, event, subject_type))
    except Exception as e:
        return jsonify({"alerts": [], "error": str(e)}), 500


@app.route("/api/market/kg/commodities")
def api_kg_commodities():
    """Return all commodity nodes currently in the KG, with exposure counts.

    Powers the commodity dropdown on the /market Supply Chain Propagation UI.
    """
    try:
        from tools.trading.market_intel.alert_engine import list_commodities
        return jsonify({"commodities": list_commodities()})
    except Exception as e:
        return jsonify({"commodities": [], "error": str(e)}), 500


@app.route("/api/market/supply-chain/theme")
def api_supply_chain_theme():
    """Theme Intelligence — supply chain analysis for an industry concept.

    Query params:
      query  — natural-language theme, e.g. 'electric vehicle', 'machine learning'
    Returns a bill of materials with country sourcing, concentration risk,
    exposed tickers, and opportunity signals.
    """
    query = (request.args.get("query") or "").strip()
    if not query:
        return jsonify({"error": "missing ?query=..."}), 400
    try:
        from tools.trading.market_intel.alert_engine import propagate_theme
        return jsonify(propagate_theme(query))
    except Exception as e:
        return jsonify({"error": str(e), "query": query}), 500


@app.route("/api/market/supply-chain/themes")
def api_supply_chain_themes():
    """Return all available themes with keyword hints (powers the autocomplete)."""
    try:
        from tools.trading.market_intel.alert_engine import list_themes
        return jsonify({"themes": list_themes()})
    except Exception as e:
        return jsonify({"themes": [], "error": str(e)}), 500


@app.route("/api/market/geopolitical/country-dependency")
def api_country_dependency():
    """Country Supply Dependency Report — risks + investment opportunities.

    Query params:
      country  — e.g. 'China', 'Russia', 'Taiwan', 'India', 'Kazakhstan'
    Returns commodity dependencies, impacted themes, and categorized investment plays.
    """
    country = (request.args.get("country") or "").strip()
    if not country:
        from tools.trading.market_intel.alert_engine import ANALYZABLE_COUNTRIES
        return jsonify({"error": "missing ?country=...", "suggestions": ANALYZABLE_COUNTRIES}), 400
    try:
        from tools.trading.market_intel.alert_engine import get_country_dependency_report
        return jsonify(get_country_dependency_report(country))
    except Exception as e:
        return jsonify({"error": str(e), "country": country}), 500


@app.route("/api/market/geopolitical/countries")
def api_geopolitical_countries():
    """Return list of countries with full dependency profiles."""
    try:
        from tools.trading.market_intel.alert_engine import ANALYZABLE_COUNTRIES
        return jsonify({"countries": ANALYZABLE_COUNTRIES})
    except Exception as e:
        return jsonify({"countries": [], "error": str(e)}), 500


# ---------------------------------------------------------------------------
# API — Orders
# ---------------------------------------------------------------------------
@app.route("/api/orders")
def api_orders():
    from tools.trading.db import get_conn
    uid = _active_uid()
    tid = _active_tenant_id()

    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM ad_orders WHERE user_id=%s AND tenant_id=%s "
        "ORDER BY created_at DESC LIMIT 50",
        (uid, tid),
    ).fetchall()
    conn.close()
    return jsonify({"orders": [dict(r) for r in rows], "total_count": len(rows)})


@app.route("/api/orders/place", methods=["POST"])
def api_place_order():
    """Place a live broker order (market / limit / stop / stop_limit).

    Body (JSON):
        ticker       str   — required
        side         str   — "buy" | "sell"
        qty          float — required, > 0
        order_type   str   — "market" | "limit" | "stop" | "stop_limit" (default "market")
        limit_price  float — required for limit / stop_limit
        stop_price   float — required for stop / stop_limit
        time_in_force str  — "day" | "gtc" | "ioc" | "fok" (default "day")
        provider     str   — optional broker override
    """
    from flask import g
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401

    data = request.get_json(silent=True) or {}
    ticker = (data.get("ticker") or "").strip().upper()
    side = (data.get("side") or "").strip().lower()
    order_type = (data.get("order_type") or "market").strip().lower()
    time_in_force = (data.get("time_in_force") or "day").strip().lower()
    provider = data.get("provider") or None

    if not ticker:
        return jsonify({"error": "ticker required"}), 400
    if side not in ("buy", "sell"):
        return jsonify({"error": "side must be 'buy' or 'sell'"}), 400
    if order_type not in ("market", "limit", "stop", "stop_limit"):
        return jsonify({"error": "order_type must be market | limit | stop | stop_limit"}), 400

    try:
        qty = float(data.get("qty") or 0)
        if qty <= 0:
            raise ValueError("qty must be > 0")
    except (TypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400

    limit_price: float | None = None
    stop_price: float | None = None
    if order_type in ("limit", "stop_limit"):
        try:
            limit_price = float(data["limit_price"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "limit_price required for limit / stop_limit orders"}), 400
    if order_type in ("stop", "stop_limit"):
        try:
            stop_price = float(data["stop_price"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "stop_price required for stop / stop_limit orders"}), 400

    uid = _active_uid()
    try:
        from tools.trading.execution import order_manager
        result = order_manager.place_order(
            ticker=ticker,
            side=side,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            time_in_force=time_in_force,
            provider=provider,
            user_id=uid,
        )
        return jsonify({
            "ok": True,
            "order_id": result.get("id") or result.get("txid"),
            "status": result.get("status"),
            "source": result.get("source"),
            "ticker": ticker,
            "side": side,
            "qty": qty,
            "order_type": order_type,
            "limit_price": limit_price,
            "stop_price": stop_price,
            "payload": result,
        })
    except Exception as e:
        return jsonify({"error": str(e), "ok": False}), 400


# ---------------------------------------------------------------------------
# API — Risk
# ---------------------------------------------------------------------------
@app.route("/api/risk")
def api_risk():
    from tools.trading.db import get_conn
    uid = _active_uid()
    tid = _active_tenant_id()

    conn = get_conn()
    pf_row = conn.execute(
        "SELECT id FROM ad_portfolios WHERE user_id=%s AND tenant_id=%s LIMIT 1",
        (uid, tid),
    ).fetchone()
    positions = conn.execute(
        "SELECT * FROM ad_positions WHERE portfolio_id=%s AND qty > 0",
        (pf_row["id"] if pf_row else "",),
    ).fetchall() if pf_row else []
    # ad_signals is shared across users (market-wide signals — not per-user)
    pending_count = conn.execute(
        "SELECT COUNT(*) FROM ad_signals WHERE status='pending'"
    ).fetchone()[0]
    conn.close()

    return jsonify(
        {
            "portfolio_var": 0.0,
            "max_drawdown": 0.0,
            "position_count": len(positions),
            "pending_signals": pending_count,
            "concentration_risk": ("low" if len(positions) < 5 else "medium"),
        }
    )


# ---------------------------------------------------------------------------
# API — Health
# ---------------------------------------------------------------------------
@app.route("/api/health")
def api_health():
    from tools.trading.db import get_conn

    try:
        conn = get_conn()
        run_count = conn.execute("SELECT COUNT(*) FROM ad_analysis_runs").fetchone()[0]
        signal_count = conn.execute("SELECT COUNT(*) FROM ad_signals").fetchone()[0]
        conn.close()
        return jsonify(
            {
                "status": "healthy",
                "app": "fathomdesk",
                "version": "1.0.0",
                "db": "connected",
                "runs": run_count,
                "signals": signal_count,
            }
        )
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Pages — Strategist
# ---------------------------------------------------------------------------
@app.route("/strategist")
def page_strategist():
    return render_template("strategist.html")


# ---------------------------------------------------------------------------
# API — Strategist (autonomous portfolio strategy engine)
# ---------------------------------------------------------------------------
@app.route("/api/strategist/latest")
def api_strategist_latest():
    """Get the most recent strategy run with holdings and allocations."""
    from tools.trading.strategist.portfolio_strategist import get_latest_strategy

    strategy = get_latest_strategy()
    if not strategy:
        return jsonify({"run": None, "holdings": [], "sector_allocation": [], "signals": []})
    return jsonify(strategy)


@app.route("/api/strategist/history")
def api_strategist_history():
    """Get recent strategy run metadata."""
    from tools.trading.strategist.portfolio_strategist import get_strategy_history

    limit = request.args.get("limit", 10, type=int)
    return jsonify(get_strategy_history(limit=limit))


@app.route("/api/strategist/run", methods=["POST"])
def api_strategist_run():
    """Execute a full strategy run."""
    from tools.trading.strategist.portfolio_strategist import build_strategy

    data = request.get_json(silent=True) or {}
    tier_filter = data.get("tier")
    try:
        result = build_strategy(tier_filter=tier_filter)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/strategist/run/<run_id>")
def api_strategist_run_detail(run_id):
    """Get a specific strategy run by ID."""
    from flask import g
    from tools.trading.db import get_conn

    conn = get_conn()
    run = conn.execute("SELECT * FROM ad_strategy_runs WHERE id = %s", (run_id,)).fetchone()
    if not run:
        return jsonify({"error": "Run not found"}), 404
    _cu = getattr(g, "current_user", None) or {}
    _uid = _cu.get("id") or "default"
    _tid = _cu.get("tenant_id") or "default"
    _run = dict(run)
    if _run.get("user_id", "default") != _uid and _run.get("tenant_id", "default") != _tid:
        return jsonify({"error": "Run not found"}), 404

    holdings = conn.execute(
        "SELECT * FROM ad_strategy_holdings WHERE run_id = %s ORDER BY weight_pct DESC",
        (run_id,),
    ).fetchall()
    allocations = conn.execute(
        "SELECT * FROM ad_strategy_sector_allocation WHERE run_id = %s ORDER BY target_weight_pct DESC",
        (run_id,),
    ).fetchall()
    signals = conn.execute(
        "SELECT * FROM ad_strategy_signals WHERE run_id = %s ORDER BY created_at DESC",
        (run_id,),
    ).fetchall()

    return jsonify(
        {
            "run": dict(run),
            "holdings": [dict(h) for h in holdings],
            "sector_allocation": [dict(a) for a in allocations],
            "signals": [dict(s) for s in signals],
        }
    )


@app.route("/api/strategist/backtest", methods=["POST"])
def api_strategist_backtest():
    """Quick backtest of current strategy allocation."""
    from tools.trading.strategist.portfolio_strategist import (
        build_strategy,
        compute_ticker_performance,
        TIER_WEIGHTS,
    )

    result = build_strategy()
    tier_perf = {}
    for tier_name, members in result["tiers"].items():
        if not members:
            continue
        total_weight = sum(m["weight_pct"] for m in members) or 1
        weighted_return = sum(
            (compute_ticker_performance(m["ticker"]).get("p1y", 0) or 0) * (m["weight_pct"] / total_weight)
            for m in members
        )
        tier_perf[tier_name] = round(weighted_return, 2)

    portfolio_return = round(
        sum(tier_perf.get(t, 0) * ((TIER_WEIGHTS[t][0] + TIER_WEIGHTS[t][1]) / 2) for t in TIER_WEIGHTS), 2
    )

    return jsonify(
        {
            "run_id": result["run_id"],
            "regime": result["regime"],
            "tier_performance_1y": tier_perf,
            "portfolio_return_1y": portfolio_return,
        }
    )


# ---------------------------------------------------------------------------
# Today digest (Phase 4 R1) — flagship democratize feature for retail persona
# ---------------------------------------------------------------------------
@app.route("/today")
def page_today():
    return render_template("today.html")


# ─── Billing: /billing page + tier + usage (Phase 5A) ────────────────
@app.route("/billing")
def page_billing():
    return render_template("billing.html")


@app.route("/api/billing/summary")
def api_billing_summary():
    from flask import g
    from tools.trading.billing import tiers as _bt
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    tid = _active_tenant_id()
    uid = g.current_user["id"]
    return jsonify(_bt.usage_summary(tid, user_id=uid))


@app.route("/api/billing/tier", methods=["POST"])
def api_billing_set_tier():
    """Owner-only: direct tier set. Used by operator/admin flows and by
    the UI when downgrading to the free tier (which has no Stripe price).
    Paid-tier upgrades should route through /api/billing/checkout so the
    subscription gets created in Stripe — this endpoint does NOT create
    a subscription; it only flips the local plan_tier column."""
    from flask import g
    from tools.trading.billing import tiers as _bt
    if not getattr(g, "current_user", None) or not getattr(g, "current_tenant", None):
        return jsonify({"error": "auth required"}), 401
    role = (g.current_user.get("role_in_tenant") or "").lower()
    if role != "owner":
        return jsonify({"error": "owner only"}), 403
    data = request.get_json(silent=True) or {}
    tier = str(data.get("tier") or "").strip().lower()
    if tier not in _bt.list_tiers():
        return jsonify({"error": f"unknown tier: {tier}",
                         "valid": _bt.list_tiers()}), 400
    _bt.set_tier_for_tenant(g.current_tenant["id"], tier)
    return jsonify({"ok": True, "tier": tier})


# ─── Billing: Stripe checkout / portal / webhooks / invoices (Phase 5B) ────
def _stripe_return_urls(action: str) -> tuple[str, str]:
    """Build success + cancel URLs. Honors STRIPE_SUCCESS_URL / _CANCEL_URL
    env vars so reverse-proxy deployments can override the origin."""
    import os as _os
    base = (_os.environ.get("STRIPE_SUCCESS_URL") or "").strip()
    cancel = (_os.environ.get("STRIPE_CANCEL_URL") or "").strip()
    if not base:
        origin = request.host_url.rstrip("/")
        base = f"{origin}/billing?checkout={action}"
    if not cancel:
        origin = request.host_url.rstrip("/")
        cancel = f"{origin}/billing?checkout=cancel"
    return base, cancel


@app.route("/api/billing/checkout", methods=["POST"])
def api_billing_checkout():
    """Owner-only: create a Stripe Checkout Session for a paid-tier upgrade.
    Returns `{url}` — caller 303-redirects the browser."""
    from flask import g
    from tools.trading.billing import stripe_client as sc
    from tools.trading.billing import tiers as _bt
    from tools.trading.auth import mfa as a_mfa

    if not getattr(g, "current_user", None) or not getattr(g, "current_tenant", None):
        return jsonify({"error": "auth required"}), 401
    role = (g.current_user.get("role_in_tenant") or "").lower()
    if role != "owner":
        return jsonify({"error": "owner only"}), 403
    # Step-up MFA: changing billing is sensitive; require a fresh MFA
    # session if the user has MFA enabled (matches BYOK pattern).
    try:
        if a_mfa.has_mfa(g.current_user["id"]) and not a_mfa.session_mfa_satisfied(g.current_session):
            return jsonify({"error": "step-up MFA required",
                             "redirect": "/mfa/verify"}), 403
    except Exception:
        pass

    data = request.get_json(silent=True) or {}
    tier = str(data.get("tier") or "").strip().lower()
    if tier not in _bt.list_tiers():
        return jsonify({"error": f"unknown tier: {tier}"}), 400
    if tier == _bt.default_tier():
        return jsonify({"error": "free tier doesn't use Checkout — use /api/billing/tier"}), 400

    success_url, cancel_url = _stripe_return_urls("success")
    try:
        session = sc.create_checkout_session(
            tenant=g.current_tenant,
            tier_slug=tier,
            success_url=success_url,
            cancel_url=cancel_url,
            user_email=(g.current_user.get("email") or None),
        )
    except sc.StripeNotInstalled:
        return jsonify({"error": "stripe SDK not installed on server",
                         "code": "stripe_not_installed"}), 501
    except sc.StripeNotConfigured as e:
        return jsonify({"error": str(e), "code": "stripe_not_configured"}), 501
    except Exception as e:
        return jsonify({"error": f"Stripe error: {e}"}), 502
    return jsonify({"ok": True, "url": session["url"], "id": session["id"]})


@app.route("/api/billing/portal", methods=["POST"])
def api_billing_portal():
    """Owner-only: create a self-service Stripe Billing Portal link."""
    from flask import g
    from tools.trading.billing import stripe_client as sc

    if not getattr(g, "current_user", None) or not getattr(g, "current_tenant", None):
        return jsonify({"error": "auth required"}), 401
    role = (g.current_user.get("role_in_tenant") or "").lower()
    if role != "owner":
        return jsonify({"error": "owner only"}), 403

    origin = request.host_url.rstrip("/")
    return_url = f"{origin}/billing"
    try:
        session = sc.create_portal_session(tenant=g.current_tenant, return_url=return_url)
    except sc.StripeNotInstalled:
        return jsonify({"error": "stripe SDK not installed on server",
                         "code": "stripe_not_installed"}), 501
    except sc.StripeNotConfigured as e:
        return jsonify({"error": str(e), "code": "stripe_not_configured"}), 501
    except Exception as e:
        return jsonify({"error": f"Stripe error: {e}"}), 502
    return jsonify({"ok": True, "url": session["url"]})


@app.route("/api/billing/webhook", methods=["POST"])
def api_billing_webhook():
    """Stripe webhook endpoint. Authenticates via signed payload (NOT
    session cookie). Whitelisted in auth middleware.

    Signature verification uses STRIPE_WEBHOOK_SECRET. Events are
    idempotent — replays short-circuit via ad_stripe_events.
    """
    from tools.trading.billing import stripe_client as sc
    from tools.trading.billing import webhooks as wh

    sig = request.headers.get("Stripe-Signature", "")
    raw = request.get_data(cache=False)
    try:
        event = sc.construct_webhook_event(raw, sig)
    except sc.StripeNotInstalled:
        return jsonify({"error": "stripe SDK not installed"}), 501
    except sc.StripeNotConfigured as e:
        return jsonify({"error": str(e)}), 501
    except ValueError:
        return jsonify({"error": "invalid payload"}), 400
    except Exception as e:
        # stripe.error.SignatureVerificationError lands here — treat as 400
        return jsonify({"error": f"signature verification failed: {e}"}), 400

    try:
        result = wh.handle_event(dict(event))
    except Exception as e:
        # Returning 500 makes Stripe retry — the right behavior on a
        # transient DB error. The event is NOT recorded on failure, so
        # the retry will re-execute the handler.
        app.logger.exception("stripe webhook handler failed: %s", e)
        return jsonify({"error": "internal error"}), 500
    return jsonify({"received": True, **result})


@app.route("/api/billing/subscription")
def api_billing_subscription():
    """Current subscription snapshot for the active tenant."""
    from flask import g
    from tools.trading.billing import db as bdb
    if not getattr(g, "current_user", None) or not getattr(g, "current_tenant", None):
        return jsonify({"error": "auth required"}), 401
    sub = bdb.get_active_subscription(g.current_tenant["id"])
    return jsonify({"subscription": sub})


@app.route("/api/billing/invoices")
def api_billing_invoices():
    """Owner/admin: recent invoices for the active tenant."""
    from flask import g
    from tools.trading.billing import db as bdb
    if not getattr(g, "current_user", None) or not getattr(g, "current_tenant", None):
        return jsonify({"error": "auth required"}), 401
    role = (g.current_user.get("role_in_tenant") or "").lower()
    if role not in ("owner", "admin"):
        return jsonify({"error": "owner/admin only"}), 403
    invoices = bdb.list_invoices(g.current_tenant["id"], limit=24)
    return jsonify({"invoices": invoices})


# ─── Student: /lessons page + curriculum (Phase 4 R5) ────────────────
@app.route("/lessons")
def page_lessons():
    slug = request.args.get("slug")
    return render_template("lessons.html", initial_slug=slug or "")


@app.route("/api/lessons/catalog")
def api_lessons_catalog():
    from flask import g
    from tools.trading.lessons import catalog as lessons_cat
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    uid = g.current_user["id"]
    summary = lessons_cat.curriculum_summary(uid)
    # Keep the legacy shape alongside Phase 6.5 additions for backwards compat.
    summary["summary"] = lessons_cat.summarize_progress(uid)
    return jsonify(summary)


@app.route("/api/lessons/<slug>")
def api_lesson_detail(slug):
    from flask import g
    from tools.trading.lessons import catalog as lessons_cat
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    uid = g.current_user["id"]
    meta = lessons_cat.get_lesson(slug)
    if not meta:
        return jsonify({"error": "lesson not found"}), 404
    # Phase 6.5 — enforce level + prerequisite gating at the content route.
    unlocked, hint = lessons_cat.is_lesson_unlocked(uid, slug)
    if not unlocked:
        return jsonify({"error": "lesson is locked", "hint": hint}), 403
    content = lessons_cat.load_content(slug)
    if content is None:
        return jsonify({"error": "lesson content file missing"}), 404
    # Mark as started (idempotent).
    try:
        progress = lessons_cat.get_progress(uid)
        if slug not in progress:
            lessons_cat.mark(uid, _active_tenant_id(), slug, "started")
    except Exception:
        pass
    all_lessons = lessons_cat.list_lessons()
    next_lesson = None
    prev_lesson = None
    for i, l in enumerate(all_lessons):
        if l.get("slug") == slug:
            if i + 1 < len(all_lessons):
                next_lesson = all_lessons[i + 1]
            if i > 0:
                prev_lesson = all_lessons[i - 1]
            break
    # Phase 6.5 — expose quiz questions (answers stripped) + best attempt.
    quiz = meta.get("quiz") or None
    quiz_payload = None
    if quiz:
        quiz_payload = {
            "pass_percent": float(quiz.get("pass_percent") or 70),
            "questions": [
                {"id": q.get("id"), "text": q.get("text"),
                 "options": q.get("options") or []}
                for q in (quiz.get("questions") or [])
            ],
            "best_attempt": lessons_cat.best_quiz_attempt(uid, slug),
        }
    return jsonify({
        "meta": meta,
        "content": content,
        "next": next_lesson,
        "prev": prev_lesson,
        "quiz": quiz_payload,
    })


@app.route("/api/lessons/<slug>/complete", methods=["POST"])
def api_lesson_mark_complete(slug):
    from flask import g
    from tools.trading.lessons import catalog as lessons_cat
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    meta = lessons_cat.get_lesson(slug)
    if not meta:
        return jsonify({"error": "lesson not found"}), 404
    # Phase 6.5 — if the lesson has a quiz, users must pass it (via the
    # /quiz endpoint) rather than self-mark-complete. This prevents
    # bypassing the curriculum gate.
    if meta.get("quiz"):
        return jsonify({
            "error": "this lesson has a quiz — submit quiz answers to complete it",
            "code": "quiz_required",
        }), 400
    try:
        lessons_cat.mark(g.current_user["id"], _active_tenant_id(),
                          slug, "completed")
        _progression_grant_safe(
            user=g.current_user,
            reason="lesson_completed",
            dedup_key=f"lesson_completed:{slug}",
            context={"slug": slug},
        )
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/lessons/<slug>/quiz", methods=["POST"])
def api_lesson_quiz_submit(slug):
    """Phase 6.5 — grade a quiz attempt. On pass, the lesson auto-marks
    complete + the XP hook fires via the standard path. Every attempt
    is recorded in `ad_user_quiz_attempts` (append-only NIST AU)."""
    from flask import g
    from tools.trading.lessons import catalog as lessons_cat
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    uid = g.current_user["id"]
    meta = lessons_cat.get_lesson(slug)
    if not meta:
        return jsonify({"error": "lesson not found"}), 404
    unlocked, hint = lessons_cat.is_lesson_unlocked(uid, slug)
    if not unlocked:
        return jsonify({"error": "lesson is locked", "hint": hint}), 403
    if not lessons_cat.get_quiz(slug):
        return jsonify({"error": "no quiz on this lesson"}), 400
    data = request.get_json(silent=True) or {}
    answers = data.get("answers") or {}
    if not isinstance(answers, dict):
        return jsonify({"error": "answers must be a dict"}), 400

    grading = lessons_cat.grade_quiz(slug, answers)
    if grading.get("error"):
        return jsonify(grading), 400
    lessons_cat.record_quiz_attempt(
        user_id=uid, tenant_id=_active_tenant_id(),
        slug=slug, answers=answers, grading=grading,
    )
    # On pass, idempotently mark complete + grant XP. grant_xp_safe's
    # dedup key makes the XP grant fire-and-forget safe on retries.
    if grading.get("passed"):
        try:
            lessons_cat.mark(uid, _active_tenant_id(), slug, "completed")
            _progression_grant_safe(
                user=g.current_user,
                reason="lesson_completed",
                dedup_key=f"lesson_completed:{slug}",
                context={"slug": slug, "via": "quiz"},
            )
        except Exception:
            pass
    return jsonify({"ok": True, **grading})


# ─── Lesson level certificates (Phase 6.5.1) ──────────────────────────

@app.route("/api/lessons/certificate/<level_key>")
def api_lesson_certificate(level_key):
    """Generate and stream a PDF certificate for completing all lessons in a level.
    Returns 403 if the user has not completed every lesson in the level."""
    from flask import g, make_response
    from tools.trading.lessons import catalog as _cat, certificates as _cert
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    uid = g.current_user["id"]
    levels = _cat.list_levels()
    if level_key not in levels:
        return jsonify({"error": "level not found"}), 404
    # Verify completion — _level_all_completed is the authoritative gate.
    from tools.trading.lessons.catalog import _level_all_completed
    if not _level_all_completed(uid, level_key):
        return jsonify({"error": "not all lessons in this level are complete"}), 403
    level_info = levels[level_key]
    level_label = level_info.get("label") or level_key.replace("_", " ").title()
    display_name = (g.current_user.get("display_name") or
                    g.current_user.get("email") or "Learner")
    tenant_id = g.current_user.get("tenant_id")
    try:
        pdf_bytes = _cert.generate(
            user_display_name=display_name,
            level_key=level_key,
            level_label=level_label,
            tenant_id=tenant_id,
        )
    except ImportError:
        return jsonify({"error": "PDF generation not available — reportlab not installed"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    resp = make_response(pdf_bytes)
    resp.headers["Content-Type"] = "application/pdf"
    safe_name = level_key.replace(" ", "_")
    resp.headers["Content-Disposition"] = f'attachment; filename="certificate_{safe_name}.pdf"'
    return resp


# ─── Progression: /progression page + XP API (Phase 6.1) ─────────────
@app.route("/progression")
def page_progression():
    return render_template("progression.html")


@app.route("/api/progression")
def api_progression():
    from flask import g
    from tools.trading.progression import engine as _pe
    from tools.trading.profile import db as _pdb
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    uid = g.current_user["id"]
    prof = _pdb.get_profile(uid) or {}
    return jsonify(_pe.progression_summary(uid, persona=prof.get("persona")))


@app.route("/api/progression/achievements")
def api_progression_achievements():
    from flask import g
    from tools.trading.progression import achievements as _ach
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    return jsonify(_ach.achievements_summary(g.current_user["id"]))


@app.route("/api/progression/events")
def api_progression_events():
    from flask import g
    from tools.trading.progression import db as _pdb
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    limit = int(request.args.get("limit", 50))
    limit = max(1, min(limit, 500))
    return jsonify({"events": _pdb.list_events(g.current_user["id"], limit=limit)})


@app.route("/api/progression/graduation")
def api_progression_graduation():
    from flask import g
    from tools.trading.progression import graduation as _grad
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    return jsonify(_grad.evaluate(g.current_user["id"]))


@app.route("/api/progression/graduation/acknowledge", methods=["POST"])
def api_progression_graduation_acknowledge():
    from flask import g
    from tools.trading.progression import graduation as _grad
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    status = _grad.acknowledge_risk_disclosure(g.current_user["id"])
    return jsonify({"ok": True, **status})


@app.route("/api/progression/graduation/options")
def api_progression_graduation_options():
    """Phase 7.5 follow-up C — options-specific gate status. UI uses this
    to show the options-graduation banner on /options when users try
    live trading (paper path ignores this entirely)."""
    from flask import g
    from tools.trading.progression import graduation as _grad
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    return jsonify(_grad.evaluate_options(g.current_user["id"]))


@app.route("/api/progression/graduation/unlock", methods=["POST"])
def api_progression_graduation_unlock():
    from flask import g
    from tools.trading.progression import graduation as _grad
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    try:
        status = _grad.unlock_if_eligible(g.current_user["id"])
    except ValueError as e:
        return jsonify({"error": str(e), "code": "criteria_not_met"}), 400
    return jsonify({"ok": True, **status})


@app.route("/api/progression/opt-in", methods=["POST"])
def api_progression_opt_in():
    from flask import g
    from tools.trading.progression import engine as _pe
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    opted_in = bool(data.get("opted_in"))
    state = _pe.set_opt_in(g.current_user["id"], opted_in)
    return jsonify({"ok": True, "opted_in": bool(state.get("opted_in"))})


# ─── Challenges: /challenges page + attempt API (Phase 6.3) ─────────
@app.route("/challenges")
def page_challenges():
    return render_template("challenges.html")


@app.route("/api/challenges")
def api_challenges_summary():
    from flask import g
    from tools.trading.challenges import engine as _ce
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    return jsonify(_ce.summary(g.current_user["id"]))


@app.route("/api/challenges/<challenge_id>/start", methods=["POST"])
def api_challenge_start(challenge_id):
    from flask import g
    from tools.trading.challenges import engine as _ce
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    try:
        att = _ce.start_attempt(
            user_id=g.current_user["id"],
            tenant_id=_active_tenant_id(),
            challenge_id=challenge_id,
        )
    except _ce.ChallengeError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "attempt": att})


@app.route("/api/challenges/attempts/<attempt_id>/abandon", methods=["POST"])
def api_challenge_abandon(attempt_id):
    from flask import g
    from tools.trading.challenges import engine as _ce
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    try:
        att = _ce.abandon_attempt(attempt_id=attempt_id, user_id=g.current_user["id"])
    except _ce.ChallengeError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "attempt": att})


# ─── Tax-lot report (Phase 7+ family-office multi-asset) ─────────────
@app.route("/api/taxes/report")
def api_taxes_report():
    from flask import g
    from tools.trading.taxes import engine as _tax
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    year = request.args.get("year")
    year_i = int(year) if year and year.isdigit() else None
    return jsonify(_tax.tax_report(g.current_user["id"], year=year_i))


@app.route("/api/taxes/realizations")
def api_taxes_realizations():
    from flask import g
    from tools.trading.taxes import lots as _lots
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    limit = int(request.args.get("limit", 200))
    return jsonify({"realizations": _lots.list_realizations(g.current_user["id"], limit=limit)})


@app.route("/api/taxes/lots")
def api_taxes_lots():
    from flask import g
    from tools.trading.taxes import lots as _lots
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    return jsonify({"lots": _lots.list_open_lots(g.current_user["id"])})


@app.route("/api/taxes/wash-sale-flags")
def api_taxes_wash_flags():
    from flask import g
    from tools.trading.taxes import lots as _lots
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    return jsonify({"flags": _lots.list_wash_sale_flags(g.current_user["id"])})


# ─── Compliance-officer dashboard (Phase 7+) ──────────────────────────
def _require_compliance_role():
    """Returns (user, error_response) — user present and role in (owner, admin),
    else an (None, jsonify+status) tuple the caller returns directly."""
    from flask import g
    if not getattr(g, "current_user", None):
        return None, (jsonify({"error": "auth required"}), 401)
    role = (g.current_user.get("role_in_tenant") or "").lower()
    if role not in ("owner", "admin"):
        return None, (jsonify({"error": "owner/admin only"}), 403)
    return g.current_user, None


@app.route("/compliance")
def page_compliance():
    return render_template("compliance.html")


@app.route("/api/compliance/crosswalk")
def api_compliance_crosswalk():
    from tools.trading.compliance import audit_aggregator as _agg
    user, err = _require_compliance_role()
    if err: return err
    return jsonify(_agg.crosswalk())


@app.route("/api/compliance/audit")
def api_compliance_audit():
    from tools.trading.compliance import audit_aggregator as _agg
    user, err = _require_compliance_role()
    if err: return err
    args = request.args
    rows = _agg.query_audit(
        since=args.get("since") or None,
        until=args.get("until") or None,
        user_id=args.get("user_id") or None,
        category=args.get("category") or None,
        source=args.get("source") or None,
        search=args.get("search") or None,
        limit=int(args.get("limit", 500) or 500),
    )
    return jsonify({"rows": rows, "count": len(rows)})


@app.route("/api/compliance/audit.csv")
def api_compliance_audit_csv():
    from tools.trading.compliance import audit_aggregator as _agg
    from flask import Response
    user, err = _require_compliance_role()
    if err: return err
    args = request.args
    rows = _agg.query_audit(
        since=args.get("since") or None,
        until=args.get("until") or None,
        user_id=args.get("user_id") or None,
        category=args.get("category") or None,
        source=args.get("source") or None,
        search=args.get("search") or None,
        limit=int(args.get("limit", 10000) or 10000),
    )
    csv_text = _agg.to_csv(rows)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(
        csv_text, mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="audit-{ts}.csv"',
        },
    )


@app.route("/api/compliance/summary")
def api_compliance_summary():
    from tools.trading.compliance import audit_aggregator as _agg
    user, err = _require_compliance_role()
    if err: return err
    args = request.args
    return jsonify(_agg.summary(
        user_id=args.get("user_id") or None,
    ))


# ─── Paper options (Phase 7.5) ────────────────────────────────────────
@app.route("/options")
def page_options():
    return render_template("options.html")


def _sanitize_floats(obj):
    """Recursively replace float NaN/Inf with None so jsonify produces valid JSON."""
    import math
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


@app.route("/api/options/chain/<ticker>")
def api_options_chain(ticker):
    from flask import g
    from tools.trading.options import chain as _chain
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    force = request.args.get("refresh") == "1"
    data = _chain.fetch_chain(ticker, force_refresh=force)
    if not data:
        return jsonify({"error": "no chain data — check Alpaca creds or ticker",
                         "ticker": (ticker or "").upper()}), 404
    return jsonify(_sanitize_floats(data))


@app.route("/api/options/contract/<contract_symbol>")
def api_options_contract(contract_symbol):
    from flask import g
    from tools.trading.options import chain as _chain
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    q = _chain.fetch_contract_quote(contract_symbol)
    if not q:
        return jsonify({"error": "no quote", "symbol": contract_symbol}), 404
    return jsonify(q)


def _paper_user_id() -> str:
    """Return current user ID, or 'local_user' for unauthenticated sessions."""
    from flask import g as _g
    u = getattr(_g, "current_user", None)
    return u["id"] if u else "local_user"


def _resolve_attempt_or_paper(attempt_id: str, user_id: str):
    """Resolve attempt_id for both challenge attempts and paper_* portfolios.

    Returns (ok, attempt_dict_or_None, error_msg).
    Paper portfolios are auto-created if missing.
    """
    from tools.trading.challenges import sandbox_db as _sdb
    if attempt_id.startswith(_sdb.PAPER_ATTEMPT_PREFIX):
        portfolio = _sdb.ensure_paper_portfolio(user_id)
        if not portfolio:
            return False, None, "paper portfolio unavailable"
        return True, {"id": attempt_id, "user_id": user_id, "status": "active"}, None
    from tools.trading.challenges import db as _cdb
    att = _cdb.get_attempt(attempt_id)
    if not att or att.get("user_id") != user_id:
        return False, None, "attempt not found"
    if att.get("status") != "active":
        return False, None, "attempt is not active"
    return True, att, None


@app.route("/api/challenges/attempts/<attempt_id>/option-orders", methods=["POST"])
def api_challenge_place_option_order(attempt_id):
    from tools.trading.challenges import sandbox_engine as _se
    user_id = _paper_user_id()
    ok, _att, err = _resolve_attempt_or_paper(attempt_id, user_id)
    if not ok:
        return jsonify({"error": err}), 404
    data = request.get_json(silent=True) or {}
    try:
        qty = float(data.get("qty") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "qty must be a number"}), 400
    try:
        result = _se.place_option_order(
            attempt_id=attempt_id, user_id=user_id,
            contract_symbol=str(data.get("contract_symbol") or ""),
            action=str(data.get("action") or ""),
            qty=qty,
        )
    except _se.SandboxError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "fill": result})


@app.route("/api/challenges/attempts/<attempt_id>/option-positions")
def api_challenge_option_positions(attempt_id):
    from tools.trading.challenges import sandbox_db as _sdb
    user_id = _paper_user_id()
    ok, _att, err = _resolve_attempt_or_paper(attempt_id, user_id)
    if not ok:
        return jsonify({"error": err}), 404
    positions = _sdb.list_option_positions(attempt_id)
    return jsonify({"positions": positions})


@app.route("/api/options/strategies")
def api_options_strategies():
    """Phase 7.5 follow-up B — strategy library catalog for /options UI."""
    from tools.trading.options import strategies as _strat
    return jsonify({"strategies": _strat.list_strategies()})


@app.route("/api/options/payoff", methods=["POST"])
def api_options_payoff():
    """Compute payoff-at-expiry for an arbitrary set of legs. Each leg:
    {option_type, strike, action, qty, premium} (premium per share).

    Optional: expiry (YYYY-MM-DD) + iv (annualized %) → also returns
    payoff_frames [{dte_remaining, days_from_now, payoff}] for the DTE slider.
    """
    from flask import g
    from tools.trading.options import strategies as _strat
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    try:
        legs = data.get("legs") or []
        underlying_price = float(data.get("underlying_price") or 0)
        price_range_pct = float(data.get("price_range_pct") or 0.25)
        payoff = _strat.compute_payoff(
            legs=legs, underlying_price=underlying_price,
            price_range_pct=price_range_pct,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    result = dict(payoff)

    # Optional: DTE frames for the Strategy Builder mid-life slider.
    expiry_str = data.get("expiry") or ""
    iv_pct = float(data.get("iv") or 0)
    if expiry_str and underlying_price > 0:
        try:
            from datetime import date as _date
            from tools.trading.options.probability import compute_payoff_at_time
            today = _date.today()
            exp = _date.fromisoformat(expiry_str)
            total_dte = max(0, (exp - today).days)
            spot_range = payoff.get("x") or []
            frames = []
            # 5 checkpoints from "now" to expiry in DTE-remaining order
            fractions = [1.0, 0.75, 0.50, 0.25, 0.0]
            for frac in fractions:
                dte_rem = round(total_dte * frac)
                days_from_now = total_dte - dte_rem
                frame_payoff = compute_payoff_at_time(
                    legs=legs,
                    spot_range=spot_range,
                    dte_remaining_days=dte_rem,
                    iv_annual_pct=iv_pct if iv_pct > 0 else None,
                )
                frames.append({
                    "dte_remaining": dte_rem,
                    "days_from_now": days_from_now,
                    "payoff": frame_payoff,
                })
            result["payoff_frames"] = frames
            result["total_dte"] = total_dte
        except Exception:
            pass  # frames are optional — don't break the base payoff

    return jsonify(result)


@app.route("/api/challenges/attempts/<attempt_id>/multileg-orders", methods=["POST"])
def api_challenge_place_multileg(attempt_id):
    """Atomic multi-leg fill — works for challenge attempts AND paper portfolios.
    Body: {legs: [{contract_symbol, action, qty}, ...], strategy_name?}"""
    try:
        from tools.trading.challenges import sandbox_engine as _se
        user_id = _paper_user_id()
        ok, _att, err = _resolve_attempt_or_paper(attempt_id, user_id)
        if not ok:
            return jsonify({"error": err}), 404
        data = request.get_json(silent=True) or {}
        legs = data.get("legs") or []
        strategy_name = data.get("strategy_name")
        try:
            result = _se.place_multileg_order(
                attempt_id=attempt_id, user_id=user_id,
                legs=legs, strategy_name=strategy_name,
            )
        except _se.SandboxError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True, **result})
    except Exception as exc:
        import traceback
        _log.error("multileg-orders failed for %s: %s\n%s", attempt_id, exc, traceback.format_exc())
        return jsonify({"error": str(exc)}), 500


# ─── Phase 7.8 — portfolio net Greeks ─────────────────────────────────
@app.route("/api/options/portfolio/greeks")
def api_options_portfolio_greeks():
    """Aggregate Δ/Γ/Θ/ν across the current user's open option positions."""
    from flask import g
    from tools.trading.options.portfolio_greeks import compute_portfolio_greeks
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    return jsonify(compute_portfolio_greeks(g.current_user["id"]))


@app.route("/api/options/portfolio-greeks/<user_id>")
def api_options_portfolio_greeks_by_user(user_id: str):
    """Portfolio greeks by user_id — no auth required (used by paper trading UI).

    Paper trading sandboxes pass attempt IDs like ``paper_u-<id>``.
    Positions are stored under the real user_id, so strip the prefix.
    """
    from tools.trading.options.portfolio_greeks import compute_portfolio_greeks
    from tools.trading.challenges.sandbox_db import PAPER_ATTEMPT_PREFIX
    try:
        real_uid = user_id[len(PAPER_ATTEMPT_PREFIX):] if user_id.startswith(PAPER_ATTEMPT_PREFIX) else user_id
        return jsonify(compute_portfolio_greeks(user_id=real_uid))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─── Phase 7.6 — options coach events ─────────────────────────────────
@app.route("/api/options/coach/events")
def api_options_coach_events():
    """Recent coach events for the current user. Used by /portfolio
    card + /options position panel."""
    from flask import g
    from tools.trading.options import coach_db as _cdb
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    limit = int(request.args.get("limit") or 20)
    rows = _cdb.list_events(g.current_user["id"], limit=limit)
    # Drop the verbose snapshot JSON from the list payload — callers can
    # hit /api/options/coach/events/<id> for the full snapshot if needed.
    compact = [
        {
            "id": r["id"],
            "position_id": r["position_id"],
            "event_type": r["event_type"],
            "severity": r["severity"],
            "summary": r["summary"],
            "recommendation": r.get("recommendation"),
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return jsonify({"events": compact})


@app.route("/api/options/coach/events/<event_id>")
def api_options_coach_event(event_id):
    from flask import g
    from tools.trading.options import coach_db as _cdb
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    ev = _cdb.get_event(event_id)
    if not ev or ev.get("user_id") != g.current_user["id"]:
        return jsonify({"error": "event not found"}), 404
    return jsonify(ev)


# ─── Phase 7.6 — AI-assisted options flow ─────────────────────────────
@app.route("/api/options/ai-assist/propose", methods=["POST"])
def api_options_ai_assist_propose():
    """Parse intent → rank strategies → pick strikes/expiry → run preflight.

    Body: {"intent_text": str, "underlying"?: str, "qty"?: int,
           "iv_percentile"?: float}
    Returns the full proposal + preflight + optional LLM-generated
    rationale paragraph grounded on the payoff dict.
    """
    from flask import g
    from tools.trading.options.intent_parser import parse_intent
    from tools.trading.options.proposal_builder import build_proposal
    from tools.trading.options.preflight import run_preflight
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    text = (data.get("intent_text") or "").strip()
    if not text:
        return jsonify({"error": "intent_text is required"}), 400
    underlying = (data.get("underlying") or "").strip() or None
    qty = int(data.get("qty") or 1)
    iv_pct = data.get("iv_percentile")
    try:
        iv_pct = float(iv_pct) if iv_pct is not None else None
    except (TypeError, ValueError):
        iv_pct = None

    # Greek targets from the client-side sliders (keys: target_delta, target_theta,
    # target_vega, target_gamma).  Merged into intent so rank_strategies can boost
    # strategies that close the gap.
    greek_targets = data.get("greek_targets") or {}
    current_greeks = data.get("current_greeks") or {}

    intent = parse_intent(text, underlying=underlying)
    if greek_targets:
        intent["greek_targets"] = {**intent.get("greek_targets", {}), **greek_targets}
    if current_greeks:
        intent["current_greeks"] = current_greeks

    proposal = build_proposal(intent, qty=qty)
    preflight = run_preflight(
        proposal,
        user_id=g.current_user["id"],
        is_live=False,  # the propose endpoint is a dry-run by definition
        iv_percentile=iv_pct,
    )

    # Greek gap analysis: show how this proposal moves the needle vs targets
    gap_analysis = _compute_greek_gap_analysis(
        current_greeks, greek_targets, proposal.get("net_greeks") or {}
    )

    # Plain-English rationale — grounded on proposal facts. LLM is best-effort;
    # rule template fills in when unavailable.
    rationale = _ai_options_rationale(intent, proposal, preflight)

    return jsonify({
        "intent": intent,
        "proposal": proposal,
        "preflight": preflight,
        "rationale": rationale,
        "gap_analysis": gap_analysis,
    })


def _compute_greek_gap_analysis(
    current_greeks: dict, greek_targets: dict, proposal_net_greeks: dict
) -> dict:
    """Compute how a proposal moves portfolio Greeks relative to targets.

    Returns per-greek: before_gap, after_gap, improvement (positive = better).
    """
    greek_map = {
        "delta": "target_delta",
        "gamma": "target_gamma",
        "theta": "target_theta",
        "vega":  "target_vega",
    }
    result: dict = {}
    any_target = any(greek_targets.get(v, 0) != 0 for v in greek_map.values())
    if not any_target and not current_greeks:
        return result
    for gk, tk in greek_map.items():
        cur = float(current_greeks.get(gk) or current_greeks.get(f"net_{gk}") or 0)
        tgt = float(greek_targets.get(tk) or 0)
        prop_delta = float(proposal_net_greeks.get(gk) or 0)
        before_gap = tgt - cur
        after_gap = tgt - (cur + prop_delta)
        improvement = abs(before_gap) - abs(after_gap)
        result[gk] = {
            "before_gap": round(before_gap, 4),
            "after_gap": round(after_gap, 4),
            "improvement": round(improvement, 4),
        }
    total_improvement = sum(v["improvement"] for v in result.values())
    result["total_improvement"] = round(total_improvement, 4)
    return result


def _ai_options_rationale(intent: dict, proposal: dict, preflight: dict) -> dict:
    """LLM-backed rationale with a deterministic fallback.

    The LLM is pinned to a narrow template: it may NOT introduce new
    numbers — only rephrase those in the proposal/preflight dicts.
    """
    if proposal.get("status") != "ok":
        return {"source": "rule", "text": f"No proposal: {proposal.get('error','')}"}
    stub = proposal.get("rationale_stub") or {}
    payoff = proposal.get("payoff") or {}
    strategy_name = (stub.get("strategy") or {}).get("name") or proposal.get("strategy_id")

    # Rule-based template (also used as fallback + as a system-visible baseline).
    max_p = payoff.get("max_profit")
    max_l = payoff.get("max_loss")
    bes = payoff.get("breakevens") or []
    be_txt = (f"breakeven near ${bes[0]:.2f}"
              if len(bes) == 1
              else (f"breakevens ${bes[0]:.2f} and ${bes[-1]:.2f}"
                    if bes else "no clean breakeven in the modeled range"))
    blocks = preflight.get("blocks") or []
    warns = preflight.get("warnings") or []
    severity = ("BLOCKED" if blocks else "WARNING" if warns else "OK")
    rule_text = (
        f"Proposed {strategy_name} on {intent.get('underlying')} for a "
        f"{intent.get('direction')}/{intent.get('horizon')} thesis. "
        f"Max profit ${max_p:,.2f}, max loss ${max_l:,.2f}, {be_txt}. "
        f"Pre-flight: {severity}."
    )

    import os
    if (os.environ.get("ICDEV_NO_LLM") or "").strip().lower() in ("1", "true", "yes", "on"):
        return {"source": "rule", "text": rule_text}
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        provider, model_id, cfg = router.get_provider_for_function("chat")
        if provider is None:
            return {"source": "rule", "text": rule_text}
        import json as _json
        sys_p = ("You explain an options strategy in ≤3 sentences. "
                 "Use ONLY the numbers you see in the JSON. DO NOT introduce "
                 "new figures, predictions, or price targets.")
        user_p = (
            f"Proposal:\n{_json.dumps(stub, default=str)}\n\n"
            f"Payoff:\n{_json.dumps(payoff, default=str)[:400]}\n\n"
            f"Preflight blocks: {[b['code'] for b in blocks]}\n"
            f"Preflight warnings: {[w['code'] for w in warns]}\n"
            "Write the rationale."
        )
        req = LLMRequest(
            messages=[{"role": "user", "content": user_p}],
            system_prompt=sys_p,
            max_tokens=220,
            temperature=0.2,
            skip_injection_scan=True,
        )
        resp = provider.invoke(req, model_id, cfg)
        txt = (resp.content or "").strip()
        return {"source": "llm", "text": txt or rule_text}
    except Exception:
        return {"source": "rule", "text": rule_text}


@app.route("/api/options/ai-assist/share", methods=["POST"])
def api_options_ai_assist_share():
    """Phase 7.8 — encode a proposal into a shareable URL token.

    Body: {"proposal": {...}, "intent": {...}}
    Returns: {"token": str, "url": str}
    """
    from flask import g
    from tools.trading.options.share import encode_proposal
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    proposal = data.get("proposal") or {}
    intent = data.get("intent") or {}
    token = encode_proposal(proposal, intent)
    if not token:
        return jsonify({"error": "encoding failed (oversized or malformed)"}), 400
    base = request.host_url.rstrip("/")
    url = f"{base}/options?aiproposal={token}"
    return jsonify({"token": token, "url": url})


@app.route("/api/options/ai-assist/share/decode", methods=["POST"])
def api_options_ai_assist_share_decode():
    """Phase 7.8 — decode a share token for the recipient's browser.

    The decoded payload is a STUB. The server will re-run parse_intent
    + build_proposal + preflight on the recipient's behalf — nothing
    the URL claims is trusted.
    """
    from flask import g
    from tools.trading.options.share import decode_proposal
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    decoded = decode_proposal(token)
    if not decoded:
        return jsonify({"error": "invalid or expired token"}), 400
    return jsonify(decoded)


@app.route("/api/options/ai-assist/compare", methods=["POST"])
def api_options_ai_assist_compare():
    """Phase 7.7 — side-by-side comparison of multiple strategies.

    Body: {"intent_text": str, "underlying"?: str, "qty"?: int,
           "strategy_ids"?: list[str], "iv_percentile"?: float}

    When `strategy_ids` is supplied, build one proposal for each (bypasses
    rank_strategies). When omitted, returns the primary + all alternates
    from build_proposal. Server-side preflight runs on each proposal.
    No LLM — compare is a pure deterministic diff.
    """
    from flask import g
    from tools.trading.options.intent_parser import parse_intent
    from tools.trading.options.proposal_builder import (
        build_proposal, build_for_strategy,
    )
    from tools.trading.options.preflight import run_preflight
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    text = (data.get("intent_text") or "").strip()
    if not text:
        return jsonify({"error": "intent_text is required"}), 400
    underlying = (data.get("underlying") or "").strip() or None
    qty = int(data.get("qty") or 1)
    sids = data.get("strategy_ids") or []
    iv_pct = data.get("iv_percentile")
    try:
        iv_pct = float(iv_pct) if iv_pct is not None else None
    except (TypeError, ValueError):
        iv_pct = None

    greek_targets = data.get("greek_targets") or {}
    current_greeks = data.get("current_greeks") or {}

    intent = parse_intent(text, underlying=underlying)
    if greek_targets:
        intent["greek_targets"] = {**intent.get("greek_targets", {}), **greek_targets}
    if current_greeks:
        intent["current_greeks"] = current_greeks

    proposals: list[dict] = []

    if sids:
        for sid in sids:
            p = build_for_strategy(intent, sid, qty=qty)
            if p.get("status") == "ok":
                pf = run_preflight(p, user_id=g.current_user["id"],
                                   is_live=False, iv_percentile=iv_pct)
                proposals.append({
                    "proposal": p,
                    "preflight": pf,
                    "gap_analysis": _compute_greek_gap_analysis(
                        current_greeks, greek_targets, p.get("net_greeks") or {}
                    ),
                })
    else:
        primary = build_proposal(intent, qty=qty)
        if primary.get("status") == "ok":
            proposals.append({
                "proposal": primary,
                "preflight": run_preflight(primary, user_id=g.current_user["id"],
                                           is_live=False, iv_percentile=iv_pct),
                "gap_analysis": _compute_greek_gap_analysis(
                    current_greeks, greek_targets, primary.get("net_greeks") or {}
                ),
            })
            for alt in primary.get("alternates") or []:
                alt2 = dict(alt, status="ok",
                            warnings=[], rationale_stub={})
                proposals.append({
                    "proposal": alt2,
                    "preflight": run_preflight(alt2, user_id=g.current_user["id"],
                                               is_live=False, iv_percentile=iv_pct),
                    "gap_analysis": _compute_greek_gap_analysis(
                        current_greeks, greek_targets, alt2.get("net_greeks") or {}
                    ),
                })
    return jsonify({"intent": intent, "proposals": proposals})


@app.route("/api/options/ai-assist/execute", methods=["POST"])
def api_options_ai_assist_execute():
    """Take a vetted proposal and execute as a multileg order in the
    user's chosen sandbox attempt. Server re-runs preflight — never trust
    the client's cached preflight dict.

    Body: {"attempt_id": str, "proposal": {...}, "strategy_name"?: str,
           "iv_percentile"?: float}
    """
    from flask import g
    from tools.trading.challenges import sandbox_engine as _se
    from tools.trading.options.preflight import run_preflight
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    attempt_id = data.get("attempt_id")
    proposal = data.get("proposal") or {}
    strategy_name = data.get("strategy_name") or proposal.get("strategy_id")
    iv_pct = data.get("iv_percentile")
    try:
        iv_pct = float(iv_pct) if iv_pct is not None else None
    except (TypeError, ValueError):
        iv_pct = None

    if not attempt_id:
        return jsonify({"error": "attempt_id required"}), 400
    user_id = _paper_user_id()
    ok, _att, err = _resolve_attempt_or_paper(attempt_id, user_id)
    if not ok:
        return jsonify({"error": err}), 404

    # Server-side re-run — do NOT trust the client's preflight.
    preflight = run_preflight(
        proposal,
        user_id=user_id,
        is_live=False,
        iv_percentile=iv_pct,
    )
    if not preflight.get("allowed"):
        return jsonify({
            "error": "blocked by preflight",
            "preflight": preflight,
        }), 403

    # Map proposal legs → multileg-order legs.
    legs = []
    for leg in proposal.get("legs") or []:
        if not leg.get("symbol"):
            return jsonify({"error": "proposal leg missing OCC symbol"}), 400
        legs.append({
            "contract_symbol": leg["symbol"],
            "action": leg["action"],
            "qty": int(leg.get("qty") or leg.get("qty_ratio") or 1),
        })
    if not legs:
        return jsonify({"error": "proposal has no legs"}), 400

    try:
        result = _se.place_multileg_order(
            attempt_id=attempt_id, user_id=user_id,
            legs=legs, strategy_name=strategy_name,
        )
    except _se.SandboxError as e:
        return jsonify({"error": str(e), "preflight": preflight}), 400
    return jsonify({"ok": True, "preflight": preflight, **result})


@app.route("/api/options/active-attempts")
def api_options_active_attempts():
    """Return paper portfolio + any active challenge attempts for the sandbox picker.
    Paper account is always first and requires no auth."""
    from flask import g
    from tools.trading.challenges import sandbox_db as _sdb
    user_id = _paper_user_id()
    paper = _sdb.ensure_paper_portfolio(user_id)
    out = [{
        "id": _sdb.paper_attempt_id(user_id),
        "challenge_id": None,
        "challenge_name": "Free Paper Account",
        "is_paper": True,
        "cash": paper.get("cash_balance", _sdb.PAPER_STARTING_CASH),
        "started_at": paper.get("created_at"),
        "deadline_at": None,
    }]
    if getattr(g, "current_user", None):
        from tools.trading.challenges import db as _cdb
        from tools.trading.challenges import engine as _ce
        for a in _cdb.list_active_attempts_for_user(user_id):
            ch = _ce.get_challenge(a.get("challenge_id")) or {}
            out.append({
                "id": a["id"],
                "challenge_id": a.get("challenge_id"),
                "challenge_name": ch.get("name") or a.get("challenge_id"),
                "is_paper": False,
                "cash": None,
                "started_at": a.get("started_at"),
                "deadline_at": a.get("deadline_at"),
            })
    return jsonify({"attempts": out})


@app.route("/api/options/paper/reset", methods=["POST"])
def api_options_paper_reset():
    """Reset the standalone paper portfolio to $100K with no positions."""
    from tools.trading.challenges import sandbox_db as _sdb
    user_id = _paper_user_id()
    portfolio = _sdb.reset_paper_portfolio(user_id)
    return jsonify({"ok": True, "cash": portfolio.get("cash_balance", _sdb.PAPER_STARTING_CASH)})


@app.route("/api/options/recommended-tickers")
def api_options_recommended_tickers():
    """Return curated optionable tickers from market snapshot (with signals) or universe fallback.
    Sorted by composite_score desc so the best-signal names appear first."""
    out = []
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT ticker, sector, direction, composite_score, confidence
                FROM ad_market_snapshot
                ORDER BY composite_score DESC NULLS LAST
                LIMIT 200
            """).fetchall()
        for r in rows:
            out.append({
                "ticker": r["ticker"],
                "sector": r["sector"] or "",
                "direction": r["direction"] or "",
                "score": round(float(r["composite_score"] or 0), 3),
            })
    except Exception:
        pass

    # Fallback: pull straight from universe module (no signals, but always available)
    if not out:
        try:
            from tools.trading.market_intel.universe import get_full_universe
            uni = get_full_universe()
            for ticker, sector in sorted(uni.items()):
                out.append({"ticker": ticker, "sector": sector, "direction": "", "score": 0})
        except Exception:
            pass

    return jsonify({"tickers": out})


@app.route("/api/options/combo-analyzer", methods=["POST"])
def api_options_combo_analyzer():
    """AI Combo Analyzer — tests individual strategies + ranks synergistic pairs.

    Body (JSON):
      ticker          str   required
      expiry          str   YYYY-MM-DD — near expiry (required)
      expiry_far      str   YYYY-MM-DD — far expiry for calendar combos (optional)
      strategy_ids    list  subset to analyze; omit for all strategies
      qty             int   contracts per strategy leg (default 1)
      top_n           int   max combos to return (default 5)
    """
    body = request.get_json(silent=True) or {}
    ticker = (body.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker required"}), 400

    expiry = body.get("expiry") or ""
    expiry_far = body.get("expiry_far") or ""
    qty = max(1, int(body.get("qty") or 1))
    top_n = max(1, min(10, int(body.get("top_n") or 5)))

    # Fetch chain
    try:
        from tools.trading.options import chain as _ch
        chain_data = _ch.fetch_chain(ticker)
    except Exception as e:
        return jsonify({"error": f"chain fetch failed: {e}"}), 502

    if chain_data.get("error"):
        msg = chain_data.get("message") or chain_data["error"]
        code = 429 if chain_data["error"] == "rate_limited" else 404
        return jsonify({"error": msg}), code
    if not chain_data or not chain_data.get("contracts"):
        return jsonify({"error": f"no chain data for {ticker} — ticker may not have listed options"}), 404

    # Resolve expiry — auto-pick if not supplied
    if not expiry:
        try:
            from tools.trading.options.strike_picker import pick_expiry
            picked = pick_expiry("short", chain_data)
            expiry = picked.get("expiry") or ""
        except Exception:
            pass
    if not expiry:
        exps = sorted({c.get("expiry") for c in chain_data.get("contracts", []) if c.get("expiry")})
        expiry = exps[0] if exps else ""
    if not expiry:
        return jsonify({"error": "no valid expiry in chain"}), 400

    # Far expiry — default to ~30 DTE further than near
    if not expiry_far:
        from datetime import date, timedelta
        try:
            near_dt = date.fromisoformat(expiry)
            target_far = near_dt + timedelta(days=30)
            far_candidates = sorted(
                {c.get("expiry") for c in chain_data.get("contracts", []) if c.get("expiry")
                 and c.get("expiry") > expiry},
                key=lambda e: abs((date.fromisoformat(e) - target_far).days)
            )
            expiry_far = far_candidates[0] if far_candidates else expiry
        except Exception:
            expiry_far = expiry

    expiries = {"near": expiry, "far": expiry_far}

    # Strategy pool
    strategy_ids = body.get("strategy_ids") or []
    if not strategy_ids:
        from tools.trading.options.strategies import list_strategies
        strategy_ids = [s["id"] for s in list_strategies()]

    # Run analysis
    try:
        from tools.trading.options.combo_analyzer import analyze_combos
        result = analyze_combos(
            ticker=ticker,
            chain=chain_data,
            strategy_ids=strategy_ids,
            expiry=expiry,
            expiries=expiries,
            qty=qty,
            top_n=top_n,
        )
    except Exception as e:
        _log.exception("combo_analyzer failed for %s", ticker)
        return jsonify({"error": str(e)}), 500

    return jsonify(result)


# ─── Option Scanner (Phase 7.11) ──────────────────────────────────────────────
@app.route("/api/options/scanner", methods=["POST"])
def api_options_scanner():
    """Scan a list of tickers through the AI Combo Analyzer concurrently and
    return the top results ranked by edge_score.

    Body (JSON):
      tickers     list[str]  — tickers to scan; omit to use watchlist → universe top-50
      top_n       int        — results to return (default 10, max 25)
      qty         int        — contracts per leg (default 1)
      max_workers int        — thread concurrency (default 6, max 12)

    Returns:
      {
        scanned: int,          # tickers attempted
        hits: int,             # tickers with a valid combo
        results: [             # sorted by edge_score desc, length = top_n
          {
            rank, ticker, sector,
            strategy_1, strategy_2, combo_name, edge_score, tier, tier_color,
            pop_pct, expected_pnl, max_profit, max_loss, net_cost,
            expiry, expiry_far, spot, legs, greeks,
          }, ...
        ],
        elapsed_s: float,
      }
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    body = request.get_json(silent=True) or {}
    top_n = max(1, min(25, int(body.get("top_n") or 10)))
    qty = max(1, int(body.get("qty") or 1))
    max_workers = max(1, min(12, int(body.get("max_workers") or 6)))
    # Minimum P&L ratio filter: expected_pnl / max_loss must meet this threshold.
    # Prevents surfacing trades like exp_pnl=$14 vs max_loss=$5,487 (ratio ≈ 0.003).
    min_pnl_ratio = float(body.get("min_pnl_ratio") or 0.08)

    # signal_map: ticker → {direction, score} from ecosystem data
    signal_map: dict[str, dict] = {}

    # Resolve ticker list
    # Priority 1: explicit override from caller
    tickers: list[str] = [t.strip().upper() for t in (body.get("tickers") or []) if t.strip()]

    if not tickers:
        # Priority 2: ad_market_snapshot directional signals (BUY / SELL / SHORT)
        # sorted by composite_score — this is the FathomDesk ecosystem's primary output
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
            rows = conn.execute(
                "SELECT ticker, direction, composite_score, sector "
                "FROM ad_market_snapshot "
                "WHERE direction IN ('BUY', 'SELL', 'SHORT') "
                "ORDER BY composite_score DESC NULLS LAST "
                "LIMIT 100"
            ).fetchall()
            conn.close()
            for r in rows:
                d = dict(r)
                signal_map[d["ticker"]] = {
                    "direction": d.get("direction", ""),
                    "score": float(d.get("composite_score") or 0),
                    "sector": d.get("sector", ""),
                }
            tickers = list(signal_map.keys())
        except Exception:
            tickers = []

    if not tickers:
        # Priority 3: watchlist with signal enrichment
        try:
            from flask import g
            from tools.db.storage import get_connection
            uid = (getattr(g, "current_user", None) or {}).get("id", "default")
            conn = get_connection()
            rows = conn.execute(
                "SELECT w.ticker, s.direction, s.composite_score "
                "FROM ad_watchlists w "
                "LEFT JOIN ad_market_snapshot s ON s.ticker = w.ticker "
                "WHERE w.user_id = %s ORDER BY w.added_at DESC",
                (uid,)
            ).fetchall()
            conn.close()
            for r in rows:
                d = dict(r)
                signal_map[d["ticker"]] = {
                    "direction": d.get("direction", ""),
                    "score": float(d.get("composite_score") or 0),
                    "sector": "",
                }
            tickers = [dict(r)["ticker"] for r in rows]
        except Exception:
            tickers = []

    if not tickers:
        # Priority 4: static high-liquidity fallback
        _HIGH_LIQ = [
            "SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "AMZN", "GOOGL",
            "META", "NFLX", "CRM", "ADBE", "INTC", "QCOM", "AVGO", "MU", "AMAT", "TXN",
            "JPM", "GS", "MS", "BAC", "C", "WFC", "XLF", "V", "MA", "PYPL",
            "JNJ", "PFE", "MRNA", "ABBV", "UNH", "XLV", "CVX", "XOM", "COP", "XLE",
            "GLD", "SLV", "TLT", "HYG", "EEM", "XBI", "ARKK", "SMH", "SOXX", "VIX",
        ]
        tickers = _HIGH_LIQ

    from tools.trading.options import chain as _ch
    from tools.trading.options.combo_analyzer import analyze_combos
    from tools.trading.options.strategies import list_strategies
    from tools.trading.options.strike_picker import pick_expiry
    from datetime import date, timedelta

    strategy_ids = [s["id"] for s in list_strategies()]
    sector_map: dict[str, str] = {}
    try:
        from tools.trading.market_intel.universe import get_full_universe
        sector_map = get_full_universe()
    except Exception:
        pass

    def _scan_one(ticker: str) -> dict | None:
        sig = signal_map.get(ticker, {})
        direction = sig.get("direction", "")
        intent_lean = (
            "bullish" if direction == "BUY"
            else "bearish" if direction in ("SELL", "SHORT")
            else None
        )
        try:
            chain_data = _ch.fetch_chain(ticker)
            if chain_data.get("error") or not chain_data.get("contracts"):
                return None
            # Near expiry
            try:
                picked = pick_expiry("short", chain_data)
                expiry = picked.get("expiry") or ""
            except Exception:
                expiry = ""
            if not expiry:
                exps = sorted({c.get("expiry") for c in chain_data.get("contracts", []) if c.get("expiry")})
                expiry = exps[0] if exps else ""
            if not expiry:
                return None
            # Far expiry (~30 DTE beyond near)
            try:
                near_dt = date.fromisoformat(expiry)
                target_far = near_dt + timedelta(days=30)
                far_candidates = sorted(
                    {c.get("expiry") for c in chain_data.get("contracts", [])
                     if c.get("expiry") and c.get("expiry") > expiry},
                    key=lambda e: abs((date.fromisoformat(e) - target_far).days),
                )
                expiry_far = far_candidates[0] if far_candidates else expiry
            except Exception:
                expiry_far = expiry

            expiries = {"near": expiry, "far": expiry_far}
            result = analyze_combos(
                ticker=ticker,
                chain=chain_data,
                strategy_ids=strategy_ids,
                expiry=expiry,
                expiries=expiries,
                qty=qty,
                top_n=3,  # fetch top-3 and pick first that clears the P&L ratio bar
                intent_lean=intent_lean,
            )

            # Pick the best combo that clears the P&L ratio threshold
            candidates = []
            top_combo = result.get("top_combo")
            if top_combo:
                candidates.append(top_combo)
            candidates += (result.get("combos") or [])[:2]
            candidates += (result.get("individuals") or [])[:2]

            top = None
            for candidate in candidates:
                if not candidate or not candidate.get("edge_score"):
                    continue
                exp_pnl = (candidate.get("pop") or {}).get("expected_pnl", 0.0)
                max_loss_abs = abs(candidate.get("max_loss") or 0)
                if exp_pnl <= 0:
                    continue
                ratio = exp_pnl / max(max_loss_abs, 1.0)
                if ratio >= min_pnl_ratio:
                    top = candidate
                    top["_pnl_ratio"] = ratio
                    break

            if not top:
                return None

            exp_pnl = (top.get("pop") or {}).get("expected_pnl", 0.0)
            max_loss_abs = abs(top.get("max_loss") or 0)
            pnl_ratio = top.get("_pnl_ratio") or (exp_pnl / max(max_loss_abs, 1.0))

            return {
                "ticker": ticker,
                "signal_direction": direction,
                "signal_score": round(sig.get("score", 0), 3),
                "sector": sector_map.get(ticker, "") or sig.get("sector", ""),
                "combo_name": top.get("name") or top.get("strategy_id") or "—",
                "strategy_1": top.get("strategy_1_id") or top.get("strategy_id") or "—",
                "strategy_2": top.get("strategy_2_id") or "—",
                "edge_score": top.get("edge_score", 0.0),
                "tier": top.get("tier", ""),
                "tier_color": top.get("tier_color", "#8b949e"),
                "pop_pct": (top.get("pop") or {}).get("pop_pct"),
                "expected_pnl": exp_pnl,
                "max_profit": top.get("max_profit"),
                "max_loss": top.get("max_loss"),
                "pnl_ratio": round(pnl_ratio, 4),
                "net_cost": top.get("net_cost"),
                "expiry": expiry,
                "expiry_far": expiry_far,
                "spot": result.get("spot"),
                "legs": top.get("legs") or [],
                "greeks": top.get("greeks") or {},
            }
        except Exception as exc:
            app.logger.debug("scanner: skip %s — %s", ticker, exc)
            return None

    t0 = time.monotonic()
    results: list[dict] = []
    scanned = 0
    hits = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_scan_one, t): t for t in tickers}
        for fut in as_completed(futures):
            scanned += 1
            r = fut.result()
            if r:
                hits += 1
                results.append(r)

    # Sort by edge_score first, P&L ratio as tiebreaker
    results.sort(key=lambda x: (x.get("edge_score", 0), x.get("pnl_ratio", 0)), reverse=True)
    results = results[:top_n]
    for i, r in enumerate(results, 1):
        r["rank"] = i

    return jsonify({
        "scanned": scanned,
        "hits": hits,
        "filtered": scanned - hits,
        "min_pnl_ratio": min_pnl_ratio,
        "results": results,
        "elapsed_s": round(time.monotonic() - t0, 2),
    })


# ─── Options Oracle (Phase 7.12) ──────────────────────────────────────────────

@app.route("/api/options/oracle/run", methods=["POST"])
def api_oracle_run():
    """Trigger one oracle pass: scan → decide → enter positions.

    Body (JSON, all optional):
      mode  "paper" | "live"   override config mode
    """
    try:
        from tools.trading.options.oracle_engine import run_oracle
        body = request.get_json(silent=True) or {}
        mode = body.get("mode")
        result = run_oracle(mode=mode)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/options/oracle/status", methods=["GET"])
def api_oracle_status():
    """Return oracle config, open positions count, and last run summaries."""
    try:
        from tools.trading.options.oracle_engine import load_config, get_regime, _current_notional_exposure
        from tools.trading.options.oracle_db import count_open_positions, list_runs
        cfg = load_config()
        max_notional = float(cfg.get("entry_rules", {}).get("max_notional_exposure", 0) or 0)
        current_notional = _current_notional_exposure() if max_notional > 0 else None
        return jsonify({
            "mode": cfg.get("mode", "paper"),
            "regime": get_regime(),
            "open_positions": count_open_positions(),
            "entry_rules": cfg.get("entry_rules", {}),
            "exit_rules": cfg.get("exit_rules", {}),
            "recent_runs": list_runs(10),
            "current_notional": current_notional,
            "max_notional": max_notional if max_notional > 0 else None,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/options/oracle/positions", methods=["GET"])
def api_oracle_positions():
    """Return oracle-managed positions (open by default)."""
    try:
        from tools.trading.options.oracle_db import list_positions
        status = request.args.get("status", "open")
        limit = min(200, int(request.args.get("limit", 100)))
        return jsonify({"positions": list_positions(status=status, limit=limit)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/options/oracle/positions/pnl", methods=["GET"])
def api_oracle_positions_pnl():
    """Return estimated unrealized P&L for all open positions.

    Calls _estimate_position_pnl() for each open position. Results are
    returned as {position_id: pnl_float | null}. Slow (yfinance per position)
    so called separately from the positions list — UI fetches in background.
    """
    try:
        import json
        from tools.trading.options.oracle_db import list_positions
        from tools.trading.options.oracle_engine import _estimate_position_pnl
        positions = list_positions("open", limit=50)
        result: dict[str, float | None] = {}
        for pos in positions:
            try:
                legs = json.loads(pos.get("legs_json") or "[]")
                pnl = _estimate_position_pnl(pos["ticker"], legs, pos) if legs else None
            except Exception:
                pnl = None
            result[pos["id"]] = pnl
        return jsonify({"pnl": result})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/options/oracle/positions/<position_id>/close", methods=["POST"])
def api_oracle_close_position(position_id: str):
    """Manually close an oracle position."""
    try:
        from tools.trading.options.oracle_db import close_position, list_positions, log_trade_event
        body = request.get_json(silent=True) or {}
        reason = body.get("reason", "manual")
        realized_pnl = body.get("realized_pnl")
        # Fetch position BEFORE closing so ticker/combo_name are available for audit trail
        open_positions = list_positions("open", limit=500)
        pos = next((p for p in open_positions if p["id"] == position_id), {})
        close_position(position_id, close_reason=reason, realized_pnl=realized_pnl)
        try:
            log_trade_event(
                "manual_close", "manual", pos.get("ticker", "unknown"),
                position_id=position_id,
                combo_name=pos.get("combo_name", ""),
                realized_pnl=realized_pnl,
                notes=reason,
            )
        except Exception:
            pass
        return jsonify({"ok": True, "position_id": position_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/options/oracle/positions/<position_id>/adjust-qty", methods=["POST"])
def api_oracle_adjust_qty(position_id: str):
    """Partially close an oracle position by reducing qty.

    Body: {"close_qty": int}  — number of contracts to close (must be < current qty).
    Submits closing legs for close_qty contracts, reduces DB qty by that amount.
    If close_qty equals full qty, delegates to full close_position().
    """
    try:
        import json as _json
        from tools.trading.options.oracle_db import (
            list_positions, close_position, reduce_position_qty,
            log_trade_event,
        )
        body = request.get_json(silent=True) or {}
        try:
            close_qty = int(body.get("close_qty", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "close_qty must be an integer"}), 400
        if close_qty <= 0:
            return jsonify({"error": "close_qty must be > 0"}), 400

        open_positions = list_positions("open", limit=500)
        pos = next((p for p in open_positions if p["id"] == position_id), None)
        if not pos:
            return jsonify({"error": "position not found or already closed"}), 404

        current_qty = pos.get("qty") or 1
        if close_qty >= current_qty:
            # Full close — delegate to existing path
            close_position(position_id, close_reason="manual_partial_full")
            log_trade_event(
                "manual_close", "manual", pos.get("ticker", "unknown"),
                position_id=position_id, combo_name=pos.get("combo_name", ""),
                notes=f"full close via adjust-qty (qty={current_qty})",
            )
            return jsonify({"ok": True, "action": "full_close", "position_id": position_id})

        remaining_qty = current_qty - close_qty
        # For live mode: submit partial closing order
        if pos.get("mode") == "live":
            try:
                legs = _json.loads(pos.get("legs_json") or "[]")
                closing_legs = []
                for leg in legs:
                    cl = dict(leg)
                    orig = cl.get("action", "buy_to_open")
                    cl["action"] = ("sell_to_close" if orig in ("buy_to_open", "buy")
                                    else "buy_to_close")
                    closing_legs.append(cl)
                from tools.trading.options.option_order import submit_option_legs
                fill = submit_option_legs(closing_legs, close_qty, "live", pos.get("ticker", ""))
                if fill.status == "missed":
                    return jsonify({"error": "live partial close order missed — try again"}), 502
            except Exception as exc:
                return jsonify({"error": f"live partial close failed: {exc}"}), 500

        # Reduce qty in DB
        reduce_position_qty(position_id, remaining_qty)
        log_trade_event(
            "amendment", "manual", pos.get("ticker", "unknown"),
            position_id=position_id, combo_name=pos.get("combo_name", ""),
            notes=f"partial close: closed {close_qty} of {current_qty} contracts, {remaining_qty} remain",
        )
        return jsonify({
            "ok": True, "action": "partial_close",
            "position_id": position_id,
            "closed_qty": close_qty,
            "remaining_qty": remaining_qty,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/options/oracle/decisions", methods=["GET"])
def api_oracle_decisions():
    """Return recent oracle decisions with linked position outcome (realized_pnl, pos_status)."""
    try:
        from tools.trading.options.oracle_db import list_decisions, _conn as oracle_conn
        limit = min(200, int(request.args.get("limit", 50)))
        run_id = request.args.get("run_id", "")
        decisions = list_decisions(limit=limit, run_id=run_id)
        # Enrich decisions that have a position_id with outcome data
        pos_ids = [d["position_id"] for d in decisions if d.get("position_id")]
        if pos_ids:
            try:
                conn = oracle_conn()
                placeholders = ",".join("?" for _ in pos_ids)
                pos_rows = conn.execute(
                    f"SELECT id, status, realized_pnl, close_reason, closed_at "
                    f"FROM ad_oracle_option_positions WHERE id IN ({placeholders})",
                    pos_ids,
                ).fetchall()
                conn.close()
                pos_map = {r["id"]: dict(r) for r in pos_rows}
                for d in decisions:
                    pid = d.get("position_id")
                    if pid and pid in pos_map:
                        p = pos_map[pid]
                        d["pos_status"] = p.get("status")
                        d["realized_pnl"] = p.get("realized_pnl")
                        d["close_reason"] = p.get("close_reason")
                        d["closed_at"] = p.get("closed_at")
            except Exception:
                pass
        return jsonify({"decisions": decisions})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/options/genesis/status", methods=["GET"])
def api_genesis_status():
    """Return genesis daemon running state and reflex health."""
    try:
        from pathlib import Path
        pid_file = Path(__file__).resolve().parents[3] / ".tmp" / "options_genesis.pid"
        running = False
        pid = None
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                # os.kill(pid, 0) is unreliable on Windows; use tasklist instead
                import subprocess as _sp
                r = _sp.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    capture_output=True, text=True,
                )
                running = str(pid) in r.stdout
            except (OSError, ValueError):
                running = False

        from tools.db.storage import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM trading_daemon_reflex_state "
            "WHERE reflex_name IN ('scan_and_enter','regime_exit','position_monitor')"
        ).fetchall()
        conn.close()

        return jsonify({
            "running": running,
            "pid": pid,
            "reflexes": [dict(r) for r in rows],
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/options/genesis/start", methods=["POST"])
def api_genesis_start():
    """Start the Options Genesis Daemon in the background."""
    try:
        import subprocess
        from pathlib import Path
        _proj = Path(__file__).resolve().parents[3]
        script = _proj / "tools" / "trading" / "options" / "genesis_daemon.py"
        log_file = _proj / ".tmp" / "options_genesis.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [sys.executable, str(script)],
            stdout=open(str(log_file), "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return jsonify({"ok": True, "message": "Genesis daemon starting"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/options/genesis/stop", methods=["POST"])
def api_genesis_stop():
    """Stop the Options Genesis Daemon."""
    try:
        import os
        import signal
        from pathlib import Path
        pid_file = Path(__file__).resolve().parents[3] / ".tmp" / "options_genesis.pid"
        if not pid_file.exists():
            return jsonify({"ok": False, "message": "Daemon not running (no PID file)"})
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        return jsonify({"ok": True, "message": f"SIGTERM sent to PID {pid}"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/options/genesis/run-once", methods=["POST"])
def api_genesis_run_once():
    """Run all genesis reflexes once synchronously (for testing)."""
    try:
        body = request.get_json(silent=True) or {}
        reflex = body.get("reflex")
        from tools.trading.options import genesis_daemon as _gd
        if reflex:
            _gd._execute_reflex(reflex)
            return jsonify({"ok": True, "reflex": reflex})
        _gd.run_once()
        return jsonify({"ok": True, "message": "all reflexes executed"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/options/genesis/reset-cb", methods=["POST"])
def api_genesis_reset_cb():
    """Reset circuit breaker for a named reflex."""
    try:
        body = request.get_json(silent=True) or {}
        reflex = body.get("reflex", "")
        if not reflex:
            return jsonify({"error": "reflex name required"}), 400
        from tools.trading.options import genesis_daemon as _gd
        _gd._save_reflex_state(reflex, {"circuit_breaker_open": 0, "consecutive_failures": 0})
        return jsonify({"ok": True, "reflex": reflex, "message": f"Circuit breaker reset for {reflex}"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/options/oracle/config", methods=["GET", "POST"])
def api_oracle_config():
    """GET returns current config; POST updates args/options_oracle.yaml."""
    try:
        from tools.trading.options.oracle_engine import load_config, _CONFIG_PATH
        if request.method == "GET":
            return jsonify(load_config())

        # POST — validate then update specific keys
        body = request.get_json(silent=True) or {}

        # Validation schema: section → key → (type, min, max or allowed_values)
        _FLOAT_01 = (float, 0.0, 1.0)
        _FLOAT_POS = (float, 0.0, None)
        _INT_POS   = (int,   1,   None)
        _INT_GE0   = (int,   0,   None)
        _BOOL      = (bool,  None, None)
        _ORACLE_SCHEMA: dict[str, dict[str, tuple]] = {
            "entry_rules": {
                "min_edge":              _FLOAT_01,
                "min_pnl_ratio":         _FLOAT_01,
                "min_pop":               (float, 0.0, 100.0),
                "max_positions":         _INT_POS,
                "max_qty_per_ticker":    _INT_POS,
                "max_notional_exposure": _INT_GE0,
                "block_on_earnings":     _BOOL,
                "slippage_pct":          (float, 0.0, 10.0),
            },
            "exit_rules": {
                "regime_exit":                 _BOOL,
                "take_profit_pct":             (float, 1.0, 200.0),
                "take_profit_use_expected_pnl": _BOOL,
                "take_profit_dte":             _INT_GE0,
                "stop_loss_pct":               (float, 1.0, 500.0),
                "naked_stop_loss_multiplier":  (float, 0.5, 10.0),
                "dte_exit":                    _INT_GE0,
            },
            "scanner": {
                "top_n":              _INT_POS,
                "min_pnl_ratio":      _FLOAT_01,
                "max_workers":        (int, 1, 16),
                "worker_delay_sec":   (float, 0.0, 30.0),
                "fetch_timeout_sec":  (float, 5.0, 120.0),
            },
            "genesis": {
                "scan_and_enter_interval_min":  (int, 5, 240),
                "regime_exit_interval_min":     (int, 1, 60),
                "position_monitor_interval_min": (int, 5, 120),
                "market_hours_only":            _BOOL,
            },
        }
        _TOP_LEVEL_ALLOWED = {"mode": ("paper", "live")}

        errors = []
        for section, updates in body.items():
            if section in _TOP_LEVEL_ALLOWED:
                allowed_vals = _TOP_LEVEL_ALLOWED[section]
                if updates not in allowed_vals:
                    errors.append(f"{section}: must be one of {allowed_vals}")
                continue
            if section not in _ORACLE_SCHEMA:
                errors.append(f"unknown section '{section}'")
                continue
            if not isinstance(updates, dict):
                errors.append(f"{section}: expected dict of key/value pairs")
                continue
            section_schema = _ORACLE_SCHEMA[section]
            for k, v in updates.items():
                if k not in section_schema:
                    errors.append(f"{section}.{k}: unknown config key")
                    continue
                expected_type, lo, hi = section_schema[k]
                if expected_type == bool:
                    if not isinstance(v, bool):
                        errors.append(f"{section}.{k}: must be true/false")
                elif expected_type == float:
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        errors.append(f"{section}.{k}: must be a number")
                        continue
                elif expected_type == int:
                    try:
                        v = int(v)
                    except (TypeError, ValueError):
                        errors.append(f"{section}.{k}: must be an integer")
                        continue
                if lo is not None and v < lo:
                    errors.append(f"{section}.{k}: must be >= {lo}")
                if hi is not None and v > hi:
                    errors.append(f"{section}.{k}: must be <= {hi}")

        if errors:
            return jsonify({"error": "config validation failed", "details": errors}), 400

        import yaml
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            current = yaml.safe_load(f) or {}
        for k, v in body.items():
            if isinstance(v, dict) and isinstance(current.get(k), dict):
                current[k].update(v)
            else:
                current[k] = v
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(current, f, default_flow_style=False, allow_unicode=True)
        return jsonify({"ok": True, "config": load_config()})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/options/oracle/performance", methods=["GET"])
def api_oracle_performance():
    """Strategy win-rate leaderboard from closed oracle positions."""
    try:
        from tools.trading.options.oracle_db import get_strategy_performance
        limit = int(request.args.get("limit", 20))
        return jsonify({"rows": get_strategy_performance(limit=limit)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/options/oracle/events", methods=["GET"])
def api_oracle_events():
    """Trade event audit trail — all entry/exit/manual_close events."""
    try:
        from tools.trading.options.oracle_db import list_trade_events
        limit = int(request.args.get("limit", 50))
        position_id = request.args.get("position_id", "")
        return jsonify({"events": list_trade_events(position_id=position_id, limit=limit)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─── Sandbox paper portfolio per attempt (Phase 6.3.5) ────────────────
@app.route("/api/challenges/attempts/<attempt_id>/sandbox")
def api_challenge_sandbox(attempt_id):
    from flask import g
    from tools.trading.challenges import db as _cdb
    from tools.trading.challenges import sandbox_engine as _se
    from tools.trading.challenges import sandbox_db as _sdb
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    att = _cdb.get_attempt(attempt_id)
    if not att or att.get("user_id") != g.current_user["id"]:
        return jsonify({"error": "attempt not found"}), 404
    snap = _se.sandbox_snapshot(attempt_id, refresh_prices=True)
    orders = _sdb.list_orders(attempt_id, limit=50)
    return jsonify({"attempt": att, "sandbox": snap, "orders": orders})


@app.route("/api/challenges/attempts/<attempt_id>/orders", methods=["POST"])
def api_challenge_sandbox_order(attempt_id):
    from flask import g
    from tools.trading.challenges import db as _cdb
    from tools.trading.challenges import sandbox_engine as _se
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    att = _cdb.get_attempt(attempt_id)
    if not att or att.get("user_id") != g.current_user["id"]:
        return jsonify({"error": "attempt not found"}), 404
    if att.get("status") != "active":
        return jsonify({"error": "attempt is not active"}), 400
    data = request.get_json(silent=True) or {}
    try:
        qty = float(data.get("qty") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "qty must be a number"}), 400
    # Phase 6.3.5 follow-up #2 — accept optional order_type + limit/stop price.
    order_type = str(data.get("order_type") or "market").lower()
    def _as_float(key):
        v = data.get(key)
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    limit_price = _as_float("limit_price")
    stop_price = _as_float("stop_price")
    try:
        result = _se.place_order(
            attempt_id=attempt_id, user_id=g.current_user["id"],
            ticker=str(data.get("ticker") or ""),
            side=str(data.get("side") or ""),
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
        )
    except _se.SandboxError as e:
        return jsonify({"error": str(e)}), 400
    # Return shape depends on the order path:
    #   market  → {ok, fill: {...}}
    #   limit/stop → {ok, pending: {...}}
    if result.get("status") == "pending":
        return jsonify({"ok": True, "pending": result})
    return jsonify({"ok": True, "fill": result})


@app.route("/api/challenges/market-status")
def api_challenge_market_status():
    """Phase 6.3.5 follow-up #3 — lightweight endpoint the UI polls to
    decide whether to show the 'market closed' banner in the Trade panel."""
    from flask import g
    from tools.trading.challenges import market_hours as _mh
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    return jsonify(_mh.market_status())


@app.route("/api/challenges/attempts/<attempt_id>/pending-orders")
def api_challenge_pending_orders(attempt_id):
    from flask import g
    from tools.trading.challenges import db as _cdb
    from tools.trading.challenges import sandbox_db as _sdb
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    att = _cdb.get_attempt(attempt_id)
    if not att or att.get("user_id") != g.current_user["id"]:
        return jsonify({"error": "attempt not found"}), 404
    pending = _sdb.list_pending_orders(attempt_id, status="pending")
    return jsonify({"pending": pending})


@app.route("/api/challenges/attempts/<attempt_id>/pending-orders/<order_id>",
            methods=["DELETE"])
def api_challenge_cancel_pending_order(attempt_id, order_id):
    from flask import g
    from tools.trading.challenges import db as _cdb
    from tools.trading.challenges import sandbox_engine as _se
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    att = _cdb.get_attempt(attempt_id)
    if not att or att.get("user_id") != g.current_user["id"]:
        return jsonify({"error": "attempt not found"}), 404
    try:
        row = _se.cancel_pending_order(
            order_id=order_id, user_id=g.current_user["id"],
        )
    except _se.SandboxError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "canceled": row})


# ─── Leagues: /leagues page + standings API (Phase 6.4) ──────────────
@app.route("/leagues")
def page_leagues():
    return render_template("leagues.html")


@app.route("/api/leagues")
def api_leagues_summary():
    from flask import g
    from tools.trading.leagues import engine as _le
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    return jsonify(_le.summary_for_user(g.current_user["id"], _active_tenant_id()))


@app.route("/api/leagues", methods=["POST"])
def api_leagues_create():
    from flask import g
    from tools.trading.leagues import engine as _le
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    try:
        league = _le.create_league(
            tenant_id=_active_tenant_id(),
            name=str(data.get("name") or ""),
            description=(data.get("description") or None),
            visibility=str(data.get("visibility") or "public"),
            owner_user_id=g.current_user["id"],
        )
    except _le.LeagueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "league": league})


@app.route("/api/leagues/<league_id>")
def api_league_detail(league_id):
    from flask import g
    from tools.trading.leagues import db as _ldb, engine as _le
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    league = _ldb.get_league(league_id)
    if not league:
        return jsonify({"error": "league not found"}), 404
    if league.get("tenant_id") != _active_tenant_id():
        return jsonify({"error": "not in your workspace"}), 403
    my_mem = _ldb.get_membership(league_id, g.current_user["id"])
    # Private leagues hide themselves from non-members
    if league.get("visibility") == "private" and not my_mem:
        return jsonify({"error": "league not found"}), 404
    window = request.args.get("window", "weekly")
    standings = _le.compute_standings(league_id, window=window)
    members = _ldb.list_members(league_id)
    return jsonify({
        "league": league,
        "my_membership": my_mem,
        "members": members,
        "standings": standings,
        "windows": _le.list_windows(),
    })


@app.route("/api/leagues/<league_id>/join", methods=["POST"])
def api_league_join(league_id):
    from flask import g
    from tools.trading.leagues import engine as _le
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    try:
        _le.join_league(
            league_id=league_id, user_id=g.current_user["id"],
            tenant_id=_active_tenant_id(),
            join_code=data.get("join_code"),
        )
    except _le.LeagueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.route("/api/leagues/join-by-code", methods=["POST"])
def api_league_join_by_code():
    from flask import g
    from tools.trading.leagues import engine as _le
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    code = str(data.get("join_code") or "").strip()
    if not code:
        return jsonify({"error": "join code required"}), 400
    try:
        league = _le.join_by_code(
            user_id=g.current_user["id"],
            tenant_id=_active_tenant_id(),
            join_code=code,
        )
    except _le.LeagueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "league": league})


@app.route("/api/leagues/<league_id>/members", methods=["POST"])
def api_league_add_member(league_id):
    from flask import g
    from tools.trading.leagues import engine as _le
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    target = str(data.get("user_id") or "").strip()
    if not target:
        return jsonify({"error": "user_id required"}), 400
    try:
        _le.add_member_as_captain(
            league_id=league_id,
            caller_user_id=g.current_user["id"],
            user_id=target,
        )
    except _le.LeagueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.route("/api/leagues/<league_id>/members/<user_id>", methods=["DELETE"])
def api_league_remove_member(league_id, user_id):
    from flask import g
    from tools.trading.leagues import engine as _le
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    try:
        _le.remove_member(
            league_id=league_id,
            caller_user_id=g.current_user["id"],
            target_user_id=user_id,
        )
    except _le.LeagueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.route("/api/leagues/<league_id>", methods=["DELETE"])
def api_league_delete(league_id):
    from flask import g
    from tools.trading.leagues import engine as _le
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    try:
        _le.delete_league(league_id=league_id, caller_user_id=g.current_user["id"])
    except _le.LeagueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


# ─── League invite tokens (Phase 6.4.5) ──────────────────────────────

@app.route("/api/leagues/<league_id>/invite", methods=["POST"])
def api_league_invite_create(league_id):
    """Captain issues an invite token for a private league. Returns {token, link}."""
    from flask import g, request
    from tools.trading.leagues import engine as _le, tokens as _lt
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    try:
        _le.require_captain(league_id=league_id, caller_user_id=g.current_user["id"])
    except AttributeError:
        # require_captain may not exist — fall back to checking membership.
        try:
            from tools.trading.leagues import db as _ldb
            mem = _ldb.get_membership(league_id, g.current_user["id"])
            if not mem or mem.get("role") != "captain":
                return jsonify({"error": "captain role required"}), 403
        except Exception as e:
            return jsonify({"error": str(e)}), 403
    except _le.LeagueError as e:
        return jsonify({"error": str(e)}), 403
    token, _ = _lt.create(
        league_id=league_id,
        issuer_user_id=g.current_user["id"],
        secret_key=app.secret_key,
    )
    base = request.host_url.rstrip("/")
    return jsonify({"ok": True, "token": token, "link": f"{base}/leagues/join/{token}"})


@app.route("/leagues/join/<token>", methods=["GET", "POST"])
def page_league_join(token):
    """Accept a league invitation via signed token."""
    from flask import g, redirect
    from tools.trading.leagues import tokens as _lt, engine as _le, db as _ldb
    if not getattr(g, "current_user", None):
        return redirect(f"/login?next=/leagues/join/{token}")
    payload = _lt.peek(token, secret_key=app.secret_key)
    if not payload:
        return render_template("leagues_join.html", error="This invitation has expired or is invalid.", league=None)
    league = _ldb.get_league(payload["league_id"])
    if not league:
        return render_template("leagues_join.html", error="League not found.", league=None)

    if request.method == "POST":
        try:
            _lt.accept(token, secret_key=app.secret_key, used_by_user_id=g.current_user["id"])
            _le.join_league(
                league_id=league["id"],
                user_id=g.current_user["id"],
                tenant_id=g.current_user.get("tenant_id"),
                join_code=None,
                _bypass_private=True,
            )
        except ValueError as e:
            return render_template("leagues_join.html", error=str(e), league=league)
        except _le.LeagueError as e:
            return render_template("leagues_join.html", error=str(e), league=league)
        return redirect(f"/leagues?joined={league['id']}")

    return render_template("leagues_join.html", error=None, league=league)


# ─── Quant: /api-keys page + Bearer tokens (Phase 4 R4) ─────────────
@app.route("/api-keys")
def page_api_keys():
    return render_template("api_keys.html")


@app.route("/api/auth/api-tokens")
def api_api_tokens_list():
    from flask import g
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    from tools.trading.auth import api_tokens as a_api
    return jsonify({"tokens": a_api.list_for_user(g.current_user["id"])})


@app.route("/api/auth/api-tokens", methods=["POST"])
def api_api_tokens_create():
    """Generate a personal access token. Returns the raw token ONCE; after
    that only the last4 mask is available."""
    from flask import g
    from tools.trading.auth import api_tokens as a_api
    from tools.trading.auth import mfa as a_mfa
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    # Step-up: issuing a token = granting long-lived account access
    if a_mfa.has_mfa(g.current_user["id"]) and not a_mfa.session_mfa_satisfied(g.current_session):
        return jsonify({"error": "step-up MFA required",
                         "redirect": "/mfa/verify"}), 403
    # Phase 5A: per-user API token quota
    try:
        from tools.trading.billing import tiers as _bt
        _bt.check_quota(_active_tenant_id(), "api_tokens_per_user",
                         _bt.count_api_tokens_for_user(g.current_user["id"]))
    except _bt.QuotaExceeded as qe:
        return jsonify({"error": str(qe), "quota": qe.quota_key,
                         "limit": qe.limit, "tier": qe.tier,
                         "upgrade_url": "/billing"}), 402
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip() or "Unnamed token"
    ttl_days = data.get("ttl_days")
    try:
        ttl_days = int(ttl_days) if ttl_days not in (None, "") else None
    except (TypeError, ValueError):
        ttl_days = None
    try:
        raw, tid = a_api.create_token(
            user_id=g.current_user["id"],
            tenant_id=_active_tenant_id(),
            name=name,
            ttl_days=ttl_days,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "id": tid, "token": raw,
                     "warning": "This is the ONLY time you'll see the full token. Store it safely now."})


@app.route("/api/auth/api-tokens/<token_id>", methods=["DELETE"])
def api_api_tokens_revoke(token_id):
    from flask import g
    from tools.trading.auth import api_tokens as a_api
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    n = a_api.revoke(g.current_user["id"], token_id)
    return jsonify({"revoked": n})


# ─── Advisor: /clients page + share-link generator (Phase 4 R3) ─────
@app.route("/clients")
def page_clients():
    return render_template("clients.html")


@app.route("/api/clients")
def api_clients_roster():
    """Extends /api/tenant/members with per-client activity summary
    (order count, last login, alert rules, unack alerts)."""
    from flask import g
    if not getattr(g, "current_user", None) or not getattr(g, "current_tenant", None):
        return jsonify({"error": "auth required"}), 401
    role = (g.current_user.get("role_in_tenant") or "").lower()
    if role not in ("owner", "admin"):
        return jsonify({"error": "owner or admin required"}), 403
    from tools.trading.tenancy import db as tenant_db
    from tools.db.storage import get_connection, sql_placeholder
    members = tenant_db.list_tenant_members(g.current_tenant["id"])
    conn = get_connection()
    ph = sql_placeholder(conn)
    try:
        # Per-client activity — join + aggregate in a handful of cheap queries
        for m in members:
            uid = m.get("user_id")
            try:
                row = conn.execute(
                    f"SELECT COUNT(*) AS c FROM ad_orders WHERE user_id = {ph}",
                    (uid,),
                ).fetchone()
                m["orders_count"] = dict(row)["c"] if row else 0
            except Exception:
                m["orders_count"] = 0
            try:
                row = conn.execute(
                    f"SELECT COUNT(*) AS c FROM ad_analysis_runs WHERE user_id = {ph}",
                    (uid,),
                ).fetchone()
                m["analyses_count"] = dict(row)["c"] if row else 0
            except Exception:
                m["analyses_count"] = 0
            try:
                row = conn.execute(
                    f"SELECT COUNT(*) AS c FROM ad_alert_rules WHERE user_id = {ph} AND enabled = 1",
                    (uid,),
                ).fetchone()
                m["alert_rules_count"] = dict(row)["c"] if row else 0
            except Exception:
                m["alert_rules_count"] = 0
    finally:
        conn.close()
    return jsonify({
        "tenant": {"id": g.current_tenant["id"], "name": g.current_tenant.get("name")},
        "members": members,
    })


@app.route("/api/share/tokens")
def api_share_tokens_list():
    """Audit list of tokens issued in this tenant (admin/owner only)."""
    from flask import g
    if not getattr(g, "current_user", None) or not getattr(g, "current_tenant", None):
        return jsonify({"error": "auth required"}), 401
    role = (g.current_user.get("role_in_tenant") or "").lower()
    if role not in ("owner", "admin"):
        return jsonify({"error": "owner or admin required"}), 403
    from tools.trading.share import tokens as share_tokens
    return jsonify({"tokens": share_tokens.list_tokens(g.current_tenant["id"])})


@app.route("/api/share/portfolio-brief", methods=["POST"])
def api_share_portfolio_brief_create():
    """Generate a signed share-link that serves a portfolio PDF brief
    without requiring the recipient to log in. Admin/owner only."""
    from flask import g
    if not getattr(g, "current_user", None) or not getattr(g, "current_tenant", None):
        return jsonify({"error": "auth required"}), 401
    role = (g.current_user.get("role_in_tenant") or "").lower()
    if role not in ("owner", "admin"):
        return jsonify({"error": "owner or admin required"}), 403
    from tools.trading.share import tokens as share_tokens
    # Phase 5A: monthly share-link quota
    try:
        from tools.trading.billing import tiers as _bt
        _bt.check_quota(g.current_tenant["id"], "share_links_per_month",
                         _bt.count_share_links_this_month(g.current_tenant["id"]))
    except _bt.QuotaExceeded as qe:
        return jsonify({"error": str(qe), "quota": qe.quota_key,
                         "limit": qe.limit, "tier": qe.tier,
                         "upgrade_url": "/billing"}), 402
    data = request.get_json(silent=True) or {}
    ttl_seconds = int(data.get("ttl_seconds") or share_tokens.DEFAULT_TTL_SECONDS)
    try:
        token, _h = share_tokens.create(
            secret_key=app.secret_key,
            tenant_id=g.current_tenant["id"],
            kind="portfolio_brief",
            created_by_user_id=g.current_user["id"],
            ttl_seconds=ttl_seconds,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    base = (request.url_root or "http://localhost:5100/").rstrip("/")
    return jsonify({
        "ok": True,
        "share_url": f"{base}/share/portfolio-brief?token={token}",
        "expires_in_days": round(ttl_seconds / 86400, 1),
    })


@app.route("/api/share/tokens/<token_hash>/revoke", methods=["POST"])
def api_share_tokens_revoke(token_hash):
    from flask import g
    if not getattr(g, "current_user", None) or not getattr(g, "current_tenant", None):
        return jsonify({"error": "auth required"}), 401
    role = (g.current_user.get("role_in_tenant") or "").lower()
    if role not in ("owner", "admin"):
        return jsonify({"error": "owner or admin required"}), 403
    from tools.trading.share import tokens as share_tokens
    n = share_tokens.revoke(token_hash, g.current_tenant["id"])
    return jsonify({"revoked": n})


@app.route("/share/portfolio-brief")
def page_share_portfolio_brief():
    """Public: serve a portfolio brief PDF keyed on a signed token.
    No login required — the token is the authorization."""
    from flask import Response
    from tools.trading.share import tokens as share_tokens
    token = request.args.get("token", "")
    payload = share_tokens.verify(
        secret_key=app.secret_key, token=token,
        ip=request.remote_addr,
    )
    if not payload or payload.get("kind") != "portfolio_brief":
        return ("Invalid or expired share link.", 403)
    # Render the brief for the tenant the token was issued against
    from tools.trading.tenancy import db as tenant_db
    from tools.trading.analytics.portfolio_reading import generate_reading
    from tools.trading.analytics.portfolio_pdf import render_brief
    tenant = tenant_db.get_tenant(payload["tenant_id"]) or {}
    # Use the tenant owner's user_id for portfolio scoping (typical advisor use case)
    from tools.db.storage import get_connection, sql_placeholder
    conn = get_connection()
    ph = sql_placeholder(conn)
    try:
        owner_uid = None
        row = conn.execute(
            f"SELECT owner_user_id FROM ad_tenants WHERE id = {ph}",
            (payload["tenant_id"],),
        ).fetchone()
        if row:
            owner_uid = dict(row).get("owner_user_id")
        if not owner_uid:
            # Fall back to the user who created the token
            owner_uid = payload.get("created_by_user_id")
        # Pull portfolio state scoped to that user + tenant
        pf = conn.execute(
            f"SELECT * FROM ad_portfolios WHERE user_id = {ph} AND tenant_id = {ph} LIMIT 1",
            (owner_uid, payload["tenant_id"]),
        ).fetchone()
        pos = conn.execute(
            f"SELECT * FROM ad_positions WHERE portfolio_id = {ph} AND qty > 0",
            (pf["id"],),
        ).fetchall() if pf else []
    finally:
        conn.close()
    if not pf:
        return ("Tenant portfolio not found.", 404)
    pf_d = dict(pf)
    positions = [dict(p) for p in pos]
    cash = float(pf_d.get("cash_balance") or 0)
    total_value = cash + sum(float(p.get("market_value") or 0) for p in positions)
    state = {
        "cash_balance": cash,
        "total_value": total_value,
        "positions": positions,
        "position_count": len(positions),
    }
    # Metrics + reading
    try:
        hist, err = _compute_portfolio_history("6mo")
        metrics = _compute_metrics(hist["portfolio_values"], hist["spy_values"]) if hist else {}
    except Exception:
        metrics = {}
    try:
        from tools.trading.data.macro_data import fetch_macro_context
        regime_label = (fetch_macro_context() or {}).get("regime")
    except Exception:
        regime_label = None
    reading = generate_reading(state, metrics, regime_label)
    pdf_bytes = render_brief(state, metrics, reading,
                              regime=regime_label, period="6mo",
                              tenant=tenant)
    # Filename: use tenant slug + 'shared'
    fname_prefix = tenant.get("slug") if (tenant.get("white_label_enabled") and tenant.get("slug")) else "fathomdesk"
    fname = f"{fname_prefix}-portfolio-brief-shared-{datetime.now(timezone.utc).strftime('%Y%m%d')}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@app.route("/rebalance")
def page_rebalance():
    return render_template("rebalance.html")


@app.route("/api/rebalance")
def api_rebalance_plan():
    from flask import g
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    from tools.trading.analytics.rebalance import build_plan
    try:
        return jsonify(build_plan(g.current_user["id"], _active_tenant_id()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rebalance/allocations", methods=["GET"])
def api_rebalance_allocations_get():
    from flask import g
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    from tools.trading.analytics.rebalance import get_allocations
    return jsonify({
        "allocations": get_allocations(g.current_user["id"], _active_tenant_id()),
    })


@app.route("/api/rebalance/allocations", methods=["PUT"])
def api_rebalance_allocations_put():
    """Replace the user's target allocation atomically."""
    from flask import g
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    items = data.get("allocations") or []
    if not isinstance(items, list):
        return jsonify({"error": "allocations must be a list"}), 400
    # Validate: sum ≤ 100, no dupes, ticker non-empty, weight 0-100
    total = 0.0
    seen = set()
    for a in items:
        tkr = (a.get("ticker") or "").upper().strip()
        try:
            pct = float(a.get("target_weight_pct") or 0)
        except (TypeError, ValueError):
            return jsonify({"error": f"invalid target_weight_pct for {tkr}"}), 400
        if not tkr:
            return jsonify({"error": "ticker is required for each allocation"}), 400
        if pct < 0 or pct > 100:
            return jsonify({"error": f"target_weight_pct for {tkr} must be 0-100"}), 400
        if tkr in seen:
            return jsonify({"error": f"duplicate ticker: {tkr}"}), 400
        seen.add(tkr)
        total += pct
    if total > 100.01:
        return jsonify({"error": f"allocation sums to {total:.1f}%, must be ≤ 100% (leftover is target cash)"}), 400
    from tools.trading.analytics.rebalance import replace_allocations
    try:
        n = replace_allocations(g.current_user["id"], _active_tenant_id(), items)
        return jsonify({"ok": True, "saved": n, "target_cash_pct": round(100.0 - total, 2)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rebalance/execute", methods=["POST"])
def api_rebalance_execute():
    """Place sandbox market orders for every non-CASH buy/sell in the rebalance plan."""
    from flask import g
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    from tools.trading.analytics.rebalance import build_plan
    from tools.trading.execution import order_manager
    try:
        from tools.trading.data.market_data import fetch_latest_quote
    except Exception:
        fetch_latest_quote = None  # optional — fall back to position-derived price

    try:
        plan = build_plan(g.current_user["id"], _active_tenant_id())
    except Exception as e:
        return jsonify({"error": f"plan failed: {e}"}), 500

    if plan.get("error"):
        return jsonify({"error": plan["error"]}), 400

    # Build a ticker→price map from current positions (market_value / qty)
    price_map: dict[str, float] = {}
    for row in plan.get("current", []):
        tkr = row.get("ticker")
        qty = float(row.get("qty") or 0)
        mv = float(row.get("market_value") or 0)
        if tkr and tkr != "CASH" and qty > 0:
            price_map[tkr] = mv / qty

    trades_to_run = [
        t for t in plan.get("trades", [])
        if t.get("action") in ("buy", "sell") and t.get("ticker") != "CASH"
    ]

    submitted: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []
    uid = g.current_user["id"]

    for trade in trades_to_run:
        ticker = trade["ticker"]
        side = trade["action"]            # "buy" or "sell"
        dollar_amount = abs(trade["trade_value"])

        # Resolve price
        price = price_map.get(ticker)
        if price is None and fetch_latest_quote is not None:
            try:
                q = fetch_latest_quote(ticker)
                price = float(q.get("last") or q.get("bid") or 0) or None
            except Exception:
                price = None

        if not price or price <= 0:
            skipped.append({"ticker": ticker, "reason": "price unavailable"})
            continue

        qty = round(dollar_amount / price, 6)
        if qty < 0.001:
            skipped.append({"ticker": ticker, "reason": "qty < 0.001 — too small"})
            continue

        try:
            result = order_manager.place_order(
                ticker=ticker,
                side=side,
                qty=qty,
                order_type="market",
                time_in_force="day",
                user_id=uid,
            )
            submitted.append({
                "ticker": ticker,
                "side": side,
                "qty": qty,
                "approx_value": round(dollar_amount, 2),
                "order_id": result.get("id") or result.get("txid"),
                "status": result.get("status"),
                "source": result.get("source"),
            })
        except Exception as e:
            errors.append({"ticker": ticker, "side": side, "error": str(e)})

    return jsonify({
        "ok": True,
        "submitted": submitted,
        "skipped": skipped,
        "errors": errors,
        "counts": {
            "submitted": len(submitted),
            "skipped": len(skipped),
            "errors": len(errors),
            "total_attempted": len(trades_to_run),
        },
    })


@app.route("/api/today")
def api_today():
    from flask import g
    from tools.trading.analytics.today_digest import generate
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    try:
        d = generate(
            user_id=g.current_user["id"],
            tenant_id=_active_tenant_id(),
        )
        return jsonify(d)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Profile API (Phase 1) — persona-aware UX
# ---------------------------------------------------------------------------
def _active_uid():
    """Phase 2A helper — returns current user's id, or 'default' for
    unauth contexts (which only happens on public pages)."""
    from flask import g
    u = getattr(g, "current_user", None)
    return u["id"] if u else "default"


def _active_tenant_id():
    """Phase 3 helper — current tenant id (always 'default' until 3.2 invites
    bring in real tenant boundaries)."""
    from flask import g
    t = getattr(g, "current_tenant", None)
    return t["id"] if t else "default"


def _scope_clause(table_alias: str = "") -> tuple[str, tuple]:
    """Return ('user_id = ? AND tenant_id = ?', (uid, tid)) for the active
    request context. Use as `WHERE` (or `AND` part of a longer WHERE).
    The table_alias prefix is for joined queries: pass 'p' to get
    'p.user_id = ? AND p.tenant_id = ?'."""
    prefix = (table_alias + ".") if table_alias else ""
    return (
        f"{prefix}user_id = ? AND {prefix}tenant_id = ?",
        (_active_uid(), _active_tenant_id()),
    )


def _progression_grant_safe(*, user: dict | None, reason: str,
                              dedup_key: str | None = None,
                              context: dict | None = None) -> None:
    """Phase 6.1 — award XP at user-action sites. Never raises.
    Resolves the user's persona from their profile so the opt-in policy
    applies correctly for first-touch grants."""
    if not user:
        return
    try:
        from tools.trading.progression import engine as _pe
        from tools.trading.profile import db as _pdb
        prof = _pdb.get_profile(user["id"]) or {}
        _pe.grant_xp_safe(
            user_id=user["id"],
            reason=reason,
            dedup_key=dedup_key,
            context=context,
            persona=prof.get("persona"),
        )
    except Exception:
        pass


@app.route("/api/profile", methods=["GET"])
def api_profile_get():
    try:
        from tools.trading.profile import db as pdb
        from tools.trading.profile import presets as pp
        uid = _active_uid()
        prof = pdb.get_profile(uid) or pp.ensure_default_profile(uid)
        prof["available_personas"] = pp.list_personas()
        prof["needs_onboarding"] = not prof.get("onboarded")
        return jsonify(prof)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/profile", methods=["PATCH"])
def api_profile_patch():
    """Update arbitrary profile fields (e.g. theme toggle, beginner_mode)."""
    try:
        from tools.trading.profile import db as pdb
        import json as _json
        data = request.get_json(silent=True) or {}
        allowed = {
            "expertise_level", "jargon_level", "time_horizon",
            "risk_tolerance", "jurisdiction", "currency", "locale", "theme",
            "beginner_mode_enabled", "hidden_pages", "hidden_cards",
            "featured_pages", "voice", "flags", "voice_overrides",
        }
        bad = set(data) - allowed
        if bad:
            return jsonify({"error": f"Unsupported fields: {sorted(bad)}"}), 400
        uid = _active_uid()
        # voice_overrides uses merge-patch: incoming keys are merged into stored dict
        if "voice_overrides" in data:
            existing_prof = pdb.get_profile(uid) or {}
            existing_vo = existing_prof.get("voice_overrides") or {}
            if isinstance(existing_vo, str):
                try:
                    existing_vo = _json.loads(existing_vo)
                except (ValueError, TypeError):
                    existing_vo = {}
            incoming_vo = data["voice_overrides"]
            if isinstance(incoming_vo, str):
                try:
                    incoming_vo = _json.loads(incoming_vo)
                except (ValueError, TypeError):
                    incoming_vo = {}
            merged = {**existing_vo, **incoming_vo}
            data = {**data, "voice_overrides": merged}
        prof = pdb.upsert_profile(user_id=uid, **data)
        return jsonify(prof)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/profile/apply-preset", methods=["POST"])
def api_profile_apply_preset():
    """Apply a persona's preset — overwrites layout/voice/jargon/etc."""
    try:
        from tools.trading.profile import db as pdb
        from tools.trading.profile import presets as pp
        data = request.get_json(silent=True) or {}
        persona = data.get("persona")
        if not persona:
            return jsonify({"error": "persona required"}), 400
        if persona not in {p["key"] for p in pp.list_personas()}:
            return jsonify({"error": f"unknown persona: {persona}"}), 400
        uid = _active_uid()
        prof = pp.apply_preset(persona, user_id=uid)
        if data.get("mark_onboarded"):
            pdb.mark_onboarded(uid)
            prof = pdb.get_profile(uid) or prof
        prof["onboarding"] = pp.get_onboarding(persona)
        return jsonify(prof)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/profile/onboarded", methods=["POST"])
def api_profile_mark_onboarded():
    try:
        from tools.trading.profile import db as pdb
        pdb.mark_onboarded(_active_uid())
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/profile/theme", methods=["POST"])
def api_profile_theme():
    """Save the user's theme preference (light|dark) to ad_profiles.theme."""
    try:
        from tools.trading.profile import db as pdb
        data = request.get_json(silent=True) or {}
        theme = data.get("theme")
        if theme not in ("light", "dark"):
            return jsonify({"error": "theme must be 'light' or 'dark'"}), 400
        pdb.upsert_profile(user_id=_active_uid(), theme=theme)
        return jsonify({"ok": True, "theme": theme})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Server-side CSV exports (heatmap, news, alerts) — see csv_export.py
# ---------------------------------------------------------------------------
@app.route("/api/market/heatmap/export.csv")
def api_market_heatmap_export_csv():
    """Per-ticker heatmap snapshot enriched with multi-timeframe returns
    + 24h news impact aggregate. One row per ticker in ad_market_snapshot."""
    try:
        from datetime import timedelta
        from tools.trading.db import get_conn
        from tools.trading.dashboard.csv_export import csv_response, now_slug

        conn = get_conn()
        rows = conn.execute(
            "SELECT s.ticker, s.sector, s.direction, s.composite_score, "
            "s.confidence, s.regime, s.kg_centrality, s.kg_supply_chain, "
            "s.kg_competitors, s.signal_created_at, s.refreshed_at, "
            "p.p1y, p.p5y, p.p10y, p.p20y "
            "FROM ad_market_snapshot s "
            "LEFT JOIN ad_ticker_performance p ON p.ticker = s.ticker "
        ).fetchall()
        # Sort in Python so NULLs land last regardless of backend
        rows = sorted(
            (dict(r) for r in rows),
            key=lambda d: (d.get("composite_score") is None,
                           -(d.get("composite_score") or 0),
                           d.get("ticker") or ""),
        )

        # 24h aggregated news impact per ticker — use portable ISO cutoff
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        impact_map: dict[str, dict] = {}
        try:
            ir = conn.execute(
                "SELECT ticker, SUM(impact_score) AS total, COUNT(*) AS traces "
                "FROM ad_news_impact_traces "
                "WHERE traced_at >= %s "
                "GROUP BY ticker",
                (cutoff,),
            ).fetchall()
            for r in ir:
                d = dict(r) if hasattr(r, "keys") else {"ticker": r[0], "total": r[1], "traces": r[2]}
                impact_map[d["ticker"]] = {"total": d["total"], "traces": d["traces"]}
        except Exception:
            pass
        conn.close()

        out = []
        for d in rows:
            tkr = d.get("ticker")
            ni = impact_map.get(tkr, {})
            d["news_impact_24h"] = ni.get("total")
            d["news_traces_24h"] = ni.get("traces")
            out.append(d)

        cols = [
            ("ticker", "Ticker"), ("sector", "Sector"),
            ("direction", "Direction"),
            ("composite_score", "Composite Score"),
            ("confidence", "Confidence"),
            ("regime", "Regime"),
            ("p1y", "1Y Return %"), ("p5y", "5Y Return %"),
            ("p10y", "10Y Return %"), ("p20y", "20Y Return %"),
            ("news_impact_24h", "News Impact 24h (Σ)"),
            ("news_traces_24h", "News Trace Count 24h"),
            ("kg_centrality", "KG Centrality"),
            ("kg_supply_chain", "KG Supply-Chain Count"),
            ("kg_competitors", "KG Competitor Count"),
            ("signal_created_at", "Signal Created At"),
            ("refreshed_at", "Snapshot Refreshed At"),
        ]
        return csv_response(out, cols, f"fathomdesk-heatmap-{now_slug()}.csv")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/news/export.csv")
def api_news_export_csv():
    """Recent news items with classification metadata + cluster join +
    aggregate impact. Honors the same filter args as /api/news."""
    try:
        from tools.trading.news.db import list_news, list_active_clusters
        from tools.trading.dashboard.csv_export import csv_response, now_slug

        category = request.args.get("category")
        limit = int(request.args.get("limit", 500))
        items = list_news(category=category, limit=limit) or []

        # Build cluster lookup (item_id → cluster_id, cluster_status, cluster_category)
        cluster_index: dict[str, dict] = {}
        try:
            for c in (list_active_clusters() or []):
                ids = c.get("item_ids") or []
                if isinstance(ids, str):
                    import json as _j
                    try:
                        ids = _j.loads(ids)
                    except Exception:
                        ids = []
                for iid in ids:
                    cluster_index[str(iid)] = {
                        "cluster_id": c.get("id"),
                        "cluster_scenario_key": c.get("scenario_key"),
                        "cluster_status": c.get("status"),
                        "cluster_category": c.get("category"),
                    }
        except Exception:
            pass

        # Per-item impact aggregate from ad_news_impact_traces
        impact_index: dict[str, dict] = {}
        try:
            from tools.trading.db import get_conn
            conn = get_conn()
            ir = conn.execute(
                "SELECT news_id, SUM(impact_score) AS total, COUNT(*) AS traces, "
                "COUNT(DISTINCT ticker) AS ticker_count "
                "FROM ad_news_impact_traces GROUP BY news_id"
            ).fetchall()
            conn.close()
            for r in ir:
                d = dict(r) if hasattr(r, "keys") else {"news_id": r[0], "total": r[1], "traces": r[2], "ticker_count": r[3]}
                impact_index[str(d["news_id"])] = d
        except Exception:
            pass

        rows = []
        for it in items:
            iid = str(it.get("id") or "")
            cl = cluster_index.get(iid, {})
            im = impact_index.get(iid, {})
            tickers = it.get("mentioned_tickers")
            if isinstance(tickers, list):
                tickers = ",".join(tickers)
            rows.append({
                "id": it.get("id"),
                "published_at": it.get("published_at"),
                "ingested_at": it.get("ingested_at"),
                "source": it.get("source"),
                "title": it.get("title"),
                "category": it.get("category"),
                "impact_level": it.get("impact_level"),
                "net_direction": it.get("net_direction"),
                "mentioned_tickers": tickers,
                "link": it.get("link"),
                "cluster_id": cl.get("cluster_id"),
                "cluster_scenario_key": cl.get("cluster_scenario_key"),
                "cluster_status": cl.get("cluster_status"),
                "impact_total": im.get("total"),
                "impact_trace_count": im.get("traces"),
                "impact_ticker_count": im.get("ticker_count"),
            })

        cols = [
            ("id", "ID"),
            ("published_at", "Published At"),
            ("ingested_at", "Ingested At"),
            ("source", "Source"),
            ("title", "Title"),
            ("category", "Category"),
            ("impact_level", "Impact Level"),
            ("net_direction", "Net Direction"),
            ("mentioned_tickers", "Mentioned Tickers"),
            ("cluster_id", "Cluster ID"),
            ("cluster_scenario_key", "Cluster Scenario Key"),
            ("cluster_status", "Cluster Status"),
            ("impact_total", "Total Impact (Σ)"),
            ("impact_trace_count", "Impact Trace Count"),
            ("impact_ticker_count", "Impact Ticker Count"),
            ("link", "Link"),
        ]
        return csv_response(rows, cols, f"fathomdesk-news-{now_slug()}.csv")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alerts/log/export.csv")
def api_alerts_log_export_csv():
    """Append-only fired-alert log."""
    try:
        from tools.trading.alerts import db as adb
        from tools.trading.dashboard.csv_export import csv_response, now_slug
        adb.ensure_tables()
        limit = int(request.args.get("limit", 1000))
        unack = request.args.get("unack") in ("1", "true", "yes")
        rows = adb.list_alerts(limit=limit, unack_only=unack)
        # Drop the parsed-evidence column; keep raw evidence_json instead
        for r in rows:
            r.pop("evidence", None)
        cols = [
            ("id", "Alert ID"),
            ("created_at", "Created At"),
            ("severity", "Severity"),
            ("rule_name", "Rule Name"),
            ("subject", "Subject"),
            ("rule_type", "Rule Type"),
            ("message", "Message"),
            ("acknowledged", "Acknowledged"),
            ("acknowledged_at", "Acked At"),
            ("rule_id", "Rule ID"),
            ("evidence_json", "Evidence (JSON)"),
        ]
        return csv_response(rows, cols, f"fathomdesk-alerts-log-{now_slug()}.csv")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alerts/rules/export.csv")
def api_alerts_rules_export_csv():
    """User's alert rules — for backup / manual edit / re-import."""
    try:
        from tools.trading.alerts import db as adb
        from tools.trading.dashboard.csv_export import csv_response, now_slug
        adb.ensure_tables()
        rows = adb.list_rules()
        cols = [
            ("id", "Rule ID"),
            ("name", "Name"),
            ("rule_type", "Rule Type"),
            ("subject", "Subject"),
            ("comparison", "Comparison"),
            ("threshold", "Threshold"),
            ("lookback_minutes", "Lookback (min)"),
            ("cooldown_minutes", "Cooldown (min)"),
            ("severity", "Severity"),
            ("enabled", "Enabled"),
            ("last_evaluated_at", "Last Evaluated At"),
            ("last_triggered_at", "Last Triggered At"),
            ("last_value", "Last Value"),
            ("created_at", "Created At"),
        ]
        return csv_response(rows, cols, f"fathomdesk-alert-rules-{now_slug()}.csv")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Pages + API — Alerts (user-defined notification rules)
# ---------------------------------------------------------------------------
@app.route("/alerts")
def page_alerts():
    return render_template("alerts.html")


@app.route("/api/alerts/rules", methods=["GET"])
def api_alerts_list_rules():
    try:
        from tools.trading.alerts import db as adb
        adb.ensure_tables()
        return jsonify({"rules": adb.list_rules()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alerts/rules", methods=["POST"])
def api_alerts_create_rule():
    try:
        from tools.trading.alerts import db as adb
        from tools.trading.billing import tiers as _bt
        adb.ensure_tables()
        # Phase 5A: tenant-level alert-rule quota
        try:
            tid = _active_tenant_id()
            _bt.check_quota(tid, "alert_rules", _bt.count_alert_rules(tid))
        except _bt.QuotaExceeded as qe:
            return jsonify({"error": str(qe), "quota": qe.quota_key,
                             "limit": qe.limit, "tier": qe.tier,
                             "upgrade_url": "/billing"}), 402
        data = request.get_json(silent=True) or {}
        rid = adb.create_rule(
            name=str(data.get("name") or "").strip() or "Untitled rule",
            rule_type=str(data.get("rule_type") or "composite_score"),
            subject=(str(data.get("subject") or "").upper().strip() or None),
            comparison=str(data.get("comparison") or "gt"),
            threshold=(float(data["threshold"])
                       if data.get("threshold") not in (None, "") else None),
            lookback_minutes=int(data.get("lookback_minutes") or 60),
            cooldown_minutes=int(data.get("cooldown_minutes") or 60),
            severity=str(data.get("severity") or "info"),
            enabled=bool(data.get("enabled", True)),
        )
        return jsonify({"id": rid, "rule": adb.get_rule(rid)})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alerts/rules/<rule_id>", methods=["PATCH", "DELETE"])
def api_alerts_modify_rule(rule_id):
    try:
        from tools.trading.alerts import db as adb
        adb.ensure_tables()
        if request.method == "DELETE":
            n = adb.delete_rule(rule_id)
            return jsonify({"deleted": n})
        data = request.get_json(silent=True) or {}
        # Coerce numeric fields if present
        for k in ("threshold",):
            if data.get(k) not in (None, ""):
                try:
                    data[k] = float(data[k])
                except (TypeError, ValueError):
                    pass
        for k in ("lookback_minutes", "cooldown_minutes", "enabled"):
            if data.get(k) is not None:
                try:
                    data[k] = int(bool(data[k])) if k == "enabled" else int(data[k])
                except (TypeError, ValueError):
                    pass
        n = adb.update_rule(rule_id, **data)
        return jsonify({"updated": n, "rule": adb.get_rule(rule_id)})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alerts/log")
def api_alerts_log():
    try:
        from tools.trading.alerts import db as adb
        adb.ensure_tables()
        unack = request.args.get("unack") in ("1", "true", "yes")
        limit = int(request.args.get("limit", 100))
        return jsonify({
            "alerts": adb.list_alerts(limit=limit, unack_only=unack),
            "unack_count": adb.unack_count(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alerts/<alert_id>/ack", methods=["POST"])
def api_alerts_ack_one(alert_id):
    try:
        from tools.trading.alerts import db as adb
        n = adb.acknowledge(alert_id=alert_id)
        return jsonify({"acknowledged": n, "unack_count": adb.unack_count()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alerts/ack-all", methods=["POST"])
def api_alerts_ack_all():
    try:
        from tools.trading.alerts import db as adb
        n = adb.acknowledge(all_for_user=True)
        return jsonify({"acknowledged": n, "unack_count": adb.unack_count()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alerts/evaluate", methods=["POST"])
def api_alerts_evaluate():
    """Manual one-shot evaluation — useful for testing without the daemon."""
    try:
        from tools.trading.alerts.evaluator import evaluate_all
        return jsonify(evaluate_all())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Alert delivery channels (out-of-band webhook + email) ───────────

@app.route("/api/alerts/delivery-channels", methods=["GET"])
def api_alerts_delivery_channels_list():
    from flask import g
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    from tools.trading.alerts import db as adb
    adb.ensure_tables()
    return jsonify({"channels": adb.list_delivery_channels(g.current_user["id"])})


@app.route("/api/alerts/delivery-channels", methods=["POST"])
def api_alerts_delivery_channels_create():
    from flask import g
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    from tools.trading.alerts import db as adb
    data = request.get_json(silent=True) or {}
    channel_type = data.get("channel_type") or ""
    if channel_type not in adb.CHANNEL_TYPES:
        return jsonify({"error": f"channel_type must be one of {adb.CHANNEL_TYPES}"}), 400
    config = data.get("config") or {}
    # Minimal validation per channel type
    if channel_type == "webhook" and not config.get("url"):
        return jsonify({"error": "webhook config requires url"}), 400
    if channel_type == "email" and not (config.get("smtp_host") and config.get("to_address")):
        return jsonify({"error": "email config requires smtp_host and to_address"}), 400
    try:
        cid = adb.create_delivery_channel(
            user_id=g.current_user["id"],
            tenant_id=g.current_user.get("tenant_id") or "default",
            channel_type=channel_type,
            label=data.get("label") or "",
            config=config,
            min_severity=data.get("min_severity") or "info",
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "id": cid})


@app.route("/api/alerts/delivery-channels/<channel_id>", methods=["PATCH"])
def api_alerts_delivery_channels_update(channel_id):
    from flask import g
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    from tools.trading.alerts import db as adb
    import json as _json
    data = request.get_json(silent=True) or {}
    fields = {}
    if "label" in data:
        fields["label"] = str(data["label"])
    if "min_severity" in data:
        if data["min_severity"] not in adb.SEVERITY_ORDER:
            return jsonify({"error": "invalid min_severity"}), 400
        fields["min_severity"] = data["min_severity"]
    if "enabled" in data:
        fields["enabled"] = 1 if data["enabled"] else 0
    if "config" in data and isinstance(data["config"], dict):
        fields["config_json"] = _json.dumps(data["config"], default=str)
    adb.update_delivery_channel(channel_id, user_id=g.current_user["id"], **fields)
    return jsonify({"ok": True})


@app.route("/api/alerts/delivery-channels/<channel_id>", methods=["DELETE"])
def api_alerts_delivery_channels_delete(channel_id):
    from flask import g
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    from tools.trading.alerts import db as adb
    adb.delete_delivery_channel(channel_id, g.current_user["id"])
    return jsonify({"ok": True})


@app.route("/api/alerts/delivery-channels/<channel_id>/test", methods=["POST"])
def api_alerts_delivery_channels_test(channel_id):
    """Send a test payload to the channel to verify it's configured correctly."""
    from flask import g
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    from tools.trading.alerts import db as adb
    from tools.trading.alerts.delivery import deliver_alert
    channels = [ch for ch in adb.get_delivery_channels_for_user(g.current_user["id"])
                if ch["id"] == channel_id]
    if not channels:
        return jsonify({"error": "channel not found"}), 404
    test_alert = {
        "id": "test-0000000000",
        "rule_name": "Test Alert",
        "subject": "FathomDesk",
        "rule_type": "composite_score",
        "severity": "info",
        "message": "This is a test delivery from FathomDesk. Your channel is working.",
        "evidence": {"test": True},
        "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
    # Temporarily override min_severity to always deliver the test
    for ch in channels:
        ch["min_severity"] = "info"
    try:
        n = deliver_alert(test_alert, channels)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": n > 0, "delivered": n})


# Lightweight unack-count probe for the nav bell badge (cheap query).
@app.route("/api/alerts/badge")
def api_alerts_badge():
    try:
        from tools.trading.alerts import db as adb
        adb.ensure_tables()
        return jsonify({"unack_count": adb.unack_count()})
    except Exception as e:
        return jsonify({"unack_count": 0, "error": str(e)})


# Persona alert-default seeding (Phase 1C).
# Returns the persona's suggested rules WITHOUT inserting them; UI shows the
# list and the user opts in via "Seed selected" — many use placeholder
# subjects (WATCHLIST_ANY, PORTFOLIO_DRAWDOWN) the evaluator can't resolve
# yet (Phase 4), so silent seeding would create dead rules.
@app.route("/api/alerts/suggested")
def api_alerts_suggested():
    """Return alert rules pre-suggested by the active persona, with
    `evaluator_supports` flag indicating whether the evaluator can run
    each rule today. Placeholders are flagged but not blocked."""
    try:
        from tools.trading.profile import db as pdb
        from tools.trading.profile import presets as pp
        prof = pdb.get_profile() or {}
        persona = prof.get("persona")
        rules = pp.get_alert_defaults(persona) if persona else []
        # Phase 4 follow-up — all persona placeholder subjects are now
        # resolved by the evaluator at fire time (see
        # tools/trading/alerts/virtual_subjects.py). Every rule here is
        # considered supported.
        for r in rules:
            r["evaluator_supports"] = True
        return jsonify({
            "persona": persona,
            "rules": rules,
            "note": ("Virtual subjects (WATCHLIST_ANY / _AVG / _TOP, "
                     "PORTFOLIO_DRAWDOWN / _DRIFT) expand at evaluation "
                     "time — WATCHLIST_* scans your watchlist; PORTFOLIO_* "
                     "compute across current positions vs targets."),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alerts/suggested/seed", methods=["POST"])
def api_alerts_suggested_seed():
    """Insert selected suggested rules into ad_alert_rules. Skip rules with
    duplicate names. Returns count inserted/skipped/blocked."""
    try:
        from tools.trading.alerts import db as adb
        adb.ensure_tables()
        data = request.get_json(silent=True) or {}
        rules = data.get("rules") or []
        existing_names = {r["name"] for r in adb.list_rules()}
        inserted = 0
        skipped_duplicate = 0
        errors = 0
        for rule in rules:
            name = rule.get("name") or "Untitled"
            if name in existing_names:
                skipped_duplicate += 1
                continue
            try:
                adb.create_rule(
                    name=name,
                    rule_type=rule.get("rule_type") or "composite_score",
                    subject=rule.get("subject") or None,
                    comparison=rule.get("comparison") or "gt",
                    threshold=(float(rule["threshold"])
                               if rule.get("threshold") not in (None, "")
                               else None),
                    lookback_minutes=int(rule.get("lookback_minutes") or 60),
                    cooldown_minutes=int(rule.get("cooldown_minutes") or 60),
                    severity=rule.get("severity") or "info",
                    enabled=bool(rule.get("enabled", True)),
                )
                inserted += 1
            except Exception:
                errors += 1
        return jsonify({
            "inserted": inserted,
            "skipped_duplicate": skipped_duplicate,
            "errors": errors,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Phase 2A.1 — Auth pages + endpoints (email/password + sessions)
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET"])
def auth_login():
    next_url = request.args.get("next", "/")
    return render_template("auth_login.html", next_url=next_url)


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    """Email/password login. Sets ad_session cookie on success.

    If the user has TOTP enabled, the response indicates `mfa_required: true`
    and the session is created but `mfa_satisfied=0`. The frontend then
    POSTs to /api/auth/mfa/verify with the 6-digit code (or backup code).
    """
    from tools.trading.auth import db as adb
    from tools.trading.auth import passwords
    from tools.trading.auth import mfa as a_mfa
    from tools.trading.auth.middleware import SESSION_COOKIE
    from flask import make_response

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"error": "email + password required"}), 400

    user = adb.get_user_by_email(email)
    if not user or user.get("disabled"):
        return jsonify({"error": "Invalid email or password"}), 401
    if not passwords.verify_password(user.get("password_hash"), password):
        return jsonify({"error": "Invalid email or password"}), 401

    if passwords.needs_rehash(user.get("password_hash") or ""):
        adb.update_password_hash(user["id"], passwords.hash_password(password))

    adb.touch_login(user["id"])
    token = adb.create_session(
        user_id=user["id"],
        user_agent=request.headers.get("User-Agent"),
        ip=request.remote_addr,
    )
    mfa_required = a_mfa.has_mfa(user["id"])
    mfa_enrollment_required = False
    if not mfa_required:
        try:
            mfa_req_at = user.get("mfa_required_at")
            if mfa_req_at:
                from datetime import datetime, timezone as _tz
                deadline = datetime.fromisoformat(mfa_req_at)
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=_tz.utc)
                mfa_enrollment_required = datetime.now(_tz.utc) > deadline
        except Exception:
            pass
    if not mfa_required:
        # No MFA configured — session is fully usable. Stamp it as freshly
        # mfa-satisfied so step-up gates (which check freshness) trivially pass.
        a_mfa.mark_session_mfa_satisfied(token)

    # Phase 2C — if the user has opted into BYOK vault mode, derive the
    # master DEK from the plaintext password (which is only available
    # during this login handler) and stash it in the Flask session cookie.
    # Must NEVER fail the login itself — vault outages fall back to Path A
    # behavior (resolver returns None → caller falls back to env vars).
    try:
        from tools.trading.credentials import db as _cred_db
        from tools.trading.credentials import vault as _vault
        vrow = _cred_db.get_vault_row(user["id"])
        if vrow and _vault.is_available():
            dek = _vault.decrypt_dek_with_password(
                salt_b64=vrow["salt_b64"],
                password_wrapped_dek=vrow["password_wrapped_dek"],
                password=password,
            )
            _vault.stash_in_session(dek)
    except Exception as e:
        app.logger.warning("vault DEK derivation failed for user=%s: %s",
                            user["id"], e)

    resp = make_response(jsonify({
        "ok": True,
        "mfa_required": mfa_required,
        "mfa_enrollment_required": mfa_enrollment_required,
        "user": {"id": user["id"], "email": user["email"],
                  "display_name": user.get("display_name")},
    }))
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax",
                     max_age=168 * 3600, secure=False)
    return resp


@app.route("/signup", methods=["GET"])
def auth_signup():
    first_run = request.args.get("first_run") == "1"
    return render_template("auth_signup.html", first_run=first_run)


@app.route("/api/auth/signup", methods=["POST"])
def api_auth_signup():
    """Email/password signup. First-run signup also takes ownership of the
    legacy 'default' user-id data via the bootstrap helper."""
    from tools.trading.auth import db as adb
    from tools.trading.auth import passwords
    from tools.trading.auth.middleware import SESSION_COOKIE
    from flask import make_response
    try:
        from email_validator import validate_email, EmailNotValidError
    except ImportError:
        validate_email = None  # graceful degrade

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or "").strip() or None

    if not email or not password:
        return jsonify({"error": "email + password required"}), 400
    if validate_email:
        try:
            email = validate_email(email, check_deliverability=False).normalized.lower()
        except EmailNotValidError as e:
            return jsonify({"error": f"Invalid email: {e}"}), 400

    ok, msg = passwords.policy_check(password)
    if not ok:
        return jsonify({"error": msg}), 400

    # Reject duplicate
    if adb.get_user_by_email(email):
        return jsonify({"error": "An account with that email already exists"}), 409

    is_first_user = (adb.count_users() == 0)
    pwhash = passwords.hash_password(password)
    uid = adb.create_user(
        email=email,
        password_hash=pwhash,
        display_name=display_name,
        tenant_id="default",
        role_in_tenant="owner",
        auth_provider="local",
    )

    # Bootstrap: first user inherits the legacy 'default' single-user data.
    # Profile row keyed off user_id='default' gets re-keyed to the new user.
    if is_first_user:
        try:
            from tools.db.storage import get_connection, sql_placeholder
            conn = get_connection()
            ph = sql_placeholder(conn)
            try:
                conn.execute(
                    f"UPDATE ad_user_profiles SET user_id = {ph} WHERE user_id = 'default'",
                    (uid,),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass  # profile row might not exist yet — fine
        # Phase 3.1: first user becomes owner of the default tenant
        try:
            from tools.trading.tenancy import db as tenant_db
            tenant_db.claim_default_tenant_owner(uid)
        except Exception:
            pass

    # Phase 3.2: every signup gets a membership row in their primary tenant
    try:
        from tools.trading.tenancy import db as tenant_db
        tenant_db.add_membership(
            uid,
            "default" if is_first_user else "default",  # Phase 3.2 single tenant
            "owner" if is_first_user else "member",
        )
    except Exception:
        pass

    # Auto-login on signup
    adb.touch_login(uid)
    token = adb.create_session(
        user_id=uid,
        user_agent=request.headers.get("User-Agent"),
        ip=request.remote_addr,
    )
    resp = make_response(jsonify({
        "ok": True, "first_run": is_first_user,
        "user": {"id": uid, "email": email, "display_name": display_name},
    }))
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax",
                     max_age=168 * 3600, secure=False)
    return resp


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    from tools.trading.auth import db as adb
    from tools.trading.auth.middleware import SESSION_COOKIE
    from flask import make_response, g
    token = getattr(g, "current_session_token", None) or request.cookies.get(SESSION_COOKIE)
    if token:
        adb.revoke_session(token)
    # Phase 2C — clear the vault DEK from the Flask session cookie. The
    # next login will re-derive it from the password again.
    try:
        from tools.trading.credentials import vault as _vault
        _vault.clear_session()
    except Exception:
        pass
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.route("/api/auth/me")
def api_auth_me():
    from flask import g
    if not getattr(g, "current_user", None):
        return jsonify({"authenticated": False}), 401
    u = g.current_user
    return jsonify({
        "authenticated": True,
        "user": {"id": u["id"], "email": u["email"],
                  "display_name": u.get("display_name"),
                  "role_in_tenant": u.get("role_in_tenant"),
                  "tenant_id": u.get("tenant_id")},
    })


@app.route("/api/auth/sessions")
def api_auth_sessions():
    from flask import g
    from tools.trading.auth import db as adb
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    return jsonify({
        "sessions": adb.list_sessions(g.current_user["id"]),
        "current_session_id": g.current_session_token,
    })


@app.route("/api/auth/sessions/<sid>", methods=["DELETE"])
def api_auth_revoke_session(sid):
    from flask import g
    from tools.trading.auth import db as adb
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    # Make sure the session being revoked belongs to current user
    sess = adb.get_session(sid)
    if not sess or sess["user_id"] != g.current_user["id"]:
        return jsonify({"error": "not found"}), 404
    n = adb.revoke_session(sid)
    return jsonify({"revoked": n})


# ─── Password reset (Phase 2A.2) ───────────────────────────────────────
@app.route("/forgot-password", methods=["GET"])
def auth_forgot_password():
    return render_template("auth_forgot.html")


@app.route("/api/auth/forgot-password", methods=["POST"])
def api_auth_forgot_password():
    """Issue a reset token + email it. Always returns 200 OK regardless of
    whether the email exists (no enumeration). Rate-limited to 3 requests
    per email per hour."""
    from tools.trading.auth import db as adb
    from tools.trading.auth import reset as adr
    from tools.trading.auth import email as ae
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "email required"}), 400
    user = adb.get_user_by_email(email)
    if user and not user.get("disabled"):
        # Rate limit
        if adr.recent_request_count(user["id"]) >= adr.RATE_LIMIT_PER_HOUR:
            # Silent — don't reveal rate-limit state to caller
            return jsonify({"ok": True})
        token = adr.issue_token(
            user["id"],
            ip=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
        base = (request.url_root or "http://localhost:5100/").rstrip("/")
        reset_url = f"{base}/reset-password?token={token}"
        ae.send_password_reset(
            to=email, reset_url=reset_url,
            display_name=user.get("display_name"),
        )
    # Always 200 — no user-existence leak
    return jsonify({"ok": True})


@app.route("/reset-password", methods=["GET"])
def auth_reset_password():
    token = request.args.get("token", "")
    return render_template("auth_reset.html", token=token)


@app.route("/api/auth/reset-password", methods=["POST"])
def api_auth_reset_password():
    """Consume the token + set the new password. Revokes all sessions."""
    from tools.trading.auth import db as adb
    from tools.trading.auth import passwords
    from tools.trading.auth import reset as adr
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    new_pw = data.get("new_password") or ""
    if not token or not new_pw:
        return jsonify({"error": "token + new_password required"}), 400
    ok, msg = passwords.policy_check(new_pw)
    if not ok:
        return jsonify({"error": msg}), 400
    user_id, status = adr.consume_token(token)
    if status == "ok":
        adb.update_password_hash(user_id, passwords.hash_password(new_pw))
        # Force re-login everywhere so a stolen session can't outlive the reset
        adb.revoke_all_sessions(user_id)
        return jsonify({"ok": True})
    return jsonify({
        "error": ({
            "invalid": "Reset link is invalid.",
            "expired": "Reset link has expired. Request a new one.",
            "used":    "Reset link has already been used. Request a new one.",
        }).get(status, "Reset link is invalid."),
    }), 400


# ─── Google OAuth (Phase 2A.2) ────────────────────────────────────────
@app.route("/oauth/<provider>/login")
def oauth_login(provider):
    from tools.trading.auth import oauth as a_oauth
    if not a_oauth.is_any_provider_configured():
        return jsonify({"error": "no OAuth providers configured"}), 503
    o = a_oauth.get_oauth()
    client = o.create_client(provider) if o else None
    if not client:
        return jsonify({"error": f"unknown provider: {provider}"}), 404
    redirect_base = (
        os.environ.get("ICDEV_OAUTH_REDIRECT_BASE")
        or (request.url_root or "http://localhost:5100/").rstrip("/")
    )
    callback = f"{redirect_base}/oauth/{provider}/callback"
    return client.authorize_redirect(callback)


@app.route("/oauth/<provider>/callback")
def oauth_callback(provider):
    from tools.trading.auth import oauth as a_oauth
    from tools.trading.auth import db as adb
    from tools.trading.auth.middleware import SESSION_COOKIE
    from flask import make_response, redirect as flask_redirect
    try:
        o = a_oauth.get_oauth()
        client = o.create_client(provider) if o else None
        if not client:
            return jsonify({"error": f"unknown provider: {provider}"}), 404
        token = client.authorize_access_token()

        # Provider-specific userinfo extraction.
        # OIDC providers (Google, Microsoft) include `userinfo` in the token.
        # GitHub is OAuth 2.0 — call /user explicitly, and /user/emails when
        # the public profile email is null (user has it set to private).
        info: dict = {}
        if provider == "github":
            try:
                resp = client.get("user", token=token)
                info = resp.json() if resp.status_code == 200 else {}
                if not info.get("email"):
                    er = client.get("user/emails", token=token)
                    if er.status_code == 200:
                        emails = er.json() or []
                        primary = next(
                            (e for e in emails
                             if e.get("primary") and e.get("verified")),
                            None,
                        )
                        if primary:
                            info["email"] = primary.get("email")
            except Exception:
                info = {}
        else:
            info = token.get("userinfo") or {}
            if not info and hasattr(client, "userinfo"):
                try:
                    info = client.userinfo() or {}
                except Exception:
                    info = {}

        email = info.get("email") or ""
        sub = info.get("sub") or str(info.get("id") or "")
        name = (info.get("name") or info.get("given_name")
                or info.get("login"))  # GitHub: 'login' is the username
        if not email:
            return jsonify({"error": "OAuth provider did not return a verified email. For GitHub, ensure your primary email is verified."}), 400

        is_first_user = (adb.count_users() == 0)
        uid = a_oauth.upsert_oauth_user(
            email=email, provider=provider, provider_sub=sub, display_name=name,
        )

        # Bootstrap the legacy default profile to this user too
        if is_first_user:
            try:
                from tools.db.storage import get_connection, sql_placeholder
                conn = get_connection()
                ph = sql_placeholder(conn)
                try:
                    conn.execute(
                        f"UPDATE ad_user_profiles SET user_id = {ph} WHERE user_id = 'default'",
                        (uid,),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass
            try:
                from tools.trading.tenancy import db as tenant_db
                tenant_db.claim_default_tenant_owner(uid)
            except Exception:
                pass

        adb.touch_login(uid)
        sess_token = adb.create_session(
            user_id=uid,
            user_agent=request.headers.get("User-Agent"),
            ip=request.remote_addr,
        )
        # If user has MFA, redirect to /mfa/verify; else stamp as satisfied
        from tools.trading.auth import mfa as a_mfa
        if a_mfa.has_mfa(uid):
            target = "/mfa/verify"
        else:
            a_mfa.mark_session_mfa_satisfied(sess_token)
            target = "/"
        resp = make_response(flask_redirect(target))
        resp.set_cookie(SESSION_COOKIE, sess_token, httponly=True,
                          samesite="Lax", max_age=168 * 3600, secure=False)
        return resp
    except Exception as e:
        return jsonify({"error": f"OAuth callback failed: {e}"}), 500


# ─── MFA — TOTP enrollment + verification (Phase 2A.3) ────────────────
@app.route("/mfa/verify", methods=["GET"])
def auth_mfa_verify_page():
    """Post-login MFA challenge page (visible only when authenticated but
    session.mfa_satisfied is false)."""
    from flask import g
    if not getattr(g, "current_user", None):
        return redirect(url_for("auth_login"))
    return render_template("auth_mfa_verify.html")


@app.route("/api/auth/mfa/verify", methods=["POST"])
def api_auth_mfa_verify():
    """Verify a TOTP code OR a backup code. Marks session mfa_satisfied on success."""
    from flask import g
    from tools.trading.auth import mfa as a_mfa
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    use_backup = bool(data.get("backup"))
    if not code:
        return jsonify({"error": "code required"}), 400
    uid = g.current_user["id"]
    if use_backup:
        ok, reason = a_mfa.consume_backup_code(
            uid, code,
            ip=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
    else:
        ok, reason = a_mfa.verify_totp(
            uid, code,
            ip=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
    if reason == "locked":
        return jsonify({"error": "Too many failed attempts. Try again in 15 min."}), 429
    if not ok:
        return jsonify({"error": "Invalid code"}), 401
    a_mfa.mark_session_mfa_satisfied(g.current_session_token)
    return jsonify({"ok": True})


@app.route("/api/auth/mfa/state")
def api_auth_mfa_state():
    from flask import g
    from tools.trading.auth import mfa as a_mfa
    from tools.trading.auth import crypto as a_crypto
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    state = a_mfa.get_mfa_state(g.current_user["id"])
    state["keystore_available"] = a_crypto.is_available()
    state["session_mfa_satisfied"] = a_mfa.session_mfa_satisfied(g.current_session)
    return jsonify(state)


@app.route("/api/auth/mfa/enroll/totp", methods=["POST"])
def api_auth_mfa_enroll_totp():
    from flask import g
    from tools.trading.auth import mfa as a_mfa
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    try:
        out = a_mfa.begin_enrollment(
            g.current_user["id"],
            account_label=g.current_user["email"],
        )
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/auth/mfa/enroll/totp/confirm", methods=["POST"])
def api_auth_mfa_enroll_totp_confirm():
    from flask import g
    from tools.trading.auth import mfa as a_mfa
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    result = a_mfa.verify_enrollment_code(g.current_user["id"], code)
    if not result.get("ok"):
        return jsonify({"error": result.get("error", "invalid")}), 400
    # Stamp the current session as MFA-satisfied (user just proved they have it)
    a_mfa.mark_session_mfa_satisfied(g.current_session_token)
    return jsonify(result)


@app.route("/api/auth/mfa/disable", methods=["POST"])
def api_auth_mfa_disable():
    """Disable TOTP — requires step-up MFA proof to call."""
    from flask import g
    from tools.trading.auth import mfa as a_mfa
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    if a_mfa.has_mfa(g.current_user["id"]) and not a_mfa.session_mfa_satisfied(g.current_session):
        return jsonify({"error": "step-up MFA required",
                         "redirect": "/mfa/verify"}), 403
    a_mfa.disable(g.current_user["id"])
    return jsonify({"ok": True})


# ─── WebAuthn / Passkeys (Phase 2B) ──────────────────────────────────
@app.route("/api/auth/mfa/webauthn/credentials")
def api_auth_webauthn_list():
    from flask import g
    from tools.trading.auth import webauthn as a_wa
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    return jsonify({"credentials": a_wa.list_credentials(g.current_user["id"])})


@app.route("/api/auth/mfa/webauthn/credentials/<cred_pk>", methods=["DELETE"])
def api_auth_webauthn_delete(cred_pk):
    from flask import g
    from tools.trading.auth import webauthn as a_wa
    from tools.trading.auth import mfa as a_mfa
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    # Step-up: deleting credentials affects MFA — require fresh proof
    if a_mfa.has_mfa(g.current_user["id"]) and not a_mfa.session_mfa_satisfied(g.current_session):
        return jsonify({"error": "step-up MFA required",
                         "redirect": "/mfa/verify"}), 403
    n = a_wa.delete_credential(g.current_user["id"], cred_pk)
    return jsonify({"deleted": n})


@app.route("/api/auth/mfa/webauthn/register/begin", methods=["POST"])
def api_auth_webauthn_register_begin():
    from flask import g, session as fsession
    from tools.trading.auth import webauthn as a_wa
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    out = a_wa.begin_registration(
        user_id=g.current_user["id"],
        user_email=g.current_user["email"],
        display_name=g.current_user.get("display_name"),
    )
    # Stash the challenge in the signed Flask session for the complete step
    fsession["webauthn_register_challenge"] = out["challenge_b64"]
    return jsonify(out["options"])


@app.route("/api/auth/mfa/webauthn/register/complete", methods=["POST"])
def api_auth_webauthn_register_complete():
    from flask import g, session as fsession
    from tools.trading.auth import webauthn as a_wa
    from tools.trading.auth import mfa as a_mfa
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    chall = fsession.pop("webauthn_register_challenge", None)
    if not chall:
        return jsonify({"error": "no pending registration challenge"}), 400
    data = request.get_json(silent=True) or {}
    response = data.get("response") or data
    nickname = (data.get("nickname") or "").strip() or None
    try:
        out = a_wa.complete_registration(
            user_id=g.current_user["id"],
            expected_challenge_b64=chall,
            response=response,
            nickname=nickname,
        )
    except Exception as e:
        return jsonify({"error": f"registration failed: {e}"}), 400
    # User just proved possession — stamp session as MFA-satisfied
    a_mfa.mark_session_mfa_satisfied(g.current_session_token)
    return jsonify({"ok": True, **out})


@app.route("/api/auth/mfa/webauthn/authenticate/begin", methods=["POST"])
def api_auth_webauthn_auth_begin():
    from flask import g, session as fsession
    from tools.trading.auth import webauthn as a_wa
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    if not a_wa.has_credentials(g.current_user["id"]):
        return jsonify({"error": "no passkeys registered"}), 400
    out = a_wa.begin_authentication(user_id=g.current_user["id"])
    fsession["webauthn_auth_challenge"] = out["challenge_b64"]
    return jsonify(out["options"])


@app.route("/api/auth/mfa/webauthn/authenticate/complete", methods=["POST"])
def api_auth_webauthn_auth_complete():
    from flask import g, session as fsession
    from tools.trading.auth import webauthn as a_wa
    from tools.trading.auth import mfa as a_mfa
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    chall = fsession.pop("webauthn_auth_challenge", None)
    if not chall:
        return jsonify({"error": "no pending authentication challenge"}), 400
    data = request.get_json(silent=True) or {}
    response = data.get("response") or data
    result = a_wa.complete_authentication(
        user_id=g.current_user["id"],
        expected_challenge_b64=chall,
        response=response,
    )
    if not result.get("ok"):
        return jsonify({"error": result.get("error", "verification failed")}), 401
    a_mfa.mark_session_mfa_satisfied(g.current_session_token)
    return jsonify({"ok": True})


# Allow the WebAuthn auth endpoints during pre-MFA-verify (the user is
# logged in but hasn't satisfied MFA yet — they need these to authenticate)
# Add to middleware allowlist via an after-import patch
try:
    from tools.trading.auth.middleware import _MFA_PRE_VERIFY_ALLOWED
    _MFA_PRE_VERIFY_ALLOWED.add("/api/auth/mfa/webauthn/authenticate/begin")
    _MFA_PRE_VERIFY_ALLOWED.add("/api/auth/mfa/webauthn/authenticate/complete")
except Exception:
    pass


@app.route("/api/auth/mfa/backup-codes/regenerate", methods=["POST"])
def api_auth_mfa_regen_backup():
    from flask import g
    from tools.trading.auth import mfa as a_mfa
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    if not a_mfa.session_mfa_satisfied(g.current_session):
        return jsonify({"error": "step-up MFA required",
                         "redirect": "/mfa/verify"}), 403
    codes = a_mfa.regenerate_backup_codes(g.current_user["id"])
    return jsonify({"backup_codes": codes})


@app.route("/api/auth/oauth/providers")
def api_auth_oauth_providers():
    """Tells the login/signup pages which OAuth buttons to render."""
    from tools.trading.auth import oauth as a_oauth
    return jsonify({"providers": a_oauth.configured_providers()})


@app.route("/api/auth/password", methods=["POST"])
def api_auth_change_password():
    """Change password. Verifies current password; revokes all OTHER sessions."""
    from flask import g
    from tools.trading.auth import db as adb
    from tools.trading.auth import passwords
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    current = data.get("current_password") or ""
    new = data.get("new_password") or ""
    if not passwords.verify_password(g.current_user.get("password_hash"), current):
        return jsonify({"error": "Current password is incorrect"}), 401
    ok, msg = passwords.policy_check(new)
    if not ok:
        return jsonify({"error": msg}), 400
    adb.update_password_hash(g.current_user["id"], passwords.hash_password(new))
    # Keep the current session, kill the rest
    n = adb.revoke_all_sessions(g.current_user["id"],
                                  except_token=g.current_session_token)
    return jsonify({"ok": True, "other_sessions_revoked": n})


# ---------------------------------------------------------------------------
# BYOK — per-user API credentials (Phase 2A follow-up)
# ---------------------------------------------------------------------------
@app.route("/api/credentials")
def api_credentials_list():
    """Return all 4 provider states for the active user (frontend-safe;
    plaintext keys never leave the server)."""
    from flask import g
    from tools.trading.credentials import db as cred_db
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    try:
        return jsonify({
            "providers": cred_db.list_states(g.current_user["id"]),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/credentials/<provider>", methods=["PUT"])
def api_credentials_put(provider):
    """Set or update a provider's credentials. Only the fields supplied
    in the body are updated — pass null to skip a field."""
    from flask import g
    from tools.trading.credentials import db as cred_db
    from tools.trading.auth import mfa as a_mfa
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    # Step-up: API keys grant API spend — require fresh MFA proof when
    # the user has MFA enabled at all.
    if a_mfa.has_mfa(g.current_user["id"]) and not a_mfa.session_mfa_satisfied(g.current_session):
        return jsonify({"error": "step-up MFA required",
                         "redirect": "/mfa/verify"}), 403
    data = request.get_json(silent=True) or {}
    try:
        out = cred_db.upsert_credential(
            user_id=g.current_user["id"],
            provider=provider,
            key=(data.get("key") or None),
            secret=(data.get("secret") or None),
            base_url=(data.get("base_url") if "base_url" in data else None),
            ip=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
        return jsonify(out)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/credentials/<provider>", methods=["DELETE"])
def api_credentials_delete(provider):
    from flask import g
    from tools.trading.credentials import db as cred_db
    from tools.trading.auth import mfa as a_mfa
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    if a_mfa.has_mfa(g.current_user["id"]) and not a_mfa.session_mfa_satisfied(g.current_session):
        return jsonify({"error": "step-up MFA required",
                         "redirect": "/mfa/verify"}), 403
    try:
        n = cred_db.delete_credential(
            user_id=g.current_user["id"],
            provider=provider,
            ip=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
        return jsonify({"deleted": n})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/credentials/<provider>/test", methods=["POST"])
def api_credentials_test(provider):
    """Run the no-op probe for a provider's credentials."""
    from flask import g
    from tools.trading.credentials import tester
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    try:
        ok, msg = tester.test_provider(provider, g.current_user["id"])
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


# ─── Tenant (Phase 3.1) ──────────────────────────────────────────────
@app.route("/api/tenant")
def api_tenant_get():
    """Return the active user's tenant + their role within it."""
    from flask import g
    if not getattr(g, "current_user", None) or not getattr(g, "current_tenant", None):
        return jsonify({"error": "auth required"}), 401
    from tools.trading.tenancy import db as tenant_db
    t = dict(g.current_tenant)
    t["member_count"] = tenant_db.member_count(t["id"])
    t["my_role"] = g.current_user.get("role_in_tenant")
    return jsonify(t)


@app.route("/api/tenant", methods=["PATCH"])
def api_tenant_patch():
    """Update tenant settings (name, slug, branding). Owner/admin only.
    Branding fields land properly in Phase 3.3; for 3.1 we accept name + slug."""
    from flask import g
    from tools.trading.tenancy import db as tenant_db
    if not getattr(g, "current_user", None) or not getattr(g, "current_tenant", None):
        return jsonify({"error": "auth required"}), 401
    role = (g.current_user.get("role_in_tenant") or "").lower()
    if role not in ("owner", "admin"):
        return jsonify({"error": "owner or admin required"}), 403
    data = request.get_json(silent=True) or {}
    # Phase 3.3: branding fields are now editable
    allowed = {"name", "slug", "branding_logo_url",
               "branding_accent_color", "white_label_enabled"}
    bad = set(data) - allowed
    if bad:
        return jsonify({"error": f"unsupported fields: {sorted(bad)}"}), 400
    # Validate accent color shape (hex like #58a6ff or empty)
    accent = data.get("branding_accent_color")
    if accent:
        accent = accent.strip()
        import re as _re
        if not _re.match(r"^#[0-9a-fA-F]{6}$", accent):
            return jsonify({"error": "branding_accent_color must be 6-digit hex like #58a6ff"}), 400
        data["branding_accent_color"] = accent
    elif accent == "":
        data["branding_accent_color"] = None
    # Coerce white_label_enabled to int (DB stores 0/1)
    if "white_label_enabled" in data:
        data["white_label_enabled"] = int(bool(data["white_label_enabled"]))
    # Logo URL — light validation (must be https:// or http:// or empty)
    url = data.get("branding_logo_url")
    if url:
        url = url.strip()
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("data:")):
            return jsonify({"error": "branding_logo_url must be http(s):// or data: URL"}), 400
        if len(url) > 4096:
            return jsonify({"error": "branding_logo_url too long (max 4096 chars)"}), 400
        data["branding_logo_url"] = url
    elif url == "":
        data["branding_logo_url"] = None
    try:
        n = tenant_db.update_tenant(g.current_tenant["id"], **data)
        return jsonify({"updated": n})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


# ─── Tenant memberships + invitations (Phase 3.2) ─────────────────────
@app.route("/api/memberships")
def api_memberships_list():
    """List the current user's tenant memberships (for the switcher)."""
    from flask import g
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    from tools.trading.tenancy import db as tenant_db
    mems = tenant_db.list_user_memberships(g.current_user["id"])
    return jsonify({
        "memberships": mems,
        "active_tenant_id": (g.current_tenant or {}).get("id"),
    })


@app.route("/api/memberships/switch", methods=["POST"])
def api_memberships_switch():
    """Switch the active tenant. Updates ad_users.tenant_id (the
    'primary/active' tenant pointer) and ad_users.role_in_tenant."""
    from flask import g
    from tools.trading.tenancy import db as tenant_db
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    target = data.get("tenant_id")
    if not target:
        return jsonify({"error": "tenant_id required"}), 400
    mem = tenant_db.get_membership(g.current_user["id"], target)
    if not mem:
        return jsonify({"error": "not a member of that tenant"}), 403
    # Update ad_users.tenant_id + role_in_tenant
    from tools.db.storage import get_connection, sql_placeholder
    conn = get_connection()
    ph = sql_placeholder(conn)
    try:
        conn.execute(
            f"UPDATE ad_users SET tenant_id = {ph}, role_in_tenant = {ph} WHERE id = {ph}",
            (target, mem["role"], g.current_user["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "tenant_id": target, "role": mem["role"]})


# ─── Tenant members + invitations management ──────────────────────────
def _require_owner_or_admin():
    """Helper: 401 if not authed, 403 if not owner/admin in current tenant."""
    from flask import g
    if not getattr(g, "current_user", None) or not getattr(g, "current_tenant", None):
        return jsonify({"error": "auth required"}), 401
    role = (g.current_user.get("role_in_tenant") or "").lower()
    if role not in ("owner", "admin"):
        return jsonify({"error": "owner or admin required"}), 403
    return None


@app.route("/api/tenant/members")
def api_tenant_members_list():
    err = _require_owner_or_admin()
    if err: return err
    from flask import g
    from tools.trading.tenancy import db as tenant_db
    return jsonify({"members": tenant_db.list_tenant_members(g.current_tenant["id"])})


@app.route("/api/tenant/members/<user_id>", methods=["PATCH"])
def api_tenant_members_role(user_id):
    """Change a member's role. Owner-only (admins can invite but not change roles)."""
    from flask import g
    from tools.trading.tenancy import db as tenant_db
    if not getattr(g, "current_user", None) or not getattr(g, "current_tenant", None):
        return jsonify({"error": "auth required"}), 401
    if (g.current_user.get("role_in_tenant") or "").lower() != "owner":
        return jsonify({"error": "owner required"}), 403
    data = request.get_json(silent=True) or {}
    new_role = data.get("role")
    if new_role not in tenant_db.ROLES:
        return jsonify({"error": f"role must be one of {tenant_db.ROLES}"}), 400
    if new_role == "owner":
        return jsonify({"error": "transfer-ownership not supported in 3.2"}), 400
    n = tenant_db.update_membership_role(user_id, g.current_tenant["id"], new_role)
    return jsonify({"updated": n})


@app.route("/api/tenant/members/<user_id>", methods=["DELETE"])
def api_tenant_members_remove(user_id):
    """Remove a member from the tenant. Owner-only. Cannot remove self
    while you're owner (would orphan the tenant)."""
    from flask import g
    from tools.trading.tenancy import db as tenant_db
    if not getattr(g, "current_user", None) or not getattr(g, "current_tenant", None):
        return jsonify({"error": "auth required"}), 401
    if (g.current_user.get("role_in_tenant") or "").lower() != "owner":
        return jsonify({"error": "owner required"}), 403
    if user_id == g.current_user["id"]:
        return jsonify({"error": "cannot remove yourself as owner"}), 400
    target = tenant_db.get_membership(user_id, g.current_tenant["id"])
    if target and target.get("role") == "owner":
        return jsonify({"error": "cannot remove another owner"}), 400
    n = tenant_db.remove_membership(user_id, g.current_tenant["id"])
    return jsonify({"removed": n})


@app.route("/api/tenant/invitations")
def api_tenant_invitations_list():
    err = _require_owner_or_admin()
    if err: return err
    from flask import g
    from tools.trading.tenancy import db as tenant_db
    include = request.args.get("include_resolved") in ("1", "true", "yes")
    return jsonify({
        "invitations": tenant_db.list_invitations(
            g.current_tenant["id"], include_resolved=include
        ),
    })


@app.route("/api/tenant/invitations", methods=["POST"])
def api_tenant_invitations_create():
    """Generate an invite + send the email. Step-up MFA enforced."""
    err = _require_owner_or_admin()
    if err: return err
    from flask import g
    from tools.trading.tenancy import db as tenant_db
    from tools.trading.auth import mfa as a_mfa, email as ae
    if a_mfa.has_mfa(g.current_user["id"]) and not a_mfa.session_mfa_satisfied(g.current_session):
        return jsonify({"error": "step-up MFA required",
                         "redirect": "/mfa/verify"}), 403
    # Phase 5A: members + monthly invitation quotas
    try:
        from tools.trading.billing import tiers as _bt
        tid = g.current_tenant["id"]
        _bt.check_quota(tid, "members", _bt.count_members(tid))
        _bt.check_quota(tid, "invitations_per_month",
                         _bt.count_invitations_this_month(tid))
    except _bt.QuotaExceeded as qe:
        return jsonify({"error": str(qe), "quota": qe.quota_key,
                         "limit": qe.limit, "tier": qe.tier,
                         "upgrade_url": "/billing"}), 402
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    role = data.get("role") or "member"
    if not email:
        return jsonify({"error": "email required"}), 400
    try:
        inv_id, token = tenant_db.create_invitation(
            tenant_id=g.current_tenant["id"],
            email=email, role=role,
            invited_by=g.current_user["id"],
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    base = (request.url_root or "http://localhost:5100/").rstrip("/")
    accept_url = f"{base}/accept-invite?token={token}"
    inviter = g.current_user.get("display_name") or g.current_user["email"]
    text = (
        f"Hi,\n\n"
        f"{inviter} has invited you to join the FathomDesk workspace "
        f"\"{g.current_tenant.get('name')}\" as a {role}.\n\n"
        f"To accept, open this link within 7 days:\n\n"
        f"  {accept_url}\n\n"
        f"If you don't have an FathomDesk account, you'll be prompted to "
        f"create one — the invitation auto-applies after signup.\n\n"
        f"— FathomDesk\n"
    )
    try:
        ae.send(to=email, subject=f"You're invited to {g.current_tenant.get('name')} on FathomDesk",
                  text_body=text)
    except Exception as e:
        return jsonify({"ok": True, "invitation_id": inv_id,
                         "warning": f"email send failed: {e}",
                         "accept_url": accept_url})
    return jsonify({"ok": True, "invitation_id": inv_id, "accept_url": accept_url})


@app.route("/api/tenant/invitations/<inv_id>", methods=["DELETE"])
def api_tenant_invitations_revoke(inv_id):
    err = _require_owner_or_admin()
    if err: return err
    from tools.trading.tenancy import db as tenant_db
    n = tenant_db.revoke_invitation(inv_id)
    return jsonify({"revoked": n})


@app.route("/accept-invite", methods=["GET"])
def page_accept_invite():
    """Landing page for invitation links. Unauth: redirect to signup with
    return-token. Auth: show 'Accept' button that POSTs the token."""
    token = request.args.get("token", "").strip()
    return render_template("auth_accept_invite.html", token=token)


@app.route("/api/invitations/<token>/accept", methods=["POST"])
def api_invitation_accept(token):
    """Accept an invitation as the currently-authenticated user."""
    from flask import g
    from tools.trading.tenancy import db as tenant_db
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required",
                         "redirect": f"/login?next=/accept-invite?token={token}"}), 401
    ok, msg = tenant_db.accept_invitation(token, g.current_user["id"])
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"ok": True, "tenant_id": msg})


@app.route("/api/invitations/<token>/peek")
def api_invitation_peek(token):
    """Public: returns invitation metadata (email, tenant name, role,
    status). Used by the accept page to show context before login."""
    from tools.trading.tenancy import db as tenant_db
    inv = tenant_db.get_invitation_by_token(token)
    if not inv:
        return jsonify({"error": "invitation not found"}), 404
    # Strip token_hash — never return it to the client
    inv.pop("token_hash", None)
    return jsonify(inv)


@app.route("/api/credentials/audit")
def api_credentials_audit():
    """Per-user audit trail (set/delete/test/used)."""
    from flask import g
    from tools.trading.credentials import db as cred_db
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    limit = int(request.args.get("limit", 100))
    return jsonify({"audit": cred_db.list_audit(g.current_user["id"], limit=limit)})


@app.route("/api/credentials/secret-backend")
def api_credentials_secret_backend():
    """Phase 2D — show the operator which secrets backend is active +
    whether it's healthy. Visible to any authed user so they know where
    their keys live (informational; does not leak secrets)."""
    from flask import g
    from tools.trading.credentials import secret_store as _ss
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    store = _ss.get_active_store()
    return jsonify({
        "backend": _ss.active_backend_name(),
        "health": store.health(),
        "vault_backend_active": _ss.is_vault_backend(),
    })


# ─── Phase 2C — Vault (BYOK Path B) ────────────────────────────────────
def _vault_verify_password(user, password: str) -> bool:
    """Helper — refetch the user row and run argon2 verify."""
    from tools.trading.auth import db as adb
    from tools.trading.auth import passwords
    u = adb.get_user_by_id(user["id"])
    if not u:
        return False
    return passwords.verify_password(u.get("password_hash"), password)


def _vault_migrate_keys(
    *, user_id: str,
    from_dek: "bytes | None",
    to_dek: "bytes | None",
    to_vault_mode: int,
) -> dict:
    """Re-encrypt every credential row for this user from one crypto to
    another. `from_dek=None` means source is Path A (operator KEK);
    `to_dek=None` means destination is Path A."""
    from tools.trading.credentials import db as cred_db
    from tools.trading.credentials import vault as _vault
    from tools.trading.auth import crypto as ks
    rows = cred_db.list_credentials_raw_rows(user_id)
    migrated = 0
    errors = 0
    for r in rows:
        try:
            key_enc = r.get("key_encrypted")
            secret_enc = r.get("secret_encrypted")
            if not key_enc and not secret_enc:
                cred_db.set_vault_mode_for_credential(
                    user_id, r["provider"], to_vault_mode)
                continue
            # Decrypt from source
            key_pt = None
            secret_pt = None
            if key_enc:
                key_pt = (_vault.decrypt_with_dek(from_dek, key_enc)
                           if from_dek else ks.decrypt(key_enc))
            if secret_enc:
                secret_pt = (_vault.decrypt_with_dek(from_dek, secret_enc)
                              if from_dek else ks.decrypt(secret_enc))
            # Encrypt to destination
            new_key_enc = None
            new_secret_enc = None
            if key_pt:
                new_key_enc = (_vault.encrypt_with_dek(to_dek, key_pt)
                                if to_dek else ks.encrypt(key_pt))
            if secret_pt:
                new_secret_enc = (_vault.encrypt_with_dek(to_dek, secret_pt)
                                   if to_dek else ks.encrypt(secret_pt))
            cred_db.replace_credential_blobs(
                user_id=user_id, provider=r["provider"],
                key_encrypted=new_key_enc,
                secret_encrypted=new_secret_enc,
                vault_mode=to_vault_mode,
            )
            migrated += 1
        except Exception:
            errors += 1
    return {"migrated": migrated, "errors": errors}


@app.route("/api/credentials/vault")
def api_vault_status():
    """Status of the user's vault opt-in (no secrets returned)."""
    from flask import g
    from tools.trading.credentials import db as cred_db
    from tools.trading.credentials import vault as _vault
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    vrow = cred_db.get_vault_row(g.current_user["id"])
    session_dek_active = _vault.get_from_session() is not None
    return jsonify({
        "available": _vault.is_available(),
        "enabled": bool(vrow),
        "enabled_at": vrow.get("enabled_at") if vrow else None,
        "last_password_change": vrow.get("last_password_change") if vrow else None,
        "last_recovery_rotation": vrow.get("last_recovery_rotation") if vrow else None,
        "session_dek_active": session_dek_active,
    })


@app.route("/api/credentials/vault/enable", methods=["POST"])
def api_vault_enable():
    """Opt into vault mode. Requires current password.
    Migrates existing Path A keys into Path B.
    Returns the recovery code ONCE — caller must display it to the user."""
    from flask import g
    from tools.trading.credentials import db as cred_db
    from tools.trading.credentials import vault as _vault
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    if not _vault.is_available():
        return jsonify({"error": "cryptography package not installed — "
                         "vault mode unavailable",
                         "code": "crypto_unavailable"}), 501
    data = request.get_json(silent=True) or {}
    password = data.get("password") or ""
    if not password:
        return jsonify({"error": "current password required"}), 400
    if not _vault_verify_password(g.current_user, password):
        return jsonify({"error": "incorrect password"}), 401
    uid = g.current_user["id"]
    if cred_db.vault_enabled(uid):
        return jsonify({"error": "vault already enabled",
                         "code": "already_enabled"}), 400
    try:
        prov = _vault.provision(password)
        cred_db.insert_vault(
            user_id=uid,
            salt_b64=prov["salt_b64"],
            password_wrapped_dek=prov["password_wrapped_dek"],
            recovery_wrapped_dek=prov["recovery_wrapped_dek"],
        )
        # Migrate existing Path A keys into Path B.
        migration = _vault_migrate_keys(
            user_id=uid,
            from_dek=None,   # current state = Path A
            to_dek=prov["master_dek"],
            to_vault_mode=1,
        )
        # Stash the DEK in the active session so the user can immediately
        # add / edit keys without needing to re-login.
        _vault.stash_in_session(prov["master_dek"])
        cred_db.audit(user_id=uid, provider="*", action="vault_enabled",
                      detail=f"migrated={migration['migrated']} errors={migration['errors']}")
    except Exception as e:
        return jsonify({"error": f"vault enable failed: {e}"}), 500
    return jsonify({
        "ok": True,
        "recovery_code": prov["recovery_code"],
        "migration": migration,
    })


@app.route("/api/credentials/vault/disable", methods=["POST"])
def api_vault_disable():
    """Opt back out — migrate keys to Path A (operator KEK) + delete
    vault row. Requires current password. Frees daemon access again."""
    from flask import g
    from tools.trading.credentials import db as cred_db
    from tools.trading.credentials import vault as _vault
    from tools.trading.auth import crypto as ks
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    password = data.get("password") or ""
    if not password:
        return jsonify({"error": "current password required"}), 400
    if not _vault_verify_password(g.current_user, password):
        return jsonify({"error": "incorrect password"}), 401
    uid = g.current_user["id"]
    vrow = cred_db.get_vault_row(uid)
    if not vrow:
        return jsonify({"error": "vault not enabled"}), 400
    if not ks.is_available():
        return jsonify({"error": "ICDEV_KEYSTORE_KEY not set — cannot migrate "
                         "back to Path A"}), 501
    try:
        dek = _vault.decrypt_dek_with_password(
            salt_b64=vrow["salt_b64"],
            password_wrapped_dek=vrow["password_wrapped_dek"],
            password=password,
        )
        migration = _vault_migrate_keys(
            user_id=uid, from_dek=dek, to_dek=None, to_vault_mode=0,
        )
        cred_db.delete_vault(uid)
        _vault.clear_session()
        cred_db.audit(user_id=uid, provider="*", action="vault_disabled",
                      detail=f"migrated={migration['migrated']} errors={migration['errors']}")
    except Exception as e:
        return jsonify({"error": f"vault disable failed: {e}"}), 500
    return jsonify({"ok": True, "migration": migration})


@app.route("/api/credentials/vault/rotate-recovery", methods=["POST"])
def api_vault_rotate_recovery():
    """Generate a new recovery code. Invalidates the old code immediately."""
    from flask import g
    from tools.trading.credentials import db as cred_db
    from tools.trading.credentials import vault as _vault
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    password = data.get("password") or ""
    if not password:
        return jsonify({"error": "current password required"}), 400
    if not _vault_verify_password(g.current_user, password):
        return jsonify({"error": "incorrect password"}), 401
    uid = g.current_user["id"]
    vrow = cred_db.get_vault_row(uid)
    if not vrow:
        return jsonify({"error": "vault not enabled"}), 400
    try:
        dek = _vault.decrypt_dek_with_password(
            salt_b64=vrow["salt_b64"],
            password_wrapped_dek=vrow["password_wrapped_dek"],
            password=password,
        )
        new_code, new_wrapped = _vault.rotate_recovery(
            master_dek=dek, salt_b64=vrow["salt_b64"],
        )
        cred_db.update_vault_recovery_wrapping(
            user_id=uid, recovery_wrapped_dek=new_wrapped,
        )
        cred_db.audit(user_id=uid, provider="*", action="vault_recovery_rotated")
    except Exception as e:
        return jsonify({"error": f"rotate failed: {e}"}), 500
    return jsonify({"ok": True, "recovery_code": new_code})


@app.route("/api/credentials/vault/recover", methods=["POST"])
def api_vault_recover():
    """Forgot-password path. User provides recovery code + new password.
    Decrypts DEK via recovery wrapping, re-wraps with new password, and
    rotates the recovery code. Also updates the argon2 password hash."""
    from flask import g
    from tools.trading.credentials import db as cred_db
    from tools.trading.credentials import vault as _vault
    from tools.trading.auth import db as adb
    from tools.trading.auth import passwords
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401
    data = request.get_json(silent=True) or {}
    recovery_code = data.get("recovery_code") or ""
    new_password = data.get("new_password") or ""
    if not recovery_code or not new_password:
        return jsonify({"error": "recovery_code + new_password required"}), 400
    ok, msg = passwords.policy_check(new_password)
    if not ok:
        return jsonify({"error": msg}), 400
    uid = g.current_user["id"]
    vrow = cred_db.get_vault_row(uid)
    if not vrow:
        return jsonify({"error": "vault not enabled"}), 400
    try:
        dek = _vault.decrypt_dek_with_recovery(
            salt_b64=vrow["salt_b64"],
            recovery_wrapped_dek=vrow["recovery_wrapped_dek"],
            recovery_code=recovery_code,
        )
    except _vault.VaultError:
        return jsonify({"error": "invalid recovery code"}), 401
    try:
        # Re-wrap with the new password.
        new_pw_wrapped = _vault.rewrap_dek_with_new_password(
            master_dek=dek, salt_b64=vrow["salt_b64"],
            new_password=new_password,
        )
        cred_db.update_vault_password_wrapping(
            user_id=uid, password_wrapped_dek=new_pw_wrapped,
        )
        # Rotate recovery (the old code is now burned).
        new_code, new_rec_wrapped = _vault.rotate_recovery(
            master_dek=dek, salt_b64=vrow["salt_b64"],
        )
        cred_db.update_vault_recovery_wrapping(
            user_id=uid, recovery_wrapped_dek=new_rec_wrapped,
        )
        # Update the argon2 password hash + stash DEK in session so the
        # user is unlocked immediately.
        adb.update_password_hash(uid, passwords.hash_password(new_password))
        _vault.stash_in_session(dek)
        cred_db.audit(user_id=uid, provider="*", action="vault_recovered")
    except Exception as e:
        return jsonify({"error": f"recovery failed: {e}"}), 500
    return jsonify({"ok": True, "recovery_code": new_code})


# ---------------------------------------------------------------------------
# TA Chart API  (ad79-ui-01)
# ---------------------------------------------------------------------------
import threading as _threading

_ta_chart_cache: dict = {}
_ta_chart_lock = _threading.Lock()
_TA_CHART_TTL = 60  # seconds

_PATTERN_LABELS: dict = {
    "double_top": "▼ Double Top",
    "double_bottom": "▲ Double Bottom",
    "triple_top": "▼ Triple Top",
    "triple_bottom": "▲ Triple Bottom",
    "rising_wedge": "⋀ Rising Wedge",
    "falling_wedge": "⋁ Falling Wedge",
    "head_and_shoulders": "▼ Head & Shoulders",
    "inverse_head_and_shoulders": "▲ Inv. H&S",
}


def _enrich_chart_patterns(patterns: list) -> list:
    """Add breakout_bar, confidence, label, price_low/high to raw detect_patterns output."""
    enriched = []
    for p in patterns:
        ep = dict(p)
        ep["label"] = _PATTERN_LABELS.get(p["type"], p["type"])
        ep["breakout_bar"] = p["end_bar"]
        tol = p.get("tolerance_pct", 3.0) or 3.0

        if p["type"] in ("double_top", "double_bottom"):
            avg = p.get("avg_price") or 0.0
            if p["type"] == "double_top":
                s1, s2 = p["high_1"], p["high_2"]
                ep["price_high"] = round(avg, 4)
                ep["price_low"] = round(p["neckline"]["price"], 4)
            else:
                s1, s2 = p["low_1"], p["low_2"]
                ep["price_low"] = round(avg, 4)
                ep["price_high"] = round(p["neckline"]["price"], 4)
            max_dev = (max(abs(s1["price"] - avg), abs(s2["price"] - avg)) / avg * 100) if avg else 0
            ep["confidence"] = round(max(0.10, min(0.99, 1.0 - max_dev / tol)), 2)

        elif p["type"] in ("triple_top", "triple_bottom"):
            avg = p.get("avg_price") or 0.0
            prices = [p["swing_1"]["price"], p["swing_2"]["price"], p["swing_3"]["price"]]
            if p["type"] == "triple_top":
                ep["price_high"] = round(avg, 4)
                ep["price_low"] = round(min(prices), 4)
            else:
                ep["price_low"] = round(avg, 4)
                ep["price_high"] = round(max(prices), 4)
            max_dev = (max(abs(pr - avg) for pr in prices) / avg * 100) if avg else 0
            ep["confidence"] = round(max(0.10, min(0.99, 1.0 - max_dev / tol)), 2)

        elif p["type"] in ("rising_wedge", "falling_wedge"):
            sb, eb = p["start_bar"], p["end_bar"]
            sh, ih = p.get("slope_high", 0), p.get("intercept_high", 0)
            sl, il = p.get("slope_low", 0), p.get("intercept_low", 0)
            ep["price_high"] = round(max(sh * sb + ih, sh * eb + ih), 4)
            ep["price_low"] = round(min(sl * sb + il, sl * eb + il), 4)
            ep["confidence"] = 0.65

        else:
            ep["confidence"] = 0.50
            ep.setdefault("price_low", 0.0)
            ep.setdefault("price_high", 0.0)

        enriched.append(ep)
    return enriched


def _ta_cache_key(ticker: str, timeframe: str, limit: int) -> str:
    return f"{ticker.upper()}:{timeframe}:{limit}"


@app.route("/api/ta/chart/<ticker>")
def api_ta_chart(ticker: str):
    from flask import g
    if not getattr(g, "current_user", None):
        return jsonify({"error": "auth required"}), 401

    timeframe = request.args.get("timeframe", "1D") or "1D"
    try:
        limit = max(1, min(int(request.args.get("limit", 120)), 1000))
    except (ValueError, TypeError):
        limit = 120

    # Map UI-style timeframe aliases (1D, 1H) to Alpaca-style (1Day, 1Hour)
    _tf_map = {
        "1D": "1Day", "1W": "1Week", "1M": "1Month",
        "1H": "1Hour", "4H": "4Hour", "15": "15Min", "5": "5Min", "1": "1Min",
    }
    alpaca_tf = _tf_map.get(timeframe, timeframe)

    cache_key = _ta_cache_key(ticker, alpaca_tf, limit)
    now_ts = _time.time()

    with _ta_chart_lock:
        hit = _ta_chart_cache.get(cache_key)
        if hit and (now_ts - hit["ts"]) < _TA_CHART_TTL:
            payload = hit["payload"]
            payload["cached"] = True
            return jsonify(payload)

    try:
        from tools.trading.data.market_data import fetch_bars
        from tools.trading.ta.volume_profile import volume_profile as _vp
        from tools.trading.ta.swings import find_swings
        from tools.trading.ta.sr import find_support_resistance
        from tools.trading.ta.patterns import detect_patterns

        bars = fetch_bars(ticker.upper(), timeframe=alpaca_tf, limit=limit) or []
        swings = find_swings(bars)
        vp = _vp(bars)
        sr_levels = find_support_resistance(bars, swings=swings, volume_profile=vp)
        patterns = _enrich_chart_patterns(detect_patterns(bars))

        payload = {
            "ticker": ticker.upper(),
            "timeframe": timeframe,
            "limit": limit,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "cached": False,
            "bars": bars,
            "volume_profile": vp,
            "sr_levels": sr_levels,
            "patterns": patterns,
            "swings": swings,
        }
    except Exception as exc:
        return jsonify({"error": str(exc), "ticker": ticker.upper()}), 500

    with _ta_chart_lock:
        _ta_chart_cache[cache_key] = {"ts": _time.time(), "payload": payload}

    return jsonify(payload)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FathomDesk Dashboard")
    parser.add_argument("--port", type=int, default=5100)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port, debug=args.debug, threaded=True)  # nosec B104 -- intentional bind-all for containerized/dev deployment
