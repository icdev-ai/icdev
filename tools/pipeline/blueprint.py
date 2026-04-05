# CUI // SP-CTI
"""ICDEV™ Pipeline Design Canvas — Flask Blueprint integration.

Fully self-contained Blueprint mounted at /devops/ inside the ICDEV dashboard.
Uses ICDEV's auth system, separate pipeline_canvas.db, and feature flag
ICDEV_PIPELINE_ENABLED.

Usage in ICDEV dashboard app.py:
    from tools.pipeline.blueprint import create_pipeline_blueprint
    bp = create_pipeline_blueprint()
    if bp:
        app.register_blueprint(bp, url_prefix="/devops")
"""

import json
import logging
import os
import uuid as _uuid
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint, jsonify, redirect, render_template,
    request, session, g,
)

logger = logging.getLogger("icdev.pipeline")

_PIPELINE_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _PIPELINE_DIR.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"

# ── Import pipeline constants ─────────────────────────────────────────────────
from tools.pipeline.constants import (  # noqa: E402
    PIPELINE_STAGES, PIPELINE_OBJECTS, CSP_SERVICE_EQUIVALENCE,
    PIPELINE_COMPLIANCE_FRAMEWORKS, PIPELINE_COMPLIANCE_RULES,
    compute_owasp_coverage, estimate_pipeline_cost, estimate_execution_time,
)
from tools.common.helpers import row_to_dict, now_isoformat  # noqa: E402
from tools.pipeline.db.init_db import get_connection, init_db  # noqa: E402
from tools.pipeline.runbooks import (  # noqa: E402
    get_all_runbooks as _pdc_get_all_runbooks,
    get_runbook_by_id as _pdc_get_runbook_by_id,
)

# ── Optional imports from existing ICDEV modules ─────────────────────────────
try:
    from tools.compliance.slsa_attestation_generator import SLSA_LEVEL_REQUIREMENTS
except ImportError:
    SLSA_LEVEL_REQUIREMENTS = {}

try:
    import yaml
    _config_path = _ICDEV_ROOT / "args" / "pipeline_canvas_config.yaml"
    if _config_path.exists():
        with open(_config_path, encoding="utf-8") as f:
            PC_CONFIG = yaml.safe_load(f) or {}
    else:
        PC_CONFIG = {}
