# CUI // SP-CTI
"""ICDEV™ Boundary Design Canvas — Flask Blueprint integration.

Fully self-contained Blueprint mounted at /boundary/ inside the ICDEV dashboard.
Uses ICDEV's auth system, separate boundary_canvas.db, and feature flag
ICDEV_BOUNDARY_ENABLED.

Usage in ICDEV dashboard app.py:
    from tools.boundary_canvas.blueprint import create_boundary_blueprint
    bp = create_boundary_blueprint()
    if bp:
        app.register_blueprint(bp, url_prefix="/boundary")
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import os
import uuid as _uuid
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)

logger = get_logger("icdev.boundary_canvas")

# ── Paths ──────────────────────────────────────────────────────────────────────
_BDC_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _BDC_DIR.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"
_CONFIG_PATH = _ICDEV_ROOT / "args" / "boundary_canvas_config.yaml"

try:
    import yaml as _yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def _load_config() -> dict:
    """Load BDC config from args/boundary_canvas_config.yaml."""
    if not _HAS_YAML or not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
        return _yaml.safe_load(_f) or {}


_BDC_CONFIG = _load_config()

# ── Import helper modules ──────────────────────────────────────────────────────
from tools.boundary_canvas.constants import (  # noqa: E402
    BOUNDARY_OBJECTS,
    BOUNDARY_COMPLIANCE_RULES,
    ISA_LIFECYCLE_STATES,
)
from tools.boundary_canvas.boundary_engine import (  # noqa: E402
    assess_boundary_design,
    compute_isa_status,
    generate_pps_matrix,
    detect_boundary_gaps,
)
from tools.canvas.ai_trace_mixin import record_canvas_decision  # noqa: E402


def create_boundary_blueprint():
    """Create and return the Boundary Design Canvas Blueprint.

    Returns None if ICDEV_BOUNDARY_ENABLED is false.
    """
    enabled = os.environ.get("ICDEV_BOUNDARY_ENABLED", "true").lower()
    if enabled not in ("true", "1", "yes"):
        logger.info("Boundary Canvas disabled (ICDEV_BOUNDARY_ENABLED=%s)", enabled)
        return None

    # Initialize DB
    try:
        from tools.boundary_canvas.db.init_db import init_db

        init_db()
    except Exception as exc:
        logger.warning("Boundary Canvas DB init failed: %s", exc)

    bp = Blueprint(
        "boundary_canvas",
        __name__,
        template_folder=str(_TEMPLATE_DIR),
    )

    # ── Auth wrapper (uses ICDEV dashboard session) ────────────────────────
    def bdc_login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("user_id"):
                if (
                    request.is_json
                    or request.path.startswith("/boundary/api/")
                    or request.method in ("DELETE", "POST", "PUT")
                ):
                    return jsonify({"error": "Authentication required"}), 401
                return redirect("/login")
            return f(*args, **kwargs)

        return decorated

    # ── DB helpers ─────────────────────────────────────────────────────────
    from tools.boundary_canvas.db.init_db import get_connection
    from tools.common.helpers import now_isoformat

    def _audit(design_id, action, detail=""):
        user_id = ""
        try:
            user_id = session.get("user_id", "")
        except RuntimeError:
            pass
        try:
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO bd_audit (design_id, user, action, detail, created_at) VALUES (%s,%s,%s,%s,%s)",
                    (design_id, user_id, action, detail, now_isoformat()),
                )
        except Exception:
            logger.warning("audit trail write failed for design %s", design_id, exc_info=True)

    def _row_to_dict(row):
        return dict(row) if row else {}

    # ====================================================================
    # PAGE ROUTES
    # ====================================================================

    @bp.route("/")
    @bdc_login_required
    def bdc_index():
        """Boundary Design Canvas dashboard — list designs + recent assessments."""
        with get_connection() as conn:
            designs = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT id, name, description, classification, "
                    "created_at, updated_at "
                    "FROM boundary_designs ORDER BY updated_at DESC"
                ).fetchall()
            ]
            recent_assessments = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT a.id, a.design_id, a.assessment_type, a.score, "
                    "a.grade, a.created_at, d.name AS design_name "
                    "FROM bd_assessments a "
                    "JOIN boundary_designs d ON a.design_id = d.id "
                    "ORDER BY a.created_at DESC LIMIT 10"
                ).fetchall()
            ]
            templates = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT id, name, category, description, tags FROM bd_templates ORDER BY category, name"
                ).fetchall()
            ]
        return render_template(
            "boundary_canvas/index.html",
            designs=designs,
            recent_assessments=recent_assessments,
            templates=templates,
        )

    @bp.route("/canvas/<design_id>")
    @bdc_login_required
    def bdc_canvas(design_id):
        """Open existing boundary design canvas."""
        with get_connection() as conn:
            design = _row_to_dict(conn.execute("SELECT * FROM boundary_designs WHERE id=%s", (design_id,)).fetchone())
        if not design:
            abort(404)
        return render_template(
            "boundary_canvas/canvas.html",
            design_id=design_id,
            design=design,
            boundary_objects=BOUNDARY_OBJECTS,
            compliance_rules=BOUNDARY_COMPLIANCE_RULES,
        )

    @bp.route("/canvas/new")
    @bdc_login_required
    def bdc_new_canvas():
        """New boundary design canvas, optionally from template."""
        template_id = request.args.get("template")
        graph_json = '{"nodes":[],"edges":[]}'
        name = "New Boundary Design"
        if template_id:
            with get_connection() as conn:
                tpl = conn.execute(
                    "SELECT name, graph_json FROM bd_templates WHERE id=%s",
                    (template_id,),
                ).fetchone()
            if tpl:
                tpl = _row_to_dict(tpl)
                graph_json = tpl["graph_json"]
                name = f"{tpl['name']} (copy)"
        design = {
            "name": name,
            "graph_json": graph_json,
            "classification": "CUI",
        }
        return render_template(
            "boundary_canvas/canvas.html",
            design_id="new",
            design=design,
            boundary_objects=BOUNDARY_OBJECTS,
            compliance_rules=BOUNDARY_COMPLIANCE_RULES,
        )

    @bp.route("/templates")
    @bdc_login_required
    def bdc_templates():
        """Template gallery page."""
        with get_connection() as conn:
            templates = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT id, name, category, description, tags FROM bd_templates ORDER BY category, name"
                ).fetchall()
            ]
        return render_template("boundary_canvas/templates.html", templates=templates)

    @bp.route("/assessments")
    @bdc_login_required
    def bdc_assessments():
        """Assessment history page."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT a.id, a.design_id, a.assessment_type, a.score, "
                "a.grade, a.cat1_findings, a.cat2_findings, a.cat3_findings, "
                "a.created_at, d.name AS design_name "
                "FROM bd_assessments a "
                "LEFT JOIN boundary_designs d ON a.design_id = d.id "
                "ORDER BY a.created_at DESC LIMIT 50"
            ).fetchall()
            assessments = [_row_to_dict(r) for r in rows]
        return render_template("boundary_canvas/assessments.html", assessments=assessments)

    @bp.route("/isa-tracker")
    @bdc_login_required
    def bdc_isa_tracker_page():
        """ISA Tracker — all ISAs across all boundary designs."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT t.*, d.name as design_name "
                "FROM bd_isa_tracker t "
                "LEFT JOIN boundary_designs d ON t.design_id = d.id "
                "ORDER BY t.status, t.expiry_date"
            ).fetchall()
        return render_template(
            "boundary_canvas/isa_tracker.html",
            isas=[_row_to_dict(r) for r in rows],
        )

    @bp.route("/pps-matrix/<design_id>")
    @bdc_login_required
    def bdc_pps_matrix_page(design_id):
        """PPS Matrix page for a specific boundary design."""
        with get_connection() as conn:
            design = _row_to_dict(
                conn.execute("SELECT id, name FROM boundary_designs WHERE id=%s", (design_id,)).fetchone()
            )
        if not design:
            return redirect("/boundary/")
        return render_template("boundary_canvas/pps_matrix.html", design=design)

    @bp.route("/compliance/<design_id>")
    @bdc_login_required
    def bdc_compliance_page(design_id):
        """Compliance view page for a specific boundary design."""
        with get_connection() as conn:
            design = _row_to_dict(
                conn.execute("SELECT id, name FROM boundary_designs WHERE id=%s", (design_id,)).fetchone()
            )
        if not design:
            return redirect("/boundary/")
        return render_template("boundary_canvas/compliance.html", design=design)

    @bp.route("/remediation/<design_id>")
    @bdc_login_required
    def bdc_remediation_page(design_id):
        """Remediation page — gap analysis with recommended fixes."""
        with get_connection() as conn:
            design = _row_to_dict(
                conn.execute("SELECT id, name FROM boundary_designs WHERE id=%s", (design_id,)).fetchone()
            )
        if not design:
            return redirect("/boundary/")
        return render_template("boundary_canvas/remediation.html", design=design)

    # ====================================================================
    # API ROUTES — Designs CRUD
    # ====================================================================

    @bp.route("/api/designs", methods=["POST"])
    @bdc_login_required
    def bdc_api_create_design():
        """Create a new boundary design."""
        data = request.get_json(force=True, silent=True) or {}
        template_id = data.get("template_id")
        name = data.get("name", "Untitled Boundary Design")
        now = now_isoformat()
        default_graph = {"nodes": [], "edges": [], "boundaries": []}
        # Idempotency: if loading from a template, return the existing design rather than duplicating
        if template_id:
            with get_connection() as conn:
                ex = conn.execute(
                    "SELECT id, name FROM boundary_designs WHERE template_id=%s LIMIT 1", (template_id,)
                ).fetchone()
            if ex:
                return jsonify({"id": ex["id"], "name": ex["name"]}), 200
        design_id = str(_uuid.uuid4())
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO boundary_designs "
                "(id, name, description, graph_json, template_id, "
                "classification, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    design_id,
                    name,
                    data.get("description", ""),
                    data.get("graph_json")
                    if isinstance(data.get("graph_json"), str)
                    else json.dumps(data.get("graph_json", default_graph)),
                    template_id,
                    data.get("classification", "CUI"),
                    now,
                    now,
                ),
            )
        _audit(design_id, "CREATE", name)
        # Hook: refresh BDC KG so /ask reflects the new design immediately
        from tools.knowledge_graph.canvas_ask import reindex_canvas_on_save
        reindex_canvas_on_save("bdc")
        return jsonify({"id": design_id, "name": name}), 201

    @bp.route("/api/designs/<design_id>", methods=["GET"])
    @bdc_login_required
    def bdc_api_get_design(design_id):
        """Get a single boundary design."""
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM boundary_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify(_row_to_dict(row))

    @bp.route("/api/designs/<design_id>", methods=["PUT"])
    @bdc_login_required
    def bdc_api_update_design(design_id):
        """Update an existing boundary design."""
        data = request.get_json(force=True, silent=True) or {}
        now = now_isoformat()
        with get_connection() as conn:
            existing = conn.execute("SELECT id FROM boundary_designs WHERE id=%s", (design_id,)).fetchone()
            if not existing:
                return jsonify({"error": "Not found"}), 404
            updates = []
            params = []
            for key in ("name", "description", "classification"):
                if key in data:
                    updates.append(f"{key}=%s")
                    params.append(data[key])
            if "graph_json" in data:
                updates.append("graph_json=%s")
                val = data["graph_json"]
                params.append(json.dumps(val) if isinstance(val, (dict, list)) else val)
            updates.append("updated_at=%s")
            params.append(now)
            params.append(design_id)
            conn.execute(
                f"UPDATE boundary_designs SET {', '.join(updates)} WHERE id=%s",  # noqa: S608  # nosec B608 — columns from hardcoded allowlist
                params,
            )
        _audit(design_id, "UPDATE", json.dumps(list(data.keys())))
        # Hook: refresh BDC KG so /ask reflects the edit immediately
        from tools.knowledge_graph.canvas_ask import reindex_canvas_on_save
        reindex_canvas_on_save("bdc")

        # Cross-canvas trigger: validate boundary vs NDC enclaves, check ISA
        try:
            from tools.security_canvas.agent import on_bdc_design_saved

            on_bdc_design_saved(design_id)
        except Exception:
            logger.warning("security-canvas boundary validation hook failed for design %s", design_id, exc_info=True)
        try:
            from tools.canvas.event_bus import publish as _eb_publish
            _eb_publish("bdc", "bdc.design.saved", {
                "design_id": design_id,
                "classification": data.get("classification", "CUI"),
                "graph_changed": "graph_json" in data,
            }, target_canvas="odc")
        except Exception:
            logger.warning("event bus publish (bdc.design.saved) failed for design %s", design_id, exc_info=True)

        # Incremental KG update: re-extract only if graph_json changed
        try:
            from tools.canvas.kg_builder import rebuild_canvas_kg

            rebuild_canvas_kg("bdc", design_id)
        except Exception:
            logger.warning("canvas KG rebuild failed for design %s", design_id, exc_info=True)
        # Blockchain provenance
        try:
            from tools.canvas.provenance import register_canvas_provenance
            register_canvas_provenance(
                canvas_key="bdc",
                design_id=design_id,
                graph_json=data.get("graph_json", {}),
                project_id=data.get("project_id", ""),
            )
        except Exception:
            logger.warning("blockchain provenance registration failed for design %s", design_id, exc_info=True)

        return jsonify({"id": design_id, "updated_at": now})

    @bp.route("/api/designs/<design_id>", methods=["DELETE"])
    @bdc_login_required
    def bdc_api_delete_design(design_id):
        """Delete a boundary design and all related records."""
        child_tables = ("bd_isa_tracker", "bd_assessments", "bd_versions")
        with get_connection() as conn:
            for table in child_tables:
                conn.execute(
                    f"DELETE FROM {table} WHERE design_id=%s",  # nosec B608 — table from hardcoded tuple
                    (design_id,),  # noqa: S608
                )
            conn.execute("DELETE FROM boundary_designs WHERE id=%s", (design_id,))
        _audit(design_id, "DELETE", "")
        return jsonify({"deleted": design_id})

    @bp.route("/api/designs", methods=["DELETE"])
    @bdc_login_required
    def bdc_api_delete_all_designs():
        """Delete all boundary designs and their related records."""
        child_tables = ("bd_isa_tracker", "bd_assessments", "bd_versions")
        with get_connection() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM boundary_designs").fetchall()]
            for did in ids:
                for table in child_tables:
                    conn.execute(f"DELETE FROM {table} WHERE design_id=%s", (did,))  # nosec B608
                conn.execute("DELETE FROM boundary_designs WHERE id=%s", (did,))
        return jsonify({"deleted": len(ids)})

    # ====================================================================
    # API ROUTES — Assessment
    # ====================================================================

    @bp.route("/api/designs/<design_id>/assess", methods=["POST"])
    @bdc_login_required
    def bdc_api_assess(design_id):
        """Run a full boundary compliance assessment on a design."""
        data = request.get_json(force=True, silent=True) or {}
        use_cod = data.get("use_cod", False)
        chain_mode = "cod" if use_cod else ""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM boundary_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404
            try:
                graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            except Exception:
                return jsonify({"error": "Bad graph data"}), 500

            # Fetch ISA tracker entries for this design
            isa_rows = conn.execute(
                "SELECT * FROM bd_isa_tracker WHERE design_id=%s",
                (design_id,),
            ).fetchall()
            isa_tracker = [_row_to_dict(r) for r in isa_rows]

            # Run assessment
            assessment_type = data.get("type", "full")
            result = assess_boundary_design(graph, isa_tracker=isa_tracker)

            # Persist assessment
            assess_id = str(_uuid.uuid4())
            now = now_isoformat()
            conn.execute(
                "INSERT INTO bd_assessments "
                "(id, design_id, assessment_type, findings_json, score, grade, "
                "cat1_findings, cat2_findings, cat3_findings, "
                "nist_coverage_json, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    assess_id,
                    design_id,
                    assessment_type,
                    json.dumps(result.get("findings", [])),
                    result.get("score", 0),
                    result.get("grade", "N/A"),
                    result.get("cat1_findings", 0),
                    result.get("cat2_findings", 0),
                    result.get("cat3_findings", 0),
                    json.dumps(result.get("nist_coverage", {})),
                    now,
                ),
            )

        _audit(design_id, "ASSESS", f"score={result.get('score', 0)} grade={result.get('grade')}")
        record_canvas_decision(
            canvas_type="bdc",
            record_id=design_id,
            decision_type="boundary_impact",
            decision=f"Grade {result.get('grade','?')} — CAT1={result.get('cat1_findings',0)} CAT2={result.get('cat2_findings',0)} CAT3={result.get('cat3_findings',0)}",
            rationale=f"Score: {result.get('score', 0)}",
            model_used=None,
            confidence=None,
        )

        # Add gap analysis
        gaps = detect_boundary_gaps(result)
        result["assessment_id"] = assess_id
        result["gap_analysis"] = gaps
        result["chain_mode"] = chain_mode
        # Blockchain provenance for assessment
        try:
            from tools.canvas.provenance import register_canvas_provenance
            register_canvas_provenance(
                canvas_key="bdc",
                design_id=design_id,
                assessment_data=result,
                project_id="",
            )
        except Exception:
            logger.warning("blockchain provenance registration (assessment) failed for design %s", design_id, exc_info=True)
        return jsonify(result)

    # ====================================================================
    # API ROUTES — Templates
    # ====================================================================

    @bp.route("/api/templates", methods=["GET"])
    @bdc_login_required
    def bdc_api_list_templates():
        """List all boundary design templates."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, name, category, description, tags FROM bd_templates ORDER BY category, name"
            ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/snippets", methods=["GET"])
    @bdc_login_required
    def bdc_api_list_snippets():
        """List available BDC snippets (reusable graph fragments)."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, name, category, description, graph_json, tags FROM bd_snippets ORDER BY category, name"
            ).fetchall()
        return jsonify({"snippets": [_row_to_dict(r) for r in rows]})

    @bp.route("/api/templates/<template_id>", methods=["GET"])
    @bdc_login_required
    def bdc_api_get_template(template_id):
        """Get a single template with full graph JSON."""
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM bd_templates WHERE id=%s", (template_id,)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify(_row_to_dict(row))

    # ====================================================================
    # API ROUTES — PPS Matrix
    # ====================================================================

    @bp.route("/api/designs/<design_id>/pps-matrix", methods=["GET"])
    @bdc_login_required
    def bdc_api_pps_matrix(design_id):
        """Generate PPS matrix for a boundary design."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM boundary_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            return jsonify({"error": "Bad graph data"}), 500
        result = generate_pps_matrix(graph)
        return jsonify(result)

    # ====================================================================
    # API ROUTES — ISA Tracker
    # ====================================================================

    @bp.route("/api/designs/<design_id>/isa-tracker", methods=["GET"])
    @bdc_login_required
    def bdc_api_isa_status(design_id):
        """Get ISA lifecycle status for all interconnections in a design."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM boundary_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404
            try:
                graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            except Exception:
                return jsonify({"error": "Bad graph data"}), 500
            isa_rows = conn.execute(
                "SELECT * FROM bd_isa_tracker WHERE design_id=%s",
                (design_id,),
            ).fetchall()
            isa_tracker = [_row_to_dict(r) for r in isa_rows]
        result = compute_isa_status(graph, isa_tracker=isa_tracker)
        return jsonify(result)

    @bp.route("/api/designs/<design_id>/isa-tracker", methods=["POST"])
    @bdc_login_required
    def bdc_api_upsert_isa(design_id):
        """Create or update an ISA tracker entry."""
        data = request.get_json(force=True, silent=True) or {}
        now = now_isoformat()
        interconnection_id = data.get("interconnection_id")
        if not interconnection_id:
            return jsonify({"error": "interconnection_id required"}), 400

        with get_connection() as conn:
            # Verify design exists
            design = conn.execute("SELECT id FROM boundary_designs WHERE id=%s", (design_id,)).fetchone()
            if not design:
                return jsonify({"error": "Design not found"}), 404

            existing = conn.execute(
                "SELECT id FROM bd_isa_tracker WHERE design_id=%s AND interconnection_id=%s",
                (design_id, interconnection_id),
            ).fetchone()

            status = data.get("status", "draft")
            if status not in ISA_LIFECYCLE_STATES:
                return jsonify({"error": f"Invalid status. Must be one of: {ISA_LIFECYCLE_STATES}"}), 400

            if existing:
                conn.execute(
                    "UPDATE bd_isa_tracker SET status=%s, expiry_date=%s, review_date=%s, "
                    "owner=%s, isa_doc_id=%s, notes=%s, updated_at=%s "
                    "WHERE design_id=%s AND interconnection_id=%s",
                    (
                        status,
                        data.get("expiry_date"),
                        data.get("review_date"),
                        data.get("owner", ""),
                        data.get("isa_doc_id"),
                        data.get("notes", ""),
                        now,
                        design_id,
                        interconnection_id,
                    ),
                )
                _audit(design_id, "ISA_UPDATE", f"{interconnection_id} -> {status}")
                return jsonify({"updated": interconnection_id, "status": status})
            else:
                tracker_id = str(_uuid.uuid4())
                conn.execute(
                    "INSERT INTO bd_isa_tracker "
                    "(id, design_id, interconnection_id, isa_doc_id, status, "
                    "expiry_date, review_date, owner, notes, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        tracker_id,
                        design_id,
                        interconnection_id,
                        data.get("isa_doc_id"),
                        status,
                        data.get("expiry_date"),
                        data.get("review_date"),
                        data.get("owner", ""),
                        data.get("notes", ""),
                        now,
                        now,
                    ),
                )
                _audit(design_id, "ISA_CREATE", f"{interconnection_id} status={status}")
                return jsonify({"id": tracker_id, "interconnection_id": interconnection_id}), 201

    # ====================================================================
    # API ROUTES — Design Versioning
    # ====================================================================

    def _bdc_diff_graph(old: dict, new: dict) -> str:
        """Return a human-readable change summary between two graph states."""
        old_nodes = {n.get("id") for n in old.get("nodes", [])}
        new_nodes = {n.get("id") for n in new.get("nodes", [])}
        added = len(new_nodes - old_nodes)
        removed = len(old_nodes - new_nodes)
        old_edges = {(e.get("source"), e.get("target")) for e in old.get("edges", [])}
        new_edges = {(e.get("source"), e.get("target")) for e in new.get("edges", [])}
        e_added = len(new_edges - old_edges)
        e_removed = len(old_edges - new_edges)
        parts = []
        if added:
            parts.append(f"+{added} node(s)")
        if removed:
            parts.append(f"-{removed} node(s)")
        if e_added:
            parts.append(f"+{e_added} edge(s)")
        if e_removed:
            parts.append(f"-{e_removed} edge(s)")
        return ", ".join(parts) if parts else "No structural changes"

    @bp.route("/api/versions/<design_id>", methods=["GET"])
    @bdc_login_required
    def bdc_api_list_versions(design_id):
        """List all version snapshots for a boundary design."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, version_number, change_summary, user_id, created_at "
                "FROM bd_versions WHERE design_id=%s ORDER BY version_number DESC",
                (design_id,),
            ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/versions/<design_id>", methods=["POST"])
    @bdc_login_required
    def bdc_api_create_version(design_id):
        """Create a version snapshot of the current boundary design state."""
        data = request.get_json(force=True, silent=True) or {}
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM boundary_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
            if not row:
                return jsonify({"error": "Design not found"}), 404
            raw = _row_to_dict(row)["graph_json"]
            try:
                current_graph = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                current_graph = {}
            ver_num = conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) + 1 FROM bd_versions WHERE design_id=%s",
                (design_id,),
            ).fetchone()[0]
            change_summary = data.get("change_summary", "")
            if not change_summary:
                prev = conn.execute(
                    "SELECT graph_json FROM bd_versions WHERE design_id=%s ORDER BY version_number DESC LIMIT 1",
                    (design_id,),
                ).fetchone()
                if prev:
                    try:
                        prev_graph = json.loads(prev[0]) if isinstance(prev[0], str) else prev[0]
                        change_summary = _bdc_diff_graph(prev_graph, current_graph)
                    except Exception:
                        logger.warning("graph diff change-summary computation failed for design %s", design_id, exc_info=True)
            ver_id = str(_uuid.uuid4())
            now = now_isoformat()
            conn.execute(
                "INSERT INTO bd_versions (id, design_id, version_number, graph_json, change_summary, user_id, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    ver_id,
                    design_id,
                    ver_num,
                    json.dumps(current_graph) if isinstance(current_graph, dict) else str(raw),
                    change_summary,
                    session.get("user_id", ""),
                    now,
                ),
            )
        _audit(design_id, "VERSION_CREATE", f"v{ver_num}")
        return jsonify(
            {"id": ver_id, "version_number": ver_num, "change_summary": change_summary, "created_at": now}
        ), 201

    @bp.route("/api/versions/<design_id>/restore/<version_id>", methods=["POST"])
    @bdc_login_required
    def bdc_api_restore_version(design_id, version_id):
        """Restore a boundary design to a previous version snapshot."""
        with get_connection() as conn:
            ver = conn.execute(
                "SELECT graph_json, version_number FROM bd_versions WHERE id=%s AND design_id=%s",
                (version_id, design_id),
            ).fetchone()
            if not ver:
                return jsonify({"error": "Version not found"}), 404
            now = now_isoformat()
            conn.execute(
                "UPDATE boundary_designs SET graph_json=%s, updated_at=%s WHERE id=%s",
                (ver[0], now, design_id),
            )
        _audit(design_id, "VERSION_RESTORE", f"restored to v{ver[1]}")
        return jsonify({"id": design_id, "restored_version": version_id, "version_number": ver[1], "updated_at": now})

    @bp.route("/api/versions/<design_id>/diff", methods=["POST"])
    @bdc_login_required
    def bdc_api_diff_versions(design_id):
        """Compare two version snapshots of a boundary design."""
        data = request.get_json(force=True, silent=True) or {}
        ver_a_id = data.get("version_a")
        ver_b_id = data.get("version_b")
        if not ver_a_id or not ver_b_id:
            return jsonify({"error": "version_a and version_b required"}), 400
        with get_connection() as conn:
            ver_a = conn.execute(
                "SELECT graph_json, version_number FROM bd_versions WHERE id=%s AND design_id=%s",
                (ver_a_id, design_id),
            ).fetchone()
            ver_b = conn.execute(
                "SELECT graph_json, version_number FROM bd_versions WHERE id=%s AND design_id=%s",
                (ver_b_id, design_id),
            ).fetchone()
        if not ver_a or not ver_b:
            return jsonify({"error": "One or both versions not found"}), 404
        try:
            graph_a = json.loads(ver_a[0]) if isinstance(ver_a[0], str) else ver_a[0]
            graph_b = json.loads(ver_b[0]) if isinstance(ver_b[0], str) else ver_b[0]
        except Exception:
            return jsonify({"error": "Failed to parse graph data"}), 500
        summary = _bdc_diff_graph(graph_a, graph_b)
        return jsonify(
            {
                "version_a": {"id": ver_a_id, "version_number": ver_a[1]},
                "version_b": {"id": ver_b_id, "version_number": ver_b[1]},
                "summary": summary,
            }
        )

    # ====================================================================
    # API ROUTES — Object palette
    # ====================================================================

    @bp.route("/api/objects", methods=["GET"])
    @bdc_login_required
    def bdc_api_objects():
        """Return the boundary object palette for the canvas toolbar."""
        return jsonify(BOUNDARY_OBJECTS)

    @bp.route("/api/rules", methods=["GET"])
    @bdc_login_required
    def bdc_api_rules():
        """Return the boundary compliance rules."""
        return jsonify(BOUNDARY_COMPLIANCE_RULES)

    @bp.route("/api/export/<design_id>/vsdx", methods=["POST"])
    @bdc_login_required
    def bdc_api_export_vsdx(design_id):
        """Export boundary design as Visio .vsdx file."""
        import base64

        with get_connection() as conn:
            row = conn.execute(
                "SELECT name, graph_json FROM boundary_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = _row_to_dict(row)
        gj = d["graph_json"]
        graph_data = json.loads(gj) if isinstance(gj, str) else gj
        from tools.network.visio_export import export_vsdx

        vsdx_bytes = export_vsdx(d["name"], graph_data)
        return jsonify(
            {
                "format": "vsdx",
                "filename": d["name"].replace(" ", "_"),
                "data": base64.b64encode(vsdx_bytes).decode("ascii"),
            }
        )

    def _bdc_fetch(design_id):
        with get_connection() as conn:
            row = conn.execute("SELECT name, graph_json FROM boundary_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return None, None
        d = _row_to_dict(row)
        gj = d["graph_json"]
        graph = json.loads(gj) if isinstance(gj, str) else gj
        return d["name"], graph

    @bp.route("/api/export/<design_id>/json", methods=["POST"])
    @bdc_login_required
    def bdc_api_export_json(design_id):
        """Export boundary design as JSON."""
        import base64

        name, graph = _bdc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_json

        data = base64.b64encode(export_json(name, graph, "BDC")).decode("ascii")
        return jsonify({"format": "json", "filename": f"{name.replace(' ', '_')}.json", "data": data})

    @bp.route("/api/export/<design_id>/markdown", methods=["POST"])
    @bdc_login_required
    def bdc_api_export_markdown(design_id):
        """Export boundary design as Markdown."""
        import base64

        name, graph = _bdc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_markdown

        data = base64.b64encode(export_markdown(name, graph, "BDC")).decode("ascii")
        return jsonify({"format": "markdown", "filename": f"{name.replace(' ', '_')}.md", "data": data})

    @bp.route("/api/export/<design_id>/csv", methods=["POST"])
    @bdc_login_required
    def bdc_api_export_csv(design_id):
        """Export boundary design node inventory as CSV."""
        import base64

        name, graph = _bdc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_csv

        data = base64.b64encode(export_csv(name, graph, "BDC")).decode("ascii")
        return jsonify({"format": "csv", "filename": f"{name.replace(' ', '_')}.csv", "data": data})

    @bp.route("/api/export/<design_id>/drawio", methods=["POST"])
    @bdc_login_required
    def bdc_api_export_drawio(design_id):
        """Export boundary design as DrawIO XML."""
        import base64

        name, graph = _bdc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_drawio

        data = base64.b64encode(export_drawio(name, graph, "BDC")).decode("ascii")
        return jsonify({"format": "drawio", "filename": f"{name.replace(' ', '_')}.drawio", "data": data})

    @bp.route("/api/export/<design_id>/svg", methods=["POST"])
    @bdc_login_required
    def bdc_api_export_svg(design_id):
        """Export boundary design as SVG vector graphic."""
        import base64

        name, graph = _bdc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_svg

        data = base64.b64encode(export_svg(name, graph, "BDC")).decode("ascii")
        return jsonify({"format": "svg", "filename": f"{name.replace(' ', '_')}.svg", "data": data})

    @bp.route("/api/export/<design_id>/pptx", methods=["GET"])
    @bdc_login_required
    def bdc_api_export_pptx(design_id):
        """Export boundary design as a PowerPoint (.pptx) deck.

        Returns the raw file bytes (not base64) with an attachment filename.
        Bundles a title slide, the boundary diagram (same geometry the SVG /
        drawio exporters use), and a compliance/readiness table from the
        latest bd_assessments row + cATO readiness — each degrading cleanly
        when its data is absent.
        """
        from flask import Response

        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM boundary_designs WHERE id=%s", (design_id,)
            ).fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404
            design = _row_to_dict(row)

            assessment = None
            try:
                a_row = conn.execute(
                    "SELECT * FROM bd_assessments WHERE design_id=%s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (design_id,),
                ).fetchone()
                if a_row:
                    assessment = _row_to_dict(a_row)
            except Exception as exc:  # noqa: BLE001 — missing table -> no assessment slide data
                logger.info("bd_assessments unavailable for %s: %s", design_id, exc)

        readiness = None
        try:
            from tools.boundary_canvas.cato_readiness import compute_readiness

            readiness = compute_readiness(design_id, design_id)
        except Exception as exc:  # noqa: BLE001 — readiness slide degrades gracefully
            logger.info("cATO readiness unavailable for %s: %s", design_id, exc)

        from tools.boundary_canvas.export_pptx import design_to_pptx

        pptx_bytes = design_to_pptx(design, assessment=assessment, readiness=readiness)
        filename = f"{str(design.get('name') or 'design').replace(' ', '_')}.pptx"
        return Response(
            pptx_bytes,
            mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ── Collaboration (Task 18) ───────────────────────────────────────────────
    import uuid as _uuid_mod
    from tools.canvas.collaboration import CanvasCollabManager as _BDCCollabMgr

    _bdc_collab = _BDCCollabMgr("bd")

    @bp.route("/api/collab/<design_id>/join", methods=["POST"])
    @bdc_login_required
    def bdc_collab_join(design_id):
        """Join a collaborative BDC editing session."""
        body = request.json or {}
        user_id = body.get("user_id", str(_uuid_mod.uuid4())[:8])
        user_name = body.get("user_name", "")
        return jsonify(_bdc_collab.join(design_id, user_id, user_name))

    @bp.route("/api/collab/<design_id>/leave", methods=["POST"])
    @bdc_login_required
    def bdc_collab_leave(design_id):
        """Leave a BDC collaborative session."""
        body = request.json or {}
        user_id = body.get("user_id", "")
        _bdc_collab.leave(design_id, user_id)
        return jsonify({"ok": True})

    @bp.route("/api/collab/<design_id>/push", methods=["POST"])
    @bdc_login_required
    def bdc_collab_push(design_id):
        """Push an operation into a BDC collaborative session."""
        body = request.json or {}
        user_id = body.get("user_id", "")
        op_type = body.get("op_type", "")
        data = body.get("data", {})
        seq = _bdc_collab.push(design_id, user_id, op_type, data)
        return jsonify({"seq": seq})

    @bp.route("/api/collab/<design_id>/poll", methods=["GET"])
    @bdc_login_required
    def bdc_collab_poll(design_id):
        """Poll for BDC collaborative operations since a sequence number."""
        since = int(request.args.get("since", 0))
        user_id = request.args.get("user_id", "")
        cx = request.args.get("cx")
        cy = request.args.get("cy")
        if user_id and cx is not None and cy is not None:
            _bdc_collab.update_cursor(design_id, user_id, float(cx), float(cy))
        ops, participants, latest_seq = _bdc_collab.poll(design_id, since)
        return jsonify({"operations": ops, "participants": participants, "latest_seq": latest_seq})

    @bp.route("/api/collab/<design_id>/participants", methods=["GET"])
    @bdc_login_required
    def bdc_collab_participants(design_id):
        """Return current participants in a BDC collaborative session."""
        return jsonify({"participants": _bdc_collab.get_participants(design_id)})

    # ====================================================================
    # API — ISA Expiry Alerting & Risk Scoring
    # ====================================================================

    def _compute_days_until(date_str: str | None) -> int | None:
        """Return days between today and an ISO date string (negative = past)."""
        if not date_str:
            return None
        try:
            from datetime import date

            target = date.fromisoformat(date_str[:10])
            delta = (target - date.today()).days
            return delta
        except (ValueError, TypeError):
            return None

    def _isa_risk_score(interconnection_type: str) -> dict:
        """Compute risk score and tier for an interconnection type.

        Risk tiers (NIST SP 800-47 / DoD cross-domain guidance):
          cross-domain  — highest risk (different classification levels, need CDSE approval)
          direct-connection — high risk (direct IP connectivity, attack surface)
          api           — medium risk (API boundary, limited surface)
          federation    — lower risk (identity federation, no data transit)
          vpn           — medium risk (encrypted tunnel, perimeter trust)
        """
        _RISK_TABLE: dict[str, dict] = {
            "cross-domain": {"score": 95, "tier": "CRITICAL", "nist_controls": ["CA-3", "SC-7", "SC-28", "AC-4"]},
            "cross_domain": {"score": 95, "tier": "CRITICAL", "nist_controls": ["CA-3", "SC-7", "SC-28", "AC-4"]},
            "direct": {"score": 80, "tier": "HIGH", "nist_controls": ["CA-3", "SC-7", "AC-17"]},
            "direct-connection": {"score": 80, "tier": "HIGH", "nist_controls": ["CA-3", "SC-7", "AC-17"]},
            "dedicated": {"score": 80, "tier": "HIGH", "nist_controls": ["CA-3", "SC-7", "AC-17"]},
            "vpn": {"score": 60, "tier": "MEDIUM", "nist_controls": ["CA-3", "SC-8", "IA-3"]},
            "api": {"score": 50, "tier": "MEDIUM", "nist_controls": ["CA-3", "SC-8", "AC-4"]},
            "rest": {"score": 50, "tier": "MEDIUM", "nist_controls": ["CA-3", "SC-8", "AC-4"]},
            "federation": {"score": 30, "tier": "LOW", "nist_controls": ["CA-3", "IA-8", "AC-16"]},
            "saml": {"score": 30, "tier": "LOW", "nist_controls": ["CA-3", "IA-8", "AC-16"]},
            "oidc": {"score": 30, "tier": "LOW", "nist_controls": ["CA-3", "IA-8", "AC-16"]},
        }
        key = (interconnection_type or "api").lower().strip()
        return _RISK_TABLE.get(key, {"score": 50, "tier": "MEDIUM", "nist_controls": ["CA-3"]})

    def _generate_expiry_alerts(design_id: str) -> list[dict]:
        """Scan bd_isa_tracker for expiring ISAs and generate alert records.

        Thresholds: 90 / 60 / 30 days — severity escalates as deadline approaches.
        """
        import uuid as _uuid_mod

        thresholds = [
            (30, "critical", "ISA expires in ≤30 days"),
            (60, "high", "ISA expires in ≤60 days"),
            (90, "medium", "ISA expires in ≤90 days"),
        ]
        new_alerts: list[dict] = []
        with get_connection() as conn:
            isas = conn.execute(
                "SELECT id, interconnection_id, expiry_date, status, owner "
                "FROM bd_isa_tracker WHERE design_id=%s AND status NOT IN ('expired','revoked')",
                (design_id,),
            ).fetchall()

            for isa in isas:
                isa_dict = _row_to_dict(isa)
                days = _compute_days_until(isa_dict.get("expiry_date"))
                if days is None:
                    continue

                alert_type = None
                severity = None
                message = None

                if days < 0:
                    alert_type = "ISA_EXPIRED"
                    severity = "critical"
                    message = (
                        f"ISA for interconnection '{isa_dict['interconnection_id']}' "
                        f"EXPIRED {abs(days)} day(s) ago. Immediate action required."
                    )
                else:
                    for threshold, sev, prefix in thresholds:
                        if days <= threshold:
                            alert_type = f"ISA_EXPIRY_{threshold}D"
                            severity = sev
                            message = (
                                f"{prefix}: ISA for interconnection "
                                f"'{isa_dict['interconnection_id']}' expires in {days} day(s)."
                            )
                            break

                if not alert_type:
                    continue

                # Check if this alert already exists (avoid duplicates)
                existing = conn.execute(
                    "SELECT id FROM bd_alerts WHERE isa_id=%s AND alert_type=%s AND acknowledged=0",
                    (isa_dict["id"], alert_type),
                ).fetchone()
                if existing:
                    continue

                alert_id = f"bda-{_uuid_mod.uuid4().hex[:10]}"
                conn.execute(
                    "INSERT INTO bd_alerts "
                    "(id, design_id, isa_id, alert_type, severity, days_until_expiry, message, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (alert_id, design_id, isa_dict["id"], alert_type, severity, days, message, now_isoformat()),
                )
                new_alerts.append(
                    {
                        "id": alert_id,
                        "isa_id": isa_dict["id"],
                        "alert_type": alert_type,
                        "severity": severity,
                        "days_until_expiry": days,
                        "message": message,
                    }
                )
        return new_alerts

    @bp.route("/api/alerts", methods=["GET"])
    @bdc_login_required
    def bdc_api_alerts_global():
        """List all unacknowledged ISA expiry alerts across all designs."""
        design_id = request.args.get("design_id")
        with get_connection() as conn:
            if design_id:
                rows = conn.execute(
                    "SELECT a.*, i.interconnection_id, i.expiry_date, i.owner "
                    "FROM bd_alerts a LEFT JOIN bd_isa_tracker i ON a.isa_id=i.id "
                    "WHERE a.design_id=%s ORDER BY a.created_at DESC",
                    (design_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT a.*, i.interconnection_id, i.expiry_date, i.owner "
                    "FROM bd_alerts a LEFT JOIN bd_isa_tracker i ON a.isa_id=i.id "
                    "ORDER BY a.created_at DESC",
                ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/designs/<design_id>/alerts", methods=["GET"])
    @bdc_login_required
    def bdc_api_alerts(design_id):
        """List ISA expiry alerts for a specific boundary design.

        Query params:
          ?refresh=true  — Re-scan ISAs and generate fresh alerts before returning
          ?unacked=true  — Return only unacknowledged alerts (default: all)
        """
        if request.args.get("refresh", "").lower() in ("true", "1"):
            _generate_expiry_alerts(design_id)

        unacked_only = request.args.get("unacked", "false").lower() in ("true", "1")
        with get_connection() as conn:
            query = (
                "SELECT a.*, i.interconnection_id, i.expiry_date, i.owner "
                "FROM bd_alerts a LEFT JOIN bd_isa_tracker i ON a.isa_id=i.id "
                "WHERE a.design_id=%s"
            )
            params: list = [design_id]
            if unacked_only:
                query += " AND a.acknowledged=0"
            query += " ORDER BY a.created_at DESC"
            rows = conn.execute(query, params).fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/designs/<design_id>/alerts/refresh", methods=["POST"])
    @bdc_login_required
    def bdc_api_alerts_refresh(design_id):
        """Trigger an ISA expiry scan and create new alerts for a design."""
        new_alerts = _generate_expiry_alerts(design_id)
        _audit(design_id, "ALERT_REFRESH", f"generated={len(new_alerts)}")
        return jsonify({"generated": len(new_alerts), "alerts": new_alerts})

    @bp.route("/api/alerts/<alert_id>/acknowledge", methods=["POST"])
    @bdc_login_required
    def bdc_api_alert_acknowledge(alert_id):
        """Acknowledge an alert, suppressing it from active lists."""
        user_id = session.get("user_id", "system")
        with get_connection() as conn:
            row = conn.execute("SELECT id FROM bd_alerts WHERE id=%s", (alert_id,)).fetchone()
            if not row:
                return jsonify({"error": "Alert not found"}), 404
            conn.execute(
                "UPDATE bd_alerts SET acknowledged=1, acknowledged_by=%s, acknowledged_at=%s WHERE id=%s",
                (user_id, now_isoformat(), alert_id),
            )
        return jsonify({"status": "acknowledged", "alert_id": alert_id})

    @bp.route("/api/designs/<design_id>/risk-score", methods=["GET"])
    @bdc_login_required
    def bdc_api_risk_score(design_id):
        """Compute interconnection risk scores for all boundary edges.

        Returns per-interconnection risk and an overall design risk score.
        Also incorporates supply chain risk from ISA status.
        """
        with get_connection() as conn:
            row = conn.execute("SELECT graph_json FROM boundary_designs WHERE id=%s", (design_id,)).fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404

            # Supply-chain cross-link inputs degrade gracefully: a partial/fresh
            # canvas DB without bd_isa_tracker must not 500 the risk view — the
            # edge-derived scores and supply_chain_risk still compute (bdr-vv-1).
            try:
                isas = conn.execute(
                    "SELECT interconnection_id, status, expiry_date FROM bd_isa_tracker WHERE design_id=%s",
                    (design_id,),
                ).fetchall()
            except Exception as exc:  # noqa: BLE001 — missing table -> no ISA overlay
                logger.info("bd_isa_tracker unavailable for risk-score of %s: %s", design_id, exc)
                isas = []

        try:
            graph = json.loads(row["graph_json"]) if isinstance(row["graph_json"], str) else row["graph_json"]
        except (json.JSONDecodeError, TypeError):
            return jsonify({"error": "Invalid graph data"}), 400

        # Map interconnection IDs to ISA status
        isa_map: dict[str, dict] = {}
        for isa in isas:
            isa_d = _row_to_dict(isa)
            isa_map[isa_d["interconnection_id"]] = isa_d

        # Score each edge
        edges = graph.get("edges", [])
        scored_edges: list[dict] = []
        total_score = 0.0

        for edge in edges:
            edge_id = edge.get("id", "")
            edge_type = (edge.get("data", {}) or {}).get("interconnection_type", edge.get("type", "api"))
            label = edge.get("label", edge_id)

            risk_info = _isa_risk_score(edge_type)
            base_score = risk_info["score"]

            # ISA status modifier
            isa_info = isa_map.get(edge_id, {})
            isa_status = isa_info.get("status", "none")
            if isa_status == "expired":
                base_score = min(100, base_score + 20)
            elif isa_status == "draft":
                base_score = min(100, base_score + 10)
            elif isa_status == "active":
                base_score = max(0, base_score - 5)

            # Expiry proximity modifier
            days = _compute_days_until(isa_info.get("expiry_date"))
            if days is not None and 0 <= days <= 30:
                base_score = min(100, base_score + 15)

            scored_edges.append(
                {
                    "edge_id": edge_id,
                    "label": label,
                    "interconnection_type": edge_type,
                    "risk_tier": risk_info["tier"],
                    "risk_score": base_score,
                    "isa_status": isa_status,
                    "days_until_expiry": days,
                    "nist_controls": risk_info["nist_controls"],
                }
            )
            total_score += base_score

        overall = round(total_score / len(scored_edges), 1) if scored_edges else 0.0
        overall_tier = (
            "CRITICAL" if overall >= 80 else "HIGH" if overall >= 60 else "MEDIUM" if overall >= 40 else "LOW"
        )

        # Supply chain risk: scan ISAs with supply-chain-related notes
        supply_chain_risk: list[dict] = []
        for edge in scored_edges:
            if edge["risk_tier"] in ("CRITICAL", "HIGH"):
                supply_chain_risk.append(
                    {
                        "interconnection": edge["label"],
                        "risk_score": edge["risk_score"],
                        "recommendation": (
                            "Review supply chain risk for this interconnection. "
                            "Validate third-party security posture and add CMMC C2C controls."
                        ),
                    }
                )

        return jsonify(
            {
                "design_id": design_id,
                "overall_risk_score": overall,
                "overall_risk_tier": overall_tier,
                "edge_count": len(edges),
                "scored_edges": scored_edges,
                "supply_chain_risk": supply_chain_risk,
            }
        )

    # ====================================================================
    # RUNBOOK ROUTES
    # ====================================================================

    _VALID_TRIGGERS = {
        "boundary_breach",
        "isa_expiry",
        "unauthorized_interconnection",
        "pps_violation",
        "classification_boundary_cross",
        "isa_review_due",
        "fedramp_status_change",
        "supply_chain_alert",
    }
    _VALID_SEVERITIES = {"critical", "high", "medium", "low"}

    @bp.route("/runbooks")
    @bdc_login_required
    def bdc_runbooks_page():
        """Runbook library — all BDC response runbooks."""
        with get_connection() as conn:
            runbooks = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT id, title, trigger_event, severity, description, owner, "
                    "created_at, updated_at FROM bdc_runbooks ORDER BY severity, title"
                ).fetchall()
            ]
            designs = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT id, name FROM boundary_designs ORDER BY name"
                ).fetchall()
            ]
        return render_template(
            "boundary_canvas/runbooks.html",
            runbooks=runbooks,
            designs=designs,
            valid_triggers=sorted(_VALID_TRIGGERS),
            valid_severities=["critical", "high", "medium", "low"],
        )

    @bp.route("/api/runbooks", methods=["GET"])
    @bdc_login_required
    def bdc_api_list_runbooks():
        """List all runbooks, optionally filtered by trigger_event or design_id."""
        trigger = request.args.get("trigger_event", "")
        design_id = request.args.get("design_id", "")
        with get_connection() as conn:
            if trigger and design_id:
                rows = conn.execute(
                    "SELECT * FROM bdc_runbooks WHERE trigger_event=%s AND (design_id=%s OR design_id IS NULL) "
                    "ORDER BY severity, title",
                    (trigger, design_id),
                ).fetchall()
            elif trigger:
                rows = conn.execute(
                    "SELECT * FROM bdc_runbooks WHERE trigger_event=%s ORDER BY severity, title",
                    (trigger,),
                ).fetchall()
            elif design_id:
                rows = conn.execute(
                    "SELECT * FROM bdc_runbooks WHERE design_id=%s OR design_id IS NULL ORDER BY severity, title",
                    (design_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bdc_runbooks ORDER BY severity, title"
                ).fetchall()
        runbooks = []
        for r in rows:
            rb = _row_to_dict(r)
            try:
                rb["steps"] = json.loads(rb.get("steps_json") or "[]")
            except Exception:
                rb["steps"] = []
            runbooks.append(rb)
        return jsonify({"runbooks": runbooks, "count": len(runbooks)})

    @bp.route("/api/runbooks/<runbook_id>", methods=["GET"])
    @bdc_login_required
    def bdc_api_get_runbook(runbook_id):
        """Get a single runbook by ID."""
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM bdc_runbooks WHERE id=%s", (runbook_id,)).fetchone()
        if not row:
            return jsonify({"error": "Runbook not found"}), 404
        rb = _row_to_dict(row)
        try:
            rb["steps"] = json.loads(rb.get("steps_json") or "[]")
        except Exception:
            rb["steps"] = []
        return jsonify(rb)

    @bp.route("/api/runbooks", methods=["POST"])
    @bdc_login_required
    def bdc_api_create_runbook():
        """Create a new runbook."""
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title is required"}), 400
        trigger = data.get("trigger_event", "boundary_breach")
        if trigger not in _VALID_TRIGGERS:
            return jsonify({"error": f"invalid trigger_event: {trigger}"}), 400
        severity = data.get("severity", "high")
        if severity not in _VALID_SEVERITIES:
            severity = "high"
        steps = data.get("steps", [])
        if not isinstance(steps, list):
            steps = []
        rb_id = str(_uuid.uuid4())
        now = now_isoformat()
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO bdc_runbooks "
                "(id, design_id, title, trigger_event, severity, description, steps_json, owner, classification, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    rb_id,
                    data.get("design_id") or None,
                    title,
                    trigger,
                    severity,
                    (data.get("description") or "").strip(),
                    json.dumps(steps),
                    (data.get("owner") or "").strip(),
                    data.get("classification", "CUI // SP-CTI"),
                    now,
                    now,
                ),
            )
            conn.commit()
        _audit(data.get("design_id") or "global", "runbook_created", f"id={rb_id} title={title}")
        return jsonify({"id": rb_id, "status": "created"}), 201

    @bp.route("/api/runbooks/<runbook_id>", methods=["PUT"])
    @bdc_login_required
    def bdc_api_update_runbook(runbook_id):
        """Update an existing runbook."""
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM bdc_runbooks WHERE id=%s", (runbook_id,)).fetchone()
            if not row:
                return jsonify({"error": "Runbook not found"}), 404
            data = request.get_json(silent=True) or {}
            existing = _row_to_dict(row)
            title = (data.get("title") or existing.get("title") or "").strip()
            trigger = data.get("trigger_event", existing.get("trigger_event", "boundary_breach"))
            if trigger not in _VALID_TRIGGERS:
                trigger = existing.get("trigger_event", "boundary_breach")
            severity = data.get("severity", existing.get("severity", "high"))
            if severity not in _VALID_SEVERITIES:
                severity = existing.get("severity", "high")
            steps = data.get("steps", None)
            if steps is None:
                steps_json = existing.get("steps_json", "[]")
            else:
                steps_json = json.dumps(steps if isinstance(steps, list) else [])
            conn.execute(
                "UPDATE bdc_runbooks SET title=%s, trigger_event=%s, severity=%s, description=%s, "
                "steps_json=%s, owner=%s, classification=%s, updated_at=%s WHERE id=%s",
                (
                    title,
                    trigger,
                    severity,
                    (data.get("description") or existing.get("description") or "").strip(),
                    steps_json,
                    (data.get("owner") or existing.get("owner") or "").strip(),
                    data.get("classification", existing.get("classification", "CUI // SP-CTI")),
                    now_isoformat(),
                    runbook_id,
                ),
            )
            conn.commit()
        _audit(existing.get("design_id") or "global", "runbook_updated", f"id={runbook_id}")
        return jsonify({"id": runbook_id, "status": "updated"})

    @bp.route("/api/runbooks/<runbook_id>", methods=["DELETE"])
    @bdc_login_required
    def bdc_api_delete_runbook(runbook_id):
        """Delete a runbook."""
        with get_connection() as conn:
            row = conn.execute("SELECT design_id FROM bdc_runbooks WHERE id=%s", (runbook_id,)).fetchone()
            if not row:
                return jsonify({"error": "Runbook not found"}), 404
            design_id = row["design_id"] or "global"
            conn.execute("DELETE FROM bdc_runbooks WHERE id=%s", (runbook_id,))
            conn.commit()
        _audit(design_id, "runbook_deleted", f"id={runbook_id}")
        return jsonify({"status": "deleted"})

    @bp.route("/api/designs/<design_id>/runbooks", methods=["GET"])
    @bdc_login_required
    def bdc_api_design_runbooks(design_id):
        """List runbooks associated with a specific design (or global runbooks)."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM bdc_runbooks WHERE design_id=%s OR design_id IS NULL ORDER BY severity, title",
                (design_id,),
            ).fetchall()
        runbooks = []
        for r in rows:
            rb = _row_to_dict(r)
            try:
                rb["steps"] = json.loads(rb.get("steps_json") or "[]")
            except Exception:
                rb["steps"] = []
            runbooks.append(rb)
        return jsonify({"design_id": design_id, "runbooks": runbooks, "count": len(runbooks)})

    # ── cATO ─────────────────────────────────────────────────────────────────

    @bp.route("/cato")
    @bdc_login_required
    def bdc_cato_page():
        """cATO Dashboard — all boundary designs with ATO health status."""
        with get_connection() as conn:
            designs = []
            for r in conn.execute(
                "SELECT id, name, classification, updated_at FROM boundary_designs ORDER BY updated_at DESC"
            ).fetchall():
                d = _row_to_dict(r)
                assess = conn.execute(
                    "SELECT score, grade, cat1_findings FROM bd_assessments "
                    "WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
                    (d["id"],),
                ).fetchone()
                if assess:
                    d["score"] = assess["score"]
                    d["grade"] = assess["grade"]
                    d["cat1_findings"] = assess["cat1_findings"]
                else:
                    d["score"] = None
                    d["grade"] = None
                    d["cat1_findings"] = 0
                designs.append(d)
        # rmf-fab-02: cross-fabric roll-up, rendered as a SECTION of this page.
        # Read-only, and it degrades to `unmeasurable` (never an empty clean
        # board) when the rmf-fab-01 registry has not landed.
        try:
            from tools.fabric.posture import roll_up
            fabric_posture = roll_up()
        except Exception as exc:  # noqa: BLE001 - a roll-up must never 500 this page
            logger.warning("cross-fabric posture roll-up failed: %s", exc)
            fabric_posture = {
                "fabric_count": 0,
                "fabrics": [],
                "registry": {"state": "unreadable", "reason": str(exc)},
                "unmeasurable": True,
                "unmeasurable_reason": f"roll-up failed: {exc}",
            }
        return render_template(
            "boundary_canvas/cato.html", designs=designs, fabric_posture=fabric_posture
        )

    # ── ATO Compliance Dashboard (rmf-ui-01) ─────────────────────────────────
    #
    # Migrated from a bare `@app.route("/ato-compliance")` in
    # tools/dashboard/app.py, where it had no registry entry, no RBAC guard, no
    # completeness gate and no IQE dispatch. On this blueprint it inherits all
    # four: app.py attaches guard_component_access("bdc", min_il) as a
    # before_request on every registered canvas blueprint, the registry's
    # url_prefix + IQE adapter put /boundary/* on the path->canvas map, and the
    # canvas completeness gate owns boundary_canvas/. The page drives the
    # UNCHANGED /api/ato-compliance/* blueprint (tools/dashboard/api/
    # ato_compliance.py) -- only the PAGE route moved. The old URL redirects
    # here rather than 404ing: a silently dropped page is the failure mode a
    # one-route-per-card migration exists to refuse.

    @bp.route("/ato-compliance")
    @bdc_login_required
    def bdc_ato_compliance_page():
        """ATO Compliance Dashboard — control tracking, RMF stages, artifact readiness, crosswalk."""
        return render_template("boundary_canvas/ato_compliance.html")

    # ── Continuous ATO Health (rmf-ui-05) ────────────────────────────────────
    #
    # Migrated from a bare `@app.route("/cato")` in tools/dashboard/app.py --
    # the same shape as rmf-ui-01 above: ungoverned there, registry-registered,
    # RBAC-guarded and IQE-dispatchable here BY CONSTRUCTION. It is NOT folded
    # into /boundary/cato: that page is a per-design assessment table over
    # bd_assessments and the fabric posture roll-up, while this one is the
    # fleet-wide health gauge, evidence stream, certifications and timeline
    # over the UNCHANGED /api/cato/* blueprint (tools/dashboard/api/cato.py)
    # -- two views over two data models, so they keep two titles and two
    # routes, cross-linked. Only the PAGE route moved; the old URL is a 301.

    @bp.route("/cato-health")
    @bdc_login_required
    def bdc_cato_health_page():
        """Continuous ATO Health — ATO health score, evidence freshness, certifications, timeline."""
        return render_template("boundary_canvas/cato_health.html")

    # ── POA&M Findings (rmf-ui-07) ───────────────────────────────────────────
    #
    # Migrated from a bare `@app.route("/poam")` in tools/dashboard/app.py --
    # the same move as the ATO Compliance Dashboard above (rmf-ui-01), one
    # route per card. The page is the findings approval workflow across the
    # seven canvas DBs, an RMF artifact surface, so the Boundary canvas is its
    # governed home. It drives the UNCHANGED /api/poam/* routes in app.py --
    # only the PAGE route moved -- and the old URL redirects here rather than
    # 404ing.

    @bp.route("/poam")
    @bdc_login_required
    def bdc_poam_page():
        """POA&M — canvas findings approval workflow across the 7 canvas DBs."""
        return render_template("boundary_canvas/poam/list.html")

    # ── OSCAL Ecosystem (rmf-ui-04) ──────────────────────────────────────────
    #
    # Migrated from a bare `@app.route("/oscal")` in tools/dashboard/app.py --
    # the same shape as the rmf-ui-01 exemplar above. An RMF ARTIFACT surface
    # (OSCAL SSP/SAP/SAR/POA&M validation, catalog lookup, format conversion)
    # belongs on the canvas that already ships oscal_cato_exporter.py. The
    # page drives the UNCHANGED /api/oscal/* routes -- only the PAGE route
    # moved. The old URL redirects here rather than 404ing.

    @bp.route("/oscal")
    @bdc_login_required
    def bdc_oscal_page():
        """OSCAL ecosystem — validation, catalog, format conversion (D302-D306)."""
        return render_template("boundary_canvas/oscal.html")
    # ── FedRAMP 20x KSI Dashboard (rmf-ui-06) ────────────────────────────────
    #
    # Migrated from a bare `@app.route("/fedramp-20x")` in tools/dashboard/app.py
    # -- an RMF ARTIFACT surface (KSI evidence, maturity levels, authorization
    # package) that had no registry entry, no RBAC guard, no completeness gate
    # and no IQE dispatch, and was an orphan until rmf-inert-01 linked it. On
    # this blueprint it inherits all four by construction, exactly as the
    # rmf-ui-01 exemplar above. The page drives the UNCHANGED /api/fedramp-20x/*
    # blueprint -- only the PAGE route moved; the old URL is a 301 here.

    @bp.route("/fedramp-20x")
    @bdc_login_required
    def bdc_fedramp_20x_page():
        """FedRAMP 20x KSI Dashboard — KSI evidence generation, maturity levels, authorization package."""
        return render_template("boundary_canvas/fedramp_20x.html")

    # ── Compliance Debt Burndown (rmf-ui-09) ─────────────────────────────────
    #
    # Migrated from a bare `@app.route("/compliance-debt")` in
    # tools/dashboard/app.py -- the same shape as the rmf-ui-01 exemplar above.
    # An RMF ARTIFACT surface (POA&M, control and STIG debt burndown, ATO
    # expirations, SLA compliance) belongs on the canvas that owns the ATO
    # boundary. The page drives the UNCHANGED /api/compliance-debt/* blueprint
    # (tools/dashboard/api/compliance_debt.py) -- only the PAGE route moved.
    # The old URL redirects here rather than 404ing.

    @bp.route("/compliance-debt")
    @bdc_login_required
    def bdc_compliance_debt_page():
        """Compliance Debt Burndown — POAM, control, and STIG debt tracking."""
        return render_template("boundary_canvas/compliance_debt.html")

    @bp.route("/api/fabric-posture", methods=["GET"])
    @bdc_login_required
    def bdc_api_fabric_posture():
        """Cross-fabric posture roll-up (rmf-fab-02) — READ ONLY.

        GET with no POST sibling, by construction: this renders what the two
        cATO modules have ALREADY recorded and evaluates nothing on read.
        Five measures per fabric, each with its own denominator, and two cATO
        sources each labelled with its scope. There is no composite anywhere in
        the payload and ``assert_no_blended_score`` enforces that before it is
        serialised.
        """
        from tools.fabric.posture import assert_no_blended_score, roll_up
        try:
            result = roll_up()
            assert_no_blended_score(result)
        except AssertionError:
            raise
        except Exception as exc:  # noqa: BLE001 - never surface a 500 to the tile
            logger.warning("cross-fabric posture roll-up failed: %s", exc)
            result = {
                "fabric_count": 0,
                "fabrics": [],
                "registry": {"state": "unreadable", "reason": str(exc)},
                "unmeasurable": True,
                "unmeasurable_reason": f"roll-up failed: {exc}",
            }
        fabric_key = request.args.get("fabric")
        if fabric_key:
            result["fabrics"] = [f for f in result["fabrics"] if f["key"] == fabric_key]
            result["fabric_count"] = len(result["fabrics"])
        return jsonify(result), 200

    @bp.route("/api/designs/<design_id>/findings", methods=["GET"])
    @bdc_login_required
    def bdc_api_findings(design_id):
        """Latest assessment findings (control rows) for a boundary design."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT findings_json FROM bd_assessments WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"findings": []})
        try:
            findings = json.loads(row["findings_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            findings = []
        return jsonify({"findings": findings})

    # ── SOPs ──────────────────────────────────────────────────────────────────

    @bp.route("/sops")
    @bdc_login_required
    def bdc_sops_page():
        """SOP library — boundary-specific standard operating procedures."""
        from tools.boundary_canvas.sops import get_all_sops
        sops = get_all_sops()
        return render_template("boundary_canvas/sops.html", sops=sops)

    @bp.route("/api/sops", methods=["GET"])
    @bdc_login_required
    def bdc_api_list_sops():
        """List all SOPs, optionally filtered by sop_type and approval_status."""
        from tools.boundary_canvas.sops import get_all_sops
        sop_type = request.args.get("type", "")
        status = request.args.get("status", "")
        sops = get_all_sops(
            sop_type=sop_type or None,
            approval_status=status or None,
        )
        return jsonify({"sops": sops, "count": len(sops)})

    @bp.route("/api/sops/<sop_id>", methods=["GET"])
    @bdc_login_required
    def bdc_api_get_sop(sop_id):
        """Get a single SOP by ID."""
        from tools.boundary_canvas.sops import get_sop_by_id
        sop = get_sop_by_id(sop_id)
        if not sop:
            return jsonify({"error": "SOP not found"}), 404
        return jsonify(sop)

    @bp.route("/api/sops", methods=["POST"])
    @bdc_login_required
    def bdc_api_create_sop():
        """Create a new SOP."""
        from tools.boundary_canvas.sops import create_sop
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title is required"}), 400
        sop = create_sop(data)
        _audit("global", "sop_created", f"id={sop['id']} title={title}")
        return jsonify(sop), 201

    @bp.route("/api/sops/<sop_id>", methods=["PUT"])
    @bdc_login_required
    def bdc_api_update_sop(sop_id):
        """Update an existing SOP."""
        from tools.boundary_canvas.sops import update_sop
        data = request.get_json(silent=True) or {}
        sop = update_sop(sop_id, data)
        if not sop:
            return jsonify({"error": "SOP not found"}), 404
        _audit("global", "sop_updated", f"id={sop_id}")
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>", methods=["DELETE"])
    @bdc_login_required
    def bdc_api_delete_sop(sop_id):
        """Delete a SOP."""
        from tools.boundary_canvas.sops import delete_sop
        deleted = delete_sop(sop_id)
        if not deleted:
            return jsonify({"error": "SOP not found"}), 404
        _audit("global", "sop_deleted", f"id={sop_id}")
        return jsonify({"status": "deleted"})

    @bp.route("/api/sops/<sop_id>/submit", methods=["POST"])
    @bdc_login_required
    def bdc_api_submit_sop(sop_id):
        """Submit a SOP for review (draft → pending_review)."""
        from tools.boundary_canvas.sops import submit_for_review
        sop, err = submit_for_review(sop_id)
        if err:
            return jsonify({"error": err}), 400
        _audit("global", "sop_submitted", f"id={sop_id}")
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>/approve", methods=["POST"])
    @bdc_login_required
    def bdc_api_approve_sop(sop_id):
        """Approve a pending SOP."""
        from tools.boundary_canvas.sops import approve_sop
        data = request.get_json(silent=True) or {}
        approved_by = (data.get("approved_by") or "").strip()
        sop, err = approve_sop(sop_id, approved_by=approved_by)
        if err:
            return jsonify({"error": err}), 400
        _audit("global", "sop_approved", f"id={sop_id} by={approved_by}")
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>/reject", methods=["POST"])
    @bdc_login_required
    def bdc_api_reject_sop(sop_id):
        """Reject a pending SOP with reason."""
        from tools.boundary_canvas.sops import reject_sop
        data = request.get_json(silent=True) or {}
        reason = (data.get("reason") or "").strip()
        rejected_by = (data.get("rejected_by") or "").strip()
        if not reason:
            return jsonify({"error": "reason is required"}), 400
        sop, err = reject_sop(sop_id, reason=reason, rejected_by=rejected_by)
        if err:
            return jsonify({"error": err}), 400
        _audit("global", "sop_rejected", f"id={sop_id} reason={reason[:60]}")
        return jsonify(sop)

    # ── GraphRAG /ask — shared canvas_ask pattern ──────────────────────────
    @bp.route("/ask")
    @bdc_login_required
    def bdc_ask_page():
        return render_template(
            "canvas_ask.html",
            canvas_label="Boundary Design Canvas",
            graph_id="bdc-designs",
            profile="compliance",
            examples=["boundary", "interconnection", "ISA", "authorization", "CUI"],
            api_url="/boundary/api/ask",
            home_url="/boundary/",
        )

    @bp.route("/api/ask", methods=["POST"])
    @bdc_login_required
    def bdc_api_ask():
        from tools.knowledge_graph.canvas_ask import handle_ask_request
        data = request.get_json(silent=True) or {}
        payload = handle_ask_request(
            query=data.get("query", ""),
            graph_id="bdc-designs",
            profile="compliance",
            top_k=int(data.get("top_k", 10)),
            narrate=bool(data.get("narrate", False)),
            canvas_label="authorization boundary",
        )
        status = payload.pop("_status", 200)
        return jsonify(payload), status

    # ── Digital Twin ───────────────────────────────────────────────────────
    @bp.route("/twin/<design_id>")
    @bdc_login_required
    def bdc_twin_page(design_id):
        conn = get_connection()
        design = conn.execute("SELECT * FROM boundary_designs WHERE id=%s", (design_id,)).fetchone()
        if not design:
            return render_template("404.html"), 404
        design = _row_to_dict(design)
        try:
            snapshots = conn.execute(
                "SELECT * FROM compliance_snapshots WHERE project_id=%s ORDER BY taken_at DESC LIMIT 20",
                (design_id,),
            ).fetchall()
        except Exception:
            snapshots = []
        frameworks = ["FedRAMP Moderate", "CMMC Level 2", "NIST 800-53", "NIST 800-171", "IL4", "IL5"]
        return render_template(
            "boundary_canvas/twin.html",
            project=design,
            snapshots=[_row_to_dict(s) for s in snapshots],
            frameworks=frameworks,
        )

    @bp.route("/api/twin/<design_id>/snapshot", methods=["POST"])
    @bdc_login_required
    def bdc_api_twin_snapshot(design_id):
        from tools.boundary_canvas.twin import take_snapshot
        data = request.get_json(silent=True) or {}
        snap = take_snapshot(design_id, framework_id=data.get("framework_id", "FedRAMP Moderate"))
        if snap.get("status") == "error":
            _audit(design_id, "twin_snapshot_failed", f"err={snap.get('error')}")
            return jsonify(snap), 500
        _audit(design_id, "twin_snapshot", f"snap={snap.get('snapshot_id')}")
        return jsonify(snap), 201

    @bp.route("/api/twin/<design_id>/simulate", methods=["POST"])
    @bdc_login_required
    def bdc_api_twin_simulate(design_id):
        from tools.boundary_canvas.twin import simulate_delta
        data = request.get_json(silent=True) or {}
        result = simulate_delta(
            design_id,
            delta=data.get("delta", []),
            framework_id=data.get("framework_id", "FedRAMP Moderate"),
            baseline_snap_id=data.get("baseline_snap_id"),
        )
        _audit(design_id, "twin_simulate", f"verdict={result.get('rating')}")
        return jsonify(result), 200

    @bp.route("/api/twin/<design_id>/simulate-cod", methods=["POST"])
    @bdc_login_required
    def bdc_api_twin_simulate_cod(design_id):
        from tools.boundary_canvas.twin import simulate_delta
        data = request.get_json(silent=True) or {}
        result = simulate_delta(
            design_id,
            delta=data.get("delta", []),
            framework_id=data.get("framework_id", "FedRAMP Moderate"),
            baseline_snap_id=data.get("baseline_snap_id"),
            use_cod=True,
        )
        _audit(design_id, "twin_simulate_cod", f"verdict={result.get('rating')}")
        return jsonify(result), 200

    @bp.route("/api/twin/<design_id>/crosswalk-drift", methods=["GET"])
    @bdc_login_required
    def bdc_api_twin_crosswalk(design_id):
        from tools.boundary_canvas.twin import crosswalk_drift
        fw_src = request.args.get("fw_src", "NIST 800-53")
        fw_tgt = request.args.get("fw_tgt", "CMMC Level 2")
        result = crosswalk_drift(design_id, fw_src, fw_tgt)
        return jsonify(result), (500 if result.get("status") == "error" else 200)

    @bp.route("/api/twin/<design_id>/oscal-export", methods=["GET"])
    @bdc_login_required
    def bdc_api_twin_oscal(design_id):
        snap_id = request.args.get("snapshot_id")
        artifact_type = request.args.get("artifact_type", "ssp")
        from tools.boundary_canvas.twin import export_oscal
        # The generator reads project state from the ICDEV main DB keyed by the
        # design's id (project_id). A design whose id has no matching projects row
        # yields an {"status": "error", ...} payload — surfaced as HTTP 400 so the
        # twin UI renders a clear error state instead of a phantom artifact.
        result = export_oscal(design_id, snap_id, artifact_type)
        if result.get("status") == "error":
            logger.warning(
                "OSCAL export failed for design %s (type=%s): %s",
                design_id, artifact_type, result.get("error"),
            )
            return jsonify(result), 400
        _audit(design_id, "twin_oscal_export", f"type={artifact_type} valid={result.get('valid')}")
        return jsonify(result), 200

    @bp.route("/api/cato/readiness/<design_id>", methods=["GET"])
    @bdc_login_required
    def bdc_api_cato_readiness(design_id):
        """Compute the 0-100 cATO readiness composite for a design.

        Degrades gracefully — missing tables/data yield score=None + band
        'unknown' rather than a 500.
        """
        from tools.boundary_canvas.cato_readiness import compute_readiness
        project_id = request.args.get("project_id") or design_id
        try:
            result = compute_readiness(design_id, project_id)
        except Exception as exc:  # noqa: BLE001 — never surface a 500 to the tile
            logger.warning("cATO readiness compute failed for %s: %s", design_id, exc)
            result = {
                "design_id": design_id,
                "project_id": project_id,
                "score": None,
                "readiness_score": None,
                "band": "unknown",
                "components": {},
                "error": str(exc),
            }
        return jsonify(result), 200

    @bp.route("/api/twin/<design_id>/current-topology", methods=["GET"])
    @bdc_login_required
    def bdc_api_twin_current_topology(design_id):
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM boundary_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "Design not found"}), 404
        try:
            graph = json.loads(row["graph_json"] or "{}")
        except Exception:
            graph = {}
        return jsonify({"graph_json": graph}), 200

    @bp.route("/api/twin/<design_id>/chat-delta", methods=["POST"])
    @bdc_login_required
    def bdc_api_twin_chat_delta(design_id):
        from tools.twin_chat import boundary_chat_to_delta
        data = request.get_json(silent=True) or {}
        message = (data.get("message") or "").strip()
        if not message:
            return jsonify({"error": "message is required"}), 400
        result = boundary_chat_to_delta(message, data.get("graph_json"))
        return jsonify(result), (500 if "error" in result else 200)

    @bp.route("/api/iqe-query", methods=["POST"])
    @bdc_login_required
    def bdc_api_iqe_query():
        """IQE structured query — translate NL to IQE and execute against BDC boundary data."""
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import IQESyntaxError, parse
        from tools.iqe.executor import execute_query
        import tools.iqe.adapters.bdc  # noqa: F401 — registers bdc.* collections

        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400

        collections = ["bdc.designs", "bdc.assessments", "bdc.isas", "bdc.alerts"]
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
            logger.warning("BDC IQE query error: %s", exc)
            return jsonify({"error": str(exc), "iqe": iqe_str}), 500

    @bp.route("/api/ai-trace")
    @bdc_login_required
    def bdc_api_ai_trace():
        """Return recent AI decisions made by BDC assessment engines."""
        limit = min(int(request.args.get("limit", 50)), 200)
        record_id = request.args.get("record_id")
        try:
            from tools.db.storage import get_connection as _gc
            with _gc() as _conn:
                if record_id:
                    rows = _conn.execute(
                        "SELECT * FROM canvas_ai_decisions WHERE canvas_type='bdc' AND record_id=%s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (record_id, limit),
                    ).fetchall()
                else:
                    rows = _conn.execute(
                        "SELECT * FROM canvas_ai_decisions WHERE canvas_type='bdc' "
                        "ORDER BY created_at DESC LIMIT %s",
                        (limit,),
                    ).fetchall()
            return jsonify({"ok": True, "canvas": "bdc", "decisions": [dict(r) for r in rows]})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    def _compute_boundary_governance(graph_data: dict) -> dict:
        """Boundary governance check — Zero Trust / NIST 800-207 / FIPS 140-3 aligned."""
        from datetime import datetime, timezone as _tz
        nodes = graph_data.get("nodes", [])
        types = [n.get("type", "").lower() for n in nodes]
        labels = [str(n.get("label", "")).lower() for n in nodes]

        def _any(*pfx): return any(any(t.startswith(p) for p in pfx) for t in types)
        def _lbl(*kws): return any(kw in l for l in labels for kw in kws)

        CHECKS = [
            ("Zero Trust Architecture Defined",    "Zero Trust",        "CAT1", _any("sys-","isa-") or _lbl("zero trust","zt","zta")),
            ("Encryption Zones Defined",            "Encryption Zones",  "CAT1", _any("bnd-enc","bnd-fips") or _lbl("encrypt zone","fips","tls zone","cipher")),
            ("FIPS 140-2/3 Encryption",             "Encryption Zones",  "CAT1", _lbl("fips","nss","cryptographic module")),
            ("Access Control Boundaries",           "Access Boundaries", "CAT1", _any("bnd-","sys-iam") or _lbl("access boundary","acl","rbac","least privilege")),
            ("Network Segmentation Boundaries",     "Access Boundaries", "CAT2", _lbl("dmz","segment","perimeter","trust boundary","isolat")),
            ("API Gateway / Service Mesh Control",  "Access Boundaries", "CAT2", _any("sys-apigw","sys-mesh") or _lbl("api gateway","service mesh","istio","kong","apigee")),
            ("Mutual TLS (mTLS) Enforcement",       "Encryption Zones",  "CAT2", _lbl("mtls","mutual tls","client cert","bilateral tls")),
            ("PKI / Certificate Management",        "Encryption Zones",  "CAT2", _lbl("pki","certificate authority","ca","x.509","cert manager")),
            ("Identity Federation",                 "Zero Trust",        "CAT2", _lbl("saml","oidc","oauth","federation","sso","identity provider")),
            ("Continuous Verification",             "Zero Trust",        "CAT2", _lbl("continuous verif","posture","mfa","adaptive auth","risk-based")),
            ("Audit Logging Boundary",              "Audit & Logging",   "CAT1", _any("sys-log","sys-siem") or _lbl("audit log","siem","log boundary","cloud trail")),
            ("Real-time Threat Detection",          "Audit & Logging",   "CAT2", _lbl("threat detect","ids","ips","intrusion","guard duty","defender","sentinel")),
            ("ISA / System Interconnect Docs",      "Compliance",        "CAT2", _any("isa-") or _lbl("isa","ato","interconnect agreement","mou","system boundary")),
            ("DoD IL / FedRAMP Boundary Marked",    "Compliance",        "CAT1", _lbl("fedramp","dod","il4","il5","il6","cmmc","stig")),
            ("Data Classification Labels",          "Compliance",        "CAT2", _lbl("cui","secret","classified","marking","classification")),
            ("Supply Chain Risk Controls",          "Compliance",        "CAT3", _lbl("supply chain","sbom","third party","vendor risk","scrm")),
            ("Boundary Monitoring & Alerting",      "Audit & Logging",   "CAT2", _lbl("alert","monitor","soc","noc","watchdog","heartbeat")),
            ("Microsegmentation",                   "Access Boundaries", "CAT3", _lbl("microsegment","workload isolation","pod security","namespace")),
        ]

        PILLARS = ["Zero Trust", "Encryption Zones", "Access Boundaries", "Audit & Logging", "Compliance"]
        WEIGHTS = {"CAT1": 3, "CAT2": 2, "CAT3": 1}
        MATURITY = [
            (0,  "L1 — Initial",    "Ad-hoc boundaries, no formal controls."),
            (30, "L2 — Developing", "Some boundary controls but inconsistent."),
            (55, "L3 — Defined",    "Structured boundaries with documented controls."),
            (70, "L4 — Managed",    "Monitored boundaries with automated enforcement."),
            (85, "L5 — Optimised",  "Continuous verification and adaptive trust."),
        ]

        check_results, total_w, passed_w = [], 0, 0
        cats = {p: {"passed": 0, "total": 0, "pct": 0} for p in PILLARS}
        for title, pillar, sev, passed in CHECKS:
            w = WEIGHTS[sev]
            total_w += w
            status = "pass" if passed else "fail"
            if passed:
                passed_w += w
            cats.setdefault(pillar, {"passed": 0, "total": 0, "pct": 0})
            cats[pillar]["total"] += 1
            if passed:
                cats[pillar]["passed"] += 1
            check_results.append({"title": title, "pillar": pillar, "severity": sev,
                                   "status": status, "weight": w, "detail": ""})
        for c in cats.values():
            c["pct"] = round(c["passed"] / c["total"] * 100) if c["total"] else 0

        score = round(passed_w / total_w * 100) if total_w else 0
        grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"
        mat_level = sum(1 for t, *_ in MATURITY if score >= t)
        mat_label, mat_desc = MATURITY[mat_level - 1][1], MATURITY[mat_level - 1][2]

        recs = [{"title": c["title"], "pillar": c["pillar"], "priority": c["severity"]}
                for c in check_results if c["status"] == "fail"]
        recs.sort(key=lambda r: {"CAT1": 0, "CAT2": 1, "CAT3": 2}[r["priority"]])
        return {
            "score": score, "grade": grade,
            "maturity": {"level": mat_level, "label": mat_label.split(" — ")[1], "description": mat_desc},
            "checks": check_results, "categories": cats, "recommendations": recs,
            "total_checks": len(CHECKS), "passed_checks": sum(1 for c in check_results if c["status"] == "pass"),
            "assessed_at": datetime.now(_tz.utc).isoformat(),
        }

    @bp.route("/api/designs/<design_id>/governance", methods=["POST"])
    @bdc_login_required
    def bdc_api_governance(design_id):
        """Run boundary governance framework check."""
        import uuid as _uuid_mod
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM boundary_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        try:
            graph_data = json.loads(row["graph_json"]) if isinstance(row["graph_json"], str) else row["graph_json"]
        except (json.JSONDecodeError, TypeError):
            conn.close()
            return jsonify({"error": "Invalid graph data"}), 400

        result = _compute_boundary_governance(graph_data)

        assess_id = str(_uuid_mod.uuid4())
        conn.execute(
            "INSERT INTO bd_assessments "
            "(id, design_id, assessment_type, findings_json, score, grade, "
            "cat1_findings, cat2_findings, cat3_findings, nist_coverage_json, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (assess_id, design_id, "governance",
             json.dumps([{"title": c["title"], "severity": c["severity"], "status": c["status"]}
                         for c in result["checks"]]),
             result["score"], result["grade"],
             sum(1 for c in result["checks"] if c["severity"] == "CAT1" and c["status"] == "fail"),
             sum(1 for c in result["checks"] if c["severity"] == "CAT2" and c["status"] == "fail"),
             sum(1 for c in result["checks"] if c["severity"] == "CAT3" and c["status"] == "fail"),
             "{}", now_isoformat()),
        )
        conn.commit()
        conn.close()
        return jsonify(result)

    # ====================================================================
    # API ROUTES — RICOAS 4-tier Boundary Impact (ATO)  [bdr-feat-3]
    # Surfaces tools/requirements/boundary_analyzer.py (GREEN/YELLOW/
    # ORANGE/RED tiers + RED-alternative COAs) in the /boundary UI.
    # The engine reads MAIN-DB tables (ato_system_registry,
    # intake_requirements, boundary_impact_assessments); call it in-process.
    # A boundary design links to a project via an optional ?project_id=
    # (falling back to the design_id, matching the cATO readiness route).
    # ====================================================================

    def _impact_req_label(raw_text: str, refined_text: str) -> str:
        """Build a short, human-friendly requirement label for the picker."""
        text = (refined_text or "").strip() or (raw_text or "").strip()
        text = " ".join(text.split())
        return text[:120] + ("…" if len(text) > 120 else "")

    @bp.route("/api/impact/context", methods=["GET"])
    @bdc_login_required
    def bdc_api_impact_context():
        """Return registered ATO systems + intake requirements for the design's
        linked project, so the Boundary Impact panel can populate its pickers.

        Never 500s: a missing project, missing tables, or empty registry all
        yield a clean empty-state payload.
        """
        from tools.requirements import boundary_analyzer

        design_id = request.args.get("design_id", "")
        project_id = request.args.get("project_id") or design_id

        systems: list = []
        requirements: list = []
        try:
            sys_result = boundary_analyzer.list_systems(project_id)
            systems = sys_result.get("systems", [])
        except Exception as exc:  # noqa: BLE001 — empty-state, never a 500
            logger.warning("impact/context list_systems failed for %s: %s", project_id, exc)

        try:
            from tools.db.storage import get_connection as _mgc

            conn = _mgc(db_path=str(boundary_analyzer.DB_PATH))
            rows = conn.execute(
                "SELECT id, raw_text, refined_text, requirement_type "
                "FROM intake_requirements WHERE project_id=%s ORDER BY created_at",
                (project_id,),
            ).fetchall()
            conn.close()
            for r in rows:
                d = dict(r)
                requirements.append(
                    {
                        "id": d.get("id"),
                        "label": _impact_req_label(d.get("raw_text", ""), d.get("refined_text", "")),
                        "requirement_type": d.get("requirement_type"),
                    }
                )
        except Exception as exc:  # noqa: BLE001 — empty-state, never a 500
            logger.warning("impact/context requirements read failed for %s: %s", project_id, exc)

        empty = not systems
        message = (
            "No ATO system registered for this project — register via intake." if empty else ""
        )
        return jsonify(
            {
                "design_id": design_id,
                "project_id": project_id,
                "systems": systems,
                "requirements": requirements,
                "empty": empty,
                "message": message,
            }
        )

    @bp.route("/api/impact/assess", methods=["POST"])
    @bdc_login_required
    def bdc_api_impact_assess():
        """Assess a requirement against an ATO system boundary → 4-tier result."""
        from tools.requirements import boundary_analyzer

        data = request.get_json(silent=True) or {}
        system_id = (data.get("system_id") or "").strip()
        requirement_id = (data.get("requirement_id") or "").strip()
        if not system_id or not requirement_id:
            return jsonify({"error": "system_id and requirement_id are required"}), 400

        project_id = data.get("project_id")
        try:
            if not project_id:
                sys_info = boundary_analyzer.get_system(system_id)
                project_id = (sys_info.get("system", {}) or {}).get("project_id")
            result = boundary_analyzer.assess_boundary_impact(project_id, system_id, requirement_id)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            logger.warning("impact/assess failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

        result["project_id"] = project_id
        _audit(
            request.args.get("design_id", ""),
            "IMPACT_ASSESS",
            f"sys={system_id} req={requirement_id} tier={result.get('impact_tier')}",
        )
        return jsonify(result)

    @bp.route("/api/impact/alternatives", methods=["POST"])
    @bdc_login_required
    def bdc_api_impact_alternatives():
        """Generate RED-tier alternative COAs for a boundary impact assessment."""
        from tools.requirements import boundary_analyzer

        data = request.get_json(silent=True) or {}
        assessment_id = (data.get("assessment_id") or "").strip()
        if not assessment_id:
            return jsonify({"error": "assessment_id is required"}), 400

        project_id = data.get("project_id")
        try:
            if not project_id:
                from tools.db.storage import get_connection as _mgc

                conn = _mgc(db_path=str(boundary_analyzer.DB_PATH))
                row = conn.execute(
                    "SELECT project_id FROM boundary_impact_assessments WHERE id=%s",
                    (assessment_id,),
                ).fetchone()
                conn.close()
                if row:
                    project_id = dict(row).get("project_id")
            result = boundary_analyzer.generate_alternatives(project_id, assessment_id)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            logger.warning("impact/alternatives failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

        return jsonify(result)

    return bp
