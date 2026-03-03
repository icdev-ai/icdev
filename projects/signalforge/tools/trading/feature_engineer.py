#!/usr/bin/env python3
"""SignalForge Feature Engineering — 25 deterministic technical features.

CUI // SP-CTI

Computes features from 1-minute OHLCV data for XGBoost model training
and live signal generation. All features are deterministic and reproducible.

Usage:
    python tools/trading/feature_engineer.py --data data/raw/ES_1min.csv --json
    python tools/trading/feature_engineer.py --data data/raw/ES_1min.csv --output data/processed/features.csv
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def _load_config() -> dict:
    """Load trading config."""
    config_path = Path(__file__).parent.parent.parent / "args" / "trading_config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                return yaml.safe_load(f)
        except ImportError:
            pass
    return {
        "features": {
            "sma_periods": [20, 50, 200],
            "rsi_period": 14,
            "macd": [12, 26, 9],
            "bb_period": 20,
            "bb_std": 2,
            "atr_period": 14,
            "adx_period": 14,
            "stoch": [14, 3, 3],
            "volume_sma_period": 20,
            "support_resistance_lookback": 50,
        }
    }


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series, fast: int, slow: int, signal: int) -> tuple:
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    macd_signal = _ema(macd_line, signal)
    macd_hist = macd_line - macd_signal
    return macd_line, macd_signal, macd_hist


def _bollinger_bands(close: pd.Series, period: int, std: int) -> tuple:
    sma = _sma(close, period)
    rolling_std = close.rolling(window=period, min_periods=period).std()
    upper = sma + std * rolling_std
    lower = sma - std * rolling_std
    return upper, lower


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    mask = plus_dm < minus_dm
    plus_dm[mask] = 0
    mask2 = minus_dm < plus_dm
    minus_dm[mask2] = 0

    atr_val = _atr(high, low, close, period)
    plus_di = 100 * _ema(plus_dm, period) / atr_val.replace(0, np.nan)
    minus_di = 100 * _ema(minus_dm, period) / atr_val.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return _ema(dx, period)


def _stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                k_period: int, d_period: int, smooth: int) -> tuple:
    lowest_low = low.rolling(window=k_period, min_periods=k_period).min()
    highest_high = high.rolling(window=k_period, min_periods=k_period).max()
    denom = (highest_high - lowest_low).replace(0, np.nan)
    raw_k = 100 * (close - lowest_low) / denom
    stoch_k = raw_k.rolling(window=smooth, min_periods=1).mean()
    stoch_d = stoch_k.rolling(window=d_period, min_periods=1).mean()
    return stoch_k, stoch_d


def _vwap(high: pd.Series, low: pd.Series, close: pd.Series,
          volume: pd.Series) -> pd.Series:
    typical = (high + low + close) / 3
    cum_tp_vol = (typical * volume).cumsum()
    cum_vol = volume.cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


def _support_resistance(close: pd.Series, lookback: int) -> tuple:
    support = close.rolling(window=lookback, min_periods=lookback).min()
    resistance = close.rolling(window=lookback, min_periods=lookback).max()
    dist_support = (close - support) / close.replace(0, np.nan)
    dist_resistance = (resistance - close) / close.replace(0, np.nan)
    return dist_support, dist_resistance


def _regime(close: pd.Series, sma_short: int = 20, sma_long: int = 50) -> pd.Series:
    sma_s = _sma(close, sma_short)
    sma_l = _sma(close, sma_long)
    regime = pd.Series(0, index=close.index, dtype=int)
    regime[sma_s > sma_l] = 1   # trending up
    regime[sma_s < sma_l] = -1  # trending down
    return regime


def compute_features(df: pd.DataFrame, config: dict | None = None) -> pd.DataFrame:
    """Compute 25 features from OHLCV dataframe.

    Args:
        df: DataFrame with columns: timestamp, open, high, low, close, volume
        config: Optional feature config override

    Returns:
        DataFrame with 25 feature columns + timestamp
    """
    if config is None:
        config = _load_config().get("features", {})

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    sma_periods = config.get("sma_periods", [20, 50, 200])
    rsi_period = config.get("rsi_period", 14)
    macd_params = config.get("macd", [12, 26, 9])
    bb_period = config.get("bb_period", 20)
    bb_std = config.get("bb_std", 2)
    atr_period = config.get("atr_period", 14)
    adx_period = config.get("adx_period", 14)
    stoch_params = config.get("stoch", [14, 3, 3])
    vol_sma_period = config.get("volume_sma_period", 20)
    sr_lookback = config.get("support_resistance_lookback", 50)

    features = pd.DataFrame(index=df.index)

    # 1-4: Price features (normalized)
    features["close"] = close
    features["high"] = high
    features["low"] = low
    features["volume"] = volume

    # 5-7: Moving averages (distance from close, normalized)
    for period in sma_periods:
        sma_val = _sma(close, period)
        features[f"sma_{period}"] = (close - sma_val) / close.replace(0, np.nan)

    # 8: RSI
    features["rsi_14"] = _rsi(close, rsi_period) / 100.0  # normalize to 0-1

    # 9-11: MACD
    macd_line, macd_signal, macd_hist = _macd(close, *macd_params)
    features["macd_line"] = macd_line / close.replace(0, np.nan)
    features["macd_signal"] = macd_signal / close.replace(0, np.nan)
    features["macd_hist"] = macd_hist / close.replace(0, np.nan)

    # 12-13: Bollinger Band distance (normalized)
    bb_upper, bb_lower = _bollinger_bands(close, bb_period, bb_std)
    features["bb_upper_dist"] = (bb_upper - close) / close.replace(0, np.nan)
    features["bb_lower_dist"] = (close - bb_lower) / close.replace(0, np.nan)

    # 14: ATR (normalized by close)
    atr_val = _atr(high, low, close, atr_period)
    features["atr_14"] = atr_val / close.replace(0, np.nan)

    # 15: ADX (normalized to 0-1)
    features["adx_14"] = _adx(high, low, close, adx_period) / 100.0

    # 16-17: Stochastic (normalized to 0-1)
    stoch_k, stoch_d = _stochastic(high, low, close, *stoch_params)
    features["stoch_k"] = stoch_k / 100.0
    features["stoch_d"] = stoch_d / 100.0

    # 18: Volume relative to SMA
    vol_sma = _sma(volume.astype(float), vol_sma_period)
    features["volume_sma_ratio"] = volume / vol_sma.replace(0, np.nan)

    # 19-20: Distance to support/resistance
    dist_support, dist_resistance = _support_resistance(close, sr_lookback)
    features["dist_to_support"] = dist_support
    features["dist_to_resistance"] = dist_resistance

    # 21: Market regime
    features["regime"] = _regime(close)

    # 22: Hour of day (RTH normalized 0-1)
    if "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"])
        minutes_since_open = (ts.dt.hour * 60 + ts.dt.minute) - (9 * 60 + 30)
        rth_minutes = 6.5 * 60  # 9:30 to 16:00
        features["hour_of_day"] = minutes_since_open.clip(0, rth_minutes) / rth_minutes
    else:
        features["hour_of_day"] = 0.5

    # 23: Day of week (normalized 0-1)
    if "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"])
        features["day_of_week"] = ts.dt.dayofweek / 4.0  # Mon=0, Fri=4
    else:
        features["day_of_week"] = 0.5

    # 24: Close vs VWAP
    vwap = _vwap(high, low, close, volume)
    features["close_vs_vwap"] = (close - vwap) / close.replace(0, np.nan)

    # 25: Bar range vs ATR
    bar_range = high - low
    features["bar_range_vs_atr"] = bar_range / atr_val.replace(0, np.nan)

    # Keep timestamp for reference
    if "timestamp" in df.columns:
        features["timestamp"] = df["timestamp"]

    return features


def load_ohlcv(filepath: str | Path) -> pd.DataFrame:
    """Load OHLCV data from CSV file.

    Supports common NT8 export formats:
    - timestamp,open,high,low,close,volume
    - Date,Time,Open,High,Low,Close,Volume
    """
    df = pd.read_csv(filepath)

    col_map = {}
    for col in df.columns:
        cl = col.lower().strip()
        if cl in ("timestamp", "date", "datetime", "time"):
            col_map[col] = "timestamp"
        elif cl == "open":
            col_map[col] = "open"
        elif cl == "high":
            col_map[col] = "high"
        elif cl == "low":
            col_map[col] = "low"
        elif cl in ("close", "last"):
            col_map[col] = "close"
        elif cl in ("volume", "vol"):
            col_map[col] = "volume"

    df = df.rename(columns=col_map)

    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}. Found: {list(df.columns)}")

    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])

    df = df.dropna(subset=required)
    return df


def filter_rth(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to Regular Trading Hours (09:30-16:00 ET)."""
    if "timestamp" not in df.columns:
        return df

    ts = pd.to_datetime(df["timestamp"])
    hour_min = ts.dt.hour * 100 + ts.dt.minute
    mask = (hour_min >= 930) & (hour_min < 1600)
    return df[mask].reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(description="SignalForge Feature Engineer")
    parser.add_argument("--data", required=True, help="Path to OHLCV CSV file")
    parser.add_argument("--output", help="Path to save features CSV")
    parser.add_argument("--rth-only", action="store_true", default=True,
                        help="Filter to RTH only (default: true)")
    parser.add_argument("--json", action="store_true", help="Output summary as JSON")
    args = parser.parse_args()

    df = load_ohlcv(args.data)
    if args.rth_only:
        df = filter_rth(df)

    features = compute_features(df)
    features = features.dropna()

    if args.output:
        features.to_csv(args.output, index=False)

    if args.json:
        result = {
            "status": "success",
            "input_rows": len(df),
            "output_rows": len(features),
            "features": [c for c in features.columns if c != "timestamp"],
            "feature_count": len([c for c in features.columns if c != "timestamp"]),
            "date_range": {
                "start": str(features["timestamp"].iloc[0]) if "timestamp" in features.columns else None,
                "end": str(features["timestamp"].iloc[-1]) if "timestamp" in features.columns else None,
            },
        }
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Features computed: {len(features)} rows, "
              f"{len([c for c in features.columns if c != 'timestamp'])} features")
        if args.output:
            print(f"Saved to: {args.output}")


if __name__ == "__main__":
    main()
