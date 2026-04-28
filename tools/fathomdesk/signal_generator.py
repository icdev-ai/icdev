# CUI // SP-CTI
"""FathomDesk signal generator — threshold-gated signal filtering.

Loads args/signal_thresholds.yaml and filters candidate signals by
min_confidence and min_score before passing them downstream.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

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


def generate(
    signals: list[dict[str, Any]],
    thresholds: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return signals that pass confidence and score gates."""
    t = thresholds if thresholds is not None else load_thresholds()
    min_conf = float(t.get("min_confidence", _DEFAULTS["min_confidence"]))
    min_score = float(t.get("min_score", _DEFAULTS["min_score"]))
    max_n = int(t.get("max_signals", _DEFAULTS["max_signals"]))

    filtered = [
        s for s in signals
        if float(s.get("confidence", 0.0)) >= min_conf
        and float(s.get("score", 0.0)) >= min_score
    ]
    return filtered[:max_n]
