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
        "anomalies": [{"pillar_id", "score", "reason"}],  # flagged low/outlying pillars
    }
"""
from __future__ import annotations

import math
import pathlib
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
# Config loader — pillar weights and anomaly detection thresholds
# ---------------------------------------------------------------------------
_ARGS_PATH = pathlib.Path(__file__).parents[3] / "args" / "agent_readiness_config.yaml"

# Fallback defaults used when the config file is absent or malformed.
_DEFAULT_WEIGHTS: dict[str, float] = {
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

_DEFAULT_ANOMALY: dict[str, float] = {
    "floor_threshold": 0.25,
    "zscore_threshold": 2.0,
}


@lru_cache(maxsize=1)
def _load_scoring_config() -> dict[str, Any]:
    """Load pillar weights and anomaly thresholds from args/agent_readiness_config.yaml.

    Falls back to hard-coded defaults if the file is absent or malformed so that
    the checker remains functional in air-gapped environments without YAML support.
    """
    try:
        import yaml  # type: ignore[import]
        raw = _ARGS_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        scoring = data.get("scoring", {})

        raw_weights = scoring.get("pillar_weights", {})
        file_weights = {k: float(v) for k, v in raw_weights.items()} if raw_weights else {}
        merged_weights = {**_DEFAULT_WEIGHTS, **file_weights}

        anomaly_cfg = scoring.get("anomaly_detection", {})
        return {
            "weights": merged_weights,
            "anomaly": {
                "floor_threshold": float(
                    anomaly_cfg.get("floor_threshold", _DEFAULT_ANOMALY["floor_threshold"])
                ),
                "zscore_threshold": float(
                    anomaly_cfg.get("zscore_threshold", _DEFAULT_ANOMALY["zscore_threshold"])
                ),
            },
        }
    except Exception:  # noqa: BLE001
        return {
            "weights": dict(_DEFAULT_WEIGHTS),
            "anomaly": dict(_DEFAULT_ANOMALY),
        }


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------

def _detect_anomalies(
    pillar_scores: dict[str, dict],
    floor_threshold: float,
    zscore_threshold: float,
) -> list[dict]:
    """Flag pillar scores that are anomalously low or statistically outlying.

    A pillar is flagged when either condition is met:
    - Its percentage score is below ``floor_threshold`` (absolute low regardless of peers).
    - Its downward z-score among all evaluated pillars exceeds ``zscore_threshold``
      (relative outlier; only computed when ≥3 pillars have data).

    Skipped pillars (total == 0) are excluded from both checks.
    """
    evaluated = {
        pid: info["percentage"]
        for pid, info in pillar_scores.items()
        if info.get("total", 0) > 0
    }
    if not evaluated:
        return []

    scores_list = list(evaluated.values())
    mean = sum(scores_list) / len(scores_list)
    std = 0.0
    if len(scores_list) >= 3:
        variance = sum((s - mean) ** 2 for s in scores_list) / len(scores_list)
        std = math.sqrt(variance)

    anomalies: list[dict] = []
    flagged: set[str] = set()

    # Floor check first (absolute threshold, highest priority).
    for pid, score in evaluated.items():
        if score < floor_threshold:
            anomalies.append({
                "pillar_id": pid,
                "score": round(score, 4),
                "reason": (
                    f"score {score:.1%} is below anomaly floor "
                    f"({floor_threshold:.0%})"
                ),
            })
            flagged.add(pid)

    # Z-score check (relative outlier detection, only when std is meaningful).
    if std > 0:
        for pid, score in evaluated.items():
            if pid in flagged:
                continue
            z = (mean - score) / std  # positive z = below mean
            if z >= zscore_threshold:
                anomalies.append({
                    "pillar_id": pid,
                    "score": round(score, 4),
                    "reason": (
                        f"score {score:.1%} is a low outlier "
                        f"(z={z:.2f}, mean={mean:.1%})"
                    ),
                })

    return anomalies


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_readiness_check(repo_path: Union[str, pathlib.Path]) -> dict:
    """Run all 11 agent-readiness pillars against the given repository.

    Args:
        repo_path: Absolute path to the repository root to analyse.

    Returns:
        {
            "pillar_scores": {pillar_id: {"passed", "total", "percentage"}},
            "overall_readiness_score": float,
            "icdev_checks": {pillar_id: [criterion_result_dicts]},
            "anomalies": [{"pillar_id", "score", "reason"}],
        }
    """
    repo = pathlib.Path(repo_path)
    cfg = _load_scoring_config()
    weights: dict[str, float] = cfg["weights"]
    anomaly_cfg: dict[str, float] = cfg["anomaly"]

    pillar_scores: dict[str, dict] = {}
    icdev_checks: dict[str, list] = {}
    all_results: list[tuple[str, float, float]] = []  # (pillar_id, weighted_pct, weight)

    for pillar in _ALL_PILLARS:
        results = pillar.run(repo)
        score = pillar.score(results)
        pillar_scores[pillar.id] = score

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

    total_weight = sum(w for _, _, w in all_results)
    overall = sum(pct * w for _, pct, w in all_results) / total_weight if total_weight > 0 else 0.0

    anomalies = _detect_anomalies(
        pillar_scores,
        floor_threshold=anomaly_cfg["floor_threshold"],
        zscore_threshold=anomaly_cfg["zscore_threshold"],
    )

    return {
        "pillar_scores": pillar_scores,
        "overall_readiness_score": round(overall, 4),
        "icdev_checks": icdev_checks,
        "anomalies": anomalies,
    }
