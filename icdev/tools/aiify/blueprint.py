# CUI // SP-CTI
"""AI-ify Canvas — Flask Blueprint.

Routes:
  GET  /ai-ify/              index (scan form + history)
  POST /ai-ify/api/scan      run scan → {scan_id, ...}
  GET  /ai-ify/api/scan/<id> get scan results
  POST /ai-ify/api/iqe-query IQE natural-language query
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import importlib
import json

from flask import Blueprint, jsonify, redirect, render_template, request

import re

from tools.aiify.db.init_db import get_connection, init_db
from tools.aiify.engine import run_scan

logger = get_logger(__name__)

aiify_bp = Blueprint(
    "aiify",
    __name__,
    url_prefix="/ai-ify",
    template_folder="../../tools/dashboard/templates",
)

# Backward-compat: 301-redirect the legacy /ai-augmentation/ URLs to /ai-ify/.
aiify_compat_bp = Blueprint("aiify_compat", __name__, url_prefix="/ai-augmentation")


@aiify_compat_bp.route("/", defaults={"subpath": ""})
@aiify_compat_bp.route("/<path:subpath>")
def _legacy_redirect(subpath: str):
    target = "/ai-ify/" + subpath
    if request.query_string:
        target += "?" + request.query_string.decode("utf-8", "ignore")
    return redirect(target, code=301)


_INIT_DONE = False


def _ensure_init() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        return
    try:
        init_db()
    except Exception as exc:
        logger.warning("aiify: DB init error: %s", exc)
    _INIT_DONE = True


@aiify_bp.before_request
def _init():
    _ensure_init()


def _conn():
    return get_connection()


def _parse_phases(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return []
    return raw or []


@aiify_bp.route("/")
def index():
    conn = _conn()
    try:
        scans = [dict(r) for r in conn.execute(
            "SELECT scan_id, input_type, input_ref, total_files, total_loc, "
            "status, project_summary, created_at FROM aiify_scans ORDER BY created_at DESC LIMIT 10"
        ).fetchall()]

        # Fetch distinct recent sources from full history for the recents picker
        # Use a subquery to get the most-recent scan_id per input_ref, then join back
        # for input_type — avoids GROUP BY non-aggregate column on PostgreSQL.
        _all_sources = [dict(r) for r in conn.execute(
            "SELECT s.input_type, s.input_ref FROM aiify_scans s "
            "INNER JOIN ("
            "  SELECT input_ref, MAX(created_at) AS last_used FROM aiify_scans GROUP BY input_ref"
            ") g ON s.input_ref = g.input_ref AND s.created_at = g.last_used "
            "ORDER BY g.last_used DESC LIMIT 10"
        ).fetchall()]

        # Lazy backfill: compute summary for old scans that predate the feature
        _needs_commit = False
        for scan in scans:
            if not scan.get("project_summary"):
                try:
                    from tools.aiify.engine import _build_summary
                    opps_for_scan = [dict(r) for r in conn.execute(
                        "SELECT pattern_type, ai_paradigm FROM aiify_opportunities WHERE scan_id = ?",
                        (scan["scan_id"],),
                    ).fetchall()]
                    summary = _build_summary(scan["input_ref"], opps_for_scan)
                    conn.execute(
                        "UPDATE aiify_scans SET project_summary = ? WHERE scan_id = ?",
                        (summary, scan["scan_id"]),
                    )
                    scan["project_summary"] = summary
                    _needs_commit = True
                except Exception:
                    pass
        if _needs_commit:
            conn.commit()

        opportunities: list[dict] = []
        roadmap: dict | None = None

        if scans:
            latest_id = scans[0]["scan_id"]
            opportunities = [dict(r) for r in conn.execute(
                "SELECT o.opportunity_id, o.module_path, o.function_name, o.language, "
                "o.pattern_type, o.ai_paradigm, o.il_recommended_model, "
                "s.composite_score, s.value_score "
                "FROM aiify_opportunities o "
                "LEFT JOIN aiify_scores s ON s.opportunity_id = o.opportunity_id "
                "WHERE o.scan_id = ? ORDER BY s.composite_score DESC",
                (latest_id,)
            ).fetchall()]

            rm = conn.execute(
                "SELECT roadmap_id, title, phases, total_effort_days "
                "FROM aiify_roadmaps WHERE scan_id = ? ORDER BY created_at DESC LIMIT 1",
                (latest_id,)
            ).fetchone()
            if rm:
                roadmap = dict(rm)
                roadmap["phases"] = _parse_phases(roadmap.get("phases"))
    finally:
        conn.close()

    # Build deduplicated recent sources (most-recent first, max 8)
    recent_sources: list[dict] = [
        {"input_type": s.get("input_type", "local_path"), "input_ref": s.get("input_ref", "")}
        for s in _all_sources
        if (s.get("input_ref") or "").strip()
    ][:10]

    return render_template(
        "aiify/page.html",
        scans=scans,
        opportunities=opportunities,
        roadmap=roadmap,
        recent_sources=recent_sources,
        iqe_canvas="aiify",
        iqe_api_route="/ai-ify/api/iqe-query",
        iqe_title="AI-ify IQE",
        iqe_examples=[
            {"label": "Top opportunities", "query": "show top opportunities by composite score"},
            {"label": "Completed scans",   "query": "list all completed scans"},
            {"label": "Agentic patterns",  "query": "show agentic_trigger opportunities"},
        ],
    )


@aiify_bp.route("/api/scan/<int:scan_id>", methods=["DELETE"])
def api_delete_scan(scan_id: int):
    """Delete a single scan and cascade to its opportunities, scores, and roadmaps."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT scan_id FROM aiify_scans WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        # CASCADE FK constraints handle opportunities → scores and roadmaps.
        # audit_log rows are SET NULL (preserved for audit trail).
        conn.execute("DELETE FROM aiify_scans WHERE scan_id = ?", (scan_id,))
        conn.commit()
        return jsonify({"deleted": scan_id})
    finally:
        conn.close()


