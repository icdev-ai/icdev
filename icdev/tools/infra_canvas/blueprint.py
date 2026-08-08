# CUI // SP-CTI
"""ICDEV Infrastructure Design Canvas — Flask Blueprint.

Routes for the IDC visual designer, API endpoints for CRUD operations
and compliance assessment.
"""

import base64
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml as _yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

from flask import Blueprint, jsonify, render_template, request

_IDC_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _IDC_DIR.parent.parent
_CONFIG_PATH = _ICDEV_ROOT / "args" / "infra_canvas_config.yaml"


def _load_config() -> dict:
    """Load IDC config from args/infra_canvas_config.yaml."""
    if not _HAS_YAML or not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
        return _yaml.safe_load(_f) or {}


_IDC_CONFIG = _load_config()

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

try:
    from tools.canvas.ai_trace_mixin import record_canvas_decision as _record_decision
except Exception:
    def _record_decision(**_kw): pass  # type: ignore[assignment]

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
            "SELECT id, name, category, description, tags FROM idc_templates ORDER BY category, name"
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
                "SELECT name, graph_json FROM idc_templates WHERE id = %s",
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
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (design_id, name, "", json.dumps(graph), template_id, "CUI", now, now),
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
            "SELECT id, name, category, description, tags FROM idc_templates ORDER BY category, name"
        ).fetchall()
        templates = [dict(r) for r in rows]
        return render_template("infra_canvas/templates.html", templates=templates)
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
        return render_template("infra_canvas/assessments.html", assessments=assessments)
    finally:
        conn.close()


@infra_bp.route("/canvas/<design_id>")
def canvas(design_id):
    """Open the infrastructure canvas editor."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM infra_designs WHERE id = %s", (design_id,)).fetchone()
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
        row = conn.execute("SELECT id, name FROM infra_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            from flask import redirect

            return redirect("/infra/")
        design = dict(row)
        return render_template("infra_canvas/remediation.html", design=design)
    finally:
        conn.close()


# ── IDC Twin Dashboard ───────────────────────────────────────────────────────

_CSP_PREFIX_MAP = {
    "aws-": "AWS",
    "az-": "Azure",
    "gcp-": "GCP",
    "oci-": "OCI",
    "ibm-": "IBM",
    "op-": "On-Prem",
    "iac-": "IaC",
}


def _csp_from_node_type(ntype: str) -> str:
    for prefix, csp in _CSP_PREFIX_MAP.items():
        if ntype.startswith(prefix):
            return csp
    return "Other"


def _parse_csp_counts(graph_json: str) -> dict:
    counts: dict[str, int] = {}
    try:
        graph = json.loads(graph_json)
        for node in graph.get("nodes", []):
            csp = _csp_from_node_type(node.get("type", ""))
            counts[csp] = counts.get(csp, 0) + 1
    except (json.JSONDecodeError, TypeError):
        pass
    return counts


@infra_bp.route("/twin")
def twin_dashboard():
    """IDC Twin dashboard — latest snapshot per project with CSP resource counts and violations."""
    conn = _get_conn()
    try:
        designs = conn.execute(
            "SELECT id, name, description, classification, updated_at "
            "FROM infra_designs ORDER BY updated_at DESC"
        ).fetchall()

        projects = []
        for row in designs:
            d = dict(row)
            design_id = d["id"]

            snap_row = conn.execute(
                "SELECT id, version_number, change_summary, created_at, graph_json "
                "FROM idc_versions WHERE design_id = %s ORDER BY version_number DESC LIMIT 1",
                (design_id,),
            ).fetchone()

            if snap_row:
                snap = dict(snap_row)
                graph_json = snap.pop("graph_json", "{}")
            else:
                snap = None
                base = conn.execute(
                    "SELECT graph_json FROM infra_designs WHERE id = %s", (design_id,)
                ).fetchone()
                graph_json = dict(base)["graph_json"] if base else "{}"

            csp_counts = _parse_csp_counts(graph_json)

            assessment_row = conn.execute(
                "SELECT findings_json, score, created_at FROM idc_assessments "
                "WHERE design_id = %s ORDER BY created_at DESC LIMIT 1",
                (design_id,),
            ).fetchone()

            violations: list = []
            score = None
            if assessment_row:
                ar = dict(assessment_row)
                score = ar.get("score")
                try:
                    findings = json.loads(ar.get("findings_json", "[]"))
                    violations = [f for f in findings if isinstance(f, dict)]
                except (json.JSONDecodeError, TypeError):
                    pass

            projects.append({
                "design": d,
                "snapshot": snap,
                "csp_counts": csp_counts,
                "violations": violations,
                "violation_count": len(violations),
                "score": score,
            })

        return render_template("infra_canvas/twin.html", projects=projects)
    finally:
        conn.close()


# ── IaC Emit Page ─────────────────────────────────────────────────────────────

# Valid combinations copied from emit.py constants
_EMIT_TARGETS = ("terraform", "pulumi", "ansible", "helm")
_EMIT_CSPS = ("aws", "azure", "gcp", "oci", "on-prem")
_EMIT_SUPPORTED_CSPS: dict[str, frozenset[str]] = {
    "terraform": frozenset({"aws", "gcp", "oci"}),
    "pulumi":    frozenset({"aws", "azure"}),
    "ansible":   frozenset(_EMIT_CSPS),
    "helm":      frozenset(_EMIT_CSPS),
}


@infra_bp.route("/emit")
def emit_page():
    """IaC Emit page — form to generate IaC from a design or inline graph JSON."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name FROM infra_designs ORDER BY updated_at DESC"
        ).fetchall()
        designs = [dict(r) for r in rows]
    finally:
        conn.close()
    return render_template(
        "infra_canvas/emit.html",
        designs=designs,
        targets=list(_EMIT_TARGETS),
        csps=list(_EMIT_CSPS),
        supported_csps={t: sorted(v) for t, v in _EMIT_SUPPORTED_CSPS.items()},
    )