except Exception:
    PC_CONFIG = {}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _audit(action, entity_type, entity_id, details="", user_id=None):
    """Write an audit log entry."""
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO pc_audit (action, entity_type, entity_id, details, user_id, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (action, entity_type, entity_id, details,
             user_id or session.get("user_id", "system"), now_isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Audit write failed: %s", exc)


# ── Blueprint Factory ─────────────────────────────────────────────────────────

def create_pipeline_blueprint():
    """Create and return the Pipeline Design Canvas Blueprint.

    Returns None if ICDEV_PIPELINE_ENABLED is false.
    """
    enabled = os.environ.get("ICDEV_PIPELINE_ENABLED", "true").lower()
    if enabled not in ("true", "1", "yes"):
        logger.info("Pipeline Canvas disabled (ICDEV_PIPELINE_ENABLED=%s)", enabled)
        return None

    # Initialize DB
    try:
        init_db()
    except Exception as exc:
        logger.warning("Pipeline DB init failed: %s", exc)

    bp = Blueprint(
        "pipeline_canvas",
        __name__,
        template_folder=str(_TEMPLATE_DIR),
    )

    # ── Auth decorator ────────────────────────────────────────────────────
    def pc_login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("user_id"):
                if request.is_json or request.path.startswith("/devops/api/"):
                    return jsonify({"error": "Authentication required"}), 401
                return redirect("/login")
            return f(*args, **kwargs)
        return decorated

    # ── Context processor ─────────────────────────────────────────────────
    @bp.context_processor
    def inject_pc_context():
        user = None
        try:
            user = getattr(g, "current_user", None)
        except RuntimeError:
            pass
        return {
            "classification_banner": PC_CONFIG.get("app", {}).get("classification", ""),
            "current_user": user,
        }

    # ══════════════════════════════════════════════════════════════════════
    # PAGE ROUTES
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/")
    @pc_login_required
    def pc_index():
        conn = get_connection()
        pipelines = [row_to_dict(r) for r in conn.execute(
            "SELECT id, name, description, classification, target_csp, "
            "created_at, updated_at FROM pipelines ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()]
        templates = [row_to_dict(r) for r in conn.execute(
            "SELECT id, name, category, description, tags "
            "FROM pc_templates ORDER BY category, name"
        ).fetchall()]
        snippets = [row_to_dict(r) for r in conn.execute(
            "SELECT id, name, category, description, tags "
            "FROM pc_snippets ORDER BY category, name"
        ).fetchall()]
        conn.close()
        return render_template(
            "pipeline/index.html",
            pipelines=pipelines,
            templates=templates,
            snippets=snippets,
            stages=PIPELINE_STAGES,
        )

    @bp.route("/canvas/new")
    @pc_login_required
    def pc_new_canvas():
        return render_template(
            "pipeline/canvas.html",
            pipeline_id="new",
            pipeline_name="Untitled Pipeline",
            graph_json=json.dumps({"nodes": [], "edges": []}),
            stages=PIPELINE_STAGES,
            objects=PIPELINE_OBJECTS,
        )

    @bp.route("/canvas/<pipe_id>")
    @pc_login_required
    def pc_edit_canvas(pipe_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM pipelines WHERE id=?", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return redirect("/devops/canvas/new")
        pipe = row_to_dict(row)
        return render_template(
            "pipeline/canvas.html",
            pipeline_id=pipe["id"],
            pipeline_name=pipe["name"],
            graph_json=pipe["graph_json"],
            stages=PIPELINE_STAGES,
            objects=PIPELINE_OBJECTS,
        )

    # ══════════════════════════════════════════════════════════════════════
    # API — PIPELINE CRUD
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/health")
    def pc_health():
        return jsonify({"status": "ok", "module": "pipeline_canvas"})

    @bp.route("/api/pipelines", methods=["GET"])
    @pc_login_required
    def pc_api_list():
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, description, classification, target_csp, "
            "created_at, updated_at FROM pipelines ORDER BY updated_at DESC"
        ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    @bp.route("/api/pipelines", methods=["POST"])
    @pc_login_required
    def pc_api_create():
        data = request.get_json(force=True, silent=True) or {}
        # Input validation
        if len(json.dumps(data)) > 5_000_000:  # 5MB max
            return jsonify({"error": "Payload too large"}), 413
        pipe_id = str(_uuid.uuid4())
        name = data.get("name", "Untitled Pipeline")[:200]  # Limit name length
        logger.info("Creating pipeline: %s (%s)", name, pipe_id)
        conn = get_connection()
        conn.execute(
            "INSERT INTO pipelines (id, name, description, graph_json, classification, "
            "target_csp, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (pipe_id, name, data.get("description", ""),
             data.get("graph_json", '{"nodes":[],"edges":[]}'),
             data.get("classification", "public"),
             data.get("target_csp", "generic"), now_isoformat(), now_isoformat()),
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "pipeline", pipe_id, name)
        return jsonify({"id": pipe_id, "name": name}), 201

    @bp.route("/api/pipelines/<pipe_id>", methods=["GET"])
    @pc_login_required
    def pc_api_get(pipe_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM pipelines WHERE id=?", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify(row_to_dict(row))

    @bp.route("/api/pipelines/<pipe_id>", methods=["PUT"])
    @pc_login_required
    def pc_api_update(pipe_id):
        data = request.get_json(force=True, silent=True) or {}
        if len(json.dumps(data)) > 5_000_000:
            return jsonify({"error": "Payload too large"}), 413
        logger.info("Updating pipeline: %s", pipe_id)
        conn = get_connection()
        conn.execute(
            "UPDATE pipelines SET name=?, description=?, graph_json=?, "
            "classification=?, target_csp=?, updated_at=? WHERE id=?",
            (data.get("name", ""), data.get("description", ""),
             data.get("graph_json", "{}"), data.get("classification", "public"),
             data.get("target_csp", "generic"), now_isoformat(), pipe_id),
        )
        conn.commit()
        conn.close()
        _audit("UPDATE", "pipeline", pipe_id, data.get("name", ""))
        # Hook: notify Security Design Canvas of pipeline change
        try:
            from tools.security_canvas.agent import on_pdc_pipeline_saved
            graph_raw = data.get("graph_json", "{}")
            graph = json.loads(graph_raw) if isinstance(graph_raw, str) else graph_raw
            on_pdc_pipeline_saved(pipe_id, graph)
        except Exception:
            pass  # Security Canvas is optional
        # Incremental KG update: re-extract only if graph_json changed
        try:
            from tools.canvas.kg_builder import rebuild_canvas_kg
            rebuild_canvas_kg("pdc", pipe_id)
        except Exception:
            pass
        return jsonify({"updated": True})

    @bp.route("/api/pipelines/<pipe_id>", methods=["DELETE"])
    @pc_login_required
    def pc_api_delete(pipe_id):
        logger.info("Deleting pipeline: %s", pipe_id)
        conn = get_connection()
        conn.execute("DELETE FROM pipelines WHERE id=?", (pipe_id,))
        conn.commit()
        conn.close()
        _audit("DELETE", "pipeline", pipe_id, "")
        return jsonify({"deleted": True})

    # ══════════════════════════════════════════════════════════════════════
    # API — TEMPLATES & SNIPPETS
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/templates", methods=["GET"])
    @pc_login_required
    def pc_api_list_templates():
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, category, description, tags "
            "FROM pc_templates ORDER BY category, name"
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = row_to_dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except Exception:
                d["tags"] = []
            result.append(d)
        return jsonify(result)

    @bp.route("/api/templates/<tpl_id>", methods=["GET"])
    @pc_login_required
    def pc_api_get_template(tpl_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM pc_templates WHERE id=?", (tpl_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = row_to_dict(row)
        try:
            d["graph_json"] = json.loads(d["graph_json"])
        except Exception:
            d["graph_json"] = {"nodes": [], "edges": []}
        return jsonify(d)

    @bp.route("/api/templates/<tpl_id>/load", methods=["POST"])
    @pc_login_required
    def pc_api_load_template(tpl_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM pc_templates WHERE id=?", (tpl_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        tpl = row_to_dict(row)
        pipe_id = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO pipelines (id, name, description, graph_json, template_id, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (pipe_id, f"{tpl['name']} (copy)", tpl.get("description", ""),
             tpl["graph_json"], tpl_id, now_isoformat(), now_isoformat()),
        )
        conn.commit()
        conn.close()
        _audit("LOAD_TEMPLATE", "pipeline", pipe_id, tpl["name"])
        return jsonify({"id": pipe_id, "name": f"{tpl['name']} (copy)"}), 201

    @bp.route("/api/snippets", methods=["GET"])
    @pc_login_required
    def pc_api_list_snippets():
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, category, description, classification_level, "
            "impact_level, slsa_level, tags FROM pc_snippets ORDER BY category, name"
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = row_to_dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except Exception:
                d["tags"] = []
            result.append(d)
        return jsonify(result)

    @bp.route("/api/snippets/<snip_id>", methods=["GET"])
    @pc_login_required
    def pc_api_get_snippet(snip_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM pc_snippets WHERE id=?", (snip_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = row_to_dict(row)
        try:
            d["graph_json"] = json.loads(d["graph_json"])
        except Exception:
            d["graph_json"] = {"nodes": [], "edges": []}
        return jsonify(d)

    @bp.route("/api/snippets/<snip_id>/load", methods=["POST"])
    @pc_login_required
    def pc_api_load_snippet(snip_id):
        """Create a new pipeline from a snippet (like template load)."""
        conn = get_connection()
        row = conn.execute("SELECT * FROM pc_snippets WHERE id=?", (snip_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        snip = row_to_dict(row)
        pipe_id = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO pipelines (id, name, description, graph_json, classification, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (pipe_id, f"{snip['name']} (copy)", snip.get("description", ""),
             snip["graph_json"], snip.get("classification_level", "CUI"),
             now_isoformat(), now_isoformat()),
        )
        conn.commit()
        conn.close()
        _audit("LOAD_SNIPPET", "pipeline", pipe_id, snip["name"])
        return jsonify({"id": pipe_id, "name": f"{snip['name']} (copy)"}), 201

    # ══════════════════════════════════════════════════════════════════════
    # API — OBJECT LIBRARY & CSP EQUIVALENCE
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/pipeline-objects")
    @pc_login_required
    def pc_api_objects():
        return jsonify(PIPELINE_OBJECTS)

    @bp.route("/api/pipeline-stages")
    @pc_login_required
    def pc_api_stages():
        return jsonify(PIPELINE_STAGES)

    @bp.route("/api/csp-equivalence")
    @pc_login_required
    def pc_api_csp_equivalence():
        return jsonify(CSP_SERVICE_EQUIVALENCE)

    @bp.route("/api/csp-equivalence/<service_key>")
    @pc_login_required
    def pc_api_csp_equivalence_detail(service_key):
        eq = CSP_SERVICE_EQUIVALENCE.get(service_key)
        if not eq:
            return jsonify({"error": "Unknown service key"}), 404
        return jsonify(eq)

    @bp.route("/api/csp-equivalence/<service_key>/<target_csp>")
    @pc_login_required
    def pc_api_csp_equiv_single(service_key, target_csp):
        eq = CSP_SERVICE_EQUIVALENCE.get(service_key, {})
        csp_data = eq.get(target_csp)
        if not csp_data:
            return jsonify({"error": f"No mapping for {service_key}/{target_csp}"}), 404
        return jsonify(csp_data)

    # ══════════════════════════════════════════════════════════════════════
    # API — ANALYSIS (calls existing ICDEV tools)
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/pipelines/<pipe_id>/analyze", methods=["POST"])
    @pc_login_required
    def pc_api_analyze(pipe_id):
        """Run analysis on a pipeline. Body: {analysis_type: "security_coverage"|"cost"|"execution_time"|"slsa"|"compliance"|"antipatterns"}."""
        logger.info("Analyzing pipeline %s", pipe_id)
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM pipelines WHERE id=?", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404

        graph = json.loads(row["graph_json"])
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        node_types = [n.get("type", "") for n in nodes]

        data = request.get_json(force=True, silent=True) or {}
        analysis_type = data.get("analysis_type", "security_coverage")

        if analysis_type == "security_coverage":
            result = compute_owasp_coverage(node_types)
        elif analysis_type == "cost":
            runs = data.get("runs_per_month", 500)
            result = estimate_pipeline_cost(node_types, runs)
        elif analysis_type == "execution_time":
            result = estimate_execution_time(nodes, edges)
        elif analysis_type == "slsa":
            result = _assess_slsa(nodes, edges)
        elif analysis_type == "compliance":
            result = _run_compliance_check(nodes, edges)
        elif analysis_type == "antipatterns":
            try:
                from tools.pipeline.antipattern_detector import detect_antipatterns
                result = {"findings": detect_antipatterns(nodes, edges), "total": 0}
                result["total"] = len(result["findings"])
            except Exception as exc:
                result = {"findings": [], "total": 0, "error": str(exc)}
        else:
            return jsonify({"error": f"Unknown analysis type: {analysis_type}"}), 400

        _audit("ANALYZE", "pipeline", pipe_id, analysis_type)
        return jsonify({"analysis_type": analysis_type, "result": result})

    # ══════════════════════════════════════════════════════════════════════
    # API — COMPLIANCE AUDIT
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/compliance/<pipe_id>/audit", methods=["POST"])
    @pc_login_required
    def pc_api_compliance_audit(pipe_id):
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM pipelines WHERE id=?", (pipe_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        graph = json.loads(row["graph_json"])
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        result = _run_compliance_check(nodes, edges)

        # Persist findings
        check_id = str(_uuid.uuid4())[:8]
        conn.execute(
            "INSERT INTO pc_compliance_checks (id, pipeline_id, check_type, passed, failed, findings_json, ran_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (check_id, pipe_id, "full_audit", result["passed"], result["failed"],
             json.dumps(result["findings"]), now_isoformat()),
        )
        conn.commit()
        conn.close()
        _audit("COMPLIANCE_AUDIT", "pipeline", pipe_id, f"passed={result['passed']}, failed={result['failed']}")
        return jsonify(result)

    # ══════════════════════════════════════════════════════════════════════
    # API — VERSIONS
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/versions/<pipe_id>", methods=["GET"])
    @pc_login_required
    def pc_api_list_versions(pipe_id):
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, version_num, label, created_by, notes, created_at "
            "FROM pc_versions WHERE pipeline_id=? ORDER BY version_num DESC", (pipe_id,)
        ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    @bp.route("/api/versions/<pipe_id>", methods=["POST"])
    @pc_login_required
    def pc_api_create_version(pipe_id):
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM pipelines WHERE id=?", (pipe_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        max_ver = conn.execute(
            "SELECT COALESCE(MAX(version_num), 0) FROM pc_versions WHERE pipeline_id=?", (pipe_id,)
        ).fetchone()[0]
        data = request.get_json(force=True, silent=True) or {}
        ver_id = str(_uuid.uuid4())[:8]
        conn.execute(
            "INSERT INTO pc_versions (id, pipeline_id, version_num, label, graph_json, created_by, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ver_id, pipe_id, max_ver + 1, data.get("label", f"v{max_ver + 1}"),
             row["graph_json"], session.get("user_id", "system"),
             data.get("notes", ""), now_isoformat()),
        )
        conn.commit()
        conn.close()
        return jsonify({"id": ver_id, "version_num": max_ver + 1}), 201

    # ══════════════════════════════════════════════════════════════════════
    # API — BOUNDARIES (Security Zones / Stage Fencing)
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/boundaries/<pipe_id>", methods=["GET"])
    @pc_login_required
    def pc_api_list_boundaries(pipe_id):
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM pc_boundaries WHERE pipeline_id=?", (pipe_id,)
        ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    @bp.route("/api/boundaries/<pipe_id>", methods=["POST"])
    @pc_login_required
    def pc_api_create_boundary(pipe_id):
        data = request.get_json(force=True, silent=True) or {}
        bid = str(_uuid.uuid4())[:8]
        conn = get_connection()
        conn.execute(
            "INSERT INTO pc_boundaries (id, pipeline_id, label, classification, color, "
            "fill_opacity, node_ids, boundary_type, pos_x, pos_y, width, height) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (bid, pipe_id, data.get("label", "Stage Boundary"),
             data.get("classification", "CUI"), data.get("color", "#e94560"),
             data.get("fill_opacity", 0.08),
             json.dumps(data.get("node_ids", [])),
             data.get("boundary_type", "security_zone"),
             data.get("pos_x", 0), data.get("pos_y", 0),
             data.get("width", 400), data.get("height", 300)),
        )
        conn.commit()
        conn.close()
        return jsonify({"id": bid}), 201

    @bp.route("/api/boundaries/<pipe_id>/<bid>", methods=["DELETE"])
    @pc_login_required
    def pc_api_delete_boundary(pipe_id, bid):
        conn = get_connection()
        conn.execute("DELETE FROM pc_boundaries WHERE id=? AND pipeline_id=?", (bid, pipe_id))
        conn.commit()
        conn.close()
        return jsonify({"deleted": True})

    # ══════════════════════════════════════════════════════════════════════
    # API — EXPORT
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/export/<pipe_id>", methods=["POST"])
    @pc_login_required
    def pc_api_export(pipe_id):
        """Export pipeline to various formats."""
        logger.info("Exporting pipeline %s", pipe_id)
        conn = get_connection()
        row = conn.execute("SELECT * FROM pipelines WHERE id=?", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        pipe = row_to_dict(row)
        graph = json.loads(pipe["graph_json"])
        data = request.get_json(force=True, silent=True) or {}
        fmt = data.get("format", "gitlab_ci")

        try:
            from tools.pipeline.export import export_pipeline
            result = export_pipeline(graph, pipe["name"], fmt)
        except ImportError:
            result = {"format": fmt, "content": f"# Export format '{fmt}' — module not yet loaded", "filename": f"pipeline.{fmt}"}
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        _audit("EXPORT", "pipeline", pipe_id, fmt)
        return jsonify(result)

    # ══════════════════════════════════════════════════════════════════════
    # API — VALIDATE IaC
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/validate/<pipe_id>", methods=["POST"])
    @pc_login_required
    def pc_api_validate(pipe_id):
        """Validate generated IaC through the 5-layer pyramid."""
        logger.info("Validating IaC for pipeline %s", pipe_id)
        conn = get_connection()
        row = conn.execute("SELECT * FROM pipelines WHERE id=?", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        pipe = row_to_dict(row)
        graph = json.loads(pipe["graph_json"])
        data = request.get_json(force=True, silent=True) or {}

        try:
            from tools.pipeline.iac_validator import validate_deploy_bundle_from_generator
            result = validate_deploy_bundle_from_generator(
                graph, pipe["name"],
                target_csp=data.get("target_csp", "auto"),
                max_layer=int(data.get("max_layer", 3)),
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        _audit("VALIDATE_IAC", "pipeline", pipe_id, f"gate={result.get('validation', {}).get('gate', '?')}")
        return jsonify(result)

    # ══════════════════════════════════════════════════════════════════════
    # API — DEPLOY IaC BUNDLE
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/deploy/<pipe_id>", methods=["POST"])
    @pc_login_required
    def pc_api_deploy(pipe_id):
        """Generate IaC deployment bundle."""
        logger.info("Generating deploy bundle for pipeline %s", pipe_id)
        conn = get_connection()
        row = conn.execute("SELECT * FROM pipelines WHERE id=?", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        pipe = row_to_dict(row)
        graph = json.loads(pipe["graph_json"])
        data = request.get_json(force=True, silent=True) or {}

        try:
            from tools.pipeline.deploy_generator import generate_deploy_bundle
            result = generate_deploy_bundle(
                graph, pipe["name"],
                target_csp=data.get("target_csp", "auto"),
                options=data.get("options", {}),
            )
        except Exception as exc:
            logger.warning("Deploy generation failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

        _audit("DEPLOY_GENERATE", "pipeline", pipe_id, result.get("summary", ""))

        # Check if zip download requested
        if data.get("format") == "zip":
            import io
            import zipfile
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in result["files"]:
                    zf.writestr(f"devops-deploy/{f['path']}", f["content"])
            buf.seek(0)
            from flask import send_file
            safe = pipe["name"].replace(" ", "-").lower()[:30]
            return send_file(
                buf,
                mimetype="application/zip",
                as_attachment=True,
                download_name=f"{safe}-deploy-bundle.zip",
            )

        return jsonify({
            "summary": result["summary"],
            "files": [f["path"] for f in result["files"]],
            "manifest": result["manifest"],
            "file_contents": {f["path"]: f["content"] for f in result["files"]},
        })

    # ══════════════════════════════════════════════════════════════════════
    # API — HEATMAP DATA
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/heatmap/<pipe_id>", methods=["GET"])
    @pc_login_required
    def pc_api_heatmap(pipe_id):
        """Get heatmap data. Query: ?type=execution_time|findings|compliance|freshness"""
        heatmap_type = request.args.get("type", "execution_time")
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM pipelines WHERE id=?", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        graph = json.loads(row["graph_json"])
        nodes = graph.get("nodes", [])

        heatmap = {}
        for node in nodes:
            nid = node.get("id", "")
            config = node.get("config") or {}
            if heatmap_type == "execution_time":
                minutes = config.get("avg_execution_min", 5)
                heatmap[nid] = {"value": minutes, "color": _time_color(minutes)}
            elif heatmap_type == "findings":
                findings = config.get("findings_count", 0)
                heatmap[nid] = {"value": findings, "color": _findings_color(findings)}
            elif heatmap_type == "compliance":
                pct = config.get("compliance_pct", 100)
                heatmap[nid] = {"value": pct, "color": _compliance_color(pct)}
            elif heatmap_type == "freshness":
                age_days = config.get("tool_age_days", 0)
                heatmap[nid] = {"value": age_days, "color": _age_color(age_days)}

        return jsonify({"type": heatmap_type, "data": heatmap})

    # ══════════════════════════════════════════════════════════════════════
    # API — CHANGE REQUESTS
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/change-requests/<pipe_id>", methods=["GET"])
    @pc_login_required
    def pc_api_list_crs(pipe_id):
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM pc_change_requests WHERE pipeline_id=? ORDER BY created_at DESC",
            (pipe_id,)
        ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    @bp.route("/api/change-requests/<pipe_id>", methods=["POST"])
    @pc_login_required
    def pc_api_create_cr(pipe_id):
        data = request.get_json(force=True, silent=True) or {}
        cr_id = str(_uuid.uuid4())[:8]
        conn = get_connection()
        conn.execute(
            "INSERT INTO pc_change_requests (id, pipeline_id, cr_number, cr_type, status, "
            "markup_json, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (cr_id, pipe_id, data.get("cr_number", f"CR-{cr_id[:4]}"),
             data.get("cr_type", "modify"), "draft",
             json.dumps(data.get("markup", [])),
             session.get("user_id", "system"), now_isoformat(), now_isoformat()),
        )
        conn.commit()
        conn.close()
        return jsonify({"id": cr_id}), 201

    # ══════════════════════════════════════════════════════════════════════
    # API — DESIGN RULES
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/design-rules/node/<node_type>")
    @pc_login_required
    def pc_api_design_rules_node(node_type):
        """Get design rules for a node type from pipeline_design_rules.yaml."""
        try:
            rules_path = _ICDEV_ROOT / "args" / "pipeline_design_rules.yaml"
            if yaml and rules_path.exists():
                with open(rules_path, encoding="utf-8") as f:
                    rules = yaml.safe_load(f) or {}
                node_rules = rules.get("on_node_add", {}).get(node_type, {})
                return jsonify(node_rules)
        except Exception:
            pass
        return jsonify({})

    # ══════════════════════════════════════════════════════════════════════
    # API — COMPLIANCE FRAMEWORKS
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/compliance-frameworks")
    @pc_login_required
    def pc_api_frameworks():
        return jsonify(PIPELINE_COMPLIANCE_FRAMEWORKS)

    @bp.route("/api/compliance-rules")
    @pc_login_required
    def pc_api_compliance_rules():
        return jsonify(PIPELINE_COMPLIANCE_RULES)

    @bp.route("/api/slsa-levels")
    @pc_login_required
    def pc_api_slsa_levels():
        return jsonify(SLSA_LEVEL_REQUIREMENTS)

    # ══════════════════════════════════════════════════════════════════════
    # API — SCORECARD (calls existing DevSecOps profile manager)
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/pipelines/<pipe_id>/scorecard", methods=["GET"])
    @pc_login_required
    def pc_api_scorecard(pipe_id):
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM pipelines WHERE id=?", (pipe_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        graph = json.loads(row["graph_json"])
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        node_types = [n.get("type", "") for n in nodes]

        # Compute scorecard
        owasp = compute_owasp_coverage(node_types)
        slsa = _assess_slsa(nodes, edges)
        compliance = _run_compliance_check(nodes, edges)
        cost = estimate_pipeline_cost(node_types)
        exec_time = estimate_execution_time(nodes, edges)

        # Anti-pattern detection
        try:
            from tools.pipeline.antipattern_detector import detect_antipatterns
            antipatterns = detect_antipatterns(nodes, edges)
        except Exception:
            antipatterns = []

        scorecard = {
            "security_coverage": owasp,
            "slsa_level": slsa,
            "compliance": {
                "passed": compliance["passed"],
                "failed": compliance["failed"],
                "score_pct": round(compliance["passed"] / max(compliance["passed"] + compliance["failed"], 1) * 100, 1),
                "findings": compliance.get("findings", []),
            },
            "antipatterns": {
                "total": len(antipatterns),
                "critical": len([a for a in antipatterns if a["severity"] == "critical"]),
                "high": len([a for a in antipatterns if a["severity"] == "high"]),
                "medium": len([a for a in antipatterns if a["severity"] == "medium"]),
                "findings": antipatterns,
            },
            "cost_estimate": cost,
            "execution_time": exec_time,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "stages_covered": len(set(n.get("stage", "") for n in nodes if n.get("stage"))),
            "total_stages": len(PIPELINE_STAGES),
        }
        return jsonify(scorecard)

    # ══════════════════════════════════════════════════════════════════════
    # INTERNAL HELPERS
    # ══════════════════════════════════════════════════════════════════════

    def _assess_slsa(nodes, edges):
        """Assess SLSA level from pipeline graph."""
        node_types = set(n.get("type", "") for n in nodes)
        evidence = {
            "build_process_documented": any(t.startswith("cicd-") or t.startswith("build-") for t in node_types),
            "version_controlled_source": any(t.startswith("scm-") for t in node_types),
            "build_service_authenticated": any(t in ("cicd-tekton", "gcp-cloudbuild", "cicd-gitlab") for t in node_types),
            "build_as_code": any(t in ("cicd-tekton", "cicd-gitlab", "cicd-github-actions") for t in node_types),
            "ephemeral_environment": any(t in ("cicd-tekton", "gcp-cloudbuild") for t in node_types),
            "isolated_builds": any(t in ("build-kaniko", "build-buildah", "cicd-tekton") for t in node_types),
            "hermetic_builds": any(t == "build-bazel" for t in node_types),
            "reproducible_builds": any(t == "build-bazel" for t in node_types),
        }
        has_provenance = any(t in ("attest-slsa-gen", "attest-in-toto") for t in node_types)
        has_signing = any(t.startswith("sign-") for t in node_types)

        achieved = 0
        for level in range(4, -1, -1):
            reqs = SLSA_LEVEL_REQUIREMENTS.get(level, {}).get("requirements", [])
            if all(evidence.get(r, False) for r in reqs):
                achieved = level
                break

        # Signing and provenance can cap the level
        if achieved >= 2 and not has_signing:
            achieved = min(achieved, 1)
        if achieved >= 1 and not has_provenance:
            achieved = min(achieved, 0)

        return {
            "achieved_level": achieved,
            "evidence": evidence,
            "has_provenance": has_provenance,
            "has_signing": has_signing,
        }

    def _run_compliance_check(nodes, edges):
        """Run pipeline compliance rules against graph."""
        node_types = set(n.get("type", "") for n in nodes)
        findings = []
        passed = 0
        failed = 0

        # Type-category index for quick lookups
        has_category = {}
        for cat, items in PIPELINE_OBJECTS.items():
            for item in items:
                if item["type"] in node_types:
                    has_category.setdefault(cat, set()).add(item["type"])

        checks = {
            "branch_protection": any(t in ("branch-policy", "commit-signing") for t in node_types),
            "code_review_required": "branch-policy" in node_types,
            "hermetic_build": any(t in ("build-bazel", "build-kaniko") for t in node_types),
            "sbom_generated": any(t.startswith("sbom-") for t in node_types),
            "provenance_attestation": any(t in ("attest-slsa-gen", "attest-in-toto") for t in node_types),
            "sast_present": any("sast" in t or t in ("scan-sonarqube", "scan-semgrep", "scan-codeql", "scan-bandit", "scan-spotbugs", "aws-codeguru") for t in node_types),
            "sca_present": any(t in ("scan-sca", "scan-trivy", "scan-grype", "scan-snyk", "scan-dep-check") for t in node_types),
            "container_scan_before_push": any(t in ("scan-container", "scan-trivy", "scan-anchore", "scan-neuvector", "aws-inspector", "az-defender", "gcp-artifact-analysis", "ibm-vuln-advisor") for t in node_types),
            "secret_detection_present": any(t in ("scan-secret", "scan-gitleaks", "scan-trufflehog", "scan-detect-secrets") for t in node_types),
            "iac_scan_present": any(t in ("scan-iac", "scan-checkov", "scan-tfsec", "scan-kics") for t in node_types),
            "dast_present": any(t in ("scan-dast", "scan-zap", "scan-nuclei", "scan-burp") for t in node_types),
            "image_signing": any(t.startswith("sign-") for t in node_types),
            "vuln_threshold_gate": any(t in ("gate-vuln-threshold", "gate-automated") for t in node_types),
            "admission_controller": any(t in ("policy-opa", "policy-kyverno", "policy-gatekeeper", "policy-kubewarden", "gcp-binary-auth", "ibm-portieris") for t in node_types),
            "prod_approval_gate": any(t in ("gate-manual", "gate-deploy-window") for t in node_types),
            "progressive_delivery": any(t in ("deploy-canary", "deploy-bluegreen", "deploy-feature-flag") for t in node_types),
            "cds_for_cross_domain": not any(t.startswith("boundary-") for t in node_types) or any(t.startswith("cds-") for t in node_types),
            "runtime_monitoring": any(t.startswith("mon-") or t in ("aws-cloudwatch", "az-monitor", "gcp-monitoring", "aws-guardduty", "gcp-scc") for t in node_types),
            "evidence_collection": any(t in ("comp-evidence", "comp-oscal") for t in node_types),
            "audit_logging": True,  # Pipeline canvas itself provides audit
            "airgap_vuln_mirror": not any(t.startswith("pipeline-sipr") or t.startswith("pipeline-jwics") for t in node_types) or "vuln-db-mirror" in node_types,
            "airgap_package_mirror": not any(t.startswith("pipeline-sipr") or t.startswith("pipeline-jwics") for t in node_types) or "package-mirror" in node_types,
            # SRE checks
            "slo_defined": any(t.startswith("sre-slo") or t in ("sre-openslo", "sre-sloth", "sre-pyrra", "aws-cw-slo", "gcp-service-mon") for t in node_types),
            "incident_mgmt_present": any(t.startswith("sre-incident") or t in ("sre-pagerduty", "sre-grafana-oncall", "sre-opsgenie", "aws-incident-mgr") for t in node_types),
            "runbooks_present": any(t in ("sre-runbook", "sre-self-heal") for t in node_types),
            "chaos_present": any(t in ("sre-chaos", "sre-chaos-litmus", "aws-fis", "az-chaos-studio") for t in node_types),
            "dora_tracked": any(t.startswith("sre-dora") for t in node_types),
        }

        for rule in PIPELINE_COMPLIANCE_RULES:
            check_key = rule["check"]
            if checks.get(check_key, False):
                passed += 1
            else:
                failed += 1
                findings.append({
                    "rule_id": rule["id"],
                    "title": rule["title"],
                    "severity": rule["severity"],
                    "category": rule["category"],
                    "frameworks": rule["frameworks"],
                })

        return {"passed": passed, "failed": failed, "findings": findings, "total": passed + failed}

    # Heatmap color helpers
    def _time_color(minutes):
        if minutes <= 2:
            return "#27ae60"
        if minutes <= 10:
            return "#f39c12"
        return "#e74c3c"

    def _findings_color(count):
        if count == 0:
            return "#27ae60"
        if count <= 5:
            return "#f39c12"
        return "#e74c3c"

    def _compliance_color(pct):
        if pct >= 90:
            return "#27ae60"
        if pct >= 60:
            return "#f39c12"
        return "#e74c3c"

    def _age_color(days):
        if days <= 90:
            return "#27ae60"
        if days <= 365:
            return "#f39c12"
        return "#e74c3c"

    # ══════════════════════════════════════════════════════════════════════
    # PAGE ROUTES — RUNBOOKS
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/runbooks")
    @pc_login_required
    def pc_runbooks_page():
        """Browse all pipeline incident-response runbooks."""
        return render_template(
            "pipeline/runbooks.html",
            runbooks=_pdc_get_all_runbooks(),
        )

    @bp.route("/runbooks/<runbook_id>")
    @pc_login_required
    def pc_runbook_detail(runbook_id):
        """View a single pipeline runbook playbook."""
        runbook = _pdc_get_runbook_by_id(runbook_id)
        if not runbook:
            return redirect("/devops/runbooks")
        return render_template(
            "pipeline/runbook_detail.html",
            runbook=runbook,
        )

    # ══════════════════════════════════════════════════════════════════════
    # API — RUNBOOKS
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/runbooks", methods=["GET"])
    @pc_login_required
    def pc_api_list_runbooks():
        """Return all pipeline incident-response runbooks."""
        return jsonify(_pdc_get_all_runbooks())

    @bp.route("/api/runbooks/<runbook_id>", methods=["GET"])
    @pc_login_required
    def pc_api_get_runbook(runbook_id):
        """Return a single pipeline runbook by ID."""
        runbook = _pdc_get_runbook_by_id(runbook_id)
        if not runbook:
            return jsonify({"error": "Not found"}), 404
        return jsonify(runbook)

    # ── Remediation ────────────────────────────────────────────────────────
    @bp.route("/api/pipelines/<pipeline_id>/remediate", methods=["POST"])
    @pc_login_required
    def pc_api_remediate(pipeline_id):
        """Generate remediation plan for a pipeline's compliance findings."""
        from tools.pipeline.remediation import generate_remediation_plan

        conn = get_connection()
        row = conn.execute(
            "SELECT graph_json FROM pipelines WHERE id=?", (pipeline_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404

        graph = json.loads(row["graph_json"])
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        # Run compliance check to get findings
        result = _run_compliance_check(nodes, edges)
        findings = result.get("findings", [])

        if not findings:
            conn.close()
            return jsonify({
                "phases": [],
                "total_actions": 0,
                "auto_fixable": 0,
                "summary": "No compliance findings — pipeline is fully compliant.",
                "created_at": now_isoformat(),
            })

        plan = generate_remediation_plan(findings, rules=PIPELINE_COMPLIANCE_RULES)

        _audit("REMEDIATION_PLAN", "pipeline", pipeline_id,
               f"actions={plan['total_actions']}, auto_fixable={plan['auto_fixable']}")
        conn.close()
        return jsonify(plan)

    # ── Collaboration (Task 18) ───────────────────────────────────────────────
    import uuid as _uuid_mod
    from tools.canvas.collaboration import CanvasCollabManager as _PDCCollabMgr
    _pdc_collab = _PDCCollabMgr("pc")

    @bp.route("/api/collab/<design_id>/join", methods=["POST"])
    @pc_login_required
    def pc_collab_join(design_id):
        """Join a collaborative PDC editing session."""
        body = request.json or {}
        user_id = body.get("user_id", str(_uuid_mod.uuid4())[:8])
        user_name = body.get("user_name", "")
        return jsonify(_pdc_collab.join(design_id, user_id, user_name))

    @bp.route("/api/collab/<design_id>/leave", methods=["POST"])
    @pc_login_required
    def pc_collab_leave(design_id):
        """Leave a PDC collaborative session."""
        body = request.json or {}
        user_id = body.get("user_id", "")
        _pdc_collab.leave(design_id, user_id)
        return jsonify({"ok": True})

    @bp.route("/api/collab/<design_id>/push", methods=["POST"])
    @pc_login_required
    def pc_collab_push(design_id):
        """Push an operation into a PDC collaborative session."""
        body = request.json or {}
        user_id = body.get("user_id", "")
        op_type = body.get("op_type", "")
        data = body.get("data", {})
        seq = _pdc_collab.push(design_id, user_id, op_type, data)
        return jsonify({"seq": seq})

    @bp.route("/api/collab/<design_id>/poll", methods=["GET"])
    @pc_login_required
    def pc_collab_poll(design_id):
        """Poll for PDC collaborative operations since a sequence number."""
        since = int(request.args.get("since", 0))
        user_id = request.args.get("user_id", "")
        cx = request.args.get("cx")
        cy = request.args.get("cy")
        if user_id and cx is not None and cy is not None:
            _pdc_collab.update_cursor(design_id, user_id, float(cx), float(cy))
        ops, participants, latest_seq = _pdc_collab.poll(design_id, since)
        return jsonify({"operations": ops, "participants": participants, "latest_seq": latest_seq})

    @bp.route("/api/collab/<design_id>/participants", methods=["GET"])
    @pc_login_required
    def pc_collab_participants(design_id):
        """Return current participants in a PDC collaborative session."""
        return jsonify({"participants": _pdc_collab.get_participants(design_id)})

    return bp