@aiify_bp.route("/api/scan/all", methods=["DELETE"])
def api_delete_all_scans():
    """Delete all scan records and their cascaded children."""
    conn = _conn()
    try:
        count_row = conn.execute("SELECT COUNT(*) FROM aiify_scans").fetchone()
        count = count_row[0] if count_row else 0
        # Delete in dependency order to satisfy FK constraints where CASCADE
        # may not be enforced (e.g., SQLite without PRAGMA foreign_keys=ON).
        conn.execute("DELETE FROM aiify_audit_log")
        conn.execute("DELETE FROM aiify_scores")
        conn.execute("DELETE FROM aiify_roadmaps")
        conn.execute("DELETE FROM aiify_opportunities")
        conn.execute("DELETE FROM aiify_scans")
        conn.commit()
        return jsonify({"deleted_scans": count})
    finally:
        conn.close()


# ── PRD helpers ─────────────────────────────────────────────────────────────

_PATTERN_CRITERIA: dict[str, str] = {
    "hardcoded_threshold":      "Replace static threshold with anomaly_detection model learned from historical data",
    "nested_conditionals":      "Replace branching logic with ml_classifier achieving ≥90% classification accuracy",
    "string_template_rendering": "Replace template rendering with llm_generation using validated structured output schema",
    "scheduled_cron":           "Replace time-based scheduling with agentic_trigger responding to event conditions",
    "regex_user_input":         "Replace regex with nlp_extractor achieving ≥85% entity recognition F1",
    "db_render_notify_chain":   "Replace manual DB→render→notify chain with llm_generation pipeline",
    "keyword_list_search":      "Replace keyword list with embedding_search at ≥0.85 cosine similarity threshold",
    "large_rule_table":         "Replace rule table with decision_agent using context-aware LLM evaluation with audit trail",
}

_PATTERN_DESCRIPTIONS: dict[str, str] = {
    "hardcoded_threshold":      "Literal numeric constants in comparisons/arithmetic (e.g. `if score > 0.7`). Brittle when distributions shift; an anomaly detection model learns optimal thresholds dynamically.",
    "nested_conditionals":      "Decision logic with 3+ nesting levels. High cognitive complexity; an ML classifier learns the rule surface from labeled examples.",
    "string_template_rendering": "Jinja2/format-string rendering that could benefit from LLM-generated, context-aware content.",
    "scheduled_cron":           "Time-based trigger that would be better served by event-driven agentic execution.",
    "regex_user_input":         "Regex applied to user-provided text; an NLP extractor handles variation and ambiguity more robustly.",
    "db_render_notify_chain":   "Manual orchestration of DB read → template render → notification; an LLM pipeline manages context across steps.",
    "keyword_list_search":      "Keyword list membership check; vector embedding search enables semantic similarity matching.",
    "large_rule_table":         "Dict/map with 10+ entries encoding business rules; a decision agent reasons over rules with context.",
}


