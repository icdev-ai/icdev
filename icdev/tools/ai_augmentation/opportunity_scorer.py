# [TEMPLATE: CUI // SP-CTI]
#!/usr/bin/env python3
"""AI Augmentation Opportunity Scorer for ICDEV™ AAC Engine.

Scores detected AI augmentation opportunities using a weighted multi-criteria
model loaded from args/aac_config.yaml.  All scoring is deterministic — no LLM
calls, no external network access.

Scoring formulas (weights loaded from args/aac_config.yaml):
  value       = usage_freq_norm * 0.40 + task_complexity_norm * 0.35 + automation_deficit_norm * 0.25
  feasibility = data_avail * 0.40 + il_model_exists * 0.35 + (1 - integration_complexity_norm) * 0.25
  risk        = reversibility * 0.40 + compliance_impact_inv * 0.35 + (1 - dep_complexity_norm) * 0.25
  composite   = value * 0.45 + feasibility * 0.35 + (1 - risk) * 0.20

All scores normalized to [0.0, 1.0].

Usage:
    python tools/ai_augmentation/opportunity_scorer.py --opportunity-file opp.json
    python tools/ai_augmentation/opportunity_scorer.py --opportunity-file opp.json --json
    python tools/ai_augmentation/opportunity_scorer.py \\
        --usage-freq 0.8 --task-complexity 0.7 --automation-deficit 0.6 \\
        --data-avail 0.9 --il-model-exists 1 --integration-complexity 0.3 \\
        --reversibility 0.8 --compliance-impact 0.2 --dep-complexity 0.4

Classification: CUI // SP-CTI
Environment:    AWS GovCloud (us-gov-west-1)
Compliance:     NIST 800-53 Rev 5 / RMF
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = BASE_DIR / "args" / "aac_config.yaml"

# ---------------------------------------------------------------------------
# Fallback weights (mirrors aac_config.yaml defaults)
# Used only if the config file is missing or malformed.
# ---------------------------------------------------------------------------
_FALLBACK_CONFIG = {
    "scoring_weights": {"value": 0.45, "feasibility": 0.35, "risk_inv": 0.20},
    "value_weights": {"usage_freq": 0.40, "task_complexity": 0.35, "automation_deficit": 0.25},
    "feasibility_weights": {"data_avail": 0.40, "il_model_exists": 0.35, "integration_simplicity": 0.25},
    "risk_weights": {"reversibility": 0.40, "compliance_impact_inv": 0.35, "dep_simplicity": 0.25},
    "il_penalties": {"il4": 0.0, "il5": 0.1, "il6": 0.2},
    "anomaly_detection": {
        "value_feasibility_max_delta": 0.50,
        "component_outlier_floor": 0.05,
        "component_outlier_ceiling": 0.95,
    },
}


# ============================================================================
# Config loader
# ============================================================================


def _load_config(config_path=None) -> dict:
    """Load AAC scoring weights from args/aac_config.yaml.

    Args:
        config_path: Optional override path to the config YAML file.

    Returns:
        Parsed config dict; falls back to _FALLBACK_CONFIG on any error.
    """
    path = config_path or CONFIG_PATH
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        if not isinstance(cfg, dict):
            return dict(_FALLBACK_CONFIG)
        return cfg
    except Exception:
        return dict(_FALLBACK_CONFIG)


# ============================================================================
# Input normalizers
# ============================================================================


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, float(value)))


def _norm(raw, *, default: float = 0.5) -> float:
    """Normalize a raw opportunity field to [0.0, 1.0].

    Accepts:
      - bool  → 1.0 / 0.0
      - float / int already in [0.0, 1.0] → returned as-is (clamped)
      - None / missing → ``default``
    """
    if raw is None:
        return _clamp(default)
    if isinstance(raw, bool):
        return 1.0 if raw else 0.0
    return _clamp(float(raw))


def _extract_components(opportunity: dict, scan_context: dict, config: dict | None = None) -> dict:
    """Extract and normalize all scoring components from the opportunity dict.

    Missing keys fall back to neutral defaults (0.5) so partial opportunity
    dicts still produce a meaningful score.

    Args:
        opportunity:  Opportunity dict from the AAC scanner.
        scan_context: Broader scan context (il_level, available_models, etc.).

    Returns:
        Dict of all normalized components in [0.0, 1.0].
    """
    # Value components
    usage_freq_norm = _norm(opportunity.get("usage_freq"), default=0.5)
    task_complexity_norm = _norm(opportunity.get("task_complexity"), default=0.5)
    automation_deficit_norm = _norm(opportunity.get("automation_deficit"), default=0.5)

    # Feasibility components
    data_avail = _norm(opportunity.get("data_avail"), default=0.5)
    # il_model_exists is often boolean — convert to 0/1 float
    il_model_exists = _norm(opportunity.get("il_model_exists"), default=0.0)
    # integration_complexity is stored as-is; scorer inverts it below
    integration_complexity_norm = _norm(opportunity.get("integration_complexity"), default=0.5)

    # Risk components
    reversibility = _norm(opportunity.get("reversibility"), default=0.5)
    # compliance_impact_inv: caller may provide already-inverted value OR raw impact
    # Convention: field named compliance_impact_inv is already inverted;
    #             compliance_impact is raw (we invert it here).
    if "compliance_impact_inv" in opportunity:
        compliance_impact_inv = _norm(opportunity["compliance_impact_inv"], default=0.5)
    else:
        compliance_impact_inv = 1.0 - _norm(opportunity.get("compliance_impact"), default=0.5)

    dep_complexity_norm = _norm(opportunity.get("dep_complexity"), default=0.5)

    # IL-level adjustment: il6 raises compliance burden slightly when
    # compliance_impact is not explicitly provided.
    il_level = str(scan_context.get("il_level", "il4")).lower()
    if "compliance_impact_inv" not in opportunity and "compliance_impact" not in opportunity:
        cfg = config if config is not None else _load_config()
        il_penalties = cfg.get("il_penalties", _FALLBACK_CONFIG["il_penalties"])
        il_penalty = il_penalties.get(il_level, 0.0)
        compliance_impact_inv = _clamp(compliance_impact_inv - il_penalty)

    return {
        "usage_freq_norm": usage_freq_norm,
        "task_complexity_norm": task_complexity_norm,
        "automation_deficit_norm": automation_deficit_norm,
        "data_avail": data_avail,
        "il_model_exists": il_model_exists,
        "integration_complexity_norm": integration_complexity_norm,
        "reversibility": reversibility,
        "compliance_impact_inv": compliance_impact_inv,
        "dep_complexity_norm": dep_complexity_norm,
    }


# ============================================================================
# Sub-score computations
# ============================================================================


def _compute_value_score(components: dict, weights: dict) -> float:
    """Compute value_score from normalized components.

    Formula:
        value = usage_freq_norm * w_usage
               + task_complexity_norm * w_complexity
               + automation_deficit_norm * w_deficit

    Args:
        components: Normalized component dict from _extract_components.
        weights:    value_weights section of aac_config.yaml.

    Returns:
        Float in [0.0, 1.0].
    """
    w_usage = weights.get("usage_freq", 0.40)
    w_complexity = weights.get("task_complexity", 0.35)
    w_deficit = weights.get("automation_deficit", 0.25)

    total_w = w_usage + w_complexity + w_deficit
    if total_w <= 0:
        return 0.0

    raw = (
        components["usage_freq_norm"] * w_usage
        + components["task_complexity_norm"] * w_complexity
        + components["automation_deficit_norm"] * w_deficit
    ) / total_w

    return _clamp(raw)


def _compute_feasibility_score(components: dict, weights: dict) -> float:
    """Compute feasibility_score from normalized components.

    Formula:
        feasibility = data_avail * w_data
                     + il_model_exists * w_model
                     + (1 - integration_complexity_norm) * w_integration

    Args:
        components: Normalized component dict from _extract_components.
        weights:    feasibility_weights section of aac_config.yaml.

    Returns:
        Float in [0.0, 1.0].
    """
    w_data = weights.get("data_avail", 0.40)
    w_model = weights.get("il_model_exists", 0.35)
    w_integration = weights.get("integration_simplicity", 0.25)

    total_w = w_data + w_model + w_integration
    if total_w <= 0:
        return 0.0

    integration_simplicity = 1.0 - components["integration_complexity_norm"]

    raw = (
        components["data_avail"] * w_data
        + components["il_model_exists"] * w_model
        + integration_simplicity * w_integration
    ) / total_w

    return _clamp(raw)


def _compute_risk_score(components: dict, weights: dict) -> float:
    """Compute risk_score from normalized components.

    Formula:
        risk = reversibility * w_rev
               + compliance_impact_inv * w_compliance
               + (1 - dep_complexity_norm) * w_dep

    A higher risk_score means *lower* net risk (all components are
    "good-direction" so the composite formula uses (1 - risk) internally
    when computing value contribution from risk).

    Wait — re-reading the task description:
        risk = reversibility*0.40 + compliance_impact_inv*0.35 + (1-dep_complexity_norm)*0.25
        composite = value*0.45 + feasibility*0.35 + (1-risk)*0.20

    So risk_score IS the weighted sum (higher = less risky), and composite
    applies (1 - risk) to weight the risk contribution against value.
    Callers should interpret risk_score as "higher = riskier inverse" — i.e.
    a score of 1.0 means zero reversibility concerns.

    Args:
        components: Normalized component dict from _extract_components.
        weights:    risk_weights section of aac_config.yaml.

    Returns:
        Float in [0.0, 1.0].
    """
    w_rev = weights.get("reversibility", 0.40)
    w_compliance = weights.get("compliance_impact_inv", 0.35)
    w_dep = weights.get("dep_simplicity", 0.25)

    total_w = w_rev + w_compliance + w_dep
    if total_w <= 0:
        return 0.0

    dep_simplicity = 1.0 - components["dep_complexity_norm"]

    raw = (
        components["reversibility"] * w_rev
        + components["compliance_impact_inv"] * w_compliance
        + dep_simplicity * w_dep
    ) / total_w

    return _clamp(raw)


def _compute_composite_score(value_score: float, feasibility_score: float, risk_score: float, weights: dict) -> float:
    """Compute composite_score from the three sub-scores.

    Formula:
        composite = value * w_value + feasibility * w_feasibility + (1 - risk) * w_risk_inv

    Note: risk_score as returned by _compute_risk_score is already in
    "higher = safer" orientation; composite uses (1 - risk) so that
    riskier opportunities reduce the composite score.

    Args:
        value_score:       Float in [0.0, 1.0].
        feasibility_score: Float in [0.0, 1.0].
        risk_score:        Float in [0.0, 1.0] (higher = safer).
        weights:           scoring_weights section of aac_config.yaml.

    Returns:
        Float in [0.0, 1.0].
    """
    w_value = weights.get("value", 0.45)
    w_feasibility = weights.get("feasibility", 0.35)
    w_risk_inv = weights.get("risk_inv", 0.20)

    total_w = w_value + w_feasibility + w_risk_inv
    if total_w <= 0:
        return 0.0

    raw = (
        value_score * w_value
        + feasibility_score * w_feasibility
        + (1.0 - risk_score) * w_risk_inv
    ) / total_w

    return _clamp(raw)


# ============================================================================
# Public API
# ============================================================================


def detect_score_anomalies(score_result: dict, config: dict | None = None) -> list:
    """Detect statistical anomalies in a scored opportunity.

    Uses thresholds from the ``anomaly_detection`` section of aac_config.yaml
    rather than hardcoded values.  Returns a list of anomaly dicts; empty list
    means no anomalies detected.

    Checks performed:
      1. ``value_feasibility_delta`` — |value_score - feasibility_score| > threshold
      2. ``component_outlier_low``  — any component below the floor threshold
      3. ``component_outlier_high`` — any component above the ceiling threshold

    Args:
        score_result: Return value of ``score_opportunity``.
        config:       Optional pre-loaded config dict; loaded from file if None.

    Returns:
        List of anomaly dicts, each with at least ``type`` and ``severity`` keys.
    """
    cfg = config if config is not None else _load_config()
    anomaly_cfg = cfg.get("anomaly_detection", _FALLBACK_CONFIG["anomaly_detection"])

    max_delta = float(anomaly_cfg.get("value_feasibility_max_delta", 0.50))
    floor = float(anomaly_cfg.get("component_outlier_floor", 0.05))
    ceiling = float(anomaly_cfg.get("component_outlier_ceiling", 0.95))

    anomalies = []

    value = float(score_result.get("value_score", 0.0))
    feasibility = float(score_result.get("feasibility_score", 0.0))
    delta = abs(value - feasibility)
    if delta > max_delta:
        anomalies.append({
            "type": "value_feasibility_delta",
            "detail": f"|value({value:.4f}) - feasibility({feasibility:.4f})| = {delta:.4f} > {max_delta}",
            "severity": "warning",
        })

    components = score_result.get("score_detail", {}).get("components", {})
    for name, val in components.items():
        fval = float(val)
        if fval < floor:
            anomalies.append({
                "type": "component_outlier_low",
                "component": name,
                "value": fval,
                "threshold": floor,
                "severity": "warning",
            })
        elif fval > ceiling:
            anomalies.append({
                "type": "component_outlier_high",
                "component": name,
                "value": fval,
                "threshold": ceiling,
                "severity": "warning",
            })

    return anomalies


def score_opportunity(opportunity: dict, scan_context: dict) -> dict:
    """Score a single AI augmentation opportunity.

    All sub-scores and the composite score are normalized to [0.0, 1.0].
    Weights are loaded from args/aac_config.yaml; the function degrades
    gracefully to built-in defaults if the file is missing.

    Args:
        opportunity:  Dict with any subset of the following fields (all
                      expected in [0.0, 1.0] or bool; missing keys → 0.5):
                        usage_freq            — how frequently the code path runs
                        task_complexity       — complexity of the task being automated
                        automation_deficit    — degree of manual effort currently spent
                        data_avail            — data availability for AI model training
                        il_model_exists       — IL-compatible model exists (bool or float)
                        integration_complexity — effort to wire in the AI component
                        reversibility         — ease of reverting the AI change
                        compliance_impact     — raw compliance burden (auto-inverted)
                        compliance_impact_inv — pre-inverted compliance burden
                        dep_complexity        — dependency complexity of the AI change
        scan_context: Dict with broader scan context:
                        il_level        — "il4" | "il5" | "il6" (default "il4")
                        available_models — list of model IDs (informational)

    Returns:
        Dict with:
            value_score      — float [0.0, 1.0]: business value of automating this
            feasibility_score— float [0.0, 1.0]: ease of implementing AI here
            risk_score       — float [0.0, 1.0]: higher = safer (less risky)
            composite_score  — float [0.0, 1.0]: overall opportunity attractiveness
            score_detail     — dict with weights used and normalized components
            anomaly_flags    — list of anomaly dicts (empty = no anomalies)
    """
    cfg = _load_config()

    scoring_w = cfg.get("scoring_weights", _FALLBACK_CONFIG["scoring_weights"])
    value_w = cfg.get("value_weights", _FALLBACK_CONFIG["value_weights"])
    feasibility_w = cfg.get("feasibility_weights", _FALLBACK_CONFIG["feasibility_weights"])
    risk_w = cfg.get("risk_weights", _FALLBACK_CONFIG["risk_weights"])

    components = _extract_components(opportunity, scan_context, config=cfg)

    value_score = _compute_value_score(components, value_w)
    feasibility_score = _compute_feasibility_score(components, feasibility_w)
    risk_score = _compute_risk_score(components, risk_w)
    composite_score = _compute_composite_score(value_score, feasibility_score, risk_score, scoring_w)

    score_detail = {
        "components": {k: round(v, 4) for k, v in components.items()},
        "weights": {
            "value": value_w,
            "feasibility": feasibility_w,
            "risk": risk_w,
            "scoring": scoring_w,
        },
        "sub_scores": {
            "value": round(value_score, 4),
            "feasibility": round(feasibility_score, 4),
            "risk": round(risk_score, 4),
        },
    }

    result = {
        "value_score": round(value_score, 4),
        "feasibility_score": round(feasibility_score, 4),
        "risk_score": round(risk_score, 4),
        "composite_score": round(composite_score, 4),
        "score_detail": score_detail,
    }
    result["anomaly_flags"] = detect_score_anomalies(result, config=cfg)
    return result


# ============================================================================
# CLI entry point
# ============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "AI Augmentation Opportunity Scorer — "
            "Scores detected opportunities using weighted multi-criteria scoring "
            "loaded from args/aac_config.yaml."
        ),
        epilog="CUI // SP-CTI",
    )
    parser.add_argument("--opportunity-file", metavar="PATH", help="Path to opportunity JSON file")
    parser.add_argument("--context-file", metavar="PATH", help="Path to scan context JSON file")
    # Individual field overrides (used when no --opportunity-file is given)
    parser.add_argument("--usage-freq", type=float, metavar="0-1")
    parser.add_argument("--task-complexity", type=float, metavar="0-1")
    parser.add_argument("--automation-deficit", type=float, metavar="0-1")
    parser.add_argument("--data-avail", type=float, metavar="0-1")
    parser.add_argument("--il-model-exists", type=float, metavar="0-1")
    parser.add_argument("--integration-complexity", type=float, metavar="0-1")
    parser.add_argument("--reversibility", type=float, metavar="0-1")
    parser.add_argument("--compliance-impact", type=float, metavar="0-1")
    parser.add_argument("--dep-complexity", type=float, metavar="0-1")
    parser.add_argument("--il-level", default="il4", choices=["il4", "il5", "il6"])
    parser.add_argument("--json", action="store_true", dest="output_json", help="Output as JSON")
    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()

    # Build opportunity dict
    if args.opportunity_file:
        opp_path = Path(args.opportunity_file)
        if not opp_path.exists():
            print(f"ERROR: opportunity file not found: {opp_path}", file=sys.stderr)
            sys.exit(1)
        with open(opp_path, "r", encoding="utf-8") as fh:
            opportunity = json.load(fh)
    else:
        opportunity = {}
        field_map = {
            "usage_freq": args.usage_freq,
            "task_complexity": args.task_complexity,
            "automation_deficit": args.automation_deficit,
            "data_avail": args.data_avail,
            "il_model_exists": args.il_model_exists,
            "integration_complexity": args.integration_complexity,
            "reversibility": args.reversibility,
            "compliance_impact": args.compliance_impact,
            "dep_complexity": args.dep_complexity,
        }
        for key, val in field_map.items():
            if val is not None:
                opportunity[key] = val

    # Build scan context
    if args.context_file:
        ctx_path = Path(args.context_file)
        if not ctx_path.exists():
            print(f"ERROR: context file not found: {ctx_path}", file=sys.stderr)
            sys.exit(1)
        with open(ctx_path, "r", encoding="utf-8") as fh:
            scan_context = json.load(fh)
    else:
        scan_context = {"il_level": args.il_level}

    result = score_opportunity(opportunity, scan_context)

    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        print("=" * 60)
        print("CUI // SP-CTI")
        print("=" * 60)
        print("AI Augmentation Opportunity Score")
        print(f"  Value Score:       {result['value_score']:.4f}")
        print(f"  Feasibility Score: {result['feasibility_score']:.4f}")
        print(f"  Risk Score:        {result['risk_score']:.4f}  (higher = safer)")
        print(f"  Composite Score:   {result['composite_score']:.4f}")
        print()
        print("Components:")
        for k, v in result["score_detail"]["components"].items():
            print(f"    {k:<30} {v:.4f}")
        print("=" * 60)
        print("CUI // SP-CTI")
        print("=" * 60)


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
