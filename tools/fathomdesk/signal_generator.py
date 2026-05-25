# CUI // SP-CTI
"""FathomDesk signal generator — threshold-gated signal filtering.

Loads args/signal_thresholds.yaml and filters candidate signals by
min_confidence and min_score before passing them downstream.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import math
from pathlib import Path
from typing import Any

import yaml

logger = get_logger(__name__)

_ARGS_PATH = Path(__file__).resolve().parents[2] / "args" / "signal_thresholds.yaml"

_DEFAULTS: dict[str, Any] = {
    "min_confidence": 0.60,
    "min_score": 0.50,
    "max_signals": 20,
    "long_bias": 0.55,
    "short_bias": 0.45,
    "sentiment": 0.6,
    "macro": 0.65,
    "technical": 0.55,
    "momentum_window": 20,
    "mean_reversion_window": 20,
}


def load_thresholds(path: Path | None = None) -> dict[str, Any]:
    """Load signal thresholds from YAML; fall back to defaults on any error."""
    target = Path(path) if path else _ARGS_PATH
    try:
        with target.open("r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        return {**_DEFAULTS, **cfg.get("thresholds", {})}
    except FileNotFoundError:
        logger.warning("signal_thresholds.yaml not found — using defaults")
        return dict(_DEFAULTS)
    except Exception as exc:
        logger.warning("signal_thresholds.yaml unreadable (%s) — using defaults", exc)
        return dict(_DEFAULTS)


# Loaded once at module initialization; callers may override per-call via generate(thresholds=…).
_THRESHOLDS: dict[str, Any] = load_thresholds()


def compute_momentum(prices: list[float], window: int = 20) -> float:
    """Return simple rate-of-change momentum over *window* periods."""
    if len(prices) < window:
        raise ValueError(f"Need at least {window} prices, got {len(prices)}")
    return prices[-1] / prices[-window] - 1


def compute_mean_reversion(prices: list[float], window: int = 20) -> float:
    """Return z-score of the last price vs the rolling mean/std over *window* periods."""
    if len(prices) < window:
        raise ValueError(f"Need at least {window} prices, got {len(prices)}")
    window_prices = prices[-window:]
    mean = sum(window_prices) / window
    variance = sum((p - mean) ** 2 for p in window_prices) / window
    std = math.sqrt(variance)
    if std == 0.0:
        return 0.0
    return (prices[-1] - mean) / std


def generate(
    signals: list[dict[str, Any]],
    thresholds: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return signals that pass confidence and score gates."""
    t = thresholds if thresholds is not None else _THRESHOLDS
    min_conf = float(t.get("min_confidence", _THRESHOLDS["min_confidence"]))
    min_score = float(t.get("min_score", _THRESHOLDS["min_score"]))
    max_n = int(t.get("max_signals", _THRESHOLDS["max_signals"]))

    filtered = [
        s for s in signals
        if float(s.get("confidence", 0.0)) >= min_conf
        and float(s.get("score", 0.0)) >= min_score
    ]
    return filtered[:max_n]
