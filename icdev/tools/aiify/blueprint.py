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
from tools.aiify.prd_common import (
    PHASE_PRIORITY,
    build_task_steps,
    load_engine_enrichment,
    phase_key,
)
# penta-aiify-02: hard-gate destructive/expensive routes with the established
# dashboard RBAC decorator (same pattern as tools/dashboard/app.py). Enforces
# login (401) + operator role (403) independent of ICDEV_ENFORCE_CANVAS_ACCESS.
from tools.dashboard.auth import require_role

logger = get_logger(__name__)

# Roles permitted to invoke AI-ify mutating / resource-spawning routes.
_AIIFY_MUTATE_ROLES = ("admin", "pm", "developer", "isso")

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


def _load_prd_hitl_decisions(roadmap_id: str) -> dict[str, str]:
    """Load PRD HITL decisions for ``roadmap_id`` as {phase_id: decision}.

    Decisions are stored with source_id of the form '{roadmap_id}:{phase_id}';
    we fetch all PRD rows (no bound params → backend agnostic) and filter in
    Python. This lookup is a fail-CLOSED control gate: it deliberately does NOT
    swallow DB errors — the caller must treat a lookup failure as "cannot verify
    approval" and refuse to promote, rather than silently proceeding.
    """
    prd_decisions: dict[str, str] = {}
    aiify = _conn()
    try:
        rows = aiify.execute(
            "SELECT source_id, decision FROM aiify_hitl_decisions WHERE source_type='prd'"
        ).fetchall()
        for r in rows:
            sid = r["source_id"] if hasattr(r, "keys") else r[0]
            dec = r["decision"] if hasattr(r, "keys") else r[1]
            if sid and str(sid).startswith(roadmap_id + ":"):
                prd_decisions[str(sid).split(":", 1)[1]] = dec
    finally:
        aiify.close()
    return prd_decisions


