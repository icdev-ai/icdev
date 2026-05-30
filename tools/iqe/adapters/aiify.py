# CUI // SP-CTI
"""IQE AI-ify collection adapters.

Importing this module registers three collections on the module-level Executor:
  aiify.opportunities — AI-ify opportunities with scores (aiify_opportunities JOIN aiify_scores)
  aiify.scans         — AI-ify scan records (aiify_scans)
  aiify.roadmaps      — AI-ify roadmap records (aiify_roadmaps)
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    return conn


def _canvas_conn():
    """AI-ify canvas tables live in the canvas DB, not the main ICDEV DB."""
    from tools.aiify.db.init_db import get_connection as _aiify_conn  # noqa: PLC0415
    return _aiify_conn()


def opportunities_adapter(conn: Any) -> list[dict]:
    """Return rows from aiify_opportunities joined with aiify_scores.

    Reads from the canvas DB (ignores any main-DB conn passed by IQE). Selects
    the canonical opportunity_id key and the new verdict/category columns.
    """
    canvas = _canvas_conn()
    try:
        cur = canvas.execute(
            "SELECT o.opportunity_id, o.scan_id, o.module_path, o.function_name, o.language, "
            "o.pattern_type, o.ai_paradigm, o.il_recommended_model, "
            "s.composite_score, s.value_score, s.feasibility_score, s.risk_score, "
            "s.verdict, s.ai_readiness, s.category "
            "FROM aiify_opportunities o "
            "LEFT JOIN aiify_scores s ON s.opportunity_id = o.opportunity_id "
            "ORDER BY s.composite_score DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        canvas.close()


def scans_adapter(conn: Any) -> list[dict]:
    """Return rows from aiify_scans (canvas DB)."""
    canvas = _canvas_conn()
    try:
        cur = canvas.execute(
            "SELECT scan_id, input_type, input_ref, language_profile, total_files, "
            "total_loc, status, overall_verdict, overall_ai_readiness, "
            "created_at, completed_at "
            "FROM aiify_scans ORDER BY created_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        canvas.close()


def roadmaps_adapter(conn: Any) -> list[dict]:
    """Return rows from aiify_roadmaps (canvas DB)."""
    canvas = _canvas_conn()
    try:
        cur = canvas.execute(
            "SELECT scan_id, roadmap_id, title, total_effort_days, created_at "
            "FROM aiify_roadmaps ORDER BY created_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        canvas.close()


register_collection("aiify.opportunities", opportunities_adapter)
register_collection("aiify.scans", scans_adapter)
register_collection("aiify.roadmaps", roadmaps_adapter)


def opportunities_with_innovation_adapter(conn: Any) -> list[dict]:
    """AI-ify opportunities enriched with matching innovation signals (Python join across DBs)."""
    from tools.aiify.db.init_db import get_connection as _aiify_conn

    # Load AI-ify opportunities from the canvas DB
    aiify = _aiify_conn()
    try:
        cur = aiify.execute(
            "SELECT o.opportunity_id, o.scan_id, o.module_path, o.function_name, "
            "o.pattern_type, o.ai_paradigm, o.il_recommended_model, "
            "s.composite_score "
            "FROM aiify_opportunities o "
            "LEFT JOIN aiify_scores s ON s.opportunity_id = o.opportunity_id "
            "ORDER BY s.composite_score DESC"
        )
        cols = [d[0] for d in cur.description]
        opps = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        aiify.close()

    # Load innovation signals from main ICDEV DB (best-effort)
    signal_map: dict[str, str] = {}
    try:
        icdev = _conn(None)
        try:
            rows = icdev.execute(
                "SELECT category, title FROM innovation_signals "
                "WHERE category IN ('ai_tooling','agentic','external_framework_analysis','aiify_opportunity') "
                "AND innovation_score >= 0.60 ORDER BY innovation_score DESC LIMIT 100"
            ).fetchall()
            for r in rows:
                signal_map[r[0]] = r[1]
        except Exception:
            pass
        finally:
            icdev.close()
    except Exception:
        pass

    # Python join: attach innovation signal title where pattern category matches
    _PATTERN_TO_CATEGORY = {
        "hardcoded_threshold": "ai_tooling",
        "nested_conditionals": "ai_tooling",
        "string_template_rendering": "agentic",
        "scheduled_cron": "agentic",
        "keyword_list_search": "external_framework_analysis",
        "large_rule_table": "aiify_opportunity",
    }
    for opp in opps:
        cat = _PATTERN_TO_CATEGORY.get(opp.get("pattern_type", ""), "")
        opp["matched_innovation_signal"] = signal_map.get(cat, None)

    return opps


def roadmap_with_research_adapter(conn: Any) -> list[dict]:
    """AI-ify roadmap phases enriched with research regulatory milestones (Python join)."""
    from tools.aiify.db.init_db import get_connection as _aiify_conn
    import json as _json

    aiify = _aiify_conn()
    try:
        cur = aiify.execute(
            "SELECT roadmap_id, title, phases, total_effort_days, created_at FROM aiify_roadmaps ORDER BY created_at DESC"
        )
        cols = [d[0] for d in cur.description]
        roadmaps = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        aiify.close()

    # Load research regulatory map (best-effort)
    reg_rows: list[dict] = []
    try:
        icdev = _conn(None)
        try:
            rows = icdev.execute(
                "SELECT regulation_name, deadline, requirements FROM research_regulatory_map "
                "WHERE LOWER(requirements) LIKE '%ai%' OR LOWER(requirements) LIKE '%automat%' LIMIT 20"
            ).fetchall()
            reg_rows = [{"regulation_name": r[0], "deadline": r[1], "requirements": r[2]} for r in rows]
        except Exception:
            pass
        finally:
            icdev.close()
    except Exception:
        pass

    results = []
    for rm in roadmaps:
        phases_raw = rm.get("phases")
        try:
            phases = _json.loads(phases_raw) if isinstance(phases_raw, str) else (phases_raw or [])
        except Exception:
            phases = []
        for ph in phases:
            results.append({
                "roadmap_id": rm["roadmap_id"],
                "roadmap_title": rm["title"],
                "phase_label": ph.get("label", ""),
                "phase_effort_days": ph.get("total_effort_days", 0),
                "phase_opportunity_count": ph.get("count", 0),
                "regulatory_deadlines": [r["regulation_name"] for r in reg_rows if r.get("deadline")],
                "created_at": rm["created_at"],
            })
    return results


register_collection("aiify.opportunities_with_innovation", opportunities_with_innovation_adapter)
register_collection("aiify.roadmap_with_research", roadmap_with_research_adapter)
