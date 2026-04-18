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
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)

logger = logging.getLogger("icdev.data_canvas")

_DC_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _DC_DIR.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"
_CONFIG_PATH = _ICDEV_ROOT / "args" / "data_canvas_config.yaml"

try:
    import yaml as _yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def _load_config() -> dict:
    """Load DDC config from args/data_canvas_config.yaml."""
    if not _HAS_YAML or not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
        return _yaml.safe_load(_f) or {}


_DDC_CONFIG = _load_config()

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
    build_column_lineage_dag,
)
from tools.data_canvas.lineage import generate_contract_assertions  # noqa: E402
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
                if (
                    request.is_json
                    or request.path.startswith("/data/api/")
                    or request.method in ("DELETE", "POST", "PUT")
                ):
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
        designs = [
            row_to_dict(r)
            for r in conn.execute(
                "SELECT id, name, description, classification, created_at, updated_at "
                "FROM data_designs ORDER BY updated_at DESC LIMIT 20"
            ).fetchall()
        ]
        templates = [
            row_to_dict(r)
            for r in conn.execute(
                "SELECT id, name, category, description, tags FROM dd_templates ORDER BY category, name"
            ).fetchall()
        ]
        sop_row = conn.execute("SELECT COUNT(*) AS cnt FROM ddc_sops").fetchone()
        sop_count = sop_row["cnt"] if sop_row else 0
        approved_sop_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM ddc_sops WHERE status='approved'"
        ).fetchone()
        approved_sop_count = approved_sop_row["cnt"] if approved_sop_row else 0
        conn.close()
        return render_template(
            "data_canvas/index.html",
            designs=designs,
            templates=templates,
            sop_count=sop_count,
            approved_sop_count=approved_sop_count,
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
        row = conn.execute("SELECT * FROM data_designs WHERE id=?", (design_id,)).fetchone()
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
        templates = [
            row_to_dict(r)
            for r in conn.execute(
                "SELECT id, name, category, description, tags FROM dd_templates ORDER BY category, name"
            ).fetchall()
        ]
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
        row = conn.execute("SELECT id, name FROM data_designs WHERE id=?", (design_id,)).fetchone()
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
            (
                design_id,
                name,
                data.get("description", ""),
                graph_json,
                template_id,
                classification,
                now_isoformat(),
                now_isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        _audit(design_id, session.get("user_id", "system"), "CREATE", name)
        return jsonify({"id": design_id, "name": name}), 201

    @bp.route("/api/designs/<design_id>", methods=["GET"])
    @dc_login_required
    def dc_api_get(design_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM data_designs WHERE id=?", (design_id,)).fetchone()
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
            "UPDATE data_designs SET name=?, description=?, graph_json=?, classification=?, updated_at=? WHERE id=?",
            (
                data.get("name", ""),
                data.get("description", ""),
                data.get("graph_json", "{}"),
                data.get("classification", "CUI"),
                now_isoformat(),
                design_id,
            ),
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

        # Incremental KG update: re-extract only if graph_json changed
        try:
            from tools.canvas.kg_builder import rebuild_canvas_kg

            rebuild_canvas_kg("ddc", design_id)
        except Exception:
            pass

        return jsonify({"updated": True})

    @bp.route("/api/designs/<design_id>", methods=["DELETE"])
    @dc_login_required
    def dc_api_delete(design_id):
        logger.info("Deleting data design: %s", design_id)
        conn = get_connection()
        # Cascade-delete child records before removing parent (foreign key constraint)
        conn.execute("DELETE FROM dd_versions WHERE design_id=?", (design_id,))
        conn.execute("DELETE FROM dd_assessments WHERE design_id=?", (design_id,))
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
            "SELECT id, name, category, description, graph_json, tags FROM dd_templates ORDER BY category, name"
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
            "SELECT id, name, category, description, graph_json, tags FROM dd_snippets ORDER BY category, name"
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
        row = conn.execute("SELECT graph_json FROM data_designs WHERE id=?", (design_id,)).fetchone()
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

        # PII/PHI detection — wire into findings
        pii_scan: dict = {}
        try:
            from tools.data_canvas.pii_detector import scan_graph as _scan_graph

            pii_scan = _scan_graph(graph_data)
            result["findings"].extend(pii_scan.get("compliance_findings", []))
        except Exception:
            pass

        # Persist assessment
        assess_id = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO dd_assessments (id, design_id, assessment_type, findings_json, score, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (assess_id, design_id, "compliance", json.dumps(result["findings"]), result["risk_score"], now_isoformat()),
        )
        conn.commit()
        conn.close()

        _audit(
            design_id,
            session.get("user_id", "system"),
            "ASSESS",
            f"score={result['risk_score']} grade={result['posture_grade']}",
        )

        return jsonify(
            {
                "assessment_id": assess_id,
                "assessment": result,
                "classification_coverage": classification_cov,
                "nist_coverage": nist_cov,
                "gaps": gaps,
                "pii_scan": pii_scan,
            }
        )

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

    # ====================================================================
    # API ROUTES — Data Lineage
    # ====================================================================

    @bp.route("/api/designs/<design_id>/lineage", methods=["GET"])
    @dc_login_required
    def dc_api_lineage_list(design_id):
        """List all lineage edges for a data design."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, source_node_id, target_node_id, lineage_type, "
            "column_name, transform_desc, classification, created_at "
            "FROM dd_lineage WHERE design_id=? ORDER BY created_at",
            (design_id,),
        ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    @bp.route("/api/designs/<design_id>/lineage", methods=["POST"])
    @dc_login_required
    def dc_api_lineage_create(design_id):
        """Add a column-level lineage edge to a data design."""
        data = request.get_json(force=True, silent=True) or {}
        source = data.get("source_node_id", "")
        target = data.get("target_node_id", "")
        if not source or not target:
            return jsonify({"error": "source_node_id and target_node_id required"}), 400
        edge_id = f"lin-{_uuid.uuid4().hex[:10]}"
        conn = get_connection()
        conn.execute(
            "INSERT INTO dd_lineage "
            "(id, design_id, source_node_id, target_node_id, lineage_type, "
            "column_name, transform_desc, classification, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                edge_id,
                design_id,
                source,
                target,
                data.get("lineage_type", "flow"),
                data.get("column_name", ""),
                data.get("transform_desc", ""),
                data.get("classification", "CUI"),
                now_isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        _audit(design_id, session.get("user_id", "system"), "LINEAGE_CREATE", f"source={source} target={target}")
        return jsonify({"id": edge_id, "status": "created"}), 201

    @bp.route("/api/designs/<design_id>/lineage/<edge_id>", methods=["DELETE"])
    @dc_login_required
    def dc_api_lineage_delete(design_id, edge_id):
        """Delete a lineage edge."""
        conn = get_connection()
        conn.execute("DELETE FROM dd_lineage WHERE id=? AND design_id=?", (edge_id, design_id))
        conn.commit()
        conn.close()
        return jsonify({"status": "deleted"})

    @bp.route("/api/designs/<design_id>/pii-scan", methods=["POST"])
    @dc_login_required
    def dc_api_pii_scan(design_id):
        """Run PII/PHI detection on the current graph nodes."""
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM data_designs WHERE id=?", (design_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row["graph_json"]) if isinstance(row["graph_json"], str) else row["graph_json"]
        except (json.JSONDecodeError, TypeError):
            return jsonify({"error": "Invalid graph data"}), 400
        from tools.data_canvas.pii_detector import scan_graph as _scan_graph

        result = _scan_graph(graph)
        _audit(
            design_id,
            session.get("user_id", "system"),
            "PII_SCAN",
            f"pii_nodes={result['pii_node_count']} high={result['high_count']}",
        )
        return jsonify(result)

    # ====================================================================
    # API ROUTES — Design Versioning
    # ====================================================================

    def _dc_diff_graph(old: dict, new: dict) -> str:
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
    @dc_login_required
    def dc_api_list_versions(design_id):
        """List all version snapshots for a data design."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, version_number, change_summary, user_id, created_at "
            "FROM dd_versions WHERE design_id=? ORDER BY version_number DESC",
            (design_id,),
        ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    @bp.route("/api/versions/<design_id>", methods=["POST"])
    @dc_login_required
    def dc_api_create_version(design_id):
        """Create a version snapshot of the current data design state."""
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        row = conn.execute(
            "SELECT graph_json FROM data_designs WHERE id=?",
            (design_id,),
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Design not found"}), 404
        raw = row_to_dict(row)["graph_json"]
        try:
            current_graph = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            current_graph = {}
        ver_num = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 FROM dd_versions WHERE design_id=?",
            (design_id,),
        ).fetchone()[0]
        change_summary = data.get("change_summary", "")
        if not change_summary:
            prev = conn.execute(
                "SELECT graph_json FROM dd_versions WHERE design_id=? ORDER BY version_number DESC LIMIT 1",
                (design_id,),
            ).fetchone()
            if prev:
                try:
                    prev_graph = json.loads(prev[0]) if isinstance(prev[0], str) else prev[0]
                    change_summary = _dc_diff_graph(prev_graph, current_graph)
                except Exception:
                    pass
        ver_id = str(_uuid.uuid4())
        now = now_isoformat()
        conn.execute(
            "INSERT INTO dd_versions (id, design_id, version_number, graph_json, change_summary, user_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
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
        conn.commit()
        conn.close()
        _audit(design_id, session.get("user_id", ""), "VERSION_CREATE", f"v{ver_num}")
        return jsonify(
            {"id": ver_id, "version_number": ver_num, "change_summary": change_summary, "created_at": now}
        ), 201

    @bp.route("/api/versions/<design_id>/restore/<version_id>", methods=["POST"])
    @dc_login_required
    def dc_api_restore_version(design_id, version_id):
        """Restore a data design to a previous version snapshot."""
        conn = get_connection()
        ver = conn.execute(
            "SELECT graph_json, version_number FROM dd_versions WHERE id=? AND design_id=?",
            (version_id, design_id),
        ).fetchone()
        if not ver:
            conn.close()
            return jsonify({"error": "Version not found"}), 404
        now = now_isoformat()
        conn.execute(
            "UPDATE data_designs SET graph_json=?, updated_at=? WHERE id=?",
            (ver[0], now, design_id),
        )
        conn.commit()
        ver_num = ver[1]
        conn.close()
        _audit(design_id, session.get("user_id", ""), "VERSION_RESTORE", f"restored to v{ver_num}")
        return jsonify({"id": design_id, "restored_version": version_id, "version_number": ver_num, "updated_at": now})

    @bp.route("/api/versions/<design_id>/diff", methods=["POST"])
    @dc_login_required
    def dc_api_diff_versions(design_id):
        """Compare two version snapshots of a data design."""
        data = request.get_json(force=True, silent=True) or {}
        ver_a_id = data.get("version_a")
        ver_b_id = data.get("version_b")
        if not ver_a_id or not ver_b_id:
            return jsonify({"error": "version_a and version_b required"}), 400
        conn = get_connection()
        ver_a = conn.execute(
            "SELECT graph_json, version_number FROM dd_versions WHERE id=? AND design_id=?",
            (ver_a_id, design_id),
        ).fetchone()
        ver_b = conn.execute(
            "SELECT graph_json, version_number FROM dd_versions WHERE id=? AND design_id=?",
            (ver_b_id, design_id),
        ).fetchone()
        conn.close()
        if not ver_a or not ver_b:
            return jsonify({"error": "One or both versions not found"}), 404
        try:
            graph_a = json.loads(ver_a[0]) if isinstance(ver_a[0], str) else ver_a[0]
            graph_b = json.loads(ver_b[0]) if isinstance(ver_b[0], str) else ver_b[0]
        except Exception:
            return jsonify({"error": "Failed to parse graph data"}), 500
        summary = _dc_diff_graph(graph_a, graph_b)
        return jsonify(
            {
                "version_a": {"id": ver_a_id, "version_number": ver_a[1]},
                "version_b": {"id": ver_b_id, "version_number": ver_b[1]},
                "summary": summary,
            }
        )

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
        return jsonify(
            {
                "format": "vsdx",
                "filename": d["name"].replace(" ", "_"),
                "data": base64.b64encode(vsdx_bytes).decode("ascii"),
            }
        )

    def _ddc_fetch(design_id):
        conn = get_connection()
        row = conn.execute("SELECT name, graph_json FROM data_designs WHERE id=?", (design_id,)).fetchone()
        conn.close()
        if not row:
            return None, None
        d = row_to_dict(row)
        gj = d["graph_json"]
        graph = json.loads(gj) if isinstance(gj, str) else gj
        return d["name"], graph

    @bp.route("/api/export/<design_id>/json", methods=["POST"])
    @dc_login_required
    def dc_api_export_json(design_id):
        """Export data design as JSON."""
        import base64

        name, graph = _ddc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_json

        data = base64.b64encode(export_json(name, graph, "DDC")).decode("ascii")
        return jsonify({"format": "json", "filename": f"{name.replace(' ', '_')}.json", "data": data})

    @bp.route("/api/export/<design_id>/markdown", methods=["POST"])
    @dc_login_required
    def dc_api_export_markdown(design_id):
        """Export data design as Markdown."""
        import base64

        name, graph = _ddc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_markdown

        data = base64.b64encode(export_markdown(name, graph, "DDC")).decode("ascii")
        return jsonify({"format": "markdown", "filename": f"{name.replace(' ', '_')}.md", "data": data})

    @bp.route("/api/export/<design_id>/csv", methods=["POST"])
    @dc_login_required
    def dc_api_export_csv(design_id):
        """Export data design node inventory as CSV."""
        import base64

        name, graph = _ddc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_csv

        data = base64.b64encode(export_csv(name, graph, "DDC")).decode("ascii")
        return jsonify({"format": "csv", "filename": f"{name.replace(' ', '_')}.csv", "data": data})

    @bp.route("/api/export/<design_id>/drawio", methods=["POST"])
    @dc_login_required
    def dc_api_export_drawio(design_id):
        """Export data design as DrawIO XML."""
        import base64

        name, graph = _ddc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_drawio

        data = base64.b64encode(export_drawio(name, graph, "DDC")).decode("ascii")
        return jsonify({"format": "drawio", "filename": f"{name.replace(' ', '_')}.drawio", "data": data})

    @bp.route("/api/export/<design_id>/svg", methods=["POST"])
    @dc_login_required
    def dc_api_export_svg(design_id):
        """Export data design as SVG vector graphic."""
        import base64

        name, graph = _ddc_fetch(design_id)
        if name is None:
            return jsonify({"error": "Not found"}), 404
        from tools.canvas.export_utils import export_svg

        data = base64.b64encode(export_svg(name, graph, "DDC")).decode("ascii")
        return jsonify({"format": "svg", "filename": f"{name.replace(' ', '_')}.svg", "data": data})

    @bp.route("/api/export/<design_id>/odcs", methods=["POST"])
    @dc_login_required
    def dc_api_export_odcs(design_id):
        """Export data design as Open Data Contract Standard (ODCS) v3 YAML."""
        import base64

        conn = get_connection()
        row = conn.execute(
            "SELECT name, graph_json, classification FROM data_designs WHERE id=?",
            (design_id,),
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = row_to_dict(row)
        gj = d["graph_json"]
        graph = json.loads(gj) if isinstance(gj, str) else gj
        from tools.data_canvas.exporters.odcs import export_odcs

        yaml_bytes = export_odcs(
            d["name"],
            graph,
            design_id=design_id,
            classification=d.get("classification") or "CUI",
        )
        data = base64.b64encode(yaml_bytes).decode("ascii")
        filename = f"{d['name'].replace(' ', '_')}_odcs.yaml"
        return jsonify({"format": "odcs", "filename": filename, "data": data})

    @bp.route("/api/export/<design_id>/odps", methods=["POST"])
    @dc_login_required
    def dc_api_export_odps(design_id):
        """Export data design as Open Data Product Standard (ODPS) v3 YAML."""
        import base64

        conn = get_connection()
        row = conn.execute(
            "SELECT name, graph_json, classification FROM data_designs WHERE id=?",
            (design_id,),
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = row_to_dict(row)
        gj = d["graph_json"]
        graph = json.loads(gj) if isinstance(gj, str) else gj
        from tools.data_canvas.exporters.odps import export_odps

        yaml_bytes = export_odps(
            d["name"],
            graph,
            design_id=design_id,
            classification=d.get("classification") or "CUI",
        )
        data = base64.b64encode(yaml_bytes).decode("ascii")
        filename = f"{d['name'].replace(' ', '_')}_odps.yaml"
        return jsonify({"format": "odps", "filename": filename, "data": data})

    @bp.route("/api/export/<design_id>/dbt", methods=["POST"])
    @dc_login_required
    def dc_api_export_dbt(design_id):
        """Export data design as dbt sources.yml + model.yml (ZIP archive)."""
        import base64
        import io
        import zipfile

        conn = get_connection()
        row = conn.execute(
            "SELECT name, graph_json, classification FROM data_designs WHERE id=?",
            (design_id,),
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = row_to_dict(row)
        gj = d["graph_json"]
        graph = json.loads(gj) if isinstance(gj, str) else gj
        from tools.data_canvas.exporters.dbt import export_dbt

        sources_bytes, model_bytes = export_dbt(
            d["name"],
            graph,
            design_id=design_id,
            classification=d.get("classification") or "CUI",
        )
        # Bundle both YAML files into a single ZIP for download
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("sources.yml", sources_bytes)
            zf.writestr("model.yml", model_bytes)
        zip_bytes = buf.getvalue()
        slug = d["name"].replace(" ", "_")
        return jsonify(
            {
                "format": "dbt",
                "filename": f"{slug}_dbt.zip",
                "data": base64.b64encode(zip_bytes).decode("ascii"),
                "files": ["sources.yml", "model.yml"],
            }
        )

    # ── Collaboration (Task 18) ───────────────────────────────────────────────
    import uuid as _uuid_mod
    from tools.canvas.collaboration import CanvasCollabManager as _DDCCollabMgr

    _ddc_collab = _DDCCollabMgr("dd")

    @bp.route("/api/collab/<design_id>/join", methods=["POST"])
    @dc_login_required
    def dc_collab_join(design_id):
        """Join a collaborative DDC editing session."""
        body = request.json or {}
        user_id = body.get("user_id", str(_uuid_mod.uuid4())[:8])
        user_name = body.get("user_name", "")
        return jsonify(_ddc_collab.join(design_id, user_id, user_name))

    @bp.route("/api/collab/<design_id>/leave", methods=["POST"])
    @dc_login_required
    def dc_collab_leave(design_id):
        """Leave a DDC collaborative session."""
        body = request.json or {}
        user_id = body.get("user_id", "")
        _ddc_collab.leave(design_id, user_id)
        return jsonify({"ok": True})

    @bp.route("/api/collab/<design_id>/push", methods=["POST"])
    @dc_login_required
    def dc_collab_push(design_id):
        """Push an operation into a DDC collaborative session."""
        body = request.json or {}
        user_id = body.get("user_id", "")
        op_type = body.get("op_type", "")
        data = body.get("data", {})
        seq = _ddc_collab.push(design_id, user_id, op_type, data)
        return jsonify({"seq": seq})

    @bp.route("/api/collab/<design_id>/poll", methods=["GET"])
    @dc_login_required
    def dc_collab_poll(design_id):
        """Poll for DDC collaborative operations since a sequence number."""
        since = int(request.args.get("since", 0))
        user_id = request.args.get("user_id", "")
        cx = request.args.get("cx")
        cy = request.args.get("cy")
        if user_id and cx is not None and cy is not None:
            _ddc_collab.update_cursor(design_id, user_id, float(cx), float(cy))
        ops, participants, latest_seq = _ddc_collab.poll(design_id, since)
        return jsonify({"operations": ops, "participants": participants, "latest_seq": latest_seq})

    @bp.route("/api/collab/<design_id>/participants", methods=["GET"])
    @dc_login_required
    def dc_collab_participants(design_id):
        """Return current participants in a DDC collaborative session."""
        return jsonify({"participants": _ddc_collab.get_participants(design_id)})

    # ====================================================================
    # API ROUTES — Schema Introspection (reverse-engineer live DB)
    # ====================================================================

    @bp.route("/api/introspect", methods=["POST"])
    @dc_login_required
    def dc_api_introspect():
        """Introspect a live database and return a Data Canvas graph.

        POST body (JSON):
            dsn         — DB connection string (required)
            db_type     — "auto"|"sqlite"|"postgresql"|"mysql"|"sqlserver"
            schema      — optional schema/database filter
            design_id   — if provided, merge result into this design
            design_name — if creating a new design, use this name

        Returns (200):
            {
              "nodes": [...],
              "edges": [...],
              "boundaries": [],
              "meta": {"db_type":..., "db_name":..., "table_count":...,
                        "column_count":..., "fk_count":...},
              "design_id": "<uuid|null>"
            }

        Returns (400) if dsn is missing.
        Returns (500) if introspection fails (driver not installed, auth error, etc.).
        """
        body = request.get_json(force=True, silent=True) or {}
        dsn = body.get("dsn", "").strip()
        if not dsn:
            return jsonify({"error": "dsn is required"}), 400

        db_type = body.get("db_type", "auto")
        schema_filter = body.get("schema") or None
        design_id_param = body.get("design_id") or None
        design_name = body.get("design_name") or None

        try:
            from tools.data_canvas.introspector import introspect as _ddc_introspect

            graph = _ddc_introspect(dsn, db_type=db_type, schema_filter=schema_filter)
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 422
        except Exception as exc:  # noqa: BLE001
            logger.exception("Introspection failed for dsn=%r", dsn)
            return jsonify({"error": f"Introspection failed: {exc}"}), 500

        meta = graph.pop("_meta", {})
        graph_clean = {k: v for k, v in graph.items() if not k.startswith("_")}

        result_design_id = None

        # Optionally persist as a new design or merge into existing one
        if design_id_param:
            # Merge into existing design
            conn = get_connection()
            row = conn.execute(
                "SELECT graph_json FROM data_designs WHERE id=?", (design_id_param,)
            ).fetchone()
            if row:
                try:
                    existing = json.loads(row["graph_json"]) if isinstance(row["graph_json"], str) else row["graph_json"]
                except Exception:
                    existing = {"nodes": [], "edges": [], "boundaries": []}
                # Merge: add introspected nodes/edges that aren't already present
                existing_node_ids = {n["id"] for n in existing.get("nodes", [])}
                existing_edge_ids = {e.get("id") for e in existing.get("edges", [])}
                new_nodes = [n for n in graph_clean.get("nodes", []) if n["id"] not in existing_node_ids]
                new_edges = [e for e in graph_clean.get("edges", []) if e.get("id") not in existing_edge_ids]
                existing["nodes"] = existing.get("nodes", []) + new_nodes
                existing["edges"] = existing.get("edges", []) + new_edges
                merged_json = json.dumps(existing)
                conn.execute(
                    "UPDATE data_designs SET graph_json=?, updated_at=? WHERE id=?",
                    (merged_json, now_isoformat(), design_id_param),
                )
                conn.commit()
                conn.close()
                _audit(
                    design_id_param,
                    session.get("user_id", "system"),
                    "INTROSPECT_MERGE",
                    f"db={meta.get('db_name','?')} tables={meta.get('table_count',0)}",
                )
                result_design_id = design_id_param
            else:
                conn.close()
        elif design_name:
            # Create a new design from the introspected graph
            new_id = str(_uuid.uuid4())
            conn = get_connection()
            conn.execute(
                "INSERT INTO data_designs (id, name, description, graph_json, classification, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    new_id,
                    design_name,
                    f"Auto-generated from {meta.get('db_type', 'db')}:{meta.get('db_name', '?')}",
                    json.dumps(graph_clean),
                    "CUI",
                    now_isoformat(),
                    now_isoformat(),
                ),
            )
            conn.commit()
            conn.close()
            _audit(
                new_id,
                session.get("user_id", "system"),
                "INTROSPECT_CREATE",
                f"db={meta.get('db_name','?')} tables={meta.get('table_count',0)}",
            )
            result_design_id = new_id

        return jsonify(
            {
                "nodes": graph_clean.get("nodes", []),
                "edges": graph_clean.get("edges", []),
                "boundaries": graph_clean.get("boundaries", []),
                "meta": meta,
                "design_id": result_design_id,
            }
        )

    @bp.route("/api/introspect/preview", methods=["POST"])
    @dc_login_required
    def dc_api_introspect_preview():
        """Return schema metadata without generating full graph nodes.

        Lightweight endpoint for pre-flight checks — shows table/column
        counts before committing to a full introspection import.

        POST body: same as /api/introspect (dsn, db_type, schema)
        Returns: {"db_type":..., "db_name":..., "table_count":...,
                   "column_count":..., "fk_count":...}
        """
        body = request.get_json(force=True, silent=True) or {}
        dsn = body.get("dsn", "").strip()
        if not dsn:
            return jsonify({"error": "dsn is required"}), 400

        db_type = body.get("db_type", "auto")
        schema_filter = body.get("schema") or None

        try:
            from tools.data_canvas.introspector import read_schema

            schema = read_schema(dsn, db_type=db_type, schema_filter=schema_filter)
        except Exception as exc:
            logger.exception("Schema preview failed for dsn=%r", dsn)
            return jsonify({"error": f"Schema preview failed: {exc}"}), 500

        return jsonify(
            {
                "db_type": schema["db_type"],
                "db_name": schema["db_name"],
                "table_count": len(schema["tables"]),
                "column_count": sum(len(t["columns"]) for t in schema["tables"]),
                "fk_count": len(schema["foreign_keys"]),
                "tables": [
                    {
                        "schema": t["schema"],
                        "name": t["name"],
                        "type": t["type"],
                        "column_count": len(t["columns"]),
                    }
                    for t in schema["tables"]
                ],
            }
        )

    # ══════════════════════════════════════════════════════════════════════
    # RUNBOOKS — Page Routes
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/runbooks")
    @dc_login_required
    def dc_runbooks():
        """Runbook list page — all DDC incident response runbooks."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, title, category, severity, description, status, created_at "
            "FROM ddc_runbooks ORDER BY severity DESC, category, title"
        ).fetchall()
        conn.close()
        runbooks = [row_to_dict(r) for r in rows]
        return render_template("data_canvas/runbooks.html", runbooks=runbooks)

    @bp.route("/runbooks/<runbook_id>")
    @dc_login_required
    def dc_runbook_detail(runbook_id):
        """Runbook detail page — view steps and execution history."""
        conn = get_connection()
        rb_row = conn.execute("SELECT * FROM ddc_runbooks WHERE id=?", (runbook_id,)).fetchone()
        if not rb_row:
            conn.close()
            return redirect("/data/runbooks")
        runbook = row_to_dict(rb_row)
        try:
            import json as _json
            runbook["steps"] = _json.loads(runbook.get("steps_json") or "[]")
        except Exception:
            runbook["steps"] = []
        execs = [
            row_to_dict(r)
            for r in conn.execute(
                "SELECT id, triggered_by, status, notes, started_at, completed_at "
                "FROM ddc_runbook_executions WHERE runbook_id=? ORDER BY started_at DESC LIMIT 20",
                (runbook_id,),
            ).fetchall()
        ]
        conn.close()
        return render_template("data_canvas/runbooks.html", runbook=runbook, executions=execs, detail=True)

    # ══════════════════════════════════════════════════════════════════════
    # RUNBOOKS — API
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/runbooks", methods=["GET"])
    @dc_login_required
    def dc_api_runbooks_list():
        """List all runbooks, optionally filtered by category or severity."""
        category = request.args.get("category")
        severity = request.args.get("severity")
        conn = get_connection()
        if category and severity:
            rows = conn.execute(
                "SELECT * FROM ddc_runbooks WHERE category=? AND severity=? ORDER BY title",
                (category, severity),
            ).fetchall()
        elif category:
            rows = conn.execute(
                "SELECT * FROM ddc_runbooks WHERE category=? ORDER BY title", (category,)
            ).fetchall()
        elif severity:
            rows = conn.execute(
                "SELECT * FROM ddc_runbooks WHERE severity=? ORDER BY title", (severity,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ddc_runbooks ORDER BY severity DESC, category, title"
            ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    @bp.route("/api/runbooks", methods=["POST"])
    @dc_login_required
    def dc_api_runbooks_create():
        """Create a new runbook."""
        data = request.get_json(force=True, silent=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title is required"}), 400
        rb_id = f"rb-ddc-{_uuid.uuid4().hex[:10]}"
        conn = get_connection()
        conn.execute(
            "INSERT INTO ddc_runbooks "
            "(id, title, category, severity, description, trigger_condition, steps_json, "
            "classification, status, linked_design_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rb_id,
                title[:200],
                data.get("category", "general")[:50],
                data.get("severity", "medium")[:20],
                data.get("description", ""),
                data.get("trigger_condition", ""),
                json.dumps(data.get("steps", [])),
                data.get("classification", "CUI // SP-CTI"),
                data.get("status", "active"),
                data.get("linked_design_id"),
                now_isoformat(),
                now_isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        _audit(rb_id, session.get("user_id", "system"), "RUNBOOK_CREATE", title)
        return jsonify({"id": rb_id, "title": title}), 201

    @bp.route("/api/runbooks/<runbook_id>", methods=["GET"])
    @dc_login_required
    def dc_api_runbooks_get(runbook_id):
        """Get a single runbook by ID."""
        conn = get_connection()
        row = conn.execute("SELECT * FROM ddc_runbooks WHERE id=?", (runbook_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        rb = row_to_dict(row)
        try:
            rb["steps"] = json.loads(rb.get("steps_json") or "[]")
        except Exception:
            rb["steps"] = []
        return jsonify(rb)

    @bp.route("/api/runbooks/<runbook_id>", methods=["PUT"])
    @dc_login_required
    def dc_api_runbooks_update(runbook_id):
        """Update an existing runbook."""
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        row = conn.execute("SELECT id FROM ddc_runbooks WHERE id=?", (runbook_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        conn.execute(
            "UPDATE ddc_runbooks SET title=?, category=?, severity=?, description=?, "
            "trigger_condition=?, steps_json=?, classification=?, status=?, "
            "linked_design_id=?, updated_at=? WHERE id=?",
            (
                data.get("title", "")[:200],
                data.get("category", "general")[:50],
                data.get("severity", "medium")[:20],
                data.get("description", ""),
                data.get("trigger_condition", ""),
                json.dumps(data.get("steps", [])),
                data.get("classification", "CUI // SP-CTI"),
                data.get("status", "active"),
                data.get("linked_design_id"),
                now_isoformat(),
                runbook_id,
            ),
        )
        conn.commit()
        conn.close()
        _audit(runbook_id, session.get("user_id", "system"), "RUNBOOK_UPDATE", data.get("title", ""))
        return jsonify({"updated": True})

    @bp.route("/api/runbooks/<runbook_id>", methods=["DELETE"])
    @dc_login_required
    def dc_api_runbooks_delete(runbook_id):
        """Delete a runbook and its execution history."""
        conn = get_connection()
        conn.execute("DELETE FROM ddc_runbook_executions WHERE runbook_id=?", (runbook_id,))
        conn.execute("DELETE FROM ddc_runbooks WHERE id=?", (runbook_id,))
        conn.commit()
        conn.close()
        _audit(runbook_id, session.get("user_id", "system"), "RUNBOOK_DELETE", "")
        return jsonify({"deleted": True})

    @bp.route("/api/runbooks/<runbook_id>/execute", methods=["POST"])
    @dc_login_required
    def dc_api_runbooks_execute(runbook_id):
        """Log the start of a runbook execution."""
        conn = get_connection()
        rb_row = conn.execute("SELECT id, title FROM ddc_runbooks WHERE id=?", (runbook_id,)).fetchone()
        if not rb_row:
            conn.close()
            return jsonify({"error": "Runbook not found"}), 404
        data = request.get_json(force=True, silent=True) or {}
        exec_id = f"exec-{_uuid.uuid4().hex[:12]}"
        triggered_by = data.get("triggered_by") or session.get("user_id", "system")
        conn.execute(
            "INSERT INTO ddc_runbook_executions "
            "(id, runbook_id, triggered_by, status, notes, started_at) "
            "VALUES (?,?,?,?,?,?)",
            (exec_id, runbook_id, triggered_by, "in_progress", data.get("notes", ""), now_isoformat()),
        )
        conn.commit()
        conn.close()
        _audit(runbook_id, session.get("user_id", "system"), "RUNBOOK_EXECUTE", f"exec_id={exec_id}")
        return jsonify({"execution_id": exec_id, "status": "in_progress"}), 201

    @bp.route("/api/runbooks/<runbook_id>/execute/<exec_id>", methods=["PUT"])
    @dc_login_required
    def dc_api_runbooks_execution_complete(runbook_id, exec_id):
        """Mark a runbook execution as completed or failed."""
        data = request.get_json(force=True, silent=True) or {}
        status = data.get("status", "completed")
        if status not in ("completed", "failed", "in_progress"):
            return jsonify({"error": "status must be completed, failed, or in_progress"}), 400
        conn = get_connection()
        conn.execute(
            "UPDATE ddc_runbook_executions SET status=?, notes=?, completed_at=? "
            "WHERE id=? AND runbook_id=?",
            (
                status,
                data.get("notes", ""),
                now_isoformat() if status != "in_progress" else None,
                exec_id,
                runbook_id,
            ),
        )
        conn.commit()
        conn.close()
        return jsonify({"updated": True, "status": status})

    @bp.route("/api/runbooks/<runbook_id>/executions", methods=["GET"])
    @dc_login_required
    def dc_api_runbooks_execution_list(runbook_id):
        """List execution history for a runbook."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, triggered_by, status, notes, started_at, completed_at "
            "FROM ddc_runbook_executions WHERE runbook_id=? ORDER BY started_at DESC",
            (runbook_id,),
        ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    # ── External Catalog Sync ──────────────────────────────────────────────────

    # ══════════════════════════════════════════════════════════════════════
    # SOP PAGE ROUTES
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/sops")
    @dc_login_required
    def dc_sops():
        """SOP list page — all DDC standard operating procedures."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, title, category, status, version, owner, classification, created_at, updated_at "
            "FROM ddc_sops ORDER BY category, title"
        ).fetchall()
        conn.close()
        sops = [row_to_dict(r) for r in rows]
        return render_template("data_canvas/sops.html", sops=sops)

    @bp.route("/sops/<sop_id>")
    @dc_login_required
    def dc_sop_detail(sop_id):
        """SOP detail page."""
        conn = get_connection()
        sop_row = conn.execute("SELECT * FROM ddc_sops WHERE id=?", (sop_id,)).fetchone()
        if not sop_row:
            conn.close()
            return redirect("/data/sops")
        import json as _json
        sop = row_to_dict(sop_row)
        try:
            sop["steps"] = _json.loads(sop.get("steps_json") or "[]")
        except Exception:
            sop["steps"] = []
        try:
            sop["references"] = _json.loads(sop.get("references_json") or "[]")
        except Exception:
            sop["references"] = []
        approvals = [
            row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM ddc_sop_approvals WHERE sop_id=? ORDER BY created_at DESC",
                (sop_id,),
            ).fetchall()
        ]
        conn.close()
        return render_template("data_canvas/sops.html", sop=sop, approvals=approvals, detail=True)

    # ══════════════════════════════════════════════════════════════════════
    # SOP API ROUTES
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/sops", methods=["GET"])
    @dc_login_required
    def dc_api_sops_list():
        """List all SOPs, optionally filtered by category or status."""
        category = request.args.get("category", "")
        status = request.args.get("status", "")
        conn = get_connection()
        if category and status:
            rows = conn.execute(
                "SELECT * FROM ddc_sops WHERE category=? AND status=? ORDER BY title",
                (category, status),
            ).fetchall()
        elif category:
            rows = conn.execute(
                "SELECT * FROM ddc_sops WHERE category=? ORDER BY title", (category,)
            ).fetchall()
        elif status:
            rows = conn.execute(
                "SELECT * FROM ddc_sops WHERE status=? ORDER BY title", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ddc_sops ORDER BY category, title"
            ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    @bp.route("/api/sops", methods=["POST"])
    @dc_login_required
    def dc_api_sops_create():
        """Create a new SOP."""
        import json as _json
        data = request.get_json(force=True, silent=True) or {}
        if not data.get("title"):
            return jsonify({"error": "title is required"}), 400
        sop_id = str(_uuid.uuid4())
        steps = data.get("steps", [])
        refs = data.get("references", [])
        conn = get_connection()
        conn.execute(
            "INSERT INTO ddc_sops "
            "(id, title, category, description, purpose, scope, steps_json, references_json, "
            "version, status, classification, linked_design_id, owner, reviewer, approver, "
            "created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sop_id,
                data["title"],
                data.get("category", "general"),
                data.get("description", ""),
                data.get("purpose", ""),
                data.get("scope", ""),
                _json.dumps(steps),
                _json.dumps(refs),
                data.get("version", "1.0"),
                "draft",
                data.get("classification", "CUI // SP-CTI"),
                data.get("linked_design_id"),
                data.get("owner", ""),
                data.get("reviewer", ""),
                data.get("approver", ""),
                now_isoformat(),
                now_isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        _audit(sop_id, session.get("user_id", "system"), "SOP_CREATE", data["title"])
        return jsonify({"id": sop_id, "status": "created"}), 201

    @bp.route("/api/sops/<sop_id>", methods=["GET"])
    @dc_login_required
    def dc_api_sops_get(sop_id):
        """Get a single SOP by ID."""
        import json as _json
        conn = get_connection()
        row = conn.execute("SELECT * FROM ddc_sops WHERE id=?", (sop_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "not found"}), 404
        sop = row_to_dict(row)
        try:
            sop["steps"] = _json.loads(sop.get("steps_json") or "[]")
        except Exception:
            sop["steps"] = []
        try:
            sop["references"] = _json.loads(sop.get("references_json") or "[]")
        except Exception:
            sop["references"] = []
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>", methods=["PUT"])
    @dc_login_required
    def dc_api_sops_update(sop_id):
        """Update an existing SOP."""
        import json as _json
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        row = conn.execute("SELECT id FROM ddc_sops WHERE id=?", (sop_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "not found"}), 404
        steps = data.get("steps")
        refs = data.get("references")
        conn.execute(
            "UPDATE ddc_sops SET title=?, category=?, description=?, purpose=?, scope=?, "
            "steps_json=?, references_json=?, version=?, classification=?, owner=?, "
            "reviewer=?, approver=?, linked_design_id=?, updated_at=? "
            "WHERE id=?",
            (
                data.get("title", ""),
                data.get("category", "general"),
                data.get("description", ""),
                data.get("purpose", ""),
                data.get("scope", ""),
                _json.dumps(steps) if steps is not None else "[]",
                _json.dumps(refs) if refs is not None else "[]",
                data.get("version", "1.0"),
                data.get("classification", "CUI // SP-CTI"),
                data.get("owner", ""),
                data.get("reviewer", ""),
                data.get("approver", ""),
                data.get("linked_design_id"),
                now_isoformat(),
                sop_id,
            ),
        )
        conn.commit()
        conn.close()
        _audit(sop_id, session.get("user_id", "system"), "SOP_UPDATE", data.get("title", ""))
        return jsonify({"id": sop_id, "status": "updated"})

    @bp.route("/api/sops/<sop_id>", methods=["DELETE"])
    @dc_login_required
    def dc_api_sops_delete(sop_id):
        """Delete a SOP and its approval history."""
        conn = get_connection()
        conn.execute("DELETE FROM ddc_sop_approvals WHERE sop_id=?", (sop_id,))
        conn.execute("DELETE FROM ddc_sops WHERE id=?", (sop_id,))
        conn.commit()
        conn.close()
        _audit(sop_id, session.get("user_id", "system"), "SOP_DELETE", "")
        return jsonify({"status": "deleted"})

    @bp.route("/api/sops/<sop_id>/submit", methods=["POST"])
    @dc_login_required
    def dc_api_sops_submit(sop_id):
        """Submit a SOP for review (draft → pending_review)."""
        conn = get_connection()
        row = conn.execute("SELECT id, status FROM ddc_sops WHERE id=?", (sop_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "not found"}), 404
        data = request.get_json(force=True, silent=True) or {}
        reviewer = data.get("reviewer") or session.get("user_id", "system")
        approval_id = str(_uuid.uuid4())
        conn.execute(
            "UPDATE ddc_sops SET status='pending_review', updated_at=? WHERE id=?",
            (now_isoformat(), sop_id),
        )
        conn.execute(
            "INSERT INTO ddc_sop_approvals (id, sop_id, reviewer, action, comment, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (approval_id, sop_id, reviewer, "submitted", data.get("comment", ""), now_isoformat()),
        )
        conn.commit()
        conn.close()
        _audit(sop_id, session.get("user_id", "system"), "SOP_SUBMIT", f"reviewer={reviewer}")
        return jsonify({"id": sop_id, "status": "pending_review"})

    @bp.route("/api/sops/<sop_id>/approve", methods=["POST"])
    @dc_login_required
    def dc_api_sops_approve(sop_id):
        """Approve or reject a SOP."""
        data = request.get_json(force=True, silent=True) or {}
        action = data.get("action", "approved")
        if action not in ("approved", "rejected"):
            return jsonify({"error": "action must be 'approved' or 'rejected'"}), 400
        conn = get_connection()
        row = conn.execute("SELECT id FROM ddc_sops WHERE id=?", (sop_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "not found"}), 404
        new_status = action  # 'approved' or 'rejected' maps directly to status
        if action == "rejected":
            new_status = "draft"  # rejected goes back to draft
        approver = data.get("approver") or session.get("user_id", "system")
        approval_id = str(_uuid.uuid4())
        conn.execute(
            "UPDATE ddc_sops SET status=?, approver=?, updated_at=? WHERE id=?",
            (new_status, approver, now_isoformat(), sop_id),
        )
        conn.execute(
            "INSERT INTO ddc_sop_approvals (id, sop_id, reviewer, action, comment, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (approval_id, sop_id, approver, action, data.get("comment", ""), now_isoformat()),
        )
        conn.commit()
        conn.close()
        _audit(sop_id, session.get("user_id", "system"), f"SOP_{action.upper()}", f"approver={approver}")
        return jsonify({"id": sop_id, "status": new_status})

    @bp.route("/api/sops/<sop_id>/retire", methods=["POST"])
    @dc_login_required
    def dc_api_sops_retire(sop_id):
        """Retire a SOP (approved → retired)."""
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        row = conn.execute("SELECT id FROM ddc_sops WHERE id=?", (sop_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "not found"}), 404
        reviewer = data.get("reviewer") or session.get("user_id", "system")
        approval_id = str(_uuid.uuid4())
        conn.execute(
            "UPDATE ddc_sops SET status='retired', updated_at=? WHERE id=?",
            (now_isoformat(), sop_id),
        )
        conn.execute(
            "INSERT INTO ddc_sop_approvals (id, sop_id, reviewer, action, comment, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (approval_id, sop_id, reviewer, "retired", data.get("comment", ""), now_isoformat()),
        )
        conn.commit()
        conn.close()
        _audit(sop_id, session.get("user_id", "system"), "SOP_RETIRE", "")
        return jsonify({"id": sop_id, "status": "retired"})

    @bp.route("/api/sops/<sop_id>/approvals", methods=["GET"])
    @dc_login_required
    def dc_api_sops_approvals(sop_id):
        """Get approval history for a SOP."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM ddc_sop_approvals WHERE sop_id=? ORDER BY created_at DESC",
            (sop_id,),
        ).fetchall()
        conn.close()
        return jsonify([row_to_dict(r) for r in rows])

    # ── External Catalog Sync ──────────────────────────────────────────────────

    @bp.route("/api/sync/datahub", methods=["POST"])
    @dc_login_required
    def dc_api_sync_datahub():
        """Trigger a one-way DDC → DataHub sync.

        Body (JSON, all optional):
            design_id  str   Sync a single design; omit to sync all designs.
            dry_run    bool  Parse only — no writes to DataHub (default false).

        Returns JSON sync report from DDCDataHubSync.
        """
        data = request.get_json(force=True, silent=True) or {}
        design_id = data.get("design_id")
        dry_run = bool(data.get("dry_run", False))

        try:
            from tools.data_canvas.sync.datahub_sync import DDCDataHubSync
        except ImportError as exc:
            return jsonify({"status": "error", "message": f"datahub_sync import failed: {exc}"}), 500

        syncer = DDCDataHubSync(dry_run=dry_run)

        if not dry_run and not syncer.client.ping():
            return jsonify({
                "status": "error",
                "message": f"DataHub GMS not reachable at {syncer.cfg['url']}",
            }), 503

        if design_id:
            result = syncer.sync_design(design_id)
        else:
            result = syncer.sync_all()

        status_code = 200 if result.get("status") == "ok" else 207
        return jsonify(result), status_code

    # ══════════════════════════════════════════════════════════════════════
    # PAGE ROUTE — Lineage Graph
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/lineage")
    @dc_login_required
    def dc_lineage():
        """Lineage graph page — classification-colored DAG across all designs."""
        classification_filter = request.args.get("classification", "").strip()
        conn = get_connection()
        designs_rows = conn.execute(
            "SELECT id, name, classification FROM data_designs ORDER BY name"
        ).fetchall()
        designs = [row_to_dict(r) for r in designs_rows]
        if classification_filter:
            lin_rows = conn.execute(
                "SELECT id, design_id, source_node_id, target_node_id, lineage_type, "
                "column_name, transform_desc, classification, created_at "
                "FROM dd_lineage WHERE classification=? ORDER BY created_at",
                (classification_filter,),
            ).fetchall()
        else:
            lin_rows = conn.execute(
                "SELECT id, design_id, source_node_id, target_node_id, lineage_type, "
                "column_name, transform_desc, classification, created_at "
                "FROM dd_lineage ORDER BY created_at"
            ).fetchall()
        conn.close()
        lineage_records = [row_to_dict(r) for r in lin_rows]
        dag = build_column_lineage_dag(lineage_records)
        gaps = generate_contract_assertions(
            lineage_records, {"nodes": [], "edges": [], "boundaries": []}
        )
        return render_template(
            "data_canvas/lineage.html",
            dag_json=json.dumps(dag),
            gaps=gaps,
            designs=designs,
            classification_levels=DATA_CLASSIFICATION_LEVELS,
            classification_filter=classification_filter,
        )

    @bp.route("/api/sync/openmetadata", methods=["POST"])
    @dc_login_required
    def dc_api_sync_openmetadata():
        """Trigger a one-way DDC → OpenMetadata sync.

        Body (JSON, all optional):
            design_id  str   Sync a single design; omit to sync all designs.
            dry_run    bool  Parse only — no writes to OpenMetadata (default false).

        Returns JSON sync report from DDCOpenMetadataSync.
        """
        data = request.get_json(force=True, silent=True) or {}
        design_id = data.get("design_id")
        dry_run = bool(data.get("dry_run", False))

        try:
            from tools.data_canvas.sync.openmetadata_sync import DDCOpenMetadataSync
        except ImportError as exc:
            return jsonify({"status": "error", "message": f"openmetadata_sync import failed: {exc}"}), 500

        syncer = DDCOpenMetadataSync(dry_run=dry_run)

        if not dry_run and not syncer.client.ping():
            return jsonify({
                "status": "error",
                "message": f"OpenMetadata not reachable at {syncer.cfg['url']}",
            }), 503

        if design_id:
            result = syncer.sync_design(design_id)
        else:
            result = syncer.sync_all()

        status_code = 200 if result.get("status") == "ok" else 207
        return jsonify(result), status_code

    return bp
