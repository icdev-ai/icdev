# CUI // SP-CTI
"""IQE AI Augmentation collection adapters.

Importing this module registers three collections on the module-level Executor:
  ai_augmentation.opportunities — AAC opportunities with scores (aac_opportunities JOIN aac_scores)
  ai_augmentation.scans         — AAC scan records (aac_scans)
  ai_augmentation.roadmaps      — AAC roadmap records (aac_roadmaps)
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    return conn


def opportunities_adapter(conn: Any) -> list[dict]:
    """Return rows from aac_opportunities joined with aac_scores."""
    conn = _conn(conn)
    cur = conn.execute(
        "SELECT o.id, o.scan_id, o.module_path, o.function_name, o.language, "
        "o.pattern_type, o.ai_paradigm, o.il_recommended_model, "
        "s.composite_score, s.value_score, s.feasibility_score, "
        "s.risk_score, s.effort_days "
        "FROM aac_opportunities o "
        "LEFT JOIN aac_scores s ON s.opportunity_id = o.id "
        "ORDER BY s.composite_score DESC"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def scans_adapter(conn: Any) -> list[dict]:
    """Return rows from aac_scans."""
    conn = _conn(conn)
    cur = conn.execute(
        "SELECT scan_id, input_type, input_ref, language_profile, "
        "total_files, total_loc, status, created_at, completed_at "
        "FROM aac_scans ORDER BY created_at DESC"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def roadmaps_adapter(conn: Any) -> list[dict]:
    """Return rows from aac_roadmaps."""
    conn = _conn(conn)
    cur = conn.execute(
        "SELECT scan_id, roadmap_id, title, total_effort_days, created_at "
        "FROM aac_roadmaps ORDER BY created_at DESC"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


register_collection("ai_augmentation.opportunities", opportunities_adapter)
register_collection("ai_augmentation.scans", scans_adapter)
register_collection("ai_augmentation.roadmaps", roadmaps_adapter)


def opportunities_with_innovation_adapter(conn: Any) -> list[dict]:
    """AAC opportunities enriched with matching innovation signals (Python join across DBs)."""
    from tools.ai_augmentation.db.init_db import get_connection as _aac_conn

    # Load AAC opportunities from the canvas DB
    aac = _aac_conn()
    try:
        cur = aac.execute(
            "SELECT o.opportunity_id, o.scan_id, o.module_path, o.function_name, "
            "o.pattern_type, o.ai_paradigm, o.il_recommended_model, "
            "s.composite_score "
            "FROM aac_opportunities o "
            "LEFT JOIN aac_scores s ON s.opportunity_id = o.opportunity_id "
            "ORDER BY s.composite_score DESC"
        )
        cols = [d[0] for d in cur.description]
        opps = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        aac.close()

    # Load innovation signals from main ICDEV DB (best-effort)
    signal_map: dict[str, str] = {}
    try:
        icdev = _conn(None)
        try:
            rows = icdev.execute(
                "SELECT category, title FROM innovation_signals "
                "WHERE category IN ('ai_tooling','agentic','external_framework_analysis','ai_augmentation_opportunity') "
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
        "large_rule_table": "ai_augmentation_opportunity",
    }
    for opp in opps:
        cat = _PATTERN_TO_CATEGORY.get(opp.get("pattern_type", ""), "")
        opp["matched_innovation_signal"] = signal_map.get(cat, None)

    return opps


def roadmap_with_research_adapter(conn: Any) -> list[dict]:
    """AAC roadmap phases enriched with research regulatory milestones (Python join)."""
    from tools.ai_augmentation.db.init_db import get_connection as _aac_conn
    import json as _json

    aac = _aac_conn()
    try:
        cur = aac.execute(
            "SELECT roadmap_id, title, phases, total_effort_days, created_at FROM aac_roadmaps ORDER BY created_at DESC"
        )
        cols = [d[0] for d in cur.description]
        roadmaps = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        aac.close()

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


register_collection("ai_augmentation.opportunities_with_innovation", opportunities_with_innovation_adapter)
register_collection("ai_augmentation.roadmap_with_research", roadmap_with_research_adapter)
