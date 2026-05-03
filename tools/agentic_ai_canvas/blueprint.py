# CUI // SP-CTI
"""Agentic AI Design Canvas — Flask Blueprint.

Routes:
  GET  /agentic-ai/                          index (design list)
  GET  /agentic-ai/canvas                    new canvas
  GET  /agentic-ai/canvas/<id>               existing canvas editor
  GET  /agentic-ai/templates                 template gallery
  GET  /agentic-ai/snippets                  snippet library
  GET  /agentic-ai/assessments/<id>          assessment detail
  GET  /agentic-ai/artifacts/<id>            artifacts for design

  POST /agentic-ai/api/designs               create design
  GET  /agentic-ai/api/designs/<id>          get design
  PUT  /agentic-ai/api/designs/<id>          save design (assess + workflow)
  DELETE /agentic-ai/api/designs/<id>        delete design
  POST /agentic-ai/api/designs/<id>/assess   run assessment
  POST /agentic-ai/api/designs/<id>/launch   launch to Kanban (loop_engine)

  GET  /agentic-ai/api/templates             list templates
  POST /agentic-ai/api/templates/<tid>/apply/<did>  apply template to canvas
  POST /agentic-ai/api/templates/save        save canvas as template

  GET  /agentic-ai/api/snippets              list snippets
  POST /agentic-ai/api/snippets/<sid>/insert/<did>  insert snippet into canvas

  GET  /agentic-ai/api/designs/<id>/artifacts  list artifacts
  POST /agentic-ai/api/designs/<id>/artifacts  generate artifact
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from tools.agentic_ai_canvas.constants import AADC_OBJECTS, FRAMEWORK_LABELS, NODE_DESCRIPTIONS
from tools.agentic_ai_canvas.agentic_engine import assess_design
from tools.agentic_ai_canvas import bus_subscriber, workflow

logger = logging.getLogger(__name__)

aadc_bp = Blueprint(
    "agentic_ai_canvas",
    __name__,
    url_prefix="/agentic-ai",
    template_folder="../../tools/dashboard/templates",
)

_INIT_DONE = False


def _ensure_init() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        return
    try:
        from tools.agentic_ai_canvas.db.init_db import init_db
        init_db()
    except Exception as exc:
        logger.warning("aadc: DB init error: %s", exc)
    try:
        workflow.seed_hitl_templates()
    except Exception as exc:
        logger.warning("aadc: HITL seed error: %s", exc)
    try:
        bus_subscriber.register()
    except Exception as exc:
        logger.warning("aadc: bus register error: %s", exc)
    _INIT_DONE = True


def _conn():
    from tools.agentic_ai_canvas.db.init_db import get_connection
    return get_connection()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _row(row) -> dict:
    if row is None:
        return {}
    return dict(row)


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@aadc_bp.before_request
def _init():
    _ensure_init()


@aadc_bp.route("/")
def index():
    conn = _conn()
    try:
        designs = [dict(r) for r in conn.execute(
            "SELECT id, name, description, domain, classification, autonomy_max, "
            "safety_impacting, rights_impacting, updated_at "
            "FROM aadc_designs ORDER BY updated_at DESC"
        ).fetchall()]
        # Attach latest score
        for d in designs:
            row = conn.execute(
                "SELECT score, nist_rmf_score, owasp_score FROM aadc_assessments "
                "WHERE design_id=? ORDER BY created_at DESC LIMIT 1", (d["id"],)
            ).fetchone()
            d["score"] = round(row["score"], 1) if row else None
    finally:
        conn.close()
    return render_template("agentic_ai_canvas/index.html", designs=designs)


@aadc_bp.route("/canvas")
def new_canvas():
    return render_template(
        "agentic_ai_canvas/canvas.html",
        design={"id": "", "name": "Untitled Design", "graph_json": '{"nodes":[],"edges":[]}',
                "domain": "", "classification": "CUI"},
        palette=AADC_OBJECTS,
        node_descs=NODE_DESCRIPTIONS,
        framework_labels=FRAMEWORK_LABELS,
        assessment=None,
        wf_status=None,
    )


@aadc_bp.route("/canvas/<design_id>")
def edit_canvas(design_id: str):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM aadc_designs WHERE id=?", (design_id,)).fetchone()
        if not row:
            return render_template("404.html"), 404
        design = dict(row)
        assessment_row = conn.execute(
            "SELECT * FROM aadc_assessments WHERE design_id=? ORDER BY created_at DESC LIMIT 1",
            (design_id,)
        ).fetchone()
        assessment = dict(assessment_row) if assessment_row else None
        if assessment:
            assessment["findings"] = json.loads(assessment.get("findings_json", "[]"))
            assessment["atlas"] = json.loads(assessment.get("atlas_threats", "[]"))
    finally:
        conn.close()

    wf_status = workflow.get_workflow_status(design_id)
    return render_template(
        "agentic_ai_canvas/canvas.html",
        design=design,
        palette=AADC_OBJECTS,
        node_descs=NODE_DESCRIPTIONS,
        framework_labels=FRAMEWORK_LABELS,
        assessment=assessment,
        wf_status=wf_status,
    )


@aadc_bp.route("/templates")
def templates_gallery():
    conn = _conn()
    try:
        templates = [dict(r) for r in conn.execute(
            "SELECT * FROM aadc_templates ORDER BY is_builtin DESC, name"
        ).fetchall()]
        for t in templates:
            t["badges"] = json.loads(t.get("compliance_badges", "{}"))
            t["tag_list"] = json.loads(t.get("tags", "[]"))
            g = json.loads(t.get("graph_json", '{"nodes":[],"edges":[]}'))
            t["node_count"] = len(g.get("nodes", []))
            t["edge_count"] = len(g.get("edges", []))
    finally:
        conn.close()
    return render_template("agentic_ai_canvas/templates.html", templates=templates)


@aadc_bp.route("/snippets")
def snippets_library():
    conn = _conn()
    try:
        snippets = [dict(r) for r in conn.execute(
            "SELECT * FROM aadc_snippets ORDER BY is_builtin DESC, category, name"
        ).fetchall()]
        for s in snippets:
            s["tag_list"] = json.loads(s.get("tags", "[]"))
    finally:
        conn.close()
    return render_template("agentic_ai_canvas/snippets.html", snippets=snippets)


@aadc_bp.route("/assessments/<design_id>")
def assessments_detail(design_id: str):
    conn = _conn()
    try:
        design = _row(conn.execute("SELECT * FROM aadc_designs WHERE id=?", (design_id,)).fetchone())
        assessments = [dict(r) for r in conn.execute(
            "SELECT * FROM aadc_assessments WHERE design_id=? ORDER BY created_at DESC",
            (design_id,)
        ).fetchall()]
        for a in assessments:
            a["findings"] = json.loads(a.get("findings_json", "[]"))
            a["atlas"] = json.loads(a.get("atlas_threats", "[]"))
    finally:
        conn.close()
    return render_template("agentic_ai_canvas/assessments.html",
                           design=design, assessments=assessments)


@aadc_bp.route("/artifacts/<design_id>")
def artifacts_view(design_id: str):
    conn = _conn()
    try:
        design = _row(conn.execute("SELECT * FROM aadc_designs WHERE id=?", (design_id,)).fetchone())
        artifacts = [dict(r) for r in conn.execute(
            "SELECT * FROM aadc_artifacts WHERE design_id=? ORDER BY created_at DESC",
            (design_id,)
        ).fetchall()]
    finally:
        conn.close()
    return render_template("agentic_ai_canvas/artifacts.html",
                           design=design, artifacts=artifacts)


# ---------------------------------------------------------------------------
# Design API
# ---------------------------------------------------------------------------

@aadc_bp.route("/api/designs", methods=["POST"])
def create_design():
    data = request.get_json(force=True) or {}
    did = f"aadc-{_uid()}"
    now = _utcnow()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO aadc_designs "
            "(id, name, description, domain, classification, graph_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (did, data.get("name", "Untitled Design"),
             data.get("description", ""), data.get("domain", ""),
             data.get("classification", "CUI"),
             json.dumps(data.get("graph", {"nodes": [], "edges": []})),
             now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"id": did, "status": "created"}), 201


@aadc_bp.route("/api/designs/<design_id>", methods=["GET"])
def get_design(design_id: str):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM aadc_designs WHERE id=?", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify(dict(row))
    finally:
        conn.close()


@aadc_bp.route("/api/designs/<design_id>", methods=["PUT"])
def save_design(design_id: str):
    data = request.get_json(force=True) or {}
    graph = data.get("graph", {"nodes": [], "edges": []})
    graph_json = json.dumps(graph)
    now = _utcnow()

    conn = _conn()
    try:
        existing = conn.execute(
            "SELECT id FROM aadc_designs WHERE id=?", (design_id,)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO aadc_designs (id, name, description, domain, classification, "
                "graph_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (design_id, data.get("name", "Untitled Design"),
                 data.get("description", ""), data.get("domain", ""),
                 data.get("classification", "CUI"), graph_json, now, now),
            )
        else:
            conn.execute(
                "UPDATE aadc_designs SET name=?, description=?, domain=?, classification=?, "
                "graph_json=?, updated_at=? WHERE id=?",
                (data.get("name", "Untitled Design"), data.get("description", ""),
                 data.get("domain", ""), data.get("classification", "CUI"),
                 graph_json, now, design_id),
            )
        # Version snapshot
        ver = (conn.execute(
            "SELECT MAX(version_number) FROM aadc_versions WHERE design_id=?", (design_id,)
        ).fetchone()[0] or 0) + 1
        conn.execute(
            "INSERT INTO aadc_versions (id, design_id, version_number, graph_json, created_at) "
            "VALUES (?,?,?,?,?)",
            (f"v-{_uid()}", design_id, ver, graph_json, now),
        )
        conn.commit()
    finally:
        conn.close()

    # Run assessment
    meta = {"domain": data.get("domain", ""), "classification": data.get("classification", "CUI")}
    result = assess_design(design_id, graph, meta)

    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO aadc_assessments "
            "(id, design_id, score, nist_rmf_score, owasp_score, omb_compliant, "
            "autonomy_max, safety_impacting, rights_impacting, findings_json, atlas_threats, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (result["id"], design_id, result["score"], result["nist_rmf_score"],
             result["owasp_score"], result["omb_compliant"], result["autonomy_max"],
             result["safety_impacting"], result["rights_impacting"],
             result["findings_json"], result["atlas_threats"], result["created_at"]),
        )
        conn.execute(
            "UPDATE aadc_designs SET autonomy_max=?, safety_impacting=?, rights_impacting=?, "
            "hitl_required=?, updated_at=? WHERE id=?",
            (result["autonomy_max"], result["safety_impacting"], result["rights_impacting"],
             1 if (result["safety_impacting"] or result["rights_impacting"]) else 0,
             now, design_id),
        )
        conn.commit()
    finally:
        conn.close()

    # Publish event bus
    bus_subscriber.publish_design_saved(
        design_id, bool(result["safety_impacting"]),
        bool(result["rights_impacting"]), result["autonomy_max"]
    )

    # Create HITL workflow if needed
    wf_instance_id = workflow.maybe_create_hitl_instance(
        design_id, bool(result["safety_impacting"]), bool(result["rights_impacting"])
    )

    result["wf_instance_id"] = wf_instance_id
    return jsonify(result)


@aadc_bp.route("/api/designs/<design_id>", methods=["DELETE"])
def delete_design(design_id: str):
    conn = _conn()
    try:
        conn.execute("DELETE FROM aadc_designs WHERE id=?", (design_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "deleted"})


@aadc_bp.route("/api/designs/<design_id>/assess", methods=["POST"])
def run_assessment(design_id: str):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM aadc_designs WHERE id=?", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        d = dict(row)
    finally:
        conn.close()

    meta = {"domain": d.get("domain", ""), "classification": d.get("classification", "CUI"),
            "has_prior_assessment": True}
    result = assess_design(design_id, d["graph_json"], meta)

    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO aadc_assessments "
            "(id, design_id, score, nist_rmf_score, owasp_score, omb_compliant, "
            "autonomy_max, safety_impacting, rights_impacting, findings_json, atlas_threats, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (result["id"], design_id, result["score"], result["nist_rmf_score"],
             result["owasp_score"], result["omb_compliant"], result["autonomy_max"],
             result["safety_impacting"], result["rights_impacting"],
             result["findings_json"], result["atlas_threats"], result["created_at"]),
        )
        conn.commit()
    finally:
        conn.close()

    result["findings"] = json.loads(result["findings_json"])
    result["atlas"] = json.loads(result["atlas_threats"])
    return jsonify(result)


@aadc_bp.route("/api/designs/<design_id>/launch", methods=["POST"])
def launch_design(design_id: str):
    if not workflow.is_approved(design_id):
        return jsonify({"error": "Design requires HITL approval before launch"}), 403

    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM aadc_designs WHERE id=?", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        d = dict(row)
    finally:
        conn.close()

    result = workflow.launch_to_kanban(design_id, d["name"], d["graph_json"])
    return jsonify(result)


# ---------------------------------------------------------------------------
# Template API
# ---------------------------------------------------------------------------

@aadc_bp.route("/api/templates", methods=["GET"])
def list_templates():
    category = request.args.get("category")
    conn = _conn()
    try:
        if category:
            rows = conn.execute(
                "SELECT * FROM aadc_templates WHERE category=? ORDER BY name", (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM aadc_templates ORDER BY is_builtin DESC, name"
            ).fetchall()
        templates = []
        for r in rows:
            t = dict(r)
            t["badges"] = json.loads(t.get("compliance_badges", "{}"))
            t["tag_list"] = json.loads(t.get("tags", "[]"))
            g = json.loads(t.get("graph_json", '{"nodes":[],"edges":[]}'))
            t["node_count"] = len(g.get("nodes", []))
            templates.append(t)
        return jsonify({"templates": templates})
    finally:
        conn.close()


@aadc_bp.route("/api/templates/<template_id>/apply/<design_id>", methods=["POST"])
def apply_template(template_id: str, design_id: str):
    conn = _conn()
    try:
        tmpl = conn.execute(
            "SELECT * FROM aadc_templates WHERE id=?", (template_id,)
        ).fetchone()
        if not tmpl:
            return jsonify({"error": "template not found"}), 404
        t = dict(tmpl)

        existing = conn.execute(
            "SELECT id FROM aadc_designs WHERE id=?", (design_id,)
        ).fetchone()
        now = _utcnow()
        if existing:
            conn.execute(
                "UPDATE aadc_designs SET graph_json=?, template_id=?, updated_at=? WHERE id=?",
                (t["graph_json"], template_id, now, design_id),
            )
        else:
            conn.execute(
                "INSERT INTO aadc_designs (id, name, graph_json, template_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (design_id, t["name"], t["graph_json"], template_id, now, now),
            )
        conn.commit()
        return jsonify({"status": "applied", "graph_json": t["graph_json"]})
    finally:
        conn.close()


@aadc_bp.route("/api/templates/save", methods=["POST"])
def save_as_template():
    data = request.get_json(force=True) or {}
    design_id = data.get("design_id")
    name = data.get("name", "Custom Template")
    category = data.get("category", "custom")

    conn = _conn()
    try:
        if design_id:
            row = conn.execute(
                "SELECT graph_json FROM aadc_designs WHERE id=?", (design_id,)
            ).fetchone()
            graph_json = row["graph_json"] if row else '{"nodes":[],"edges":[]}'
        else:
            graph_json = json.dumps(data.get("graph", {"nodes": [], "edges": []}))

        tid = f"tpl-{_uid()}"
        conn.execute(
            "INSERT INTO aadc_templates "
            "(id, name, category, description, graph_json, tags, is_builtin, created_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (tid, name, category, data.get("description", ""),
             graph_json, json.dumps(data.get("tags", [])),
             0, data.get("created_by", "user"), _utcnow()),
        )
        conn.commit()
        return jsonify({"id": tid, "status": "saved"}), 201
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Snippet API
# ---------------------------------------------------------------------------

@aadc_bp.route("/api/snippets", methods=["GET"])
def list_snippets():
    category = request.args.get("category")
    conn = _conn()
    try:
        if category:
            rows = conn.execute(
                "SELECT * FROM aadc_snippets WHERE category=? ORDER BY name", (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM aadc_snippets ORDER BY is_builtin DESC, category, name"
            ).fetchall()
        snippets = []
        for r in rows:
            s = dict(r)
            s["tag_list"] = json.loads(s.get("tags", "[]"))
            snippets.append(s)
        return jsonify({"snippets": snippets})
    finally:
        conn.close()


@aadc_bp.route("/api/snippets/<snippet_id>/insert/<design_id>", methods=["POST"])
def insert_snippet(snippet_id: str, design_id: str):
    data = request.get_json(force=True) or {}
    offset_x = data.get("offset_x", 0)
    offset_y = data.get("offset_y", 0)

    conn = _conn()
    try:
        snp = conn.execute(
            "SELECT * FROM aadc_snippets WHERE id=?", (snippet_id,)
        ).fetchone()
        if not snp:
            return jsonify({"error": "snippet not found"}), 404

        design_row = conn.execute(
            "SELECT graph_json FROM aadc_designs WHERE id=?", (design_id,)
        ).fetchone()
        if not design_row:
            return jsonify({"error": "design not found"}), 404

        snippet_graph = json.loads(dict(snp)["graph_json"])
        design_graph = json.loads(design_row["graph_json"])

        # Remap snippet node IDs to avoid collision with existing design nodes
        id_map: dict[str, str] = {}
        new_nodes = []
        for n in snippet_graph.get("nodes", []):
            new_id = f"n-{uuid.uuid4().hex[:10]}"
            id_map[n["id"]] = new_id
            new_nodes.append({**n, "id": new_id,
                              "x": n.get("x", 0) + offset_x,
                              "y": n.get("y", 0) + offset_y})

        new_edges = []
        for e in snippet_graph.get("edges", []):
            new_edges.append({
                "id": f"e-{uuid.uuid4().hex[:10]}",
                "source": id_map.get(e["source"], e["source"]),
                "target": id_map.get(e["target"], e["target"]),
                "label": e.get("label", ""),
            })

        design_graph["nodes"] = design_graph.get("nodes", []) + new_nodes
        design_graph["edges"] = design_graph.get("edges", []) + new_edges

        conn.execute(
            "UPDATE aadc_designs SET graph_json=?, updated_at=? WHERE id=?",
            (json.dumps(design_graph), _utcnow(), design_id),
        )
        conn.commit()
        return jsonify({"status": "inserted", "graph_json": json.dumps(design_graph),
                        "added_nodes": len(new_nodes), "added_edges": len(new_edges)})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Artifacts API
# ---------------------------------------------------------------------------

@aadc_bp.route("/api/designs/<design_id>/artifacts", methods=["GET"])
def list_artifacts(design_id: str):
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM aadc_artifacts WHERE design_id=? ORDER BY created_at DESC",
            (design_id,)
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@aadc_bp.route("/api/designs/<design_id>/artifacts", methods=["POST"])
def generate_artifact(design_id: str):
    data = request.get_json(force=True) or {}
    artifact_type = data.get("type", "model_card")

    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM aadc_designs WHERE id=?", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        d = dict(row)
        assessment_row = conn.execute(
            "SELECT * FROM aadc_assessments WHERE design_id=? ORDER BY created_at DESC LIMIT 1",
            (design_id,)
        ).fetchone()
        assessment = dict(assessment_row) if assessment_row else {}
    finally:
        conn.close()

    graph = json.loads(d.get("graph_json", '{"nodes":[],"edges":[]}'))
    nodes = graph.get("nodes", [])
    now = _utcnow()

    if artifact_type == "model_card":
        llm_nodes = [n for n in nodes if n.get("type") in {"llm", "llm-local", "fine-tuned-adapter"}]
        content = {
            "artifact_type": "model_card",
            "design_id": design_id,
            "design_name": d["name"],
            "models": [{"id": n["id"], "label": n.get("label", ""), "type": n.get("type")}
                       for n in llm_nodes],
            "nist_rmf_score": assessment.get("nist_rmf_score", 0),
            "autonomy_max": d.get("autonomy_max", 0),
            "generated_at": now,
        }
        md = (f"# Model Card — {d['name']}\n\n"
              f"**Design ID:** {design_id}  \n"
              f"**Generated:** {now}  \n\n"
              f"## Models in Design\n" +
              "\n".join(f"- {n.get('label', n['id'])} ({n.get('type')})" for n in llm_nodes) +
              f"\n\n## NIST AI RMF Score\n{assessment.get('nist_rmf_score', 0):.1f}%\n\n"
              f"## Autonomy Level (Max)\nL{d.get('autonomy_max', 0)}\n")
        title = f"Model Card — {d['name']}"

    elif artifact_type == "system_card":
        content = {
            "artifact_type": "system_card",
            "design_id": design_id,
            "design_name": d["name"],
            "domain": d.get("domain", ""),
            "classification": d.get("classification", "CUI"),
            "node_count": len(nodes),
            "safety_impacting": bool(d.get("safety_impacting")),
            "rights_impacting": bool(d.get("rights_impacting")),
            "nist_rmf_score": assessment.get("nist_rmf_score", 0),
            "owasp_score": assessment.get("owasp_score", 0),
            "generated_at": now,
        }
        md = (f"# System Card — {d['name']}\n\n"
              f"**Domain:** {d.get('domain', 'N/A')}  \n"
              f"**Classification:** {d.get('classification', 'CUI')}  \n"
              f"**Safety-Impacting:** {'Yes' if d.get('safety_impacting') else 'No'}  \n"
              f"**Rights-Impacting:** {'Yes' if d.get('rights_impacting') else 'No'}  \n\n"
              f"## Compliance Scores\n"
              f"- NIST AI RMF: {assessment.get('nist_rmf_score', 0):.1f}%\n"
              f"- OWASP LLM Top 10: {assessment.get('owasp_score', 0):.1f}%\n")
        title = f"System Card — {d['name']}"

    elif artifact_type == "ai_bom":
        content = {
            "artifact_type": "ai_bom",
            "design_id": design_id,
            "design_name": d["name"],
            "components": [{"id": n["id"], "type": n.get("type"), "label": n.get("label")}
                           for n in nodes],
            "generated_at": now,
        }
        md = (f"# AI Bill of Materials — {d['name']}\n\n"
              f"| Component | Type | Label |\n|-----------|------|-------|\n" +
              "\n".join(f"| {n['id']} | {n.get('type')} | {n.get('label')} |" for n in nodes))
        title = f"AI BOM — {d['name']}"

    else:
        return jsonify({"error": f"unknown artifact type: {artifact_type}"}), 400

    aid = f"art-{_uid()}"
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO aadc_artifacts (id, design_id, artifact_type, title, content_json, content_md, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (aid, design_id, artifact_type, title, json.dumps(content), md, now),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"id": aid, "type": artifact_type, "title": title,
                    "content": content, "markdown": md}), 201
