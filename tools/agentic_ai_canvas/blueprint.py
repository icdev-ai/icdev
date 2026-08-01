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

  POST /agentic-ai/canvas/<id>/export-pdf      export design as PDF

  GET  /agentic-ai/canvas/<id>/versions        list version history
  POST /agentic-ai/canvas/<id>/versions        save explicit version snapshot
  GET  /agentic-ai/canvas/<id>/versions/diff   diff two versions (?v1=N&v2=M)

  GET  /agentic-ai/solutions                   solution packs gallery
  GET  /agentic-ai/quick-start                 quick-start wizard (3-question router)
  POST /agentic-ai/api/solution-packs/<pid>/apply/<did>  apply pack + seed risks
  GET  /agentic-ai/api/solution-packs          list solution packs (JSON)
  GET  /agentic-ai/api/quick-start/recommend   recommend pack from answers (?domain=&goal=&autonomy=)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import uuid
from datetime import datetime, timezone

from flask import Blueprint, Response, abort, g, jsonify, render_template, request, session

from tools.agentic_ai_canvas.constants import AADC_OBJECTS, FRAMEWORK_LABELS, NODE_DESCRIPTIONS
from tools.agentic_ai_canvas.agentic_engine import assess_design
from tools.agentic_ai_canvas import bus_subscriber, workflow
from tools.agentic_ai_canvas.solution_packs import (
    SOLUTION_PACK_RISKS, SOLUTION_PACK_ATLAS,
    recommend_pack,
)
try:
    from tools.agentic_ai_canvas.cost_estimator import estimate_design_cost as _estimate_cost
    from tools.agentic_ai_canvas.iac_generator import generate_deploy_bundle as _gen_iac
except ImportError:
    _estimate_cost = None
    _gen_iac = None

try:
    from tools.canvas.ai_trace_mixin import record_canvas_decision as _record_decision
except Exception:
    def _record_decision(**_kw): pass  # type: ignore[assignment]

logger = get_logger(__name__)

aadc_bp = Blueprint(
    "agentic_ai_canvas",
    __name__,
    url_prefix="/agentic-ai",
    template_folder="../../tools/dashboard/templates",
)

_INIT_DONE = False

# Fail-loud init health. A failure in any init phase previously logged at
# WARNING and then served routes against an uninitialized schema. We now log at
# ERROR and record the failure here so it is surfaced by the health endpoint
# (GET /agentic-ai/api/health) instead of silently degrading.
_INIT_HEALTH: dict[str, object] = {"db": None, "hitl": None, "bus": None}


def _ensure_init() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        return
    try:
        from tools.agentic_ai_canvas.db.init_db import init_db
        init_db()
        _INIT_HEALTH["db"] = "ok"
    except Exception as exc:
        _INIT_HEALTH["db"] = f"error: {exc}"
        logger.error("aadc: DB init error: %s", exc)
    try:
        workflow.seed_hitl_templates()
        _INIT_HEALTH["hitl"] = "ok"
    except Exception as exc:
        _INIT_HEALTH["hitl"] = f"error: {exc}"
        logger.error("aadc: HITL seed error: %s", exc)
    try:
        bus_subscriber.register()
        _INIT_HEALTH["bus"] = "ok"
    except Exception as exc:
        _INIT_HEALTH["bus"] = f"error: {exc}"
        logger.error("aadc: bus register error: %s", exc)
    _INIT_DONE = True


@aadc_bp.route("/api/health", methods=["GET"])
def aadc_health():
    """Report blueprint init health (DB schema / HITL seed / bus register).

    ``healthy`` is False if any init phase failed, so an uninitialized-schema
    condition is observable rather than silently serving broken routes.
    """
    phases = dict(_INIT_HEALTH)
    healthy = all(v == "ok" for v in phases.values())
    status = 200 if healthy else 503
    return jsonify({"initialized": _INIT_DONE, "healthy": healthy, "phases": phases}), status


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


# ---------------------------------------------------------------------------
# Authentication guard (penta-aadc-01)
# ---------------------------------------------------------------------------
#
# This canvas exposes ~130 routes and previously carried no auth of its own;
# the platform's app-level auth hook (tools/dashboard/auth.py) was the only
# barrier, and it (a) keys its API-path 401 branch off the literal "/api/"
# prefix — which this canvas's own "/agentic-ai/api/..." paths do not match —
# and (b) can be bypassed entirely when ICDEV_DASHBOARD_API_KEY is set
# (fail-open-as-admin). That left mass-delete (delete_all_designs) and the
# live LLM/web-search cost endpoints (simulate_cot, simulate_cod,
# run_research_pipeline, run_research_agent) reachable unauthenticated.
#
# Rather than edit 130 decorators, we enforce authentication at the blueprint
# level for every state-changing (non-GET) request plus an explicit set of
# expensive GET endpoints. Read-only page/API GETs keep their existing
# posture (SCOPE: this canvas only; platform-wide default untouched). Demo
# mode (ICDEV_DEMO_MODE) is preserved — the app-level read-only guard already
# blocks writes there, and demo browsing of GET surfaces stays open.
#
# Follows the auth-session pattern in tools/dashboard/auth.py: identity is
# drawn from g.current_user (populated by the app-level before_request), with
# a session-cookie / API-key fallback so the guard is self-sufficient even
# when mounted without the platform auth middleware.

# Expensive GET endpoints that invoke the LLM / web-search and must be
# authenticated despite being GET. Endpoint names are "<blueprint>.<function>".
# The four known live-cost endpoints (simulate_cot, simulate_cod,
# run_research_pipeline, run_research_agent) are POST and are already covered
# by the non-GET rule; this set exists so any future GET cost endpoint can be
# protected by name without a decorator edit.
_AADC_PROTECTED_GET_ENDPOINTS: frozenset = frozenset()

_AADC_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _aadc_request_needs_auth() -> bool:
    """Return True if the current request must be authenticated."""
    if request.method.upper() not in _AADC_SAFE_METHODS:
        return True
    return request.endpoint in _AADC_PROTECTED_GET_ENDPOINTS


def _aadc_current_user():
    """Resolve the authenticated user for this request, or None.

    Prefers ``g.current_user`` (set by the app-level auth hook); falls back to
    the session cookie / API key using the shared dashboard auth module so the
    guard also holds when the blueprint is mounted without platform auth.
    """
    user = getattr(g, "current_user", None)
    if user:
        uid = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
        if uid:
            return user
    try:
        from tools.dashboard import auth as _auth

        uid = session.get("user_id")
        if uid:
            row = _auth.get_user_by_id(uid)
            if row is not None:
                d = dict(row)
                if d.get("status", "active") == "active":
                    return d
        raw_key = _auth._extract_api_key_from_request()
        if raw_key:
            row = _auth.validate_api_key(raw_key)
            if row is not None:
                return dict(row)
    except Exception as exc:
        logger.debug("aadc auth fallback error: %s", exc)
    return None