def _build_prd(
    phase_id: str,
    phase: dict,
    scan: dict | None,
    score_map: dict,
    regulatory_items: list[dict],
    pain_points: list[dict],
    roadmap_title: str,
    innovation_signals: list[dict] | None = None,
    all_opportunities: list[dict] | None = None,
    rejected_innovation: list[dict] | None = None,
    rejected_research: list[dict] | None = None,
    rejected_creative: list[dict] | None = None,
) -> str:
    label = phase.get("label", phase_id)
    opps = phase.get("opportunities", [])
    effort = phase.get("total_effort_days", 0)
    # Architecture diagram shows the *whole* target system, not just this phase
    arch_opps = all_opportunities if all_opportunities else opps

    input_ref = (scan or {}).get("input_ref", "Unknown project")
    project_summary = (scan or {}).get("project_summary", "")
    ref = input_ref.strip().rstrip("/")
    import re as _re
    if _re.match(r"^(https?://|git@)", ref):
        project_name = ref.rstrip("/").split("/")[-1].replace(".git", "")
    else:
        import pathlib as _pathlib
        project_name = _pathlib.Path(ref).name or ref

    # Paradigm effort breakdown (phase-specific)
    paradigm_counts: dict[str, int] = {}
    for opp in opps:
        pa = opp.get("ai_paradigm", "llm_generation")
        paradigm_counts[pa] = paradigm_counts.get(pa, 0) + 1

    lines: list[str] = []
    lines.append(f"# PRD: {label} — {project_name}")
    lines.append("> Generated by ICDEV™ AI-ify Canvas  |  CUI // SP-CTI\n")

    # HITL Curation Summary — shows accepted vs rejected for audit trail
    _rej_inn = rejected_innovation or []
    _rej_res = rejected_research or []
    _rej_cre = rejected_creative or []
    _acc_inn = innovation_signals or []
    _acc_res = regulatory_items or []
    _acc_cre = pain_points or []
    if _acc_inn or _rej_inn or _acc_res or _rej_res or _acc_cre or _rej_cre:
        lines.append("## HITL Curation Summary")
        lines.append("| Engine | Accepted | Rejected |")
        lines.append("|--------|----------|----------|")
        lines.append(f"| Innovation | {len(_acc_inn)} | {len(_rej_inn)} |")
        lines.append(f"| Research & Regulatory | {len(_acc_res)} | {len(_rej_res)} |")
        lines.append(f"| Creative Pain Points | {len(_acc_cre)} | {len(_rej_cre)} |")
        lines.append("")
        for sig in _rej_inn:
            title = sig.get("title") or sig.get("source_type") or "Unknown"
            lines.append(f"- ❌ **Rejected (Innovation):** {title}")
        for sig in _rej_res:
            title = sig.get("regulation_name") or sig.get("title") or "Unknown"
            lines.append(f"- ❌ **Rejected (Research):** {title}")
        for sig in _rej_cre:
            desc = (sig.get("description") or "")[:80]
            lines.append(f"- ❌ **Rejected (Creative):** {desc}")
        if _rej_inn or _rej_res or _rej_cre:
            lines.append("")
        lines.append("")

    # Target architecture diagram (AI / GenAI / Agentic AI) — embedded Mermaid.
    try:
        from tools.aiify.diagram_generator import build_architecture_mermaid_block
        lines.append("## Target Architecture (AI / GenAI / Agentic AI)")
        lines.append("")
        lines.append(build_architecture_mermaid_block(scan or {}, arch_opps, label, max_opps=25))
        lines.append("")
    except Exception:  # noqa: BLE001 — diagram is best-effort, never blocks the PRD
        pass

    lines.append("## Objective")
    lines.append(
        f"Replace {len(opps)} manually-coded pattern(s) in **{project_name}** with "
        f"AI-native capabilities across {len({o.get('module_path','') for o in opps})} module(s)."
    )
    if project_summary:
        lines.append(f"\n**Project context:** {project_summary}")
    lines.append("")

    if innovation_signals:
        lines.append("## Innovation Signals")
        for sig in innovation_signals:
            score = float(sig.get("composite_score") or 0)
            title = sig.get("title") or sig.get("source_type") or ""
            desc  = (sig.get("description") or "")[:120]
            mark  = " ✓" if sig.get("hitl_accepted") else ""
            lines.append(f"- [{score:.2f}]{mark} **{title}**: {desc}")
        lines.append("")

    if regulatory_items:
        lines.append("## Market & Regulatory Context")
        for r in regulatory_items:
            reg  = r.get("regulation_name", "")
            body = r.get("regulatory_body", "") or ""
            dl   = r.get("deadline", "")
            raw  = r.get("nist_controls", "") or ""
            if isinstance(raw, str) and raw.startswith("["):
                try:
                    import json as _j
                    raw = ", ".join(_j.loads(raw)[:5])
                except Exception:
                    pass
            mark = " ✓" if r.get("hitl_accepted") else ""
            lines.append(
                f"- **{reg}**{mark}{' [' + body + ']' if body else ''} "
                f"(deadline: {dl or 'TBD'}): NIST controls: {raw}"
            )
        lines.append("")

    if pain_points:
        lines.append("## Customer Pain Points (Creative Engine)")
        for pp in pain_points:
            score = float(pp.get("composite_score") or 0)
            desc  = (pp.get("description") or "")[:120]
            mark  = " ✓" if pp.get("hitl_accepted") else ""
            lines.append(f"- [{score:.2f}]{mark} {desc}")
        lines.append("")

    lines.append("## Opportunities")
    lines.append("| # | Module | Pattern | AI Paradigm | Model | Score |")
    lines.append("|---|--------|---------|-------------|-------|-------|")
    for i, opp in enumerate(opps, 1):
        opp_id = opp.get("opportunity_id", 0)
        scores = score_map.get(opp_id, {})
        pct = int(float(scores.get("composite_score", 0)) * 100)
        lines.append(
            f"| {i} | `{opp.get('module_path','?')}` | {opp.get('pattern_type','?')} "
            f"| {opp.get('ai_paradigm','?')} | {opp.get('il_recommended_model','?')} | {pct}% |"
        )
    lines.append("")

    lines.append("## Pattern Explanations")
    seen_patterns: set[str] = set()
    for opp in opps:
        pt = opp.get("pattern_type", "")
        if pt and pt not in seen_patterns:
            seen_patterns.add(pt)
            desc = _PATTERN_DESCRIPTIONS.get(pt, "")
            if desc:
                lines.append(f"- **`{pt}`** — {desc}")
    lines.append("")

    lines.append("## Effort Estimate")
    lines.append(f"- **Total effort:** {effort} days")
    lines.append(f"- **Opportunities:** {len(opps)}")
    if paradigm_counts:
        lines.append("- **By AI paradigm:**")
        for pa, count in sorted(paradigm_counts.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  - {pa}: {count} opportunity(ies)")
    lines.append("")

    lines.append("## Acceptance Criteria")
    seen_ac: set[str] = set()
    for opp in opps:
        pt = opp.get("pattern_type", "")
        criterion = _PATTERN_CRITERIA.get(pt, f"Replace {pt} with AI capability")
        if criterion not in seen_ac:
            seen_ac.add(criterion)
            lines.append(f"- [ ] {criterion}")
    lines.append("- [ ] All AI integrations pass security scan (Bandit + SAST)")
    lines.append("- [ ] Compliance gate: CUI markings on generated artifacts")
    lines.append("- [ ] Test coverage ≥80% on modified modules")
    lines.append("")

    lines.append("## Kanban Decomposition")
    lines.append("Each opportunity decomposes into 4 atomic tasks: **Design → Implement → Test → Review**")
    lines.append("- Design tasks: define interface contract + test cases")
    lines.append("- Implement tasks: integrate AI capability using recommended model")
    lines.append("- Test tasks: validate AI output parity against baseline")
    lines.append("- Review tasks: security scan + compliance gate")
    lines.append(f"\n*Click **Send {phase_id} to Kanban** to create {len(opps) * 4 + 1} atomic tasks with dependency chain.*")

    return "\n".join(lines)


@aiify_bp.route("/api/scan", methods=["POST"])
def api_scan():
    data = request.get_json(force=True, silent=True) or {}
    input_type = data.get("input_type", "local_path")
    input_ref  = (data.get("input_ref") or "").strip()
    il_level   = data.get("il_level", "il4")

    if not input_ref:
        return jsonify({"error": "input_ref is required"}), 400

    # Auto-detect git URLs if input_type wasn't explicitly set
    if input_type == "local_path" and re.match(r"^(https?://|git@)", input_ref):
        input_type = "git_url"

    try:
        result = run_scan(input_type, input_ref, {"il_level": il_level})
        return jsonify(result), 201
    except Exception as exc:
        logger.error("aiify: scan error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@aiify_bp.route("/api/scan/<int:scan_id>")
def api_get_scan(scan_id: int):
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM aiify_scans WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404

        opps = [dict(r) for r in conn.execute(
            "SELECT o.opportunity_id, o.module_path, o.function_name, o.language, "
            "o.pattern_type, o.ai_paradigm, o.il_recommended_model, "
            "s.composite_score, s.value_score, s.feasibility_score, s.risk_score, "
            "s.verdict, s.ai_readiness, s.rationale, s.pros, s.cons, s.category "
            "FROM aiify_opportunities o "
            "LEFT JOIN aiify_scores s ON s.opportunity_id = o.opportunity_id "
            "WHERE o.scan_id = ? ORDER BY s.composite_score DESC",
            (scan_id,)
        ).fetchall()]

        rm = conn.execute(
            "SELECT roadmap_id, title, phases, total_effort_days "
            "FROM aiify_roadmaps WHERE scan_id = ? ORDER BY created_at DESC LIMIT 1",
            (scan_id,)
        ).fetchone()
        roadmap = None
        if rm:
            roadmap = dict(rm)
            roadmap["phases"] = _parse_phases(roadmap.get("phases"))

        return jsonify({"scan": dict(row), "opportunities": opps, "roadmap": roadmap})
    finally:
        conn.close()


@aiify_bp.route("/api/send-to-kanban", methods=["POST"])
def api_send_to_kanban():
    """Promote roadmap opportunities to kanban_tasks with atomic decomposition.

    Each phase creates:
      - 1 epic task  (aiify-{short}-{ph}-epic)
      - 4 child tasks per opportunity: d1=Design, d2=Implement, d3=Test, d4=Review
        with sequential depends_on_task_id chain (d2→d1, d3→d2, d4→d3).
    When phase_id='all', phase epics are also chained (P2 epic → P1 epic, etc.).

    Body: {"roadmap_id": str, "phase_id": "all" | "P1" | "P2" | "P3"}
    Returns: {"created": N, "skipped": M, "phase_id": str, "epics": E}
    """
    data = request.get_json(force=True, silent=True) or {}
    roadmap_id = (data.get("roadmap_id") or "").strip()
    phase_id   = (data.get("phase_id") or "all").strip()

    # Fallback: if caller sends scan_id, resolve the latest roadmap for that scan
    if not roadmap_id:
        scan_id = data.get("scan_id")
        if scan_id:
            conn = _conn()
            try:
                rm = conn.execute(
                    "SELECT roadmap_id FROM aiify_roadmaps WHERE scan_id = ? ORDER BY created_at DESC LIMIT 1",
                    (scan_id,),
                ).fetchone()
                if rm:
                    roadmap_id = rm["roadmap_id"]
            finally:
                conn.close()

    if not roadmap_id:
        return jsonify({"error": "roadmap_id is required"}), 400

    conn = _conn()
    try:
        rm = conn.execute(
            "SELECT scan_id, phases FROM aiify_roadmaps WHERE roadmap_id = ?",
            (roadmap_id,),
        ).fetchone()
        if not rm:
            return jsonify({"error": "roadmap not found"}), 404
        scan_id = rm["scan_id"]
        phases  = _parse_phases(rm["phases"])
    finally:
        conn.close()

    # PRD HITL gate: block send if PRD for this phase was rejected
    if phase_id != "all":
        try:
            aiify = _conn()
            try:
                prd_key = f"{roadmap_id}:{phase_id}"
                row = aiify.execute(
                    "SELECT decision FROM aiify_hitl_decisions "
                    "WHERE source_type='prd' AND source_id=?",
                    (prd_key,),
                ).fetchone()
                if row and row["decision"] == "reject":
                    return jsonify({
                        "error": (
                            f"PRD for {phase_id} was rejected — "
                            "update your HITL decision before sending to Kanban"
                        ),
                        "blocked": True,
                    }), 403
            finally:
                aiify.close()
        except Exception:
            pass

    # Filter to requested phase(s)
    target_phases = [
        ph for ph in phases
        if phase_id == "all" or ph.get("phase_id") == phase_id
    ]
    if not target_phases:
        return jsonify({"created": 0, "skipped": 0, "phase_id": phase_id, "epics": 0})

    # Bulk-fetch scores for all involved opportunities
    all_opp_ids = [
        opp["opportunity_id"]
        for ph in target_phases
        for opp in ph.get("opportunities", [])
        if "opportunity_id" in opp
    ]
    score_map: dict[int, dict] = {}
    if all_opp_ids:
        conn = _conn()
        try:
            placeholders = ",".join("?" * len(all_opp_ids))
            rows = conn.execute(
                f"SELECT opportunity_id, composite_score, value_score, feasibility_score, risk_score "
                f"FROM aiify_scores WHERE opportunity_id IN ({placeholders})",
                tuple(all_opp_ids),
            ).fetchall()
            for r in rows:
                score_map[r["opportunity_id"]] = dict(r)
        finally:
            conn.close()

    _PHASE_PRIORITY = {"P1": "high", "P2": "medium", "P3": "low"}
    short_id = roadmap_id[:8]

    from tools.db.storage import get_connection as _icdev_conn
    created = skipped = epics = 0
    icdev_conn = _icdev_conn()

    try:
        prev_epic_id: str | None = None  # for inter-phase chaining when phase_id == 'all'

        for ph in target_phases:
            label   = ph.get("label", "")
            ph_key  = ph.get("phase_id") or (label.split(" ")[0] if label else "P3")
            priority = _PHASE_PRIORITY.get(ph_key, "medium")
            opps    = ph.get("opportunities", [])

            # ── Epic task ────────────────────────────────────────────────────
            epic_id = f"aiify-{short_id}-{ph_key.lower()}-epic"
            if icdev_conn.execute("SELECT id FROM kanban_tasks WHERE id = ?", (epic_id,)).fetchone():
                skipped += 1
            else:
                subtitle = label.split("—")[1].strip() if "—" in label else label
                epic_title = f"[{ph_key} Epic] AI-ify — {subtitle}"
                epic_desc = json.dumps({
                    "roadmap_id": roadmap_id, "scan_id": scan_id,
                    "phase": label, "opportunity_count": len(opps),
                    "total_effort_days": ph.get("total_effort_days", 0),
                })
                icdev_conn.execute(
                    "INSERT INTO kanban_tasks "
                    "(id, title, description, task_type, priority, status, executor_type, depends_on_task_id) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (epic_id, epic_title, epic_desc, "chore", priority, "backlog", "claude_cli",
                     prev_epic_id if phase_id == "all" else None),
                )
                icdev_conn.commit()
                created += 1
                epics += 1

            if phase_id == "all":
                prev_epic_id = epic_id

            # ── 4 atomic child tasks per opportunity ─────────────────────────
            for opp in opps:
                opp_id   = opp.get("opportunity_id", 0)
                pattern  = opp.get("pattern_type", "unknown")
                module   = opp.get("module_path", "?")
                fn       = opp.get("function_name", "<unknown>")
                paradigm = opp.get("ai_paradigm", "llm_generation")
                model    = opp.get("il_recommended_model", "")
                scores   = score_map.get(opp_id, {})
                composite = float(scores.get("composite_score", 0.0))
                child_priority = "high" if composite >= 0.7 else priority
                criterion = _PATTERN_CRITERIA.get(pattern, f"Replace {pattern} with {paradigm}")

                base_id  = f"aiify-{short_id}-{ph_key.lower()}-{opp_id}"
                base_desc_data = {
                    "opportunity_id": opp_id, "scan_id": scan_id,
                    "roadmap_id": roadmap_id, "phase": label,
                    "pattern_type": pattern, "module_path": module,
                    "function_name": fn, "ai_paradigm": paradigm,
                    "model_recommendation": model,
                    "scores": {
                        "composite": scores.get("composite_score"),
                        "value": scores.get("value_score"),
                        "feasibility": scores.get("feasibility_score"),
                        "risk": scores.get("risk_score"),
                    },
                    "acceptance_criterion": criterion,
                }

                steps = [
                    ("d1", "Design",     None,            f"Define interface contract and test cases for {pattern} replacement in {module}:{fn}"),
                    ("d2", "Implement",  f"{base_id}-d1", f"Replace {pattern} with {paradigm} ({model or 'recommended model'}) in {module}:{fn}"),
                    ("d3", "Test",       f"{base_id}-d2", f"Validate AI output parity; {criterion[:60]}"),
                    ("d4", "Review",     f"{base_id}-d3", f"Security scan + compliance gate for {paradigm} integration in {module}"),
                ]

                for suffix, step_name, dep_id, step_title in steps:
                    child_id = f"{base_id}-{suffix}"
                    if icdev_conn.execute("SELECT id FROM kanban_tasks WHERE id = ?", (child_id,)).fetchone():
                        skipped += 1
                        continue
                    child_desc = json.dumps({**base_desc_data, "step": step_name, "depends_on": dep_id})
                    icdev_conn.execute(
                        "INSERT INTO kanban_tasks "
                        "(id, title, description, task_type, priority, status, executor_type, depends_on_task_id) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (child_id, f"[{step_name}] {step_title[:90]}", child_desc,
                         "build", child_priority, "backlog", "claude_cli", dep_id),
                    )
                    icdev_conn.commit()
                    created += 1
    finally:
        icdev_conn.close()

    # Audit log
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO aiify_audit_log (event_type, scan_id, actor, detail) VALUES (?, ?, ?, ?)",
            ("kanban_promoted", scan_id, "user",
             json.dumps({"roadmap_id": roadmap_id, "phase_id": phase_id,
                         "created": created, "skipped": skipped, "epics": epics})),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"created": created, "skipped": skipped, "phase_id": phase_id, "epics": epics})


