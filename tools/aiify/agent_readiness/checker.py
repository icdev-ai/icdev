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
from functools import lru_cache
from typing import Any, Union

from tools.aiify.agent_readiness.pillars import (
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
from tools.aiify.agent_readiness.pillars._base import Pillar

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
# Fallback floor — overridden by weight_anomaly_detection.min_weight in args config.
_MIN_WEIGHT = 0.1


@lru_cache(maxsize=1)
def _load_pillar_weights() -> dict[str, float]:
    """Load pillar weights from args/agent_readiness_config.yaml.

    Falls back to built-in defaults if the file is absent or malformed.
    The anomaly-detection floor (weight_anomaly_detection.min_weight) is read
    from the same config so operators can tune it without touching code.
    Values below that floor are clamped to prevent near-zero weights from
    silencing a pillar and distorting the overall score.
    """
    try:
        import yaml  # optional dep — present in all ICDEV environments
        raw = _ARGS_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        anomaly_cfg = data.get("weight_anomaly_detection") or {}
        min_weight = float(anomaly_cfg.get("min_weight", _MIN_WEIGHT))
        cfg = data.get("pillar_weights", {})
        if not cfg:
            return dict(_WEIGHT_DEFAULTS)
        merged = dict(_WEIGHT_DEFAULTS)
        for pillar_id, raw_weight in cfg.items():
            weight = float(raw_weight)
            merged[pillar_id] = max(min_weight, weight)
        return merged
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
