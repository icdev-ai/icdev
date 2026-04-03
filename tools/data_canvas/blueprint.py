# CUI // SP-CTI
"""ICDEV™ Data Design Canvas — Flask Blueprint integration.

Fully self-contained Blueprint mounted at /data/ inside the ICDEV dashboard.
Uses ICDEV's auth system, separate data_canvas.db, and feature flag
ICDEV_DATA_CANVAS_ENABLED.

Usage in ICDEV dashboard app.py:
    from tools.data_canvas.blueprint import create_data_canvas_blueprint
    bp = create_data_canvas_blueprint()
    if bp:
        app.register_blueprint(bp, url_prefix="/data")
"""

import json
import logging
import os
import uuid as _uuid
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint, jsonify, redirect, render_template,
    request, session,
)

logger = logging.getLogger("icdev.data_canvas")

_DC_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _DC_DIR.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"

# ── Import data canvas modules ───────────────────────────────────────────────
from tools.data_canvas.constants import (  # noqa: E402
    DATA_OBJECTS,
    DATA_CLASSIFICATION_LEVELS,
    DATA_COMPLIANCE_RULES,
)  # DATA_NIST_FAMILIES available via data_engine
from tools.data_canvas.data_engine import (  # noqa: E402
    assess_data_design,
    compute_classification_coverage,
    detect_data_gaps,
    compute_nist_coverage,
)
from tools.data_canvas.db.init_db import get_connection, init_db  # noqa: E402
from tools.common.helpers import row_to_dict, now_isoformat  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────


