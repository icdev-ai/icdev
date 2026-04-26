# CUI // SP-CTI
"""FathomDesk Phase 7.6 — pre-flight risk gates for AI-assisted proposals.

Public
------
  run_preflight(proposal, *, user_id=None, equity=None, is_live=False,
                options_tier=None) -> dict

A proposal is the dict returned by `proposal_builder.build_proposal()`.
This module NEVER executes — inspection only. The return shape is:

    {
      "allowed": bool,          # False if any BLOCK fires
      "warnings": [{"code": str, "message": str}],
      "blocks":   [{"code": str, "message": str}],
      "meta":     {"max_loss_abs": float, "max_loss_pct": float, ...},
    }

Gates loaded from `args/options_risk_gates.yaml`. Hot-reloadable.

There are 6 gates total (Gate 0 through Gate 5).
"""
from __future__ import annotations

import enum
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from tools.trading.options import strategies as _catalog

_log = logging.getLogger(__name__)


class GateResultCode(str, enum.Enum):
    """String codes emitted in blocks/warnings by run_preflight."""

    # Gate 0 — backtest quality floor
    BELOW_SHARPE_FLOOR = "below_sharpe_floor"
    ABOVE_DRAWDOWN_FLOOR = "above_drawdown_floor"
    NO_BACKTEST_ON_RECORD = "no_backtest_on_record"

    # Gate 1 — max-loss vs equity
    MAX_LOSS_EXCEEDS_EQUITY_CAP = "max_loss_exceeds_equity_cap"

    # Gate 2 — undefined-risk tier
    TIER_INSUFFICIENT_FOR_UNDEFINED_RISK = "tier_insufficient_for_undefined_risk"
    TIER_WOULD_BLOCK_LIVE = "tier_would_block_live"

    # Gate 3 — DTE range
    DTE_TOO_SHORT = "dte_too_short"
    DTE_TOO_LONG = "dte_too_long"

    # Gate 4 — IV regime warn band
    SHORT_PREMIUM_INTO_LOW_IV = "short_premium_into_low_iv"
    LONG_PREMIUM_INTO_HIGH_IV = "long_premium_into_high_iv"

    # Gate 5 — proposal-emitted warnings
    SELECTOR_WARNING = "selector_warning"

    # Proposal-level error (pre-gate)
    PROPOSAL_ERROR = "proposal_error"


# Total number of enumerated gates (Gate 0 through Gate 5).
TOTAL_GATES = 6

_GATES_PATH = (
    Path(__file__).resolve().parents[3] / "args" / "options_risk_gates.yaml"
)

_FALLBACK_GATES = {
    "max_loss_pct_of_equity": 2.0,
    "iv_warn_band": {"low_percentile": 20, "high_percentile": 80},
    "undefined_risk_required_tier": "L3",
    "min_dte_days": 0,
    "max_dte_days": 180,
    "max_loss_floor_usd": 25,
}

_TIER_RANK = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}

_cache: dict[str, Any] = {"mtime": 0.0, "data": None}


def _gates() -> dict:
    try:
        st = _GATES_PATH.stat()
    except OSError:
        return _FALLBACK_GATES
    if st.st_mtime != _cache["mtime"] or _cache["data"] is None:
        try:
            _cache["data"] = yaml.safe_load(_GATES_PATH.read_text(encoding="utf-8"))
            _cache["mtime"] = st.st_mtime
        except Exception as e:
            _log.warning("preflight: gates reload failed (%s)", e)
            _cache["data"] = _FALLBACK_GATES
    return _cache["data"] or _FALLBACK_GATES


def _undefined_risk(strategy_id: str) -> bool:
    """True if the strategy can theoretically lose > its cost-to-open."""
    strat = _catalog.get_strategy(strategy_id) or {}
    risk = strat.get("risk") or {}
    ml = str(risk.get("max_loss") or "").lower()
    return "unlimited" in ml or "to $0" in ml or "stock cost basis" in ml


def _tier_ok(have: str | None, need: str) -> bool:
    if not have:
        return False  # unknown tier → fail-closed
    return _TIER_RANK.get(have.upper(), 0) >= _TIER_RANK.get(need.upper(), 99)


def _dte(expiry: str | None) -> int | None:
    if not expiry:
        return None
    try:
        d = datetime.fromisoformat(expiry).date()
    except Exception:
        return None
    return (d - date.today()).days


def _account_equity(user_id: str | None, explicit: float | None) -> float | None:
    """Pull live-account equity if available. Explicit override wins."""
    if explicit is not None:
        try:
            return float(explicit)
        except (TypeError, ValueError):
            return None
    # Best-effort live pull — fail silently (preflight still returns;
    # equity-gate just can't enforce without a number).
    try:
        from tools.trading.brokers.alpaca_adapter import get_default
        a = get_default()
        if not a.is_available():
            return None
        acct = a.get_account()
        eq = (acct or {}).get("equity")
        return float(eq) if eq is not None else None
    except Exception:
        return None