@aiify_bp.route("/api/generate-prd", methods=["POST"])
def api_generate_prd():
    """Generate a PRD markdown document for a roadmap phase.

    Body: {"roadmap_id": str, "phase_id": "P1"|"P2"|"P3"}
    Returns: {"prd": "markdown string", "phase_id": str, "roadmap_id": str}
    """
    data = request.get_json(force=True, silent=True) or {}
    roadmap_id = (data.get("roadmap_id") or "").strip()
    phase_id   = (data.get("phase_id") or "").strip()

    # Fallback: if caller sends scan_id, resolve the latest roadmap for that scan
    if not roadmap_id:
        scan_id = data.get("scan_id")
        if scan_id:
            conn = _conn()
            try:
                rm = conn.execute(
                    "SELECT roadmap_id FROM aiify_roadmaps WHERE scan_id = ? ORDER BY created_at DESC LIMIT 1",
                    (scan_id,),
                ).fetchone()
                if rm:
                    roadmap_id = rm["roadmap_id"]
            finally:
                conn.close()

    if not roadmap_id or not phase_id:
        return jsonify({"error": "roadmap_id and phase_id are required"}), 400

    conn = _conn()
    try:
        rm = conn.execute(
            "SELECT scan_id, title, phases FROM aiify_roadmaps WHERE roadmap_id = ?",
            (roadmap_id,),
        ).fetchone()
        if not rm:
            return jsonify({"error": "roadmap not found"}), 404

        scan = conn.execute(
            "SELECT input_ref, project_summary, total_files, total_loc FROM aiify_scans WHERE scan_id = ?",
            (rm["scan_id"],),
        ).fetchone()
        scan_dict = dict(scan) if scan else {}

        phases = _parse_phases(rm["phases"])
        target_phase = next(
            (ph for ph in phases if ph.get("phase_id") == phase_id), None
        )
        if not target_phase:
            return jsonify({"error": f"phase {phase_id} not found in roadmap"}), 404

        roadmap_title = rm["title"]
    finally:
        conn.close()

    # Fetch ALL opportunities for this scan (used for architecture diagram)
    all_opportunities: list[dict] = []
    conn = _conn()
    try:
        all_opportunities = [
            dict(r) for r in conn.execute(
                "SELECT opportunity_id, module_path, function_name, pattern_type, ai_paradigm, "
                "il_recommended_model FROM aiify_opportunities WHERE scan_id = ?",
                (rm["scan_id"],),
            ).fetchall()
        ]
    finally:
        conn.close()

    # Fetch scores for phase opportunities
    opp_ids = [o["opportunity_id"] for o in target_phase.get("opportunities", []) if "opportunity_id" in o]
    score_map: dict[int, dict] = {}
    if opp_ids:
        conn = _conn()
        try:
            placeholders = ",".join("?" * len(opp_ids))
            rows = conn.execute(
                f"SELECT opportunity_id, composite_score, value_score, feasibility_score, risk_score "
                f"FROM aiify_scores WHERE opportunity_id IN ({placeholders})",
                tuple(opp_ids),
            ).fetchall()
            for r in rows:
                score_map[r["opportunity_id"]] = dict(r)
        finally:
            conn.close()

    # Load HITL decisions from AI-ify canvas DB
    hitl: dict[tuple, str] = {}
    prd_key = f"{roadmap_id}:{phase_id}"
    prd_hitl_decision: str | None = None
    try:
        aiify = _conn()
        try:
            for row in aiify.execute(
                "SELECT source_type, source_id, decision FROM aiify_hitl_decisions"
            ).fetchall():
                hitl[(row["source_type"], str(row["source_id"]))] = row["decision"]
            prd_row = aiify.execute(
                "SELECT decision FROM aiify_hitl_decisions WHERE source_type='prd' AND source_id=?",
                (prd_key,),
            ).fetchone()
            if prd_row:
                prd_hitl_decision = prd_row["decision"]
        finally:
            aiify.close()
    except Exception:
        pass

    # Enrich with Innovation + Research + Creative engine data (HITL-filtered, best-effort)
    innovation_signals: list[dict] = []
    regulatory_items: list[dict] = []
    pain_points: list[dict] = []
    rejected_innovation: list[dict] = []
    rejected_research: list[dict] = []
    rejected_creative: list[dict] = []
    try:
        from tools.db.storage import get_connection as _icdev_conn
        icdev = _icdev_conn()
        try:
            try:
                rows = icdev.execute(
                    "SELECT id, source_type, title, description, composite_score "
                    "FROM innovation_signals ORDER BY id DESC LIMIT 10"
                ).fetchall()
                for r in rows:
                    dec = hitl.get(("innovation", str(r["id"])))
                    if dec == "reject":
                        rejected_innovation.append(dict(r))
                        continue
                    item = dict(r)
                    item["hitl_accepted"] = dec == "accept"
                    innovation_signals.append(item)
                    if len(innovation_signals) == 3:
                        break
            except Exception:
                pass
            try:
                rows = icdev.execute(
                    "SELECT id, regulation_name, regulatory_body, deadline, nist_controls "
                    "FROM research_regulatory_map LIMIT 10"
                ).fetchall()
                for r in rows:
                    dec = hitl.get(("research", str(r["id"])))
                    if dec == "reject":
                        rejected_research.append(dict(r))
                        continue
                    item = dict(r)
                    item["hitl_accepted"] = dec == "accept"
                    regulatory_items.append(item)
                    if len(regulatory_items) == 3:
                        break
            except Exception:
                pass
            try:
                rows = icdev.execute(
                    "SELECT id, description, composite_score "
                    "FROM creative_pain_points ORDER BY composite_score DESC LIMIT 10"
                ).fetchall()
                for r in rows:
                    dec = hitl.get(("creative", str(r["id"])))
                    if dec == "reject":
                        rejected_creative.append(dict(r))
                        continue
                    item = dict(r)
                    item["hitl_accepted"] = dec == "accept"
                    pain_points.append(item)
                    if len(pain_points) == 3:
                        break
            except Exception:
                pass
        finally:
            icdev.close()
    except Exception:
        pass

    prd = _build_prd(
        phase_id, target_phase, scan_dict, score_map,
        regulatory_items, pain_points, roadmap_title, innovation_signals,
        all_opportunities,
        rejected_innovation, rejected_research, rejected_creative,
    )
    try:
        import markdown as _markdown_lib
        prd_html = _markdown_lib.markdown(
            prd, extensions=["tables", "fenced_code"]
        )
    except Exception:
        prd_html = ""
    return jsonify({
        "prd": prd,
        "prd_html": prd_html,
        "phase_id": phase_id,
        "roadmap_id": roadmap_id,
        "hitl_decision": prd_hitl_decision,
    })


