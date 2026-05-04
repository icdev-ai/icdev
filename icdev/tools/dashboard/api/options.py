# CUI // SP-CTI
"""Options API — IV Rank and IV Percentile endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

options_api = Blueprint("options_api", __name__)


@options_api.route("/api/options/ivr/<ticker>", methods=["GET"])
def get_ivr(ticker: str):
    """Return IV Rank and IV Percentile for *ticker*.

    Optionally accepts ?iv=<float> to supply the current ATM IV directly.
    When omitted, fetches it from the nearest options expiration via yfinance.
    """
    ticker = ticker.upper()
    try:
        import math

        import yfinance as yf
        from tools.trading.options.chain import _get_atm_iv, compute_ivr

        supplied_iv = request.args.get("iv", type=float)

        if supplied_iv is not None and supplied_iv > 0:
            current_atm_iv = supplied_iv
        else:
            ticker_obj = yf.Ticker(ticker)
            try:
                spot = float(ticker_obj.fast_info.last_price)
            except Exception:
                spot = 100.0

            current_atm_iv = None
            try:
                expirations = ticker_obj.options
                if expirations:
                    chain = ticker_obj.option_chain(expirations[0])
                    iv = _get_atm_iv(chain, spot)
                    if iv and not math.isnan(iv) and iv > 0:
                        current_atm_iv = float(iv)
            except Exception:
                pass

            if current_atm_iv is None or current_atm_iv <= 0:
                current_atm_iv = 0.20

        result = compute_ivr(ticker, current_atm_iv)
        return jsonify({"ticker": ticker, **result})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@options_api.route("/api/options/hedge-proposal", methods=["POST"])
def hedge_proposal():
    """Return an AI-suggested hedge proposal based on Greek deviation from targets.

    Request body (JSON):
        {
          "user_id": "default",          # optional, defaults to "default"
          "greek_targets": {             # optional keys, all default to 0
            "target_delta": 0,
            "target_gamma": 0,
            "target_vega": 0,
            "target_theta": 0,
            "target_vanna": 0,
            "target_charm": 0,
            "target_volga": 0
          }
        }

    Response (JSON):
        {
          "user_id": str,
          "current_greeks": {...},
          "targets": {...},
          "gaps": {...},
          "strategies": [{"strategy": str, "rationale": str, "urgency": str}],
          "as_of": str
        }
    """
    try:
        from tools.trading.options.proposal_builder import build_hedge_proposal

        body = request.get_json(silent=True) or {}
        user_id = str(body.get("user_id", "default"))
        greek_targets = body.get("greek_targets", {})

        if not isinstance(greek_targets, dict):
            return jsonify({"error": "greek_targets must be a JSON object"}), 400

        result = build_hedge_proposal(user_id=user_id, greek_targets=greek_targets)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@options_api.route("/api/options/net_greeks/<ticker>", methods=["GET"])
def net_greeks(ticker: str):
    """Return portfolio-level net Greeks for *user_id* (ticker param kept for UI compat).

    Query params:
        user_id (str, default "default")
    """
    ticker = ticker.upper()
    user_id = request.args.get("user_id", "default")
    try:
        from tools.trading.options.portfolio_greeks import compute_portfolio_greeks

        pg = compute_portfolio_greeks(user_id=user_id)
        # Remap net_X → X for frontend slider compatibility
        return jsonify({
            "ticker": ticker,
            "user_id": user_id,
            "delta": pg.get("net_delta", 0.0),
            "gamma": pg.get("net_gamma", 0.0),
            "theta": pg.get("net_theta", 0.0),
            "vega":  pg.get("net_vega",  0.0),
            "rho":   0.0,
            "vanna": pg.get("net_vanna", 0.0),
            "charm": pg.get("net_charm", 0.0),
            "volga": pg.get("net_volga", 0.0),
            "position_count": pg.get("position_count", 0),
            "stale_count":    pg.get("stale_count", 0),
            "as_of": pg.get("as_of"),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@options_api.route("/api/options/iv-skew/<ticker>", methods=["GET"])
def iv_skew(ticker: str):
    """Return IV smile (by strike) and IV term structure (by expiry) for *ticker*.

    Query params:
        expiry_index (int, default 0) — which expiry to use for the skew slice
    """
    ticker = ticker.upper()
    expiry_index = request.args.get("expiry_index", 0, type=int)
    try:
        import math

        import yfinance as yf

        ticker_obj = yf.Ticker(ticker)
        try:
            spot = float(ticker_obj.fast_info.last_price)
        except Exception:
            spot = 0.0

        expirations = list(ticker_obj.options or [])
        if not expirations:
            return jsonify({"ticker": ticker, "skew": [], "term": [], "spot": spot})

        # IV smile for the chosen expiry
        chosen_exp = expirations[min(expiry_index, len(expirations) - 1)]
        skew: list[dict] = []
        try:
            chain = ticker_obj.option_chain(chosen_exp)
            for _, row in chain.calls.iterrows():
                iv = float(row.get("impliedVolatility") or 0)
                if iv > 0 and not math.isnan(iv):
                    skew.append({"strike": float(row["strike"]), "iv": round(iv * 100, 2), "type": "call"})
            for _, row in chain.puts.iterrows():
                iv = float(row.get("impliedVolatility") or 0)
                if iv > 0 and not math.isnan(iv):
                    skew.append({"strike": float(row["strike"]), "iv": round(iv * 100, 2), "type": "put"})
        except Exception:
            pass

        # ATM IV term structure across nearest 6 expirations
        from datetime import date, datetime

        term: list[dict] = []
        for exp in expirations[:6]:
            try:
                c = ticker_obj.option_chain(exp)
                if spot > 0:
                    atm = c.calls.iloc[(c.calls["strike"] - spot).abs().argsort()[:1]]
                    iv = float(atm.iloc[0].get("impliedVolatility") or 0)
                else:
                    iv = float(c.calls.iloc[0].get("impliedVolatility") or 0)
                if iv > 0 and not math.isnan(iv):
                    exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                    dte = (exp_date - date.today()).days
                    term.append({"expiry": exp, "dte": dte, "atm_iv": round(iv * 100, 2)})
            except Exception:
                continue

        return jsonify({
            "ticker": ticker,
            "expiry": chosen_exp,
            "spot": spot,
            "skew": sorted(skew, key=lambda x: x["strike"]),
            "term": term,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@options_api.route("/api/options/iv-skew", methods=["GET"])
def iv_skew_query():
    """Return put/call IV skew across strikes at nearest expiry.

    Query params:
        ticker (str, required)

    Response: [{strike, call_iv, put_iv, skew}] sorted by strike
    """
    ticker = (request.args.get("ticker") or "").upper()
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400
    try:
        import math

        import yfinance as yf

        ticker_obj = yf.Ticker(ticker)

        expirations = list(ticker_obj.options or [])
        if not expirations:
            return jsonify([])

        chain = ticker_obj.option_chain(expirations[0])

        calls_map: dict[float, float] = {}
        for _, row in chain.calls.iterrows():
            iv = float(row.get("impliedVolatility") or 0)
            if iv > 0 and not math.isnan(iv):
                calls_map[float(row["strike"])] = round(iv * 100, 2)

        puts_map: dict[float, float] = {}
        for _, row in chain.puts.iterrows():
            iv = float(row.get("impliedVolatility") or 0)
            if iv > 0 and not math.isnan(iv):
                puts_map[float(row["strike"])] = round(iv * 100, 2)

        strikes = sorted(calls_map.keys() | puts_map.keys())
        result = []
        for s in strikes:
            call_iv = calls_map.get(s, 0.0)
            put_iv = puts_map.get(s, 0.0)
            if call_iv > 0 or put_iv > 0:
                result.append({
                    "strike": s,
                    "call_iv": call_iv,
                    "put_iv": put_iv,
                    "skew": round(put_iv - call_iv, 2),
                })

        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@options_api.route("/api/options/iv-term", methods=["GET"])
def iv_term_query():
    """Return ATM IV per expiry for the nearest 8 expirations.

    Query params:
        ticker (str, required)

    Response: [{expiry, dte, atm_iv}] sorted by DTE
    """
    ticker = (request.args.get("ticker") or "").upper()
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400
    try:
        import math
        from datetime import date, datetime

        import yfinance as yf

        ticker_obj = yf.Ticker(ticker)
        try:
            spot = float(ticker_obj.fast_info.last_price)
        except Exception:
            spot = 0.0

        expirations = list(ticker_obj.options or [])
        if not expirations:
            return jsonify([])

        result = []
        for exp in expirations[:8]:
            try:
                c = ticker_obj.option_chain(exp)
                if spot > 0:
                    atm = c.calls.iloc[(c.calls["strike"] - spot).abs().argsort()[:1]]
                    iv = float(atm.iloc[0].get("impliedVolatility") or 0)
                else:
                    iv = float(c.calls.iloc[0].get("impliedVolatility") or 0)
                if iv > 0 and not math.isnan(iv):
                    exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                    dte = (exp_date - date.today()).days
                    result.append({"expiry": exp, "dte": dte, "atm_iv": round(iv * 100, 2)})
            except Exception:
                continue

        result.sort(key=lambda x: x["dte"])
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


_PAPER_PREFIX = "paper_"


@options_api.route("/api/options/portfolio-greeks/<user_id>", methods=["GET"])
def portfolio_greeks_by_user(user_id: str):
    """Return portfolio-level Greek P&L attribution for *user_id*.

    Paper sandboxes pass attempt IDs like ``paper_u-<id>``; strip the prefix
    so positions stored under the real user_id are found.
    """
    try:
        from tools.trading.options.portfolio_greeks import compute_portfolio_greeks

        real_uid = user_id[len(_PAPER_PREFIX):] if user_id.startswith(_PAPER_PREFIX) else user_id
        pg = compute_portfolio_greeks(user_id=real_uid)
        return jsonify({
            "user_id": real_uid,
            "net_delta": pg.get("net_delta", 0.0),
            "net_theta": pg.get("net_theta", 0.0),
            "net_vega":  pg.get("net_vega",  0.0),
            "net_gamma": pg.get("net_gamma", 0.0),
            "position_count": pg.get("position_count", 0),
            "stale_count":    pg.get("stale_count", 0),
            "as_of": pg.get("as_of"),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
