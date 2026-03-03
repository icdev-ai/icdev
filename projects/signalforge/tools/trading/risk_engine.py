#!/usr/bin/env python3
"""SignalForge Risk Engine — deterministic risk management.

CUI // SP-CTI

All rule-based from args/trading_config.yaml. No learned position sizing.
Hard TP/SL enforcement, circuit breakers, ATR-based position sizing.

Usage:
    python tools/trading/risk_engine.py --check --json
    python tools/trading/risk_engine.py --size --entry 5200.50 --atr 12.5 --account 100000 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import numpy as np


def _load_config() -> dict:
    config_path = Path(__file__).parent.parent.parent / "args" / "trading_config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                return yaml.safe_load(f)
        except ImportError:
            pass
    return {
        "risk": {
            "take_profit_pct": 0.015,
            "stop_loss_pct": 0.010,
            "max_daily_loss_pct": 0.03,
            "max_positions": 1,
            "position_size_method": "atr",
            "risk_per_trade_pct": 0.01,
            "trailing_stop": {
                "enabled": True,
                "atr_multiplier": 2.0,
                "activate_pct": 0.005,
            },
            "end_of_day_flatten": True,
        },
    }


@dataclass
class RiskLimits:
    """Current risk limits computed from config."""
    take_profit_pct: float
    stop_loss_pct: float
    max_daily_loss_pct: float
    max_positions: int
    risk_per_trade_pct: float
    trailing_stop_enabled: bool
    trailing_stop_atr_mult: float
    trailing_activate_pct: float
    end_of_day_flatten: bool


@dataclass
class PositionSize:
    """Position sizing result."""
    contracts: int
    risk_per_contract: float
    total_risk: float
    account_risk_pct: float
    method: str


@dataclass
class TradeRisk:
    """Risk parameters for a specific trade."""
    direction: str  # LONG or SHORT
    entry_price: float
    take_profit: float
    stop_loss: float
    position_size: int
    max_loss: float
    max_gain: float
    risk_reward_ratio: float


def get_risk_limits(config: dict | None = None) -> RiskLimits:
    """Get current risk limits from config."""
    if config is None:
        config = _load_config()
    risk = config.get("risk", {})
    trailing = risk.get("trailing_stop", {})

    return RiskLimits(
        take_profit_pct=risk.get("take_profit_pct", 0.015),
        stop_loss_pct=risk.get("stop_loss_pct", 0.010),
        max_daily_loss_pct=risk.get("max_daily_loss_pct", 0.03),
        max_positions=risk.get("max_positions", 1),
        risk_per_trade_pct=risk.get("risk_per_trade_pct", 0.01),
        trailing_stop_enabled=trailing.get("enabled", True),
        trailing_stop_atr_mult=trailing.get("atr_multiplier", 2.0),
        trailing_activate_pct=trailing.get("activate_pct", 0.005),
        end_of_day_flatten=risk.get("end_of_day_flatten", True),
    )


def calculate_position_size(
    account_value: float,
    entry_price: float,
    atr: float,
    config: dict | None = None,
) -> PositionSize:
    """Calculate position size using ATR-based risk method.

    Risk per trade = account_value * risk_per_trade_pct
    Contracts = risk_per_trade / (atr * point_value)

    For ES futures: 1 point = $50 per contract.
    """
    if config is None:
        config = _load_config()

    limits = get_risk_limits(config)
    method = config.get("risk", {}).get("position_size_method", "atr")

    point_value = 50.0  # ES/MES: $50 per point per contract
    risk_budget = account_value * limits.risk_per_trade_pct

    if method == "atr":
        risk_per_contract = atr * point_value
        if risk_per_contract <= 0:
            contracts = 0
        else:
            contracts = max(1, int(risk_budget / risk_per_contract))
    elif method == "fixed":
        contracts = 1
        risk_per_contract = atr * point_value
    else:
        contracts = 1
        risk_per_contract = atr * point_value

    total_risk = contracts * risk_per_contract
    account_risk = total_risk / max(account_value, 1)

    return PositionSize(
        contracts=contracts,
        risk_per_contract=round(risk_per_contract, 2),
        total_risk=round(total_risk, 2),
        account_risk_pct=round(account_risk, 4),
        method=method,
    )


def calculate_trade_risk(
    direction: str,
    entry_price: float,
    position_size: int = 1,
    config: dict | None = None,
) -> TradeRisk:
    """Calculate TP/SL levels and risk metrics for a trade.

    Args:
        direction: "LONG" or "SHORT"
        entry_price: Entry price
        position_size: Number of contracts
        config: Optional config override

    Returns:
        TradeRisk with computed levels
    """
    if config is None:
        config = _load_config()

    limits = get_risk_limits(config)
    point_value = 50.0

    if direction == "LONG":
        take_profit = entry_price * (1 + limits.take_profit_pct)
        stop_loss = entry_price * (1 - limits.stop_loss_pct)
        max_gain = (take_profit - entry_price) * point_value * position_size
        max_loss = (entry_price - stop_loss) * point_value * position_size
    elif direction == "SHORT":
        take_profit = entry_price * (1 - limits.take_profit_pct)
        stop_loss = entry_price * (1 + limits.stop_loss_pct)
        max_gain = (entry_price - take_profit) * point_value * position_size
        max_loss = (stop_loss - entry_price) * point_value * position_size
    else:
        raise ValueError(f"Invalid direction: {direction}. Must be LONG or SHORT.")

    rr = max_gain / max_loss if max_loss > 0 else 0

    return TradeRisk(
        direction=direction,
        entry_price=round(entry_price, 2),
        take_profit=round(take_profit, 2),
        stop_loss=round(stop_loss, 2),
        position_size=position_size,
        max_loss=round(max_loss, 2),
        max_gain=round(max_gain, 2),
        risk_reward_ratio=round(rr, 2),
    )


def check_circuit_breaker(
    daily_pnl: float,
    account_value: float,
    config: dict | None = None,
) -> dict:
    """Check if daily loss circuit breaker is triggered.

    Args:
        daily_pnl: Today's realized + unrealized PnL
        account_value: Account value at start of day
        config: Optional config override

    Returns:
        Dict with breaker status
    """
    if config is None:
        config = _load_config()

    limits = get_risk_limits(config)
    max_loss = account_value * limits.max_daily_loss_pct
    loss_pct = abs(min(daily_pnl, 0)) / max(account_value, 1)

    triggered = daily_pnl < 0 and abs(daily_pnl) >= max_loss

    return {
        "triggered": triggered,
        "daily_pnl": round(daily_pnl, 2),
        "max_daily_loss": round(max_loss, 2),
        "loss_pct": round(loss_pct, 4),
        "threshold_pct": limits.max_daily_loss_pct,
        "remaining_budget": round(max_loss - abs(min(daily_pnl, 0)), 2),
    }


def update_trailing_stop(
    direction: str,
    entry_price: float,
    current_price: float,
    current_stop: float,
    atr: float,
    config: dict | None = None,
) -> float:
    """Update trailing stop if conditions are met.

    Trailing stop activates once profit exceeds activate_pct,
    then trails at ATR * multiplier from the best price.

    Returns:
        Updated stop loss price (may be same as current_stop).
    """
    if config is None:
        config = _load_config()

    limits = get_risk_limits(config)
    if not limits.trailing_stop_enabled:
        return current_stop

    trail_distance = atr * limits.trailing_stop_atr_mult

    if direction == "LONG":
        profit_pct = (current_price - entry_price) / entry_price
        if profit_pct >= limits.trailing_activate_pct:
            new_stop = current_price - trail_distance
            return max(new_stop, current_stop)
    elif direction == "SHORT":
        profit_pct = (entry_price - current_price) / entry_price
        if profit_pct >= limits.trailing_activate_pct:
            new_stop = current_price + trail_distance
            return min(new_stop, current_stop)

    return current_stop


def check_eod_flatten(hour: int, minute: int, config: dict | None = None) -> bool:
    """Check if end-of-day flattening should trigger.

    Default: flatten at 15:55 ET (5 minutes before close).
    """
    if config is None:
        config = _load_config()

    limits = get_risk_limits(config)
    if not limits.end_of_day_flatten:
        return False

    # 15:55 ET = 5 minutes before 16:00 close
    return hour >= 15 and minute >= 55


def validate_trade(
    direction: str,
    current_positions: int,
    daily_pnl: float,
    account_value: float,
    hour: int = 12,
    minute: int = 0,
    config: dict | None = None,
) -> dict:
    """Validate whether a new trade is allowed.

    Checks:
    1. Max position limit
    2. Circuit breaker
    3. EOD flatten window
    4. Direction validity

    Returns:
        Dict with allowed status and reason
    """
    if config is None:
        config = _load_config()

    limits = get_risk_limits(config)

    if direction not in ("LONG", "SHORT"):
        return {"allowed": False, "reason": f"Invalid direction: {direction}"}

    if current_positions >= limits.max_positions:
        return {"allowed": False, "reason": f"Max positions ({limits.max_positions}) reached"}

    breaker = check_circuit_breaker(daily_pnl, account_value, config)
    if breaker["triggered"]:
        return {"allowed": False, "reason": f"Circuit breaker triggered (loss: ${abs(daily_pnl):.2f})"}

    if check_eod_flatten(hour, minute, config):
        return {"allowed": False, "reason": "EOD flatten window — no new trades"}

    return {"allowed": True, "reason": "All risk checks passed"}


def main():
    parser = argparse.ArgumentParser(description="SignalForge Risk Engine")
    parser.add_argument("--check", action="store_true", help="Show current risk limits")
    parser.add_argument("--size", action="store_true", help="Calculate position size")
    parser.add_argument("--entry", type=float, help="Entry price")
    parser.add_argument("--atr", type=float, help="Current ATR value")
    parser.add_argument("--account", type=float, default=100000, help="Account value")
    parser.add_argument("--direction", choices=["LONG", "SHORT"], help="Trade direction")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    config = _load_config()

    if args.check:
        limits = get_risk_limits(config)
        if args.json:
            print(json.dumps({"status": "success", "limits": asdict(limits)}, indent=2))
        else:
            print("Risk Limits:")
            for k, v in asdict(limits).items():
                print(f"  {k}: {v}")

    elif args.size and args.entry and args.atr:
        size = calculate_position_size(args.account, args.entry, args.atr, config)
        trade_risk = None
        if args.direction:
            trade_risk = calculate_trade_risk(
                args.direction, args.entry, size.contracts, config,
            )

        if args.json:
            result = {"status": "success", "position_size": asdict(size)}
            if trade_risk:
                result["trade_risk"] = asdict(trade_risk)
            print(json.dumps(result, indent=2))
        else:
            print(f"Position Size ({size.method}):")
            print(f"  Contracts: {size.contracts}")
            print(f"  Risk/contract: ${size.risk_per_contract}")
            print(f"  Total risk: ${size.total_risk}")
            print(f"  Account risk: {size.account_risk_pct:.2%}")
            if trade_risk:
                print(f"\nTrade Risk ({trade_risk.direction}):")
                print(f"  Entry: {trade_risk.entry_price}")
                print(f"  TP: {trade_risk.take_profit}")
                print(f"  SL: {trade_risk.stop_loss}")
                print(f"  R:R: {trade_risk.risk_reward_ratio}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
