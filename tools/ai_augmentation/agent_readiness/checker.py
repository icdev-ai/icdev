# CUI // SP-CTI
"""Agent Readiness checker — orchestrates all 11 pillars and returns scored results.

Ported from kodustech/agent-readiness (TypeScript) with ICDEV IL/NIST extensions.

Public API:
    run_readiness_check(repo_path: str | Path, config_path: Optional[Path]) -> dict

Returns:
    {
        "pillar_scores": {pillar_id: {"passed": int, "total": int, "percentage": float}},
        "overall_readiness_score": float,   # 0.0–1.0 weighted average
        "icdev_checks": {pillar_id: [{"criterion_id", "passed", "message", "details", "skipped"}]},
        "score_anomalies": [{"pillar_id", "score_pct", "threshold", "is_anomalous", "reason", "ai_reasoning"}],
        "anomalies": [{"pillar": str|None, "severity": str, "reason": str}],
    }
"""
from __future__ import annotations

import pathlib
from functools import lru_cache
from typing import Any, Optional, Union

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
from tools.ai_augmentation.agent_readiness.pillars._base import (
    Pillar,
    detect_score_anomalies,
)

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
_CRITICAL_PILLAR_IDS = {"security"} | _ICDEV_PILLAR_IDS

# ---------------------------------------------------------------------------
# Configurable defaults
# ---------------------------------------------------------------------------
_DEFAULT_PILLAR_WEIGHTS: dict[str, float] = {
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

# Legacy alias used by existing tests and callers.
_WEIGHT_DEFAULTS = _DEFAULT_PILLAR_WEIGHTS

_DEFAULT_ANOMALY_CONFIG: dict[str, Any] = {
    "enabled": True,
    "critical_pillar_min_score": 0.5,
    "min_overall_readiness": 0.4,
    "outlier_std_dev_threshold": 2.0,
    "min_pillars_for_stats": 3,
}

# Default config path used when none is supplied.
_DEFAULT_CONFIG_PATH = pathlib.Path(__file__).parents[3] / "args" / "agent_readiness_config.yaml"

# Path used by the legacy cached weight loader.
_ARGS_PATH = pathlib.Path(__file__).parents[3] / "args" / "agent_readiness_config.yaml"

# Minimum weight accepted from config — values below this are anomalously low.
_MIN_WEIGHT = 0.1


@lru_cache(maxsize=1)
def _load_pillar_weights() -> dict[str, float]:
    """Load pillar weights from args/agent_readiness_config.yaml.

    Falls back to built-in defaults if the file is absent or malformed.
    Values below _MIN_WEIGHT are clamped to prevent anomalously low weights
    from distorting the overall score.
    """
    try:
        import yaml  # optional dep — present in all ICDEV environments
        raw = _ARGS_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        cfg = data.get("pillar_weights", {})
        if not cfg:
            return dict(_WEIGHT_DEFAULTS)
        merged = dict(_WEIGHT_DEFAULTS)
        for pillar_id, raw_weight in cfg.items():
            weight = float(raw_weight)
            merged[pillar_id] = max(_MIN_WEIGHT, weight)
        return merged
    except Exception:  # noqa: BLE001
        return dict(_WEIGHT_DEFAULTS)


def _load_readiness_config(config_path: Union[str, pathlib.Path, None]) -> dict[str, Any]:
    """Load YAML readiness config from *config_path*.

    Returns an empty dict when the path is None, missing, or malformed.
    """
    if config_path is None:
        return {}
    try:
        import yaml  # optional dep — present in all ICDEV environments
        raw = pathlib.Path(config_path).read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _get_pillar_weights(config: dict[str, Any]) -> dict[str, float]:
    """Merge *config* pillar weights onto hard-coded defaults.

    Unknown pillars are accepted; values below _MIN_WEIGHT are clamped.
    """
    weights = dict(_DEFAULT_PILLAR_WEIGHTS)
    cfg_weights = config.get("pillar_weights", {})
    if isinstance(cfg_weights, dict):
        for pillar_id, raw_weight in cfg_weights.items():
            try:
                weight = float(raw_weight)
                weights[str(pillar_id)] = max(_MIN_WEIGHT, weight)
            except (ValueError, TypeError):
                continue
    return weights


def _get_anomaly_config(config: dict[str, Any]) -> dict[str, Any]:
    """Merge *config* anomaly-detection settings onto hard-coded defaults."""
    cfg = dict(_DEFAULT_ANOMALY_CONFIG)
    user_cfg = config.get("anomaly_detection", {})
    if isinstance(user_cfg, dict):
        for key in cfg:
            if key in user_cfg:
                try:
                    if isinstance(cfg[key], bool):
                        cfg[key] = bool(user_cfg[key])
                    elif isinstance(cfg[key], int):
                        cfg[key] = int(user_cfg[key])
                    else:
                        cfg[key] = float(user_cfg[key])
                except (ValueError, TypeError):
                    continue
    return cfg


def _detect_score_anomalies(
    pillar_scores: dict[str, dict],
    overall_score: float,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Detect anomalous readiness patterns across pillars and overall score.

    Rules:
      1. Any pillar in _CRITICAL_PILLAR_IDS scoring below
         config["critical_pillar_min_score"] produces a critical finding.
      2. Statistical outlier detection (mean/std) when enough pillars are
         present produces a warning finding for outlier pillars.
      3. An overall readiness score below config["min_overall_readiness"]
         produces a critical finding with pillar=None.

    Returns a list of finding dicts with keys: pillar, severity, reason.
    """
    if not config.get("enabled", True):
        return []

    findings: list[dict[str, Any]] = []

    # Rule 1 — critical pillars below minimum
    critical_min = config["critical_pillar_min_score"]
    for pillar_id, score in pillar_scores.items():
        pct = float(score.get("percentage", 0.0))
        if pillar_id in _CRITICAL_PILLAR_IDS and pct < critical_min:
            findings.append({
                "pillar": pillar_id,
                "severity": "critical",
                "reason": (
                    f"Critical pillar '{pillar_id}' scored {pct:.1%} — "
                    f"below minimum {critical_min:.1%}."
                ),
            })

    # Rule 2 — outlier detection
    percentages = [
        float(score.get("percentage", 0.0))
        for score in pillar_scores.values()
        if score.get("total", 0) > 0
    ]
    if len(percentages) >= config["min_pillars_for_stats"]:
        mean = sum(percentages) / len(percentages)
        variance = sum((p - mean) ** 2 for p in percentages) / len(percentages)
        std = variance ** 0.5
        threshold = config["outlier_std_dev_threshold"]
        cutoff = mean - threshold * std if std > 0 else mean
        for pillar_id, score in pillar_scores.items():
            pct = float(score.get("percentage", 0.0))
            if score.get("total", 0) > 0 and pct < cutoff:
                # Avoid duplicate critical-severity findings for critical pillars
                already_critical = any(
                    f["pillar"] == pillar_id and f["severity"] == "critical" for f in findings
                )
                if not already_critical:
                    findings.append({
                        "pillar": pillar_id,
                        "severity": "warning",
                        "reason": (
                            f"Pillar '{pillar_id}' scored {pct:.1%} — "
                            f"outlier (mean={mean:.1%}, std={std:.1%})."
                        ),
                    })

    # Rule 3 — overall score below floor
    if overall_score < config["min_overall_readiness"]:
        findings.append({
            "pillar": None,
            "severity": "critical",
            "reason": (
                f"Overall readiness score {overall_score:.1%} is below the "
                f"minimum {config['min_overall_readiness']:.1%}."
            ),
        })

    return findings


def run_readiness_check(
    repo_path: Union[str, pathlib.Path],
    config_path: Optional[Union[str, pathlib.Path]] = None,
) -> dict:
    """Run all 11 agent-readiness pillars against the given repository.

    Args:
        repo_path: Absolute path to the repository root to analyse.
        config_path: Optional path to a YAML config file overriding defaults.

    Returns:
        {
            "pillar_scores": {pillar_id: {"passed", "total", "percentage"}},
            "overall_readiness_score": float,
            "icdev_checks": {pillar_id: [criterion_result_dicts]},
            "score_anomalies": [...],
            "anomalies": [{"pillar", "severity", "reason"}],
        }
    """
    repo = pathlib.Path(repo_path)

    # Load configuration: explicit path wins, otherwise use the legacy cached loader.
    if config_path is not None:
        config = _load_readiness_config(config_path)
        weights = _get_pillar_weights(config)
        anomaly_config = _get_anomaly_config(config)
    else:
        weights = _load_pillar_weights()
        anomaly_config = dict(_DEFAULT_ANOMALY_CONFIG)

    pillar_scores: dict[str, dict] = {}
    icdev_checks: dict[str, list] = {}
    all_results: list[tuple[str, float, float]] = []  # (pillar_id, weighted_pct, weight)

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

    # Legacy score_anomalies via _base.detect_score_anomalies (AnomalyReport objects)
    anomaly_reports = detect_score_anomalies(pillar_scores, icdev_checks)
    score_anomalies = [
        {
            "pillar_id": r.pillar_id,
            "score_pct": r.score_pct,
            "threshold": r.threshold,
            "is_anomalous": r.is_anomalous,
            "reason": r.reason,
            "ai_reasoning": r.ai_reasoning,
        }
        for r in anomaly_reports
        if r.is_anomalous
    ]

    # New-style anomalies via checker._detect_score_anomalies
    anomalies = _detect_score_anomalies(pillar_scores, overall, anomaly_config)

    return {
        "pillar_scores": pillar_scores,
        "overall_readiness_score": round(overall, 4),
        "icdev_checks": icdev_checks,
        "score_anomalies": score_anomalies,
        "anomalies": anomalies,
    }