@aiify_bp.route("/api/prd-dry-run", methods=["POST"])
def api_prd_dry_run():
    """Dry-run PRD validation + Kanban preview. Auto AI Boost if score is low.

    Body: {"roadmap_id": str, "phase_id": "P1"|"P2"|"P3"}
    Returns: {
        "score": float,
        "original_score": float,
        "ai_boosted": bool,
        "boost_would_trigger": bool,
        "opportunity_count": int,
        "tasks": [{"id", "title", "type", "priority"}],
        "prd_hitl_decision": str | null,
    }
    """
    data = request.get_json(force=True, silent=True) or {}
    roadmap_id = (data.get("roadmap_id") or "").strip()
    phase_id = (data.get("phase_id") or "").strip()

    if not roadmap_id or not phase_id:
        return jsonify({"error": "roadmap_id and phase_id are required"}), 400

    conn = _conn()
    try:
        rm = conn.execute(
            "SELECT scan_id, title, phases FROM aiify_roadmaps WHERE roadmap_id = ?",
            (roadmap_id,),
        ).fetchone()
        if not rm:
            return jsonify({"error": "roadmap not found"}), 404
        scan = conn.execute(
            "SELECT input_ref, project_summary, total_files, total_loc FROM aiify_scans WHERE scan_id = ?",
            (rm["scan_id"],),
        ).fetchone()
        scan_dict = dict(scan) if scan else {}
        phases = _parse_phases(rm["phases"])
        target_phase = next(
            (ph for ph in phases if ph.get("phase_id") == phase_id), None
        )
        if not target_phase:
            return jsonify({"error": f"phase {phase_id} not found"}), 404
    finally:
        conn.close()

    opps = target_phase.get("opportunities", [])
    opp_ids = [o["opportunity_id"] for o in opps if "opportunity_id" in o]

    # Load scores
    score_map: dict[int, dict] = {}
    if opp_ids:
        conn = _conn()
        try:
            placeholders = ",".join("?" * len(opp_ids))
            rows = conn.execute(
                f"SELECT opportunity_id, composite_score, value_score, feasibility_score, risk_score "
                f"FROM aiify_scores WHERE opportunity_id IN ({placeholders})",
                tuple(opp_ids),
            ).fetchall()
            for r in rows:
                score_map[r["opportunity_id"]] = dict(r)
        finally:
            conn.close()

    # Load HITL decisions
    prd_key = f"{roadmap_id}:{phase_id}"
    prd_hitl_decision: str | None = None
    hitl_accepted = hitl_total = 0
    try:
        aiify = _conn()
        try:
            for row in aiify.execute(
                "SELECT source_type, source_id, decision FROM aiify_hitl_decisions"
            ).fetchall():
                hitl_total += 1
                if row["decision"] == "accept":
                    hitl_accepted += 1
            prd_row = aiify.execute(
                "SELECT decision FROM aiify_hitl_decisions WHERE source_type='prd' AND source_id=?",
                (prd_key,),
            ).fetchone()
            if prd_row:
                prd_hitl_decision = prd_row["decision"]
        finally:
            aiify.close()
    except Exception:
        pass

    # ── Generate PRD for scoring ─────────────────────────────────────────────
    # Re-use _build_prd with minimal engine data (best-effort)
    innovation_signals: list[dict] = []
    regulatory_items: list[dict] = []
    pain_points: list[dict] = []
    try:
        from tools.db.storage import get_connection as _icdev_conn
        icdev = _icdev_conn()
        try:
            try:
                for r in icdev.execute(
                    "SELECT id, source_type, title, description, composite_score FROM innovation_signals ORDER BY id DESC LIMIT 3"
                ).fetchall():
                    innovation_signals.append(dict(r))
            except Exception:
                pass
            try:
                for r in icdev.execute(
                    "SELECT id, regulation_name, regulatory_body, deadline, nist_controls FROM research_regulatory_map LIMIT 3"
                ).fetchall():
                    regulatory_items.append(dict(r))
            except Exception:
                pass
            try:
                for r in icdev.execute(
                    "SELECT id, description, composite_score FROM creative_pain_points ORDER BY composite_score DESC LIMIT 3"
                ).fetchall():
                    pain_points.append(dict(r))
            except Exception:
                pass
        finally:
            icdev.close()
    except Exception:
        pass

    prd = _build_prd(
        phase_id, target_phase, scan_dict, score_map,
        regulatory_items, pain_points, rm["title"], innovation_signals,
    )

    def _score_prd(text: str, opp_list: list[dict], scores: dict[int, dict]) -> float:
        s = 0.3
        s += min(len(opp_list) * 0.05, 0.2)
        avg_comp = 0.0
        if scores:
            vals = [float(v.get("composite_score", 0.0)) for v in scores.values()]
            avg_comp = sum(vals) / len(vals)
        if avg_comp >= 0.6:
            s += 0.1
        if "```mermaid" in text:
            s += 0.1
        if hitl_total and (hitl_accepted / hitl_total) >= 0.5:
            s += 0.1
        if innovation_signals:
            s += 0.05
        if regulatory_items:
            s += 0.05
        if pain_points:
            s += 0.05
        if len(text) > 2000:
            s += 0.1
        return round(min(s, 1.0), 2)

    score = _score_prd(prd, opps, score_map)
    original_score = score
    ai_boosted = False
    boost_would_trigger = score < 0.6

    # ── AI Boost ──────────────────────────────────────────────────────────────
    if boost_would_trigger:
        try:
            from tools.llm.router import LLMRouter
            from tools.llm.provider import LLMRequest
            router = LLMRouter()
            boost_prompt = (
                "You are an expert technical product manager. Improve the following PRD so it scores higher on:\n"
                "1. Detailed acceptance criteria per opportunity\n"
                "2. Clear architecture rationale and GenAI/ML component mapping\n"
                "3. Quantified business impact and risk mitigation\n"
                "4. Precise effort estimates with dependencies\n\n"
                "Return ONLY the improved PRD markdown (no extra commentary).\n\n"
                f"--- PRD START ---\n{prd}\n--- PRD END ---"
            )
            request_obj = LLMRequest(prompt=boost_prompt, max_tokens=4000, temperature=0.3)
            resp = router.invoke("code_generation", request_obj)
            boosted = resp.text or ""
            if boosted and len(boosted) > len(prd) * 0.8:
                prd = boosted
                score = _score_prd(prd, opps, score_map)
                ai_boosted = True
        except Exception:
            pass  # Graceful degradation in air-gap / no-LLM mode

    # ── Kanban task preview (no insert) ─────────────────────────────────────
    _PHASE_PRIORITY = {"P1": "high", "P2": "medium", "P3": "low"}
    short_id = roadmap_id[:8]
    ph_key = target_phase.get("phase_id") or (target_phase.get("label", "").split(" ")[0] if target_phase.get("label") else "P3")
    priority = _PHASE_PRIORITY.get(ph_key, "medium")
    tasks_preview: list[dict] = []

    epic_id = f"aiify-{short_id}-{ph_key.lower()}-epic"
    subtitle = target_phase.get("label", "").split("—")[1].strip() if "—" in target_phase.get("label", "") else target_phase.get("label", "")
    tasks_preview.append({
        "id": epic_id,
        "title": f"[{ph_key} Epic] AI-ify — {subtitle}",
        "type": "epic",
        "priority": priority,
    })

    for opp in opps:
        opp_id = opp.get("opportunity_id", 0)
        pattern = opp.get("pattern_type", "unknown")
        module = opp.get("module_path", "?")
        paradigm = opp.get("ai_paradigm", "llm_generation")
        scores = score_map.get(opp_id, {})
        composite = float(scores.get("composite_score", 0.0))
        child_priority = "high" if composite >= 0.7 else priority
        base_id = f"aiify-{short_id}-{ph_key.lower()}-{opp_id}"
        steps = [
            ("d1", "Design", f"Define interface contract and test cases for {pattern} replacement in {module}"),
            ("d2", "Implement", f"Replace {pattern} with {paradigm} in {module}"),
            ("d3", "Test", f"Validate AI output parity for {pattern} in {module}"),
            ("d4", "Review", f"Security scan + compliance gate for {paradigm} integration in {module}"),
        ]
        for suffix, step_name, step_title in steps:
            child_id = f"{base_id}-{suffix}"
            tasks_preview.append({
                "id": child_id,
                "title": f"[{step_name}] {step_title[:90]}",
                "type": "task",
                "priority": child_priority,
            })

    return jsonify({
        "score": score,
        "original_score": original_score,
        "ai_boosted": ai_boosted,
        "boost_would_trigger": boost_would_trigger,
        "opportunity_count": len(opps),
        "tasks": tasks_preview,
        "prd_hitl_decision": prd_hitl_decision,
    })


