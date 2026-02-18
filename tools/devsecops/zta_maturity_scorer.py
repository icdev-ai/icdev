#!/usr/bin/env python3
# CUI // SP-CTI
"""ZTA 7-Pillar Maturity Scorer — assess Zero Trust Architecture maturity per DoD strategy.

Scores each of the 7 ZTA pillars (User Identity, Device, Network, Application/Workload,
Data, Visibility/Analytics, Automation/Orchestration) from 0.0-1.0 and computes
a weighted aggregate maturity level (Traditional / Advanced / Optimal).

ADR D120: ZTA maturity model uses DoD 7-pillar scoring tracked per project per pillar.
ADR D123: ZTA posture score feeds into cATO monitor as additional evidence dimension.

Usage:
    python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --all --json
    python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --pillar network --json
    python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --trend --json
"""

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    import yaml
except ImportError:
    yaml = None

PILLARS = [
    "user_identity", "device", "network", "application_workload",
    "data", "visibility_analytics", "automation_orchestration",
]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    """Load ZTA config from YAML."""
    config_path = BASE_DIR / "args" / "zta_config.yaml"
    if yaml and config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {
        "pillars": {p: {"weight": 1.0 / len(PILLARS)} for p in PILLARS},
        "maturity_levels": {
            "traditional": {"score_range": [0.0, 0.33]},
            "advanced": {"score_range": [0.34, 0.66]},
            "optimal": {"score_range": [0.67, 1.0]},
        },
    }


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# Evidence gathering
# ---------------------------------------------------------------------------

def _gather_pillar_evidence(project_id: str, pillar: str, conn) -> dict:
    """Gather evidence for a specific ZTA pillar from project data.

    Checks: project controls (NIST 800-53), K8s manifests in DB,
    devsecops profile, scan results, and ZTA posture evidence table.
    """
    config = _load_config()
    pillar_def = config.get("pillars", {}).get(pillar, {})
    nist_controls = pillar_def.get("nist_800_53_controls", [])
    evidence_types = pillar_def.get("evidence_types", [])

    evidence = {"pillar": pillar, "checks": [], "score_components": []}

    # Check NIST 800-53 control implementations for this pillar
    if nist_controls:
        placeholders = ",".join("?" * len(nist_controls))
        rows = conn.execute(
            f"""SELECT control_id, status FROM project_controls
                WHERE project_id = ? AND control_id IN ({placeholders})""",
            [project_id] + nist_controls
        ).fetchall()

        implemented = sum(1 for r in rows if r["status"] == "implemented")
        total = len(nist_controls)
        control_score = implemented / total if total > 0 else 0.0
        evidence["checks"].append({
            "type": "nist_controls",
            "implemented": implemented,
            "total": total,
            "score": round(control_score, 3),
        })
        evidence["score_components"].append(control_score)

    # Check ZTA posture evidence
    rows = conn.execute(
        """SELECT evidence_type, status FROM zta_posture_evidence
           WHERE project_id = ? AND evidence_type IN ({})""".format(
            ",".join("?" * len(evidence_types))),
        [project_id] + evidence_types
    ).fetchall() if evidence_types else []

    current_evidence = sum(1 for r in rows if r["status"] == "current")
    total_types = len(evidence_types)
    posture_score = current_evidence / total_types if total_types > 0 else 0.0
    evidence["checks"].append({
        "type": "posture_evidence",
        "current": current_evidence,
        "total": total_types,
        "score": round(posture_score, 3),
    })
    evidence["score_components"].append(posture_score)

    # Check DevSecOps profile for relevant stages
    profile_row = conn.execute(
        "SELECT active_stages FROM devsecops_profiles WHERE project_id = ?",
        (project_id,)
    ).fetchone()

    if profile_row:
        active_stages = json.loads(profile_row["active_stages"] or "[]")
        # Map pillars to relevant DevSecOps stages
        pillar_stage_map = {
            "user_identity": [],
            "device": [],
            "network": ["policy_as_code"],
            "application_workload": ["sast", "container_scan", "image_signing"],
            "data": ["secret_detection", "sbom_attestation"],
            "visibility_analytics": ["sca", "license_compliance"],
            "automation_orchestration": ["rasp", "policy_as_code"],
        }
        relevant = pillar_stage_map.get(pillar, [])
        if relevant:
            active_relevant = [s for s in relevant if s in active_stages]
            stage_score = len(active_relevant) / len(relevant) if relevant else 0.0
            evidence["checks"].append({
                "type": "devsecops_stages",
                "active": active_relevant,
                "total_relevant": len(relevant),
                "score": round(stage_score, 3),
            })
            evidence["score_components"].append(stage_score)

    return evidence


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_pillar(project_id: str, pillar: str) -> dict:
    """Score a single ZTA pillar (0.0 - 1.0).

    Returns:
        Dict with pillar, score, maturity_level, evidence.
    """
    if pillar not in PILLARS:
        return {"error": f"Invalid pillar: {pillar}", "valid_pillars": PILLARS}

    conn = _get_db()
    try:
        evidence = _gather_pillar_evidence(project_id, pillar, conn)
        components = evidence.get("score_components", [])
        score = sum(components) / len(components) if components else 0.0
        score = round(min(score, 1.0), 3)

        maturity = _score_to_maturity(score)

        # Store score
        score_id = f"zta-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO zta_maturity_scores
               (id, project_id, pillar, score, maturity_level, evidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (score_id, project_id, pillar, score, maturity,
             json.dumps(evidence["checks"]), now)
        )
        conn.commit()

        return {
            "project_id": project_id,
            "pillar": pillar,
            "score": score,
            "maturity_level": maturity,
            "evidence": evidence["checks"],
            "assessed_at": now,
        }
    finally:
        conn.close()