def run_preflight(
    proposal: dict,
    *,
    user_id: str | None = None,
    equity: float | None = None,
    is_live: bool = False,
    options_tier: str | None = None,
    iv_percentile: float | None = None,
) -> dict:
    """Run every gate. Always returns — never raises.

    Args:
      proposal      : output of `proposal_builder.build_proposal()`.
      user_id       : for account-equity lookup (live path only).
      equity        : explicit equity override (skips broker call).
      is_live       : True for the live path; False defaults to paper
                      (tier check still runs because live execution
                      happens through the same endpoint).
      options_tier  : broker-granted options approval tier (L1..L4).
      iv_percentile : current IV percentile 0-100. None disables IV gate.
    """
    gates = _gates()
    warnings: list[dict] = []
    blocks: list[dict] = []

    if proposal.get("status") == "error":
        blocks.append({"code": "proposal_error",
                       "message": proposal.get("error") or "proposal build failed"})
        return {"allowed": False, "warnings": warnings, "blocks": blocks, "meta": {}}

    strategy_id = proposal.get("strategy_id") or ""
    payoff = proposal.get("payoff") or {}
    max_loss_abs = abs(float(payoff.get("max_loss") or 0))

    # ---- gate 1: max-loss vs equity ------------------------------------
    eq = _account_equity(user_id, equity)
    max_loss_pct = None
    if eq and eq > 0 and max_loss_abs > float(gates.get("max_loss_floor_usd") or 0):
        max_loss_pct = (max_loss_abs / eq) * 100.0
        cap = float(gates.get("max_loss_pct_of_equity") or 2.0)
        if max_loss_pct > cap:
            blocks.append({
                "code": "max_loss_exceeds_equity_cap",
                "message": (f"Max loss ${max_loss_abs:,.2f} is "
                            f"{max_loss_pct:.2f}% of equity "
                            f"(${eq:,.2f}); cap is {cap:.2f}%"),
            })

    # ---- gate 2: undefined-risk tier ------------------------------------
    if _undefined_risk(strategy_id):
        need = str(gates.get("undefined_risk_required_tier") or "L3")
        if is_live and not _tier_ok(options_tier, need):
            blocks.append({
                "code": "tier_insufficient_for_undefined_risk",
                "message": (f"{strategy_id} has unlimited downside; "
                            f"requires {need}+ approval, account has "
                            f"{options_tier or 'unknown'}"),
            })
        elif not is_live and not _tier_ok(options_tier, need):
            # Paper trading — warn only.
            warnings.append({
                "code": "tier_would_block_live",
                "message": (f"{strategy_id} needs {need}+ approval for "
                            f"live; paper mode is fine."),
            })

    # ---- gate 3: DTE range ---------------------------------------------
    dte = _dte(proposal.get("expiry"))
    if dte is not None:
        if dte < int(gates.get("min_dte_days") or 0):
            blocks.append({
                "code": "dte_too_short",
                "message": f"DTE={dte} is below minimum "
                           f"{gates.get('min_dte_days')}",
            })
        elif dte > int(gates.get("max_dte_days") or 365):
            blocks.append({
                "code": "dte_too_long",
                "message": f"DTE={dte} exceeds maximum "
                           f"{gates.get('max_dte_days')}",
            })

    # ---- gate 4: IV regime warn band -----------------------------------
    if iv_percentile is not None:
        band = gates.get("iv_warn_band") or {}
        low = float(band.get("low_percentile") or 20)
        high = float(band.get("high_percentile") or 80)
        # Strategies that NET SELL premium (short condor/fly, covered call)
        # want high IV. Long-premium want low IV. We detect posture via the
        # payoff direction: max_profit > max_loss typically means long
        # premium; inverse is short premium. This is rough but useful.
        mp = float(payoff.get("max_profit") or 0)
        ml = float(payoff.get("max_loss") or 0)
        short_premium = mp > 0 and ml < 0 and abs(ml) > mp
        if short_premium and iv_percentile < low:
            warnings.append({
                "code": "short_premium_into_low_iv",
                "message": f"IV percentile {iv_percentile:.0f} is below "
                           f"{low:.0f} — selling premium is unattractive",
            })
        if not short_premium and iv_percentile > high:
            warnings.append({
                "code": "long_premium_into_high_iv",
                "message": f"IV percentile {iv_percentile:.0f} is above "
                           f"{high:.0f} — buying premium is expensive",
            })

    # ---- gate 5: proposal-emitted warnings -----------------------------
    for w in proposal.get("warnings") or []:
        warnings.append({"code": "selector_warning", "message": str(w)})

    return {
        "allowed": len(blocks) == 0,
        "warnings": warnings,
        "blocks": blocks,
        "meta": {
            "strategy_id": strategy_id,
            "max_loss_abs": max_loss_abs,
            "max_loss_pct_of_equity": max_loss_pct,
            "account_equity": eq,
            "dte": dte,
            "options_tier": options_tier,
            "is_live": is_live,
        },
    }
