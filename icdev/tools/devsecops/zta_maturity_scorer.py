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
import uuid
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    import yaml
except ImportError:
    yaml = None

PILLARS = [
    "user_identity",
    "device",
    "network",
    "application_workload",
    "data",
    "visibility_analytics",
    "automation_orchestration",
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
    conn = get_connection()
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
        placeholders = ",".join(["%s"] * len(nist_controls))
        rows = conn.execute(
            f"""SELECT control_id, status FROM project_controls
                WHERE project_id = %s AND control_id IN ({placeholders})""",  # nosec B608 -- table/column names are internal constants, not user input
            [project_id] + nist_controls,
        ).fetchall()

        implemented = sum(1 for r in rows if r["status"] == "implemented")
        total = len(nist_controls)
        control_score = implemented / total if total > 0 else 0.0
        evidence["checks"].append(
            {
                "type": "nist_controls",
                "implemented": implemented,
                "total": total,
                "score": round(control_score, 3),
            }
        )
        evidence["score_components"].append(control_score)

    # Check ZTA posture evidence
    rows = (
        conn.execute(
            """SELECT evidence_type, status FROM zta_posture_evidence
           WHERE project_id = %s AND evidence_type IN ({})""".format(  # nosec B608 -- table/column names are internal constants, not user input
                ",".join(["%s"] * len(evidence_types))
            ),
            [project_id] + evidence_types,
        ).fetchall()
        if evidence_types
        else []
    )

    current_evidence = sum(1 for r in rows if r["status"] == "current")
    total_types = len(evidence_types)
    posture_score = current_evidence / total_types if total_types > 0 else 0.0
    evidence["checks"].append(
        {
            "type": "posture_evidence",
            "current": current_evidence,
            "total": total_types,
            "score": round(posture_score, 3),
        }
    )
    evidence["score_components"].append(posture_score)

    # Check DevSecOps profile for relevant stages
    profile_row = conn.execute(
        "SELECT active_stages FROM devsecops_profiles WHERE project_id = %s", (project_id,)
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
            evidence["checks"].append(
                {
                    "type": "devsecops_stages",
                    "active": active_relevant,
                    "total_relevant": len(relevant),
                    "score": round(stage_score, 3),
                }
            )
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
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (score_id, project_id, pillar, score, maturity, json.dumps(evidence["checks"]), now),
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
    pillar_weights = {p: config.get("pillars", {}).get(p, {}).get("weight", 1.0 / len(PILLARS)) for p in PILLARS}

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
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                score_id,
                project_id,
                "overall",
                overall_score,
                overall_maturity,
                json.dumps([{"pillar": r["pillar"], "score": r["score"]} for r in pillar_results]),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Identify weakest pillars
    sorted_pillars = sorted(pillar_results, key=lambda x: x["score"])
    weakest = sorted_pillars[:2] if len(sorted_pillars) >= 2 else sorted_pillars

    # DIC Canvas Synergy — emit ZIG gap events for below-threshold pillars (dsyn-emit-03)
    _ZIG_PILLAR_THRESHOLD = 0.70
    for r in pillar_results:
        if r["score"] < _ZIG_PILLAR_THRESHOLD:
            try:
                from tools.security.zig.event_emitter import emit_pillar_gap_detected
                emit_pillar_gap_detected(
                    pillar_name=r["pillar"],
                    current_score=round(r["score"] * 100, 1),
                    threshold=_ZIG_PILLAR_THRESHOLD * 100,
                    project_id=project_id,
                )
            except Exception:
                pass  # event emission never blocks scoring

    return {
        "project_id": project_id,
        "overall_score": overall_score,
        "overall_maturity": overall_maturity,
        "pillar_scores": {r["pillar"]: r["score"] for r in pillar_results},
        "pillar_details": pillar_results,
        "weakest_pillars": [{"pillar": w["pillar"], "score": w["score"]} for w in weakest],
        "recommendation": _generate_recommendation(overall_maturity, weakest),
    }


def run_scheduled_assessment(project_id: str, drift_threshold: float = 0.1) -> dict:
    """Run a scheduled ZTA assessment and detect score drift (G-18).

    Compares the current all-pillar score against the most recent previous
    assessment. If overall score drops by more than drift_threshold, a drift
    alert is emitted.

    Args:
        project_id: Project identifier.
        drift_threshold: Fractional drop (e.g. 0.1 = 10%) that triggers a drift alert.

    Returns:
        Dict with current score, previous score, drift, and alert flag.
    """
    from tools.logging.icdev_logger import get_logger as _gl
    _log = _gl("devsecops.zta_scheduler")

    # Run current assessment
    current = score_all_pillars(project_id)
    current_score = current.get("overall_score", 0.0)

    # Retrieve the previous assessment (the row before the one just inserted)
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT score, created_at
               FROM zta_maturity_scores
               WHERE project_id = %s AND pillar = 'overall'
               ORDER BY created_at DESC
               LIMIT 2""",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()

    previous_score: float | None = None
    if len(rows) >= 2:
        # rows[0] is the one just written, rows[1] is the previous
        previous_score = float(rows[1]["score"])

    drift = 0.0
    drift_alert = False
    if previous_score is not None:
        drift = previous_score - current_score  # positive = score dropped
        drift_alert = drift >= drift_threshold

    if drift_alert:
        _log.warning(
            "ZTA maturity drift detected: project=%s previous=%.3f current=%.3f drop=%.3f (threshold=%.3f)",
            project_id, previous_score, current_score, drift, drift_threshold,
        )
        # DIC Canvas Synergy — emit posture score drop event (dsyn-emit-03)
        try:
            from tools.security.zig.event_emitter import emit_posture_score_drop
            emit_posture_score_drop(
                pillar_name="overall",
                previous_score=round(previous_score * 100, 1),
                current_score=round(current_score * 100, 1),
                project_id=project_id,
            )
        except Exception:
            pass  # event emission never blocks assessment
    else:
        _log.info(
            "ZTA scheduled assessment: project=%s score=%.3f maturity=%s drift=%.3f",
            project_id, current_score, current.get("overall_maturity"), drift,
        )

    return {
        "project_id": project_id,
        "current_score": current_score,
        "previous_score": previous_score,
        "drift": round(drift, 4),
        "drift_alert": drift_alert,
        "drift_threshold": drift_threshold,
        "overall_maturity": current.get("overall_maturity"),
        "assessed_at": current.get("assessed_at", datetime.now(timezone.utc).isoformat()),
        "pillar_scores": current.get("pillar_scores", {}),
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
               WHERE project_id = %s AND created_at >= datetime('now', %s)
               ORDER BY created_at ASC""",
            (project_id, f"-{days} days"),
        ).fetchall()

        trend = {}
        for row in rows:
            pillar = row["pillar"]
            if pillar not in trend:
                trend[pillar] = []
            trend[pillar].append(
                {
                    "score": row["score"],
                    "maturity_level": row["maturity_level"],
                    "date": row["created_at"],
                }
            )

        return {
            "project_id": project_id,
            "period_days": days,
            "trends": trend,
            "data_points": len(rows),
        }
    finally:
        conn.close()


def get_latest_score(project_id: str | None = None) -> dict | None:
    """Read the most recent persisted ZTA maturity scores for a project.

    Read-only accessor consumed by the ZIG bridge
    (tools/security_canvas/zig_assessor.py::_try_zta_bridge). It never runs a
    new assessment — it returns whatever ``score_all_pillars`` last persisted
    to ``zta_maturity_scores``. The bridge calls this with no arguments and
    reads ``pillar_scores[<pillar_key>]``, so the returned pillar scores are
    the raw persisted values in the **0.0–1.0** range (the same scale the
    scorer stores; the CHECK constraint bounds ``score`` to [0.0, 1.0]).

    Args:
        project_id: Project to read. When None, the project of the most
            recently created score row is used (latest assessment wins).

    Returns:
        Dict of the shape::

            {
                "project_id": str,
                "overall_score": float (0.0-1.0),
                "overall_maturity": str,
                "pillar_scores": {pillar_key: float 0.0-1.0, ...},
                "assessed_at": str | None,   # ISO timestamp of latest row
            }

        or ``None`` when no assessment has ever been persisted.
    """
    conn = _get_db()
    try:
        if project_id is None:
            latest = conn.execute(
                "SELECT project_id FROM zta_maturity_scores "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not latest:
                return None
            project_id = latest["project_id"]

        rows = conn.execute(
            """SELECT pillar, score, maturity_level, created_at
               FROM zta_maturity_scores
               WHERE project_id = %s
               ORDER BY created_at ASC""",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return None

    # Later rows (ASC by created_at) overwrite earlier ones, so each pillar
    # ends up holding its most recent score.
    latest_by_pillar: dict[str, dict] = {}
    for r in rows:
        latest_by_pillar[r["pillar"]] = {
            "score": float(r["score"]) if r["score"] is not None else 0.0,
            "maturity_level": r["maturity_level"],
            "created_at": r["created_at"],
        }

    pillar_scores = {
        p: latest_by_pillar[p]["score"] for p in PILLARS if p in latest_by_pillar
    }
    overall = latest_by_pillar.get("overall", {})
    assessed_at = max(
        (v["created_at"] for v in latest_by_pillar.values() if v["created_at"]),
        default=None,
    )

    return {
        "project_id": project_id,
        "overall_score": overall.get("score", 0.0),
        "overall_maturity": overall.get("maturity_level", "traditional"),
        "pillar_scores": pillar_scores,
        "assessed_at": assessed_at,
    }


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
    parser.add_argument("--schedule", action="store_true", help="Run scheduled assessment with drift detection (G-18)")
    parser.add_argument("--drift-threshold", type=float, default=0.1, help="Drift alert threshold (default 0.1)")
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
    elif args.schedule:
        result = run_scheduled_assessment(args.project_id, drift_threshold=args.drift_threshold)
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