@aiify_bp.route("/api/intelligence-feed", methods=["GET"])
def api_intelligence_feed():
    """Return signals from Innovation, Creative, and Research engines with HITL state."""
    result: dict = {"innovation": [], "creative": [], "research": []}

    # Fetch HITL decisions from AI-ify canvas DB
    decisions: dict = {}
    try:
        aiify = _conn()
        try:
            for row in aiify.execute(
                "SELECT source_type, source_id, decision FROM aiify_hitl_decisions"
            ).fetchall():
                decisions[f"{row['source_type']}:{row['source_id']}"] = row["decision"]
        finally:
            aiify.close()
    except Exception:
        pass

    # Fetch engine data from main ICDEV DB
    try:
        from tools.db.storage import get_connection as _icdev_conn
        icdev = _icdev_conn()
        try:
            try:
                rows = icdev.execute(
                    "SELECT id, source_type, title, description, composite_score, source "
                    "FROM innovation_signals ORDER BY id DESC LIMIT 3"
                ).fetchall()
                for r in rows:
                    item = dict(r)
                    item["hitl_decision"] = decisions.get(f"innovation:{r['id']}")
                    result["innovation"].append(item)
            except Exception:
                pass
            try:
                rows = icdev.execute(
                    "SELECT id, category, title, description, composite_score "
                    "FROM creative_pain_points ORDER BY composite_score DESC LIMIT 3"
                ).fetchall()
                for r in rows:
                    item = dict(r)
                    item["hitl_decision"] = decisions.get(f"creative:{r['id']}")
                    result["creative"].append(item)
            except Exception:
                pass
            try:
                rows = icdev.execute(
                    "SELECT id, regulation_name, regulatory_body, deadline, "
                    "nist_controls, gap_analysis "
                    "FROM research_regulatory_map ORDER BY id DESC LIMIT 3"
                ).fetchall()
                for r in rows:
                    item = dict(r)
                    raw = item.get("nist_controls") or ""
                    if isinstance(raw, str) and raw.startswith("["):
                        try:
                            parsed = json.loads(raw)
                            raw = ", ".join(parsed[:5])
                        except Exception:
                            pass
                    body = item.get("regulatory_body") or ""
                    item["title"] = item.get("regulation_name") or ""
                    item["description"] = (
                        f"{body + ' — ' if body else ''}NIST: {raw}" if raw else body
                    )
                    item["hitl_decision"] = decisions.get(f"research:{r['id']}")
                    result["research"].append(item)
            except Exception:
                pass
        finally:
            icdev.close()
    except Exception:
        pass

    return jsonify(result)


