# CUI // SP-CTI
"""Agent Readiness checker — orchestrates all 11 pillars and returns scored results.

Ported from kodustech/agent-readiness (TypeScript) with ICDEV IL/NIST extensions.

Public API:
    run_readiness_check(repo_path: str | Path) -> dict

Returns:
    {
        "pillar_scores": {pillar_id: {"passed": int, "total": int, "percentage": float}},
        "overall_readiness_score": float,   # 0.0–1.0 weighted average
        "icdev_checks": {pillar_id: [{"criterion_id", "passed", "message", "details", "skipped"}]},
    }
"""
from __future__ import annotations

import pathlib
import statistics
from functools import lru_cache
from typing import Any, Union

from tools.ai_augmentation.agent_readiness.pillars import (
    append_only_audit,
    code_quality,
    configuration,
    dependencies,
    documentation,
    il_classification,
    nist_controls,
    security,
    stig_compliance,
    structure,
    testing,
)
from tools.ai_augmentation.agent_readiness.pillars._base import Pillar

# All 11 pillars in evaluation order.
# Pillars 1–7 are ported from kodustech/agent-readiness.
# Pillars 8–11 are ICDEV extensions.
_ALL_PILLARS: list[Pillar] = [
    code_quality.PILLAR,       # 1 — Code Quality
    documentation.PILLAR,      # 2 — Documentation
    testing.PILLAR,            # 3 — Testing
    structure.PILLAR,          # 4 — Structure
    dependencies.PILLAR,       # 5 — Dependencies
    configuration.PILLAR,      # 6 — Configuration
    security.PILLAR,           # 7 — Security
    il_classification.PILLAR,  # 8 — IL Classification (ICDEV)
    nist_controls.PILLAR,      # 9 — NIST 800-53 Control References (ICDEV)
    stig_compliance.PILLAR,    # 10 — STIG Compliance Markers (ICDEV)
    append_only_audit.PILLAR,  # 11 — Append-Only Audit Tables (ICDEV)
]

_ICDEV_PILLAR_IDS = {"il-classification", "nist-controls", "stig-compliance", "append-only-audit"}

# ---------------------------------------------------------------------------
# Anomaly-detection weight loader — reads from args/agent_readiness_config.yaml
# ---------------------------------------------------------------------------
_ARGS_PATH = pathlib.Path(__file__).parents[3] / "args" / "agent_readiness_config.yaml"

_WEIGHT_DEFAULTS: dict[str, Any] = {
    "code-quality":      1.0,
    "documentation":     1.0,
    "testing":           1.2,
    "structure":         0.8,
    "dependencies":      1.0,
    "configuration":     0.8,
    "security":          1.2,
    "il-classification": 1.5,
    "nist-controls":     1.5,
    "stig-compliance":   1.3,
    "append-only-audit": 1.3,
}
# Anomaly detection defaults — used when args/agent_readiness_config.yaml has no
# anomaly_detection section.  fallback_floor is the hard minimum applied when the
# weight distribution is too small or degenerate to compute a statistical floor.
_ANOMALY_DEFAULTS: dict[str, Any] = {
    "method": "iqr",       # "iqr" (interquartile range) or "z_score"
    "sensitivity": 1.5,    # IQR multiplier / standard-deviation count
    "fallback_floor": 0.1, # last-resort minimum when distribution is degenerate
}


def _compute_weight_floor(weights: list[float], cfg: dict[str, Any]) -> float:
    """Return a dynamic anomaly-detection floor for pillar weights.

    Computes the lower bound below which a weight is considered anomalously low,
    using either IQR (Q1 - sensitivity*IQR) or z-score (mean - sensitivity*std).
    Falls back to cfg["fallback_floor"] when the sample is too small (<3 values)
    or the distribution is degenerate (IQR == 0 / stdev == 0).
    """
    fallback = float(cfg.get("fallback_floor", _ANOMALY_DEFAULTS["fallback_floor"]))
    if len(weights) < 3:
        return fallback

    method = str(cfg.get("method", _ANOMALY_DEFAULTS["method"]))
    sensitivity = float(cfg.get("sensitivity", _ANOMALY_DEFAULTS["sensitivity"]))

    if method == "z_score":
        mean = statistics.mean(weights)
        try:
            std = statistics.stdev(weights)
        except statistics.StatisticsError:
            return fallback
        if std == 0:
            return fallback
        floor = mean - sensitivity * std
    else:  # default: iqr
        sorted_w = sorted(weights)
        n = len(sorted_w)
        lower_half = sorted_w[: n // 2]
        upper_half = sorted_w[(n + 1) // 2 :]
        if not lower_half or not upper_half:
            return fallback
        q1 = statistics.median(lower_half)
        q3 = statistics.median(upper_half)
        iqr = q3 - q1
        if iqr == 0:
            return fallback
        floor = q1 - sensitivity * iqr

    # Never let the computed floor drop below the hard fallback_floor.
    return max(floor, fallback)


@lru_cache(maxsize=1)
def _load_pillar_weights() -> dict[str, float]:
    """Load pillar weights from args/agent_readiness_config.yaml.

    Falls back to built-in defaults if the file is absent or malformed.
    Anomalously low weights (detected via IQR or z-score) are clamped to the
    computed floor so they cannot distort the overall readiness score.
    """
    try:
        import yaml  # optional dep — present in all ICDEV environments
        raw = _ARGS_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        cfg = data.get("pillar_weights", {})
        anomaly_cfg: dict[str, Any] = data.get("anomaly_detection", _ANOMALY_DEFAULTS)
        if not cfg:
            return dict(_WEIGHT_DEFAULTS)
        merged = dict(_WEIGHT_DEFAULTS)
        for pillar_id, raw_weight in cfg.items():
            merged[pillar_id] = float(raw_weight)
        floor = _compute_weight_floor(list(merged.values()), anomaly_cfg)
        return {pid: max(floor, w) for pid, w in merged.items()}
    except Exception:  # noqa: BLE001
        return dict(_WEIGHT_DEFAULTS)


def run_readiness_check(repo_path: Union[str, pathlib.Path]) -> dict:
    """Run all 11 agent-readiness pillars against the given repository.

    Args:
        repo_path: Absolute path to the repository root to analyse.

    Returns:
        {
            "pillar_scores": {pillar_id: {"passed", "total", "percentage"}},
            "overall_readiness_score": float,
            "icdev_checks": {pillar_id: [criterion_result_dicts]},
        }
    """
    repo = pathlib.Path(repo_path)

    pillar_scores: dict[str, dict] = {}
    icdev_checks: dict[str, list] = {}
    all_results: list[tuple[str, float, float]] = []  # (pillar_id, weighted_pct, weight)
    weights = _load_pillar_weights()

    for pillar in _ALL_PILLARS:
        results = pillar.run(repo)
        score = pillar.score(results)
        pillar_scores[pillar.id] = score

        # Serialise criterion results
        result_dicts = [
            {
                "criterion_id": r.criterion_id,
                "passed": r.passed,
                "message": r.message,
                "details": r.details,
                "skipped": r.skipped,
            }
            for r in results
        ]
        icdev_checks[pillar.id] = result_dicts

        weight = weights.get(pillar.id, 1.0)
        all_results.append((pillar.id, score["percentage"], weight))

    # Weighted average overall score
    total_weight = sum(w for _, _, w in all_results)
    overall = sum(pct * w for _, pct, w in all_results) / total_weight if total_weight > 0 else 0.0

    return {
        "pillar_scores": pillar_scores,
        "overall_readiness_score": round(overall, 4),
        "icdev_checks": icdev_checks,
    }