def _audit(design_id, user, action, detail="", classification="CUI // SP-CTI"):
    """Write an audit log entry."""
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO dd_audit (design_id, user, action, detail, classification, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (design_id, user, action, detail, classification, now_isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("DDC audit write failed: %s", exc)


# ── Blueprint Factory ────────────────────────────────────────────────────────

def create_data_canvas_blueprint():
    """Create and return the Data Design Canvas Blueprint.

    Returns None if ICDEV_DATA_CANVAS_ENABLED is false.
    """
    enabled = os.environ.get("ICDEV_DATA_CANVAS_ENABLED", "true").lower()
    if enabled not in ("true", "1", "yes"):
        logger.info("Data Canvas disabled (ICDEV_DATA_CANVAS_ENABLED=%s)", enabled)
        return None

    # Initialize DB
    try:
        init_db()
    except Exception as exc:
        logger.warning("Data Canvas DB init failed: %s", exc)

    bp = Blueprint(
        "data_canvas",
        __name__,
        template_folder=str(_TEMPLATE_DIR),
    )

    # ── Auth decorator ────────────────────────────────────────────────────
    def dc_login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("user_id"):
                if (request.is_json
                        or request.path.startswith("/data/api/")
                        or request.method in ("DELETE", "POST", "PUT")):
                    return jsonify({"error": "Authentication required"}), 401
                return redirect("/login")
            return f(*args, **kwargs)
        return decorated

    # ══════════════════════════════════════════════════════════════════════
    # PAGE ROUTES
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/")
    @dc_login_required
    def dc_index():
        conn = get_connection()
        designs = [row_to_dict(r) for r in conn.execute(
            "SELECT id, name, description, classification, created_at, updated_at "
            "FROM data_designs ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()]
        templates = [row_to_dict(r) for r in conn.execute(
            "SELECT id, name, category, description, tags "
            "FROM dd_templates ORDER BY category, name"
        ).fetchall()]
        conn.close()
        return render_template(
            "data_canvas/index.html",
            designs=designs,
            templates=templates,
            objects=DATA_OBJECTS,
            classification_levels=DATA_CLASSIFICATION_LEVELS,
        )

    @bp.route("/canvas/new")
    @dc_login_required
    def dc_new_canvas():
        template_id = request.args.get("template")
        graph_json = json.dumps({"nodes": [], "edges": [], "boundaries": []})
        name = "Untitled Data Design"
        if template_id:
            conn = get_connection()
            tpl = conn.execute(
                "SELECT name, graph_json FROM dd_templates WHERE id=?",
                (template_id,),
            ).fetchone()
            conn.close()
            if tpl:
                tpl = row_to_dict(tpl)
                graph_json = tpl["graph_json"]
                name = f"{tpl['name']} (copy)"
        return render_template(
            "data_canvas/canvas.html",
            design_id="new",
            design_name=name,
            graph_json=graph_json,
            objects=DATA_OBJECTS,
            classification_levels=DATA_CLASSIFICATION_LEVELS,
        )

    @bp.route("/canvas/<design_id>")
    @dc_login_required
    def dc_edit_canvas(design_id):
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM data_designs WHERE id=?", (design_id,)
        ).fetchone()
        conn.close()
        if not row:
            return redirect("/data/canvas/new")
        design = row_to_dict(row)
        return render_template(
            "data_canvas/canvas.html",
            design_id=design["id"],
            design_name=design["name"],
            graph_json=design["graph_json"],
            objects=DATA_OBJECTS,
            classification_levels=DATA_CLASSIFICATION_LEVELS,
        )

    @bp.route("/templates")
    @dc_login_required
    def dc_templates():
        """Template gallery page."""
        conn = get_connection()
        templates = [row_to_dict(r) for r in conn.execute(
            "SELECT id, name, category, description, tags "
            "FROM dd_templates ORDER BY category, name"
        ).fetchall()]
        conn.close()
        return render_template("data_canvas/templates.html", templates=templates)

    @bp.route("/assessments")
    @dc_login_required
    def dc_assessments():
        """Assessment history page."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT a.id, a.design_id, a.assessment_type, a.score, "
            "a.created_at, d.name AS design_name "
            "FROM dd_assessments a "
            "LEFT JOIN data_designs d ON a.design_id = d.id "
            "ORDER BY a.created_at DESC LIMIT 50"
        ).fetchall()
        assessments = [row_to_dict(r) for r in rows]
        conn.close()
        return render_template("data_canvas/assessments.html", assessments=assessments)

    @bp.route("/remediation/<design_id>")
    @dc_login_required
    def dc_remediation_page(design_id):
        """Remediation page — gap analysis with recommended fixes."""
        conn = get_connection()
        row = conn.execute(
            "SELECT id, name FROM data_designs WHERE id=?", (design_id,)
        ).fetchone()
        conn.close()
        if not row:
            return redirect("/data/")
        design = row_to_dict(row)
        return render_template("data_canvas/remediation.html", design=design)

    # ══════════════════════════════════════════════════════════════════════
    # API — DESIGN CRUD
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/health")
    def dc_health():
        return jsonify({"status": "ok", "module": "data_canvas"})

    @bp.route("/api/designs", methods=["GET"])
    @dc_login_required
    def dc_api_list():
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, description, classification, created_at, updated_at "
            "FROM data_designs ORDER BY updated_at DESC"
        ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    @bp.route("/api/designs", methods=["POST"])
    @dc_login_required
    def dc_api_create():
        data = request.get_json(force=True, silent=True) or {}
        if len(json.dumps(data)) > 5_000_000:
            return jsonify({"error": "Payload too large"}), 413
        design_id = str(_uuid.uuid4())
        name = data.get("name", "Untitled Data Design")[:200]
        classification = data.get("classification", "CUI")
        graph_json = data.get("graph_json", '{"nodes":[],"edges":[],"boundaries":[]}')
        template_id = data.get("template_id", None)
        logger.info("Creating data design: %s (%s)", name, design_id)
        conn = get_connection()
        conn.execute(
            "INSERT INTO data_designs "
            "(id, name, description, graph_json, template_id, classification, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (design_id, name, data.get("description", ""),
             graph_json, template_id, classification, now_isoformat(), now_isoformat()),
        )
        conn.commit()
        conn.close()
        _audit(design_id, session.get("user_id", "system"), "CREATE", name)
        return jsonify({"id": design_id, "name": name}), 201

    @bp.route("/api/designs/<design_id>", methods=["GET"])
    @dc_login_required
    def dc_api_get(design_id):
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM data_designs WHERE id=?", (design_id,)
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify(row_to_dict(row))

    @bp.route("/api/designs/<design_id>", methods=["PUT"])
    @dc_login_required
    def dc_api_update(design_id):
        data = request.get_json(force=True, silent=True) or {}
        if len(json.dumps(data)) > 5_000_000:
            return jsonify({"error": "Payload too large"}), 413
        logger.info("Updating data design: %s", design_id)
        conn = get_connection()
        conn.execute(
            "UPDATE data_designs SET name=?, description=?, graph_json=?, "
            "classification=?, updated_at=? WHERE id=?",
            (data.get("name", ""), data.get("description", ""),
             data.get("graph_json", "{}"),
             data.get("classification", "CUI"), now_isoformat(), design_id),
        )
        conn.commit()
        conn.close()
        _audit(design_id, session.get("user_id", "system"), "UPDATE", data.get("name", ""))

        # Cross-canvas trigger: auto-classify data flows, detect CUI/PII threats
        try:
            from tools.security_canvas.agent import on_ddc_design_saved
            on_ddc_design_saved(design_id)
        except Exception:
            pass

        return jsonify({"updated": True})

    @bp.route("/api/designs/<design_id>", methods=["DELETE"])
    @dc_login_required
    def dc_api_delete(design_id):
        logger.info("Deleting data design: %s", design_id)
        conn = get_connection()
        conn.execute("DELETE FROM data_designs WHERE id=?", (design_id,))
        conn.commit()
        conn.close()
        _audit(design_id, session.get("user_id", "system"), "DELETE", "")
        return jsonify({"deleted": True})

    # ══════════════════════════════════════════════════════════════════════
    # API — TEMPLATES
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/templates", methods=["GET"])
    @dc_login_required
    def dc_api_list_templates():
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, category, description, graph_json, tags "
            "FROM dd_templates ORDER BY category, name"
        ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    # ══════════════════════════════════════════════════════════════════════
    # API — SNIPPETS
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/snippets", methods=["GET"])
    @dc_login_required
    def dc_api_list_snippets():
        """List available DDC snippets (reusable graph fragments)."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, category, description, graph_json, tags "
            "FROM dd_snippets ORDER BY category, name"
        ).fetchall()
        conn.close()
        return jsonify({"snippets": [row_to_dict(r) for r in rows]})

    # ══════════════════════════════════════════════════════════════════════
    # API — ASSESSMENT
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/designs/<design_id>/assess", methods=["POST"])
    @dc_login_required
    def dc_api_assess(design_id):
        """Run compliance assessment on a data design."""
        conn = get_connection()
        row = conn.execute(
            "SELECT graph_json FROM data_designs WHERE id=?", (design_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404

        graph_raw = row["graph_json"]
        try:
            graph_data = json.loads(graph_raw) if isinstance(graph_raw, str) else graph_raw
        except (json.JSONDecodeError, TypeError):
            conn.close()
            return jsonify({"error": "Invalid graph data"}), 400

        # Run assessment
        result = assess_data_design(design_id, graph_data)

        # Compute additional metrics
        classification_cov = compute_classification_coverage(graph_data)
        nist_cov = compute_nist_coverage(graph_data)
        gaps = detect_data_gaps(result)

        # Persist assessment
        assess_id = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO dd_assessments (id, design_id, assessment_type, findings_json, score, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (assess_id, design_id, "compliance",
             json.dumps(result["findings"]), result["risk_score"], now_isoformat()),
        )
        conn.commit()
        conn.close()

        _audit(design_id, session.get("user_id", "system"), "ASSESS",
               f"score={result['risk_score']} grade={result['posture_grade']}")

        return jsonify({
            "assessment_id": assess_id,
            "assessment": result,
            "classification_coverage": classification_cov,
            "nist_coverage": nist_cov,
            "gaps": gaps,
        })

    @bp.route("/api/designs/<design_id>/assessments", methods=["GET"])
    @dc_login_required
    def dc_api_list_assessments(design_id):
        """List previous assessments for a design."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, assessment_type, score, created_at "
            "FROM dd_assessments WHERE design_id=? ORDER BY created_at DESC",
            (design_id,),
        ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    # ══════════════════════════════════════════════════════════════════════
    # API — CONSTANTS (for frontend)
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/objects")
    def dc_api_objects():
        """Return data object palette for the canvas frontend."""
        return jsonify(DATA_OBJECTS)

    @bp.route("/api/classification-levels")
    def dc_api_classification_levels():
        return jsonify(DATA_CLASSIFICATION_LEVELS)

    @bp.route("/api/rules")
    def dc_api_rules():
        return jsonify(DATA_COMPLIANCE_RULES)

    @bp.route("/api/export/<design_id>/vsdx", methods=["POST"])
    @dc_login_required
    def dc_api_export_vsdx(design_id):
        """Export data design as Visio .vsdx file."""
        import base64
        conn = get_connection()
        row = conn.execute(
            "SELECT name, graph_json FROM data_designs WHERE id=?",
            (design_id,),
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = row_to_dict(row)
        gj = d["graph_json"]
        graph = json.loads(gj) if isinstance(gj, str) else gj
        from tools.network.visio_export import export_vsdx
        vsdx_bytes = export_vsdx(d["name"], graph)
        return jsonify({
            "format": "vsdx",
            "filename": d["name"].replace(" ", "_"),
            "data": base64.b64encode(vsdx_bytes).decode("ascii"),
        })

    return bp
