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
