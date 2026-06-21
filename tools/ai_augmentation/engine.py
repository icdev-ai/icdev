# CUI // SP-CTI
"""AI Augmentation Canvas (AAC) — Pipeline Engine.

Public API:
    run_scan(input_type, input_ref, scan_context=None) -> dict

Orchestrates the full AAC pipeline:
  1. init_db()         — ensure schema is ready
  2. aac_scans INSERT  — status='running'
  3. detect_patterns() — Semgrep or AST fallback
  4. aac_opportunities INSERT + score_opportunity() per hit
  5. aac_scores INSERT
  6. generate_roadmap() -> aac_roadmaps INSERT
  7. promote top-5 opportunities -> kanban_tasks + aac_audit_log
  8. aac_scans UPDATE  — status='completed'
  9. aac_audit_log     — scan_started / scan_completed events
"""
from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime, timezone
from typing import Any

from tools.ai_augmentation.agent_readiness import run_readiness_check
from tools.ai_augmentation.batch_processor import is_batch_eligible, run_batch_scan
from tools.ai_augmentation.capability_mapper import map_capabilities
from tools.ai_augmentation.db.init_db import get_connection, init_db
from tools.ai_augmentation.opportunity_scorer import score_opportunity
from tools.ai_augmentation.pattern_classifier import detect_patterns
from tools.ai_augmentation.roadmap_generator import generate_roadmap

# Maps each pattern type to a valid AI paradigm (constants.AI_PARADIGMS)
_PATTERN_TO_PARADIGM: dict[str, str] = {
    "nested_conditionals": "ml_classifier",
    "regex_user_input": "nlp_extractor",
    "string_template_rendering": "llm_generation",
    "scheduled_cron": "agentic_trigger",
    "hardcoded_threshold": "anomaly_detection",
    "db_render_notify_chain": "llm_generation",
    "keyword_list_search": "embedding_search",
    "large_rule_table": "decision_agent",
}

