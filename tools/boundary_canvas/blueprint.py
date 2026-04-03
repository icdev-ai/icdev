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

import json
import logging
import os
import uuid as _uuid
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint, abort, jsonify, redirect, render_template,
    request, session,
)

logger = logging.getLogger("icdev.boundary_canvas")

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
                if (request.is_json
                        or request.path.startswith("/boundary/api/")
                        or request.method in ("DELETE", "POST", "PUT")):
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
                    "INSERT INTO bd_audit (design_id, user, action, detail, created_at) "
                    "VALUES (?,?,?,?,?)",
                    (design_id, user_id, action, detail, now_isoformat()),
                )
        except Exception:
            pass

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
            designs = [_row_to_dict(r) for r in conn.execute(
                "SELECT id, name, description, classification, "
                "created_at, updated_at "
                "FROM boundary_designs ORDER BY updated_at DESC"
            ).fetchall()]
            recent_assessments = [_row_to_dict(r) for r in conn.execute(
                "SELECT a.id, a.design_id, a.assessment_type, a.score, "
                "a.grade, a.created_at, d.name AS design_name "
                "FROM bd_assessments a "
                "JOIN boundary_designs d ON a.design_id = d.id "
                "ORDER BY a.created_at DESC LIMIT 10"
            ).fetchall()]
            templates = [_row_to_dict(r) for r in conn.execute(
                "SELECT id, name, category, description, tags "
                "FROM bd_templates ORDER BY category, name"
            ).fetchall()]
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
            design = _row_to_dict(conn.execute(
                "SELECT * FROM boundary_designs WHERE id=?", (design_id,)
            ).fetchone())
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
                    "SELECT name, graph_json FROM bd_templates WHERE id=?",
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
            templates = [_row_to_dict(r) for r in conn.execute(
                "SELECT id, name, category, description, tags "
                "FROM bd_templates ORDER BY category, name"
            ).fetchall()]
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
            design = _row_to_dict(conn.execute(
                "SELECT id, name FROM boundary_designs WHERE id=?", (design_id,)
            ).fetchone())
        if not design:
            return redirect("/boundary/")
        return render_template("boundary_canvas/pps_matrix.html", design=design)

    @bp.route("/compliance/<design_id>")
    @bdc_login_required
    def bdc_compliance_page(design_id):
        """Compliance view page for a specific boundary design."""
        with get_connection() as conn:
            design = _row_to_dict(conn.execute(
                "SELECT id, name FROM boundary_designs WHERE id=?", (design_id,)
            ).fetchone())
        if not design:
            return redirect("/boundary/")
        return render_template("boundary_canvas/compliance.html", design=design)

    @bp.route("/remediation/<design_id>")
    @bdc_login_required
    def bdc_remediation_page(design_id):
        """Remediation page — gap analysis with recommended fixes."""
        with get_connection() as conn:
            design = _row_to_dict(conn.execute(
                "SELECT id, name FROM boundary_designs WHERE id=?", (design_id,)
            ).fetchone())
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
        design_id = str(_uuid.uuid4())
        name = data.get("name", "Untitled Boundary Design")
        now = now_isoformat()
        default_graph = {"nodes": [], "edges": [], "boundaries": []}
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO boundary_designs "
                "(id, name, description, graph_json, template_id, "
                "classification, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (design_id, name, data.get("description", ""),
                 data.get("graph_json") if isinstance(data.get("graph_json"), str)
                 else json.dumps(data.get("graph_json", default_graph)),
                 data.get("template_id"),
                 data.get("classification", "CUI"), now, now),
            )
        _audit(design_id, "CREATE", name)
        return jsonify({"id": design_id, "name": name}), 201

    @bp.route("/api/designs/<design_id>", methods=["GET"])
    @bdc_login_required
    def bdc_api_get_design(design_id):
        """Get a single boundary design."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM boundary_designs WHERE id=?", (design_id,)
            ).fetchone()
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
            existing = conn.execute(
                "SELECT id FROM boundary_designs WHERE id=?", (design_id,)
            ).fetchone()
            if not existing:
                return jsonify({"error": "Not found"}), 404
            updates = []
            params = []
            for key in ("name", "description", "classification"):
                if key in data:
                    updates.append(f"{key}=?")
                    params.append(data[key])
            if "graph_json" in data:
                updates.append("graph_json=?")
                val = data["graph_json"]
                params.append(
                    json.dumps(val) if isinstance(val, (dict, list)) else val
                )
            updates.append("updated_at=?")
            params.append(now)
            params.append(design_id)
            conn.execute(
                f"UPDATE boundary_designs SET {', '.join(updates)} WHERE id=?",  # noqa: S608
                params,
            )
        _audit(design_id, "UPDATE", json.dumps(list(data.keys())))

        # Cross-canvas trigger: validate boundary vs NDC enclaves, check ISA
        try:
            from tools.security_canvas.agent import on_bdc_design_saved
            on_bdc_design_saved(design_id)
        except Exception:
            pass

        return jsonify({"id": design_id, "updated_at": now})

    @bp.route("/api/designs/<design_id>", methods=["DELETE"])
    @bdc_login_required
    def bdc_api_delete_design(design_id):
        """Delete a boundary design and all related records."""
        child_tables = ("bd_isa_tracker", "bd_assessments")
        with get_connection() as conn:
            for table in child_tables:
                conn.execute(
                    f"DELETE FROM {table} WHERE design_id=?", (design_id,)  # noqa: S608
                )
            conn.execute(
                "DELETE FROM boundary_designs WHERE id=?", (design_id,)
            )
        _audit(design_id, "DELETE", "")
        return jsonify({"deleted": design_id})

    # ====================================================================
    # API ROUTES — Assessment
    # ====================================================================

    @bp.route("/api/designs/<design_id>/assess", methods=["POST"])
    @bdc_login_required
    def bdc_api_assess(design_id):
        """Run a full boundary compliance assessment on a design."""
        data = request.get_json(force=True, silent=True) or {}
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM boundary_designs WHERE id=?",
                (design_id,),
            ).fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404
            try:
                graph = (
                    json.loads(row[0]) if isinstance(row[0], str) else row[0]
                )
            except Exception:
                return jsonify({"error": "Bad graph data"}), 500

            # Fetch ISA tracker entries for this design
            isa_rows = conn.execute(
                "SELECT * FROM bd_isa_tracker WHERE design_id=?",
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
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (assess_id, design_id, assessment_type,
                 json.dumps(result.get("findings", [])),
                 result.get("score", 0),
                 result.get("grade", "N/A"),
                 result.get("cat1_findings", 0),
                 result.get("cat2_findings", 0),
                 result.get("cat3_findings", 0),
                 json.dumps(result.get("nist_coverage", {})),
                 now),
            )

        _audit(design_id, "ASSESS", f"score={result.get('score', 0)} grade={result.get('grade')}")

        # Add gap analysis
        gaps = detect_boundary_gaps(result)
        result["assessment_id"] = assess_id
        result["gap_analysis"] = gaps
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
                "SELECT id, name, category, description, tags "
                "FROM bd_templates ORDER BY category, name"
            ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/snippets", methods=["GET"])
    @bdc_login_required
    def bdc_api_list_snippets():
        """List available BDC snippets (reusable graph fragments)."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, name, category, description, graph_json, tags "
                "FROM bd_snippets ORDER BY category, name"
            ).fetchall()
        return jsonify({"snippets": [_row_to_dict(r) for r in rows]})

    @bp.route("/api/templates/<template_id>", methods=["GET"])
    @bdc_login_required
    def bdc_api_get_template(template_id):
        """Get a single template with full graph JSON."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM bd_templates WHERE id=?", (template_id,)
            ).fetchone()
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
                "SELECT graph_json FROM boundary_designs WHERE id=?",
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
                "SELECT graph_json FROM boundary_designs WHERE id=?",
                (design_id,),
            ).fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404
            try:
                graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            except Exception:
                return jsonify({"error": "Bad graph data"}), 500
            isa_rows = conn.execute(
                "SELECT * FROM bd_isa_tracker WHERE design_id=?",
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
            design = conn.execute(
                "SELECT id FROM boundary_designs WHERE id=?", (design_id,)
            ).fetchone()
            if not design:
                return jsonify({"error": "Design not found"}), 404

            existing = conn.execute(
                "SELECT id FROM bd_isa_tracker WHERE design_id=? AND interconnection_id=?",
                (design_id, interconnection_id),
            ).fetchone()

            status = data.get("status", "draft")
            if status not in ISA_LIFECYCLE_STATES:
                return jsonify({"error": f"Invalid status. Must be one of: {ISA_LIFECYCLE_STATES}"}), 400

            if existing:
                conn.execute(
                    "UPDATE bd_isa_tracker SET status=?, expiry_date=?, review_date=?, "
                    "owner=?, isa_doc_id=?, notes=?, updated_at=? "
                    "WHERE design_id=? AND interconnection_id=?",
                    (status, data.get("expiry_date"), data.get("review_date"),
                     data.get("owner", ""), data.get("isa_doc_id"),
                     data.get("notes", ""), now,
                     design_id, interconnection_id),
                )
                _audit(design_id, "ISA_UPDATE", f"{interconnection_id} -> {status}")
                return jsonify({"updated": interconnection_id, "status": status})
            else:
                tracker_id = str(_uuid.uuid4())
                conn.execute(
                    "INSERT INTO bd_isa_tracker "
                    "(id, design_id, interconnection_id, isa_doc_id, status, "
                    "expiry_date, review_date, owner, notes, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (tracker_id, design_id, interconnection_id,
                     data.get("isa_doc_id"), status,
                     data.get("expiry_date"), data.get("review_date"),
                     data.get("owner", ""), data.get("notes", ""),
                     now, now),
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
                "FROM bd_versions WHERE design_id=? ORDER BY version_number DESC",
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
                "SELECT graph_json FROM boundary_designs WHERE id=?", (design_id,),
            ).fetchone()
            if not row:
                return jsonify({"error": "Design not found"}), 404
            raw = _row_to_dict(row)["graph_json"]
            try:
                current_graph = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                current_graph = {}
            ver_num = conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) + 1 FROM bd_versions WHERE design_id=?",
                (design_id,),
            ).fetchone()[0]
            change_summary = data.get("change_summary", "")
            if not change_summary:
                prev = conn.execute(
                    "SELECT graph_json FROM bd_versions WHERE design_id=? ORDER BY version_number DESC LIMIT 1",
                    (design_id,),
                ).fetchone()
                if prev:
                    try:
                        prev_graph = json.loads(prev[0]) if isinstance(prev[0], str) else prev[0]
                        change_summary = _bdc_diff_graph(prev_graph, current_graph)
                    except Exception:
                        pass
            ver_id = str(_uuid.uuid4())
            now = now_isoformat()
            conn.execute(
                "INSERT INTO bd_versions (id, design_id, version_number, graph_json, change_summary, user_id, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (ver_id, design_id, ver_num,
                 json.dumps(current_graph) if isinstance(current_graph, dict) else str(raw),
                 change_summary, session.get("user_id", ""), now),
            )
        _audit(design_id, "VERSION_CREATE", f"v{ver_num}")
        return jsonify({"id": ver_id, "version_number": ver_num, "change_summary": change_summary, "created_at": now}), 201

    @bp.route("/api/versions/<design_id>/restore/<version_id>", methods=["POST"])
    @bdc_login_required
    def bdc_api_restore_version(design_id, version_id):
        """Restore a boundary design to a previous version snapshot."""
        with get_connection() as conn:
            ver = conn.execute(
                "SELECT graph_json, version_number FROM bd_versions WHERE id=? AND design_id=?",
                (version_id, design_id),
            ).fetchone()
            if not ver:
                return jsonify({"error": "Version not found"}), 404
            now = now_isoformat()
            conn.execute(
                "UPDATE boundary_designs SET graph_json=?, updated_at=? WHERE id=?",
                (ver[0], now, design_id),
            )
        _audit(design_id, "VERSION_RESTORE", f"restored to v{ver[1]}")
        return jsonify({"id": design_id, "restored_version": version_id,
                        "version_number": ver[1], "updated_at": now})

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
                "SELECT graph_json, version_number FROM bd_versions WHERE id=? AND design_id=?",
                (ver_a_id, design_id),
            ).fetchone()
            ver_b = conn.execute(
                "SELECT graph_json, version_number FROM bd_versions WHERE id=? AND design_id=?",
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
        return jsonify({
            "version_a": {"id": ver_a_id, "version_number": ver_a[1]},
            "version_b": {"id": ver_b_id, "version_number": ver_b[1]},
            "summary": summary,
        })

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
                "SELECT name, graph_json FROM boundary_designs WHERE id=?",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = _row_to_dict(row)
        gj = d["graph_json"]
        graph_data = json.loads(gj) if isinstance(gj, str) else gj
        from tools.network.visio_export import export_vsdx
        vsdx_bytes = export_vsdx(d["name"], graph_data)
        return jsonify({
            "format": "vsdx",
            "filename": d["name"].replace(" ", "_"),
            "data": base64.b64encode(vsdx_bytes).decode("ascii"),
        })

    def _bdc_fetch(design_id):
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name, graph_json FROM boundary_designs WHERE id=?", (design_id,)
            ).fetchone()
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

    return bp