def _audit_aiify_event(event_type: str, scan_id, actor: str, detail: dict) -> None:
    """Best-effort append to aiify_audit_log (never raises)."""
    try:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO aiify_audit_log (event_type, scan_id, actor, detail) VALUES (%s, %s, %s, %s)",
                (event_type, scan_id, actor, json.dumps(detail)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("aiify: audit write failed (%s)", event_type, exc_info=True)


def _parse_phases(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return []
    return raw or []


# ── TRUST: grounding evidence + provenance for AI-boosted PRDs (penta-aiify-04) ─

def _build_prd_evidence(opps, innovation_signals, regulatory_items, pain_points):
    """Build the citable evidence set for a PRD from its scan context.

    Returns ``(evidence_lines, allowed_ids)`` where every line names a stable id
    (``OPP-<n>`` / ``SIG-<n>`` / ``REG-<n>`` / ``PAIN-<n>``) the boost prompt asks
    the model to cite inline as ``[source: <id>]``. ``allowed_ids`` is the set
    citation validation checks against, so a boosted PRD cannot cite a source
    that is not real scan evidence.
    """
    lines: list[str] = []
    allowed: set[str] = set()
    for o in opps or []:
        oid = o.get("opportunity_id")
        if oid is None:
            continue
        sid = f"OPP-{oid}"
        allowed.add(sid)
        lines.append(
            f"- {sid}: {o.get('module_path', '?')}:{o.get('function_name', '')} — "
            f"{o.get('pattern_type', '')} → {o.get('ai_paradigm', '')}"
        )
    for s in innovation_signals or []:
        sid = f"SIG-{s.get('id')}"
        allowed.add(sid)
        lines.append(f"- {sid}: {str(s.get('title') or '')[:120]}")
    for r in regulatory_items or []:
        sid = f"REG-{r.get('id')}"
        allowed.add(sid)
        lines.append(f"- {sid}: {str(r.get('regulation_name') or '')[:120]}")
    for p in pain_points or []:
        sid = f"PAIN-{p.get('id')}"
        allowed.add(sid)
        lines.append(f"- {sid}: {str(p.get('description') or '')[:120]}")
    return lines, allowed


def _persist_prd_provenance(roadmap_id, phase_id, *, ai_boosted, model,
                            citation_valid, citation_report, evidence_ids, provenance):
    """Append a PRD provenance record (aiify_prd_provenance is append-only)."""
    try:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO aiify_prd_provenance "
                "(roadmap_id, phase_id, ai_boosted, generation_model, citation_valid, "
                " citation_report, evidence_sources, provenance) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    roadmap_id, phase_id, 1 if ai_boosted else 0, model or "",
                    1 if citation_valid else 0,
                    json.dumps(citation_report or {}),
                    json.dumps(list(evidence_ids or [])),
                    json.dumps(provenance or {}),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("aiify: PRD provenance persist failed", exc_info=True)


def _boosted_prd_citation_defects(roadmap_id: str) -> set[str]:
    """Phase ids whose LATEST AI-boosted PRD failed inline-citation validation.

    Reads the append-only aiify_prd_provenance; the most recent row per phase
    wins. Best-effort: a lookup failure returns an empty set (this gate only
    ADDS a restriction on top of the HITL gate — it never fails a promotion open
    on its own behalf).
    """
    latest: dict[str, dict] = {}
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT phase_id, ai_boosted, citation_valid FROM aiify_prd_provenance "
                "WHERE roadmap_id = %s ORDER BY created_at ASC, id ASC",
                (roadmap_id,),
            ).fetchall()
            for r in rows:
                if hasattr(r, "keys"):
                    rec = dict(r)
                else:
                    rec = {"phase_id": r[0], "ai_boosted": r[1], "citation_valid": r[2]}
                latest[str(rec.get("phase_id"))] = rec
        finally:
            conn.close()
    except Exception:
        return set()
    return {
        pid for pid, rec in latest.items()
        if int(rec.get("ai_boosted") or 0) == 1 and int(rec.get("citation_valid") or 0) == 0
    }


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

        # Display-only summary for old scans that predate the feature. The read
        # path performs NO write — the canonical summary is persisted once at scan
        # completion (engine.run_scan step 4a); computing it here for display keeps
        # index() side-effect-free (no UPDATE on a GET). (penta-aiify-06)
        for scan in scans:
            if not scan.get("project_summary"):
                try:
                    from tools.aiify.engine import _build_summary
                    opps_for_scan = [dict(r) for r in conn.execute(
                        "SELECT pattern_type, ai_paradigm FROM aiify_opportunities WHERE scan_id = %s",
                        (scan["scan_id"],),
                    ).fetchall()]
                    scan["project_summary"] = _build_summary(scan["input_ref"], opps_for_scan)
                except Exception:
                    pass

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
                "WHERE o.scan_id = %s ORDER BY s.composite_score DESC",
                (latest_id,)
            ).fetchall()]

            rm = conn.execute(
                "SELECT roadmap_id, title, phases, total_effort_days "
                "FROM aiify_roadmaps WHERE scan_id = %s ORDER BY created_at DESC LIMIT 1",
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
@require_role(*_AIIFY_MUTATE_ROLES)
def api_delete_scan(scan_id: int):
    """Delete a single scan and cascade to its opportunities, scores, and roadmaps."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT scan_id FROM aiify_scans WHERE scan_id = %s", (scan_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        # CASCADE FK constraints handle opportunities → scores and roadmaps.
        # audit_log rows are SET NULL (preserved for audit trail).
        conn.execute("DELETE FROM aiify_scans WHERE scan_id = %s", (scan_id,))
        conn.commit()
        return jsonify({"deleted": scan_id})
    finally:
        conn.close()


@aiify_bp.route("/api/scan/all", methods=["DELETE"])
@require_role(*_AIIFY_MUTATE_ROLES)
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
@require_role(*_AIIFY_MUTATE_ROLES)
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
            "SELECT * FROM aiify_scans WHERE scan_id = %s", (scan_id,)
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
            "WHERE o.scan_id = %s ORDER BY s.composite_score DESC",
            (scan_id,)
        ).fetchall()]

        rm = conn.execute(
            "SELECT roadmap_id, title, phases, total_effort_days "
            "FROM aiify_roadmaps WHERE scan_id = %s ORDER BY created_at DESC LIMIT 1",
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
@require_role(*_AIIFY_MUTATE_ROLES)
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
                    "SELECT roadmap_id FROM aiify_roadmaps WHERE scan_id = %s ORDER BY created_at DESC LIMIT 1",
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
            "SELECT scan_id, phases FROM aiify_roadmaps WHERE roadmap_id = %s",
            (roadmap_id,),
        ).fetchone()
        if not rm:
            return jsonify({"error": "roadmap not found"}), 404
        scan_id = rm["scan_id"]
        phases  = _parse_phases(rm["phases"])
    finally:
        conn.close()

    # ── PRD HITL gate + status policy (penta-aiify-01/03) ─────────────────────
    # This is a fail-CLOSED control gate. If the HITL decision lookup fails we
    # cannot verify whether any phase's PRD was rejected, so we MUST refuse to
    # promote (503) rather than proceed and risk seeding tasks for a rejected
    # PRD. An explicit force override (force / force_send in the body) lets an
    # authorized operator proceed anyway; the override is audited.
    force_override = bool(data.get("force") or data.get("force_send"))
    try:
        prd_decisions = _load_prd_hitl_decisions(roadmap_id)
    except Exception:
        logger.error(
            "aiify: HITL PRD gate lookup failed for roadmap=%s — failing closed",
            roadmap_id, exc_info=True,
        )
        if not force_override:
            return jsonify({
                "error": (
                    "HITL PRD approval gate is unavailable — cannot verify PRD "
                    "approval status; refusing to promote to Kanban (fail-closed). "
                    "Retry once the datastore is reachable, or resubmit with "
                    "force=true to override."
                ),
                "gate_unavailable": True,
            }), 503
        # Operator chose to override the unavailable gate. Proceed with NO known
        # approvals, so every task lands in 'suggested' quarantine (never
        # 'backlog'); record the override for audit.
        prd_decisions = {}
        _audit_aiify_event(
            "hitl_gate_force_override", scan_id, "user",
            {"roadmap_id": roadmap_id, "phase_id": phase_id,
             "reason": "prd_hitl_lookup_failed"},
        )

    # A rejected PRD blocks its phase. For an explicit single-phase request this
    # is a hard 403 (unchanged); for 'all' the rejected phase is skipped below
    # and the remaining phases proceed.
    if phase_id != "all" and prd_decisions.get(phase_id) == "reject":
        return jsonify({
            "error": (
                f"PRD for {phase_id} was rejected — "
                "update your HITL decision before sending to Kanban"
            ),
            "blocked": True,
        }), 403

    # ── TRUST citation gate for AI-boosted PRDs (penta-aiify-04) ──────────────
    # An AI-boosted PRD that failed inline-citation validation (no valid
    # [source: …] grounding against the scan evidence) must not seed tasks —
    # mirrors the placeholder_guard / citation_guard pattern. Single-phase → 403;
    # for 'all' the defective phase is skipped below. An explicit force override
    # (force / force_send) bypasses, audited. ``force_override`` was already read
    # once for the HITL gate above — both gates honor the same single flag (the
    # duplicate re-read left by the #524+#540 auto-merge is removed).
    citation_defect_phases = _boosted_prd_citation_defects(roadmap_id)
    if (not force_override and phase_id != "all"
            and phase_id in citation_defect_phases):
        return jsonify({
            "error": (
                f"AI-boosted PRD for {phase_id} has citation defects — it lacks "
                "valid [source: …] grounding against the scan evidence. Re-generate "
                "the PRD, or resubmit with force=true to override."
            ),
            "blocked": True,
            "citation_defect": True,
        }), 403
    if force_override and citation_defect_phases:
        try:
            _ac = _conn()
            try:
                _ac.execute(
                    "INSERT INTO aiify_audit_log (event_type, scan_id, actor, detail) "
                    "VALUES (%s, %s, %s, %s)",
                    ("citation_gate_force_override", scan_id, "user",
                     json.dumps({"roadmap_id": roadmap_id, "phase_id": phase_id,
                                 "defect_phases": sorted(citation_defect_phases)})),
                )
                _ac.commit()
            finally:
                _ac.close()
        except Exception:
            logger.warning("aiify: citation force-override audit write failed", exc_info=True)

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
            placeholders = ",".join(["%s"] * len(all_opp_ids))
            rows = conn.execute(
                f"SELECT opportunity_id, composite_score, value_score, feasibility_score, risk_score "
                f"FROM aiify_scores WHERE opportunity_id IN ({placeholders})",
                tuple(all_opp_ids),
            ).fetchall()
            for r in rows:
                score_map[r["opportunity_id"]] = dict(r)
        finally:
            conn.close()

    short_id = roadmap_id[:8]

    # Build all task specs, then create them through the canonical task_factory —
    # the single dedup + INSERT choke point (penta-aiify-01). Tasks land in
    # 'suggested' (quarantine) unless the phase PRD was explicitly approved
    # ('accept'), in which case they land in 'backlog' (directly dispatchable).
    # Never seed 'backlog' without HITL approval.
    from tools.kanban.task_factory import create_tasks

    specs: list[dict] = []
    epic_ids: set[str] = set()
    prev_epic_id: str | None = None  # inter-phase chaining when phase_id == 'all'

    for ph in target_phases:
        label   = ph.get("label", "")
        ph_key  = phase_key(ph)
        priority = PHASE_PRIORITY.get(ph_key, "medium")
        opps    = ph.get("opportunities", [])

        decision = prd_decisions.get(ph_key)
        if decision == "reject":
            # Blocked phase — never seed its tasks (fail-closed).
            continue
        if not force_override and ph_key in citation_defect_phases:
            # AI-boosted PRD failed citation validation — never seed (TRUST).
            continue
        task_status = "backlog" if decision == "accept" else "suggested"

        # ── Epic task ────────────────────────────────────────────────────────
        epic_id = f"aiify-{short_id}-{ph_key.lower()}-epic"
        subtitle = label.split("—")[1].strip() if "—" in label else label
        epic_title = f"[{ph_key} Epic] AI-ify — {subtitle}"
        epic_desc = json.dumps({
            "roadmap_id": roadmap_id, "scan_id": scan_id,
            "phase": label, "opportunity_count": len(opps),
            "total_effort_days": ph.get("total_effort_days", 0),
        })
        specs.append({
            "id": epic_id,
            "idempotency_key": epic_id,
            "title": epic_title,
            "description": epic_desc,
            "task_type": "chore",
            "priority": priority,
            "status": task_status,
            "depends_on_task_id": prev_epic_id if phase_id == "all" else None,
        })
        epic_ids.add(epic_id)
        if phase_id == "all":
            prev_epic_id = epic_id

        # ── 4 atomic child tasks per opportunity ──────────────────────────────
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

            steps = build_task_steps(
                base_id, pattern=pattern, paradigm=paradigm, module=module,
                fn=fn, model=model, criterion=criterion,
            )
            for step in steps:
                child_id = f"{base_id}-{step['suffix']}"
                dep_id = step["dep"]
                child_desc = json.dumps({**base_desc_data, "step": step["step"], "depends_on": dep_id})
                specs.append({
                    "id": child_id,
                    "idempotency_key": child_id,
                    "title": f"[{step['step']}] {step['title'][:90]}",
                    "description": child_desc,
                    "task_type": "build",
                    "priority": child_priority,
                    "status": task_status,
                    "depends_on_task_id": dep_id,
                })

    created_ids = create_tasks(specs)
    created = len(created_ids)
    skipped = len(specs) - created
    _created_set = set(created_ids)
    epics = len([e for e in epic_ids if e in _created_set])

    # Audit log (best-effort — an audit hiccup must not fail a successful create)
    try:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO aiify_audit_log (event_type, scan_id, actor, detail) VALUES (%s, %s, %s, %s)",
                ("kanban_promoted", scan_id, "user",
                 json.dumps({"roadmap_id": roadmap_id, "phase_id": phase_id,
                             "created": created, "skipped": skipped, "epics": epics})),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("aiify: send-to-kanban audit write failed", exc_info=True)

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
                    "SELECT roadmap_id FROM aiify_roadmaps WHERE scan_id = %s ORDER BY created_at DESC LIMIT 1",
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
            "SELECT scan_id, title, phases FROM aiify_roadmaps WHERE roadmap_id = %s",
            (roadmap_id,),
        ).fetchone()
        if not rm:
            return jsonify({"error": "roadmap not found"}), 404

        scan = conn.execute(
            "SELECT input_ref, project_summary, total_files, total_loc FROM aiify_scans WHERE scan_id = %s",
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
                "il_recommended_model FROM aiify_opportunities WHERE scan_id = %s",
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
            placeholders = ",".join(["%s"] * len(opp_ids))
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
                "SELECT decision FROM aiify_hitl_decisions WHERE source_type='prd' AND source_id=%s",
                (prd_key,),
            ).fetchone()
            if prd_row:
                prd_hitl_decision = prd_row["decision"]
        finally:
            aiify.close()
    except Exception:
        pass

    # Enrich with Innovation + Research + Creative engine data (HITL-filtered,
    # best-effort). Shared reader — see tools/aiify/prd_common.py.
    _enr = load_engine_enrichment(hitl=hitl, limit=3)
    innovation_signals = _enr["innovation"]
    regulatory_items = _enr["research"]
    pain_points = _enr["creative"]
    rejected_innovation = _enr["rejected_innovation"]
    rejected_research = _enr["rejected_research"]
    rejected_creative = _enr["rejected_creative"]

    prd = _build_prd(
        phase_id, target_phase, scan_dict, score_map,
        regulatory_items, pain_points, roadmap_title, innovation_signals,
        all_opportunities,
        rejected_innovation, rejected_research, rejected_creative,
    )
    try:
        import markdown as _markdown_lib
        from tools.quality.html_sanitizer import sanitize_html
        # Strict server-side allowlist sanitize: the client injects prd_html via
        # innerHTML, and the PRD embeds scan-derived (module paths) and
        # LLM-drafted prose that must not be able to smuggle <script>/handlers.
        prd_html = sanitize_html(
            _markdown_lib.markdown(prd, extensions=["tables", "fenced_code"])
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
            "SELECT scan_id, title, phases FROM aiify_roadmaps WHERE roadmap_id = %s",
            (roadmap_id,),
        ).fetchone()
        if not rm:
            return jsonify({"error": "roadmap not found"}), 404
        scan = conn.execute(
            "SELECT input_ref, project_summary, total_files, total_loc FROM aiify_scans WHERE scan_id = %s",
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
            placeholders = ",".join(["%s"] * len(opp_ids))
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
                "SELECT decision FROM aiify_hitl_decisions WHERE source_type='prd' AND source_id=%s",
                (prd_key,),
            ).fetchone()
            if prd_row:
                prd_hitl_decision = prd_row["decision"]
        finally:
            aiify.close()
    except Exception:
        pass

    # ── Generate PRD for scoring ─────────────────────────────────────────────
    # Re-use _build_prd with minimal engine data (best-effort, unfiltered top-3).
    _enr = load_engine_enrichment(hitl=None, limit=3)
    innovation_signals = _enr["innovation"]
    regulatory_items = _enr["research"]
    pain_points = _enr["creative"]

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
    citation_valid: bool | None = None  # None == not boosted

    # ── AI Boost ──────────────────────────────────────────────────────────────
    if boost_would_trigger:
        try:
            from tools.llm.router import LLMRouter
            from tools.llm.provider import LLMRequest
            from tools.quality.citation_grounding import (
                citation_gate, parse_citations, build_artifact_provenance,
            )
            router = LLMRouter()
            evidence_lines, allowed_sources = _build_prd_evidence(
                opps, innovation_signals, regulatory_items, pain_points)
            evidence_block = "\n".join(evidence_lines) if evidence_lines else \
                "- (no evidence sources available)"
            boost_prompt = (
                "You are an expert technical product manager. Improve the following PRD so it scores higher on:\n"
                "1. Detailed acceptance criteria per opportunity\n"
                "2. Clear architecture rationale and GenAI/ML component mapping\n"
                "3. Quantified business impact and risk mitigation\n"
                "4. Precise effort estimates with dependencies\n\n"
                "GROUNDING REQUIREMENT (mandatory): every factual claim MUST carry an\n"
                "inline citation of the form [source: <ID>] using ONLY ids from the\n"
                "EVIDENCE list below. Never cite an id that is not in the list. Include\n"
                "at least one citation.\n\n"
                "EVIDENCE:\n" + evidence_block + "\n\n"
                "Return ONLY the improved PRD markdown (no extra commentary).\n\n"
                f"--- PRD START ---\n{prd}\n--- PRD END ---"
            )
            # NOTE: correct router API is messages=[…] + response.content — the
            # prior LLMRequest(prompt=…)/resp.text form raised on every call and
            # silently degraded (the AI Boost never actually ran). Fixed here so
            # the citation/provenance TRUST controls have real output to gate on.
            request_obj = LLMRequest(
                messages=[{"role": "user", "content": boost_prompt}],
                agent_id="aiify-prd-boost",
                classification="CUI",
                max_tokens=4000,
                temperature=0.3,
            )
            resp = router.invoke("code_generation", request_obj)
            boosted = (getattr(resp, "content", None) or getattr(resp, "text", None) or "")
            if boosted and len(boosted) > len(prd) * 0.8:
                prd = boosted
                score = _score_prd(prd, opps, score_map)
                ai_boosted = True
                # TRUST: validate inline citations against the scan evidence and
                # persist a provenance record. send-to-kanban gates on this.
                defects = citation_gate(
                    [{"item_number": phase_id, "content": prd,
                      "allowed_sources": allowed_sources}],
                    require_citations=True,
                )
                citation_valid = not defects
                cited = [c for c in parse_citations(prd) if c in allowed_sources]
                model_name = getattr(resp, "model_id", "") or getattr(resp, "model", "") or ""
                prov = build_artifact_provenance(
                    f"{roadmap_id}:{phase_id}", cited,
                    generation_model=model_name, method="ai_boost").to_dict()
                _persist_prd_provenance(
                    roadmap_id, phase_id, ai_boosted=True, model=model_name,
                    citation_valid=citation_valid,
                    citation_report={"defects": defects, "cited": cited,
                                     "allowed_count": len(allowed_sources)},
                    evidence_ids=sorted(allowed_sources), provenance=prov)
        except Exception:
            logger.warning("aiify: AI Boost failed — graceful degrade", exc_info=True)

    # ── Kanban task preview (no insert) ─────────────────────────────────────
    short_id = roadmap_id[:8]
    ph_key = phase_key(target_phase)
    priority = PHASE_PRIORITY.get(ph_key, "medium")
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
        fn = opp.get("function_name", "")
        paradigm = opp.get("ai_paradigm", "llm_generation")
        model = opp.get("il_recommended_model", "")
        scores = score_map.get(opp_id, {})
        composite = float(scores.get("composite_score", 0.0))
        child_priority = "high" if composite >= 0.7 else priority
        base_id = f"aiify-{short_id}-{ph_key.lower()}-{opp_id}"
        # Same canonical steps the real promotion creates, so the preview titles
        # never drift from the seeded tasks (shared build_task_steps helper).
        for step in build_task_steps(
            base_id, pattern=pattern, paradigm=paradigm, module=module,
            fn=fn, model=model,
        ):
            tasks_preview.append({
                "id": f"{base_id}-{step['suffix']}",
                "title": f"[{step['step']}] {step['title'][:90]}",
                "type": "task",
                "priority": child_priority,
            })

    return jsonify({
        "score": score,
        "original_score": original_score,
        "ai_boosted": ai_boosted,
        "boost_would_trigger": boost_would_trigger,
        "citation_valid": citation_valid,
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
            "DELETE FROM aiify_hitl_decisions WHERE source_type=%s AND source_id=%s",
            (source_type, source_id),
        )
        if decision != "clear":
            conn.execute(
                "INSERT INTO aiify_hitl_decisions "
                "(source_type, source_id, phase_id, decision, reason) "
                "VALUES (%s,%s,%s,%s,%s)",
                (source_type, source_id, phase_id, decision, reason),
            )
        conn.execute(
            "INSERT INTO aiify_audit_log (event_type, actor, detail) VALUES (%s,%s,%s)",
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
@require_role(*_AIIFY_MUTATE_ROLES)
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