@aiify_bp.route("/api/hitl-decision", methods=["POST"])
def api_hitl_decision():
    """Record an accept / reject / clear HITL decision for an engine signal or PRD."""
    data = request.get_json(force=True, silent=True) or {}
    source_type = (data.get("source_type") or "").strip()
    source_id   = str(data.get("source_id") or "").strip()
    decision    = (data.get("decision") or "").strip()
    reason      = (data.get("reason") or "").strip() or None
    phase_id    = (data.get("phase_id") or "").strip() or None

    if source_type not in ("innovation", "creative", "research", "prd"):
        return jsonify({"error": "invalid source_type"}), 400
    if not source_id:
        return jsonify({"error": "source_id required"}), 400
    if decision not in ("accept", "reject", "clear"):
        return jsonify({"error": "decision must be accept, reject, or clear"}), 400

    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM aiify_hitl_decisions WHERE source_type=? AND source_id=?",
            (source_type, source_id),
        )
        if decision != "clear":
            conn.execute(
                "INSERT INTO aiify_hitl_decisions "
                "(source_type, source_id, phase_id, decision, reason) "
                "VALUES (?,?,?,?,?)",
                (source_type, source_id, phase_id, decision, reason),
            )
        conn.execute(
            "INSERT INTO aiify_audit_log (event_type, actor, detail) VALUES (?,?,?)",
            (
                "hitl_decision",
                "user",
                json.dumps({
                    "source_type": source_type,
                    "source_id": source_id,
                    "decision": decision,
                    "reason": reason,
                }),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"status": "ok", "decision": decision})


