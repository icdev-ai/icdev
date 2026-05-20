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
  7. aac_scans UPDATE  — status='completed'
  8. aac_audit_log     — scan_started / scan_completed events
"""
from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime, timezone
from typing import Any

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
        {"scan_id", "opportunities_count", "scores_count", "roadmap_id", "status"}
    """
    if scan_context is None:
        scan_context = {"il_level": "il4"}

    init_db()

    total_files, total_loc = _count_source(input_ref)
    language_profile = {"python": total_files}

    # 1. Insert scan record
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

    # 2. Detect patterns
    patterns = detect_patterns(input_ref)

    # 3. Insert opportunities + scores
    opp_rows: list[dict] = []
    score_rows: list[dict] = []

    conn = get_connection()
    try:
        for pat in patterns:
            paradigm = _PATTERN_TO_PARADIGM.get(pat["pattern_type"], "llm_generation")
            il_model = _PARADIGM_TO_MODEL.get(paradigm, "claude-sonnet-4-6")

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
                    _dump(pat.get("pattern_detail", {})),
                    paradigm,
                    il_model,
                    _dump({}),
                ),
            )
            conn.commit()
            opp_id: int = cur.lastrowid

            score = score_opportunity(pat, scan_context)
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

    # 5. Mark scan completed + final audit entry
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
        "status": "completed",
    }