@infra_bp.route("/emit/run", methods=["POST"])
def emit_run():
    """Execute IaC emission and return generated file contents as JSON.

    Request JSON:
      project  — design ID (DB lookup) or raw graph JSON string
      target   — terraform | pulumi | ansible | helm
      csp      — aws | azure | gcp | oci | on-prem

    Response JSON (200):
      {
        "target": "terraform",
        "csp": "aws",
        "files": {"main.tf": "<content>"},
        "skipped_count": 0,
        "node_count": 1
      }

    Error responses:
      400 — missing required field
      422 — unsupported target/CSP combination
      500 — emitter error
    """
    import tempfile
    from pathlib import Path as _Path

    body = request.get_json(force=True) or {}

    project = body.get("project")
    target = body.get("target")
    csp = body.get("csp")

    # Validate required fields
    if not project:
        return jsonify({"error": "project is required"}), 400
    if not target:
        return jsonify({"error": "target is required"}), 400
    if not csp:
        return jsonify({"error": "csp is required"}), 400

    # Validate target/CSP combination
    supported = _EMIT_SUPPORTED_CSPS.get(target, frozenset())
    if csp not in supported:
        return jsonify({
            "error": f"CSP {csp!r} is not supported for target {target!r}. "
                     f"Supported: {sorted(supported)}",
        }), 422

    # Parse graph — project may be a raw JSON string or a design ID
    try:
        graph = json.loads(project)
    except (json.JSONDecodeError, TypeError):
        # Try DB lookup
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT graph_json FROM infra_designs WHERE id = %s", (project,)
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return jsonify({"error": f"Design {project!r} not found"}), 404
        graph = json.loads(row["graph_json"])

    nodes = graph.get("nodes", [])

    # Emit into a temp directory, then read back file contents
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = _Path(tmp)
        try:
            if target == "terraform":
                from tools.infra_canvas.emitters.terraform import (
                    UnsupportedResourceError, emit_resource,
                )
                csp_key_map = {"aws": "aws-govcloud", "gcp": "gcp", "oci": "oci"}
                csp_key = csp_key_map[csp]
                blocks, skipped = [], 0
                for node in nodes:
                    try:
                        blocks.append(emit_resource(node, csp_key))
                    except UnsupportedResourceError:
                        skipped += 1
                if not blocks:
                    return jsonify({"error": "No terraform-emittable nodes found"}), 422
                content = "\n\n".join(blocks)
                (out_dir / "main.tf").write_text(content, encoding="utf-8", newline="")
                file_names = ["main.tf"]

            elif target == "ansible":
                from tools.infra_canvas.emitters.ansible import (
                    UnsupportedResourceError, emit_playbook,
                )
                ok_types = {"user", "package", "service", "file", "lineinfile"}
                good = [n for n in nodes if n.get("type") in ok_types]
                skipped = len(nodes) - len(good)
                if not good:
                    return jsonify({"error": "No Ansible-compatible nodes found"}), 422
                content = emit_playbook(good)
                (out_dir / "playbook.yaml").write_text(content, encoding="utf-8", newline="")
                file_names = ["playbook.yaml"]

            elif target == "pulumi":
                from tools.infra_canvas.emitters.pulumi import (
                    UnsupportedResourceError, emit_resource,
                )
                csp_key_map = {"aws": "aws-govcloud", "azure": "azure-government"}
                csp_key = csp_key_map[csp]
                blocks, skipped = [], 0
                for node in nodes:
                    try:
                        blocks.append(emit_resource(node, csp_key))
                    except UnsupportedResourceError:
                        skipped += 1
                if not blocks:
                    return jsonify({"error": "No pulumi-emittable nodes found"}), 422
                aws_import = 'import * as aws from "@pulumi/aws";' if csp == "aws" else ""
                az_import = (
                    'import * as azure_native from "@pulumi/azure-native";'
                    if csp == "azure" else ""
                )
                header = "\n".join(line for line in [aws_import, az_import] if line)
                body_text = "\n\n".join(blocks)
                content = f"{header}\n\n{body_text}" if header else body_text
                (out_dir / "index.ts").write_text(content, encoding="utf-8", newline="")
                file_names = ["index.ts"]

            else:  # helm
                from tools.infra_canvas.emitters.helm import (
                    UnsupportedResourceError, emit_chart,
                )
                ok_types = {
                    "k8s-deployment", "k8s-service", "k8s-configmap",
                    "k8s-secret", "k8s-hpa",
                }
                good = [n for n in nodes if n.get("type") in ok_types]
                skipped = len(nodes) - len(good)
                if not good:
                    return jsonify({"error": "No Helm-compatible nodes found"}), 422
                files_map = emit_chart(good, chart_name="icdev-infra")
                file_names = []
                for fname, fcontent in files_map.items():
                    fpath = out_dir / fname
                    fpath.parent.mkdir(parents=True, exist_ok=True)
                    fpath.write_text(fcontent, encoding="utf-8", newline="")
                    file_names.append(fname)

        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        # Read file contents to return as JSON tabs
        files = {}
        for fname in file_names:
            try:
                files[fname] = (out_dir / fname).read_text(encoding="utf-8")
            except Exception:
                files[fname] = ""

    return jsonify({
        "target": target,
        "csp": csp,
        "node_count": len(nodes),
        "emitted_count": len(nodes) - skipped,
        "skipped_count": skipped,
        "files": files,
    })


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
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
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
    finally:
        conn.close()
    # Hook: refresh IDC KG so /ask reflects the new design immediately
    from tools.knowledge_graph.canvas_ask import reindex_canvas_on_save
    reindex_canvas_on_save("idc")
    return jsonify({"status": "created", "id": design_id}), 201


