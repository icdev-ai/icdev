# CUI // SP-CTI
"""AIMC — AI/ML Model Canvas Flask Blueprint.

Routes:
  GET  /ai-ml/                              index (design list + stats)
  GET  /ai-ml/canvas/new                   new design wizard
  GET  /ai-ml/canvas/<id>                  canvas editor
  GET  /ai-ml/templates                    template gallery
  GET  /ai-ml/snippets                     snippet library
  GET  /ai-ml/model-catalog                foundation model browser
  GET  /ai-ml/assessments/<id>             assessment detail

  POST /ai-ml/api/designs                  create design
  GET  /ai-ml/api/designs                  list designs (JSON)
  GET  /ai-ml/api/designs/<id>             get design (JSON)
  PUT  /ai-ml/api/designs/<id>             save design graph
  DELETE /ai-ml/api/designs/<id>           delete design

  POST /ai-ml/api/designs/<id>/assess      run canvas compliance assessment
  POST /ai-ml/api/designs/<id>/assess-gov  run governance assessment (DoD RAI + IL + OMB)
  POST /ai-ml/api/designs/<id>/artifact/model-card      generate model card
  POST /ai-ml/api/designs/<id>/artifact/deploy-manifest generate deployment manifest

  GET  /ai-ml/api/templates                list templates
  POST /ai-ml/api/templates/<tid>/apply/<did>  apply template to design
  GET  /ai-ml/api/snippets                 list snippets
  POST /ai-ml/api/snippets/<sid>/insert/<did>  insert snippet into design

  GET  /ai-ml/api/models                   list foundation model catalog
  GET  /ai-ml/api/models/rank              rank models for IL level
  POST /ai-ml/api/adapt/recommend          adaptation recommendation (no design required)
  POST /ai-ml/api/deploy/plan              deployment plan (no design required)

  GET  /ai-ml/api/designs/<id>/versions    version history
  GET  /ai-ml/api/designs/<id>/artifacts   artifact list
  GET  /ai-ml/api/stats                    dashboard stats
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import os

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

# penta-aimc-03: hard-gate AIMC mutating routes with the established dashboard
# RBAC decorator (same pattern as tools/aiify/blueprint.py, PR #514). Enforces
# login (401) + operator role (403) independent of ICDEV_ENFORCE_CANVAS_ACCESS.
# Read-only pages and GET/list JSON routes keep their current posture.
from tools.dashboard.auth import require_role

log = get_logger(__name__)

# Roles permitted to invoke AIMC mutating routes (create/save/delete designs,
# run assessments, generate artifacts, apply templates/snippets, recommend).
_AIMC_MUTATE_ROLES = ("admin", "pm", "developer", "isso")

try:
    from tools.canvas.ai_trace_mixin import record_canvas_decision as _record_decision
except Exception:
    def _record_decision(**_kw): pass  # type: ignore[assignment]

_AIMC_ENABLED = os.environ.get("ICDEV_AIML_CANVAS_ENABLED", "true").lower() not in ("0", "false", "no")


def _load_scanned_inventory(project_id: str) -> dict | None:
    """Return real scanned model inventory for ``project_id``, or None.

    Reads through ``tools.aimc.model_scanner.scan_models`` (PR #507), which uses
    the RLS-disabled canvas connection and returns an explicit ``no-data`` result
    rather than fabricating demo values. Returns the scan dict only when it has
    real rows (``status == 'success'``); otherwise None so the model-catalog page
    shows nothing extra. Never raises — the catalog page must always render.
    """
    try:
        from tools.aimc.model_scanner import scan_models
        result = scan_models(project_id)
    except Exception as exc:
        log.warning("AIMC model-catalog scanned-inventory load failed: %s", exc)
        return None
    if isinstance(result, dict) and result.get("status") == "success":
        return result
    return None


def create_aiml_blueprint() -> Blueprint | None:
    if not _AIMC_ENABLED:
        log.info("AIMC disabled via ICDEV_AIML_CANVAS_ENABLED=false")
        return None

    # Init DB on first import
    try:
        from tools.aiml_canvas.db.init_db import init_db
        init_db(verbose=False)
    except Exception as exc:
        log.warning("AIMC DB init failed: %s", exc)

    bp = Blueprint("aiml_canvas", __name__, template_folder="../../tools/dashboard/templates")

    from tools.aiml_canvas import aiml_engine as eng
    from tools.aiml_canvas import adaptation_engine as adapt_eng
    from tools.aiml_canvas import deployment_planner as dep_plan
    from tools.aiml_canvas import governance_assessor as gov_eng
    from tools.aiml_canvas.constants import (
        AIMC_NODE_PALETTE, FOUNDATION_MODELS, IL_LEVELS,
        ADAPTATION_MATRIX,
    )

    # ── Pages ─────────────────────────────────────────────────────────────────

    @bp.route("/")
    def index():
        designs = eng.list_designs()
        stats = eng.get_stats()
        return render_template(
            "aiml_canvas/index.html",
            designs=designs,
            stats=stats,
            il_levels=IL_LEVELS,
        )

    @bp.route("/canvas/new")
    def new_canvas():
        templates = eng.list_templates()
        return render_template(
            "aiml_canvas/canvas.html",
            design=None,
            templates=templates,
            palette=AIMC_NODE_PALETTE,
            models=FOUNDATION_MODELS,
            il_levels=IL_LEVELS,
            adaptation_matrix=ADAPTATION_MATRIX,
            new_design=True,
        )

    @bp.route("/canvas/<design_id>")
    def canvas(design_id: str):
        design = eng.get_design(design_id)
        if not design:
            return redirect(url_for("aiml_canvas.index"))
        templates = eng.list_templates()
        snippets = eng.list_snippets()
        return render_template(
            "aiml_canvas/canvas.html",
            design=design,
            templates=templates,
            snippets=snippets,
            palette=AIMC_NODE_PALETTE,
            models=FOUNDATION_MODELS,
            il_levels=IL_LEVELS,
            adaptation_matrix=ADAPTATION_MATRIX,
            new_design=False,
        )

    @bp.route("/templates")
    def templates_page():
        templates = eng.list_templates()
        return render_template("aiml_canvas/templates.html", templates=templates, il_levels=IL_LEVELS)

    @bp.route("/snippets")
    def snippets_page():
        snippets = eng.list_snippets()
        return render_template("aiml_canvas/snippets.html", snippets=snippets)

    @bp.route("/model-catalog")
    def model_catalog():
        # Static catalog (FOUNDATION_MODELS) plus, when present, the real scanned
        # inventory recorded in aimc_models for the given project. Empty/no-data
        # inventory yields scanned=None and the page shows only the catalog.
        project_id = request.args.get("project_id", "default")
        scanned = _load_scanned_inventory(project_id)
        return render_template(
            "aiml_canvas/model_catalog.html",
            models=FOUNDATION_MODELS,
            il_levels=IL_LEVELS,
            scanned=scanned,
            scanned_project_id=project_id,
        )

    @bp.route("/assessments/<assessment_id>")
    def assessment_detail(assessment_id: str):
        from tools.aiml_canvas.db.init_db import get_connection
        from tools.db.storage import sql_placeholder
        conn = get_connection()
        try:
            ph = sql_placeholder(conn)
            row = conn.execute(
                f"SELECT * FROM aiml_assessments WHERE id={ph}", (assessment_id,)
            ).fetchone()
            if not row:
                return redirect(url_for("aiml_canvas.index"))
            assessment = dict(row)
            assessment["findings"] = json.loads(assessment.get("findings_json") or "[]")
            design = eng.get_design(assessment["design_id"])
        finally:
            conn.close()
        return render_template("aiml_canvas/assessments.html", assessment=assessment, design=design)

    # ── API: Designs ──────────────────────────────────────────────────────────

    @bp.route("/api/designs", methods=["GET"])
    def api_list_designs():
        return jsonify(eng.list_designs())

    @bp.route("/api/designs", methods=["POST"])
    @require_role(*_AIMC_MUTATE_ROLES)
    def api_create_design():
        data = request.get_json(silent=True) or {}
        name = data.get("name", "Untitled AI/ML Design")
        design = eng.create_design(
            name=name,
            description=data.get("description", ""),
            classification=data.get("classification", "CUI"),
            il_level=data.get("il_level", "IL4"),
            primary_use_case=data.get("primary_use_case", ""),
            adaptation_strategy=data.get("adaptation_strategy", "prompt"),
            template_id=data.get("template_id"),
        )
        return jsonify(design), 201

    @bp.route("/api/designs/<design_id>", methods=["GET"])
    def api_get_design(design_id: str):
        design = eng.get_design(design_id)
        if not design:
            return jsonify({"error": "not found"}), 404
        return jsonify(design)

    @bp.route("/api/designs/<design_id>", methods=["PUT"])
    @require_role(*_AIMC_MUTATE_ROLES)
    def api_save_design(design_id: str):
        data = request.get_json(silent=True) or {}
        try:
            design = eng.save_design(
                design_id,
                graph=data.get("graph", data.get("graph_json", {})),
                name=data.get("name"),
                description=data.get("description"),
                il_level=data.get("il_level"),
                classification=data.get("classification"),
            )
            return jsonify(design)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404

    @bp.route("/api/designs/<design_id>", methods=["DELETE"])
    @require_role(*_AIMC_MUTATE_ROLES)
    def api_delete_design(design_id: str):
        deleted = eng.delete_design(design_id)
        return jsonify({"deleted": deleted}), 200 if deleted else 404

    # ── API: Assessment ───────────────────────────────────────────────────────

    @bp.route("/api/designs/<design_id>/assess", methods=["POST"])
    @require_role(*_AIMC_MUTATE_ROLES)
    def api_assess(design_id: str):
        data = request.get_json(silent=True) or {}
        use_cot = data.get("use_cot", False)
        chain_mode = "cot" if use_cot else ""
        try:
            result = eng.run_assessment(design_id)
            result["chain_mode"] = chain_mode
            _record_decision(
                canvas_type="aimc",
                record_id=design_id,
                decision_type="risk_score",
                decision=f"Score={result.get('score', result.get('overall_score', '?'))} Grade={result.get('grade', '?')}",
                rationale=f"Findings: {len(result.get('findings', []))}",
                model_used=None,
            )
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404

    @bp.route("/api/designs/<design_id>/assess-gov", methods=["POST"])
    @require_role(*_AIMC_MUTATE_ROLES)
    def api_assess_gov(design_id: str):
        design = eng.get_design(design_id)
        if not design:
            return jsonify({"error": "not found"}), 404
        result = gov_eng.run_all(design.get("graph", {}), design)
        _record_decision(
            canvas_type="aimc",
            record_id=design_id,
            decision_type="compliance_finding",
            decision=f"Gov assessment score={result.get('overall_score','?')}",
            rationale=f"IL={design.get('il_level','?')}",
            model_used=None,
        )
        return jsonify(result)

    # ── API: Adaptation Recommendation ───────────────────────────────────────
    # NOTE (penta-aimc-04): the design-scoped /api/designs/<id>/adapt and
    # /api/designs/<id>/deploy-plan endpoints were removed — the canvas UI only
    # ever calls the standalone /api/adapt/recommend and /api/deploy/plan routes.

    @bp.route("/api/adapt/recommend", methods=["POST"])
    @require_role(*_AIMC_MUTATE_ROLES)
    def api_adapt_recommend():
        data = request.get_json(silent=True) or {}
        valid_keys = {
            "has_corpus", "has_training_data", "training_examples", "has_gpu",
            "vram_gb", "latency_budget_ms", "accuracy_target_pct", "il_level",
            "knowledge_changes_frequently", "requires_source_citation",
            "requires_consistent_format", "domain_specific_reasoning",
        }
        kwargs = {k: v for k, v in data.items() if k in valid_keys}
        rec = adapt_eng.recommend(**kwargs)
        return jsonify(rec)

    # ── API: Deployment Plan ──────────────────────────────────────────────────

    @bp.route("/api/deploy/plan", methods=["POST"])
    @require_role(*_AIMC_MUTATE_ROLES)
    def api_deploy_plan_standalone():
        data = request.get_json(silent=True) or {}
        plan = dep_plan.plan(
            model_id=data.get("model_id", "qwen3-local"),
            il_level=data.get("il_level", "IL4"),
            vram_gb=float(data.get("vram_gb", 8)),
            latency_target_ms=int(data.get("latency_target_ms", 2000)),
            throughput_rps=int(data.get("throughput_rps", 1)),
            air_gap_required=data.get("air_gap_required"),
        )
        return jsonify(plan)

    # ── API: Artifacts ────────────────────────────────────────────────────────

    @bp.route("/api/designs/<design_id>/artifacts", methods=["GET"])
    def api_list_artifacts(design_id: str):
        from tools.aiml_canvas.db.init_db import get_connection
        from tools.db.storage import sql_placeholder
        conn = get_connection()
        try:
            ph = sql_placeholder(conn)
            rows = conn.execute(
                "SELECT id, design_id, artifact_type, title, format, created_at "
                f"FROM aiml_artifacts WHERE design_id={ph} ORDER BY created_at DESC",
                (design_id,),
            ).fetchall()
            return jsonify([dict(r) for r in rows])
        finally:
            conn.close()

    @bp.route("/api/designs/<design_id>/artifact/model-card", methods=["POST"])
    @require_role(*_AIMC_MUTATE_ROLES)
    def api_artifact_model_card(design_id: str):
        try:
            result = eng.generate_model_card(design_id)
            return jsonify(result), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404

    @bp.route("/api/designs/<design_id>/artifact/deploy-manifest", methods=["POST"])
    @require_role(*_AIMC_MUTATE_ROLES)
    def api_artifact_deploy_manifest(design_id: str):
        try:
            result = eng.generate_deployment_manifest(design_id)
            return jsonify(result), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404

    # ── API: Templates + Snippets ─────────────────────────────────────────────

    @bp.route("/api/templates", methods=["GET"])
    def api_list_templates():
        return jsonify(eng.list_templates())

    @bp.route("/api/templates/<template_id>/apply/<design_id>", methods=["POST"])
    @require_role(*_AIMC_MUTATE_ROLES)
    def api_apply_template(template_id: str, design_id: str):
        tpl = eng.get_template(template_id)
        if not tpl:
            return jsonify({"error": "template not found"}), 404
        design = eng.get_design(design_id)
        if not design:
            return jsonify({"error": "design not found"}), 404
        saved = eng.save_design(design_id, graph=tpl["graph"])
        return jsonify(saved)

    @bp.route("/api/snippets", methods=["GET"])
    def api_list_snippets():
        cat = request.args.get("category")
        return jsonify(eng.list_snippets(category=cat))

    @bp.route("/api/snippets/<snippet_id>/insert/<design_id>", methods=["POST"])
    @require_role(*_AIMC_MUTATE_ROLES)
    def api_insert_snippet(snippet_id: str, design_id: str):
        snippets = eng.list_snippets()
        snp = next((s for s in snippets if s["id"] == snippet_id), None)
        if not snp:
            return jsonify({"error": "snippet not found"}), 404
        design = eng.get_design(design_id)
        if not design:
            return jsonify({"error": "design not found"}), 404
        data = request.get_json(silent=True) or {}
        offset_x = float(data.get("offset_x", 100))
        offset_y = float(data.get("offset_y", 100))

        graph = design.get("graph", {"nodes": [], "edges": [], "boundaries": []})
        snp_graph = snp.get("graph", {})
        import uuid as _uuid
        id_map: dict[str, str] = {}

        for node in snp_graph.get("nodes", []):
            new_id = str(_uuid.uuid4())
            id_map[node["id"]] = new_id
            new_node = {**node, "id": new_id,
                        "x": node.get("x", 0) + offset_x,
                        "y": node.get("y", 0) + offset_y}
            graph["nodes"].append(new_node)

        for edge in snp_graph.get("edges", []):
            new_edge = {**edge, "id": str(_uuid.uuid4()),
                        "source": id_map.get(edge["source"], edge["source"]),
                        "target": id_map.get(edge["target"], edge["target"])}
            graph["edges"].append(new_edge)

        saved = eng.save_design(design_id, graph=graph)
        return jsonify(saved)

    # ── API: Models ───────────────────────────────────────────────────────────

    @bp.route("/api/models", methods=["GET"])
    def api_list_models():
        model_type = request.args.get("type")
        models = FOUNDATION_MODELS
        if model_type:
            models = [m for m in models if m.get("type") == model_type]
        return jsonify(models)

    @bp.route("/api/models/rank", methods=["GET"])
    def api_rank_models():
        il_level = request.args.get("il_level", "IL4")
        ranked = adapt_eng.rank_models_for_il(il_level)
        return jsonify(ranked)

    @bp.route("/api/scanned-inventory", methods=["GET"])
    def api_scanned_inventory():
        """Real scanned model inventory (aimc_models) for the model catalog.

        Returns the scanner's ``success`` payload when inventory exists, or an
        explicit ``no-data`` result — never fabricated demo values.
        """
        project_id = request.args.get("project_id", "default")
        result = _load_scanned_inventory(project_id)
        if result is None:
            return jsonify({"status": "no-data", "project_id": project_id})
        return jsonify(result)

    # ── API: Versions ─────────────────────────────────────────────────────────

    @bp.route("/api/designs/<design_id>/versions", methods=["GET"])
    def api_list_versions(design_id: str):
        return jsonify(eng.list_versions(design_id))

    @bp.route("/api/designs/<design_id>/versions/<version_id>", methods=["GET"])
    def api_get_version(design_id: str, version_id: str):
        v = eng.get_version(version_id)
        if not v or v["design_id"] != design_id:
            return jsonify({"error": "not found"}), 404
        return jsonify(v)

    # ── API: Stats ────────────────────────────────────────────────────────────

    @bp.route("/api/stats", methods=["GET"])
    def api_stats():
        return jsonify(eng.get_stats())

    # ── API: AADC Bridge — backref lookup ─────────────────────────────────────

    @bp.route("/api/models/<model_id>/aadc-refs", methods=["GET"])
    def api_model_aadc_refs(model_id: str):
        """Return AADC designs that have linked this AIMC model."""
        try:
            from tools.agentic_ai_canvas.canvas_bridge import get_aadc_refs_for_model
            refs = get_aadc_refs_for_model(model_id)
            return jsonify({"model_id": model_id, "aadc_refs": refs, "count": len(refs)})
        except Exception:
            return jsonify({"model_id": model_id, "aadc_refs": [], "count": 0})

    @bp.route("/api/iqe-query", methods=["POST"])
    def aimc_api_iqe_query():
        """IQE structured query — translate NL to IQE and execute against AIMC model data."""
        import logging as _log
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import IQESyntaxError, parse
        from tools.iqe.executor import execute_query
        import tools.iqe.adapters.aimc  # noqa: F401 — registers aimc.* collections

        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400

        collections = ["aimc.designs", "aimc.nodes", "aimc.assessments", "aimc.artifacts"]
        translation = nl_to_iqe(question, collections)
        iqe_str = translation.get("iqe", "")
        explanation = translation.get("explanation", "")

        if not data.get("execute", True):
            return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation}), 200

        try:
            ast = parse(iqe_str)
            rows = execute_query(ast, None)
            return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation,
                            "results": rows, "row_count": len(rows)}), 200
        except IQESyntaxError as exc:
            return jsonify({"error": f"IQE syntax error: {exc}", "iqe": iqe_str}), 400
        except Exception as exc:
            _log.getLogger(__name__).warning("AIMC IQE query error: %s", exc)
            return jsonify({"error": str(exc), "iqe": iqe_str}), 500

    @bp.route("/modernize")
    def modernize_page():
        from tools.aiml_canvas.modernization_bridge import (
            LANGUAGES, ARCHITECTURES, DATA_STATES, USER_GOALS, TEAM_AI_READINESS,
        )
        from apps.forge_academy.patterns import INJECTION_PATTERNS
        return render_template(
            "aiml_canvas/modernize.html",
            languages=LANGUAGES,
            architectures=ARCHITECTURES,
            data_states=DATA_STATES,
            user_goals=USER_GOALS,
            team_readiness_options=TEAM_AI_READINESS,
            patterns=INJECTION_PATTERNS,
        )

    @bp.route("/api/modernize/recommend", methods=["POST"])
    @require_role(*_AIMC_MUTATE_ROLES)
    def api_modernize_recommend():
        data = request.get_json(silent=True) or {}
        from tools.aiml_canvas.modernization_bridge import recommend_json
        try:
            result = recommend_json({
                "language": data.get("language", ""),
                "architecture": data.get("architecture", ""),
                "data_state": data.get("data_state", ""),
                "goal": data.get("goal", ""),
                "team_readiness": data.get("team_readiness", ""),
            })
            return jsonify({"ok": True, "recommendation": result})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/ai-trace")
    def aimc_api_ai_trace():
        """Return recent AI decisions made by AIMC assessment engines.

        canvas_ai_decisions is written through the RLS-aware
        tools.db.storage.get_connection (see tools/canvas/ai_trace_mixin.py), so
        it is read back through the same connection. Placeholders are derived
        from sql_placeholder() rather than hardcoded to %s so the SQLite
        init-fallback path works too, and query failures are logged instead of
        being swallowed silently.
        """
        limit = min(int(request.args.get("limit", 50)), 200)
        record_id = request.args.get("record_id")
        try:
            from tools.db.storage import get_connection as _gc, sql_placeholder
            with _gc() as _conn:
                ph = sql_placeholder(_conn)
                if record_id:
                    rows = _conn.execute(
                        f"SELECT * FROM canvas_ai_decisions "
                        f"WHERE canvas_type={ph} AND record_id={ph} "
                        f"ORDER BY created_at DESC LIMIT {ph}",
                        ("aimc", record_id, limit),
                    ).fetchall()
                else:
                    rows = _conn.execute(
                        f"SELECT * FROM canvas_ai_decisions WHERE canvas_type={ph} "
                        f"ORDER BY created_at DESC LIMIT {ph}",
                        ("aimc", limit),
                    ).fetchall()
            return jsonify({"ok": True, "canvas": "aimc", "decisions": [dict(r) for r in rows]})
        except Exception as exc:
            log.warning("AIMC ai-trace query failed: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    return bp
