#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Scale-adaptive project complexity scorer (BMAD pattern).

Assesses project complexity from intake session data and recommends the
appropriate pipeline depth:

  - **Quick Flow**: Simple project, abbreviated workflow
    (< 5 requirements, no compliance frameworks, IL2, < 5 turns)
  - **Standard**: Moderate complexity, standard ICDEV pipeline
  - **Full Pipeline**: Complex project, all 4 tiers of validation needed

Usage:
    python tools/requirements/complexity_scorer.py --session-id sess-abc --json
    python tools/requirements/complexity_scorer.py --session-id sess-abc
"""

import argparse
import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Graceful import of audit logger
try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def log_event(**kwargs) -> int:
        return -1


def _get_connection(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Dimension scoring helpers
# ---------------------------------------------------------------------------

def _score_scope(requirement_count: int) -> tuple:
    """Score based on requirement count. Returns (score, detail)."""
    if requirement_count <= 3:
        score = 10
    elif requirement_count <= 8:
        score = 30
    elif requirement_count <= 15:
        score = 50
    elif requirement_count <= 30:
        score = 70
    else:
        score = 90
    return score, f"{requirement_count} requirements"


def _score_compliance_load(framework_count: int, impact_level: str) -> tuple:
    """Score based on framework count + impact level bonus."""
    if framework_count == 0:
        base = 10
    elif framework_count <= 2:
        base = 30
    elif framework_count <= 5:
        base = 60
    else:
        base = 90

    il_bonus_map = {"il2": 0, "il4": 15, "il5": 25, "il6": 35}
    il_key = (impact_level or "il2").lower().replace("-", "")
    il_bonus = il_bonus_map.get(il_key, 0)

    score = min(100, base + il_bonus)
    detail = f"{framework_count} frameworks, {impact_level or 'IL2'}"
    return score, detail


def _score_stakeholder_depth(turn_count: int, document_count: int) -> tuple:
    """Score based on conversation turns + document count bonus."""
    if turn_count <= 3:
        base = 10
    elif turn_count <= 8:
        base = 30
    elif turn_count <= 20:
        base = 55
    else:
        base = 80

    doc_bonus = min(20, document_count * 5)
    score = min(100, base + doc_bonus)
    detail = f"{turn_count} turns, {document_count} documents"
    return score, detail


def _score_requirement_diversity(requirement_types: list) -> tuple:
    """Score based on unique requirement type count."""
    unique_count = len(set(requirement_types))
    if unique_count <= 2:
        score = 20
    elif unique_count <= 4:
        score = 50
    else:
        score = 80
    detail = f"{unique_count} unique types"
    return score, detail


def _score_risk_profile(impact_level: str, requirement_texts: list) -> tuple:
    """Score based on IL + classified/secret keyword presence."""
    il_score_map = {"il2": 10, "il4": 30, "il5": 50, "il6": 80}
    il_key = (impact_level or "il2").lower().replace("-", "")
    base = il_score_map.get(il_key, 10)

    risk_keywords = ("classified", "secret")
    has_risk_keyword = any(
        kw in (text or "").lower()
        for text in requirement_texts
        for kw in risk_keywords
    )
    bonus = 20 if has_risk_keyword else 0

    score = min(100, base + bonus)
    flags = []
    if has_risk_keyword:
        flags.append("classified/secret keyword detected")
    detail = f"{impact_level or 'IL2'}" + (f" + {', '.join(flags)}" if flags else "")
    return score, detail


# ---------------------------------------------------------------------------
# Pipeline recommendation
# ---------------------------------------------------------------------------

def _build_recommendation(overall_score: float, complexity_level: str) -> dict:
    """Build pipeline recommendation from complexity level."""
    if complexity_level == "quick_flow":
        return {
            "pipeline_depth": "quick_flow",
            "skip_tiers": ["tier_3", "tier_4"],
            "estimated_phases": 2,
            "rationale": (
                "Low complexity project. Abbreviated workflow recommended: "
                "basic scaffolding and lightweight compliance checks are sufficient. "
                "Skip full simulation and multi-regime validation tiers."
            ),
        }
    elif complexity_level == "standard":
        return {
            "pipeline_depth": "standard",
            "skip_tiers": ["tier_4"],
            "estimated_phases": 3,
            "rationale": (
                "Moderate complexity project. Standard ICDEV pipeline recommended: "
                "full TDD workflow, compliance artifact generation, and boundary "
                "analysis. Tier 4 (full simulation/Monte Carlo) can be deferred."
            ),
        }
    else:  # full_pipeline
        return {
            "pipeline_depth": "full_pipeline",
            "skip_tiers": [],
            "estimated_phases": 4,
            "rationale": (
                "High complexity project. Full pipeline recommended: all 4 tiers "
                "of validation including boundary impact analysis, supply chain "
                "assessment, Digital Program Twin simulation, and multi-regime "
                "compliance. No tiers should be skipped."
            ),
        }


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def score_complexity(session_id: str, db_path=None) -> dict:
    """Assess project complexity from intake session data.

    Computes 5 dimension scores (0-100) and a weighted overall score,
    then maps to a complexity level (quick_flow / standard / full_pipeline)
    with a pipeline depth recommendation.

    Args:
        session_id: Intake session ID (e.g. "sess-abc123").
        db_path: Optional Path override for the ICDEV database.

    Returns:
        Dict with status, scores, complexity_level, dimensions,
        recommendation, and raw factors.

    Raises:
        ValueError: If session_id is not found.
        FileNotFoundError: If database does not exist.
    """
    conn = _get_connection(db_path)

    # ------------------------------------------------------------------
    # 1. Load session metadata
    # ------------------------------------------------------------------
    session = conn.execute(
        "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not session:
        conn.close()
        raise ValueError(f"Session '{session_id}' not found.")

    session_data = dict(session)

    # ------------------------------------------------------------------
    # 2. Load requirements
    # ------------------------------------------------------------------
    reqs = conn.execute(
        "SELECT * FROM intake_requirements WHERE session_id = ?", (session_id,)
    ).fetchall()
    reqs = [dict(r) for r in reqs]
    requirement_count = len(reqs)
    requirement_types = [r.get("requirement_type", "functional") for r in reqs]
    requirement_texts = [
        (r.get("raw_text") or "") + " " + (r.get("refined_text") or "")
        for r in reqs
    ]

    # ------------------------------------------------------------------
    # 3. Load conversation turns
    # ------------------------------------------------------------------
    turn_row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM intake_conversation WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    turn_count = turn_row["cnt"] if turn_row else 0

    # ------------------------------------------------------------------
    # 4. Load documents
    # ------------------------------------------------------------------
    doc_row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM intake_documents WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    document_count = doc_row["cnt"] if doc_row else 0

    conn.close()

    # ------------------------------------------------------------------
    # 5. Parse context_summary for classification / frameworks
    # ------------------------------------------------------------------
    context = {}
    try:
        context = json.loads(session_data.get("context_summary") or "{}")
    except (ValueError, TypeError):
        pass

    impact_level = session_data.get("impact_level") or context.get("impact_level") or "IL2"
    selected_frameworks = context.get("selected_frameworks", [])
    framework_count = len(selected_frameworks)

    # ------------------------------------------------------------------
    # 6. Score each dimension (0-100)
    # ------------------------------------------------------------------
    scope_score, scope_detail = _score_scope(requirement_count)
    compliance_score, compliance_detail = _score_compliance_load(framework_count, impact_level)
    stakeholder_score, stakeholder_detail = _score_stakeholder_depth(turn_count, document_count)
    diversity_score, diversity_detail = _score_requirement_diversity(requirement_types)
    risk_score, risk_detail = _score_risk_profile(impact_level, requirement_texts)

    # ------------------------------------------------------------------
    # 7. Weighted overall score
    # ------------------------------------------------------------------
    overall_score = (
        scope_score * 0.25
        + compliance_score * 0.25
        + stakeholder_score * 0.15
        + diversity_score * 0.15
        + risk_score * 0.20
    )
    overall_score = round(overall_score, 2)

    # ------------------------------------------------------------------
    # 8. Map to complexity level
    # ------------------------------------------------------------------
    if overall_score < 30:
        complexity_level = "quick_flow"
    elif overall_score <= 65:
        complexity_level = "standard"
    else:
        complexity_level = "full_pipeline"

    recommendation = _build_recommendation(overall_score, complexity_level)

    # ------------------------------------------------------------------
    # 9. Audit event
    # ------------------------------------------------------------------
    if _HAS_AUDIT:
        log_event(
            event_type="complexity_scored",
            actor="icdev-requirements-analyst",
            action=f"Complexity scored for session {session_id}: {overall_score} ({complexity_level})",
            project_id=session_data.get("project_id"),
            details={
                "session_id": session_id,
                "overall_score": overall_score,
                "complexity_level": complexity_level,
            },
        )

    # ------------------------------------------------------------------
    # 10. Build result
    # ------------------------------------------------------------------
    return {
        "status": "ok",
        "session_id": session_id,
        "overall_score": overall_score,
        "complexity_level": complexity_level,
        "dimensions": {
            "scope": {"score": scope_score, "detail": scope_detail},
            "compliance_load": {"score": compliance_score, "detail": compliance_detail},
            "stakeholder_depth": {"score": stakeholder_score, "detail": stakeholder_detail},
            "requirement_diversity": {"score": diversity_score, "detail": diversity_detail},
            "risk_profile": {"score": risk_score, "detail": risk_detail},
        },
        "recommendation": recommendation,
        "factors": {
            "requirement_count": requirement_count,
            "framework_count": framework_count,
            "impact_level": impact_level,
            "turn_count": turn_count,
            "document_count": document_count,
            "requirement_types": sorted(set(requirement_types)),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Scale-Adaptive Complexity Scorer (BMAD pattern)"
    )
    parser.add_argument("--session-id", required=True, help="Intake session ID")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        result = score_complexity(args.session_id)

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            level = result["complexity_level"]
            score = result["overall_score"]
            rec = result["recommendation"]
            print(f"Complexity: {score:.1f}/100 -> {level}")
            print()
            print("Dimensions:")
            for dim, data in result["dimensions"].items():
                print(f"  {dim:25s} {data['score']:3d}  ({data['detail']})")
            print()
            print(f"Pipeline depth : {rec['pipeline_depth']}")
            print(f"Estimated phases: {rec['estimated_phases']}")
            if rec["skip_tiers"]:
                print(f"Skip tiers     : {', '.join(rec['skip_tiers'])}")
            print(f"Rationale      : {rec['rationale']}")
    except (ValueError, FileNotFoundError) as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"Error: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
