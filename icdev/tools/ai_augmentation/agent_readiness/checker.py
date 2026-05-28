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
from typing import Union

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

# Weight each pillar in the overall score.
# ICDEV pillars (8–11) are weighted equally to the core pillars.
_PILLAR_WEIGHTS: dict[str, float] = {
    "code-quality":     1.0,
    "documentation":    1.0,
    "testing":          1.2,   # testing weighted slightly higher
    "structure":        0.8,
    "dependencies":     1.0,
    "configuration":    0.8,
    "security":         1.2,   # security weighted slightly higher
    "il-classification": 1.5,  # ICDEV: IL classification is high-priority
    "nist-controls":    1.5,   # ICDEV: NIST compliance is high-priority
    "stig-compliance":  1.3,   # ICDEV: STIG compliance matters
    "append-only-audit": 1.3,  # ICDEV: audit integrity matters
}

_ICDEV_PILLAR_IDS = {"il-classification", "nist-controls", "stig-compliance", "append-only-audit"}


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

        weight = _PILLAR_WEIGHTS.get(pillar.id, 1.0)
        all_results.append((pillar.id, score["percentage"], weight))

    # Weighted average overall score
    total_weight = sum(w for _, _, w in all_results)
    overall = sum(pct * w for _, pct, w in all_results) / total_weight if total_weight > 0 else 0.0

    return {
        "pillar_scores": pillar_scores,
        "overall_readiness_score": round(overall, 4),
        "icdev_checks": icdev_checks,
    }