def score_all_pillars(project_id: str) -> dict:
    """Score all 7 ZTA pillars and compute weighted aggregate.

    Returns:
        Dict with per-pillar scores, overall score, maturity level.
    """
    config = _load_config()
    pillar_weights = {p: config.get("pillars", {}).get(p, {}).get("weight", 1.0 / len(PILLARS))
                      for p in PILLARS}

    pillar_results = []
    weighted_sum = 0.0
    total_weight = 0.0

    for pillar in PILLARS:
        result = score_pillar(project_id, pillar)
        if "error" in result:
            continue
        pillar_results.append(result)
        weight = pillar_weights.get(pillar, 0.0)
        weighted_sum += result["score"] * weight
        total_weight += weight

    overall_score = round(weighted_sum / total_weight, 3) if total_weight > 0 else 0.0
    overall_maturity = _score_to_maturity(overall_score)

    # Store overall score
    conn = _get_db()
    try:
        score_id = f"zta-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO zta_maturity_scores
               (id, project_id, pillar, score, maturity_level, evidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (score_id, project_id, "overall", overall_score, overall_maturity,
             json.dumps([{"pillar": r["pillar"], "score": r["score"]} for r in pillar_results]),
             now)
        )
        conn.commit()
    finally:
        conn.close()

    # Identify weakest pillars
    sorted_pillars = sorted(pillar_results, key=lambda x: x["score"])
    weakest = sorted_pillars[:2] if len(sorted_pillars) >= 2 else sorted_pillars

    return {
        "project_id": project_id,
        "overall_score": overall_score,
        "overall_maturity": overall_maturity,
        "pillar_scores": {r["pillar"]: r["score"] for r in pillar_results},
        "pillar_details": pillar_results,
        "weakest_pillars": [{"pillar": w["pillar"], "score": w["score"]} for w in weakest],
        "recommendation": _generate_recommendation(overall_maturity, weakest),
    }


def get_trend(project_id: str, days: int = 90) -> dict:
    """Get ZTA maturity score trend over time.

    Returns:
        Dict with historical scores for overall and per-pillar.
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT pillar, score, maturity_level, created_at
               FROM zta_maturity_scores
               WHERE project_id = ? AND created_at >= datetime('now', ?)
               ORDER BY created_at ASC""",
            (project_id, f"-{days} days")
        ).fetchall()

        trend = {}
        for row in rows:
            pillar = row["pillar"]
            if pillar not in trend:
                trend[pillar] = []
            trend[pillar].append({
                "score": row["score"],
                "maturity_level": row["maturity_level"],
                "date": row["created_at"],
            })

        return {
            "project_id": project_id,
            "period_days": days,
            "trends": trend,
            "data_points": len(rows),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _score_to_maturity(score: float) -> str:
    """Map score to maturity level."""
    config = _load_config()
    levels = config.get("maturity_levels", {})
    for level_id, level_def in levels.items():
        lo, hi = level_def.get("score_range", [0, 1])
        if lo <= score <= hi:
            return level_id
    return "traditional"


def _generate_recommendation(maturity: str, weakest: list) -> str:
    """Generate improvement recommendation."""
    if maturity == "optimal":
        return "ZTA maturity is optimal. Maintain continuous monitoring and improvement."
    weak_names = [w["pillar"].replace("_", " ").title() for w in weakest]
    target = "optimal" if maturity == "advanced" else "advanced"
    return f"Focus on improving {' and '.join(weak_names)} pillars to reach {target} maturity."


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="ZTA 7-Pillar Maturity Scorer")
    parser.add_argument("--project-id", required=True, help="Project identifier")
    parser.add_argument("--pillar", choices=PILLARS, help="Score a specific pillar")
    parser.add_argument("--all", action="store_true", help="Score all 7 pillars + aggregate")
    parser.add_argument("--trend", action="store_true", help="Show maturity trend")
    parser.add_argument("--days", type=int, default=90, help="Trend window in days")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    if args.pillar:
        result = score_pillar(args.project_id, args.pillar)
    elif args.all:
        result = score_all_pillars(args.project_id)
    elif args.trend:
        result = get_trend(args.project_id, args.days)
    else:
        result = score_all_pillars(args.project_id)

    if args.json or not args.human:
        print(json.dumps(result, indent=2))
    else:
        if "error" in result:
            print(f"ERROR: {result['error']}")
        elif "overall_score" in result:
            print(f"Project: {result['project_id']}")
            print(f"Overall Score: {result['overall_score']:.1%}")
            print(f"Maturity Level: {result['overall_maturity'].upper()}")
            print("\nPillar Scores:")
            for pillar, score in result.get("pillar_scores", {}).items():
                bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
                print(f"  {pillar.replace('_', ' ').title():30s} {bar} {score:.1%}")
            if result.get("recommendation"):
                print(f"\nRecommendation: {result['recommendation']}")
        elif "pillar" in result:
            print(f"Pillar: {result['pillar'].replace('_', ' ').title()}")
            print(f"Score: {result['score']:.1%}")
            print(f"Maturity: {result['maturity_level'].upper()}")


if __name__ == "__main__":
    main()
