#!/usr/bin/env python3
"""SignalForge Signal Generator — main prediction loop.

CUI // SP-CTI

Loads trained model, computes features on new bar data, predicts
LONG/SHORT/FLAT signal with confidence, applies risk checks,
and logs to trade journal.

Usage:
    python tools/trading/signal_generator.py --model models/best_model.json --bar data/raw/latest_bar.csv --json
    python tools/trading/signal_generator.py --model models/best_model.json --live
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
        "risk": {
            "take_profit_pct": 0.015,
            "stop_loss_pct": 0.010,
            "max_daily_loss_pct": 0.03,
            "max_positions": 1,
            "risk_per_trade_pct": 0.01,
        },
    }


LABEL_MAP_INV = {0: "SHORT", 1: "FLAT", 2: "LONG"}
LABEL_VALUES = {0: -1, 1: 0, 2: 1}


def generate_signal(
    model,
    features: pd.DataFrame,
    feature_cols: list[str],
    config: dict | None = None,
) -> dict:
    """Generate a trading signal from features.

    Args:
        model: Trained model with predict/predict_proba methods
        features: DataFrame with feature columns (single row or multiple)
        feature_cols: List of feature column names model expects
        config: Optional config

    Returns:
        Dict with signal, confidence, TP/SL levels
    """
    if config is None:
        config = _load_config()

    available = [c for c in feature_cols if c in features.columns]
    X = features[available].iloc[[-1]].replace([np.inf, -np.inf], np.nan).fillna(0)

    prediction = model.predict(X)[0]
    signal_name = LABEL_MAP_INV.get(int(prediction), "FLAT")
    signal_value = LABEL_VALUES.get(int(prediction), 0)

    # Get prediction probabilities if available
    confidence = 0.0
    class_probs = {}
    try:
        proba = model.predict_proba(X)[0]
        confidence = float(proba[int(prediction)])
        for idx, p in enumerate(proba):
            class_probs[LABEL_MAP_INV.get(idx, str(idx))] = round(float(p), 4)
    except (AttributeError, Exception):
        confidence = 1.0  # No probability available

    # Compute TP/SL levels
    entry_price = float(features["close"].iloc[-1]) if "close" in features.columns else 0
    tp_pct = config.get("risk", {}).get("take_profit_pct", 0.015)
    sl_pct = config.get("risk", {}).get("stop_loss_pct", 0.010)

    if signal_name == "LONG":
        take_profit = entry_price * (1 + tp_pct)
        stop_loss = entry_price * (1 - sl_pct)
    elif signal_name == "SHORT":
        take_profit = entry_price * (1 - tp_pct)
        stop_loss = entry_price * (1 + sl_pct)
    else:
        take_profit = entry_price
        stop_loss = entry_price

    return {
        "signal": signal_name,
        "signal_value": signal_value,
        "confidence": round(confidence, 4),
        "class_probabilities": class_probs,
        "entry_price": round(entry_price, 2),
        "take_profit": round(take_profit, 2),
        "stop_loss": round(stop_loss, 2),
    }


def process_bar(
    model,
    bar_data: pd.DataFrame,
    feature_cols: list[str],
    account_value: float = 100000,
    current_positions: int = 0,
    daily_pnl: float = 0,
    config: dict | None = None,
) -> dict:
    """Process a new bar and generate signal with risk validation.

    Full pipeline: features → predict → risk check → signal output.

    Args:
        model: Trained model
        bar_data: DataFrame with enough history for feature computation
        feature_cols: Feature column names
        account_value: Current account value
        current_positions: Number of open positions
        daily_pnl: Today's PnL so far
        config: Optional config

    Returns:
        Dict with signal, risk validation, position sizing
    """
    if config is None:
        config = _load_config()

    from tools.trading.feature_engineer import compute_features
    from tools.trading.risk_engine import (
        validate_trade, calculate_position_size, calculate_trade_risk,
    )

    # Compute features
    features = compute_features(bar_data, config.get("features"))

    # Ensure we have enough data (drop NaN warmup rows)
    features_clean = features.dropna()
    if len(features_clean) == 0:
        return {
            "signal": "FLAT",
            "reason": "insufficient_data",
            "confidence": 0,
        }

    # Generate signal
    signal = generate_signal(model, features_clean, feature_cols, config)

    if signal["signal"] == "FLAT":
        return {
            "signal": "FLAT",
            "confidence": signal["confidence"],
            "class_probabilities": signal.get("class_probabilities", {}),
            "action": "no_trade",
        }

    # Get current time from bar data
    hour, minute = 12, 0
    if "timestamp" in bar_data.columns:
        ts = pd.to_datetime(bar_data["timestamp"].iloc[-1])
        hour, minute = ts.hour, ts.minute

    # Risk validation
    risk_check = validate_trade(
        signal["signal"], current_positions, daily_pnl,
        account_value, hour, minute, config,
    )

    if not risk_check["allowed"]:
        return {
            "signal": signal["signal"],
            "confidence": signal["confidence"],
            "action": "blocked",
            "reason": risk_check["reason"],
        }

    # Position sizing
    atr = float(features_clean["atr_14"].iloc[-1]) if "atr_14" in features_clean.columns else 10.0
    # Denormalize ATR (it was normalized by close)
    close_price = signal["entry_price"]
    atr_actual = atr * close_price

    pos_size = calculate_position_size(account_value, close_price, atr_actual, config)
    trade_risk = calculate_trade_risk(signal["signal"], close_price, pos_size.contracts, config)

    return {
        "signal": signal["signal"],
        "confidence": signal["confidence"],
        "class_probabilities": signal.get("class_probabilities", {}),
        "action": "trade",
        "entry_price": signal["entry_price"],
        "take_profit": signal["take_profit"],
        "stop_loss": signal["stop_loss"],
        "position_size": pos_size.contracts,
        "risk_per_contract": pos_size.risk_per_contract,
        "total_risk": pos_size.total_risk,
        "risk_reward_ratio": trade_risk.risk_reward_ratio,
        "max_gain": trade_risk.max_gain,
        "max_loss": trade_risk.max_loss,
    }


def main():
    parser = argparse.ArgumentParser(description="SignalForge Signal Generator")
    parser.add_argument("--model", required=True, help="Path to trained model")
    parser.add_argument("--bar", help="Path to bar data CSV (for single prediction)")
    parser.add_argument("--account", type=float, default=100000, help="Account value")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    from tools.trading.model_trainer import load_model, FEATURE_COLS

    model, meta = load_model(args.model)
    feature_cols = meta.get("features", FEATURE_COLS)
    config = _load_config()

    if args.bar:
        df = pd.read_csv(args.bar)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        result = process_bar(
            model, df, feature_cols,
            account_value=args.account,
            config=config,
        )

        if args.json:
            print(json.dumps({"status": "success", **result}, indent=2))
        else:
            print(f"Signal: {result['signal']}")
            print(f"  Confidence: {result.get('confidence', 0):.1%}")
            print(f"  Action: {result.get('action', 'none')}")
            if result.get("action") == "trade":
                print(f"  Entry: {result['entry_price']}")
                print(f"  TP: {result['take_profit']}")
                print(f"  SL: {result['stop_loss']}")
                print(f"  Size: {result['position_size']} contracts")
                print(f"  R:R: {result['risk_reward_ratio']}")
            elif result.get("reason"):
                print(f"  Reason: {result['reason']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