@aadc_bp.before_request
def _aadc_auth_guard():
    """Enforce authentication on state-changing and expensive-GET requests."""
    if not _aadc_request_needs_auth():
        return None
    if _aadc_current_user() is not None:
        return None
    logger.warning(
        "aadc: unauthenticated %s %s blocked", request.method, request.path
    )
    # 401 for API/JSON callers; 403 for browser form posts to page routes.
    if request.is_json or "/api/" in request.path:
        abort(401)
    abort(403)


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
                "WHERE design_id=%s ORDER BY created_at DESC LIMIT 1", (d["id"],)
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
    if design_id == "new":
        return new_canvas()
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return render_template("404.html"), 404
        design = dict(row)
        assessment_row = conn.execute(
            "SELECT * FROM aadc_assessments WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
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
        design = _row(conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone())
        assessments = [dict(r) for r in conn.execute(
            "SELECT * FROM aadc_assessments WHERE design_id=%s ORDER BY created_at DESC",
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
        design = _row(conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone())
        artifacts = [dict(r) for r in conn.execute(
            "SELECT * FROM aadc_artifacts WHERE design_id=%s ORDER BY created_at DESC",
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
    data = request.get_json(force=True, silent=True) or {}
    did = f"aadc-{_uid()}"
    now = _utcnow()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO aadc_designs "
            "(id, name, description, domain, classification, graph_json, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
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
        row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify(dict(row))
    finally:
        conn.close()


@aadc_bp.route("/api/designs/<design_id>", methods=["PUT"])
def save_design(design_id: str):
    data = request.get_json(force=True, silent=True) or {}
    graph = data.get("graph", {"nodes": [], "edges": []})
    graph_json = json.dumps(graph)
    now = _utcnow()

    conn = _conn()
    try:
        existing = conn.execute(
            "SELECT id FROM aadc_designs WHERE id=%s", (design_id,)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO aadc_designs (id, name, description, domain, classification, "
                "graph_json, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (design_id, data.get("name", "Untitled Design"),
                 data.get("description", ""), data.get("domain", ""),
                 data.get("classification", "CUI"), graph_json, now, now),
            )
        else:
            conn.execute(
                "UPDATE aadc_designs SET name=%s, description=%s, domain=%s, classification=%s, "
                "graph_json=%s, updated_at=%s WHERE id=%s",
                (data.get("name", "Untitled Design"), data.get("description", ""),
                 data.get("domain", ""), data.get("classification", "CUI"),
                 graph_json, now, design_id),
            )
        # Version snapshot
        ver = (conn.execute(
            "SELECT MAX(version_number) FROM aadc_versions WHERE design_id=%s", (design_id,)
        ).fetchone()[0] or 0) + 1
        conn.execute(
            "INSERT INTO aadc_versions (id, design_id, version_number, graph_json, created_at) "
            "VALUES (%s,%s,%s,%s,%s)",
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
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (result["id"], design_id, result["score"], result["nist_rmf_score"],
             result["owasp_score"], result["omb_compliant"], result["autonomy_max"],
             result["safety_impacting"], result["rights_impacting"],
             result["findings_json"], result["atlas_threats"], result["created_at"]),
        )
        conn.execute(
            "UPDATE aadc_designs SET autonomy_max=%s, safety_impacting=%s, rights_impacting=%s, "
            "hitl_required=%s, updated_at=%s WHERE id=%s",
            (result["autonomy_max"], result["safety_impacting"], result["rights_impacting"],
             1 if (result["safety_impacting"] or result["rights_impacting"]) else 0,
             now, design_id),
        )
        conn.commit()
    finally:
        conn.close()

    # Design changed — drop any memoized gate data so scorecard/deploy-gate
    # recompute against the new graph on their next call.
    _invalidate_gate_cache(design_id)

    # Publish event bus
    bus_subscriber.publish_design_saved(
        design_id, bool(result["safety_impacting"]),
        bool(result["rights_impacting"]), result["autonomy_max"]
    )

    # Phase 2 — emit activity event
    try:
        from tools.agentic_ai_canvas.events import emit_event
        emit_event(design_id, "save", metadata={"version": ver})
    except Exception:
        pass

    # Phase 2 — sync agent/tool nodes to MCP registry
    try:
        from tools.agentic_ai_canvas.mcp_sync import sync_design_to_mcp
        mcp_result = sync_design_to_mcp(design_id)
        result["mcp_synced"] = mcp_result.get("synced", 0)
    except Exception:
        result["mcp_synced"] = 0

    # Create HITL workflow if needed
    wf_instance_id = workflow.maybe_create_hitl_instance(
        design_id, bool(result["safety_impacting"]), bool(result["rights_impacting"])
    )

    result["wf_instance_id"] = wf_instance_id
    return jsonify(result)


_DESIGN_CHILD_TABLES = [
    "aadc_audit",
    "aadc_versions",
    "aadc_loop_links",
    "aadc_workflow_links",
    "aadc_artifacts",
    "aadc_assessments",
]


def _delete_design_cascade(conn, design_id: str) -> None:
    for tbl in _DESIGN_CHILD_TABLES:
        conn.execute(f"DELETE FROM {tbl} WHERE design_id=%s", (design_id,))  # nosec B608
    conn.execute("DELETE FROM aadc_designs WHERE id=%s", (design_id,))


@aadc_bp.route("/api/designs/<design_id>", methods=["DELETE"])
def delete_design(design_id: str):
    conn = _conn()
    try:
        _delete_design_cascade(conn, design_id)
        conn.commit()
    finally:
        conn.close()
    _invalidate_gate_cache(design_id)
    return jsonify({"status": "deleted"})


@aadc_bp.route("/api/designs", methods=["DELETE"])
def delete_all_designs():
    conn = _conn()
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM aadc_designs").fetchall()]
        for did in ids:
            _delete_design_cascade(conn, did)
        conn.commit()
    finally:
        conn.close()
    for did in ids:
        _invalidate_gate_cache(did)
    return jsonify({"status": "deleted", "count": len(ids)})


@aadc_bp.route("/api/designs/<design_id>/assess", methods=["POST"])
def run_assessment(design_id: str):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        d = dict(row)
    finally:
        conn.close()

    data = request.get_json(force=True, silent=True) or {}
    use_cot = data.get("use_cot", False)
    use_cod = data.get("use_cod", False)
    chain_mode = "cot" if use_cot else "cod" if use_cod else ""

    meta = {"domain": d.get("domain", ""), "classification": d.get("classification", "CUI"),
            "has_prior_assessment": True, "chain_mode": chain_mode}
    result = assess_design(design_id, d["graph_json"], meta)
    result["chain_mode"] = chain_mode

    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO aadc_assessments "
            "(id, design_id, score, nist_rmf_score, owasp_score, omb_compliant, "
            "autonomy_max, safety_impacting, rights_impacting, findings_json, atlas_threats, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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
    _record_decision(
        canvas_type="aadc",
        record_id=design_id,
        decision_type="risk_score",
        decision=f"Score {result.get('score',0)} — NIST RMF {result.get('nist_rmf_score',0)}, OWASP {result.get('owasp_score',0)}, OMB compliant={result.get('omb_compliant',False)}",
        rationale=f"Autonomy level: {result.get('autonomy_max','?')}, safety_impacting={result.get('safety_impacting',False)}",
        model_used=None,
        confidence=result.get("score", 0) / 100.0 if result.get("score") else None,
    )
    return jsonify(result)


@aadc_bp.route("/api/designs/<design_id>/launch", methods=["POST"])
def launch_design(design_id: str):
    if not workflow.is_approved(design_id):
        return jsonify({"error": "Design requires HITL approval before launch"}), 403

    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        d = dict(row)
    finally:
        conn.close()

    result = workflow.launch_to_kanban(design_id, d["name"], d["graph_json"])
    return jsonify(result)


@aadc_bp.route("/api/designs/<design_id>/simulate-cot", methods=["POST"])
def simulate_cot(design_id: str):
    """Run Chain of Thought reasoning over the design and return the trace."""
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        d = dict(row)
    finally:
        conn.close()

    data = request.get_json(force=True, silent=True) or {}
    prompt = data.get("prompt") or (
        f"Analyze this agentic AI design '{d.get('name', '')}' "
        f"for risks, compliance gaps, and improvement opportunities. "
        f"Domain: {d.get('domain', 'general')}. "
        f"Classification: {d.get('classification', 'CUI')}."
    )

    try:
        from tools.llm.chain_orchestrator import ChainOrchestrator
        from tools.llm.provider import LLMRequest
        orchestrator = ChainOrchestrator()
        req = LLMRequest(
            function="aadc_design_analysis",
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are an expert in agentic AI system design, NIST RMF, and OWASP LLM security.",
        )
        result = orchestrator.invoke_chain_of_thought("aadc_design_analysis", req)
        _record_decision(
            canvas_type="aadc",
            record_id=design_id,
            decision_type="chain_of_thought",
            decision=result.content[:2000],
            rationale=f"CoT trace_id={result.trace_id}, rounds={len(result.rounds)}",
            model_used=result.models_used[-1] if result.models_used else None,
            confidence=result.confidence,
        )
        return jsonify({
            "design_id": design_id,
            "chain_mode": result.chain_mode,
            "trace_id": result.trace_id,
            "content": result.content,
            "rounds": result.rounds,
            "models_used": result.models_used,
            "total_cost_usd": result.total_cost_usd,
            "total_duration_ms": result.total_duration_ms,
            "stop_reason": result.stop_reason,
            "confidence": result.confidence,
        })
    except Exception as exc:
        logger.warning("AADC simulate-cot failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@aadc_bp.route("/api/designs/<design_id>/simulate-cod", methods=["POST"])
def simulate_cod(design_id: str):
    """Run Chain of Debate over the design and return the debate transcript."""
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        d = dict(row)
    finally:
        conn.close()

    data = request.get_json(force=True, silent=True) or {}
    prompt = data.get("prompt") or (
        f"Debate the risks and benefits of deploying this agentic AI design: '{d.get('name', '')}'. "
        f"Domain: {d.get('domain', 'general')}. "
        f"Classification: {d.get('classification', 'CUI')}."
    )

    try:
        from tools.llm.chain_orchestrator import ChainOrchestrator
        from tools.llm.provider import LLMRequest
        orchestrator = ChainOrchestrator()
        req = LLMRequest(
            function="aadc_design_debate",
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are an expert panel evaluating agentic AI system designs for safety and compliance.",
        )
        result = orchestrator.invoke_chain_of_debate("aadc_design_debate", req)
        _record_decision(
            canvas_type="aadc",
            record_id=design_id,
            decision_type="chain_of_debate",
            decision=result.content[:2000],
            rationale=f"CoD trace_id={result.trace_id}, rounds={len(result.rounds)}",
            model_used=result.models_used[-1] if result.models_used else None,
            confidence=result.confidence,
        )
        return jsonify({
            "design_id": design_id,
            "chain_mode": result.chain_mode,
            "trace_id": result.trace_id,
            "content": result.content,
            "rounds": result.rounds,
            "models_used": result.models_used,
            "total_cost_usd": result.total_cost_usd,
            "total_duration_ms": result.total_duration_ms,
            "stop_reason": result.stop_reason,
            "confidence": result.confidence,
        })
    except Exception as exc:
        logger.warning("AADC simulate-cod failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


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
                "SELECT * FROM aadc_templates WHERE category=%s ORDER BY name", (category,)
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
            "SELECT * FROM aadc_templates WHERE id=%s", (template_id,)
        ).fetchone()
        if not tmpl:
            return jsonify({"error": "template not found"}), 404
        t = dict(tmpl)

        existing = conn.execute(
            "SELECT id FROM aadc_designs WHERE id=%s", (design_id,)
        ).fetchone()
        now = _utcnow()
        if existing:
            conn.execute(
                "UPDATE aadc_designs SET graph_json=%s, template_id=%s, updated_at=%s WHERE id=%s",
                (t["graph_json"], template_id, now, design_id),
            )
        else:
            conn.execute(
                "INSERT INTO aadc_designs (id, name, graph_json, template_id, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (design_id, t["name"], t["graph_json"], template_id, now, now),
            )
        conn.commit()
        return jsonify({"status": "applied", "graph_json": t["graph_json"]})
    finally:
        conn.close()


@aadc_bp.route("/api/templates/save", methods=["POST"])
def save_as_template():
    data = request.get_json(force=True, silent=True) or {}
    design_id = data.get("design_id")
    name = data.get("name", "Custom Template")
    category = data.get("category", "custom")

    conn = _conn()
    try:
        if design_id:
            row = conn.execute(
                "SELECT graph_json FROM aadc_designs WHERE id=%s", (design_id,)
            ).fetchone()
            graph_json = row["graph_json"] if row else '{"nodes":[],"edges":[]}'
        else:
            graph_json = json.dumps(data.get("graph", {"nodes": [], "edges": []}))

        tid = f"tpl-{_uid()}"
        conn.execute(
            "INSERT INTO aadc_templates "
            "(id, name, category, description, graph_json, tags, is_builtin, created_by, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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
                "SELECT * FROM aadc_snippets WHERE category=%s ORDER BY name", (category,)
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
    data = request.get_json(force=True, silent=True) or {}
    offset_x = data.get("offset_x", 0)
    offset_y = data.get("offset_y", 0)

    conn = _conn()
    try:
        snp = conn.execute(
            "SELECT * FROM aadc_snippets WHERE id=%s", (snippet_id,)
        ).fetchone()
        if not snp:
            return jsonify({"error": "snippet not found"}), 404

        design_row = conn.execute(
            "SELECT graph_json FROM aadc_designs WHERE id=%s", (design_id,)
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
            "UPDATE aadc_designs SET graph_json=%s, updated_at=%s WHERE id=%s",
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
            "SELECT * FROM aadc_artifacts WHERE design_id=%s ORDER BY created_at DESC",
            (design_id,)
        ).fetchall()
        return jsonify({"artifacts": [dict(r) for r in rows]})
    finally:
        conn.close()


@aadc_bp.route("/api/designs/<design_id>/artifacts", methods=["POST"])
def generate_artifact(design_id: str):
    data = request.get_json(force=True, silent=True) or {}
    artifact_type = data.get("type", "model_card")

    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        d = dict(row)
        assessment_row = conn.execute(
            "SELECT * FROM aadc_assessments WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
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
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (aid, design_id, artifact_type, title, json.dumps(content), md, now),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"id": aid, "type": artifact_type, "title": title,
                    "content": content, "markdown": md}), 201


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------

@aadc_bp.route("/canvas/<design_id>/export-pdf", methods=["POST"])
def export_pdf(design_id: str):
    from tools.agentic_ai_canvas.export_pdf import generate_pdf

    data = request.get_json(force=True, silent=True) or {}
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT classification, name FROM aadc_designs WHERE id=%s", (design_id,)
        ).fetchone()
        classification = row["classification"] if row else "CUI"
        design_name = (row["name"] if row else design_id) or design_id
    finally:
        conn.close()

    try:
        pdf_bytes = generate_pdf(design_id, nodes, edges, classification)
    except Exception as exc:
        logger.error("aadc: PDF export error: %s", exc)
        return jsonify({"error": str(exc)}), 500

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in design_name)
    filename = f"aadc-{safe_name}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Version history API
# ---------------------------------------------------------------------------

@aadc_bp.route("/canvas/<design_id>/versions", methods=["GET"])
def get_versions(design_id: str):
    from tools.agentic_ai_canvas.version_diff import list_versions
    return jsonify({"versions": list_versions(design_id)})


@aadc_bp.route("/canvas/<design_id>/versions", methods=["POST"])
def create_version(design_id: str):
    from tools.agentic_ai_canvas.version_diff import save_version

    data = request.get_json(force=True, silent=True) or {}
    label = data.get("label")

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT graph_json FROM aadc_designs WHERE id=%s", (design_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "design not found"}), 404
        snapshot = row["graph_json"]
    finally:
        conn.close()

    ver = save_version(design_id, snapshot, label)
    return jsonify({"version_number": ver}), 201


@aadc_bp.route("/canvas/<design_id>/versions/diff", methods=["GET"])
def diff_design_versions(design_id: str):
    from tools.agentic_ai_canvas.version_diff import diff_versions

    try:
        v1 = int(request.args.get("v1", ""))
        v2 = int(request.args.get("v2", ""))
    except (TypeError, ValueError):
        return jsonify({"error": "v1 and v2 query params are required integers"}), 400

    try:
        result = diff_versions(design_id, v1, v2)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404

    return jsonify(result)


# ---------------------------------------------------------------------------
# Phase 4 — Checkpoint / fork API
# ---------------------------------------------------------------------------

@aadc_bp.route("/api/designs/<design_id>/checkpoints", methods=["GET"])
def list_checkpoints(design_id: str):
    from tools.agentic_ai_canvas.checkpoint_manager import list_checkpoints as _list
    return jsonify({"checkpoints": _list(design_id)})


@aadc_bp.route("/api/designs/<design_id>/checkpoints", methods=["POST"])
def create_checkpoint(design_id: str):
    from tools.agentic_ai_canvas.checkpoint_manager import save_checkpoint

    data = request.get_json(force=True, silent=True) or {}
    label = data.get("label", "")
    node_id = data.get("node_id", "")

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT graph_json FROM aadc_designs WHERE id=%s", (design_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "design not found"}), 404
        graph_json = row["graph_json"]
    finally:
        conn.close()

    result = save_checkpoint(design_id, graph_json, label=label, node_id=node_id)
    return jsonify(result), 201


@aadc_bp.route("/api/designs/<design_id>/checkpoints/<checkpoint_id>/restore",
               methods=["POST"])
def restore_checkpoint(design_id: str, checkpoint_id: str):
    from tools.agentic_ai_canvas.checkpoint_manager import restore_checkpoint as _restore
    try:
        result = _restore(design_id, checkpoint_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(result)


@aadc_bp.route("/api/designs/<design_id>/checkpoints/<checkpoint_id>/fork",
               methods=["POST"])
def fork_checkpoint(design_id: str, checkpoint_id: str):
    from tools.agentic_ai_canvas.checkpoint_manager import fork_design

    data = request.get_json(force=True, silent=True) or {}
    new_name = data.get("name", "Forked Design")

    try:
        result = fork_design(design_id, checkpoint_id, new_name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(result), 201


@aadc_bp.route("/api/designs/<design_id>/checkpoints/<checkpoint_id>",
               methods=["DELETE"])
def delete_checkpoint(design_id: str, checkpoint_id: str):
    from tools.agentic_ai_canvas.checkpoint_manager import delete_checkpoint as _del
    return jsonify(_del(design_id, checkpoint_id))


# ---------------------------------------------------------------------------
# Phase 4 — Parallel group API
# ---------------------------------------------------------------------------

@aadc_bp.route("/api/designs/<design_id>/parallel-groups", methods=["GET"])
def list_parallel_groups(design_id: str):
    from tools.agentic_ai_canvas.parallel_graph import list_groups
    return jsonify({"groups": list_groups(design_id)})


@aadc_bp.route("/api/designs/<design_id>/parallel-groups", methods=["POST"])
def create_parallel_group(design_id: str):
    from tools.agentic_ai_canvas.parallel_graph import create_group

    data = request.get_json(force=True, silent=True) or {}
    result = create_group(
        design_id,
        node_ids=data.get("node_ids", []),
        label=data.get("label", "Parallel Group"),
        color=data.get("color", "#7e22ce"),
    )
    return jsonify(result), 201


@aadc_bp.route("/api/designs/<design_id>/parallel-groups/<group_id>",
               methods=["PUT"])
def update_parallel_group(design_id: str, group_id: str):
    from tools.agentic_ai_canvas.parallel_graph import update_group

    data = request.get_json(force=True, silent=True) or {}
    try:
        result = update_group(design_id, group_id,
                              node_ids=data.get("node_ids"),
                              label=data.get("label"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(result)


@aadc_bp.route("/api/designs/<design_id>/parallel-groups/<group_id>",
               methods=["DELETE"])
def delete_parallel_group(design_id: str, group_id: str):
    from tools.agentic_ai_canvas.parallel_graph import delete_group
    return jsonify(delete_group(design_id, group_id))


# ---------------------------------------------------------------------------
# Phase 4 — Parallel path validation
# ---------------------------------------------------------------------------

@aadc_bp.route("/api/designs/<design_id>/validate-parallel", methods=["POST"])
def validate_parallel_paths(design_id: str):
    from tools.agentic_ai_canvas.parallel_graph import validate_parallel_paths as _validate

    data = request.get_json(force=True, silent=True) or {}
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    warnings = _validate(nodes, edges)
    return jsonify({"warnings": warnings, "count": len(warnings)})


# ---------------------------------------------------------------------------
# Phase 3 — Safety Redundancy Graph
# ---------------------------------------------------------------------------

@aadc_bp.route("/api/designs/<design_id>/safety-redundancy", methods=["GET"])
def get_safety_redundancy(design_id: str):
    from tools.agentic_ai_canvas.safety_redundancy import analyze_safety_redundancy
    import json as _json

    conn = _conn()
    row = _row(conn.execute("SELECT graph_json FROM aadc_designs WHERE id=%s", (design_id,)).fetchone())
    conn.close()
    if not row:
        return jsonify({"error": "design not found"}), 404

    graph = _json.loads(row.get("graph_json") or '{"nodes":[],"edges":[]}')
    result = analyze_safety_redundancy(graph.get("nodes", []), graph.get("edges", []))

    # Persist snapshot
    conn2 = _conn()
    conn2.execute(
        "INSERT INTO aadc_safety_graphs (id, design_id, score, protected_count, unprotected_count, analysis_json) VALUES (%s,%s,%s,%s,%s,%s)",
        (_uid(), design_id, result["score"], len(result["protected_agents"]),
         len(result["unprotected_agents"]), _json.dumps(result)),
    )
    conn2.commit()
    conn2.close()
    return jsonify(result)


# ---------------------------------------------------------------------------
# Phase 3 — Multi-Agent Coordination Matrix
# ---------------------------------------------------------------------------

@aadc_bp.route("/api/designs/<design_id>/coordination-matrix", methods=["GET"])
def get_coordination_matrix(design_id: str):
    from tools.agentic_ai_canvas.coordination_matrix import build_coordination_matrix
    import json as _json

    conn = _conn()
    row = _row(conn.execute("SELECT graph_json FROM aadc_designs WHERE id=%s", (design_id,)).fetchone())
    conn.close()
    if not row:
        return jsonify({"error": "design not found"}), 404

    graph = _json.loads(row.get("graph_json") or '{"nodes":[],"edges":[]}')
    result = build_coordination_matrix(graph.get("nodes", []), graph.get("edges", []))
    return jsonify(result)


# ---------------------------------------------------------------------------
# Phase 3 — Model Provenance Chain
# ---------------------------------------------------------------------------

@aadc_bp.route("/api/designs/<design_id>/provenance", methods=["GET"])
def get_provenance(design_id: str):
    from tools.agentic_ai_canvas.model_provenance import extract_provenance_chain, get_compliance_flags
    import json as _json

    conn = _conn()
    row = _row(conn.execute("SELECT graph_json FROM aadc_designs WHERE id=%s", (design_id,)).fetchone())
    conn.close()
    if not row:
        return jsonify({"error": "design not found"}), 404

    graph = _json.loads(row.get("graph_json") or '{"nodes":[],"edges":[]}')
    chain = extract_provenance_chain(graph.get("nodes", []))
    flags = get_compliance_flags(chain)
    return jsonify({"chain": chain, "flags": flags, "count": len(chain)})


@aadc_bp.route("/api/designs/<design_id>/nodes/<node_id>/provenance", methods=["PUT"])
def update_node_provenance(design_id: str, node_id: str):
    import json as _json

    data = request.get_json(force=True, silent=True) or {}
    conn = _conn()
    row = _row(conn.execute("SELECT graph_json FROM aadc_designs WHERE id=%s", (design_id,)).fetchone())
    if not row:
        conn.close()
        return jsonify({"error": "design not found"}), 404

    graph = _json.loads(row.get("graph_json") or '{"nodes":[],"edges":[]}')
    nodes = graph.get("nodes", [])
    updated = False
    for n in nodes:
        if n["id"] == node_id:
            props = n.get("props") or {}
            for field in ("model_source", "training_data", "model_version", "model_license"):
                if field in data:
                    props[field] = data[field]
            n["props"] = props
            updated = True
            break

    if not updated:
        conn.close()
        return jsonify({"error": "node not found"}), 404

    graph["nodes"] = nodes
    conn.execute(
        "UPDATE aadc_designs SET graph_json=%s, updated_at=%s WHERE id=%s",
        (_json.dumps(graph), _utcnow(), design_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "node_id": node_id})


# ---------------------------------------------------------------------------
# Phase 3 — Agent Behavior Simulation
# ---------------------------------------------------------------------------

@aadc_bp.route("/api/designs/<design_id>/simulate", methods=["POST"])
def run_simulation(design_id: str):
    from tools.agentic_ai_canvas.simulation_engine import simulate_execution
    import json as _json

    data = request.get_json(force=True, silent=True) or {}
    start_node_id = data.get("start_node_id", "")
    input_payload = data.get("input_payload", {})

    conn = _conn()
    row = _row(conn.execute("SELECT graph_json FROM aadc_designs WHERE id=%s", (design_id,)).fetchone())
    if not row:
        conn.close()
        return jsonify({"error": "design not found"}), 404

    graph = _json.loads(row.get("graph_json") or '{"nodes":[],"edges":[]}')
    result = simulate_execution(
        graph.get("nodes", []),
        graph.get("edges", []),
        start_node_id,
        input_payload,
    )

    sim_id = _uid()
    conn.execute(
        """INSERT INTO aadc_agent_simulations
           (id, design_id, start_node_id, input_payload, trace_json, decisions_json,
            status, steps_count, halted_by, halted_by_label)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (sim_id, design_id, start_node_id, _json.dumps(input_payload),
         _json.dumps(result["trace"]), _json.dumps(result["decisions"]),
         result["status"], result["steps_count"],
         result.get("halted_by", ""), result.get("halted_by_label", "")),
    )
    conn.commit()
    conn.close()
    result["sim_id"] = sim_id
    return jsonify(result)


@aadc_bp.route("/api/designs/<design_id>/simulations", methods=["GET"])
def list_simulations(design_id: str):
    conn = _conn()
    rows = conn.execute(
        "SELECT id, start_node_id, status, steps_count, halted_by, halted_by_label, created_at "
        "FROM aadc_agent_simulations WHERE design_id=%s ORDER BY created_at DESC LIMIT 20",
        (design_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Phase 5 — Risk Register
# ---------------------------------------------------------------------------

@aadc_bp.route("/risks/<design_id>")
def risks_page(design_id: str):
    conn = _conn()
    design = _row(conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone())
    risks = [dict(r) for r in conn.execute(
        "SELECT * FROM aadc_risk_items WHERE design_id=%s ORDER BY created_at DESC", (design_id,)
    ).fetchall()]
    conn.close()
    if not design:
        from flask import abort
        abort(404)
    from tools.agentic_ai_canvas.risk_register import summarize_register, RISK_CATEGORIES, SEVERITY_LEVELS, RISK_STATUSES
    summary = summarize_register(risks)
    return render_template(
        "agentic_ai_canvas/risks.html",
        design=design, risks=risks, summary=summary,
        categories=RISK_CATEGORIES, severity_levels=SEVERITY_LEVELS, risk_statuses=RISK_STATUSES,
    )


@aadc_bp.route("/api/designs/<design_id>/risks", methods=["GET"])
def list_risks(design_id: str):
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM aadc_risk_items WHERE design_id=%s ORDER BY created_at DESC", (design_id,)
    ).fetchall()
    conn.close()
    items = [dict(r) for r in rows]
    from tools.agentic_ai_canvas.risk_register import summarize_register
    return jsonify({"risks": items, "summary": summarize_register(items)})


@aadc_bp.route("/api/designs/<design_id>/risks", methods=["POST"])
def create_risk(design_id: str):
    data = request.get_json(force=True, silent=True) or {}
    rid = _uid()
    now = _utcnow()
    conn = _conn()
    conn.execute(
        """INSERT INTO aadc_risk_items
           (id, design_id, title, description, risk_category, severity, likelihood,
            impact, status, owner, mitigation, finding_id, node_id, created_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (rid, design_id,
         data.get("title", "New Risk"),
         data.get("description", ""),
         data.get("risk_category", "operational"),
         data.get("severity", "MEDIUM"),
         data.get("likelihood", "MEDIUM"),
         data.get("impact", "MEDIUM"),
         data.get("status", "open"),
         data.get("owner", ""),
         data.get("mitigation", ""),
         data.get("finding_id", ""),
         data.get("node_id", ""),
         now, now),
    )
    conn.commit()
    conn.close()
    return jsonify({"id": rid, "ok": True})


@aadc_bp.route("/api/designs/<design_id>/risks/<risk_id>", methods=["PUT"])
def update_risk(design_id: str, risk_id: str):
    data = request.get_json(force=True, silent=True) or {}
    fields = ["title", "description", "risk_category", "severity", "likelihood",
              "impact", "status", "owner", "mitigation"]
    updates = {f: data[f] for f in fields if f in data}
    if not updates:
        return jsonify({"ok": True})
    set_clause = ", ".join(f"{f}=%s" for f in updates)
    conn = _conn()
    conn.execute(
        f"UPDATE aadc_risk_items SET {set_clause}, updated_at=%s WHERE id=%s AND design_id=%s",
        list(updates.values()) + [_utcnow(), risk_id, design_id],
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@aadc_bp.route("/api/designs/<design_id>/risks/<risk_id>", methods=["DELETE"])
def delete_risk(design_id: str, risk_id: str):
    conn = _conn()
    conn.execute("DELETE FROM aadc_risk_items WHERE id=%s AND design_id=%s", (risk_id, design_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@aadc_bp.route("/api/designs/<design_id>/risks/import-findings", methods=["POST"])
def import_findings_as_risks(design_id: str):
    """Convert latest assessment findings into risk items (deduplicated by finding_id)."""
    import json as _json
    from tools.agentic_ai_canvas.risk_register import finding_to_risk

    conn = _conn()
    row = _row(conn.execute("SELECT findings_json FROM aadc_assessments WHERE design_id=%s ORDER BY created_at DESC LIMIT 1", (design_id,)).fetchone())
    if not row:
        conn.close()
        return jsonify({"imported": 0, "message": "No assessment found"})

    findings = _json.loads(row.get("findings_json") or "[]")
    existing_fids = {
        r["finding_id"] for r in conn.execute(
            "SELECT finding_id FROM aadc_risk_items WHERE design_id=%s", (design_id,)
        ).fetchall()
    }

    imported = 0
    now = _utcnow()
    for f in findings:
        fid = f.get("id", "")
        if fid and fid in existing_fids:
            continue
        risk = finding_to_risk(f)
        rid = _uid()
        conn.execute(
            """INSERT INTO aadc_risk_items
               (id, design_id, title, description, risk_category, severity, likelihood,
                impact, status, owner, mitigation, finding_id, node_id, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (rid, design_id, risk["title"], risk["description"], risk["risk_category"],
             risk["severity"], risk["likelihood"], risk["impact"], risk["status"],
             risk["owner"], risk["mitigation"], risk["finding_id"], risk["node_id"], now, now),
        )
        imported += 1

    conn.commit()
    conn.close()
    return jsonify({"imported": imported})


# ---------------------------------------------------------------------------
# Phase 5 — Threat Model
# ---------------------------------------------------------------------------

@aadc_bp.route("/api/designs/<design_id>/threat-model", methods=["POST"])
def generate_threat_model(design_id: str):
    import json as _json
    from tools.agentic_ai_canvas.threat_model import generate_threat_model as _gen

    conn = _conn()
    row = _row(conn.execute("SELECT graph_json FROM aadc_designs WHERE id=%s", (design_id,)).fetchone())
    if not row:
        conn.close()
        return jsonify({"error": "design not found"}), 404

    graph = _json.loads(row.get("graph_json") or '{"nodes":[],"edges":[]}')
    result = _gen(graph.get("nodes", []), graph.get("edges", []))

    # Persist snapshot
    tm_id = _uid()
    conn.execute(
        "INSERT INTO aadc_threat_models (id, design_id, stride_json, atlas_threats, threat_count, high_count) VALUES (%s,%s,%s,%s,%s,%s)",
        (tm_id, design_id, _json.dumps(result["stride"]), _json.dumps(result["atlas"]),
         result["threat_count"], result["high_count"]),
    )
    conn.commit()
    conn.close()
    result["id"] = tm_id
    return jsonify(result)


@aadc_bp.route("/api/designs/<design_id>/threat-model", methods=["GET"])
def get_latest_threat_model(design_id: str):
    import json as _json

    conn = _conn()
    row = _row(conn.execute(
        "SELECT * FROM aadc_threat_models WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
        (design_id,),
    ).fetchone())
    conn.close()
    if not row:
        return jsonify({"stride": [], "atlas": [], "threat_count": 0, "high_count": 0})
    return jsonify({
        **row,
        "stride": _json.loads(row.get("stride_json") or "[]"),
        "atlas": _json.loads(row.get("atlas_threats") or "[]"),
    })


# ---------------------------------------------------------------------------
# Phase 5 — Portfolio Dashboard
# ---------------------------------------------------------------------------

@aadc_bp.route("/api/portfolio", methods=["GET"])
def get_portfolio():
    from tools.agentic_ai_canvas.portfolio import aggregate_portfolio

    conn = _conn()
    designs = [dict(r) for r in conn.execute("SELECT * FROM aadc_designs ORDER BY updated_at DESC").fetchall()]
    assessments = []
    for d in designs:
        row = _row(conn.execute(
            "SELECT * FROM aadc_assessments WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
            (d["id"],),
        ).fetchone())
        if row:
            assessments.append({**row, "design_id": d["id"]})
    risks = [dict(r) for r in conn.execute("SELECT * FROM aadc_risk_items").fetchall()]
    conn.close()
    return jsonify(aggregate_portfolio(designs, assessments, risks))


# ---------------------------------------------------------------------------
# Phase 5 — OSCAL Export
# ---------------------------------------------------------------------------

@aadc_bp.route("/api/designs/<design_id>/oscal", methods=["GET"])
def export_oscal(design_id: str):
    import json as _json
    from tools.agentic_ai_canvas.oscal_export import export_oscal_component

    conn = _conn()
    design = _row(conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone())
    if not design:
        conn.close()
        return jsonify({"error": "design not found"}), 404
    assessment = _row(conn.execute(
        "SELECT * FROM aadc_assessments WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
        (design_id,),
    ).fetchone())
    conn.close()

    graph = _json.loads(design.get("graph_json") or '{"nodes":[],"edges":[]}')
    result = export_oscal_component(dict(design), graph, dict(assessment) if assessment else None)
    return jsonify(result)


@aadc_bp.route("/api/designs/<design_id>/oscal/control-coverage", methods=["GET"])
def get_oscal_coverage(design_id: str):
    import json as _json
    from tools.agentic_ai_canvas.oscal_export import get_control_coverage_summary

    conn = _conn()
    row = _row(conn.execute("SELECT graph_json FROM aadc_designs WHERE id=%s", (design_id,)).fetchone())
    conn.close()
    if not row:
        return jsonify({"error": "design not found"}), 404
    graph = _json.loads(row.get("graph_json") or '{"nodes":[],"edges":[]}')
    return jsonify(get_control_coverage_summary(graph.get("nodes", [])))


# ---------------------------------------------------------------------------
# Phase 2 — Ecosystem Wiring Routes
# ---------------------------------------------------------------------------

@aadc_bp.route("/canvas/<design_id>/events", methods=["POST"])
def canvas_emit_event(design_id: str):
    """Client-side event hook (export-JSON, export-SVG, export-drawio, export-CSV)."""
    data = request.get_json(force=True, silent=True) or {}
    event_type = data.get("event_type", "")
    metadata = data.get("metadata", {})

    valid_types = {
        "save", "export_json", "export_svg", "export_drawio", "export_pdf",
        "export_csv", "assess", "version_save", "node_add", "node_delete",
        "edge_add", "edge_delete", "simulation_run",
    }
    if event_type not in valid_types:
        return jsonify({"error": f"invalid event_type: {event_type}"}), 400

    try:
        from tools.agentic_ai_canvas.events import emit_event
        ok = emit_event(design_id, event_type, metadata=metadata)
    except Exception as exc:
        return jsonify({"status": "skipped", "reason": str(exc)})
    return jsonify({"status": "ok" if ok else "skipped"})


@aadc_bp.route("/canvas/<design_id>/sync-mcp", methods=["POST"])
def canvas_sync_mcp(design_id: str):
    """Manual trigger: sync AADC agent/tool nodes to MCP tool registry."""
    try:
        from tools.agentic_ai_canvas.mcp_sync import sync_design_to_mcp
        result = sync_design_to_mcp(design_id)
    except Exception as exc:
        return jsonify({"synced": 0, "error": str(exc)})
    return jsonify(result)


@aadc_bp.route("/canvas/<design_id>/ft-link", methods=["GET"])
def canvas_ft_link(design_id: str):
    """Export latest assessment as fine-tuning signal dataset row."""
    try:
        from tools.agentic_ai_canvas.ft_linkage import export_assessment_as_ft_signal
        result = export_assessment_as_ft_signal(design_id)
    except Exception as exc:
        return jsonify({"datasets_created": 0, "error": str(exc)})
    return jsonify(result)


@aadc_bp.route("/canvas/<design_id>/kanban-status", methods=["GET"])
def canvas_kanban_status(design_id: str):
    """Return {node_id: {task_id, status, title}} for nodes with kanban_task_id."""
    import json as _json

    conn = _conn()
    row = _row(conn.execute(
        "SELECT graph_json FROM aadc_designs WHERE id=%s", (design_id,)
    ).fetchone())
    conn.close()

    if not row:
        return jsonify({"error": "design not found"}), 404

    try:
        graph = _json.loads(row.get("graph_json") or '{"nodes":[],"edges":[]}')
    except (ValueError, TypeError):
        graph = {"nodes": [], "edges": []}

    # Collect nodes that have metadata.kanban_task_id
    linked: dict[str, str] = {}
    for node in graph.get("nodes", []):
        meta = node.get("metadata") or {}
        task_id = meta.get("kanban_task_id") or node.get("kanban_task_id")
        if task_id:
            linked[node["id"]] = task_id

    if not linked:
        return jsonify({})

    # Look up kanban task statuses
    try:
        from tools.db.storage import get_connection as _main_conn
        mconn = _main_conn()
        result = {}
        for node_id, task_id in linked.items():
            t = mconn.execute(
                "SELECT id, title, status FROM kanban_tasks WHERE id=%s LIMIT 1",
                (task_id,),
            ).fetchone()
            if t:
                result[node_id] = {"task_id": t[0], "title": t[1], "status": t[2]}
            else:
                result[node_id] = {"task_id": task_id, "title": "", "status": "unknown"}
        mconn.close()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)})


# ---------------------------------------------------------------------------
# Phase 6 — ATO Readiness + Regulatory Tracker + Design Compare + Exec Summary
# ---------------------------------------------------------------------------

@aadc_bp.route("/ato/<design_id>", methods=["GET"])
def ato_page(design_id: str):
    import json as _json
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    conn.close()
    if not row:
        return "Design not found", 404
    design = dict(row)
    graph = _json.loads(design.get("graph_json") or '{"nodes":[],"edges":[]}')
    from tools.agentic_ai_canvas.ato_readiness import run_ato_checklist
    result = run_ato_checklist(graph.get("nodes", []), design)
    frameworks = list(result["by_framework"].keys())
    return render_template(
        "agentic_ai_canvas/ato.html",
        design=design,
        result=result,
        frameworks=frameworks,
    )


@aadc_bp.route("/api/designs/<design_id>/ato", methods=["GET"])
def get_ato(design_id: str):
    import json as _json
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "design not found"}), 404
    design = dict(row)
    graph = _json.loads(design.get("graph_json") or '{"nodes":[],"edges":[]}')
    from tools.agentic_ai_canvas.ato_readiness import run_ato_checklist
    result = run_ato_checklist(graph.get("nodes", []), design)
    # Persist latest report
    rid = _uid()
    now = _utcnow()
    s = result["summary"]
    try:
        conn.execute(
            """INSERT INTO aadc_ato_reports (id, design_id, score_pct, ato_ready, passed, failed, critical_failed, report_json, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (rid, design_id, s["score_pct"], 1 if s["ato_ready"] else 0,
             s["passed"], s["failed"], s["critical_failed"], _json.dumps(result), now),
        )
        conn.commit()
    except Exception:
        pass
    conn.close()
    return jsonify(result)


@aadc_bp.route("/api/designs/<design_id>/regulatory", methods=["GET"])
def get_regulatory(design_id: str):
    import json as _json
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "design not found"}), 404
    design = dict(row)
    graph = _json.loads(design.get("graph_json") or '{"nodes":[],"edges":[]}')
    risks = [
        dict(r) for r in conn.execute(
            "SELECT * FROM aadc_risk_items WHERE design_id=%s", (design_id,)
        ).fetchall()
    ]
    from tools.agentic_ai_canvas.regulatory_tracker import run_regulatory_analysis
    result = run_regulatory_analysis(graph.get("nodes", []), design, risks)
    # Persist
    rid = _uid()
    now = _utcnow()
    s = result["summary"]
    try:
        conn.execute(
            """INSERT INTO aadc_regulatory_gaps (id, design_id, score_pct, compliant, gaps, critical_gaps, report_json, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (rid, design_id, s["score_pct"], s["compliant"], s["gaps"], s["critical_gaps"], _json.dumps(result), now),
        )
        conn.commit()
    except Exception:
        pass
    conn.close()
    return jsonify(result)


@aadc_bp.route("/exec-summary/<design_id>", methods=["GET"])
def exec_summary_page(design_id: str):
    import json as _json
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return "Design not found", 404
    design = dict(row)
    graph = _json.loads(design.get("graph_json") or '{"nodes":[],"edges":[]}')
    nodes = graph.get("nodes", [])
    assessment = None
    arow = conn.execute(
        "SELECT * FROM aadc_assessments WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
        (design_id,),
    ).fetchone()
    if arow:
        assessment = dict(arow)
    risks = [dict(r) for r in conn.execute(
        "SELECT * FROM aadc_risk_items WHERE design_id=%s", (design_id,)
    ).fetchall()]
    threat_model = None
    tmrow = conn.execute(
        "SELECT * FROM aadc_threat_models WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
        (design_id,),
    ).fetchone()
    if tmrow:
        threat_model = dict(tmrow)
    conn.close()
    from tools.agentic_ai_canvas.ato_readiness import run_ato_checklist
    from tools.agentic_ai_canvas.regulatory_tracker import run_regulatory_analysis
    from tools.agentic_ai_canvas.exec_summary import generate_exec_summary
    ato_result = run_ato_checklist(nodes, design)
    reg_result = run_regulatory_analysis(nodes, design, risks)
    summary = generate_exec_summary(design, assessment, risks, threat_model, ato_result, reg_result)
    return render_template(
        "agentic_ai_canvas/exec_summary.html",
        design=design,
        summary=summary,
        ato=ato_result,
        reg=reg_result,
    )


@aadc_bp.route("/api/designs/<design_id>/exec-summary", methods=["GET"])
def get_exec_summary(design_id: str):
    import json as _json
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "design not found"}), 404
    design = dict(row)
    graph = _json.loads(design.get("graph_json") or '{"nodes":[],"edges":[]}')
    nodes = graph.get("nodes", [])
    assessment = None
    arow = conn.execute(
        "SELECT * FROM aadc_assessments WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
        (design_id,),
    ).fetchone()
    if arow:
        assessment = dict(arow)
    risks = [dict(r) for r in conn.execute(
        "SELECT * FROM aadc_risk_items WHERE design_id=%s", (design_id,)
    ).fetchall()]
    threat_model = None
    tmrow = conn.execute(
        "SELECT * FROM aadc_threat_models WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
        (design_id,),
    ).fetchone()
    if tmrow:
        threat_model = dict(tmrow)
    conn.close()
    from tools.agentic_ai_canvas.ato_readiness import run_ato_checklist
    from tools.agentic_ai_canvas.regulatory_tracker import run_regulatory_analysis
    from tools.agentic_ai_canvas.exec_summary import generate_exec_summary
    ato_result = run_ato_checklist(nodes, design)
    reg_result = run_regulatory_analysis(nodes, design, risks)
    result = generate_exec_summary(design, assessment, risks, threat_model, ato_result, reg_result)
    return jsonify(result)


@aadc_bp.route("/api/designs/compare", methods=["POST"])
def compare_designs_api():
    data = request.get_json(force=True, silent=True) or {}
    id_a = data.get("design_a_id", "")
    id_b = data.get("design_b_id", "")
    if not id_a or not id_b:
        return jsonify({"error": "design_a_id and design_b_id required"}), 400
    conn = _conn()
    row_a = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (id_a,)).fetchone()
    row_b = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (id_b,)).fetchone()
    if not row_a or not row_b:
        conn.close()
        return jsonify({"error": "one or both designs not found"}), 404
    design_a = dict(row_a)
    design_b = dict(row_b)
    ass_a = conn.execute(
        "SELECT * FROM aadc_assessments WHERE design_id=%s ORDER BY created_at DESC LIMIT 1", (id_a,)
    ).fetchone()
    ass_b = conn.execute(
        "SELECT * FROM aadc_assessments WHERE design_id=%s ORDER BY created_at DESC LIMIT 1", (id_b,)
    ).fetchone()
    risks_a = [dict(r) for r in conn.execute("SELECT * FROM aadc_risk_items WHERE design_id=%s", (id_a,)).fetchall()]
    risks_b = [dict(r) for r in conn.execute("SELECT * FROM aadc_risk_items WHERE design_id=%s", (id_b,)).fetchall()]
    conn.close()
    from tools.agentic_ai_canvas.design_compare import compare_designs
    result = compare_designs(
        design_a, design_b,
        dict(ass_a) if ass_a else None,
        dict(ass_b) if ass_b else None,
        risks_a, risks_b,
    )
    result["design_a"] = {"id": id_a, "name": design_a["name"]}
    result["design_b"] = {"id": id_b, "name": design_b["name"]}
    return jsonify(result)


# ---------------------------------------------------------------------------
# Phase 7 — Red Team, Design Linter, Accreditation Package
# ---------------------------------------------------------------------------

@aadc_bp.route("/red-team/<design_id>", methods=["GET"])
def red_team_page(design_id: str):
    import json as _json
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    conn.close()
    if not row:
        return "Design not found", 404
    design = dict(row)
    graph = _json.loads(design.get("graph_json") or '{"nodes":[],"edges":[]}')
    from tools.agentic_ai_canvas.red_team import run_red_team
    result = run_red_team(graph.get("nodes", []), graph.get("edges", []))
    return render_template(
        "agentic_ai_canvas/red_team.html",
        design=design,
        result=result,
    )


@aadc_bp.route("/api/designs/<design_id>/red-team", methods=["GET"])
def get_red_team(design_id: str):
    import json as _json
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "design not found"}), 404
    design = dict(row)
    graph = _json.loads(design.get("graph_json") or '{"nodes":[],"edges":[]}')
    from tools.agentic_ai_canvas.red_team import run_red_team
    result = run_red_team(graph.get("nodes", []), graph.get("edges", []))
    # Persist
    rid = _uid()
    now = _utcnow()
    s = result["summary"]
    try:
        conn.execute(
            """INSERT INTO aadc_red_team_reports
               (id, design_id, overall_risk, applicable, unmitigated, critical_unmitigated,
                avg_exploitability, report_json, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (rid, design_id, s["overall_risk"], s["applicable"], s["unmitigated"],
             s["critical_unmitigated"], s["avg_exploitability"], _json.dumps(result), now),
        )
        conn.commit()
    except Exception:
        pass
    conn.close()
    return jsonify(result)


@aadc_bp.route("/api/designs/<design_id>/lint", methods=["GET"])
def get_lint(design_id: str):
    import json as _json
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "design not found"}), 404
    design = dict(row)
    graph = _json.loads(design.get("graph_json") or '{"nodes":[],"edges":[]}')
    from tools.agentic_ai_canvas.auto_recommend import lint_design
    result = lint_design(graph.get("nodes", []), graph.get("edges", []), design)
    # Persist
    rid = _uid()
    now = _utcnow()
    s = result["summary"]
    try:
        conn.execute(
            """INSERT INTO aadc_lint_reports
               (id, design_id, lint_score, total_issues, critical_issues, report_json, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (rid, design_id, s["lint_score"], s["total"], s["critical"], _json.dumps(result), now),
        )
        conn.commit()
    except Exception:
        pass
    conn.close()
    return jsonify(result)


@aadc_bp.route("/api/designs/<design_id>/accred-package", methods=["GET"])
def get_accred_package(design_id: str):
    import json as _json
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "design not found"}), 404
    design = dict(row)
    graph = _json.loads(design.get("graph_json") or '{"nodes":[],"edges":[]}')
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    assessment = None
    arow = conn.execute(
        "SELECT * FROM aadc_assessments WHERE design_id=%s ORDER BY created_at DESC LIMIT 1", (design_id,)
    ).fetchone()
    if arow:
        assessment = dict(arow)

    risks = [dict(r) for r in conn.execute(
        "SELECT * FROM aadc_risk_items WHERE design_id=%s", (design_id,)
    ).fetchall()]

    threat_model = None
    tmrow = conn.execute(
        "SELECT * FROM aadc_threat_models WHERE design_id=%s ORDER BY created_at DESC LIMIT 1", (design_id,)
    ).fetchone()
    if tmrow:
        threat_model = dict(tmrow)

    conn.close()

    from tools.agentic_ai_canvas.ato_readiness import run_ato_checklist
    from tools.agentic_ai_canvas.regulatory_tracker import run_regulatory_analysis
    from tools.agentic_ai_canvas.exec_summary import generate_exec_summary
    from tools.agentic_ai_canvas.red_team import run_red_team
    from tools.agentic_ai_canvas.oscal_export import export_oscal_component
    from tools.agentic_ai_canvas.accred_package import build_accred_zip

    ato_data = run_ato_checklist(nodes, design)
    reg_data = run_regulatory_analysis(nodes, design, risks)
    red_team_data = run_red_team(nodes, edges)
    exec_data = generate_exec_summary(design, assessment, risks, threat_model, ato_data, reg_data)
    oscal_data = export_oscal_component(design, graph, assessment)

    zip_bytes = build_accred_zip(
        design, assessment, risks, threat_model,
        ato_data, reg_data, red_team_data, exec_data, oscal_data,
    )

    from flask import make_response
    resp = make_response(zip_bytes)
    resp.headers["Content-Type"] = "application/zip"
    resp.headers["Content-Disposition"] = f"attachment; filename=accred-package-{design_id}.zip"
    return resp


# ---------------------------------------------------------------------------
# PHASE 8 — Design Intelligence & Analytics
# ---------------------------------------------------------------------------

@aadc_bp.route("/patterns/<design_id>", methods=["GET"])
def pattern_analysis_page(design_id: str):
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return "Design not found", 404
    design = dict(row)
    import json as _json
    graph = _json.loads(design.get("graph_json") or '{"nodes":[],"edges":[]}')
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    conn.close()

    from tools.agentic_ai_canvas.pattern_detector import detect_patterns
    result = detect_patterns(nodes, edges)
    return render_template("agentic_ai_canvas/pattern_analysis.html", design=design, result=result)


@aadc_bp.route("/api/designs/<design_id>/patterns", methods=["GET"])
def get_patterns_api(design_id: str):
    import json as _json
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "design not found"}), 404
    design = dict(row)
    graph = _json.loads(design.get("graph_json") or '{"nodes":[],"edges":[]}')
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    from tools.agentic_ai_canvas.pattern_detector import detect_patterns
    result = detect_patterns(nodes, edges)

    rep_id = str(uuid.uuid4())
    try:
        conn.execute(
            "INSERT INTO aadc_pattern_reports (id,design_id,dominant_pattern,pattern_json) VALUES (%s,%s,%s,%s)",
            (rep_id, design_id, result["dominant"], _json.dumps(result)),
        )
        conn.commit()
    except Exception:
        pass
    conn.close()
    return jsonify(result)


@aadc_bp.route("/impact/<design_id>", methods=["GET"])
def impact_analysis_page(design_id: str):
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return "Design not found", 404
    design = dict(row)
    import json as _json
    graph = _json.loads(design.get("graph_json") or '{"nodes":[],"edges":[]}')
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    conn.close()

    from tools.agentic_ai_canvas.impact_analyzer import analyze_impact
    result = analyze_impact(nodes, edges)
    return render_template("agentic_ai_canvas/impact_analysis.html", design=design, result=result)


@aadc_bp.route("/api/designs/<design_id>/impact", methods=["GET"])
def get_impact_api(design_id: str):
    import json as _json
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "design not found"}), 404
    design = dict(row)
    graph = _json.loads(design.get("graph_json") or '{"nodes":[],"edges":[]}')
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    from tools.agentic_ai_canvas.impact_analyzer import analyze_impact
    result = analyze_impact(nodes, edges)

    rep_id = str(uuid.uuid4())
    try:
        s = result.get("summary", {})
        conn.execute(
            "INSERT INTO aadc_impact_reports (id,design_id,resilience_score,spof_count,overall_risk_level,report_json) VALUES (%s,%s,%s,%s,%s,%s)",
            (rep_id, design_id, s.get("resilience_score", 100),
             len(s.get("spofs", [])), s.get("overall_risk_level", "LOW"),
             _json.dumps(result)),
        )
        conn.commit()
    except Exception:
        pass
    conn.close()
    return jsonify(result)


def _compute_analytics_payload() -> dict:
    """Load AADC analysis tables and return the computed analytics payload.

    Shared by the analytics page and the analytics API, which previously
    duplicated the same seven queries + compute_analytics call verbatim.
    """
    conn = _conn()
    try:
        designs = [dict(r) for r in conn.execute(
            "SELECT * FROM aadc_designs ORDER BY created_at DESC"
        ).fetchall()]
        assessments = [dict(r) for r in conn.execute(
            "SELECT * FROM aadc_assessments"
        ).fetchall()]
        risk_items = [dict(r) for r in conn.execute(
            "SELECT * FROM aadc_risk_items"
        ).fetchall()]
        pattern_reports = [dict(r) for r in conn.execute(
            "SELECT design_id, dominant_pattern FROM aadc_pattern_reports"
        ).fetchall()]
        ato_reports = [dict(r) for r in conn.execute(
            "SELECT design_id, ato_ready FROM aadc_ato_reports"
        ).fetchall()]
        red_team_reports = [dict(r) for r in conn.execute(
            "SELECT design_id, overall_risk FROM aadc_red_team_reports"
        ).fetchall()]
        lint_reports = [dict(r) for r in conn.execute(
            "SELECT design_id, lint_score FROM aadc_lint_reports"
        ).fetchall()]
    finally:
        conn.close()

    from tools.agentic_ai_canvas.analytics_engine import compute_analytics
    return compute_analytics(designs, assessments, pattern_reports, ato_reports,
                             red_team_reports, lint_reports, risk_items)


@aadc_bp.route("/analytics", methods=["GET"])
def analytics_page():
    data = _compute_analytics_payload()
    return render_template("agentic_ai_canvas/analytics.html", data=data)


@aadc_bp.route("/api/analytics", methods=["GET"])
def get_analytics_api():
    return jsonify(_compute_analytics_payload())


# ---------------------------------------------------------------------------
# PHASE 9 — Unified Scorecard, Deployment Gate & Findings Inbox
# ---------------------------------------------------------------------------

# Short-TTL in-process cache for the expensive gate-data fan-out. Each call to
# _load_all_gate_data runs five analysis engines (ATO, regulatory, red-team,
# lint, impact); the scorecard/deploy-gate/download routes call it back-to-back
# per design. Cache the 8-tuple keyed by (design_id, design.updated_at) so a
# design edit (which bumps updated_at) naturally misses, and evict explicitly on
# save. TTL bounds staleness from mutations that don't touch updated_at.
_GATE_DATA_CACHE: dict[tuple, tuple] = {}
_GATE_DATA_CACHE_AT: dict[tuple, float] = {}
_GATE_DATA_CACHE_TTL = 30.0


def _invalidate_gate_cache(design_id: str) -> None:
    """Drop any cached gate data for a design (call after a save)."""
    for key in [k for k in _GATE_DATA_CACHE if k[0] == design_id]:
        _GATE_DATA_CACHE.pop(key, None)
        _GATE_DATA_CACHE_AT.pop(key, None)


def _load_all_gate_data(conn, design_id: str) -> tuple:
    """Load all analysis data needed for scorecard and deploy gate.

    Results are memoized per (design_id, updated_at) for a short TTL so the
    scorecard/deploy-gate/ATO routes don't re-run the five analysis engines on
    every request.
    """
    import json as _json
    import time as _time

    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        return None, None, None, None, None, None, None, None

    design = dict(row)
    cache_key = (design_id, design.get("updated_at"))
    now = _time.monotonic()
    cached = _GATE_DATA_CACHE.get(cache_key)
    if cached is not None and (now - _GATE_DATA_CACHE_AT.get(cache_key, 0.0)) < _GATE_DATA_CACHE_TTL:
        return cached

    graph = _json.loads(design.get("graph_json") or '{"nodes":[],"edges":[]}')
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    assessment = None
    arow = conn.execute(
        "SELECT * FROM aadc_assessments WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
        (design_id,),
    ).fetchone()
    if arow:
        assessment = dict(arow)

    risks = [dict(r) for r in conn.execute(
        "SELECT * FROM aadc_risk_items WHERE design_id=%s", (design_id,)
    ).fetchall()]

    from tools.agentic_ai_canvas.ato_readiness import run_ato_checklist
    from tools.agentic_ai_canvas.regulatory_tracker import run_regulatory_analysis
    from tools.agentic_ai_canvas.red_team import run_red_team
    from tools.agentic_ai_canvas.auto_recommend import lint_design
    from tools.agentic_ai_canvas.impact_analyzer import analyze_impact

    ato_data = run_ato_checklist(nodes, design)
    reg_data = run_regulatory_analysis(nodes, design, risks)
    red_team_data = run_red_team(nodes, edges)
    lint_data = lint_design(nodes, edges, design)
    impact_data = analyze_impact(nodes, edges)

    result = (design, assessment, risks, ato_data, reg_data, red_team_data, lint_data, impact_data)
    _GATE_DATA_CACHE[cache_key] = result
    _GATE_DATA_CACHE_AT[cache_key] = now
    return result


@aadc_bp.route("/scorecard/<design_id>", methods=["GET"])
def scorecard_page(design_id: str):
    conn = _conn()
    design, assessment, risks, ato_data, reg_data, red_team_data, lint_data, impact_data = \
        _load_all_gate_data(conn, design_id)
    conn.close()
    if design is None:
        return "Design not found", 404

    from tools.agentic_ai_canvas.scorecard import build_scorecard
    sc = build_scorecard(design, assessment, ato_data, reg_data, red_team_data,
                         lint_data, impact_data, risks)
    return render_template("agentic_ai_canvas/scorecard.html", sc=sc)


@aadc_bp.route("/api/designs/<design_id>/scorecard", methods=["GET"])
def get_scorecard_api(design_id: str):
    import json as _json
    conn = _conn()
    design, assessment, risks, ato_data, reg_data, red_team_data, lint_data, impact_data = \
        _load_all_gate_data(conn, design_id)
    if design is None:
        conn.close()
        return jsonify({"error": "design not found"}), 404

    from tools.agentic_ai_canvas.scorecard import build_scorecard
    sc = build_scorecard(design, assessment, ato_data, reg_data, red_team_data,
                         lint_data, impact_data, risks)

    rep_id = str(uuid.uuid4())
    try:
        conn.execute(
            "INSERT INTO aadc_scorecard_snapshots (id,design_id,overall_score,health,snapshot_json) VALUES (%s,%s,%s,%s,%s)",
            (rep_id, design_id, sc["overall_score"], sc["health"], _json.dumps(sc)),
        )
        conn.commit()
    except Exception as exc:
        logger.error("aadc: scorecard snapshot insert failed for %s: %s", design_id, exc)
    conn.close()
    return jsonify(sc)


@aadc_bp.route("/deploy-gate/<design_id>", methods=["GET"])
def deploy_gate_page(design_id: str):
    conn = _conn()
    design, assessment, risks, ato_data, reg_data, red_team_data, lint_data, impact_data = \
        _load_all_gate_data(conn, design_id)
    conn.close()
    if design is None:
        return "Design not found", 404

    from tools.agentic_ai_canvas.deploy_gate import run_deploy_gate
    gate = run_deploy_gate(design, assessment, ato_data, reg_data, red_team_data,
                           lint_data, impact_data, risks)
    return render_template("agentic_ai_canvas/deploy_gate.html", gate=gate)


@aadc_bp.route("/api/designs/<design_id>/deploy-gate", methods=["GET"])
def get_deploy_gate_api(design_id: str):
    import json as _json
    conn = _conn()
    design, assessment, risks, ato_data, reg_data, red_team_data, lint_data, impact_data = \
        _load_all_gate_data(conn, design_id)
    if design is None:
        conn.close()
        return jsonify({"error": "design not found"}), 404

    from tools.agentic_ai_canvas.deploy_gate import run_deploy_gate
    gate = run_deploy_gate(design, assessment, ato_data, reg_data, red_team_data,
                           lint_data, impact_data, risks)

    rep_id = str(uuid.uuid4())
    try:
        conn.execute(
            "INSERT INTO aadc_deploy_gates (id,design_id,verdict,blocker_count,warning_count,gate_json) VALUES (%s,%s,%s,%s,%s,%s)",
            (rep_id, design_id, gate["verdict"],
             len(gate["blockers"]), len(gate["warnings"]), _json.dumps(gate)),
        )
        conn.commit()
    except Exception as exc:
        logger.error("aadc: deploy-gate snapshot insert failed for %s: %s", design_id, exc)
    conn.close()
    return jsonify(gate)


@aadc_bp.route("/api/designs/<design_id>/deploy-gate/download", methods=["GET"])
def download_deploy_gate(design_id: str):
    conn = _conn()
    design, assessment, risks, ato_data, reg_data, red_team_data, lint_data, impact_data = \
        _load_all_gate_data(conn, design_id)
    conn.close()
    if design is None:
        return "Design not found", 404

    from tools.agentic_ai_canvas.deploy_gate import run_deploy_gate
    from flask import make_response
    gate = run_deploy_gate(design, assessment, ato_data, reg_data, red_team_data,
                           lint_data, impact_data, risks)
    resp = make_response(gate["gate_yaml"])
    resp.headers["Content-Type"] = "application/yaml"
    resp.headers["Content-Disposition"] = f"attachment; filename=gate-check-{design_id}.yaml"
    return resp


@aadc_bp.route("/findings", methods=["GET"])
def findings_inbox_page():
    from flask import request
    severity_f = request.args.get("severity") or None
    source_f = request.args.get("source") or None
    design_f = request.args.get("design_id") or None

    conn = _conn()
    designs = [dict(r) for r in conn.execute("SELECT id, name FROM aadc_designs").fetchall()]
    assessments = [dict(r) for r in conn.execute("SELECT * FROM aadc_assessments").fetchall()]
    lint_reports = [dict(r) for r in conn.execute("SELECT * FROM aadc_lint_reports").fetchall()]
    red_team_reports = [dict(r) for r in conn.execute("SELECT * FROM aadc_red_team_reports").fetchall()]
    ato_reports = [dict(r) for r in conn.execute("SELECT * FROM aadc_ato_reports").fetchall()]
    reg_reports = [dict(r) for r in conn.execute("SELECT * FROM aadc_regulatory_gaps").fetchall()]
    risk_items = [dict(r) for r in conn.execute("SELECT * FROM aadc_risk_items").fetchall()]
    conn.close()

    from tools.agentic_ai_canvas.findings_inbox import aggregate_findings
    result = aggregate_findings(
        designs, assessments, lint_reports, red_team_reports,
        ato_reports, reg_reports, risk_items,
        severity_filter=severity_f, source_filter=source_f, design_filter=design_f,
    )
    return render_template("agentic_ai_canvas/findings.html", result=result)


@aadc_bp.route("/api/findings", methods=["GET"])
def get_findings_api():
    from flask import request
    severity_f = request.args.get("severity") or None
    source_f = request.args.get("source") or None
    design_f = request.args.get("design_id") or None

    conn = _conn()
    designs = [dict(r) for r in conn.execute("SELECT id, name FROM aadc_designs").fetchall()]
    assessments = [dict(r) for r in conn.execute("SELECT * FROM aadc_assessments").fetchall()]
    lint_reports = [dict(r) for r in conn.execute("SELECT * FROM aadc_lint_reports").fetchall()]
    red_team_reports = [dict(r) for r in conn.execute("SELECT * FROM aadc_red_team_reports").fetchall()]
    ato_reports = [dict(r) for r in conn.execute("SELECT * FROM aadc_ato_reports").fetchall()]
    reg_reports = [dict(r) for r in conn.execute("SELECT * FROM aadc_regulatory_gaps").fetchall()]
    risk_items = [dict(r) for r in conn.execute("SELECT * FROM aadc_risk_items").fetchall()]
    conn.close()

    from tools.agentic_ai_canvas.findings_inbox import aggregate_findings
    result = aggregate_findings(
        designs, assessments, lint_reports, red_team_reports,
        ato_reports, reg_reports, risk_items,
        severity_filter=severity_f, source_filter=source_f, design_filter=design_f,
    )
    return jsonify(result)


# ---------------------------------------------------------------------------
# PHASE 10 — Design Review, Lifecycle & Monitoring
# ---------------------------------------------------------------------------

@aadc_bp.route("/lifecycle/<design_id>", methods=["GET"])
def lifecycle_page(design_id: str):
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return "Design not found", 404
    design = dict(row)
    from tools.agentic_ai_canvas.lifecycle_manager import get_lifecycle
    lc = get_lifecycle(design_id, conn)
    conn.close()
    return render_template("agentic_ai_canvas/lifecycle.html", design=design, lc=lc)


@aadc_bp.route("/api/designs/<design_id>/lifecycle", methods=["GET"])
def get_lifecycle_api(design_id: str):
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "design not found"}), 404
    from tools.agentic_ai_canvas.lifecycle_manager import get_lifecycle
    lc = get_lifecycle(design_id, conn)
    conn.close()
    return jsonify(lc)


@aadc_bp.route("/api/designs/<design_id>/lifecycle/transition", methods=["POST"])
def lifecycle_transition(design_id: str):
    from flask import request
    data = request.get_json(silent=True) or {}
    to_state = data.get("to_state", "")
    actor = data.get("actor", "")
    reason = data.get("reason", "")
    conn = _conn()
    row = conn.execute("SELECT id FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"ok": False, "error": "design not found"}), 404
    from tools.agentic_ai_canvas.lifecycle_manager import transition
    result = transition(design_id, to_state, actor, reason, conn)
    conn.close()
    return jsonify(result)


@aadc_bp.route("/review/<design_id>", methods=["GET"])
def review_page(design_id: str):
    conn = _conn()
    row = conn.execute("SELECT * FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return "Design not found", 404
    design = dict(row)
    from tools.agentic_ai_canvas.review_workflow import get_review
    review = get_review(design_id, conn)
    conn.close()
    return render_template("agentic_ai_canvas/review.html", design=design, review=review)


@aadc_bp.route("/api/designs/<design_id>/review", methods=["GET"])
def get_review_api(design_id: str):
    conn = _conn()
    row = conn.execute("SELECT id FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "design not found"}), 404
    from tools.agentic_ai_canvas.review_workflow import get_review
    result = get_review(design_id, conn)
    conn.close()
    return jsonify(result)


@aadc_bp.route("/api/designs/<design_id>/review", methods=["POST"])
def add_review_comment(design_id: str):
    from flask import request
    data = request.get_json(silent=True) or {}
    conn = _conn()
    row = conn.execute("SELECT id FROM aadc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"ok": False, "error": "design not found"}), 404
    from tools.agentic_ai_canvas.review_workflow import add_comment
    result = add_comment(
        design_id,
        data.get("reviewer", ""),
        data.get("comment_type", "COMMENT"),
        data.get("body", ""),
        data.get("node_id"),
        conn,
    )
    conn.close()
    return jsonify(result)


@aadc_bp.route("/monitoring", methods=["GET"])
def monitoring_page():
    conn = _conn()
    designs = [dict(r) for r in conn.execute("SELECT id, name, domain FROM aadc_designs").fetchall()]
    assessments = [dict(r) for r in conn.execute(
        "SELECT design_id, score, created_at FROM aadc_assessments"
    ).fetchall()]
    conn.close()
    from tools.agentic_ai_canvas.monitoring_engine import compute_monitoring
    data = compute_monitoring(designs, assessments)
    return render_template("agentic_ai_canvas/monitoring.html", data=data)


@aadc_bp.route("/api/monitoring", methods=["GET"])
def get_monitoring_api():
    conn = _conn()
    designs = [dict(r) for r in conn.execute("SELECT id, name, domain FROM aadc_designs").fetchall()]
    assessments = [dict(r) for r in conn.execute(
        "SELECT design_id, score, created_at FROM aadc_assessments"
    ).fetchall()]
    conn.close()
    from tools.agentic_ai_canvas.monitoring_engine import compute_monitoring
    data = compute_monitoring(designs, assessments)
    return jsonify(data)


# ---------------------------------------------------------------------------
# Solution Packs — gallery, quick-start wizard, apply API
# ---------------------------------------------------------------------------

@aadc_bp.route("/solutions")
def solution_packs_gallery():
    _ensure_init()
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM aadc_templates WHERE category='solution-pack' ORDER BY name"
        ).fetchall()
        packs = []
        for r in rows:
            p = dict(r)
            p["badges"] = json.loads(p.get("compliance_badges", "{}"))
            p["tag_list"] = json.loads(p.get("tags", "[]"))
            g = json.loads(p.get("graph_json", '{"nodes":[],"edges":[]}'))
            p["node_count"] = len(g.get("nodes", []))
            p["edge_count"] = len(g.get("edges", []))
            p["risk_count"] = len(SOLUTION_PACK_RISKS.get(p["name"], []))
            p["atlas_count"] = len(SOLUTION_PACK_ATLAS.get(p["name"], []))
        packs = [dict(r) for r in rows]
        # Re-enrich after collecting raw rows
        for p in packs:
            p["badges"] = json.loads(p.get("compliance_badges", "{}"))
            p["tag_list"] = json.loads(p.get("tags", "[]"))
            g = json.loads(p.get("graph_json", '{"nodes":[],"edges":[]}'))
            p["node_count"] = len(g.get("nodes", []))
            p["edge_count"] = len(g.get("edges", []))
            p["risk_count"] = len(SOLUTION_PACK_RISKS.get(p["name"], []))
            p["atlas_count"] = len(SOLUTION_PACK_ATLAS.get(p["name"], []))
    finally:
        conn.close()
    return render_template("agentic_ai_canvas/solution_packs.html", packs=packs)


@aadc_bp.route("/quick-start")
def quick_start_wizard():
    return render_template("agentic_ai_canvas/quick_start.html")


@aadc_bp.route("/api/solution-packs", methods=["GET"])
def list_solution_packs():
    _ensure_init()
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM aadc_templates WHERE category='solution-pack' ORDER BY name"
        ).fetchall()
        packs = []
        for r in rows:
            p = dict(r)
            p["badges"] = json.loads(p.get("compliance_badges", "{}"))
            p["tag_list"] = json.loads(p.get("tags", "[]"))
            g = json.loads(p.get("graph_json", '{"nodes":[],"edges":[]}'))
            p["node_count"] = len(g.get("nodes", []))
            p["risk_seeds"] = SOLUTION_PACK_RISKS.get(p["name"], [])
            p["atlas_scenarios"] = SOLUTION_PACK_ATLAS.get(p["name"], [])
            packs.append(p)
        return jsonify({"packs": packs, "total": len(packs)})
    finally:
        conn.close()


@aadc_bp.route("/api/solution-packs/<pack_id>/apply/<design_id>", methods=["POST"])
def apply_solution_pack(pack_id: str, design_id: str):
    """Apply a solution pack template + seed its risk register into the design."""
    _ensure_init()
    conn = _conn()
    try:
        tmpl = conn.execute(
            "SELECT * FROM aadc_templates WHERE id=%s AND category='solution-pack'",
            (pack_id,),
        ).fetchone()
        if not tmpl:
            return jsonify({"error": "solution pack not found"}), 404
        t = dict(tmpl)

        existing = conn.execute(
            "SELECT id FROM aadc_designs WHERE id=%s", (design_id,)
        ).fetchone()
        now = _utcnow()
        if existing:
            conn.execute(
                "UPDATE aadc_designs SET graph_json=%s, template_id=%s, updated_at=%s WHERE id=%s",
                (t["graph_json"], pack_id, now, design_id),
            )
        else:
            conn.execute(
                "INSERT INTO aadc_designs "
                "(id, name, description, graph_json, template_id, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (design_id, t["name"],
                 f"Created from Solution Pack: {t['name']}",
                 t["graph_json"], pack_id, now, now),
            )

        # Seed risk register entries (skip if already seeded for this design)
        existing_risks = conn.execute(
            "SELECT COUNT(*) FROM aadc_risk_items WHERE design_id=%s", (design_id,)
        ).fetchone()[0]
        risks_seeded = 0
        if existing_risks == 0:
            for seed in SOLUTION_PACK_RISKS.get(t["name"], []):
                conn.execute(
                    "INSERT INTO aadc_risk_items "
                    "(id, design_id, title, description, risk_category, severity, "
                    "likelihood, impact, status, mitigation, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        f"risk-{_uid()}",
                        design_id,
                        seed["title"],
                        seed["description"],
                        seed["risk_category"],
                        seed["severity"],
                        seed.get("likelihood", "MEDIUM"),
                        seed.get("impact", "MEDIUM"),
                        seed.get("status", "open"),
                        seed.get("mitigation", ""),
                        now,
                    ),
                )
                risks_seeded += 1

        conn.commit()
        return jsonify({
            "status": "applied",
            "design_id": design_id,
            "pack_name": t["name"],
            "graph_json": t["graph_json"],
            "risks_seeded": risks_seeded,
            "atlas_scenarios": SOLUTION_PACK_ATLAS.get(t["name"], []),
        })
    finally:
        conn.close()


@aadc_bp.route("/api/quick-start/recommend", methods=["GET"])
def quickstart_recommend():
    domain   = (request.args.get("domain",   "") or "").lower().strip()
    goal     = (request.args.get("goal",     "") or "").lower().strip()
    autonomy = (request.args.get("autonomy", "") or "").lower().strip()
    pack_name = recommend_pack(domain, goal, autonomy)

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, name, description, compliance_badges, autonomy_max, tags "
            "FROM aadc_templates WHERE name=%s AND category='solution-pack'",
            (pack_name,),
        ).fetchone()
        if not row:
            return jsonify({"pack_name": pack_name, "pack_id": None})
        p = dict(row)
        p["badges"] = json.loads(p.get("compliance_badges", "{}"))
        p["tag_list"] = json.loads(p.get("tags", "[]"))
        return jsonify({
            "pack_name": pack_name,
            "pack_id": p["id"],
            "description": p["description"],
            "autonomy_max": p["autonomy_max"],
            "badges": p["badges"],
        })
    finally:
        conn.close()


# ── Enhancement routes ── cost estimation, IaC, design links, impact graph ──

@aadc_bp.route("/api/designs/<design_id>/assessments", methods=["GET"])
def aadc_api_list_assessments(design_id):
    """GET /agentic-ai/api/designs/<id>/assessments — list assessments for a design."""
    limit = min(int(request.args.get("limit", 10)), 50)
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, design_id, score, nist_rmf_score, owasp_score, omb_compliant, "
            "autonomy_max, safety_impacting, rights_impacting, findings_json, atlas_threats, "
            "created_at FROM aadc_assessments "
            "WHERE design_id=%s ORDER BY created_at DESC LIMIT %s",
            (design_id, limit),
        ).fetchall()
        assessments = [dict(r) for r in rows]
        return jsonify({"assessments": assessments, "total": len(assessments)})
    except Exception as exc:
        return jsonify({"error": str(exc), "assessments": []}), 500
    finally:
        conn.close()


@aadc_bp.route("/api/designs/<did>/cost-estimate", methods=["POST"])
def aadc_api_cost_estimate(did):
    if not _estimate_cost:
        return jsonify({"error": "cost estimator not available"}), 503
    conn = _conn()
    try:
        row = conn.execute("SELECT graph_json FROM aadc_designs WHERE id=%s", (did,)).fetchone()
        if not row:
            return jsonify({"error": "design not found"}), 404
        graph = json.loads(row["graph_json"] or '{"nodes":[],"edges":[]}')
        runs = request.json.get("runs_per_month", 1000) if request.is_json else 1000
        result = _estimate_cost(graph, runs_per_month=int(runs))
        conn.execute(
            "DELETE FROM aadc_cost_estimates WHERE design_id=%s", (did,)
        )
        conn.execute(
            "INSERT INTO aadc_cost_estimates "
            "(design_id, model_breakdown, total_per_run, total_monthly, runs_per_month, optimization_hints) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (
                did,
                json.dumps(result["model_breakdown"]),
                result["total_per_run"],
                result["total_monthly"],
                result["runs_per_month"],
                json.dumps(result["optimization_hints"]),
            ),
        )
        conn.commit()
        return jsonify(result)
    finally:
        conn.close()


@aadc_bp.route("/api/designs/<did>/iac", methods=["GET"])
def aadc_api_iac(did):
    if not _gen_iac:
        return jsonify({"error": "IaC generator not available"}), 503
    conn = _conn()
    try:
        row = conn.execute("SELECT name, graph_json FROM aadc_designs WHERE id=%s", (did,)).fetchone()
        if not row:
            return jsonify({"error": "design not found"}), 404
        name = row["name"] or did
        graph = json.loads(row["graph_json"] or '{"nodes":[],"edges":[]}')
        csp = request.args.get("csp", "auto")
        bundle = _gen_iac(graph, name, target_csp=csp)
        import re as _re
        safe = _re.sub(r"[^a-z0-9\-]", "-", name.lower())[:40]
        return Response(
            bundle["zip_bytes"],
            mimetype="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{safe}-iac.zip"'},
        )
    finally:
        conn.close()


@aadc_bp.route("/api/agentic-ai/designs/<did>/links", methods=["GET", "POST"])
def aadc_api_design_links(did):
    conn = _conn()
    try:
        if request.method == "GET":
            rows = conn.execute(
                "SELECT * FROM aadc_design_links WHERE src_design_id=%s OR tgt_design_id=%s",
                (did, did),
            ).fetchall()
            return jsonify([dict(r) for r in rows])
        data = request.get_json(force=True) or {}
        tgt = data.get("tgt_design_id", "")
        if not tgt:
            return jsonify({"error": "tgt_design_id required"}), 400
        link_type = data.get("link_type", "calls")
        label = data.get("link_label", "")
        conn.execute(
            "INSERT OR REPLACE INTO aadc_design_links "
            "(src_design_id, tgt_design_id, link_type, link_label, auto_detected) "
            "VALUES (%s,%s,%s,%s,0)",
            (did, tgt, link_type, label),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM aadc_design_links WHERE src_design_id=%s AND tgt_design_id=%s AND link_type=%s",
            (did, tgt, link_type),
        ).fetchone()
        return jsonify(dict(row)), 201
    finally:
        conn.close()


@aadc_bp.route("/api/agentic-ai/designs/<did>/links/<int:lid>", methods=["DELETE"])
def aadc_api_design_link_delete(did, lid):
    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM aadc_design_links WHERE id=%s AND src_design_id=%s", (lid, did)
        )
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@aadc_bp.route("/impact-graph")
def aadc_impact_graph_page():
    return render_template("agentic_ai_canvas/impact_graph.html")


@aadc_bp.route("/api/agentic-ai/impact-graph", methods=["GET"])
def aadc_api_impact_graph():
    conn = _conn()
    try:
        designs = conn.execute(
            "SELECT id, name, autonomy_max FROM aadc_designs ORDER BY updated_at DESC"
        ).fetchall()
        links = conn.execute("SELECT * FROM aadc_design_links").fetchall()

        # Build adjacency for blast-radius DFS
        children: dict[str, list[str]] = {}
        for lnk in links:
            src = lnk["src_design_id"]
            tgt = lnk["tgt_design_id"]
            children.setdefault(src, []).append(tgt)

        def _blast_radius(did: str) -> int:
            visited: set[str] = set()
            stack = list(children.get(did, []))
            while stack:
                cur = stack.pop()
                if cur in visited:
                    continue
                visited.add(cur)
                stack.extend(children.get(cur, []))
            return len(visited)

        # Latest assessment score per design
        scores: dict[str, float] = {}
        for row in conn.execute(
            "SELECT design_id, MAX(score) as score FROM aadc_assessments GROUP BY design_id"
        ).fetchall():
            scores[row["design_id"]] = row["score"]

        nodes = []
        for d in designs:
            did = d["id"]
            nodes.append({
                "id": did,
                "label": d["name"],
                "score": scores.get(did, 0),
                "autonomy_level": f"L{d['autonomy_max']}",
                "blast_radius": _blast_radius(did),
            })

        edges = [
            {
                "source": lnk["src_design_id"],
                "target": lnk["tgt_design_id"],
                "link_type": lnk["link_type"],
                "label": lnk["link_label"] or lnk["link_type"],
            }
            for lnk in links
        ]

        high_risk = sum(1 for n in nodes if n["blast_radius"] > 2 or n["score"] < 50)
        max_br = max((n["blast_radius"] for n in nodes), default=0)

        return jsonify({
            "nodes": nodes,
            "edges": edges,
            "risk_summary": {
                "total_designs": len(nodes),
                "high_risk_count": high_risk,
                "max_blast_radius": max_br,
            },
        })
    finally:
        conn.close()


# ── AIMC Bridge Routes ────────────────────────────────────────────────────────

@aadc_bp.route('/api/aimc-catalog', methods=['GET'])
def api_aimc_catalog():
    """Return full AIMC FOUNDATION_MODELS list for AADC model linking."""
    from tools.agentic_ai_canvas.canvas_bridge import get_aimc_catalog
    il_filter = request.args.get('il_level')
    models = get_aimc_catalog()
    if il_filter and il_filter.startswith('IL'):
        il_int = int(il_filter.replace('IL', ''))
        models = [m for m in models if il_int in m.get('il_suitability', [])]
    return jsonify(models)


@aadc_bp.route('/api/designs/<design_id>/link-model', methods=['POST'])
def api_link_model(design_id: str):
    """Link an AADC node to an AIMC FOUNDATION_MODELS entry."""
    from tools.agentic_ai_canvas.canvas_bridge import link_model_node, check_il_compatibility
    _ensure_init()
    data = request.get_json(silent=True) or {}
    aadc_node_id = data.get('aadc_node_id')
    aimc_model_id = data.get('aimc_model_id')
    if not aadc_node_id or not aimc_model_id:
        return jsonify({'error': 'aadc_node_id and aimc_model_id required'}), 400
    ref = link_model_node(
        aadc_design_id=design_id,
        aadc_node_id=aadc_node_id,
        aimc_model_id=aimc_model_id,
        aimc_design_id=data.get('aimc_design_id'),
        notes=data.get('notes', ''),
    )
    # Attach IL compatibility status
    violations = check_il_compatibility(design_id)
    node_violations = [v for v in violations if v.get('aadc_node_id') == aadc_node_id]
    ref['il_status'] = 'FAIL' if node_violations else 'PASS'
    ref['il_violations'] = node_violations
    return jsonify(ref), 201


@aadc_bp.route('/api/designs/<design_id>/model-refs', methods=['GET'])
def api_get_model_refs(design_id: str):
    """Get all AIMC model refs for an AADC design."""
    from tools.agentic_ai_canvas.canvas_bridge import get_model_refs, check_il_compatibility
    _ensure_init()
    refs = get_model_refs(design_id)
    violations = check_il_compatibility(design_id)
    violation_node_ids = {v.get('aadc_node_id') for v in violations}
    for ref in refs:
        ref['il_status'] = 'FAIL' if ref['aadc_node_id'] in violation_node_ids else 'PASS'
    return jsonify({'refs': refs, 'il_violations': violations})


@aadc_bp.route('/api/designs/<design_id>/model-refs/<ref_id>', methods=['DELETE'])
def api_delete_model_ref(design_id: str, ref_id: str):
    """Remove an AADC↔AIMC model reference."""
    from tools.agentic_ai_canvas.canvas_bridge import unlink_model_node
    deleted = unlink_model_node(ref_id)
    return jsonify({'deleted': deleted}), 200 if deleted else 404


@aadc_bp.route('/api/iqe-query', methods=['POST'])
def aadc_api_iqe_query():
    """IQE structured query — translate NL to IQE and execute against AADC agentic AI data."""
    import logging as _log
    from tools.iqe.nl_to_iqe import nl_to_iqe
    from tools.iqe.parser import IQESyntaxError, parse
    from tools.iqe.executor import execute_query
    import tools.iqe.adapters.aadc  # noqa: F401 — registers aadc.* collections

    data = request.get_json(silent=True) or {}
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({'error': 'question is required'}), 400

    collections = ['aadc.designs', 'aadc.assessments', 'aadc.artifacts']
    translation = nl_to_iqe(question, collections)
    iqe_str = translation.get('iqe', '')
    explanation = translation.get('explanation', '')

    if not data.get('execute', True):
        return jsonify({'ok': True, 'iqe': iqe_str, 'explanation': explanation}), 200

    try:
        ast = parse(iqe_str)
        rows = execute_query(ast, None)
        return jsonify({'ok': True, 'iqe': iqe_str, 'explanation': explanation,
                        'results': rows, 'row_count': len(rows)}), 200
    except IQESyntaxError as exc:
        return jsonify({'error': f'IQE syntax error: {exc}', 'iqe': iqe_str}), 400
    except Exception as exc:
        _log.getLogger(__name__).warning('AADC IQE query error: %s', exc)
        return jsonify({'error': str(exc), 'iqe': iqe_str}), 500


# ---------------------------------------------------------------------------
# Track B — AADC Ops Config Generator
# ---------------------------------------------------------------------------

@aadc_bp.route("/canvas/<design_id>/ops-config", methods=["GET"])
def ops_config_page(design_id: str):
    """Render the Ops Config modal page for a design."""
    design_name = design_id
    not_found = False
    try:
        conn = _conn()
        row = _row(conn.execute("SELECT id, name FROM aadc_designs WHERE id=%s", (design_id,)).fetchone())
        conn.close()
        if row:
            design_name = row.get("name", design_id)
        else:
            not_found = True
    except Exception:
        not_found = True
    return render_template(
        "agentic_ai_canvas/ops_config.html",
        design_id=design_id,
        design_name=design_name,
        not_found=not_found,
    )


@aadc_bp.route("/api/designs/<design_id>/ops-config", methods=["POST"])
def generate_ops_config_api(design_id: str):
    """Generate ops config and (optionally) create Kanban tasks."""
    import json as _json
    from tools.agentic_ai_canvas.ops_config_generator import (
        generate_ops_config,
        create_kanban_tasks,
    )

    data = request.get_json(force=True, silent=True) or {}
    create_tasks = data.get("create_tasks", True)

    try:
        result = generate_ops_config(design_id)
        if create_tasks and result["kanban_tasks"]:
            task_ids = create_kanban_tasks(result["kanban_tasks"])
            result["created_task_ids"] = task_ids
        else:
            result["created_task_ids"] = []

        return jsonify({
            "ok": True,
            "config_path": result["config_path"],
            "design_name": result["design_name"],
            "matched_nodes": result["matched_nodes"],
            "unmatched_nodes": result["unmatched_nodes"],
            "kanban_tasks": result["kanban_tasks"],
            "created_task_ids": result.get("created_task_ids", []),
            "config_preview": _json.dumps(result["config"], indent=2, ensure_ascii=False)[:4000],
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        get_logger(__name__).error("Ops config generation error: %s", e)
        return jsonify({"error": str(e)}), 500


@aadc_bp.route("/api/designs/<design_id>/run-pipeline", methods=["POST"])
def run_research_pipeline(design_id: str):
    """Execute the Agentic Research Pipeline model layer for a design.

    Accepts a JSON body with:
      - query  (str, required)  — research question
      - chunks (list[str], opt) — pre-retrieved candidate text chunks
      - top_k  (int, opt, default 5)

    Returns a PipelineResult JSON: answer, chunks_used, model,
    confidence, embed_model, top_chunks, error.

    Route: POST /agentic-ai/api/designs/<id>/run-pipeline
    """
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, name FROM aadc_designs WHERE id=%s", (design_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "design not found"}), 404
    finally:
        conn.close()

    data = request.get_json(force=True, silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    chunks: list = data.get("chunks") or []
    top_k = max(1, min(int(data.get("top_k", 5)), 20))

    try:
        from tools.agentic_ai_canvas.model_layer import AgenticResearchPipeline
        pipeline = AgenticResearchPipeline(top_k=top_k, design_id=design_id)
        result = pipeline.run(query=query, chunks=chunks)
    except Exception as exc:
        logger.warning("run-pipeline failed for %s: %s", design_id, exc)
        return jsonify({"error": str(exc)}), 500

    payload = {
        "design_id": design_id,
        "query": result.query,
        "answer": result.answer,
        "chunks_used": result.chunks_used,
        "model": result.model,
        "confidence": result.confidence,
        "embed_model": result.embed_model,
        "top_chunks": [
            {"content": c.content[:500], "score": c.score, "rerank_score": c.rerank_score}
            for c in result.top_chunks
        ],
        "error": result.error,
        "governance": {
            "confidence_gate_passed": result.confidence_gate_passed,
            "output_valid": result.output_valid,
            "audit_entry_id": result.audit_entry_id,
        },
    }
    _record_decision(
        canvas_type="aadc",
        record_id=design_id,
        decision_type="pipeline_run",
        decision=f"query='{query[:120]}' → {result.chunks_used} chunks, model={result.model}",
        rationale=f"confidence={result.confidence:.2f}, embed_model={result.embed_model}",
        model_used=result.model or None,
        confidence=result.confidence or None,
    )
    return jsonify(payload)


@aadc_bp.route("/api/designs/<design_id>/run-agent", methods=["POST"])
def run_research_agent(design_id: str):
    """Execute the full Agentic Research Pipeline (agent + model layers).

    The Research Agent (researcher-agent node) performs web search and
    chunking; the Model Layer (embedder → reranker → synthesis LLM) then
    produces a grounded answer.

    Accepts a JSON body with:
      - query       (str, required)     — research question
      - max_results (int, opt, def 10)  — web search result cap
      - top_k       (int, opt, def 5)   — chunks passed to synthesis LLM

    Returns a JSON payload with agent metadata (sources, duration_ms) plus
    the full PipelineResult fields (answer, chunks_used, model, confidence).

    Route: POST /agentic-ai/api/designs/<id>/run-agent
    """
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, name FROM aadc_designs WHERE id=%s", (design_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "design not found"}), 404
    finally:
        conn.close()

    data = request.get_json(force=True, silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    max_results = max(1, min(int(data.get("max_results", 10)), 50))
    top_k = max(1, min(int(data.get("top_k", 5)), 20))

    try:
        from tools.agentic_ai_canvas.agent_layer import ResearchAgent
        from tools.agentic_ai_canvas.model_layer import AgenticResearchPipeline

        agent = ResearchAgent(max_results=max_results)
        research = agent.search(query)

        pipeline = AgenticResearchPipeline(top_k=top_k)
        result = pipeline.run(query=query, chunks=research.chunks)
    except Exception as exc:
        logger.warning("run-agent failed for %s: %s", design_id, exc)
        return jsonify({"error": str(exc)}), 500

    payload = {
        "design_id": design_id,
        "query": result.query,
        "answer": result.answer,
        "chunks_used": result.chunks_used,
        "model": result.model,
        "confidence": result.confidence,
        "embed_model": result.embed_model,
        "top_chunks": [
            {"content": c.content[:500], "score": c.score, "rerank_score": c.rerank_score}
            for c in result.top_chunks
        ],
        "sources": [
            {"title": s.title, "url": s.url, "snippet": s.snippet[:300]}
            for s in research.sources
        ],
        "agent_duration_ms": research.duration_ms,
        "agent_error": research.error,
        "error": result.error,
    }
    _record_decision(
        canvas_type="aadc",
        record_id=design_id,
        decision_type="agent_run",
        decision=(
            f"query='{query[:120]}' → {len(research.sources)} hits, "
            f"{result.chunks_used} chunks, model={result.model}"
        ),
        rationale=(
            f"confidence={result.confidence:.2f}, "
            f"agent_duration={research.duration_ms:.0f}ms"
        ),
        model_used=result.model or None,
        confidence=result.confidence or None,
    )
    return jsonify(payload)


@aadc_bp.route("/api/ai-trace")
def aadc_api_ai_trace():
    """Return recent AI decisions made by AADC assessment engines."""
    limit = min(int(request.args.get("limit", 50)), 200)
    record_id = request.args.get("record_id")
    try:
        from tools.db.storage import get_connection as _gc
        with _gc() as _conn:
            if record_id:
                rows = _conn.execute(
                    "SELECT * FROM canvas_ai_decisions WHERE canvas_type='aadc' AND record_id=%s "
                    "ORDER BY created_at DESC LIMIT %s",
                    (record_id, limit),
                ).fetchall()
            else:
                rows = _conn.execute(
                    "SELECT * FROM canvas_ai_decisions WHERE canvas_type='aadc' "
                    "ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                ).fetchall()
        return jsonify({"ok": True, "canvas": "aadc", "decisions": [dict(r) for r in rows]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
