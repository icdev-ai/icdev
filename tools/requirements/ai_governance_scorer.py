#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
"""AI Governance readiness sub-scorer (D323).

Checks 6 governance components against DB and returns a weighted score (0.0–1.0)
with a gap list.  Called by readiness_scorer.py as the 7th readiness dimension.

Components (from args/ai_governance_config.yaml):
  - inventory_registered   (0.20)
  - model_cards_present    (0.15)
  - oversight_plan_exists  (0.20)
  - impact_assessment_done (0.20)
  - caio_designated        (0.10)
  - transparency_frameworks_selected (0.15)

Usage:
    from tools.requirements.ai_governance_scorer import score_ai_governance_readiness
    result = score_ai_governance_readiness(project_id, conn=conn)
"""

import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

DEFAULT_WEIGHTS = {
    "inventory_registered": 0.20,
    "model_cards_present": 0.15,
    "oversight_plan_exists": 0.20,
    "impact_assessment_done": 0.20,
    "caio_designated": 0.10,
    "transparency_frameworks_selected": 0.15,
}


def _load_gov_config() -> dict:
    """Load AI governance scoring config from YAML."""
    config_path = BASE_DIR / "args" / "ai_governance_config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("ai_governance", {}).get("readiness", {}).get("scoring", DEFAULT_WEIGHTS)
        except ImportError:
            pass
    return dict(DEFAULT_WEIGHTS)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Check if a table exists in the database."""
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return (row[0] if isinstance(row, (tuple, list)) else row["cnt"]) > 0


def score_ai_governance_readiness(project_id: str, conn: sqlite3.Connection = None,
                                   db_path=None) -> dict:
    """Score AI governance readiness for a project.

    Returns:
        dict with keys: score (float 0.0-1.0), components (dict), gaps (list)
    """
    close_conn = False
    if conn is None:
        path = db_path or (BASE_DIR / "data" / "icdev.db")
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        close_conn = True

    weights = _load_gov_config()
    components = {}
    gaps = []

    # 1. inventory_registered — ai_use_case_inventory has entries
    if _table_exists(conn, "ai_use_case_inventory"):
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM ai_use_case_inventory WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        cnt = count[0] if isinstance(count, (tuple, list)) else count["cnt"]
        components["inventory_registered"] = 1.0 if cnt > 0 else 0.0
    else:
        components["inventory_registered"] = 0.0

    if components["inventory_registered"] == 0.0:
        gaps.append({
            "component": "inventory_registered",
            "message": "No AI/ML systems registered in inventory",
            "remediation": "Register AI systems via /icdev-transparency inventory",
        })

    # 2. model_cards_present — ai_model_cards has entries
    if _table_exists(conn, "ai_model_cards"):
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM ai_model_cards WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        cnt = count[0] if isinstance(count, (tuple, list)) else count["cnt"]
        components["model_cards_present"] = 1.0 if cnt > 0 else 0.0
    else:
        components["model_cards_present"] = 0.0

    if components["model_cards_present"] == 0.0:
        gaps.append({
            "component": "model_cards_present",
            "message": "No model cards documented",
            "remediation": "Create model cards via /icdev-transparency model-card",
        })

    # 3. oversight_plan_exists — ai_oversight_plans has entries
    if _table_exists(conn, "ai_oversight_plans"):
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM ai_oversight_plans WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        cnt = count[0] if isinstance(count, (tuple, list)) else count["cnt"]
        components["oversight_plan_exists"] = 1.0 if cnt > 0 else 0.0
    else:
        components["oversight_plan_exists"] = 0.0

    if components["oversight_plan_exists"] == 0.0:
        gaps.append({
            "component": "oversight_plan_exists",
            "message": "No human oversight plan registered",
            "remediation": "Register oversight plan via /icdev-accountability",
        })

    # 4. impact_assessment_done — ai_ethics_reviews with review_type='impact_assessment'
    if _table_exists(conn, "ai_ethics_reviews"):
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM ai_ethics_reviews "
            "WHERE project_id = ? AND review_type = 'impact_assessment'",
            (project_id,),
        ).fetchone()
        cnt = count[0] if isinstance(count, (tuple, list)) else count["cnt"]
        components["impact_assessment_done"] = 1.0 if cnt > 0 else 0.0
    else:
        components["impact_assessment_done"] = 0.0

    if components["impact_assessment_done"] == 0.0:
        gaps.append({
            "component": "impact_assessment_done",
            "message": "No algorithmic impact assessment completed",
            "remediation": "Run impact assessment via /icdev-accountability",
        })

    # 5. caio_designated — ai_caio_registry has entries
    if _table_exists(conn, "ai_caio_registry"):
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM ai_caio_registry WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        cnt = count[0] if isinstance(count, (tuple, list)) else count["cnt"]
        components["caio_designated"] = 1.0 if cnt > 0 else 0.0
    else:
        components["caio_designated"] = 0.0

    if components["caio_designated"] == 0.0:
        gaps.append({
            "component": "caio_designated",
            "message": "No Chief AI Officer (CAIO) designated",
            "remediation": "Designate CAIO via /icdev-accountability",
        })

    # 6. transparency_frameworks_selected — check framework_applicability for AI frameworks
    if _table_exists(conn, "framework_applicability"):
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM framework_applicability "
            "WHERE project_id = ? AND framework_id IN "
            "('nist_ai_rmf', 'iso42001', 'owasp_llm', 'atlas')",
            (project_id,),
        ).fetchone()
        cnt = count[0] if isinstance(count, (tuple, list)) else count["cnt"]
        components["transparency_frameworks_selected"] = 1.0 if cnt > 0 else 0.0
    else:
        components["transparency_frameworks_selected"] = 0.0

    if components["transparency_frameworks_selected"] == 0.0:
        gaps.append({
            "component": "transparency_frameworks_selected",
            "message": "No AI transparency/governance framework selected",
            "remediation": "Select frameworks (NIST AI RMF, ISO 42001, etc.) via compliance detector",
        })

    # Calculate weighted score
    score = sum(
        components.get(comp, 0.0) * weights.get(comp, 0.0)
        for comp in weights
    )
    score = round(min(1.0, max(0.0, score)), 4)

    if close_conn:
        conn.close()

    return {
        "score": score,
        "components": components,
        "gaps": gaps,
        "gap_count": len(gaps),
        "project_id": project_id,
    }