@infra_bp.route("/api/designs/<design_id>", methods=["GET"])
def get_design(design_id):
    """Get a design by ID."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM infra_designs WHERE id = %s", (design_id,)).fetchone()
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
            "UPDATE infra_designs SET name = %s, description = %s, "
            "graph_json = %s, classification = %s, updated_at = %s "
            "WHERE id = %s",
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

    # Hook: refresh IDC KG so /ask reflects the edit immediately
    from tools.knowledge_graph.canvas_ask import reindex_canvas_on_save
    reindex_canvas_on_save("idc")

    # Cross-canvas trigger: auto-assess for security gaps
    try:
        from tools.security_canvas.agent import on_idc_design_saved

        on_idc_design_saved(design_id)
    except Exception:
        pass

    # Incremental KG update: re-extract only if graph_json changed
    try:
        from tools.canvas.kg_builder import rebuild_canvas_kg

        rebuild_canvas_kg("idc", design_id)
    except Exception:
        pass

    # Blockchain provenance
    try:
        from tools.canvas.provenance import register_canvas_provenance
        register_canvas_provenance(
            canvas_key="idc",
            design_id=design_id,
            graph_json=data.get("graph", {}),
            project_id=data.get("project_id", ""),
        )
    except Exception:
        pass

    return jsonify({"status": "saved", "id": design_id})


_IDC_CHILD_TABLES = ("idc_collab_sessions", "idc_assessments", "idc_versions", "idc_audit")


@infra_bp.route("/api/designs/<design_id>", methods=["DELETE"])
def delete_design(design_id):
    """Delete an infrastructure design and cascade child records."""
    conn = _get_conn()
    try:
        for tbl in _IDC_CHILD_TABLES:
            conn.execute(f"DELETE FROM {tbl} WHERE design_id=%s", (design_id,))  # nosec B608
        conn.execute("DELETE FROM infra_designs WHERE id=%s", (design_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"deleted": design_id})


@infra_bp.route("/api/designs", methods=["DELETE"])
def delete_all_designs():
    """Delete all infrastructure designs and cascade child records."""
    conn = _get_conn()
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM infra_designs").fetchall()]
        for did in ids:
            for tbl in _IDC_CHILD_TABLES:
                conn.execute(f"DELETE FROM {tbl} WHERE design_id=%s", (did,))  # nosec B608
            conn.execute("DELETE FROM infra_designs WHERE id=%s", (did,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"deleted": len(ids)})


@infra_bp.route("/api/designs/<design_id>/assess", methods=["POST"])
def run_assessment(design_id):
    """Run compliance assessment on a design."""
    data = request.get_json(force=True, silent=True) or {}
    use_cot = data.get("use_cot", False)
    chain_mode = "cot" if use_cot else ""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT graph_json FROM infra_designs WHERE id = %s", (design_id,)).fetchone()
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
            "created_at) VALUES (%s,%s,%s,%s,%s,%s)",
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
        _record_decision(
            canvas_type="idc",
            record_id=design_id,
            decision_type="compliance_finding",
            decision=f"Score {result.get('score',0)} — {len(result.get('findings',[]))} finding(s)",
            rationale=f"CSP coverage: {csp_info}",
            model_used=None,
        )
        result["chain_mode"] = chain_mode
        return jsonify(result)
    finally:
        conn.close()


@infra_bp.route("/api/designs/<design_id>/auto-fix", methods=["POST"])
def run_auto_fix(design_id):
    """Auto-remediate a design by injecting missing compliance nodes.

    Runs assessment to get current findings, calls the shared auto-remediation
    engine to inject missing nodes/edges, saves the updated graph, re-assesses,
    and returns {fixes_applied, old_score, new_score, nodes_added}.
    """
    from tools.canvas.auto_remediate import auto_remediate_idc

    conn = _get_conn()
    try:
        row = conn.execute("SELECT graph_json FROM infra_designs WHERE id = %s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404

        graph = json.loads(row["graph_json"])
        old_result = assess_infra_design(graph)
        old_score = old_result["score"]

        fix_result = auto_remediate_idc(design_id, graph, old_result["findings"])

        if fix_result.get("rate_limited"):
            return jsonify({"error": "Rate limit reached (max 5 auto-fixes/hour)"}), 429

        # Save updated graph
        conn.execute(
            "UPDATE infra_designs SET graph_json = %s, updated_at = %s WHERE id = %s",
            (json.dumps(graph), _utcnow(), design_id),
        )
        conn.commit()

        # Re-assess to compute new score
        new_result = assess_infra_design(graph)
        new_score = new_result["score"]

        return jsonify(
            {
                "status": "ok",
                "design_id": design_id,
                "fixes_applied": fix_result["fixes_applied"],
                "old_score": old_score,
                "new_score": new_score,
                "nodes_added": fix_result["nodes_added"],
                "edges_added": fix_result["edges_added"],
            }
        )
    finally:
        conn.close()


@infra_bp.route("/api/templates", methods=["GET"])
def list_templates():
    """List available IDC templates."""
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT id, name, category, description, tags FROM idc_templates ORDER BY name").fetchall()
        return jsonify({"templates": [dict(r) for r in rows]})
    finally:
        conn.close()


@infra_bp.route("/api/snippets", methods=["GET"])
def list_snippets():
    """List available IDC snippets (reusable graph fragments)."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, category, description, graph_json, tags FROM idc_snippets ORDER BY category, name"
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
            "FROM idc_versions WHERE design_id=%s ORDER BY version_number DESC",
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
            "SELECT graph_json FROM infra_designs WHERE id=%s",
            (design_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "Design not found"}), 404
        raw = row["graph_json"]
        try:
            current_graph = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            current_graph = {}
        ver_num = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 FROM idc_versions WHERE design_id=%s",
            (design_id,),
        ).fetchone()[0]
        change_summary = data.get("change_summary", "")
        if not change_summary:
            prev = conn.execute(
                "SELECT graph_json FROM idc_versions WHERE design_id=%s ORDER BY version_number DESC LIMIT 1",
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
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                ver_id,
                design_id,
                ver_num,
                json.dumps(current_graph) if isinstance(current_graph, dict) else str(raw),
                change_summary,
                data.get("user_id", ""),
                now,
            ),
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
            "SELECT graph_json, version_number FROM idc_versions WHERE id=%s AND design_id=%s",
            (version_id, design_id),
        ).fetchone()
        if not ver:
            return jsonify({"error": "Version not found"}), 404
        now = _utcnow()
        conn.execute(
            "UPDATE infra_designs SET graph_json=%s, updated_at=%s WHERE id=%s",
            (ver["graph_json"], now, design_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(
        {"id": design_id, "restored_version": version_id, "version_number": ver["version_number"], "updated_at": now}
    )


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
            "SELECT graph_json, version_number FROM idc_versions WHERE id=%s AND design_id=%s",
            (ver_a_id, design_id),
        ).fetchone()
        ver_b = conn.execute(
            "SELECT graph_json, version_number FROM idc_versions WHERE id=%s AND design_id=%s",
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
    return jsonify(
        {
            "version_a": {"id": ver_a_id, "version_number": ver_a["version_number"]},
            "version_b": {"id": ver_b_id, "version_number": ver_b["version_number"]},
            "summary": summary,
        }
    )


@infra_bp.route("/api/equivalents", methods=["GET"])
def get_equivalents():
    """Get CSP equivalents for a node type."""
    node_type = request.args.get("type", "")
    target_csp = request.args.get("csp", "")
    if not node_type or not target_csp:
        return jsonify({"error": "type and csp params required"}), 400
    suggestions = suggest_equivalents(node_type, target_csp)
    return jsonify({"node_type": node_type, "target_csp": target_csp, "equivalents": suggestions})


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
            "SELECT name, graph_json FROM infra_designs WHERE id = %s",
            (design_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = dict(row)
        graph = json.loads(d["graph_json"])
        from tools.network.visio_export import export_vsdx

        vsdx_bytes = export_vsdx(d["name"], graph)
        encoded = base64.b64encode(vsdx_bytes).decode("ascii")
        return jsonify(
            {
                "format": "vsdx",
                "filename": d["name"].replace(" ", "_"),
                "data": encoded,
            }
        )
    finally:
        conn.close()


def _idc_fetch_design(design_id: str):
    """Return (name, graph) for an infra design or (None, None) if not found."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT name, graph_json FROM infra_designs WHERE id = %s",
            (design_id,),
        ).fetchone()
        if not row:
            return None, None
        d = dict(row)
        graph = json.loads(d["graph_json"]) if isinstance(d["graph_json"], str) else d["graph_json"]
        return d["name"], graph
    finally:
        conn.close()


@infra_bp.route("/api/export/<design_id>/json", methods=["POST"])
def idc_export_json(design_id):
    """Export infrastructure design as JSON."""
    name, graph = _idc_fetch_design(design_id)
    if name is None:
        return jsonify({"error": "Not found"}), 404
    from tools.canvas.export_utils import export_json

    data = base64.b64encode(export_json(name, graph, "IDC")).decode("ascii")
    return jsonify({"format": "json", "filename": f"{name.replace(' ', '_')}.json", "data": data})


@infra_bp.route("/api/export/<design_id>/markdown", methods=["POST"])
def idc_export_markdown(design_id):
    """Export infrastructure design as Markdown."""
    name, graph = _idc_fetch_design(design_id)
    if name is None:
        return jsonify({"error": "Not found"}), 404
    from tools.canvas.export_utils import export_markdown

    data = base64.b64encode(export_markdown(name, graph, "IDC")).decode("ascii")
    return jsonify({"format": "markdown", "filename": f"{name.replace(' ', '_')}.md", "data": data})


@infra_bp.route("/api/export/<design_id>/csv", methods=["POST"])
def idc_export_csv(design_id):
    """Export infrastructure design node inventory as CSV."""
    name, graph = _idc_fetch_design(design_id)
    if name is None:
        return jsonify({"error": "Not found"}), 404
    from tools.canvas.export_utils import export_csv

    data = base64.b64encode(export_csv(name, graph, "IDC")).decode("ascii")
    return jsonify({"format": "csv", "filename": f"{name.replace(' ', '_')}.csv", "data": data})


@infra_bp.route("/api/export/<design_id>/drawio", methods=["POST"])
def idc_export_drawio(design_id):
    """Export infrastructure design as DrawIO XML."""
    name, graph = _idc_fetch_design(design_id)
    if name is None:
        return jsonify({"error": "Not found"}), 404
    from tools.canvas.export_utils import export_drawio

    data = base64.b64encode(export_drawio(name, graph, "IDC")).decode("ascii")
    return jsonify({"format": "drawio", "filename": f"{name.replace(' ', '_')}.drawio", "data": data})


@infra_bp.route("/api/export/<design_id>/svg", methods=["POST"])
def idc_export_svg(design_id):
    """Export infrastructure design as SVG vector graphic."""
    name, graph = _idc_fetch_design(design_id)
    if name is None:
        return jsonify({"error": "Not found"}), 404
    from tools.canvas.export_utils import export_svg

    data = base64.b64encode(export_svg(name, graph, "IDC")).decode("ascii")
    return jsonify({"format": "svg", "filename": f"{name.replace(' ', '_')}.svg", "data": data})


@infra_bp.route("/api/export/<design_id>/terraform", methods=["POST"])
def idc_export_terraform(design_id):
    """Export infrastructure design as Terraform HCL."""
    name, graph = _idc_fetch_design(design_id)
    if name is None:
        return jsonify({"error": "Not found"}), 404
    from tools.infra_canvas.iac_generator import generate_terraform

    hcl = generate_terraform(graph)
    data = base64.b64encode(hcl.encode("utf-8")).decode("ascii")
    return jsonify({"format": "terraform", "filename": f"{name.replace(' ', '_')}.tf", "data": data})


@infra_bp.route("/api/export/<design_id>/cloudformation", methods=["POST"])
def idc_export_cloudformation(design_id):
    """Export infrastructure design as CloudFormation YAML."""
    name, graph = _idc_fetch_design(design_id)
    if name is None:
        return jsonify({"error": "Not found"}), 404
    from tools.infra_canvas.iac_generator import generate_cloudformation

    cf_yaml = generate_cloudformation(graph)
    data = base64.b64encode(cf_yaml.encode("utf-8")).decode("ascii")
    return jsonify({"format": "cloudformation", "filename": f"{name.replace(' ', '_')}.yaml", "data": data})


@infra_bp.route("/api/export/<design_id>/pulumi", methods=["POST"])
def idc_export_pulumi(design_id):
    """Export infrastructure design as a Pulumi Python program."""
    name, graph = _idc_fetch_design(design_id)
    if name is None:
        return jsonify({"error": "Not found"}), 404
    from tools.infra_canvas.iac_generator import generate_pulumi

    py_src = generate_pulumi(graph)
    data = base64.b64encode(py_src.encode("utf-8")).decode("ascii")
    slug = name.replace(" ", "_")
    return jsonify({"format": "pulumi", "filename": f"{slug}__main__.py", "data": data})


@infra_bp.route("/api/export/<design_id>/ansible", methods=["POST"])
def idc_export_ansible(design_id):
    """Export infrastructure design as an Ansible site.yml playbook."""
    name, graph = _idc_fetch_design(design_id)
    if name is None:
        return jsonify({"error": "Not found"}), 404
    from tools.infra_canvas.iac_generator import generate_ansible

    playbook = generate_ansible(graph)
    data = base64.b64encode(playbook.encode("utf-8")).decode("ascii")
    return jsonify({"format": "ansible", "filename": f"{name.replace(' ', '_')}_site.yml", "data": data})


@infra_bp.route("/api/export/<design_id>/helm", methods=["POST"])
def idc_export_helm(design_id):
    """Export infrastructure design as a Helm chart ZIP archive."""
    name, graph = _idc_fetch_design(design_id)
    if name is None:
        return jsonify({"error": "Not found"}), 404
    from tools.infra_canvas.iac_generator import generate_helm

    zip_bytes = generate_helm(graph)
    data = base64.b64encode(zip_bytes).decode("ascii")
    slug = name.replace(" ", "_")
    return jsonify({"format": "helm", "filename": f"{slug}-helm.zip", "data": data})


# ── Collaboration (Task 18) ───────────────────────────────────────────────────

from tools.canvas.collaboration import CanvasCollabManager as _IDCCollabMgr

_idc_collab = _IDCCollabMgr("idc")


@infra_bp.route("/api/collab/<design_id>/join", methods=["POST"])
def idc_collab_join(design_id):
    """Join a collaborative IDC editing session."""
    body = request.json or {}
    user_id = body.get("user_id", str(uuid.uuid4())[:8])
    user_name = body.get("user_name", "")
    return jsonify(_idc_collab.join(design_id, user_id, user_name))


@infra_bp.route("/api/collab/<design_id>/leave", methods=["POST"])
def idc_collab_leave(design_id):
    """Leave an IDC collaborative session."""
    body = request.json or {}
    user_id = body.get("user_id", "")
    _idc_collab.leave(design_id, user_id)
    return jsonify({"ok": True})


@infra_bp.route("/api/collab/<design_id>/push", methods=["POST"])
def idc_collab_push(design_id):
    """Push an operation into an IDC collaborative session."""
    body = request.json or {}
    user_id = body.get("user_id", "")
    op_type = body.get("op_type", "")
    data = body.get("data", {})
    seq = _idc_collab.push(design_id, user_id, op_type, data)
    return jsonify({"seq": seq})


@infra_bp.route("/api/collab/<design_id>/poll", methods=["GET"])
def idc_collab_poll(design_id):
    """Poll for IDC collaborative operations since a sequence number."""
    since = int(request.args.get("since", 0))
    user_id = request.args.get("user_id", "")
    cx = request.args.get("cx")
    cy = request.args.get("cy")
    if user_id and cx is not None and cy is not None:
        _idc_collab.update_cursor(design_id, user_id, float(cx), float(cy))
    ops, participants, latest_seq = _idc_collab.poll(design_id, since)
    return jsonify({"operations": ops, "participants": participants, "latest_seq": latest_seq})


@infra_bp.route("/api/collab/<design_id>/participants", methods=["GET"])
def idc_collab_participants(design_id):
    """Return current participants in an IDC collaborative session."""
    return jsonify({"participants": _idc_collab.get_participants(design_id)})


# ── Cloud Discovery Import ────────────────────────────────────────────────────


@infra_bp.route("/api/import/cloud", methods=["POST"])
def idc_import_cloud():
    """Import existing infrastructure from a cloud inventory JSON payload.

    Accepts:
      - JSON body with 'payload' key containing the cloud resource JSON
      - Optional 'cloud' key: 'aws'|'azure'|'gcp'|'auto' (default: 'auto')
      - Optional 'design_id' key to merge into an existing design
      - Optional 'file_path' key for air-gapped import from local file
      - Optional 'name' / 'description' for new design creation

    Returns the import result and the created/updated design ID.
    """
    from tools.infra_canvas.cloud_import import import_cloud_inventory, import_from_file

    body = request.get_json(force=True, silent=True) or {}
    cloud = body.get("cloud", "auto")

    # Air-gapped: import from local file path
    file_path = body.get("file_path")
    if file_path:
        result = import_from_file(file_path, cloud=cloud)
    else:
        payload = body.get("payload", body.get("data", {}))
        result = import_cloud_inventory(payload, cloud=cloud)

    if result["node_count"] == 0:
        return jsonify({"error": "No resources imported", "warnings": result["warnings"]}), 400

    graph = result["graph"]
    conn = _get_conn()
    try:
        design_id = body.get("design_id")
        if design_id:
            # Merge into existing design
            row = conn.execute("SELECT graph_json FROM infra_designs WHERE id = %s", (design_id,)).fetchone()
            if not row:
                return jsonify({"error": "Design not found"}), 404
            existing = json.loads(row["graph_json"])
            existing_ids = {n["id"] for n in existing.get("nodes", [])}
            new_nodes = [n for n in graph["nodes"] if n["id"] not in existing_ids]
            existing["nodes"].extend(new_nodes)
            conn.execute(
                "UPDATE infra_designs SET graph_json = %s, updated_at = %s WHERE id = %s",
                (json.dumps(existing), _utcnow(), design_id),
            )
            conn.commit()
        else:
            # Create new design from import
            design_id = _gen_id()
            now = _utcnow()
            cloud_label = result["cloud"].upper()
            name = body.get("name") or f"{cloud_label} Import {now[:10]}"
            conn.execute(
                "INSERT INTO infra_designs "
                "(id, name, description, graph_json, classification, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    design_id,
                    name[:200],
                    body.get("description", f"Imported from {cloud_label} cloud inventory"),
                    json.dumps(graph),
                    body.get("classification", "CUI"),
                    now,
                    now,
                ),
            )
            conn.commit()
    finally:
        conn.close()

    return jsonify(
        {
            "status": "imported",
            "design_id": design_id,
            "cloud": result["cloud"],
            "node_count": result["node_count"],
            "imported_at": result["imported_at"],
            "warnings": result["warnings"],
        }
    ), 201


# ── Runbooks ──────────────────────────────────────────────────────────────────

@infra_bp.route("/runbooks")
def runbooks_page():
    """Runbooks management page."""
    from tools.infra_canvas.runbooks import get_all_runbooks
    runbooks = get_all_runbooks()
    return render_template("infra_canvas/runbooks.html", runbooks=runbooks)


@infra_bp.route("/api/runbooks", methods=["GET"])
def api_runbooks_list():
    """List runbooks with optional category/severity filters."""
    from tools.infra_canvas.runbooks import get_all_runbooks
    category = request.args.get("category")
    severity = request.args.get("severity")
    return jsonify(get_all_runbooks(category=category, severity=severity))


@infra_bp.route("/api/runbooks/<runbook_id>", methods=["GET"])
def api_runbook_get(runbook_id):
    """Get a single runbook by ID."""
    from tools.infra_canvas.runbooks import get_runbook_by_id
    rb = get_runbook_by_id(runbook_id)
    if not rb:
        return jsonify({"error": "not found"}), 404
    return jsonify(rb)


@infra_bp.route("/api/runbooks", methods=["POST"])
def api_runbook_create():
    """Create a new runbook."""
    from tools.infra_canvas.runbooks import create_runbook
    rb = create_runbook(request.get_json() or {})
    return jsonify(rb), 201


@infra_bp.route("/api/runbooks/<runbook_id>", methods=["PUT"])
def api_runbook_update(runbook_id):
    """Update an existing runbook."""
    from tools.infra_canvas.runbooks import update_runbook
    rb = update_runbook(runbook_id, request.get_json() or {})
    if not rb:
        return jsonify({"error": "not found"}), 404
    return jsonify(rb)


@infra_bp.route("/api/runbooks/<runbook_id>", methods=["DELETE"])
def api_runbook_delete(runbook_id):
    """Delete a runbook."""
    from tools.infra_canvas.runbooks import delete_runbook
    deleted = delete_runbook(runbook_id)
    return jsonify({"deleted": deleted})


@infra_bp.route("/api/runbooks/<runbook_id>/execute", methods=["POST"])
def api_runbook_execute(runbook_id):
    """Record a runbook execution (increment counter, update timestamp)."""
    from tools.infra_canvas.runbooks import record_execution
    rb = record_execution(runbook_id)
    if not rb:
        return jsonify({"error": "not found"}), 404
    return jsonify(rb)


# ── SOPs ──────────────────────────────────────────────────────────────────────

@infra_bp.route("/sops")
def sops_page():
    """Standard Operating Procedures management page."""
    from tools.infra_canvas.sops import get_all_sops
    sops = get_all_sops()
    return render_template("infra_canvas/sops.html", sops=sops)


@infra_bp.route("/api/sops", methods=["GET"])
def api_sops_list():
    """List SOPs with optional category/status filters."""
    from tools.infra_canvas.sops import get_all_sops
    category = request.args.get("category")
    status = request.args.get("status")
    return jsonify(get_all_sops(category=category, status=status))


@infra_bp.route("/api/sops/<sop_id>", methods=["GET"])
def api_sop_get(sop_id):
    """Get a single SOP by ID."""
    from tools.infra_canvas.sops import get_sop_by_id
    sop = get_sop_by_id(sop_id)
    if not sop:
        return jsonify({"error": "not found"}), 404
    return jsonify(sop)


@infra_bp.route("/api/sops", methods=["POST"])
def api_sop_create():
    """Create a new SOP (starts in draft)."""
    from tools.infra_canvas.sops import create_sop
    sop = create_sop(request.get_json() or {})
    return jsonify(sop), 201


@infra_bp.route("/api/sops/<sop_id>", methods=["PUT"])
def api_sop_update(sop_id):
    """Update an existing SOP's content fields."""
    from tools.infra_canvas.sops import update_sop
    sop = update_sop(sop_id, request.get_json() or {})
    if not sop:
        return jsonify({"error": "not found"}), 404
    return jsonify(sop)


@infra_bp.route("/api/sops/<sop_id>", methods=["DELETE"])
def api_sop_delete(sop_id):
    """Delete a SOP."""
    from tools.infra_canvas.sops import delete_sop
    deleted = delete_sop(sop_id)
    return jsonify({"deleted": deleted})


@infra_bp.route("/api/sops/<sop_id>/transition", methods=["POST"])
def api_sop_transition(sop_id):
    """Advance or revert SOP approval status.

    Body: {"status": "pending_review"|"approved"|"rejected"|"archived", "actor": "..."}
    """
    from tools.infra_canvas.sops import transition_status
    body = request.get_json() or {}
    new_status = body.get("status", "")
    if not new_status:
        return jsonify({"error": "status is required"}), 400
    result = transition_status(sop_id, new_status, actor=body.get("actor", ""))
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


# ── GraphRAG /ask — shared canvas_ask pattern (IDC) ────────────────────────
@infra_bp.route("/ask")
def idc_ask_page():
    return render_template(
        "canvas_ask.html",
        canvas_label="Infrastructure Design Canvas",
        graph_id="idc-designs",
        profile="compliance",
        examples=["terraform", "compute", "storage", "container", "region"],
        api_url="/infra/api/ask",
        home_url="/infra/",
    )


@infra_bp.route("/api/ask", methods=["POST"])
def idc_api_ask():
    from tools.knowledge_graph.canvas_ask import handle_ask_request
    data = request.get_json(silent=True) or {}
    payload = handle_ask_request(
        query=data.get("query", ""),
        graph_id="idc-designs",
        profile="compliance",
        top_k=int(data.get("top_k", 10)),
        narrate=bool(data.get("narrate", False)),
        canvas_label="infrastructure as code",
    )
    status = payload.pop("_status", 200)
    return jsonify(payload), status


@infra_bp.route("/api/iqe-query", methods=["POST"])
def idc_api_iqe_query():
    """IQE structured query — translate NL to IQE and execute against IDC infrastructure data."""
    import logging as _log
    from tools.iqe.nl_to_iqe import nl_to_iqe
    from tools.iqe.parser import IQESyntaxError, parse
    from tools.iqe.executor import execute_query
    import tools.iqe.adapters.infra  # noqa: F401 — registers infra.* collections

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    collections = ["infra.resources", "infra.snapshots"]
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
        _log.getLogger(__name__).warning("IDC IQE query error: %s", exc)
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500


@infra_bp.route("/api/ai-trace")
def idc_api_ai_trace():
    """Return recent AI decisions made by IDC assessment engines."""
    limit = min(int(request.args.get("limit", 50)), 200)
    record_id = request.args.get("record_id")
    try:
        from tools.db.storage import get_connection as _gc
        with _gc() as _conn:
            if record_id:
                rows = _conn.execute(
                    "SELECT * FROM canvas_ai_decisions WHERE canvas_type='idc' AND record_id=%s "
                    "ORDER BY created_at DESC LIMIT %s",
                    (record_id, limit),
                ).fetchall()
            else:
                rows = _conn.execute(
                    "SELECT * FROM canvas_ai_decisions WHERE canvas_type='idc' "
                    "ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                ).fetchall()
        return jsonify({"ok": True, "canvas": "idc", "decisions": [dict(r) for r in rows]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


def _compute_infra_governance(graph_data: dict) -> dict:
    """Infrastructure governance check — ISO 27001 / NIST 800-53 / FedRAMP aligned."""
    from datetime import datetime, timezone as _tz
    nodes = graph_data.get("nodes", [])
    types = [n.get("type", "").lower() for n in nodes]
    labels = [str(n.get("label", "")).lower() for n in nodes]

    def _any(*prefixes):
        return any(any(t.startswith(p) for p in prefixes) for t in types)

    def _label(*kws):
        return any(kw in l for l in labels for kw in kws)

    CHECKS = [
        ("Network Segmentation", "Network Security", "CAT2", _any("aws-vpc","az-vnet","gcp-vpc","oci-vcn") or _label("vpc","vnet","subnet","segment")),
        ("Encryption at Rest (KMS)", "Data Protection", "CAT1", _any("aws-kms","az-keyvault","gcp-kms","oci-vault") or _label("kms","key vault","encrypt")),
        ("TLS / Encryption in Transit", "Data Protection", "CAT1", _label("tls","ssl","certificate","https")),
        ("Identity & Access Management", "Access Control", "CAT1", _any("aws-iam","az-ad","gcp-iam","oci-iam") or _label("iam","rbac","azure ad","identity")),
        ("Load Balancing / HA", "Resilience", "CAT2", _any("aws-elb","aws-alb","az-lb","gcp-lb","oci-lb") or _label("load balanc","elb","alb","nlb","gateway")),
        ("Auto-Scaling", "Resilience", "CAT2", _any("aws-asg","az-vmss") or _label("auto scal","asg","vmss","scale")),
        ("Monitoring & Metrics", "Operations", "CAT2", _any("aws-cw","az-mon","gcp-mon","plt-prom","plt-datadog","plt-grafana") or _label("cloudwatch","monitor","prometheus","grafana","datadog","metrics")),
        ("Centralized Logging", "Operations", "CAT2", _any("aws-ct","aws-cwl","az-la","gcp-log") or _label("cloudtrail","logging","log analytics","stackdriver","elk","splunk")),
        ("Backup & Disaster Recovery", "Resilience", "CAT3", _label("backup","snapshot","replicat","disaster","dr","rto","rpo")),
        ("WAF / DDoS Protection", "Network Security", "CAT1", _any("aws-waf","az-waf","gcp-armor") or _label("waf","ddos","shield","armor")),
        ("Secrets Management", "Access Control", "CAT1", _any("aws-sm","az-keyvault") or _label("secret","vault","credential manager","parameter store")),
        ("Container Security", "Application Security", "CAT2", _any("aws-eks","aws-ecs","az-aks","gcp-gke","oci-oke") or _label("container","kubernetes","k8s","docker","eks","ecs","aks","gke")),
        ("IaC Defined", "Compliance", "CAT2", _any("iac-") or _label("terraform","ansible","cloudformation","bicep","pulumi","iac")),
        ("Multi-AZ / Multi-Region", "Resilience", "CAT2", _label("multi-az","multi-region","availability zone","region","ha","high availability")),
        ("Patch Management", "Operations", "CAT3", _any("aws-ssm") or _label("patch","ssm","systems manager","update","upgrade")),
        ("CDN / Edge Caching", "Performance", "CAT3", _any("aws-cf","az-cdn","gcp-cdn") or _label("cdn","cloudfront","edge","cache")),
        ("Security Groups / NACLs", "Network Security", "CAT1", _label("security group","nacl","acl","firewall","nsg")),
        ("FIPS 140-2/3 Compliance", "Compliance", "CAT1", _label("fips","gov","fedramp","govcloud")),
        ("Cost Tagging & Governance", "Compliance", "CAT3", _label("tag","cost","billing","budget")),
        ("Database Security", "Data Protection", "CAT2", _any("aws-rds","aws-ddb","az-sql","gcp-sql","oci-db") or _label("database","rds","dynamodb","sql","postgres","mysql")),
    ]

    PILLARS = ["Access Control", "Network Security", "Data Protection", "Resilience",
               "Operations", "Application Security", "Compliance", "Performance"]
    WEIGHTS = {"CAT1": 3, "CAT2": 2, "CAT3": 1}
    MATURITY = [
        (0,  "Level 1 — Initial",      "Ad-hoc infrastructure, no formal controls."),
        (30, "Level 2 — Developing",   "Some controls in place but inconsistent."),
        (50, "Level 3 — Defined",      "Core controls standardised and documented."),
        (70, "Level 4 — Managed",      "Measured and monitored with metrics."),
        (85, "Level 5 — Optimised",    "Continuous improvement, automated enforcement."),
    ]

    check_results = []
    total_w = passed_w = 0
    cats: dict = {p: {"passed": 0, "total": 0, "pct": 0} for p in PILLARS}
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

    for p, c in cats.items():
        c["pct"] = round(c["passed"] / c["total"] * 100) if c["total"] else 0

    score = round(passed_w / total_w * 100) if total_w else 0
    grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"

    mat_level = 1
    for threshold, label, desc in MATURITY:
        if score >= threshold:
            mat_level += 1
    mat_level = min(mat_level - 1, 5)
    mat_label, mat_desc = MATURITY[mat_level - 1][1], MATURITY[mat_level - 1][2]

    passed_count = sum(1 for c in check_results if c["status"] == "pass")
    recs = [{"title": c["title"], "pillar": c["pillar"], "priority": c["severity"]}
            for c in check_results if c["status"] == "fail"]
    recs.sort(key=lambda r: {"CAT1": 0, "CAT2": 1, "CAT3": 2}[r["priority"]])

    return {
        "score": score, "grade": grade,
        "maturity": {"level": mat_level, "label": mat_label.split(" — ")[1], "description": mat_desc},
        "checks": check_results, "categories": cats, "recommendations": recs,
        "total_checks": len(CHECKS), "passed_checks": passed_count,
        "assessed_at": datetime.now(_tz.utc).isoformat(),
    }


@infra_bp.route("/api/designs/<design_id>/governance", methods=["POST"])
def idc_api_governance(design_id):
    """Run infrastructure governance framework check."""
    conn = _get_conn()
    row = conn.execute("SELECT graph_json FROM infra_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Not found"}), 404
    try:
        graph_data = json.loads(row["graph_json"]) if isinstance(row["graph_json"], str) else row["graph_json"]
    except (json.JSONDecodeError, TypeError):
        conn.close()
        return jsonify({"error": "Invalid graph data"}), 400

    result = _compute_infra_governance(graph_data)

    assess_id = f"ia-{uuid.uuid4().hex[:10]}"
    conn.execute(
        "INSERT INTO idc_assessments (id, design_id, assessment_type, findings_json, score, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (assess_id, design_id, "governance",
         json.dumps([{"title": c["title"], "severity": c["severity"], "status": c["status"]}
                     for c in result["checks"]]),
         result["score"], _utcnow()),
    )
    conn.commit()
    conn.close()

    # Blockchain provenance for assessment
    try:
        from tools.canvas.provenance import register_canvas_provenance
        register_canvas_provenance(
            canvas_key="idc",
            design_id=design_id,
            assessment_data=result,
            project_id="",
        )
    except Exception:
        pass

    return jsonify(result)


# Register IDC event bus subscriptions at import time
try:
    from tools.infra_canvas import bus_subscriber as _idc_bus
    _idc_bus.register()
except Exception:
    pass
