#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
"""Multi-dimensional requirements readiness scorer.

Scores an intake session across 5 dimensions:
  completeness, clarity, feasibility, compliance, testability

Usage:
    python tools/requirements/readiness_scorer.py --session-id sess-abc --json
    python tools/requirements/readiness_scorer.py --session-id sess-abc --trend --json
"""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def log_event(**kwargs): return -1


def _get_connection(db_path=None):
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_weights():
    """Load readiness weights from config."""
    config_path = BASE_DIR / "args" / "ricoas_config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
                return cfg.get("ricoas", {}).get("readiness_weights", {})
        except ImportError:
            pass
    return {"completeness": 0.25, "clarity": 0.25, "feasibility": 0.20,
            "compliance": 0.15, "testability": 0.15}


def score_readiness(session_id: str, db_path=None) -> dict:
    """Calculate multi-dimensional readiness score for a session."""
    conn = _get_connection(db_path)

    session = conn.execute(
        "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not session:
        conn.close()
        raise ValueError(f"Session '{session_id}' not found.")

    session_data = dict(session)
    reqs = conn.execute(
        "SELECT * FROM intake_requirements WHERE session_id = ?", (session_id,)
    ).fetchall()
    reqs = [dict(r) for r in reqs]
    total = len(reqs)

    # --- Completeness ---
    types_present = set(r["requirement_type"] for r in reqs)
    expected_types = {"functional", "security", "interface", "data", "performance", "compliance"}
    type_coverage = len(types_present & expected_types) / len(expected_types)
    count_factor = min(1.0, total / 15.0)  # expect ~15 requirements minimum
    completeness = type_coverage * 0.6 + count_factor * 0.4

    # --- Clarity ---
    amb_count = session_data.get("ambiguity_count", 0)
    clarity = max(0.0, 1.0 - (amb_count / max(total, 1)))

    # --- Feasibility ---
    # Without architect review, estimate from constraints
    has_timeline = any("timeline" in (r.get("raw_text") or "").lower() for r in reqs)
    has_budget = any("budget" in (r.get("raw_text") or "").lower() for r in reqs)
    has_team = any("team" in (r.get("raw_text") or "").lower() for r in reqs)
    feasibility = 0.4 + (0.2 if has_timeline else 0) + (0.2 if has_budget else 0) + (0.2 if has_team else 0)

    # --- Compliance ---
    sec_reqs = sum(1 for r in reqs if r["requirement_type"] in ("security", "compliance"))
    compliance = min(1.0, sec_reqs / 5.0)  # expect ~5 security/compliance reqs

    # --- Testability ---
    with_criteria = sum(1 for r in reqs if r.get("acceptance_criteria"))
    testability = with_criteria / max(total, 1)

    weights = _load_weights()
    overall = (
        completeness * weights.get("completeness", 0.25)
        + clarity * weights.get("clarity", 0.25)
        + feasibility * weights.get("feasibility", 0.20)
        + compliance * weights.get("compliance", 0.15)
        + testability * weights.get("testability", 0.15)
    )

    # Get turn number for tracking
    last_turn = conn.execute(
        "SELECT MAX(turn_number) as mt FROM intake_conversation WHERE session_id = ?",
        (session_id,)
    ).fetchone()
    turn_num = last_turn["mt"] if last_turn else 0

    # Store score history
    conn.execute(
        """INSERT INTO readiness_scores
           (session_id, turn_number, overall_score, completeness, clarity,
            feasibility, compliance, testability, gap_count, ambiguity_count,
            requirement_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, turn_num, round(overall, 4), round(completeness, 4),
         round(clarity, 4), round(feasibility, 4), round(compliance, 4),
         round(testability, 4), session_data.get("gap_count", 0),
         amb_count, total),
    )

    # Update session
    conn.execute(
        """UPDATE intake_sessions
           SET readiness_score = ?, readiness_breakdown = ?, updated_at = ?
           WHERE id = ?""",
        (round(overall, 4),
         json.dumps({"completeness": round(completeness, 4), "clarity": round(clarity, 4),
                      "feasibility": round(feasibility, 4), "compliance": round(compliance, 4),
                      "testability": round(testability, 4)}),
         datetime.utcnow().isoformat(), session_id),
    )

    conn.commit()
    conn.close()

    if _HAS_AUDIT:
        log_event(
            event_type="readiness_scored",
            actor="icdev-requirements-analyst",
            action=f"Readiness scored for session {session_id}: {overall:.1%}",
            project_id=session_data.get("project_id"),
            details={"session_id": session_id, "overall": overall},
        )

    threshold = 0.7
    recommendation = "proceed" if overall >= threshold else (
        "gather_more" if overall >= 0.4 else "critical_gaps"
    )

    return {
        "status": "ok",
        "session_id": session_id,
        "overall_score": round(overall, 4),
        "dimensions": {
            "completeness": {"score": round(completeness, 4), "weight": weights.get("completeness", 0.25)},
            "clarity": {"score": round(clarity, 4), "weight": weights.get("clarity", 0.25)},
            "feasibility": {"score": round(feasibility, 4), "weight": weights.get("feasibility", 0.20)},
            "compliance": {"score": round(compliance, 4), "weight": weights.get("compliance", 0.15)},
            "testability": {"score": round(testability, 4), "weight": weights.get("testability", 0.15)},
        },
        "requirement_count": total,
        "types_present": list(types_present),
        "types_missing": list(expected_types - types_present),
        "recommendation": recommendation,
        "threshold": threshold,
    }


def get_score_trend(session_id: str, db_path=None) -> dict:
    """Get readiness score trend over time."""
    conn = _get_connection(db_path)
    scores = conn.execute(
        """SELECT turn_number, overall_score, completeness, clarity,
                  feasibility, compliance, testability, requirement_count, scored_at
           FROM readiness_scores
           WHERE session_id = ?
           ORDER BY scored_at""",
        (session_id,),
    ).fetchall()
    conn.close()

    return {
        "status": "ok",
        "session_id": session_id,
        "data_points": len(scores),
        "trend": [dict(s) for s in scores],
    }


def main():
    parser = argparse.ArgumentParser(description="ICDEV Readiness Scorer")
    parser.add_argument("--session-id", required=True, help="Intake session ID")
    parser.add_argument("--trend", action="store_true", help="Show score trend")
    parser.add_argument("--threshold", type=float, default=0.7, help="Minimum readiness")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        if args.trend:
            result = get_score_trend(args.session_id)
        else:
            result = score_readiness(args.session_id)

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if "overall_score" in result:
                print(f"Readiness: {result['overall_score']:.1%} ({result['recommendation']})")
                for dim, data in result.get("dimensions", {}).items():
                    print(f"  {dim}: {data['score']:.1%}")
            else:
                print(json.dumps(result, indent=2, default=str))
    except (ValueError, FileNotFoundError) as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"Error: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
