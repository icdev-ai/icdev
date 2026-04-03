# CUI // SP-CTI
"""ICDEV Infrastructure Design Canvas — Flask Blueprint.

Routes for the IDC visual designer, API endpoints for CRUD operations
and compliance assessment.
"""

import base64
import json
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template, request

from tools.infra_canvas.constants import (
    CSP_EQUIVALENCE,
    INFRA_COMPLIANCE_RULES,
    INFRA_OBJECTS,
)
from tools.infra_canvas.infra_engine import (
    assess_infra_design,
    compute_csp_coverage,
    suggest_equivalents,
)

infra_bp = Blueprint(
    "infra_canvas",
    __name__,
    url_prefix="/infra",
    template_folder="../../tools/dashboard/templates",
)


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _gen_id():
    return f"idc-{uuid.uuid4().hex[:10]}"


def _get_conn():
    from tools.infra_canvas.db.init_db import get_connection
    return get_connection()


# ── Pages ────────────────────────────────────────────────────────────────────

@infra_bp.route("/")
def index():
    """List all infrastructure designs."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, description, classification, created_at, "
            "updated_at FROM infra_designs ORDER BY updated_at DESC"
        ).fetchall()
        designs = [dict(r) for r in rows]
        tpls = conn.execute(
            "SELECT id, name, category, description, tags "
            "FROM idc_templates ORDER BY category, name"
        ).fetchall()
        templates = [dict(r) for r in tpls]
        return render_template(
            "infra_canvas/index.html",
            designs=designs,
            templates=templates,
        )
    finally:
        conn.close()


@infra_bp.route("/canvas/new")
def new_canvas():
    """Create a new infrastructure design and open the canvas."""
    conn = _get_conn()
    try:
        # Check for template parameter
        template_id = request.args.get("template")
        graph = {"nodes": [], "edges": []}
        name = "Untitled Infrastructure"
        if template_id:
            tpl = conn.execute(
                "SELECT name, graph_json FROM idc_templates WHERE id = ?",
                (template_id,),
            ).fetchone()
            if tpl:
                graph = json.loads(dict(tpl)["graph_json"])
                name = f"{dict(tpl)['name']} (copy)"

        design_id = _gen_id()
        now = _utcnow()
        conn.execute(
            "INSERT INTO infra_designs "
            "(id, name, description, graph_json, template_id, "
            "classification, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (design_id, name, "", json.dumps(graph),
             template_id, "CUI", now, now),
        )
        conn.commit()
        from flask import redirect
        return redirect(f"/infra/canvas/{design_id}")
    finally:
        conn.close()


@infra_bp.route("/templates")
def templates_page():
    """Template gallery page."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, category, description, tags "
            "FROM idc_templates ORDER BY category, name"
        ).fetchall()
        templates = [dict(r) for r in rows]
        return render_template(
            "infra_canvas/templates.html", templates=templates
        )
    finally:
        conn.close()


@infra_bp.route("/assessments")
def assessments_page():
    """Assessment history page."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT a.id, a.design_id, a.assessment_type, a.score, "
            "a.created_at, d.name AS design_name "
            "FROM idc_assessments a "
            "LEFT JOIN infra_designs d ON a.design_id = d.id "
            "ORDER BY a.created_at DESC LIMIT 50"
        ).fetchall()
        assessments = [dict(r) for r in rows]
        return render_template(
            "infra_canvas/assessments.html", assessments=assessments
        )
    finally:
        conn.close()


@infra_bp.route("/canvas/<design_id>")
def canvas(design_id):
    """Open the infrastructure canvas editor."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM infra_designs WHERE id = ?", (design_id,)
        ).fetchone()
        if not row:
            return "Design not found", 404
        design = dict(row)
        return render_template(
            "infra_canvas/canvas.html",
            design=design,
            objects=INFRA_OBJECTS,
            rules=INFRA_COMPLIANCE_RULES,
            csp_equivalence=CSP_EQUIVALENCE,
        )
    finally:
        conn.close()