_PARADIGM_TO_MODEL: dict[str, str] = {
    "ml_classifier": "claude-haiku-4-5-20251001",
    "nlp_extractor": "claude-haiku-4-5-20251001",
    "llm_generation": "claude-sonnet-4-6",
    "agentic_trigger": "claude-sonnet-4-6",
    "anomaly_detection": "claude-haiku-4-5-20251001",
    "embedding_search": "claude-haiku-4-5-20251001",
    "decision_agent": "claude-opus-4-7",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dump(value: Any) -> Any:
    backend = os.environ.get(
        "AAC_STORAGE_BACKEND",
        os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
    ).lower()
    return value if backend == "postgresql" else json.dumps(value)


def _exec(conn: Any, sql: str, params: tuple) -> Any:
    try:
        return conn.execute(sql, params)
    except Exception:
        return conn.execute(sql.replace("?", "%s"), params)


def _phase_label(score: float) -> str:
    if score >= 0.7:
        return "P1 — Quick Wins"
    if score >= 0.5:
        return "P2 — Core Modernization"
    if score >= 0.3:
        return "P3 — Long-Horizon Investments"
    return "Unclassified"


def _promote_top_opportunities(
    opp_rows: list[dict],
    score_rows: list[dict],
    scan_id: int,
    roadmap_id: str,
) -> int:
    """Promote top 5 opportunities (by composite_score) to kanban_tasks.

    Uses the main ICDEV get_connection() for kanban_tasks and the AAC
    get_connection() for aac_audit_log. Skips tasks whose id already exists.
    Returns the count of tasks actually inserted.
    """
    from tools.db.storage import get_connection as _icdev_get_connection

    score_index: dict[int, dict] = {int(s["opportunity_id"]): s for s in score_rows}
    enriched: list[dict] = []
    for opp in opp_rows:
        opp_id = int(opp["opportunity_id"])
        score = score_index.get(opp_id, {})
        enriched.append({
            "opportunity_id": opp_id,
            "pattern_type": opp.get("pattern_type", ""),
            "module_path": opp.get("module_path", ""),
            "function_name": opp.get("function_name", "<unknown>"),
            "ai_paradigm": opp.get("ai_paradigm", "llm_generation"),
            "il_recommended_model": opp.get("il_recommended_model", ""),
            "composite_score": float(score.get("composite_score", 0.0)),
            "value_score": float(score.get("value_score", 0.0)),
            "feasibility_score": float(score.get("feasibility_score", 0.0)),
            "risk_score": float(score.get("risk_score", 0.0)),
        })

    enriched.sort(key=lambda x: x["composite_score"], reverse=True)
    top5 = enriched[:5]
    if not top5:
        return 0

    promoted_opps: list[dict] = []
    icdev_conn = _icdev_get_connection()
    try:
        for opp in top5:
            opp_id = opp["opportunity_id"]
            task_id = f"aac-opp-{str(opp_id)[:8]}"

            existing = _exec(
                icdev_conn,
                "SELECT id FROM kanban_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if existing:
                continue

            priority = "high" if opp["composite_score"] >= 0.7 else "medium"
            title = (
                f"[AI Opp] {opp['pattern_type']} in "
                f"{opp['module_path']}:{opp['function_name']} "
                f"-> {opp['ai_paradigm']}"
            )
            description = json.dumps(
                {
                    "opportunity_id": opp_id,
                    "scan_id": scan_id,
                    "roadmap_id": roadmap_id,
                    "pattern_type": opp["pattern_type"],
                    "module_path": opp["module_path"],
                    "function_name": opp["function_name"],
                    "ai_paradigm": opp["ai_paradigm"],
                    "scores": {
                        "composite": opp["composite_score"],
                        "value": opp["value_score"],
                        "feasibility": opp["feasibility_score"],
                        "risk": opp["risk_score"],
                    },
                    "roadmap_phase": _phase_label(opp["composite_score"]),
                    "model_recommendation": opp["il_recommended_model"],
                },
                indent=2,
            )
            _exec(
                icdev_conn,
                "INSERT INTO kanban_tasks "
                "(id, title, description, task_type, priority, status, executor_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, title, description, "build", priority, "suggested", "claude_cli"),
            )
            icdev_conn.commit()
            promoted_opps.append({**opp, "task_id": task_id})
    finally:
        icdev_conn.close()

    if not promoted_opps:
        return 0

    # Write one audit entry per promoted task (separate AAC connection)
    aac_conn = get_connection()
    try:
        for opp in promoted_opps:
            _exec(
                aac_conn,
                "INSERT INTO aac_audit_log (event_type, scan_id, actor, detail) VALUES (?, ?, ?, ?)",
                (
                    "kanban_promoted",
                    scan_id,
                    "system",
                    _dump({
                        "task_id": opp["task_id"],
                        "opportunity_id": opp["opportunity_id"],
                        "composite_score": opp["composite_score"],
                        "roadmap_id": roadmap_id,
                    }),
                ),
            )
        aac_conn.commit()
    finally:
        aac_conn.close()

    return len(promoted_opps)


def _count_source(path: str) -> tuple[int, int]:
    p = pathlib.Path(path)
    total_files = total_loc = 0
    items = [p] if p.is_file() else list(p.rglob("*"))
    for f in items:
        if f.is_file():
            total_files += 1
            try:
                total_loc += len(f.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                pass
    return total_files, total_loc


def run_scan(
    input_type: str,
    input_ref: str,
    scan_context: dict | None = None,
) -> dict:
    """Run the full AAC pipeline for a given source input.

    Args:
        input_type:   e.g. 'local_path', 'git_url', 'upload'
        input_ref:    Path or URL to the source to analyze.
        scan_context: Optional; il_level defaults to 'il4'.

    Returns:
        {"scan_id", "opportunities_count", "scores_count", "roadmap_id",
         "kanban_promoted", "status"}
    """
    if scan_context is None:
        scan_context = {"il_level": "il4"}

    init_db()

    total_files, total_loc = _count_source(input_ref)

    # 1a. Agent Readiness check (runs before Semgrep scan)
    try:
        readiness_result = run_readiness_check(input_ref)
    except Exception as exc:  # noqa: BLE001
        readiness_result = {
            "pillar_scores": {},
            "overall_readiness_score": 0.0,
            "icdev_checks": {},
            "error": str(exc),
        }

    language_profile: dict = {
        "python": total_files,
        "agent_readiness_summary": {
            "pillar_scores": readiness_result["pillar_scores"],
            "overall_readiness_score": readiness_result["overall_readiness_score"],
            "icdev_checks": readiness_result["icdev_checks"],
        },
    }

    # 1b. Insert scan record
    conn = get_connection()
    try:
        cur = _exec(
            conn,
            "INSERT INTO aac_scans "
            "(input_type, input_ref, language_profile, total_files, total_loc, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (input_type, input_ref, _dump(language_profile), total_files, total_loc, "running"),
        )
        conn.commit()
        scan_id: int = cur.lastrowid

        _exec(
            conn,
            "INSERT INTO aac_audit_log (event_type, scan_id, actor, detail) VALUES (?, ?, ?, ?)",
            ("scan_started", scan_id, "system", _dump({"input_type": input_type, "input_ref": input_ref})),
        )
        conn.commit()
    finally:
        conn.close()

    # 2. Detect patterns — batch API path for large repos, else Semgrep/AST
    if is_batch_eligible(total_files):
        batch_patterns, batch_id = run_batch_scan(input_ref, scan_id)
        if batch_id is not None:
            language_profile["batch_id"] = batch_id
            conn = get_connection()
            try:
                _exec(
                    conn,
                    "UPDATE aac_scans SET language_profile = ? WHERE scan_id = ?",
                    (_dump(language_profile), scan_id),
                )
                conn.commit()
            finally:
                conn.close()
        patterns = batch_patterns if batch_patterns else detect_patterns(input_ref)
    else:
        patterns = detect_patterns(input_ref)

    # 3. Insert opportunities + scores
    opp_rows: list[dict] = []
    score_rows: list[dict] = []

    conn = get_connection()
    try:
        for pat in patterns:
            paradigm = _PATTERN_TO_PARADIGM.get(pat["pattern_type"], "llm_generation")
            il_model = _PARADIGM_TO_MODEL.get(paradigm, "claude-sonnet-4-6")

            # Compute score first so capability mapper can use it.
            score = score_opportunity(pat, scan_context)

            # Enrich pattern_detail with NIST AI RMF + OWASP tags.
            tags = map_capabilities(pat, score, scan_context)
            pattern_detail = dict(pat.get("pattern_detail", {}))
            pattern_detail["nist_ai_rmf"] = tags["nist_ai_rmf"]
            pattern_detail["owasp_llm_risk"] = tags["owasp_llm_risk"]

            cur = _exec(
                conn,
                "INSERT INTO aac_opportunities "
                "(scan_id, module_path, function_name, line_start, line_end, language, "
                "pattern_type, pattern_detail, ai_paradigm, il_recommended_model, data_requirements) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scan_id,
                    pat["module_path"],
                    pat.get("function_name", "<unknown>"),
                    pat.get("line_start", 0),
                    pat.get("line_end", 0),
                    pat.get("language", "python"),
                    pat["pattern_type"],
                    _dump(pattern_detail),
                    paradigm,
                    il_model,
                    _dump({}),
                ),
            )
            conn.commit()
            opp_id: int = cur.lastrowid

            _exec(
                conn,
                "INSERT INTO aac_scores "
                "(opportunity_id, value_score, feasibility_score, risk_score, composite_score, score_detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    opp_id,
                    score["value_score"],
                    score["feasibility_score"],
                    score["risk_score"],
                    score["composite_score"],
                    _dump(score["score_detail"]),
                ),
            )
            conn.commit()

            opp_rows.append({"opportunity_id": opp_id, **pat})
            score_rows.append({"opportunity_id": opp_id, **score})
    finally:
        conn.close()

    # 4. Generate roadmap (persists to aac_roadmaps internally)
    roadmap = generate_roadmap(scan_id, opp_rows, score_rows)

    # 5. Promote top opportunities to kanban
    kanban_promoted = _promote_top_opportunities(opp_rows, score_rows, scan_id, roadmap["roadmap_id"])

    # 6. Mark scan completed + final audit entry
    conn = get_connection()
    try:
        _exec(
            conn,
            "UPDATE aac_scans SET status = ?, completed_at = ? WHERE scan_id = ?",
            ("completed", _now(), scan_id),
        )
        _exec(
            conn,
            "INSERT INTO aac_audit_log (event_type, scan_id, actor, detail) VALUES (?, ?, ?, ?)",
            (
                "scan_completed",
                scan_id,
                "system",
                _dump({"opportunities_count": len(opp_rows), "roadmap_id": roadmap["roadmap_id"]}),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "scan_id": scan_id,
        "opportunities_count": len(opp_rows),
        "scores_count": len(score_rows),
        "roadmap_id": roadmap["roadmap_id"],
        "kanban_promoted": kanban_promoted,
        "status": "completed",
        "pillar_scores": readiness_result["pillar_scores"],
        "overall_readiness_score": readiness_result["overall_readiness_score"],
        "icdev_checks": readiness_result["icdev_checks"],
    }
