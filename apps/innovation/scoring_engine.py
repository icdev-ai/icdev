# CUI // SP-CTI
"""FORGE IGNITE scoring engine — composite score + phase tag.

Pulls two grounded signals from real system state:
  - Technical Feasibility: Academy cohort L2+ completion rate (fa_users)
  - Data Readiness:        fine-tune datasets + RAG quality scores
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from tools.db.storage import get_connection

logger = logging.getLogger("icdev.innovation.scoring")

_CFG_PATH = Path(__file__).resolve().parent.parent.parent / "args" / "innovation_scoring.yaml"
_cfg: dict = {}


def _cfg_path_fixed():
    """Lazy load with correct path."""
    global _cfg
    if not _cfg:
        try:
            with open(_CFG_PATH) as f:
                _cfg = yaml.safe_load(f) or {}
        except Exception:
            _cfg = {}
    return _cfg


# ---------------------------------------------------------------------------
# Grounded signal pullers
# ---------------------------------------------------------------------------

def _pull_technical_feasibility() -> float:
    """L2+ (xp >= 500) completion rate in Academy cohort → 2.0–9.0 scale."""
    try:
        with get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM fa_users").fetchone()[0]
            if total == 0:
                return 5.0
            l2_plus = conn.execute(
                "SELECT COUNT(*) FROM fa_users WHERE xp >= 500"
            ).fetchone()[0]
            pct = l2_plus / total
            return round(2.0 + pct * 7.0, 1)
    except Exception:
        return 5.0


def _pull_data_readiness() -> float:
    """Fine-tune ready datasets + RAG NDCG quality → 0.0–10.0 scale."""
    try:
        with get_connection() as conn:
            # Count ready finetune datasets
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM ft_datasets WHERE status='ready'"
                ).fetchone()[0]
            except Exception:
                count = 0
            # Average RAG quality NDCG score
            try:
                avg_ndcg = conn.execute(
                    "SELECT AVG(score_value) FROM ft_quality_scores WHERE metric_name='ndcg'"
                ).fetchone()[0] or 0.0
            except Exception:
                avg_ndcg = 0.0
            data_score = min(5.0, count * 0.5)
            quality_score = float(avg_ndcg) * 5.0
            return round(min(10.0, data_score + quality_score), 1)
    except Exception:
        return 5.0


# ---------------------------------------------------------------------------
# Core scoring
# ---------------------------------------------------------------------------

def compute_score(answers: dict, override_scores: dict = None) -> dict:
    """Compute composite innovation score from intake answers + system signals.

    Args:
        answers: intake_answers dict from the Socratic questionnaire
        override_scores: optional dict of {dimension: value} to bypass inference

    Returns:
        {
          "dimensions": {dim: {"score": float, "weight": float, "source": str}},
          "total": float,          # 0-100
          "phase_tag": str,        # "phase1" | "phase2" | "phase3" | "deferred"
          "phase_label": str,
          "phase_emoji": str,
        }
    """
    cfg = _cfg_path_fixed()
    dim_cfg = cfg.get("scoring", {}).get("dimensions", {})
    overrides = override_scores or {}

    # ── Infer scores from answers ──────────────────────────────────────
    freq_map = {
        "Multiple times per hour": 10, "Daily": 8, "Weekly": 6,
        "Monthly": 4, "Ad hoc": 2, "": 5,
    }
    pattern_map = {
        "Highly predictable / repeating pattern": 9,
        "Mostly predictable": 7,
        "Mixed": 5,
        "Unique each time": 3,
        "": 5,
    }
    ai_fit_map = {
        "No — judgment / reasoning is essential": 9,
        "Partially — simpler handles basics but misses edge cases": 7,
        "Unsure": 5,
        "Yes — simpler solution probably works": 2,
        "": 5,
    }
    data_avail_map = {
        "Yes — clean, labeled data exists": 10,
        "Partial — some data exists but needs work": 6,
        "Unsure": 4,
        "No — would need to collect data first": 1,
        "": 5,
    }

    # Business Value: frequency × impact length (proxy)
    freq_score = freq_map.get(answers.get("q2_frequency", ""), 5)
    impact_len = min(10, max(1, len(answers.get("q3_impact", "")) / 30))
    bv_inferred = round((freq_score + impact_len) / 2, 1)

    # Strategic Alignment: pattern score (repeating = more strategic to automate)
    sa_inferred = round(pattern_map.get(answers.get("q5_pattern", ""), 5) * 0.8 + 1.0, 1)

    # Technical Feasibility: pulled from Academy cohort (live signal)
    tf_live = _pull_technical_feasibility()

    # Data Readiness: blend of intake answer + live RAG/finetune signal
    intake_data_score = data_avail_map.get(answers.get("q10_data_available", ""), 5)
    live_data_score = _pull_data_readiness()
    dr_blended = round((intake_data_score * 0.6 + live_data_score * 0.4), 1)

    # Time to Value: inferred from MVP description length (longer = more complex = lower)
    mvp_len = len(answers.get("q11_mvp", ""))
    integration_len = len(answers.get("q12_integration", ""))
    ttv_inferred = round(max(2, 9 - (mvp_len + integration_len) / 200), 1)

    # Risk: AI fit (if simpler solution exists → lower risk of AI adding complexity)
    ai_fit_score = ai_fit_map.get(answers.get("q7_simple_solution", ""), 5)
    failure_cost_len = len(answers.get("q9_failure_cost", ""))
    risk_inferred = round(min(9, (10 - ai_fit_score * 0.5) + failure_cost_len / 200), 1)

    raw = {
        "business_value": overrides.get("business_value", bv_inferred),
        "strategic_alignment": overrides.get("strategic_alignment", sa_inferred),
        "technical_feasibility": overrides.get("technical_feasibility", tf_live),
        "data_readiness": overrides.get("data_readiness", dr_blended),
        "time_to_value": overrides.get("time_to_value", ttv_inferred),
        "risk": overrides.get("risk", risk_inferred),
    }

    # Clamp all to [0, 10]
    for k in raw:
        raw[k] = max(0.0, min(10.0, float(raw[k])))

    sources = {
        "business_value": "intake",
        "strategic_alignment": "intake",
        "technical_feasibility": "academy_cohort" if "technical_feasibility" not in overrides else "override",
        "data_readiness": "blended_intake_and_system" if "data_readiness" not in overrides else "override",
        "time_to_value": "intake",
        "risk": "intake",
    }
    if "technical_feasibility" in overrides:
        sources["technical_feasibility"] = "override"
    if "data_readiness" in overrides:
        sources["data_readiness"] = "override"

    # ── Weighted sum ───────────────────────────────────────────────────
    weights = {
        "business_value": 0.25,
        "strategic_alignment": 0.20,
        "technical_feasibility": 0.20,
        "data_readiness": 0.15,
        "time_to_value": 0.15,
        "risk": 0.05,
    }
    if dim_cfg:
        for k, cfg_dim in dim_cfg.items():
            if k in weights:
                weights[k] = float(cfg_dim.get("weight", weights[k]))

    risk_penalty = cfg.get("scoring", {}).get("risk_penalty_factor", 0.05)
    weighted_sum = sum(
        raw[k] * weights[k] for k in raw if k != "risk"
    )
    # Scale to 0-100, then apply risk penalty
    raw_total = weighted_sum * (1.0 / (1.0 - weights["risk"])) * 10
    total = round(raw_total * (1.0 - raw["risk"] * risk_penalty), 1)
    total = max(0.0, min(100.0, total))

    # ── Phase tag ──────────────────────────────────────────────────────
    phase_tag, phase_label, phase_emoji = _assign_phase(
        total, raw["time_to_value"], raw["risk"],
        raw["data_readiness"], raw["technical_feasibility"], cfg
    )

    dimensions = {}
    for k in raw:
        dimensions[k] = {
            "score": raw[k],
            "weight": weights[k],
            "source": sources.get(k, "intake"),
            "label": dim_cfg.get(k, {}).get("label", k.replace("_", " ").title()),
        }

    return {
        "dimensions": dimensions,
        "total": total,
        "phase_tag": phase_tag,
        "phase_label": phase_label,
        "phase_emoji": phase_emoji,
    }


def _assign_phase(total: float, ttv: float, risk: float,
                  data_readiness: float, tech_feasibility: float, cfg: dict) -> tuple:
    thresholds = cfg.get("phase_thresholds", {})

    # Deferred first: blocked by system signals
    deferred_triggers = thresholds.get("deferred", {}).get("triggers", [])
    for trigger in deferred_triggers:
        if "data_readiness_below" in trigger and data_readiness < trigger["data_readiness_below"]:
            return "deferred", "Deferred", "⚪"
        if "technical_feasibility_below" in trigger and tech_feasibility < trigger["technical_feasibility_below"]:
            return "deferred", "Deferred", "⚪"

    p1 = thresholds.get("phase1_quick_win", {})
    if (total >= p1.get("min_score", 70) and
            ttv >= p1.get("min_time_to_value", 8) and
            risk <= p1.get("max_risk", 3)):
        return "phase1", p1.get("label", "Phase 1 — Quick Win"), p1.get("emoji", "🟢")

    p2 = thresholds.get("phase2_augmentation", {})
    if total >= p2.get("min_score", 50):
        return "phase2", p2.get("label", "Phase 2 — Augmentation"), p2.get("emoji", "🟡")

    p3 = thresholds.get("phase3_transformation", {})
    return "phase3", p3.get("label", "Phase 3 — Transformation"), p3.get("emoji", "🔴")