@infra_bp.route("/remediation/<design_id>")
def idc_remediation_page(design_id):
    """Remediation page — gap analysis with recommended fixes."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, name FROM infra_designs WHERE id=?", (design_id,)
        ).fetchone()
        if not row:
            from flask import redirect
            return redirect("/infra/")
        design = dict(row)
        return render_template("infra_canvas/remediation.html", design=design)
    finally:
        conn.close()


# ── API ──────────────────────────────────────────────────────────────────────

@infra_bp.route("/api/designs", methods=["POST"])
def create_design():
    """Create a new infrastructure design."""
    data = request.get_json(force=True)
    design_id = _gen_id()
    now = _utcnow()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO infra_designs "
            "(id, name, description, graph_json, template_id, "
            "classification, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                design_id,
                data.get("name", "Untitled Infrastructure"),
                data.get("description", ""),
                json.dumps(data.get("graph", {"nodes": [], "edges": []})),
                data.get("template_id"),
                data.get("classification", "CUI"),
                now,
                now,
            ),
        )
        conn.commit()
        return jsonify({"status": "created", "id": design_id}), 201
    finally:
        conn.close()


@infra_bp.route("/api/designs/<design_id>", methods=["GET"])
def get_design(design_id):
    """Get a design by ID."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM infra_designs WHERE id = ?", (design_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = dict(row)
        d["graph"] = json.loads(d.get("graph_json", "{}"))
        return jsonify(d)
    finally:
        conn.close()


@infra_bp.route("/api/designs/<design_id>", methods=["PUT"])
def save_design(design_id):
    """Save/update a design."""
    data = request.get_json(force=True)
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE infra_designs SET name = ?, description = ?, "
            "graph_json = ?, classification = ?, updated_at = ? "
            "WHERE id = ?",
            (
                data.get("name"),
                data.get("description", ""),
                json.dumps(data.get("graph", {})),
                data.get("classification", "CUI"),
                _utcnow(),
                design_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Cross-canvas trigger: auto-assess for security gaps
    try:
        from tools.security_canvas.agent import on_idc_design_saved
        on_idc_design_saved(design_id)
    except Exception:
        pass

    return jsonify({"status": "saved", "id": design_id})


@infra_bp.route("/api/designs/<design_id>/assess", methods=["POST"])
def run_assessment(design_id):
    """Run compliance assessment on a design."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT graph_json FROM infra_designs WHERE id = ?", (design_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404

        graph = json.loads(row["graph_json"])
        result = assess_infra_design(graph)
        csp_info = compute_csp_coverage(graph)
        result["csp_coverage"] = csp_info

        # Persist assessment
        assess_id = f"ia-{uuid.uuid4().hex[:10]}"
        conn.execute(
            "INSERT INTO idc_assessments "
            "(id, design_id, assessment_type, findings_json, score, "
            "created_at) VALUES (?,?,?,?,?,?)",
            (
                assess_id,
                design_id,
                "compliance",
                json.dumps(result["findings"]),
                result["score"],
                _utcnow(),
            ),
        )
        conn.commit()
        return jsonify(result)
    finally:
        conn.close()


@infra_bp.route("/api/templates", methods=["GET"])
def list_templates():
    """List available IDC templates."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, category, description, tags FROM idc_templates "
            "ORDER BY name"
        ).fetchall()
        return jsonify({"templates": [dict(r) for r in rows]})
    finally:
        conn.close()


@infra_bp.route("/api/snippets", methods=["GET"])
def list_snippets():
    """List available IDC snippets (reusable graph fragments)."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, category, description, graph_json, tags "
            "FROM idc_snippets ORDER BY category, name"
        ).fetchall()
        return jsonify({"snippets": [dict(r) for r in rows]})
    finally:
        conn.close()


# ── Versioning helpers ────────────────────────────────────────────────────────

def _idc_diff_graph(old: dict, new: dict) -> str:
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


# ── Versioning API ────────────────────────────────────────────────────────────

@infra_bp.route("/api/versions/<design_id>", methods=["GET"])
def idc_api_list_versions(design_id):
    """List all version snapshots for an infra design."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, version_number, change_summary, user_id, created_at "
            "FROM idc_versions WHERE design_id=? ORDER BY version_number DESC",
            (design_id,),
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@infra_bp.route("/api/versions/<design_id>", methods=["POST"])
def idc_api_create_version(design_id):
    """Create a version snapshot of the current infra design state."""
    data = request.get_json(force=True, silent=True) or {}
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT graph_json FROM infra_designs WHERE id=?", (design_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "Design not found"}), 404
        raw = row["graph_json"]
        try:
            current_graph = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            current_graph = {}
        ver_num = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 FROM idc_versions WHERE design_id=?",
            (design_id,),
        ).fetchone()[0]
        change_summary = data.get("change_summary", "")
        if not change_summary:
            prev = conn.execute(
                "SELECT graph_json FROM idc_versions WHERE design_id=? ORDER BY version_number DESC LIMIT 1",
                (design_id,),
            ).fetchone()
            if prev:
                try:
                    prev_graph = json.loads(prev[0]) if isinstance(prev[0], str) else prev[0]
                    change_summary = _idc_diff_graph(prev_graph, current_graph)
                except Exception:
                    pass
        ver_id = str(uuid.uuid4())
        now = _utcnow()
        conn.execute(
            "INSERT INTO idc_versions (id, design_id, version_number, graph_json, change_summary, user_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (ver_id, design_id, ver_num,
             json.dumps(current_graph) if isinstance(current_graph, dict) else str(raw),
             change_summary, data.get("user_id", ""), now),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"id": ver_id, "version_number": ver_num, "change_summary": change_summary, "created_at": now}), 201


