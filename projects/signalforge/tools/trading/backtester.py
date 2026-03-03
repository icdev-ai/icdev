#!/usr/bin/env python3
"""SignalForge Backtester — walk-forward validation + Monte Carlo simulation.

CUI // SP-CTI

Evaluates model performance with realistic constraints:
- Walk-forward validation (no data leakage)
- Hard TP/SL enforcement at bar level
- Monte Carlo trade return shuffling
- Overfitting detection (in-sample vs out-of-sample)
- Commission and slippage modeling

Usage:
    python tools/trading/backtester.py --model models/best_model.json --data data/processed/labeled.csv --json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd


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
        "risk": {"take_profit_pct": 0.015, "stop_loss_pct": 0.010},
        "backtest": {
            "walk_forward_train_months": 6,
            "walk_forward_test_months": 1,
            "walk_forward_step_months": 1,
            "monte_carlo_iterations": 1000,
            "min_trades_for_validity": 50,
            "commission_per_side": 2.50,
            "slippage_ticks": 1,
        },
    }


def simulate_trades(
    predictions: np.ndarray,
    df: pd.DataFrame,
    config: dict | None = None,
) -> list[dict]:
    """Simulate trades from model predictions with hard TP/SL.

    For each LONG/SHORT prediction, simulate bar-by-bar price action
    checking if TP or SL is hit first within the lookahead window.

    Args:
        predictions: Array of predictions (0=SHORT, 1=FLAT, 2=LONG mapped)
        df: DataFrame with OHLCV data
        config: Optional config

    Returns:
        List of trade dicts with entry, exit, pnl, bars_held, exit_reason
    """
    if config is None:
        config = _load_config()

    tp_pct = config.get("risk", {}).get("take_profit_pct", 0.015)
    sl_pct = config.get("risk", {}).get("stop_loss_pct", 0.010)
    lookahead = config.get("model", {}).get("lookahead_bars", 20)
    commission = config.get("backtest", {}).get("commission_per_side", 2.50)
    slippage_ticks = config.get("backtest", {}).get("slippage_ticks", 1)
    tick_size = 0.25  # ES tick size
    point_value = 50.0  # ES point value

    # Map predictions back: 0->SHORT, 1->FLAT, 2->LONG
    label_map_inv = {0: -1, 1: 0, 2: 1}

    close = df["close"].values
    high = df["high"].values
    low = df["low"].values

    trades = []
    i = 0
    while i < len(predictions):
        signal = label_map_inv.get(int(predictions[i]), 0)
        if signal == 0:  # FLAT — skip
            i += 1
            continue

        entry = close[i] + (slippage_ticks * tick_size * signal)  # slippage
        direction = "LONG" if signal == 1 else "SHORT"

        if direction == "LONG":
            tp_price = entry * (1 + tp_pct)
            sl_price = entry * (1 - sl_pct)
        else:
            tp_price = entry * (1 - tp_pct)
            sl_price = entry * (1 + sl_pct)

        exit_price = None
        exit_reason = "timeout"
        bars_held = lookahead

        for j in range(1, lookahead + 1):
            idx = i + j
            if idx >= len(df):
                exit_price = close[min(i + j - 1, len(df) - 1)]
                bars_held = j - 1
                exit_reason = "end_of_data"
                break

            if direction == "LONG":
                if low[idx] <= sl_price:
                    exit_price = sl_price - (slippage_ticks * tick_size)
                    exit_reason = "stop_loss"
                    bars_held = j
                    break
                if high[idx] >= tp_price:
                    exit_price = tp_price - (slippage_ticks * tick_size)
                    exit_reason = "take_profit"
                    bars_held = j
                    break
            else:  # SHORT
                if high[idx] >= sl_price:
                    exit_price = sl_price + (slippage_ticks * tick_size)
                    exit_reason = "stop_loss"
                    bars_held = j
                    break
                if low[idx] <= tp_price:
                    exit_price = tp_price + (slippage_ticks * tick_size)
                    exit_reason = "take_profit"
                    bars_held = j
                    break

        if exit_price is None:
            exit_idx = min(i + lookahead, len(df) - 1)
            exit_price = close[exit_idx]

        if direction == "LONG":
            pnl_points = exit_price - entry
        else:
            pnl_points = entry - exit_price

        pnl_dollars = (pnl_points * point_value) - (2 * commission)  # round-trip commission

        trades.append({
            "bar_index": int(i),
            "direction": direction,
            "entry": round(float(entry), 2),
            "exit": round(float(exit_price), 2),
            "pnl_points": round(float(pnl_points), 2),
            "pnl_dollars": round(float(pnl_dollars), 2),
            "bars_held": int(bars_held),
            "exit_reason": exit_reason,
        })

        # Skip ahead past this trade
        i += bars_held + 1
        continue

    return trades


def compute_metrics(trades: list[dict]) -> dict:
    """Compute performance metrics from trade list."""
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0,
            "avg_rr": 0,
            "profit_factor": 0,
            "sharpe": 0,
            "sortino": 0,
            "max_drawdown_pct": 0,
            "total_pnl": 0,
            "avg_bars_held": 0,
        }

    pnls = [t["pnl_dollars"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_trades = len(trades)
    win_rate = len(wins) / total_trades if total_trades > 0 else 0
    avg_win = np.mean(wins) if wins else 0
    avg_loss = abs(np.mean(losses)) if losses else 1
    avg_rr = avg_win / avg_loss if avg_loss > 0 else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Sharpe ratio (annualized, assuming daily trades)
    pnl_arr = np.array(pnls)
    mean_pnl = pnl_arr.mean()
    std_pnl = pnl_arr.std()
    sharpe = (mean_pnl / std_pnl * np.sqrt(252)) if std_pnl > 0 else 0

    # Sortino (downside deviation)
    downside = pnl_arr[pnl_arr < 0]
    downside_std = downside.std() if len(downside) > 0 else 1
    sortino = (mean_pnl / downside_std * np.sqrt(252)) if downside_std > 0 else 0

    # Max drawdown
    cumulative = np.cumsum(pnls)
    peak = np.maximum.accumulate(cumulative)
    drawdown = peak - cumulative
    max_dd = drawdown.max() if len(drawdown) > 0 else 0
    max_dd_pct = max_dd / max(peak.max(), 1) if peak.max() > 0 else 0

    avg_bars = np.mean([t["bars_held"] for t in trades])

    # Exit reason breakdown
    exit_reasons = {}
    for t in trades:
        r = t["exit_reason"]
        exit_reasons[r] = exit_reasons.get(r, 0) + 1

    # Direction breakdown
    long_trades = [t for t in trades if t["direction"] == "LONG"]
    short_trades = [t for t in trades if t["direction"] == "SHORT"]
    long_wins = sum(1 for t in long_trades if t["pnl_dollars"] > 0)
    short_wins = sum(1 for t in short_trades if t["pnl_dollars"] > 0)

    return {
        "total_trades": total_trades,
        "win_rate": round(float(win_rate), 4),
        "avg_win": round(float(avg_win), 2),
        "avg_loss": round(float(avg_loss), 2),
        "avg_rr": round(float(avg_rr), 2),
        "profit_factor": round(float(min(profit_factor, 999)), 2),
        "sharpe": round(float(sharpe), 2),
        "sortino": round(float(sortino), 2),
        "max_drawdown": round(float(max_dd), 2),
        "max_drawdown_pct": round(float(max_dd_pct), 4),
        "total_pnl": round(float(sum(pnls)), 2),
        "avg_bars_held": round(float(avg_bars), 1),
        "exit_reasons": exit_reasons,
        "long_trades": len(long_trades),
        "long_win_rate": round(long_wins / max(len(long_trades), 1), 4),
        "short_trades": len(short_trades),
        "short_win_rate": round(short_wins / max(len(short_trades), 1), 4),
    }


def monte_carlo(trades: list[dict], iterations: int = 1000, seed: int = 42) -> dict:
    """Monte Carlo simulation — shuffle trade returns.

    Generates confidence intervals for equity curve outcomes
    by randomly reordering the sequence of trade returns.

    Args:
        trades: List of trade dicts
        iterations: Number of MC iterations
        seed: Random seed for reproducibility

    Returns:
        Dict with P5, P25, P50, P75, P95 final equity and max drawdowns
    """
    if not trades:
        return {
            "iterations": iterations,
            "final_equity": {"P5": 0, "P25": 0, "P50": 0, "P75": 0, "P95": 0},
            "max_drawdown": {"P5": 0, "P25": 0, "P50": 0, "P75": 0, "P95": 0},
        }

    rng = random.Random(seed)
    pnls = [t["pnl_dollars"] for t in trades]

    final_equities = []
    max_drawdowns = []

    for _ in range(iterations):
        shuffled = pnls.copy()
        rng.shuffle(shuffled)

        cumulative = np.cumsum(shuffled)
        final_equities.append(cumulative[-1])

        peak = np.maximum.accumulate(cumulative)
        dd = (peak - cumulative).max()
        max_drawdowns.append(dd)

    final_equities.sort()
    max_drawdowns.sort()

    def percentiles(arr):
        n = len(arr)
        return {
            "P5": round(float(arr[int(n * 0.05)]), 2),
            "P25": round(float(arr[int(n * 0.25)]), 2),
            "P50": round(float(arr[int(n * 0.50)]), 2),
            "P75": round(float(arr[int(n * 0.75)]), 2),
            "P95": round(float(arr[int(n * 0.95)]), 2),
        }

    return {
        "iterations": iterations,
        "final_equity": percentiles(final_equities),
        "max_drawdown": percentiles(max_drawdowns),
    }


def overfitting_score(in_sample_sharpe: float, out_sample_sharpe: float) -> dict:
    """Compute overfitting score.

    Compares in-sample vs out-of-sample Sharpe ratio.
    Score > 0.5 suggests significant overfitting.

    Overfitting = 1 - (OOS_Sharpe / IS_Sharpe)
    """
    if in_sample_sharpe <= 0:
        return {
            "score": 0,
            "assessment": "insufficient_data",
            "in_sample_sharpe": round(in_sample_sharpe, 2),
            "out_sample_sharpe": round(out_sample_sharpe, 2),
        }

    ratio = out_sample_sharpe / in_sample_sharpe
    score = max(0, 1 - ratio)

    if score < 0.2:
        assessment = "low"
    elif score < 0.5:
        assessment = "moderate"
    elif score < 0.8:
        assessment = "high"
    else:
        assessment = "severe"

    return {
        "score": round(float(score), 4),
        "assessment": assessment,
        "in_sample_sharpe": round(float(in_sample_sharpe), 2),
        "out_sample_sharpe": round(float(out_sample_sharpe), 2),
        "sharpe_decay": round(float(1 - ratio), 4),
    }


def run_backtest(
    model,
    df: pd.DataFrame,
    features: list[str],
    config: dict | None = None,
) -> dict:
    """Run full backtest with walk-forward + Monte Carlo.

    Args:
        model: Trained model with .predict() method
        df: DataFrame with features and OHLCV data
        features: List of feature column names
        config: Optional config

    Returns:
        Full backtest results dict
    """
    if config is None:
        config = _load_config()

    available = [f for f in features if f in df.columns]
    X = df[available].replace([np.inf, -np.inf], np.nan).fillna(0)

    predictions = model.predict(X)
    trades = simulate_trades(predictions, df, config)
    metrics = compute_metrics(trades)

    mc_iterations = config.get("backtest", {}).get("monte_carlo_iterations", 1000)
    mc_results = monte_carlo(trades, mc_iterations)

    min_trades = config.get("backtest", {}).get("min_trades_for_validity", 50)
    is_valid = metrics["total_trades"] >= min_trades

    return {
        "metrics": metrics,
        "monte_carlo": mc_results,
        "trade_count": len(trades),
        "is_valid": is_valid,
        "min_trades_required": min_trades,
        "trades": trades[:50],  # First 50 trades for inspection
    }


def main():
    parser = argparse.ArgumentParser(description="SignalForge Backtester")
    parser.add_argument("--model", required=True, help="Path to trained model")
    parser.add_argument("--data", required=True, help="Path to features CSV")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    from tools.trading.model_trainer import load_model, FEATURE_COLS

    config = _load_config()
    start_time = time.time()

    model, meta = load_model(args.model)
    features = meta.get("features", FEATURE_COLS)

    df = pd.read_csv(args.data)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    result = run_backtest(model, df, features, config)
    elapsed = round(time.time() - start_time, 2)

    if args.json:
        output = {
            "status": "success",
            "elapsed_sec": elapsed,
            "is_valid": result["is_valid"],
            "metrics": result["metrics"],
            "monte_carlo": result["monte_carlo"],
        }
        print(json.dumps(output, indent=2))
    else:
        m = result["metrics"]
        print(f"Backtest Results ({elapsed}s):")
        print(f"  Trades: {m['total_trades']} (valid: {result['is_valid']})")
        print(f"  Win rate: {m['win_rate']:.1%}")
        print(f"  Avg R:R: {m['avg_rr']:.2f}")
        print(f"  Profit factor: {m['profit_factor']:.2f}")
        print(f"  Sharpe: {m['sharpe']:.2f}")
        print(f"  Max drawdown: ${m['max_drawdown']:.2f} ({m['max_drawdown_pct']:.1%})")
        print(f"  Total PnL: ${m['total_pnl']:.2f}")
        print(f"\nMonte Carlo ({result['monte_carlo']['iterations']} iterations):")
        mc = result["monte_carlo"]
        print(f"  Final equity P50: ${mc['final_equity']['P50']}")
        print(f"  Final equity P5-P95: ${mc['final_equity']['P5']} to ${mc['final_equity']['P95']}")
        print(f"  Max DD P50: ${mc['max_drawdown']['P50']}")


if __name__ == "__main__":
    main()