@aiify_bp.route("/api/run-innovation", methods=["POST"])
def api_run_innovation():
    """Trigger the Innovation engine pipeline asynchronously."""
    import threading
    try:
        from tools.innovation.innovation_manager import run_full_pipeline
        t = threading.Thread(target=run_full_pipeline, daemon=True)
        t.start()
        return jsonify({"status": "started"})
    except Exception as exc:
        return jsonify({"status": "error", "detail": str(exc)}), 500


@aiify_bp.route("/api/iqe-query", methods=["POST"])
def api_iqe_query():
    data = request.get_json(force=True, silent=True) or {}
    question = (data.get("question") or "").strip()
    execute   = data.get("execute", True)

    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        importlib.import_module("tools.iqe.adapters.aiify")
    except Exception:
        pass

    iqe_str = ""
    try:
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import parse as _parse
        from tools.iqe.executor import execute_query

        collections = [
            "aiify.opportunities",
            "aiify.scans",
            "aiify.roadmaps",
            "aiify.posture",
        ]
        iqe_str = nl_to_iqe(question, collections=collections)
        ast = _parse(iqe_str)
        results = execute_query(ast) if execute else []
        return jsonify({"iqe": iqe_str, "results": results, "row_count": len(results)})
    except Exception as exc:
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500


# ── Compliance Posture ────────────────────────────────────────────────────────
@aiify_bp.route("/posture")
def posture_page():
    """AI-ify compliance posture overview (AI-governance grade + dimensions)."""
    return render_template("aiify/posture.html")


@aiify_bp.route("/api/posture-summary", methods=["GET"])
def api_posture_summary():
    """Return the live AI-ify compliance posture plus recent snapshot trend."""
    from tools.aiify.posture import compute_posture, posture_trend

    conn = _conn()
    try:
        posture = compute_posture(conn)
        posture["trend"] = posture_trend(conn, limit=20)
    finally:
        conn.close()
    return jsonify(posture)


@aiify_bp.route("/api/posture/snapshot", methods=["POST"])
def api_posture_snapshot():
    """Compute and persist a posture snapshot (audit evidence). Returns the posture."""
    from tools.aiify.posture import snapshot_posture

    conn = _conn()
    try:
        posture = snapshot_posture(conn, actor="user")
    finally:
        conn.close()
    return jsonify({"status": "ok", "posture": posture})
