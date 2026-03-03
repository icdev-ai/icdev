#!/usr/bin/env python3
"""SignalForge Label Generator — create training labels from OHLCV data.

CUI // SP-CTI

Labels each bar as LONG (1), SHORT (-1), or FLAT (0) based on whether
a hypothetical trade would have hit take-profit or stop-loss first.

Usage:
    python tools/trading/label_generator.py --data data/processed/features.csv --json
    python tools/trading/label_generator.py --data data/raw/ES_1min.csv --output data/processed/labeled.csv
"""
from __future__ import annotations

import argparse
import json
import sys
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
        "model": {"lookahead_bars": 20, "min_move_threshold": 0.001, "flat_zone_pct": 0.0005},
        "risk": {"take_profit_pct": 0.015, "stop_loss_pct": 0.010},
    }


def generate_labels_tpsl(df: pd.DataFrame, config: dict | None = None) -> pd.Series:
    """Label bars based on TP/SL simulation.

    For each bar, simulate entering a LONG and SHORT trade:
    - Look ahead N bars
    - Check if TP or SL would be hit first for each direction
    - If LONG TP hit first: label = 1 (LONG)
    - If SHORT TP hit first: label = -1 (SHORT)
    - If neither or both: label = 0 (FLAT)

    Args:
        df: DataFrame with open, high, low, close columns
        config: Optional config override

    Returns:
        Series of labels: 1 (LONG), -1 (SHORT), 0 (FLAT)
    """
    if config is None:
        config = _load_config()

    lookahead = config.get("model", {}).get("lookahead_bars", 20)
    tp_pct = config.get("risk", {}).get("take_profit_pct", 0.015)
    sl_pct = config.get("risk", {}).get("stop_loss_pct", 0.010)

    labels = np.zeros(len(df), dtype=int)
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values

    for i in range(len(df) - lookahead):
        entry = close[i]
        long_tp = entry * (1 + tp_pct)
        long_sl = entry * (1 - sl_pct)
        short_tp = entry * (1 - tp_pct)
        short_sl = entry * (1 + sl_pct)

        long_tp_bar = -1
        long_sl_bar = -1
        short_tp_bar = -1
        short_sl_bar = -1

        for j in range(1, lookahead + 1):
            idx = i + j
            if idx >= len(df):
                break

            if long_tp_bar == -1 and high[idx] >= long_tp:
                long_tp_bar = j
            if long_sl_bar == -1 and low[idx] <= long_sl:
                long_sl_bar = j
            if short_tp_bar == -1 and low[idx] <= short_tp:
                short_tp_bar = j
            if short_sl_bar == -1 and high[idx] >= short_sl:
                short_sl_bar = j

        # LONG wins if TP hit before SL (or TP hit and SL never hit)
        long_wins = (long_tp_bar > 0 and
                     (long_sl_bar == -1 or long_tp_bar <= long_sl_bar))
        # SHORT wins if TP hit before SL
        short_wins = (short_tp_bar > 0 and
                      (short_sl_bar == -1 or short_tp_bar <= short_sl_bar))

        if long_wins and not short_wins:
            labels[i] = 1
        elif short_wins and not long_wins:
            labels[i] = -1
        else:
            labels[i] = 0  # FLAT: ambiguous or both hit

    return pd.Series(labels, index=df.index, name="label")


def generate_labels_return(df: pd.DataFrame, config: dict | None = None) -> pd.Series:
    """Simple return-based labeling (alternative method).

    Labels based on whether future N-bar return exceeds threshold.
    """
    if config is None:
        config = _load_config()

    lookahead = config.get("model", {}).get("lookahead_bars", 20)
    threshold = config.get("model", {}).get("min_move_threshold", 0.001)

    future_return = df["close"].pct_change(periods=lookahead).shift(-lookahead)

    labels = pd.Series(0, index=df.index, dtype=int, name="label")
    labels[future_return > threshold] = 1
    labels[future_return < -threshold] = -1

    return labels


def main():
    parser = argparse.ArgumentParser(description="SignalForge Label Generator")
    parser.add_argument("--data", required=True, help="Path to OHLCV or features CSV")
    parser.add_argument("--output", help="Path to save labeled CSV")
    parser.add_argument("--method", choices=["tpsl", "return"], default="tpsl",
                        help="Labeling method (default: tpsl)")
    parser.add_argument("--json", action="store_true", help="Output summary as JSON")
    args = parser.parse_args()

    df = pd.read_csv(args.data)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    config = _load_config()

    if args.method == "tpsl":
        labels = generate_labels_tpsl(df, config)
    else:
        labels = generate_labels_return(df, config)

    df["label"] = labels

    if args.output:
        df.to_csv(args.output, index=False)

    if args.json:
        label_counts = labels.value_counts().to_dict()
        total = len(labels)
        result = {
            "status": "success",
            "method": args.method,
            "total_bars": total,
            "label_distribution": {
                "LONG": int(label_counts.get(1, 0)),
                "SHORT": int(label_counts.get(-1, 0)),
                "FLAT": int(label_counts.get(0, 0)),
            },
            "label_pct": {
                "LONG": round(label_counts.get(1, 0) / max(total, 1) * 100, 1),
                "SHORT": round(label_counts.get(-1, 0) / max(total, 1) * 100, 1),
                "FLAT": round(label_counts.get(0, 0) / max(total, 1) * 100, 1),
            },
            "config": {
                "lookahead_bars": config.get("model", {}).get("lookahead_bars", 20),
                "take_profit_pct": config.get("risk", {}).get("take_profit_pct", 0.015),
                "stop_loss_pct": config.get("risk", {}).get("stop_loss_pct", 0.010),
            },
        }
        if args.output:
            result["output_path"] = args.output
        print(json.dumps(result, indent=2))
    else:
        label_counts = labels.value_counts()
        print(f"Labels generated ({args.method}):")
        print(f"  LONG:  {label_counts.get(1, 0)}")
        print(f"  SHORT: {label_counts.get(-1, 0)}")
        print(f"  FLAT:  {label_counts.get(0, 0)}")


if __name__ == "__main__":
    main()
