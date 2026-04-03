# CUI // SP-CTI
"""ICDEV Observability Design Canvas — Flask Blueprint integration.

Fully self-contained Blueprint mounted at /observability/ inside the ICDEV dashboard.
Uses ICDEV's auth system, separate observability_canvas.db, and feature flag
ICDEV_OBSERVABILITY_ENABLED.

Usage in ICDEV dashboard app.py:
    from tools.observability_canvas.blueprint import create_observability_blueprint
    bp = create_observability_blueprint()
    if bp:
        app.register_blueprint(bp, url_prefix="/observability")
"""

import json
import logging
import os
import uuid as _uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint, jsonify, redirect, render_template,
    request, session,
)

logger = logging.getLogger("icdev.observability_canvas")

# ── Paths ──────────────────────────────────────────────────────────────────────
_OC_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _OC_DIR.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"
_CONFIG_PATH = _ICDEV_ROOT / "args" / "observability_canvas_config.yaml"

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def _load_config() -> dict:
    """Load ODC config from args/observability_canvas_config.yaml."""
    if not _HAS_YAML or not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
        return _yaml.safe_load(_f) or {}


_ODC_CONFIG = _load_config()

# ── Import helper modules ──────────────────────────────────────────────────────
from tools.observability_canvas.constants import (  # noqa: E402
    OBSERVABILITY_OBJECTS,
    OBSERVABILITY_COMPLIANCE_RULES,
)
from tools.observability_canvas.observability_engine import (  # noqa: E402
    assess_observability_design,
    compute_coverage_score,
    compute_mitre_detection_coverage,
    detect_observability_gaps,
)