@infra_bp.route("/api/versions/<design_id>/restore/<version_id>", methods=["POST"])
def idc_api_restore_version(design_id, version_id):
    """Restore an infra design to a previous version snapshot."""
    conn = _get_conn()
    try:
        ver = conn.execute(
            "SELECT graph_json, version_number FROM idc_versions WHERE id=? AND design_id=?",
            (version_id, design_id),
        ).fetchone()
        if not ver:
            return jsonify({"error": "Version not found"}), 404
        now = _utcnow()
        conn.execute(
            "UPDATE infra_designs SET graph_json=?, updated_at=? WHERE id=?",
            (ver["graph_json"], now, design_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"id": design_id, "restored_version": version_id,
                    "version_number": ver["version_number"], "updated_at": now})


@infra_bp.route("/api/versions/<design_id>/diff", methods=["POST"])
def idc_api_diff_versions(design_id):
    """Compare two version snapshots of an infra design."""
    data = request.get_json(force=True, silent=True) or {}
    ver_a_id = data.get("version_a")
    ver_b_id = data.get("version_b")
    if not ver_a_id or not ver_b_id:
        return jsonify({"error": "version_a and version_b required"}), 400
    conn = _get_conn()
    try:
        ver_a = conn.execute(
            "SELECT graph_json, version_number FROM idc_versions WHERE id=? AND design_id=?",
            (ver_a_id, design_id),
        ).fetchone()
        ver_b = conn.execute(
            "SELECT graph_json, version_number FROM idc_versions WHERE id=? AND design_id=?",
            (ver_b_id, design_id),
        ).fetchone()
    finally:
        conn.close()
    if not ver_a or not ver_b:
        return jsonify({"error": "One or both versions not found"}), 404
    try:
        graph_a = json.loads(ver_a["graph_json"]) if isinstance(ver_a["graph_json"], str) else ver_a["graph_json"]
        graph_b = json.loads(ver_b["graph_json"]) if isinstance(ver_b["graph_json"], str) else ver_b["graph_json"]
    except Exception:
        return jsonify({"error": "Failed to parse graph data"}), 500
    summary = _idc_diff_graph(graph_a, graph_b)
    return jsonify({
        "version_a": {"id": ver_a_id, "version_number": ver_a["version_number"]},
        "version_b": {"id": ver_b_id, "version_number": ver_b["version_number"]},
        "summary": summary,
    })


@infra_bp.route("/api/equivalents", methods=["GET"])
def get_equivalents():
    """Get CSP equivalents for a node type."""
    node_type = request.args.get("type", "")
    target_csp = request.args.get("csp", "")
    if not node_type or not target_csp:
        return jsonify({"error": "type and csp params required"}), 400
    suggestions = suggest_equivalents(node_type, target_csp)
    return jsonify({"node_type": node_type, "target_csp": target_csp,
                     "equivalents": suggestions})


@infra_bp.route("/api/objects", methods=["GET"])
def list_objects():
    """Return the full IDC object palette."""
    return jsonify(INFRA_OBJECTS)


@infra_bp.route("/api/export/<design_id>/vsdx", methods=["POST"])
def export_vsdx_file(design_id):
    """Export design as Visio .vsdx file."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT name, graph_json FROM infra_designs WHERE id = ?",
            (design_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = dict(row)
        graph = json.loads(d["graph_json"])
        from tools.network.visio_export import export_vsdx
        vsdx_bytes = export_vsdx(d["name"], graph)
        encoded = base64.b64encode(vsdx_bytes).decode("ascii")
        return jsonify({
            "format": "vsdx",
            "filename": d["name"].replace(" ", "_"),
            "data": encoded,
        })
    finally:
        conn.close()
