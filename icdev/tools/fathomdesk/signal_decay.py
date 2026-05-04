# CUI // SP-CTI
"""FathomDesk signal decay — Hippo exponential decay formula.

Applies w(t) = max(weight_floor, e^{-lambda * age_days}) to ad_signals.
Config is loaded from args/signal_decay_config.yaml; falls back to module
defaults if the file is missing or malformed.
"""
from __future__ import annotations

import math
import pathlib
from typing import Any

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_CONFIG_PATH = pathlib.Path(__file__).parents[2] / "args" / "signal_decay_config.yaml"

_DEFAULTS: dict[str, Any] = {
    "lambda": 0.05,
    "weight_floor": 0.01,
    "batch_cadence_hours": 24,
}


def load_config() -> dict[str, Any]:
    if _HAS_YAML and _CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            cfg = {**_DEFAULTS}
            if "lambda" in raw:
                cfg["lambda"] = float(raw["lambda"])
            if "weight_floor" in raw:
                cfg["weight_floor"] = float(raw["weight_floor"])
            if "batch_cadence_hours" in raw:
                cfg["batch_cadence_hours"] = int(raw["batch_cadence_hours"])
            return cfg
        except Exception:
            pass
    return dict(_DEFAULTS)


def decay_weight(age_days: float, cfg: dict[str, Any] | None = None) -> float:
    """Return the decay weight for a signal of the given age in days."""
    if cfg is None:
        cfg = load_config()
    lam: float = cfg["lambda"]
    floor: float = cfg["weight_floor"]
    return max(floor, math.exp(-lam * age_days))
