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
from tools.db.storage import get_connection
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


_AI_KEYWORDS = {
    "ai", "artificial intelligence", "machine learning", "ml", "deep learning",
    "neural network", "llm", "large language model", "generative ai", "genai",
    "model inference", "algorithmic decision", "predictive model", "classification model",
    "recommendation engine", "natural language processing", "computer vision",
}


def _project_mentions_ai(project_id: str, conn) -> bool:
    """Scan project requirements and session context for AI/ML keywords.

    Returns True when tables don't exist (conservative: assume AI-relevant
    when we can't determine otherwise).
    """
    try:
        # Check requirements text for any session linked to this project
        rows = conn.execute(
            "SELECT raw_text FROM intake_requirements WHERE session_id IN ("
            "SELECT id FROM intake_sessions WHERE project_id = %s)",
            (project_id,),
        ).fetchall()
        all_text = " ".join(
            (r[0] if isinstance(r, (tuple, list)) else (dict(r).get("raw_text") or "")) for r in rows
        ).lower()
        if any(kw in all_text for kw in _AI_KEYWORDS):
            return True

        # Check session context_summary
        ctx_rows = conn.execute(
            "SELECT context_summary FROM intake_sessions WHERE project_id = %s", (project_id,)
        ).fetchall()
        for row in ctx_rows:
            ctx = row[0] if isinstance(row, (tuple, list)) else (dict(row).get("context_summary") or "")
            if ctx:
                try:
                    parsed = json.loads(ctx)
                    goal = (parsed.get("goal") or "").lower()
                    if any(kw in goal for kw in _AI_KEYWORDS):
                        return True
                except (ValueError, TypeError):
                    pass
                if any(kw in ctx.lower() for kw in _AI_KEYWORDS):
                    return True
        return False
    except Exception:  # noqa: BLE001 — intake tables absent → conservative assume relevant
        return True


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists in the database (SQLite or PostgreSQL)."""
    # Backend-aware, translation-independent existence probe (pgrt-sweep-06).
    # Replaces an env-var-guarded branch that could misfire if the connection's
    # actual backend diverged from ICDEV_STORAGE_BACKEND; the helper reads the
    # connection itself.
    from tools.db.storage import table_exists
    return table_exists(conn, table_name)


def score_ai_governance_readiness(project_id: str, conn: sqlite3.Connection = None, db_path=None) -> dict:
    """Score AI governance readiness for a project.

    Returns:
        dict with keys: score (float 0.0-1.0), components (dict), gaps (list)
    """
    close_conn = False
    if conn is None:
        db_path or (BASE_DIR / "data" / "icdev.db")
        conn = get_connection()
        close_conn = True

    # Check if AI/ML is relevant to this project
    ai_relevant = _project_mentions_ai(project_id, conn)
    if not ai_relevant:
        return {
            "score": 1.0,
            "components": {},
            "gaps": [],
            "gap_count": 0,
            "project_id": project_id,
            "note": "AI governance not applicable — no AI/ML detected in project scope",
        }

    weights = _load_gov_config()
    components = {}
    gaps = []

    # 1. inventory_registered — ai_use_case_inventory has entries
    if _table_exists(conn, "ai_use_case_inventory"):
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM ai_use_case_inventory WHERE project_id = %s",
            (project_id,),
        ).fetchone()
        cnt = count[0] if isinstance(count, (tuple, list)) else count["cnt"]
        components["inventory_registered"] = 1.0 if cnt > 0 else 0.0
    else:
        components["inventory_registered"] = 0.0

    if components["inventory_registered"] == 0.0:
        gaps.append(
            {
                "component": "inventory_registered",
                "message": "No AI/ML systems registered in inventory",
                "remediation": "Register AI systems via /icdev-transparency inventory",
            }
        )

    # 2. model_cards_present — ai_model_cards has entries
    if _table_exists(conn, "ai_model_cards"):
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM ai_model_cards WHERE project_id = %s",
            (project_id,),
        ).fetchone()
        cnt = count[0] if isinstance(count, (tuple, list)) else count["cnt"]
        components["model_cards_present"] = 1.0 if cnt > 0 else 0.0
    else:
        components["model_cards_present"] = 0.0

    if components["model_cards_present"] == 0.0:
        gaps.append(
            {
                "component": "model_cards_present",
                "message": "No model cards documented",
                "remediation": "Create model cards via /icdev-transparency model-card",
            }
        )

    # 3. oversight_plan_exists — ai_oversight_plans has entries
    if _table_exists(conn, "ai_oversight_plans"):
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM ai_oversight_plans WHERE project_id = %s",
            (project_id,),
        ).fetchone()
        cnt = count[0] if isinstance(count, (tuple, list)) else count["cnt"]
        components["oversight_plan_exists"] = 1.0 if cnt > 0 else 0.0
    else:
        components["oversight_plan_exists"] = 0.0

    if components["oversight_plan_exists"] == 0.0:
        gaps.append(
            {
                "component": "oversight_plan_exists",
                "message": "No human oversight plan registered",
                "remediation": "Register oversight plan via /icdev-accountability",
            }
        )

    # 4. impact_assessment_done — ai_ethics_reviews with review_type='impact_assessment'
    if _table_exists(conn, "ai_ethics_reviews"):
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM ai_ethics_reviews WHERE project_id = %s AND review_type = 'impact_assessment'",
            (project_id,),
        ).fetchone()
        cnt = count[0] if isinstance(count, (tuple, list)) else count["cnt"]
        components["impact_assessment_done"] = 1.0 if cnt > 0 else 0.0
    else:
        components["impact_assessment_done"] = 0.0

    if components["impact_assessment_done"] == 0.0:
        gaps.append(
            {
                "component": "impact_assessment_done",
                "message": "No algorithmic impact assessment completed",
                "remediation": "Run impact assessment via /icdev-accountability",
            }
        )

    # 5. caio_designated — ai_caio_registry has entries
    if _table_exists(conn, "ai_caio_registry"):
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM ai_caio_registry WHERE project_id = %s",
            (project_id,),
        ).fetchone()
        cnt = count[0] if isinstance(count, (tuple, list)) else count["cnt"]
        components["caio_designated"] = 1.0 if cnt > 0 else 0.0
    else:
        components["caio_designated"] = 0.0

    if components["caio_designated"] == 0.0:
        gaps.append(
            {
                "component": "caio_designated",
                "message": "No Chief AI Officer (CAIO) designated",
                "remediation": "Designate CAIO via /icdev-accountability",
            }
        )

    # 6. transparency_frameworks_selected — check framework_applicability for AI frameworks
    if _table_exists(conn, "framework_applicability"):
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM framework_applicability "
            "WHERE project_id = %s AND framework_id IN "
            "('nist_ai_rmf', 'iso42001', 'owasp_llm', 'atlas')",
            (project_id,),
        ).fetchone()
        cnt = count[0] if isinstance(count, (tuple, list)) else count["cnt"]
        components["transparency_frameworks_selected"] = 1.0 if cnt > 0 else 0.0
    else:
        components["transparency_frameworks_selected"] = 0.0

    if components["transparency_frameworks_selected"] == 0.0:
        gaps.append(
            {
                "component": "transparency_frameworks_selected",
                "message": "No AI transparency/governance framework selected",
                "remediation": "Select frameworks (NIST AI RMF, ISO 42001, etc.) via compliance detector",
            }
        )

    # Calculate weighted score
    score = sum(components.get(comp, 0.0) * weights.get(comp, 0.0) for comp in weights)
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