def create_observability_blueprint():
    """Create and return the Observability Design Canvas Blueprint.

    Returns None if ICDEV_OBSERVABILITY_ENABLED is false.
    """
    enabled = os.environ.get("ICDEV_OBSERVABILITY_ENABLED", "true").lower()
    if enabled not in ("true", "1", "yes"):
        logger.info("Observability Canvas disabled (ICDEV_OBSERVABILITY_ENABLED=%s)", enabled)
        return None

    # Initialize DB
    try:
        from tools.observability_canvas.db.init_db import init_db
        init_db()
    except Exception as exc:
        logger.warning("Observability Canvas DB init failed: %s", exc)

    bp = Blueprint(
        "observability_canvas",
        __name__,
        template_folder=str(_TEMPLATE_DIR),
    )

    # ── Auth wrapper (uses ICDEV dashboard session) ────────────────────────
    def oc_login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("user_id"):
                if (request.is_json
                        or request.path.startswith("/observability/api/")
                        or request.method in ("DELETE", "POST", "PUT")):
                    return jsonify({"error": "Authentication required"}), 401
                return redirect("/login")
            return f(*args, **kwargs)
        return decorated

    # ── DB helpers ─────────────────────────────────────────────────────────
    from tools.observability_canvas.db.init_db import get_connection

    def _now():
        return datetime.now(timezone.utc).isoformat()

    def _audit(action, design_id="", details=""):
        user_id = ""
        try:
            user_id = session.get("user_id", "")
        except RuntimeError:
            pass
        try:
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO od_audit (design_id, user, action, detail, created_at) "
                    "VALUES (?,?,?,?,?)",
                    (design_id, user_id, action, details, _now()),
                )
        except Exception:
            pass

    def _row_to_dict(row):
        return dict(row) if row else {}

    # ====================================================================
    # PAGE ROUTES
    # ====================================================================

    @bp.route("/")
    @oc_login_required
    def oc_index():
        """Observability Design Canvas dashboard — list designs + recent assessments."""
        with get_connection() as conn:
            designs = [_row_to_dict(r) for r in conn.execute(
                "SELECT id, name, description, classification, "
                "created_at, updated_at "
                "FROM observability_designs ORDER BY updated_at DESC"
            ).fetchall()]
            recent_assessments = [_row_to_dict(r) for r in conn.execute(
                "SELECT a.id, a.design_id, a.assessment_type, a.score, "
                "a.grade, a.created_at, d.name AS design_name "
                "FROM od_assessments a "
                "JOIN observability_designs d ON a.design_id = d.id "
                "ORDER BY a.created_at DESC LIMIT 10"
            ).fetchall()]
            templates = [_row_to_dict(r) for r in conn.execute(
                "SELECT id, name, category, description, tags "
                "FROM od_templates ORDER BY category, name"
            ).fetchall()]
        return render_template(
            "observability_canvas/index.html",
            designs=designs,
            recent_assessments=recent_assessments,
            templates=templates,
            objects=OBSERVABILITY_OBJECTS,
        )

    @bp.route("/canvas/new")
    @oc_login_required
    def oc_new_canvas():
        """Create a new observability design and open the canvas."""
        template_id = request.args.get("template")
        graph = json.dumps({"nodes": [], "edges": []})
        name = "Untitled Observability Design"
        if template_id:
            with get_connection() as conn:
                tpl = conn.execute(
                    "SELECT name, graph_json FROM od_templates WHERE id=?",
                    (template_id,),
                ).fetchone()
            if tpl:
                tpl = _row_to_dict(tpl)
                graph = tpl["graph_json"]
                name = f"{tpl['name']} (copy)"
        return render_template(
            "observability_canvas/canvas.html",
            design_id="new",
            design_name=name,
            graph_json=graph,
            objects=OBSERVABILITY_OBJECTS,
            rules=OBSERVABILITY_COMPLIANCE_RULES,
        )

    @bp.route("/canvas/<design_id>")
    @oc_login_required
    def oc_canvas(design_id):
        """Canvas editor for a specific observability design."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM observability_designs WHERE id=?", (design_id,)
            ).fetchone()
        if not row:
            return redirect("/observability/")
        design = _row_to_dict(row)
        return render_template(
            "observability_canvas/canvas.html",
            design_id=design["id"],
            design_name=design["name"],
            graph_json=design["graph_json"],
            objects=OBSERVABILITY_OBJECTS,
            rules=OBSERVABILITY_COMPLIANCE_RULES,
        )

    @bp.route("/templates")
    @oc_login_required
    def oc_templates():
        """Template gallery page."""
        with get_connection() as conn:
            templates = [_row_to_dict(r) for r in conn.execute(
                "SELECT id, name, category, description, tags "
                "FROM od_templates ORDER BY category, name"
            ).fetchall()]
        return render_template("observability_canvas/templates.html", templates=templates)

    @bp.route("/assessments")
    @oc_login_required
    def oc_assessments():
        """Assessment history page."""
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT a.id, a.design_id, a.assessment_type, a.score, "
                "a.grade, a.created_at, d.name AS design_name "
                "FROM od_assessments a "
                "LEFT JOIN observability_designs d ON a.design_id = d.id "
                "ORDER BY a.created_at DESC LIMIT 50"
            ).fetchall()
            assessments = [_row_to_dict(r) for r in rows]
        finally:
            conn.close()
        return render_template("observability_canvas/assessments.html", assessments=assessments)

    @bp.route("/coverage/<design_id>")
    @oc_login_required
    def oc_coverage_page(design_id):
        """Detection coverage page for a specific observability design."""
        with get_connection() as conn:
            design = _row_to_dict(conn.execute(
                "SELECT id, name FROM observability_designs WHERE id=?", (design_id,)
            ).fetchone())
        if not design:
            return redirect("/observability/")
        return render_template("observability_canvas/coverage.html", design=design)

    @bp.route("/remediation/<design_id>")
    @oc_login_required
    def oc_remediation_page(design_id):
        """Remediation page — gap analysis with recommended fixes."""
        with get_connection() as conn:
            design = _row_to_dict(conn.execute(
                "SELECT id, name FROM observability_designs WHERE id=?", (design_id,)
            ).fetchone())
        if not design:
            return redirect("/observability/")
        return render_template("observability_canvas/remediation.html", design=design)

    # ====================================================================
    # API — DESIGN CRUD
    # ====================================================================

    @bp.route("/api/health")
    def oc_health():
        return jsonify({"status": "ok", "module": "observability_canvas"})

    @bp.route("/api/designs", methods=["POST"])
    @oc_login_required
    def oc_api_create():
        """Create a new observability design."""
        data = request.get_json(force=True, silent=True) or {}
        if len(json.dumps(data)) > 5_000_000:
            return jsonify({"error": "Payload too large"}), 413
        design_id = str(_uuid.uuid4())
        name = data.get("name", "Untitled Observability Design")[:200]
        template_id = data.get("template_id", "")
        graph_json = data.get("graph_json", '{"nodes":[],"edges":[]}')

        # If template_id given and no graph_json, load from template
        if template_id and graph_json == '{"nodes":[],"edges":[]}':
            try:
                with get_connection() as conn:
                    tpl = conn.execute(
                        "SELECT graph_json FROM od_templates WHERE id=?", (template_id,)
                    ).fetchone()
                    if tpl:
                        graph_json = tpl["graph_json"]
            except Exception:
                pass

        logger.info("Creating observability design: %s (%s)", name, design_id)
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO observability_designs "
                "(id, name, description, graph_json, template_id, classification, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (design_id, name, data.get("description", ""),
                 graph_json, template_id,
                 data.get("classification", "CUI"), _now(), _now()),
            )
            conn.commit()
        finally:
            conn.close()
        _audit("CREATE", design_id, name)
        return jsonify({"id": design_id, "name": name}), 201

    @bp.route("/api/designs", methods=["GET"])
    @oc_login_required
    def oc_api_list():
        """List all observability designs."""
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, description, classification, template_id, "
                "created_at, updated_at FROM observability_designs ORDER BY updated_at DESC"
            ).fetchall()
        finally:
            conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/designs/<design_id>", methods=["GET"])
    @oc_login_required
    def oc_api_get(design_id):
        """Get a specific observability design."""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM observability_designs WHERE id=?", (design_id,)
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify(_row_to_dict(row))

    @bp.route("/api/designs/<design_id>", methods=["PUT"])
    @oc_login_required
    def oc_api_update(design_id):
        """Update an observability design."""
        data = request.get_json(force=True, silent=True) or {}
        if len(json.dumps(data)) > 5_000_000:
            return jsonify({"error": "Payload too large"}), 413
        logger.info("Updating observability design: %s", design_id)
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE observability_designs SET name=?, description=?, "
                "graph_json=?, classification=?, updated_at=? WHERE id=?",
                (data.get("name", ""), data.get("description", ""),
                 data.get("graph_json", "{}"),
                 data.get("classification", "CUI"), _now(), design_id),
            )
            conn.commit()
        finally:
            conn.close()
        _audit("UPDATE", design_id, data.get("name", ""))

        # Cross-canvas trigger: verify SIEM/logging controls match SDC requirements
        try:
            from tools.security_canvas.agent import on_odc_design_saved
            on_odc_design_saved(design_id)
        except Exception:
            pass

        return jsonify({"updated": True})

    @bp.route("/api/designs/<design_id>", methods=["DELETE"])
    @oc_login_required
    def oc_api_delete(design_id):
        """Delete an observability design."""
        logger.info("Deleting observability design: %s", design_id)
        conn = get_connection()
        try:
            conn.execute("DELETE FROM od_assessments WHERE design_id=?", (design_id,))
            conn.execute("DELETE FROM observability_designs WHERE id=?", (design_id,))
            conn.commit()
        finally:
            conn.close()
        _audit("DELETE", design_id, "")
        return jsonify({"deleted": True})

    # ====================================================================
    # API — ASSESSMENT
    # ====================================================================

    @bp.route("/api/designs/<design_id>/assess", methods=["POST"])
    @oc_login_required
    def oc_api_assess(design_id):
        """Run observability compliance assessment on a design."""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT graph_json FROM observability_designs WHERE id=?", (design_id,)
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return jsonify({"error": "Design not found"}), 404

        graph_raw = row["graph_json"]
        try:
            graph_data = json.loads(graph_raw) if isinstance(graph_raw, str) else graph_raw
        except (json.JSONDecodeError, TypeError):
            return jsonify({"error": "Invalid graph data"}), 400

        # Run assessment
        assessment = assess_observability_design(graph_data)
        coverage = compute_coverage_score(graph_data)
        mitre = compute_mitre_detection_coverage(graph_data)
        gaps = detect_observability_gaps(assessment)

        # Persist assessment
        assessment_id = assessment["assessment_id"]
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO od_assessments "
                "(id, design_id, assessment_type, findings_json, score, grade, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (assessment_id, design_id, assessment["assessment_type"],
                 json.dumps(assessment["findings"]),
                 assessment["score"], assessment["grade"], _now()),
            )
            conn.commit()
        finally:
            conn.close()

        _audit("ASSESS", design_id, f"Score: {assessment['score']}, Grade: {assessment['grade']}")

        return jsonify({
            "assessment": assessment,
            "coverage": coverage,
            "mitre_detection": mitre,
            "gaps": gaps,
        })

    # ====================================================================
    # API — TEMPLATES
    # ====================================================================

    @bp.route("/api/templates", methods=["GET"])
    @oc_login_required
    def oc_api_templates():
        """List all observability design templates."""
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, category, description, tags "
                "FROM od_templates ORDER BY category, name"
            ).fetchall()
        finally:
            conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/snippets", methods=["GET"])
    @oc_login_required
    def oc_api_snippets():
        """List available ODC snippets (reusable graph fragments)."""
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, category, description, graph_json, tags "
                "FROM od_snippets ORDER BY category, name"
            ).fetchall()
        finally:
            conn.close()
        return jsonify({"snippets": [_row_to_dict(r) for r in rows]})

    @bp.route("/api/templates/<template_id>", methods=["GET"])
    @oc_login_required
    def oc_api_get_template(template_id):
        """Get a specific template with full graph_json."""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM od_templates WHERE id=?", (template_id,)
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return jsonify({"error": "Template not found"}), 404
        return jsonify(_row_to_dict(row))

    # ====================================================================
    # API ROUTES — Design Versioning
    # ====================================================================

    def _odc_diff_graph(old: dict, new: dict) -> str:
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
    @oc_login_required
    def oc_api_list_versions(design_id):
        """List all version snapshots for an observability design."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, version_number, change_summary, user_id, created_at "
                "FROM od_versions WHERE design_id=? ORDER BY version_number DESC",
                (design_id,),
            ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/versions/<design_id>", methods=["POST"])
    @oc_login_required
    def oc_api_create_version(design_id):
        """Create a version snapshot of the current observability design state."""
        data = request.get_json(force=True, silent=True) or {}
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM observability_designs WHERE id=?", (design_id,),
            ).fetchone()
            if not row:
                return jsonify({"error": "Design not found"}), 404
            raw = _row_to_dict(row)["graph_json"]
            try:
                current_graph = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                current_graph = {}
            ver_num = conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) + 1 FROM od_versions WHERE design_id=?",
                (design_id,),
            ).fetchone()[0]
            change_summary = data.get("change_summary", "")
            if not change_summary:
                prev = conn.execute(
                    "SELECT graph_json FROM od_versions WHERE design_id=? ORDER BY version_number DESC LIMIT 1",
                    (design_id,),
                ).fetchone()
                if prev:
                    try:
                        prev_graph = json.loads(prev[0]) if isinstance(prev[0], str) else prev[0]
                        change_summary = _odc_diff_graph(prev_graph, current_graph)
                    except Exception:
                        pass
            ver_id = str(_uuid.uuid4())
            now = _now()
            conn.execute(
                "INSERT INTO od_versions (id, design_id, version_number, graph_json, change_summary, user_id, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (ver_id, design_id, ver_num,
                 json.dumps(current_graph) if isinstance(current_graph, dict) else str(raw),
                 change_summary, session.get("user_id", ""), now),
            )
        _audit("VERSION_CREATE", design_id, f"v{ver_num}")
        return jsonify({"id": ver_id, "version_number": ver_num, "change_summary": change_summary, "created_at": now}), 201

    @bp.route("/api/versions/<design_id>/restore/<version_id>", methods=["POST"])
    @oc_login_required
    def oc_api_restore_version(design_id, version_id):
        """Restore an observability design to a previous version snapshot."""
        with get_connection() as conn:
            ver = conn.execute(
                "SELECT graph_json, version_number FROM od_versions WHERE id=? AND design_id=?",
                (version_id, design_id),
            ).fetchone()
            if not ver:
                return jsonify({"error": "Version not found"}), 404
            now = _now()
            conn.execute(
                "UPDATE observability_designs SET graph_json=?, updated_at=? WHERE id=?",
                (ver[0], now, design_id),
            )
        _audit("VERSION_RESTORE", design_id, f"restored to v{ver[1]}")
        return jsonify({"id": design_id, "restored_version": version_id,
                        "version_number": ver[1], "updated_at": now})

    @bp.route("/api/versions/<design_id>/diff", methods=["POST"])
    @oc_login_required
    def oc_api_diff_versions(design_id):
        """Compare two version snapshots of an observability design."""
        data = request.get_json(force=True, silent=True) or {}
        ver_a_id = data.get("version_a")
        ver_b_id = data.get("version_b")
        if not ver_a_id or not ver_b_id:
            return jsonify({"error": "version_a and version_b required"}), 400
        with get_connection() as conn:
            ver_a = conn.execute(
                "SELECT graph_json, version_number FROM od_versions WHERE id=? AND design_id=?",
                (ver_a_id, design_id),
            ).fetchone()
            ver_b = conn.execute(
                "SELECT graph_json, version_number FROM od_versions WHERE id=? AND design_id=?",
                (ver_b_id, design_id),
            ).fetchone()
        if not ver_a or not ver_b:
            return jsonify({"error": "One or both versions not found"}), 404
        try:
            graph_a = json.loads(ver_a[0]) if isinstance(ver_a[0], str) else ver_a[0]
            graph_b = json.loads(ver_b[0]) if isinstance(ver_b[0], str) else ver_b[0]
        except Exception:
            return jsonify({"error": "Failed to parse graph data"}), 500
        summary = _odc_diff_graph(graph_a, graph_b)
        return jsonify({
            "version_a": {"id": ver_a_id, "version_number": ver_a[1]},
            "version_b": {"id": ver_b_id, "version_number": ver_b[1]},
            "summary": summary,
        })

    # ====================================================================
    # API — OBJECTS PALETTE
    # ====================================================================

    @bp.route("/api/objects")
    def oc_api_objects():
        """Return the full observability object palette for the canvas UI."""
        return jsonify(OBSERVABILITY_OBJECTS)

    @bp.route("/api/rules")
    def oc_api_rules():
        """Return the observability compliance rules."""
        return jsonify(OBSERVABILITY_COMPLIANCE_RULES)

    @bp.route("/api/export/<design_id>/vsdx", methods=["POST"])
    @oc_login_required
    def oc_api_export_vsdx(design_id):
        """Export observability design as Visio .vsdx file."""
        import base64
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name, graph_json FROM observability_designs WHERE id=?",
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

    def _odc_fetch(design_id):
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name, graph_json FROM observability_designs WHERE id=?", (design_id,)
            ).fetchone()
        if not row:
            return None, None
        d = _row_to_dict(row)
        gj = d["graph_json"]
        graph = json.loads(gj) if isinstance(gj, str) else gj
        return d["name"], graph

    @bp.route("/api/export/<design_id>/json", methods=["POST"])
    @oc_login_required
    def oc_api_export_json(design_id):
        """Export observability design as JSON."""
        import base64
        name, graph = _odc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_json
        data = base64.b64encode(export_json(name, graph, "ODC")).decode("ascii")
        return jsonify({"format": "json", "filename": f"{name.replace(' ', '_')}.json", "data": data})

    @bp.route("/api/export/<design_id>/markdown", methods=["POST"])
    @oc_login_required
    def oc_api_export_markdown(design_id):
        """Export observability design as Markdown."""
        import base64
        name, graph = _odc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_markdown
        data = base64.b64encode(export_markdown(name, graph, "ODC")).decode("ascii")
        return jsonify({"format": "markdown", "filename": f"{name.replace(' ', '_')}.md", "data": data})

    @bp.route("/api/export/<design_id>/csv", methods=["POST"])
    @oc_login_required
    def oc_api_export_csv(design_id):
        """Export observability design node inventory as CSV."""
        import base64
        name, graph = _odc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_csv
        data = base64.b64encode(export_csv(name, graph, "ODC")).decode("ascii")
        return jsonify({"format": "csv", "filename": f"{name.replace(' ', '_')}.csv", "data": data})

    @bp.route("/api/export/<design_id>/drawio", methods=["POST"])
    @oc_login_required
    def oc_api_export_drawio(design_id):
        """Export observability design as DrawIO XML."""
        import base64
        name, graph = _odc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_drawio
        data = base64.b64encode(export_drawio(name, graph, "ODC")).decode("ascii")
        return jsonify({"format": "drawio", "filename": f"{name.replace(' ', '_')}.drawio", "data": data})

    @bp.route("/api/export/<design_id>/svg", methods=["POST"])
    @oc_login_required
    def oc_api_export_svg(design_id):
        """Export observability design as SVG vector graphic."""
        import base64
        name, graph = _odc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_svg
        data = base64.b64encode(export_svg(name, graph, "ODC")).decode("ascii")
        return jsonify({"format": "svg", "filename": f"{name.replace(' ', '_')}.svg", "data": data})

    return bp
