
from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
"""ICDEV™ Security Design Canvas — Flask Blueprint integration.

Fully self-contained Blueprint mounted at /security/ inside the ICDEV dashboard.
Uses ICDEV's auth system, separate security_canvas.db, and feature flag
ICDEV_SECURITY_ENABLED.

Usage in ICDEV dashboard app.py:
    from tools.security_canvas.blueprint import create_security_blueprint
    bp = create_security_blueprint()
    if bp:
        app.register_blueprint(bp, url_prefix="/security")
"""

import hmac
import json
import os
import uuid as _uuid
from datetime import datetime, timezone
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

logger = get_logger("icdev.security_canvas")

# nav-sec-05: ZIG mutating endpoints (capability-status PATCH, global assess
# POST) must be restricted to security-officer roles, not merely any
# authenticated session. require_role reads g.current_user (set by the
# dashboard before_request hook) and 401s anonymous / 403s an unauthorized
# role. Every role here also appears in VALID_DASHBOARD_ROLES
# (tools/dashboard/auth.py).
from tools.dashboard.auth import require_role  # noqa: E402

_ZIG_MUTATION_ROLES = ("admin", "isso", "ciso")

# ── Paths ──────────────────────────────────────────────────────────────────────
_SC_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _SC_DIR.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"

# ── Import helper modules ──────────────────────────────────────────────────────
from tools.security_canvas.constants import (  # noqa: E402
    SECURITY_OBJECTS,
)
from tools.security_canvas.security_engine import (  # noqa: E402
    run_stride_analysis,
    run_security_assessment,
    detect_security_gaps,
    map_controls_to_threats,
    generate_auto_fix,
    compute_nist_coverage,
    find_attack_paths,
    generate_fedramp_boundary,
    compare_designs,
    compute_mitre_coverage,
    compute_compliance_crosswalk,
    diff_graph_versions,
)
from tools.security_canvas.attack_path_twin import (  # noqa: E402
    build_attack_graph,
    replay_attack_paths,
    query_attack_paths,
)
from tools.security_canvas.remediation import (  # noqa: E402
    generate_remediation_plan,
)
from tools.security_canvas.agent import (  # noqa: E402
    on_iac_generated,
    scan_iac_file,
    scan_iac_directory,
    llm_identify_threats,
)
from tools.security_canvas.runbooks import (  # noqa: E402
    get_all_runbooks,
    get_runbook_by_id,
    get_applicable_runbooks,
)
from tools.security_canvas.artifacts import (  # noqa: E402
    generate_ssp_artifact,
    generate_sar_artifact,
    generate_poam_artifact,
    generate_artifact_bundle,
)
from tools.security_canvas.sops import (  # noqa: E402
    get_all_sops,
    get_sop_by_id,
    create_sop,
    update_sop,
    delete_sop,
    submit_for_review,
    approve_sop,
    reject_sop,
    seed_sops,
)
from tools.canvas.ai_trace_mixin import record_canvas_decision  # noqa: E402
from tools.security_canvas.posture import compute_posture_summary  # noqa: E402


def create_security_blueprint():
    """Create and return the Security Design Canvas Blueprint.

    Returns None if ICDEV_SECURITY_ENABLED is false.
    """
    enabled = os.environ.get("ICDEV_SECURITY_ENABLED", "true").lower()
    if enabled not in ("true", "1", "yes"):
        logger.info("Security Canvas disabled (ICDEV_SECURITY_ENABLED=%s)", enabled)
        return None

    # Initialize DB
    try:
        from tools.security_canvas.db.init_db import init_db

        init_db()
    except Exception as exc:
        logger.warning("Security Canvas DB init failed: %s", exc)

    # Register canvas event bus subscribers
    try:
        from tools.security_canvas.bus_subscriber import register as _register_bus

        _register_bus()
    except Exception as exc:
        logger.warning("Security Canvas bus subscriber registration failed: %s", exc)

    # Register twin_core cross-canvas twin subscriptions (twx-bus-01). SDC is the
    # hub for both wired subs (PDC pipeline_deployed -> SDC refresh; SDC
    # threat-model-changed -> BDC crosswalk drift), so register them here.
    try:
        from tools.twin_core.event_bridge import register_subscriptions as _register_twin_bus

        _register_twin_bus()
    except Exception as exc:
        logger.warning("twin_core bus subscription registration failed: %s", exc)

    # Seed SOPs
    try:
        seed_sops()
    except Exception as exc:
        logger.warning("SOP seed failed: %s", exc)

    bp = Blueprint(
        "security_canvas",
        __name__,
        template_folder=str(_TEMPLATE_DIR),
    )

    # ── Standing zero-trust stub banner (rmf-zt-01) ────────────────────────
    # ICDEV_ZT_ALLOW_STUB lets an UNVERIFIABLE device posture be honored so
    # dev/CI/e2e keep working without live CrowdStrike credentials. Every ZIG
    # maturity number, device-pillar score and compliance figure on these pages
    # is then computed over a posture nothing measured — and until now the only
    # place that said so was an environment variable. The banner is STANDING:
    # it renders on every /security page for as long as the gate is open.
    #
    # Its ABSENCE asserts the gate is CLOSED (the production default), not that
    # the estate is healthy — `stub_status()` returns banner=None only when
    # `stub_allowed()` is False.
    @bp.context_processor
    def _zt_stub_banner():
        try:
            from tools.security.stub_gate import stub_status

            return {"zt_stub": stub_status()}
        except Exception as exc:  # noqa: BLE001 - a broken import must not 500 the canvas
            logger.warning("zero-trust stub status unavailable: %s", exc)
            return {"zt_stub": {"enabled": None, "banner": None, "env_var": None}}

    # ── Auth wrapper (uses ICDEV dashboard session) ────────────────────────
    def sc_login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("user_id"):
                # ICDEV_AUTH_BYPASS: explicit test-only opt-in — bypass unchanged.
                if os.environ.get("ICDEV_AUTH_BYPASS"):
                    session["user_id"] = "e2e-bypass"
                    return f(*args, **kwargs)
                # ICDEV_DASHBOARD_API_KEY: presented-key semantics. Authenticate
                # ONLY if the request actually presents the key (header
                # X-ICDEV-API-Key or Authorization: Bearer), compared with a
                # constant-time hmac.compare_digest. Merely having the var set in
                # the environment does NOT auto-authenticate.
                api_key = os.environ.get("ICDEV_DASHBOARD_API_KEY", "")
                if api_key:
                    presented = request.headers.get("X-ICDEV-API-Key", "")
                    if not presented:
                        auth_header = request.headers.get("Authorization", "")
                        if auth_header.startswith("Bearer "):
                            presented = auth_header[len("Bearer "):].strip()
                    if presented and hmac.compare_digest(presented, api_key):
                        session["user_id"] = "api-key"
                        return f(*args, **kwargs)
                # All API calls and DELETE/POST/PUT return JSON 401 (never redirect)
                if (
                    request.is_json
                    or request.path.startswith("/security/api/")
                    or request.method in ("DELETE", "POST", "PUT")
                ):
                    return jsonify({"error": "Authentication required"}), 401
                return redirect("/login")
            return f(*args, **kwargs)

        decorated._sc_auth_wrapped = True
        return decorated

    # ── DB helpers ─────────────────────────────────────────────────────────
    from tools.security_canvas.db.init_db import get_connection

    def _now():
        return datetime.now(timezone.utc).isoformat()

    def _audit(action, entity_type, entity_id, details=""):
        user_id = ""
        try:
            user_id = session.get("user_id", "")
        except RuntimeError:
            pass
        try:
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO sc_audit (action, entity_type, entity_id, details, user_id, ts) VALUES (%s,%s,%s,%s,%s,%s)",
                    (action, entity_type, entity_id, details, user_id, _now()),
                )
        except Exception:
            # Non-repudiation: an audit-write failure must be surfaced (logged),
            # not silently swallowed — but it must NOT break the request, so we
            # log a warning and continue rather than re-raising.
            logger.warning(
                "sc_audit write failed: action=%s entity=%s/%s",
                action,
                entity_type,
                entity_id,
            )

    def _row_to_dict(row):
        return dict(row) if row else {}

    def _require_json():
        """Parse a required JSON body for a mutating route.

        Returns ``(data, error)``. If the request carries a non-empty body that
        fails to parse as a JSON object, ``error`` is a ready-to-return
        ``(response, 400)`` tuple and ``data`` is ``None``. An empty body yields
        ``({}, None)`` so handlers keep their own required-field validation and
        GET/optional-body semantics are unaffected.
        """
        raw = request.get_data(cache=True)
        if raw and raw.strip():
            data = request.get_json(force=True, silent=True)
            if not isinstance(data, dict):
                return None, (jsonify({"error": "invalid JSON body"}), 400)
            return data, None
        return {}, None

    # ====================================================================
    # PAGE ROUTES
    # ====================================================================

    @bp.route("/")
    @sc_login_required
    def sc_index():
        """Security Design Canvas dashboard — list designs + recent assessments."""
        with get_connection() as conn:
            designs = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT id, name, description, classification, "
                    "source_topology_id, created_at, updated_at "
                    "FROM security_designs ORDER BY updated_at DESC"
                ).fetchall()
            ]
            recent_assessments = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT a.id, a.design_id, a.assessment_type, a.risk_score, "
                    "a.posture_grade, a.ran_at, d.name AS design_name "
                    "FROM sc_assessments a "
                    "JOIN security_designs d ON a.design_id = d.id "
                    "ORDER BY a.ran_at DESC LIMIT 10"
                ).fetchall()
            ]
        return render_template(
            "security_canvas/index.html",
            designs=designs,
            recent_assessments=recent_assessments,
        )

    @bp.route("/templates")
    @sc_login_required
    def sc_templates_page():
        """Template gallery — browse and load pre-built security designs."""
        conn = get_connection()
        templates = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT id, name, category, description, tags FROM sc_templates ORDER BY category, name"
            ).fetchall()
        ]
        conn.close()
        return render_template("security_canvas/templates.html", templates=templates)

    @bp.route("/canvas/new")
    @sc_login_required
    def sc_new_canvas():
        """New blank security design canvas."""
        return render_template(
            "security_canvas/canvas.html",
            design_id="new",
            design=None,
        )

    @bp.route("/canvas/<design_id>")
    @sc_login_required
    def sc_canvas(design_id):
        """Open existing security design canvas."""
        with get_connection() as conn:
            design = _row_to_dict(conn.execute("SELECT * FROM security_designs WHERE id=%s", (design_id,)).fetchone())
        if not design:
            abort(404)
        return render_template(
            "security_canvas/canvas.html",
            design_id=design_id,
            design=design,
        )

    @bp.route("/assessment/<assessment_id>")
    @sc_login_required
    def sc_assessment_page(assessment_id):
        """View a single assessment result."""
        with get_connection() as conn:
            assessment = _row_to_dict(
                conn.execute("SELECT * FROM sc_assessments WHERE id=%s", (assessment_id,)).fetchone()
            )
            if not assessment:
                abort(404)
            design = _row_to_dict(
                conn.execute("SELECT * FROM security_designs WHERE id=%s", (assessment["design_id"],)).fetchone()
            )
        return render_template(
            "security_canvas/assessment.html",
            assessment=assessment,
            design=design,
        )

    @bp.route("/remediation/<design_id>")
    @sc_login_required
    def sc_remediation_page(design_id):
        """View remediation plans for a design."""
        with get_connection() as conn:
            design = _row_to_dict(conn.execute("SELECT * FROM security_designs WHERE id=%s", (design_id,)).fetchone())
            plans = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT * FROM sc_remediation_plans WHERE design_id=%s ORDER BY created_at DESC", (design_id,)
                ).fetchall()
            ]
        return render_template(
            "security_canvas/remediation.html",
            design=design,
            plans=plans,
        )

    # ====================================================================
    # API ROUTES — Insider-Risk UBA (lite) — card crx-sec-01
    # ====================================================================

    @bp.route("/api/insider-risk", methods=["GET"])
    @sc_login_required
    def sc_api_insider_risk():
        """Latest insider-risk (UBA) findings for the dashboard panel."""
        try:
            from tools.security import insider_risk

            cfg = insider_risk.load_config()
            summary = insider_risk.get_summary()
            summary["enabled"] = bool(cfg.get("enabled"))
            return jsonify(summary)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("insider-risk summary failed: %s", exc)
            return jsonify({"enabled": False, "findings": [], "bands": {}, "count": 0})

    @bp.route("/api/insider-risk/scan", methods=["POST"])
    @sc_login_required
    def sc_api_insider_risk_scan():
        """Run a deterministic insider-risk scan over recent telemetry."""
        try:
            from tools.security import insider_risk

            cfg = insider_risk.load_config()
            if not cfg.get("enabled"):
                return jsonify({
                    "enabled": False,
                    "skipped": "Insider-risk UBA is disabled. Enable it in "
                               "args/insider_risk_config.yaml after a privacy review.",
                }), 200
            result = insider_risk.run_scan(cfg)
            _audit("insider_risk_scan", "security", "uba",
                   f"{result.get('finding_count', 0)} findings over "
                   f"{result.get('actors_evaluated', 0)} accounts")
            return jsonify(result)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("insider-risk scan failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    # ====================================================================
    # API ROUTES — Designs CRUD
    # ====================================================================

    @bp.route("/api/designs", methods=["GET"])
    @sc_login_required
    def sc_api_list_designs():
        """List all security designs."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, name, description, classification, "
                "source_topology_id, created_at, updated_at "
                "FROM security_designs ORDER BY updated_at DESC"
            ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/designs", methods=["POST"])
    @sc_login_required
    def sc_api_create_design():
        """Create a new security design."""
        data = request.get_json(force=True, silent=True) or {}
        template_id = data.get("template_id")
        if template_id:
            with get_connection() as conn:
                ex = conn.execute(
                    "SELECT id, name FROM security_designs WHERE template_id=%s LIMIT 1",
                    (template_id,),
                ).fetchone()
            if ex:
                return jsonify({"id": ex["id"], "name": ex["name"]}), 200
        design_id = str(_uuid.uuid4())
        name = data.get("name", "Untitled Security Design")
        now = _now()
        default_graph = {"nodes": [], "edges": [], "boundaries": []}
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO security_designs "
                "(id, name, description, graph_json, template_id, "
                "source_topology_id, classification, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    design_id,
                    name,
                    data.get("description", ""),
                    json.dumps(data.get("graph_json", default_graph)),
                    data.get("template_id"),
                    data.get("source_topology_id"),
                    data.get("classification", "CUI"),
                    now,
                    now,
                ),
            )
        _audit("CREATE", "design", design_id, name)
        try:
            from tools.canvas.event_bus import publish as _eb_publish
            _eb_publish("sdc", "sdc.topology.saved", {
                "design_id": design_id,
                "classification": data.get("classification", "CUI"),
                "graph_changed": True,
            }, target_canvas="aadc")
        except Exception:
            pass
        return jsonify({"id": design_id, "name": name}), 201

    @bp.route("/api/designs/<design_id>", methods=["GET"])
    @sc_login_required
    def sc_api_get_design(design_id):
        """Get a single security design."""
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM security_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify(_row_to_dict(row))

    @bp.route("/api/designs/<design_id>", methods=["PUT"])
    @sc_login_required
    def sc_api_update_design(design_id):
        """Update an existing security design."""
        data = request.get_json(force=True, silent=True) or {}
        now = _now()
        with get_connection() as conn:
            existing = conn.execute("SELECT id FROM security_designs WHERE id=%s", (design_id,)).fetchone()
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
                params.append(json.dumps(val) if isinstance(val, dict) else val)
            updates.append("updated_at=%s")
            params.append(now)
            params.append(design_id)
            conn.execute(
                f"UPDATE security_designs SET {', '.join(updates)} WHERE id=%s",  # nosec B608 -- keys from hardcoded allowlist
                params,
            )
        _audit("UPDATE", "design", design_id, json.dumps(list(data.keys())))
        try:
            from tools.canvas.event_bus import publish as _eb_publish
            _eb_publish("sdc", "sdc.topology.saved", {
                "design_id": design_id,
                "classification": data.get("classification", "CUI"),
                "graph_changed": "graph_json" in data,
            }, target_canvas="aadc")
        except Exception:
            pass
        # Trigger security agent on design save
        try:
            from tools.security_canvas.agent import auto_assess

            auto_assess(design_id, "design_save")
        except Exception:
            pass
        # Incremental KG update: re-extract only if graph_json changed
        try:
            from tools.canvas.kg_builder import rebuild_canvas_kg

            rebuild_canvas_kg("sdc", design_id)
        except Exception:
            pass
        # Blockchain provenance
        try:
            from tools.canvas.provenance import register_canvas_provenance
            register_canvas_provenance(
                canvas_key="sdc",
                design_id=design_id,
                graph_json=data.get("graph_json", {}),
                project_id=data.get("project_id", ""),
            )
        except Exception:
            pass
        return jsonify({"id": design_id, "updated_at": now})

    @bp.route("/api/designs/<design_id>", methods=["DELETE"])
    @sc_login_required
    def sc_api_delete_design(design_id):
        """Delete a security design and all related records."""
        child_tables = (
            "sc_data_flows",
            "sc_trust_boundaries",
            "sc_controls",
            "sc_threats",
            "sc_assets",
            "sc_remediation_plans",
            "sc_assessments",
        )
        with get_connection() as conn:
            for table in child_tables:
                conn.execute(f"DELETE FROM {table} WHERE design_id=%s", (design_id,))  # nosec B608 -- table from hardcoded tuple
            conn.execute("DELETE FROM security_designs WHERE id=%s", (design_id,))
        _audit("DELETE", "design", design_id, "")
        return jsonify({"deleted": design_id})

    @bp.route("/api/clear-designs", methods=["DELETE", "POST"])
    @sc_login_required
    def sc_api_clear_all_designs():
        """Delete ALL security designs and all related records."""
        child_tables = (
            "sc_data_flows",
            "sc_trust_boundaries",
            "sc_controls",
            "sc_threats",
            "sc_assets",
            "sc_remediation_plans",
            "sc_assessments",
            "sc_versions",
        )
        with get_connection() as conn:
            for table in child_tables:
                conn.execute(f"DELETE FROM {table}")  # nosec B608 -- table from hardcoded tuple
            conn.execute("DELETE FROM security_designs")
        _audit("DELETE", "design", "ALL", "clear-all")
        return jsonify({"deleted": "all"})

    @bp.route("/api/clear-assessments", methods=["DELETE", "POST"])
    @sc_login_required
    def sc_api_clear_all_assessments():
        """Delete ALL assessments."""
        with get_connection() as conn:
            conn.execute("DELETE FROM sc_assessments")
            conn.execute("DELETE FROM sc_remediation_plans")
        _audit("DELETE", "assessment", "ALL", "clear-all")
        return jsonify({"deleted": "all"})

    @bp.route("/api/delete-assessment/<assessment_id>", methods=["DELETE", "POST"])
    @sc_login_required
    def sc_api_delete_assessment(assessment_id):
        """Delete a single assessment."""
        with get_connection() as conn:
            conn.execute("DELETE FROM sc_assessments WHERE id=%s", (assessment_id,))
        _audit("DELETE", "assessment", assessment_id, "")
        return jsonify({"deleted": assessment_id})

    # ====================================================================
    # API ROUTES — Assessment
    # ====================================================================

    @bp.route("/api/designs/<design_id>/assess", methods=["POST"])
    @sc_login_required
    def sc_api_assess(design_id):
        """Run a full security assessment on a design."""
        data = request.get_json(force=True, silent=True) or {}
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404
            try:
                graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            except Exception:
                return jsonify({"error": "Bad graph data"}), 500

            assessment_type = data.get("type", "full")
            result = run_security_assessment(design_id, graph)

            # Persist assessment
            assess_id = str(_uuid.uuid4())
            now = _now()
            conn.execute(
                "INSERT INTO sc_assessments "
                "(id, design_id, assessment_type, trigger_source, "
                "total_threats, total_controls, risk_score, posture_grade, "
                "findings_json, recommendations_json, ran_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    assess_id,
                    design_id,
                    assessment_type,
                    data.get("trigger", "manual"),
                    result.get("total_threats", 0),
                    result.get("total_controls", 0),
                    result.get("risk_score", 0),
                    result.get("posture_grade", "F"),
                    json.dumps(result.get("findings", [])),
                    json.dumps(result.get("recommendations", [])),
                    now,
                ),
            )
        _audit("ASSESS", "design", design_id, f"score={result.get('risk_score', 0)}")
        record_canvas_decision(
            canvas_type="sdc",
            record_id=design_id,
            decision_type="threat_assessment",
            decision=f"Grade {result.get('posture_grade','?')} — {result.get('total_threats',0)} threats, risk={result.get('risk_score',0)}",
            rationale=f"Controls mapped: {result.get('total_controls',0)}",
            model_used=None,
            confidence=None,
        )

        # Generate remediation plan from assessment
        plan = generate_remediation_plan(result, graph)
        result["assessment_id"] = assess_id
        result["remediation_plan"] = plan
        # Blockchain provenance for assessment
        try:
            from tools.canvas.provenance import register_canvas_provenance
            register_canvas_provenance(
                canvas_key="sdc",
                design_id=design_id,
                assessment_data=result,
                project_id="",
            )
        except Exception:
            pass
        return jsonify(result)

    @bp.route("/api/designs/<design_id>/risk-score", methods=["GET"])
    @sc_login_required
    def sc_api_risk_score(design_id):
        """Get the latest risk score for a design."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT risk_score, posture_grade, ran_at "
                "FROM sc_assessments WHERE design_id=%s "
                "ORDER BY ran_at DESC LIMIT 1",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify(
                {
                    "risk_score": 0,
                    "posture_grade": "N/A",
                    "message": "No assessment yet",
                }
            )
        return jsonify(_row_to_dict(row))

    # ====================================================================
    # API ROUTES — STRIDE Analysis
    # ====================================================================

    @bp.route("/api/designs/<design_id>/stride", methods=["POST"])
    @sc_login_required
    def sc_api_stride(design_id):
        """Run STRIDE threat analysis on a design."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        result = run_stride_analysis(graph)
        return jsonify(result)

    @bp.route("/api/designs/<design_id>/export/stride", methods=["GET"])
    @sc_login_required
    def sc_api_export_stride(design_id):
        """Export full STRIDE report with gaps and suggested controls."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name, graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        graph = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        stride = run_stride_analysis(graph)
        gaps = detect_security_gaps(graph)
        suggestions = map_controls_to_threats(stride.get("threats", []))
        return jsonify(
            {
                "design_name": row[0],
                "stride_analysis": stride,
                "gaps": gaps,
                "suggested_controls": suggestions,
            }
        )

    # ====================================================================
    # API ROUTES — NDC Import
    # ====================================================================

    @bp.route("/api/import-from-ndc/<topology_id>", methods=["POST"])
    @sc_login_required
    def sc_api_import_ndc(topology_id):
        """Import an NDC topology as a security design."""
        try:
            from tools.security_canvas.bridge import import_ndc_topology

            result = import_ndc_topology(topology_id)
            return jsonify(result), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ====================================================================
    # API ROUTES — Assessments List
    # ====================================================================

    @bp.route("/api/designs/<design_id>/assessments", methods=["GET"])
    @sc_login_required
    def sc_api_list_assessments(design_id):
        """List all assessments for a design."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, assessment_type, trigger_source, risk_score, "
                "posture_grade, ran_at FROM sc_assessments "
                "WHERE design_id=%s ORDER BY ran_at DESC",
                (design_id,),
            ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    # ====================================================================
    # API ROUTES — Remediation
    # ====================================================================

    @bp.route("/api/designs/<design_id>/remediate", methods=["POST"])
    @sc_login_required
    def sc_api_remediate(design_id):
        """Generate and save a remediation plan for a design."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404
            graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            assessment = run_security_assessment(design_id, graph)
            plan = generate_remediation_plan(assessment, graph)

            # Persist remediation plan
            plan_id = str(_uuid.uuid4())
            now = _now()
            conn.execute(
                "INSERT INTO sc_remediation_plans "
                "(id, design_id, title, priority, status, "
                "remediation_steps, estimated_effort, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    plan_id,
                    design_id,
                    f"Remediation Plan — {now[:10]}",
                    plan.get("overall_risk", "medium").lower(),
                    "open",
                    json.dumps(plan.get("phases", [])),
                    plan.get("estimated_effort", ""),
                    now,
                ),
            )
        _audit("REMEDIATE", "design", design_id, f"plan={plan_id}")
        plan["plan_id"] = plan_id
        return jsonify(plan)

    # ====================================================================
    # API ROUTES — Auto-Fix
    # ====================================================================

    @bp.route("/api/designs/<design_id>/auto-fix", methods=["POST"])
    @sc_login_required
    def sc_api_auto_fix(design_id):
        """One-click auto-fix: generate missing control nodes and edges."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404
            try:
                graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            except Exception:
                return jsonify({"error": "Bad graph data"}), 500

            result = generate_auto_fix(design_id, graph)

            # Merge new nodes into graph
            graph.setdefault("nodes", []).extend(result["nodes_added"])
            graph.setdefault("edges", []).extend(result["edges_added"])

            # Apply edge modifications to existing edges
            for mod in result["edges_modified"]:
                for e in graph.get("edges", []):
                    if e["source"] == mod["source"] and e["target"] == mod["target"]:
                        for k, v in mod.items():
                            if k not in ("source", "target"):
                                e[k] = v

            # Save updated graph back to DB
            now = _now()
            conn.execute(
                "UPDATE security_designs SET graph_json=%s, updated_at=%s WHERE id=%s",
                (json.dumps(graph), now, design_id),
            )
        _audit("AUTO_FIX", "design", design_id, f"fixes={result['total_fixes']}")
        return jsonify(result)

    # ====================================================================
    # API ROUTES — NIST Coverage & Attack Paths
    # ====================================================================

    @bp.route("/api/designs/<design_id>/nist-coverage", methods=["GET"])
    @sc_login_required
    def sc_api_nist_coverage(design_id):
        """Compute NIST 800-53 control family coverage for a design."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            return jsonify({"error": "Bad graph data"}), 500
        result = compute_nist_coverage(graph)
        return jsonify(result)

    @bp.route("/api/designs/<design_id>/attack-paths", methods=["POST"])
    @sc_login_required
    def sc_api_attack_paths(design_id):
        """Find and score attack paths through the design graph."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            return jsonify({"error": "Bad graph data"}), 500
        result = find_attack_paths(graph)
        return jsonify(result)

    @bp.route("/api/designs/<design_id>/attack-graph", methods=["POST"])
    @sc_login_required
    def sc_api_attack_graph(design_id):
        """Build formal attack graph: nodes with classification levels, edges with TTP annotations."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            return jsonify({"error": "Bad graph data"}), 500
        result = build_attack_graph(graph)
        return jsonify(result)

    @bp.route("/api/designs/<design_id>/replay", methods=["POST"])
    @sc_login_required
    def sc_api_replay(design_id):
        """BAS-style replay: enumerate min-cost attack paths via Dijkstra.

        Each hop is annotated with a MITRE ATT&CK TTP. Paths crossing
        classification boundaries (IL2→IL4, CUI→IL5, etc.) are flagged.

        Optional body params:
          max_paths_per_target (int, default 5)
          max_hops (int, default 10)
        """
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            return jsonify({"error": "Bad graph data"}), 500
        body = request.get_json(silent=True) or {}
        max_paths = int(body.get("max_paths_per_target", 5))
        max_hops = int(body.get("max_hops", 10))
        result = replay_attack_paths(graph, max_paths_per_target=max_paths, max_hops=max_hops)
        _audit("ATTACK_REPLAY", "design", design_id, f"paths={result['total_paths']} critical={result['critical_paths']}")
        return jsonify(result)

    @bp.route("/api/designs/<design_id>/attack-paths/iqe", methods=["GET", "POST"])
    @sc_login_required
    def sc_api_attack_paths_iqe(design_id):
        """IQE query interface for attack paths.

        Query param or body: q=<iqe_query>

        Examples:
          ?q=foreach path in attack_paths where risk_level == 'critical' select all
          ?q=foreach path in attack_paths where total_cost < 1.5 select id, ttp_sequence
          ?q=foreach path in attack_paths where has_classification_violation select id, classification_violations
          ?q=foreach path in attack_paths where 'T1190' in ttp_sequence select id, hop_count
        """
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            return jsonify({"error": "Bad graph data"}), 500
        body = request.get_json(silent=True) or {}
        iqe_query = request.args.get("q") or body.get("q") or ""
        if not iqe_query:
            # Default: return all paths
            iqe_query = "foreach path in attack_paths select all"
        result = query_attack_paths(graph, iqe_query)
        return jsonify(result)

    @bp.route("/api/designs/<design_id>/fedramp-boundary", methods=["POST"])
    @sc_login_required
    def sc_api_fedramp_boundary(design_id):
        """Generate FedRAMP authorization boundary and missing controls."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            return jsonify({"error": "Bad graph data"}), 500

        body = request.get_json(silent=True) or {}
        impact_level = body.get("impact_level", "moderate")

        result = generate_fedramp_boundary(graph, impact_level)

        # Merge generated items into graph and save
        if result["boundaries_added"]:
            graph.setdefault("boundaries", []).extend(result["boundaries_added"])
        if result["controls_added"]:
            graph.setdefault("nodes", []).extend(result["controls_added"])
        if result["edges_added"]:
            graph.setdefault("edges", []).extend(result["edges_added"])

        now = _now()
        with get_connection() as conn:
            conn.execute(
                "UPDATE security_designs SET graph_json=%s, updated_at=%s WHERE id=%s",
                (json.dumps(graph), now, design_id),
            )
        _audit("FEDRAMP_BOUNDARY", "design", design_id, f"impact={impact_level} additions={result['total_additions']}")
        return jsonify(result)

    # ====================================================================
    # API ROUTES — Templates & Constants
    # ====================================================================

    @bp.route("/api/templates", methods=["GET"])
    @sc_login_required
    def sc_api_templates():
        """List available security design templates."""
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM sc_templates ORDER BY category, name").fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/constants", methods=["GET"])
    @sc_login_required
    def sc_api_constants():
        """Return the security object palette for the canvas UI."""
        return jsonify(SECURITY_OBJECTS)

    # Snippets
    @bp.route("/api/snippets", methods=["GET"])
    @sc_login_required
    def sc_api_snippets():
        """List all security design snippets."""
        conn = get_connection()
        rows = conn.execute("SELECT * FROM sc_snippets ORDER BY category, name").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    # ── Export Routes ──────────────────────────────────────────────────────

    @bp.route("/api/designs/<design_id>/export/<fmt>", methods=["POST"])
    @sc_login_required
    def sc_api_export(design_id, fmt):
        """Export security design in various formats."""
        conn = get_connection()
        row = conn.execute(
            "SELECT name, graph_json FROM security_designs WHERE id=%s",
            (design_id,),
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        name = row[0] or "Security Design"
        try:
            graph = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        except Exception:
            return jsonify({"error": "Bad graph data"}), 500

        # Normalize: SDC has nodes + boundaries, merge for export
        export_nodes = list(graph.get("nodes", []))
        for b in graph.get("boundaries", []):
            export_nodes.append(
                {
                    "id": b.get("id", ""),
                    "type": b.get("type", "boundary"),
                    "label": b.get("label", ""),
                    "x": b.get("x", 0),
                    "y": b.get("y", 0),
                    "width": b.get("width", 300),
                    "height": b.get("height", 200),
                    "config": b.get("config", {}),
                }
            )
        export_graph = {"nodes": export_nodes, "edges": graph.get("edges", [])}

        import re as _re

        safe_name = _re.sub(r"[^a-zA-Z0-9_-]", "_", name)

        if fmt == "drawio":
            from tools.network.export_import import to_drawio

            content = to_drawio(export_graph, name)
            _audit("EXPORT", "design", design_id, "drawio")
            return jsonify({"format": "drawio", "filename": f"{safe_name}.drawio", "content": content})

        if fmt == "svg":
            from tools.network.export_import import to_svg

            content = to_svg(export_graph, name)
            _audit("EXPORT", "design", design_id, "svg")
            return jsonify({"format": "svg", "filename": f"{safe_name}.svg", "content": content})

        if fmt == "vsdx":
            from tools.network.visio_export import export_vsdx
            import base64

            vsdx_bytes = export_vsdx(name, export_graph)
            encoded = base64.b64encode(vsdx_bytes).decode("ascii")
            _audit("EXPORT", "design", design_id, "vsdx")
            return jsonify({"format": "vsdx", "filename": f"{safe_name}.vsdx", "content_b64": encoded})

        if fmt == "csv":
            from tools.network.visio_export import export_ops_csvs
            import base64
            import io as _io
            import zipfile

            csv_files = export_ops_csvs(name, export_graph)
            buf = _io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for fname, content in csv_files.items():
                    zf.writestr(fname, content)
            encoded = base64.b64encode(buf.getvalue()).decode("ascii")
            _audit("EXPORT", "design", design_id, "csv")
            return jsonify({"format": "csv", "filename": f"{safe_name}_security.zip", "content_b64": encoded})

        if fmt == "threat-model":
            stride = run_stride_analysis(graph)
            gaps = detect_security_gaps(graph)
            controls = map_controls_to_threats(stride.get("threats", []))
            assessment = run_security_assessment(design_id, graph)
            report = {
                "design_name": name,
                "design_id": design_id,
                "stride_analysis": stride,
                "security_gaps": gaps,
                "suggested_controls": controls,
                "assessment": {
                    "risk_score": assessment.get("risk_score"),
                    "posture_grade": assessment.get("posture_grade"),
                    "findings": assessment.get("findings", []),
                },
                "exported_at": _now(),
            }
            content = json.dumps(report, indent=2)
            _audit("EXPORT", "design", design_id, "threat-model")
            return jsonify({"format": "threat-model", "filename": f"{safe_name}_threat_model.json", "content": content})

        # Delegate ATO artifact formats to their specific handlers
        if fmt == "ssp":
            assessment = run_security_assessment(design_id, graph)
            nist_coverage = compute_nist_coverage(graph)
            content = generate_ssp_artifact(name, design_id, graph, assessment, nist_coverage)
            _audit("EXPORT", "design", design_id, "ssp")
            return jsonify({"content": content, "format": "ssp"})

        if fmt == "sar":
            assessment = run_security_assessment(design_id, graph)
            remediation_plan = generate_remediation_plan(assessment, graph)
            content = generate_sar_artifact(name, assessment, remediation_plan)
            _audit("EXPORT", "design", design_id, "sar")
            return jsonify({"content": content, "format": "sar"})

        if fmt == "poam":
            assessment = run_security_assessment(design_id, graph)
            remediation_plan = generate_remediation_plan(assessment, graph)
            content = generate_poam_artifact(name, remediation_plan)
            _audit("EXPORT", "design", design_id, "poam")
            return jsonify({"content": content, "format": "poam"})

        if fmt == "artifact-bundle":
            result = generate_artifact_bundle(design_id, name, graph)
            _audit("EXPORT", "design", design_id, "artifact-bundle")
            return jsonify(result)

        return jsonify(
            {
                "error": f"Unknown format: {fmt}. Supported: drawio, svg, vsdx, csv, threat-model, ssp, sar, poam, artifact-bundle"
            }
        ), 400

    # ====================================================================
    # PAGE ROUTES — Runbooks
    # ====================================================================

    @bp.route("/runbooks")
    @sc_login_required
    def sc_runbooks_page():
        """Browse all incident-response runbooks."""
        return render_template(
            "security_canvas/runbooks.html",
            runbooks=get_all_runbooks(),
        )

    @bp.route("/runbooks/<runbook_id>")
    @sc_login_required
    def sc_runbook_detail(runbook_id):
        """View a single runbook playbook."""
        runbook = get_runbook_by_id(runbook_id)
        if not runbook:
            abort(404)
        return render_template(
            "security_canvas/runbook_detail.html",
            runbook=runbook,
        )

    # ====================================================================
    # API ROUTES — Runbooks
    # ====================================================================

    @bp.route("/api/runbooks", methods=["GET"])
    @sc_login_required
    def sc_api_list_runbooks():
        """Return all incident-response runbooks."""
        return jsonify(get_all_runbooks())

    @bp.route("/api/runbooks/<runbook_id>", methods=["GET"])
    @sc_login_required
    def sc_api_get_runbook(runbook_id):
        """Return a single runbook by ID."""
        runbook = get_runbook_by_id(runbook_id)
        if not runbook:
            return jsonify({"error": "Runbook not found"}), 404
        return jsonify(runbook)

    @bp.route("/api/designs/<design_id>/runbooks", methods=["POST"])
    @sc_login_required
    def sc_api_design_runbooks(design_id):
        """Return applicable runbooks for a design based on its assessment."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        assessment = run_security_assessment(design_id, graph)
        findings = assessment.get("findings", [])
        applicable = get_applicable_runbooks(findings)
        return jsonify(
            {
                "design_id": design_id,
                "total_findings": len(findings),
                "applicable_runbooks": applicable,
            }
        )

    # ====================================================================
    # PAGE ROUTES — ATO Artifacts
    # ====================================================================

    @bp.route("/artifacts/<design_id>")
    @sc_login_required
    def sc_artifacts_page(design_id):
        """Render the ATO artifact export page for a design."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id, name FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            abort(404)
        design = {"id": row[0], "name": row[1] or "Security Design"}
        return render_template("security_canvas/artifacts.html", design=design)

    # ====================================================================
    # API ROUTES — ATO Artifact Export
    # ====================================================================

    @bp.route("/api/designs/<design_id>/export/ssp", methods=["POST"])
    @sc_login_required
    def sc_api_export_ssp(design_id):
        """Generate and return SSP markdown."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name, graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        name = row[0] or "Security Design"
        try:
            graph = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        except Exception:
            return jsonify({"error": "Bad graph data"}), 500
        assessment = run_security_assessment(design_id, graph)
        nist_coverage = compute_nist_coverage(graph)
        content = generate_ssp_artifact(name, design_id, graph, assessment, nist_coverage)
        _audit("EXPORT_SSP", "design", design_id, "ssp")
        return jsonify({"content": content, "format": "ssp"})

    @bp.route("/api/designs/<design_id>/export/sar", methods=["POST"])
    @sc_login_required
    def sc_api_export_sar(design_id):
        """Generate and return SAR markdown."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name, graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        name = row[0] or "Security Design"
        try:
            graph = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        except Exception:
            return jsonify({"error": "Bad graph data"}), 500
        assessment = run_security_assessment(design_id, graph)
        remediation_plan = generate_remediation_plan(assessment, graph)
        content = generate_sar_artifact(name, assessment, remediation_plan)
        _audit("EXPORT_SAR", "design", design_id, "sar")
        return jsonify({"content": content, "format": "sar"})

    @bp.route("/api/designs/<design_id>/export/poam", methods=["POST"])
    @sc_login_required
    def sc_api_export_poam(design_id):
        """Generate and return POA&M markdown."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name, graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        name = row[0] or "Security Design"
        try:
            graph = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        except Exception:
            return jsonify({"error": "Bad graph data"}), 500
        assessment = run_security_assessment(design_id, graph)
        remediation_plan = generate_remediation_plan(assessment, graph)
        content = generate_poam_artifact(name, remediation_plan)
        _audit("EXPORT_POAM", "design", design_id, "poam")
        return jsonify({"content": content, "format": "poam"})

    @bp.route("/api/designs/<design_id>/export/artifact-bundle", methods=["POST"])
    @sc_login_required
    def sc_api_export_artifact_bundle(design_id):
        """Generate all ATO artifacts (SSP, SAR, POA&M) as a bundle."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name, graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        name = row[0] or "Security Design"
        try:
            graph = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        except Exception:
            return jsonify({"error": "Bad graph data"}), 500
        result = generate_artifact_bundle(design_id, name, graph)
        _audit("EXPORT_BUNDLE", "design", design_id, "artifact-bundle")
        return jsonify(result)

    # ====================================================================
    # PAGE ROUTES — Compare & Posture
    # ====================================================================

    @bp.route("/compare")
    @sc_login_required
    def sc_compare_page():
        """Side-by-side comparison of two security designs."""
        return render_template("security_canvas/compare.html")

    @bp.route("/posture")
    @sc_login_required
    def sc_posture_page():
        """Aggregate security posture overview across all designs."""
        return render_template("security_canvas/posture.html")

    # ====================================================================
    # API ROUTES — Compare & Posture
    # ====================================================================

    @bp.route("/api/designs/compare", methods=["POST"])
    @sc_login_required
    def sc_api_compare_designs():
        """Compare two security designs and return a comprehensive diff."""
        data = request.get_json(force=True)
        design_a_id = data.get("design_a", "")
        design_b_id = data.get("design_b", "")
        if not design_a_id or not design_b_id:
            return jsonify({"error": "Both design_a and design_b are required"}), 400

        with get_connection() as conn:
            row_a = conn.execute(
                "SELECT name, graph_json FROM security_designs WHERE id=%s",
                (design_a_id,),
            ).fetchone()
            row_b = conn.execute(
                "SELECT name, graph_json FROM security_designs WHERE id=%s",
                (design_b_id,),
            ).fetchone()

        if not row_a:
            return jsonify({"error": f"Design A ({design_a_id}) not found"}), 404
        if not row_b:
            return jsonify({"error": f"Design B ({design_b_id}) not found"}), 404

        graph_a = json.loads(row_a["graph_json"]) if isinstance(row_a["graph_json"], str) else row_a["graph_json"]
        graph_b = json.loads(row_b["graph_json"]) if isinstance(row_b["graph_json"], str) else row_b["graph_json"]
        name_a = row_a["name"] or design_a_id
        name_b = row_b["name"] or design_b_id

        result = compare_designs(graph_a, graph_b, name_a, name_b)
        return jsonify(result)

    @bp.route("/api/posture-summary", methods=["GET"])
    @sc_login_required
    def sc_api_posture_summary():
        """Aggregate security posture across all designs.

        Thin wrapper — all aggregation lives in
        ``tools/security_canvas/posture.py::compute_posture_summary`` (shx-hyg-02).
        """
        with get_connection() as conn:
            return jsonify(compute_posture_summary(conn))

    # ====================================================================
    # API ROUTES — IaC Scanning
    # ====================================================================

    @bp.route("/api/iac/scan-text", methods=["POST"])
    @sc_login_required
    def sc_api_iac_scan_text():
        """Scan inline IaC content for security misconfigurations."""
        data, _err = _require_json()
        if _err:
            return _err
        content = data.get("content", "")
        iac_type = data.get("iac_type", "terraform")
        topology_id = data.get("topology_id", "")
        if not content:
            return jsonify({"error": "content is required"}), 400
        result = on_iac_generated(topology_id, content, iac_type)
        return jsonify(result)

    @bp.route("/api/iac/scan-file", methods=["POST"])
    @sc_login_required
    def sc_api_iac_scan_file():
        """Scan a single IaC file on disk for security misconfigurations."""
        data, _err = _require_json()
        if _err:
            return _err
        file_path = data.get("file_path", "")
        if not file_path:
            return jsonify({"error": "file_path is required"}), 400
        result = scan_iac_file(file_path)
        return jsonify(result)

    @bp.route("/api/iac/scan-directory", methods=["POST"])
    @sc_login_required
    def sc_api_iac_scan_directory():
        """Scan all IaC files in a directory for security misconfigurations."""
        data, _err = _require_json()
        if _err:
            return _err
        directory_path = data.get("directory_path", "")
        if not directory_path:
            return jsonify({"error": "directory_path is required"}), 400
        result = scan_iac_directory(directory_path)
        return jsonify(result)

    # ====================================================================
    # API ROUTES — MITRE ATT&CK Coverage
    # ====================================================================

    @bp.route("/api/designs/<design_id>/mitre-coverage", methods=["GET"])
    @sc_login_required
    def sc_api_mitre_coverage(design_id):
        """Compute MITRE ATT&CK technique coverage for a design."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            return jsonify({"error": "Bad graph data"}), 500
        result = compute_mitre_coverage(graph)
        return jsonify(result)

    # ====================================================================
    # API ROUTES — Threat Model Import
    # ====================================================================

    @bp.route("/api/import/threat-model", methods=["POST"])
    @sc_login_required
    def sc_api_import_threat_model():
        """Import a threat model from Threat Dragon or TMT format."""
        from tools.security_canvas.importers import import_threat_model

        data, _err = _require_json()
        if _err:
            return _err
        content = data.get("content", "")
        fmt = data.get("format", "auto")
        if not content:
            return jsonify({"error": "No content provided"}), 400
        result = import_threat_model(content, fmt)
        if "error" in result:
            return jsonify(result), 400
        # Create a new design from the import
        design_id = str(_uuid.uuid4())
        now = _now()
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO security_designs "
                "(id, name, description, graph_json, classification, "
                "created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    design_id,
                    result.get("design_name", "Imported Design"),
                    f"Imported from {result.get('source_format', 'unknown')}",
                    json.dumps(result.get("graph", {})),
                    "CUI",
                    now,
                    now,
                ),
            )
        _audit("IMPORT", "design", design_id, result.get("source_format", ""))
        result["design_id"] = design_id
        return jsonify(result), 201

    # ====================================================================
    # API ROUTES — Collaborative Editing
    # ====================================================================

    @bp.route("/api/collab/<design_id>/join", methods=["POST"])
    @sc_login_required
    def sc_api_collab_join(design_id):
        """Join a collaborative editing session."""
        from tools.security_canvas.collaboration import get_session

        user_id = session.get("user_id", "anonymous")
        user_name = session.get("username", "")
        sess = get_session(design_id)
        result = sess.join(user_id, user_name)
        return jsonify(result)

    @bp.route("/api/collab/<design_id>/leave", methods=["POST"])
    @sc_login_required
    def sc_api_collab_leave(design_id):
        """Leave a collaborative editing session."""
        from tools.security_canvas.collaboration import get_session

        user_id = session.get("user_id", "anonymous")
        sess = get_session(design_id)
        sess.leave(user_id)
        return jsonify({"left": True})

    @bp.route("/api/collab/<design_id>/push", methods=["POST"])
    @sc_login_required
    def sc_api_collab_push(design_id):
        """Push an operation to the collaboration session."""
        from tools.security_canvas.collaboration import get_session

        data = request.get_json(force=True, silent=True) or {}
        user_id = session.get("user_id", "anonymous")
        sess = get_session(design_id)
        seq = sess.push_operation(
            user_id,
            data.get("op_type", "unknown"),
            data.get("data", {}),
        )
        return jsonify({"seq": seq})

    @bp.route("/api/collab/<design_id>/poll", methods=["GET"])
    @sc_login_required
    def sc_api_collab_poll(design_id):
        """Poll for new operations since a given sequence number."""
        from tools.security_canvas.collaboration import get_session

        since = request.args.get("since", 0, type=int)
        user_id = session.get("user_id", "anonymous")
        # Update cursor if provided
        cx = request.args.get("cx", type=float)
        cy = request.args.get("cy", type=float)
        sess = get_session(design_id)
        if cx is not None and cy is not None:
            sess.update_cursor(user_id, cx, cy)
        ops = sess.get_operations_since(since)
        participants = sess.get_participants()
        return jsonify(
            {
                "operations": ops,
                "participants": participants,
                "latest_seq": sess.seq,
            }
        )

    @bp.route("/api/collab/<design_id>/participants", methods=["GET"])
    @sc_login_required
    def sc_api_collab_participants(design_id):
        """Get current participants in a session."""
        from tools.security_canvas.collaboration import get_session

        sess = get_session(design_id)
        return jsonify({"participants": sess.get_participants()})

    # ====================================================================
    # API ROUTES — Compliance Crosswalk
    # ====================================================================

    @bp.route("/api/designs/<design_id>/compliance-crosswalk", methods=["GET"])
    @sc_login_required
    def sc_api_compliance_crosswalk(design_id):
        """Compute compliance crosswalk (NIST/FedRAMP/CMMC) for a design."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            return jsonify({"error": "Bad graph data"}), 500
        result = compute_compliance_crosswalk(graph)
        return jsonify(result)

    # ====================================================================
    # API ROUTES — Design Versioning
    # ====================================================================

    @bp.route("/api/designs/<design_id>/versions", methods=["GET"])
    @sc_login_required
    def sc_api_list_versions(design_id):
        """List all version snapshots for a design."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, version_number, change_summary, user_id, created_at "
                "FROM sc_versions WHERE design_id=%s ORDER BY version_number DESC",
                (design_id,),
            ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/designs/<design_id>/versions/<version_id>", methods=["GET"])
    @sc_login_required
    def sc_api_get_version(design_id, version_id):
        """Get a single version's full graph_json."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM sc_versions WHERE id=%s AND design_id=%s",
                (version_id, design_id),
            ).fetchone()
        if not row:
            return jsonify({"error": "Version not found"}), 404
        d = _row_to_dict(row)
        try:
            d["graph_json"] = json.loads(d["graph_json"]) if isinstance(d["graph_json"], str) else d["graph_json"]
        except Exception:
            pass
        return jsonify(d)

    @bp.route("/api/designs/<design_id>/versions", methods=["POST"])
    @sc_login_required
    def sc_api_create_version(design_id):
        """Manually create a version snapshot of the current design state."""
        data = request.get_json(force=True, silent=True) or {}
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
            if not row:
                return jsonify({"error": "Design not found"}), 404
            current_graph_raw = row[0]
            try:
                current_graph = (
                    json.loads(current_graph_raw) if isinstance(current_graph_raw, str) else current_graph_raw
                )
            except Exception:
                current_graph = {}
            ver_num = conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) + 1 FROM sc_versions WHERE design_id=%s",
                (design_id,),
            ).fetchone()[0]
            # Compute diff against previous version if exists
            change_summary = data.get("change_summary", "")
            if not change_summary:
                prev = conn.execute(
                    "SELECT graph_json FROM sc_versions WHERE design_id=%s ORDER BY version_number DESC LIMIT 1",
                    (design_id,),
                ).fetchone()
                if prev:
                    try:
                        prev_graph = json.loads(prev[0]) if isinstance(prev[0], str) else prev[0]
                        diff = diff_graph_versions(prev_graph, current_graph)
                        change_summary = diff.get("summary", "")
                    except Exception:
                        pass
            ver_id = str(_uuid.uuid4())
            now = _now()
            conn.execute(
                "INSERT INTO sc_versions (id, design_id, version_number, graph_json, change_summary, user_id, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    ver_id,
                    design_id,
                    ver_num,
                    json.dumps(current_graph) if isinstance(current_graph, dict) else str(current_graph_raw),
                    change_summary,
                    session.get("user_id", ""),
                    now,
                ),
            )
        _audit("CREATE", "version", ver_id, f"design={design_id} v{ver_num}")
        return jsonify({"id": ver_id, "version_number": ver_num, "change_summary": change_summary, "created_at": now})

    @bp.route("/api/designs/<design_id>/versions/<version_id>/restore", methods=["POST"])
    @sc_login_required
    def sc_api_restore_version(design_id, version_id):
        """Restore a previous version by copying its graph_json back to the design."""
        with get_connection() as conn:
            ver_row = conn.execute(
                "SELECT graph_json, version_number FROM sc_versions WHERE id=%s AND design_id=%s",
                (version_id, design_id),
            ).fetchone()
            if not ver_row:
                return jsonify({"error": "Version not found"}), 404
            design_row = conn.execute(
                "SELECT id FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
            if not design_row:
                return jsonify({"error": "Design not found"}), 404
            now = _now()
            conn.execute(
                "UPDATE security_designs SET graph_json=%s, updated_at=%s WHERE id=%s",
                (ver_row[0], now, design_id),
            )
        _audit("RESTORE", "version", version_id, f"design={design_id} restored to v{ver_row[1]}")
        return jsonify(
            {"id": design_id, "restored_version": version_id, "version_number": ver_row[1], "updated_at": now}
        )

    # ====================================================================
    # API ROUTES — LLM-Assisted Threat Identification
    # ====================================================================

    # ====================================================================
    # PAGE ROUTES — SOPs
    # ====================================================================

    @bp.route("/sops")
    @sc_login_required
    def sc_sops_page():
        """Standard Operating Procedures — list, create, approve."""
        sops = get_all_sops()
        return render_template("security_canvas/sops.html", sops=sops)

    # ====================================================================
    # API ROUTES — SOPs CRUD + Approval Workflow
    # ====================================================================

    @bp.route("/api/sops", methods=["GET"])
    @sc_login_required
    def sc_api_list_sops():
        """List SOPs with optional ?type= and ?status= filters."""
        sop_type = request.args.get("type")
        approval_status = request.args.get("status")
        return jsonify(get_all_sops(sop_type=sop_type, approval_status=approval_status))

    @bp.route("/api/sops", methods=["POST"])
    @sc_login_required
    def sc_api_create_sop():
        """Create a new SOP."""
        data, _err = _require_json()
        if _err:
            return _err
        sop = create_sop(data)
        _audit("CREATE", "sop", sop["id"], sop["title"])
        return jsonify(sop), 201

    @bp.route("/api/sops/<sop_id>", methods=["GET"])
    @sc_login_required
    def sc_api_get_sop(sop_id):
        """Get a single SOP by ID."""
        sop = get_sop_by_id(sop_id)
        if not sop:
            return jsonify({"error": "Not found"}), 404
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>", methods=["PUT"])
    @sc_login_required
    def sc_api_update_sop(sop_id):
        """Update an existing SOP."""
        data, _err = _require_json()
        if _err:
            return _err
        sop = update_sop(sop_id, data)
        if not sop:
            return jsonify({"error": "Not found"}), 404
        _audit("UPDATE", "sop", sop_id, sop["title"])
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>", methods=["DELETE"])
    @sc_login_required
    def sc_api_delete_sop(sop_id):
        """Delete a SOP."""
        deleted = delete_sop(sop_id)
        if not deleted:
            return jsonify({"error": "Not found"}), 404
        _audit("DELETE", "sop", sop_id, "")
        return jsonify({"deleted": True})

    @bp.route("/api/sops/<sop_id>/submit", methods=["POST"])
    @sc_login_required
    def sc_api_submit_sop(sop_id):
        """Submit a SOP for review (draft → pending_review)."""
        sop, err = submit_for_review(sop_id)
        if err:
            return jsonify({"error": err}), 400
        _audit("SUBMIT", "sop", sop_id, "pending_review")
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>/approve", methods=["POST"])
    @sc_login_required
    def sc_api_approve_sop(sop_id):
        """Approve a pending SOP."""
        data = request.get_json(force=True, silent=True) or {}
        approved_by = data.get("approved_by", session.get("user_id", ""))
        sop, err = approve_sop(sop_id, approved_by=approved_by)
        if err:
            return jsonify({"error": err}), 400
        _audit("APPROVE", "sop", sop_id, f"approved_by={approved_by}")
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>/reject", methods=["POST"])
    @sc_login_required
    def sc_api_reject_sop(sop_id):
        """Reject a pending SOP."""
        data = request.get_json(force=True, silent=True) or {}
        reason = data.get("reason", "")
        rejected_by = data.get("rejected_by", session.get("user_id", ""))
        sop, err = reject_sop(sop_id, reason=reason, rejected_by=rejected_by)
        if err:
            return jsonify({"error": err}), 400
        _audit("REJECT", "sop", sop_id, f"rejected_by={rejected_by} reason={reason}")
        return jsonify(sop)

    @bp.route("/api/designs/<design_id>/llm-threats", methods=["POST"])
    @sc_login_required
    def sc_api_llm_threats(design_id):
        """Run LLM-assisted threat identification on a design."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM security_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            return jsonify({"error": "Bad graph data"}), 500
        data = request.get_json(force=True, silent=True) or {}
        use_cot = data.get("use_cot", False)
        chain_mode = "cot" if use_cot else ""
        result = llm_identify_threats(graph)
        result["chain_mode"] = chain_mode
        if isinstance(result, dict) and result.get("threats"):
            _threats = result["threats"]
            _summary = f"{len(_threats)} LLM-identified threat(s)" if isinstance(_threats, list) else str(_threats)[:300]
            record_canvas_decision(
                canvas_type="sdc",
                record_id=design_id,
                decision_type="threat_assessment",
                decision=_summary,
                rationale=result.get("reasoning") or result.get("rationale", ""),
                model_used=result.get("model"),
                confidence=result.get("confidence"),
            )
        return jsonify(result)

    # ====================================================================
    # ATTACK PATH TWIN — /security/attackpath (dt-sdc-twin-07)
    # ====================================================================

    @bp.route("/attackpath")
    @sc_login_required
    def sc_attackpath_page():
        """SDC Attack Path Twin dashboard — /security/attackpath."""
        return render_template("security_canvas/attackpath.html")

    @bp.route("/api/attackpath", methods=["GET"])
    @sc_login_required
    def sc_api_attackpath():
        """Return attack path snapshot summary for the dashboard.

        JSON::

            {
                "summary": {
                    "total_snapshots": int,
                    "total_nodes": int,
                    "total_edges": int,
                    "max_risk_score": float
                },
                "snapshots": [...]
            }
        """
        from tools.security_canvas.attackpath import get_attackpath_summary, enumerate_paths

        with get_connection() as conn:
            data = get_attackpath_summary(conn)

        paths = enumerate_paths(data["snapshots"])
        return jsonify(
            {
                "summary": {
                    "total_snapshots": data["total_snapshots"],
                    "total_nodes": data["total_nodes"],
                    "total_edges": data["total_edges"],
                    "max_risk_score": data["max_risk_score"],
                },
                "snapshots": data["snapshots"],
                "paths": paths,
            }
        )

    # ── GraphRAG /ask endpoint — SDC ───────────────────────────────────────
    _SDC_KG_PROJECT_ID = "sdc-kg-9acdfb94bea0"
    _SDC_KG_PROFILE = "security"

    @bp.route("/ask")
    @sc_login_required
    def sc_ask_page():
        return render_template("security_canvas/ask.html")

    @bp.route("/api/ask", methods=["POST"])
    @sc_login_required
    def sc_api_ask():
        from tools.knowledge_graph.canvas_ask import handle_ask_request
        data = request.get_json(silent=True) or {}
        payload = handle_ask_request(
            query=data.get("query", ""),
            graph_id=_SDC_KG_PROJECT_ID,
            profile=_SDC_KG_PROFILE,
            top_k=int(data.get("top_k", 10)),
            narrate=bool(data.get("narrate", False)),
            canvas_label="security design (STRIDE × NIST)",
        )
        status = payload.pop("_status", 200)
        return jsonify(payload), status

    # ── Digital Twin ───────────────────────────────────────────────────────
    @bp.route("/twin/<design_id>")
    @sc_login_required
    def sc_twin_page(design_id):
        conn = get_connection()
        design = conn.execute("SELECT * FROM security_designs WHERE id=%s", (design_id,)).fetchone()
        if not design:
            return render_template("404.html"), 404
        design = _row_to_dict(design)
        try:
            snapshots = conn.execute(
                "SELECT * FROM sdc_attack_snapshots WHERE design_id=%s ORDER BY created_at DESC LIMIT 20",
                (design_id,),
            ).fetchall()
        except Exception:
            snapshots = []
        return render_template(
            "security_canvas/twin.html",
            design=design,
            snapshots=[_row_to_dict(s) for s in snapshots],
        )

    @bp.route("/api/twin/<design_id>/snapshot", methods=["POST"])
    @sc_login_required
    def sc_api_twin_snapshot(design_id):
        from tools.security_canvas.twin import take_snapshot
        data = request.get_json(silent=True) or {}
        snap = take_snapshot(design_id, label=data.get("label"))
        return jsonify(snap), 201

    @bp.route("/api/twin/<design_id>/simulate", methods=["POST"])
    @sc_login_required
    def sc_api_twin_simulate(design_id):
        from tools.security_canvas.twin import simulate_delta
        data = request.get_json(silent=True) or {}
        result = simulate_delta(
            design_id,
            delta_graph=data.get("delta_graph", {}),
            entry_point=data.get("entry_point"),
            target_goal=data.get("target_goal"),
            baseline_snap_id=data.get("baseline_snap_id"),
        )
        return jsonify(result), 200

    @bp.route("/api/twin/<design_id>/simulate-cot", methods=["POST"])
    @sc_login_required
    def sc_api_twin_simulate_cot(design_id):
        from tools.security_canvas.twin import simulate_delta
        data = request.get_json(silent=True) or {}
        result = simulate_delta(
            design_id,
            delta_graph=data.get("delta_graph", {}),
            entry_point=data.get("entry_point"),
            target_goal=data.get("target_goal"),
            baseline_snap_id=data.get("baseline_snap_id"),
            use_cot=True,
        )
        return jsonify(result), 200

    @bp.route("/api/twin/<design_id>/attack-paths/snapshot", methods=["POST"])
    @sc_login_required
    def sc_api_attack_snapshot(design_id):
        from tools.security_canvas.twin import take_snapshot
        data = request.get_json(silent=True) or {}
        snap = take_snapshot(design_id, label=data.get("label"))
        return jsonify(snap), 201

    @bp.route("/api/twin/<design_id>/attack-paths/simulate", methods=["POST"])
    @sc_login_required
    def sc_api_attack_simulate(design_id):
        from tools.security_canvas.twin import simulate_delta
        data = request.get_json(silent=True) or {}
        result = simulate_delta(
            design_id,
            delta_graph=data.get("delta_graph", {}),
            entry_point=data.get("entry_point"),
            target_goal=data.get("target_goal"),
            baseline_snap_id=data.get("baseline_snap_id"),
        )
        return jsonify(result), 200

    @bp.route("/api/twin/<design_id>/current-topology", methods=["GET"])
    @sc_login_required
    def sc_api_twin_current_topology(design_id):
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM security_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "Design not found"}), 404
        try:
            graph = json.loads(row["graph_json"] or "{}")
        except Exception:
            graph = {}
        return jsonify({"graph_json": graph}), 200

    @bp.route("/api/twin/<design_id>/chat-delta", methods=["POST"])
    @sc_login_required
    def sc_api_twin_chat_delta(design_id):
        from tools.twin_chat import security_chat_to_delta
        data, _err = _require_json()
        if _err:
            return _err
        message = (data.get("message") or "").strip()
        if not message:
            return jsonify({"error": "message is required"}), 400
        result = security_chat_to_delta(message, data.get("graph_json"))
        return jsonify(result), (500 if "error" in result else 200)

    @bp.route("/api/iqe-query", methods=["POST"])
    @sc_login_required
    def sc_api_iqe_query():
        """IQE structured query — translate NL to IQE and execute against SDC attack graph."""
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import IQESyntaxError, parse
        from tools.iqe.executor import execute_query
        import tools.iqe.adapters.security  # noqa: F401 — registers attack.* collections

        data, _err = _require_json()
        if _err:
            return _err
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400

        # Must match `iqe.collections` for key `sdc` in component_registry.yaml —
        # security.ai_decisions was declared there but never offered here, so the
        # adapter registered a collection no question could reach.
        collections = ["attack.nodes", "attack.edges", "attack.paths",
                       "security.ai_decisions"]
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
            logger.warning("SDC IQE query error: %s", exc)
            return jsonify({"error": str(exc), "iqe": iqe_str}), 500

    @bp.route("/api/ai-trace")
    @sc_login_required
    def sc_api_ai_trace():
        """Return recent AI decisions made by SDC assessment engines."""
        limit = min(int(request.args.get("limit", 50)), 200)
        record_id = request.args.get("record_id")
        try:
            from tools.db.storage import get_connection as _gc
            with _gc() as _conn:
                if record_id:
                    rows = _conn.execute(
                        "SELECT * FROM canvas_ai_decisions WHERE canvas_type='sdc' AND record_id=%s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (record_id, limit),
                    ).fetchall()
                else:
                    rows = _conn.execute(
                        "SELECT * FROM canvas_ai_decisions WHERE canvas_type='sdc' "
                        "ORDER BY created_at DESC LIMIT %s",
                        (limit,),
                    ).fetchall()
            return jsonify({"ok": True, "canvas": "sdc", "decisions": [dict(r) for r in rows]})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    # ── SDC Demo Runner ───────────────────────────────────────────────────────

    @bp.route("/demo")
    @sc_login_required
    def sc_demo_page():
        """SDC Demo Runner — 3-scenario executive/customer/prospect demo."""
        return render_template("security_canvas/demo.html", page_title="SDC Demo Runner")

    # ── Production Audit (rmf-ui-14) ──────────────────────────────────────────
    #
    # Migrated from a bare `@app.route("/prod-audit")` in tools/dashboard/app.py,
    # where it had no registry entry, no RBAC guard, no completeness gate and no
    # IQE dispatch. On this blueprint it inherits all four: app.py attaches
    # guard_component_access("sdc", min_il) as a before_request on every
    # registered canvas blueprint, the registry's url_prefix + IQE adapter put
    # /security/* on the path->canvas map, and the canvas completeness gate owns
    # security_canvas/. A VISIBILITY surface -- production-readiness checks,
    # read-only posture -- which is SDC's ground. The page drives the UNCHANGED
    # /api/prod-audit/* blueprint (tools/dashboard/api/prod_audit.py) -- only the
    # PAGE route moved. The old URL redirects here rather than 404ing: a silently
    # dropped page is the failure mode a one-route-per-card migration refuses.

    @bp.route("/prod-audit")
    @sc_login_required
    def sc_prod_audit_page():
        """Production readiness audit — 30 checks, 6 categories (D291-D300)."""
        return render_template("security_canvas/prod_audit.html")

    @bp.route("/api/sdc-demo-run", methods=["POST"])
    @sc_login_required
    def sc_api_sdc_demo_run():
        """Run one or more SDC demo scenarios and return JSON result."""
        data = request.get_json(silent=True) or {}
        scenarios = data.get("scenarios") or None
        audience = data.get("audience", "exec")
        simulate = bool(data.get("simulate", False))
        try:
            from tools.sdc.demo_runner import run_sdc_demo
            result = run_sdc_demo(scenarios=scenarios, audience=audience, simulate=simulate)
            return jsonify({"ok": True, **result})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @bp.route("/api/compliance-timeline/<design_id>")
    @sc_login_required
    def sc_api_compliance_timeline(design_id):
        """Return before/after compliance timeline for a design."""
        try:
            import sqlite3 as _sq
            _db = Path(__file__).resolve().parents[2] / "data" / "security_canvas.db"
            conn = _sq.connect(str(_db))
            conn.row_factory = _sq.Row
            rows = conn.execute(
                "SELECT * FROM sdc_compliance_timeline WHERE design_id=%s ORDER BY snapshot_label",
                (design_id,),
            ).fetchall()
            conn.close()
            return jsonify({"ok": True, "design_id": design_id, "snapshots": [dict(r) for r in rows]})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @bp.route("/api/roi/<design_id>")
    @sc_login_required
    def sc_api_roi(design_id):
        """Return ROI metrics for a design."""
        try:
            from tools.sdc.roi_calculator import compute_roi
            result = compute_roi(design_id)
            return jsonify({"ok": True, **result})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @bp.route("/api/isso-approve", methods=["POST"])
    @sc_login_required
    def sc_api_isso_approve():
        """Simulate ISSO approval — writes real sc_audit record."""
        data = request.get_json(silent=True) or {}
        step_run_id = data.get("step_run_id", "demo-run-step-04")
        approver = data.get("approver", "isso-demo@agency.gov")
        reason = data.get("reason", "demo-auto-approve")
        try:
            from tools.sdc.isso_gate import approve_demo
            result = approve_demo(step_run_id, approver=approver, reason=reason)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @bp.route("/api/attack-ttp-coverage/<design_id>")
    @sc_login_required
    def sc_api_attack_ttp_coverage(design_id):
        """Return MITRE ATT&CK tactic/technique coverage for a design's attack snapshots."""
        import sqlite3 as _sq
        import json as _json
        _db = Path(__file__).resolve().parents[2] / "data" / "security_canvas.db"
        try:
            conn = _sq.connect(str(_db))
            conn.row_factory = _sq.Row
            snaps = conn.execute(
                "SELECT nodes_json FROM sdc_attack_snapshots WHERE component_id=%s",
                (design_id,),
            ).fetchall()
            conn.close()

            # Aggregate TTP IDs from node data across all snapshots
            tactic_map: dict = {
                "Initial Access":       [],
                "Credential Access":    [],
                "Privilege Escalation": [],
                "Lateral Movement":     [],
                "Exfiltration":         [],
                "Impact":               [],
            }
            _TACTIC_LOOKUP = {
                "T1190": "Initial Access", "T1566": "Initial Access", "T1078": "Initial Access",
                "T1552": "Credential Access", "T1539": "Credential Access", "T1110": "Credential Access",
                "T1068": "Privilege Escalation", "T1548": "Privilege Escalation", "T1611": "Privilege Escalation",
                "T1557": "Lateral Movement", "T1563": "Lateral Movement",
                "T1071": "Exfiltration", "T1530": "Exfiltration", "T1020": "Exfiltration",
                "T1499": "Impact", "T1485": "Impact", "T1498": "Impact",
            }
            seen: set = set()
            for snap in snaps:
                nodes = _json.loads(snap["nodes_json"] or "[]")
                for node in nodes:
                    ttp = node.get("ttp")
                    if ttp and ttp not in seen:
                        seen.add(ttp)
                        tactic = _TACTIC_LOOKUP.get(ttp, "Initial Access")
                        if ttp not in tactic_map[tactic]:
                            tactic_map[tactic].append(ttp)

            return jsonify({"ok": True, "design_id": design_id, "tactics": tactic_map, "ttps": list(seen)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    # ── NSA ZIG (Zero Trust Implementation Guide) Routes ────────────────────

    @bp.route("/zig/")
    @bp.route("/zig")
    @sc_login_required
    def zig_index():
        """ZIG home — 7-pillar radar, phase progress, FY2027 status."""
        from tools.security_canvas.zig_pillar_scorer import score_all_pillars, aggregate_zig_score
        from tools.security_canvas.zig_phase_tracker import get_all_phases_status, compute_fy2027_readiness
        from tools.security_canvas.constants import ZIG_PILLARS
        pillar_scores = score_all_pillars()
        aggregate = aggregate_zig_score(pillar_scores)
        phases = get_all_phases_status()
        fy2027 = compute_fy2027_readiness(phases)
        return render_template(
            "security_canvas/zig/index.html",
            pillar_scores=pillar_scores,
            aggregate=aggregate,
            phases=phases,
            fy2027=fy2027,
            zig_pillars=ZIG_PILLARS,
        )

    @bp.route("/zig/pillar/<pillar_slug>")
    @sc_login_required
    def zig_pillar_detail(pillar_slug):
        """Per-pillar ZIG detail — capabilities checklist + activities."""
        from tools.security_canvas.zig_phase_tracker import get_capability_status_by_pillar
        from tools.security_canvas.zig_pillar_scorer import score_pillar
        from tools.security_canvas.db.init_db import get_connection
        from tools.security_canvas.constants import ZIG_PILLARS

        pillar_meta = next((p for p in ZIG_PILLARS if p["slug"] == pillar_slug), None)
        if not pillar_meta:
            return render_template("security_canvas/zig/index.html"), 404

        capabilities = get_capability_status_by_pillar(pillar_slug)
        score_data = score_pillar(pillar_slug)

        conn = get_connection()
        try:
            activities_by_cap = {}
            for cap in capabilities:
                acts = conn.execute(
                    "SELECT a.id, a.title, a.description, a.phase, a.nist_control_ref, "
                    "COALESCE(ac.status, 'not_started') as status, ac.evidence_note "
                    "FROM zig_activities a "
                    "LEFT JOIN zig_activity_completions ac ON a.id=ac.activity_id "
                    "WHERE a.capability_id=%s ORDER BY a.phase",
                    (cap["id"],),
                ).fetchall()
                activities_by_cap[cap["id"]] = [dict(a) for a in acts]
        finally:
            conn.close()

        return render_template(
            "security_canvas/zig/pillar.html",
            pillar=pillar_meta,
            capabilities=capabilities,
            score=score_data,
            activities_by_cap=activities_by_cap,
        )

    @bp.route("/zig/phase")
    @sc_login_required
    def zig_phase_tracker():
        """Phase tracker — Discovery / Phase 1 / Phase 2 activity grids."""
        from tools.security_canvas.zig_phase_tracker import get_all_phases_status
        from tools.security_canvas.db.init_db import get_connection

        conn = get_connection()
        try:
            activities_by_phase = {}
            for phase_slug in ("discovery", "phase1", "phase2"):
                acts = conn.execute(
                    "SELECT a.id, a.title, a.phase, a.nist_control_ref, a.capability_id, "
                    "c.title as cap_title, c.pillar_slug, "
                    "COALESCE(ac.status, 'not_started') as status "
                    "FROM zig_activities a "
                    "JOIN zig_capabilities c ON a.capability_id=c.id "
                    "LEFT JOIN zig_activity_completions ac ON a.id=ac.activity_id "
                    "WHERE a.phase=%s ORDER BY c.pillar_slug, c.id",
                    (phase_slug,),
                ).fetchall()
                activities_by_phase[phase_slug] = [dict(a) for a in acts]
        finally:
            conn.close()

        phases_status = get_all_phases_status()
        return render_template(
            "security_canvas/zig/phase.html",
            activities_by_phase=activities_by_phase,
            phases_status=phases_status,
        )

    @bp.route("/zig/assessment")
    @sc_login_required
    def zig_assessment_page():
        """ZIG assessment page — run gap assessment, view results.

        Also renders the DoD 7-pillar ZTA posture, whose two maturity numbers
        (evidence-backed vs self-attested) are shown separately and labelled
        (rmf-zt-02). READ-ONLY: latest_posture_summary reads what the scorer
        last persisted and never runs an assessment on page load.
        """
        from tools.security_canvas.zig_assessor import get_latest_zig_maturity
        latest = get_latest_zig_maturity()
        try:
            from tools.devsecops.zta_maturity_scorer import latest_posture_summary
            zta = latest_posture_summary()
        except Exception:  # noqa: BLE001
            # A broken panel must still SAY it is broken. Returning None would
            # hide the section, which is indistinguishable from a clean board.
            zta = {"state": "never_assessed", "error": "ZTA posture unavailable"}
        return render_template(
            "security_canvas/zig/assessment.html", latest=latest, zta=zta
        )

    @bp.route("/zig/roadmap")
    @sc_login_required
    def zig_roadmap_page():
        """ZIG roadmap — FY2027/FY2032 milestone timeline."""
        from tools.security_canvas.zig_roadmap_generator import generate_roadmap
        roadmap = generate_roadmap()
        return render_template("security_canvas/zig/roadmap.html", roadmap=roadmap)

    # ── ZIG API Endpoints ────────────────────────────────────────────────────

    @bp.route("/api/zig/pillars")
    @sc_login_required
    def zig_api_pillars():
        """GET /security/api/zig/pillars — all pillars with current maturity scores."""
        from tools.security_canvas.zig_pillar_scorer import score_all_pillars, aggregate_zig_score
        pillar_scores = score_all_pillars()
        aggregate = aggregate_zig_score(pillar_scores)
        return jsonify({"ok": True, "pillars": pillar_scores, "aggregate": aggregate})

    @bp.route("/api/zig/pillars/<pillar_slug>")
    @sc_login_required
    def zig_api_pillar_detail(pillar_slug):
        """GET /security/api/zig/pillars/<slug> — pillar + capabilities + maturity."""
        from tools.security_canvas.zig_pillar_scorer import score_pillar
        from tools.security_canvas.zig_phase_tracker import get_capability_status_by_pillar
        from tools.security_canvas.constants import ZIG_PILLARS
        meta = next((p for p in ZIG_PILLARS if p["slug"] == pillar_slug), None)
        if not meta:
            return jsonify({"ok": False, "error": "pillar not found"}), 404
        score = score_pillar(pillar_slug)
        capabilities = get_capability_status_by_pillar(pillar_slug)
        return jsonify({"ok": True, "pillar": meta, "score": score, "capabilities": capabilities})

    @bp.route("/api/zig/capabilities")
    @sc_login_required
    def zig_api_capabilities():
        """GET /security/api/zig/capabilities?pillar=&phase=&status= — filterable list."""
        from tools.security_canvas.db.init_db import get_connection
        pillar = request.args.get("pillar")
        phase = request.args.get("phase")
        status = request.args.get("status")

        sql = "SELECT * FROM zig_capabilities WHERE 1=1"
        params = []
        if pillar:
            sql += " AND pillar_slug=%s"; params.append(pillar)
        if phase:
            sql += " AND phase=%s"; params.append(phase)
        if status:
            sql += " AND implementation_status=%s"; params.append(status)
        sql += " ORDER BY pillar_slug, phase"

        conn = get_connection()
        try:
            caps = [dict(c) for c in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()
        return jsonify({"ok": True, "capabilities": caps, "count": len(caps)})

    @bp.route("/api/zig/capabilities/<cap_id>", methods=["PATCH"])
    @require_role(*_ZIG_MUTATION_ROLES)
    @sc_login_required
    def zig_api_cap_status(cap_id):
        """PATCH /security/api/zig/capabilities/<id> — update implementation_status."""
        from tools.security_canvas.db.init_db import get_connection
        data = request.get_json(silent=True) or {}
        new_status = data.get("implementation_status")
        evidence_note = data.get("evidence_note")
        valid = {"not_started", "planned", "in_progress", "implemented"}
        if new_status not in valid:
            return jsonify({"ok": False, "error": f"status must be one of {valid}"}), 400
        conn = get_connection()
        try:
            cur = conn.execute(
                "UPDATE zig_capabilities SET implementation_status=%s, evidence_note=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                (new_status, evidence_note, cap_id),
            )
            if cur.rowcount == 0:
                return jsonify({"ok": False, "error": "unknown capability id"}), 404
            conn.commit()
        finally:
            conn.close()
        # Non-repudiation: capability status + evidence writes are audited.
        _audit(
            "zig_capability_status_change", "zig_capability", cap_id,
            details=f"status={new_status}; evidence={'yes' if evidence_note else 'no'}",
        )
        return jsonify({"ok": True, "id": cap_id, "implementation_status": new_status})

    @bp.route("/api/zig/activities")
    @sc_login_required
    def zig_api_activities():
        """GET /security/api/zig/activities?capability=&phase= — filterable list."""
        from tools.security_canvas.db.init_db import get_connection
        capability = request.args.get("capability")
        phase = request.args.get("phase")

        sql = ("SELECT a.id, a.title, a.phase, a.description, a.nist_control_ref, a.capability_id, "
               "c.pillar_slug, c.title as cap_title, "
               "COALESCE(ac.status, 'not_started') as status, ac.evidence_note "
               "FROM zig_activities a "
               "JOIN zig_capabilities c ON a.capability_id=c.id "
               "LEFT JOIN zig_activity_completions ac ON a.id=ac.activity_id WHERE 1=1")
        params = []
        if capability:
            sql += " AND a.capability_id=%s"; params.append(capability)
        if phase:
            sql += " AND a.phase=%s"; params.append(phase)
        sql += " ORDER BY a.phase, c.pillar_slug"

        conn = get_connection()
        try:
            acts = [dict(a) for a in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()
        return jsonify({"ok": True, "activities": acts, "count": len(acts)})

    @bp.route("/api/zig/activities/<activity_id>/complete", methods=["PATCH"])
    @sc_login_required
    def zig_api_activity_complete(activity_id):
        """PATCH /security/api/zig/activities/<id>/complete — set completion status."""
        from tools.security_canvas.zig_activity_tracker import set_activity_status
        data = request.get_json(silent=True) or {}
        status = data.get("status", "complete")
        evidence_note = data.get("evidence_note")
        completed_by = data.get("completed_by")
        try:
            result = set_activity_status(activity_id, status, evidence_note, completed_by)
            # Non-repudiation: activity completion + evidence writes are audited.
            _audit(
                "zig_activity_completion", "zig_activity", activity_id,
                details=(f"target=icdev-self; status={status}; "
                         f"evidence={'yes' if evidence_note else 'no'}"),
            )
            return jsonify({"ok": True, **result})
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @bp.route("/api/zig/maturity")
    @sc_login_required
    def zig_api_maturity():
        """GET /security/api/zig/maturity — aggregate scores + FY2027 readiness."""
        from tools.security_canvas.zig_pillar_scorer import score_all_pillars, aggregate_zig_score
        from tools.security_canvas.zig_phase_tracker import compute_fy2027_readiness
        pillar_scores = score_all_pillars()
        aggregate = aggregate_zig_score(pillar_scores)
        fy2027 = compute_fy2027_readiness()
        return jsonify({
            "ok": True,
            "pillar_scores": pillar_scores,
            "aggregate": aggregate,
            "fy2027": fy2027,
        })

    @bp.route("/api/zig/assess", methods=["POST"])
    @require_role(*_ZIG_MUTATION_ROLES)
    @sc_login_required
    def zig_api_assess():
        """POST /security/api/zig/assess — run the GLOBAL ZIG assessment.

        Scores all 7 pillars org-wide and persists the run to
        zig_maturity_scores. This endpoint is not target-scoped; to assess a
        specific target use POST /api/zig/targets/<target_id>/assess.
        """
        from tools.security_canvas.zig_assessor import run_zig_assessment
        data = request.get_json(force=True, silent=True) or {}
        if (data.get("target_id") or "").strip():
            return jsonify({
                "ok": False,
                "error": "target_id is not supported on /api/zig/assess; use "
                         "POST /api/zig/targets/<target_id>/assess for a "
                         "target-scoped assessment",
            }), 400
        try:
            result = run_zig_assessment()
            # Non-repudiation: assessment runs are audited.
            _audit(
                "zig_assessment_run", "zig_target", "icdev-self",
                details=f"scope=global; aggregate={result.get('aggregate', {}).get('score')}",
            )
            return jsonify({"ok": True, **result})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @bp.route("/api/zig/phases")
    @sc_login_required
    def zig_api_phases():
        """GET /security/api/zig/phases — Discovery/Ph1/Ph2 completion metrics."""
        from tools.security_canvas.zig_phase_tracker import get_all_phases_status, compute_fy2027_readiness
        phases = get_all_phases_status()
        fy2027 = compute_fy2027_readiness(phases)
        return jsonify({"ok": True, "phases": phases, "fy2027": fy2027})

    @bp.route("/api/zig/roadmap")
    @sc_login_required
    def zig_api_roadmap():
        """GET /security/api/zig/roadmap — milestone timeline JSON."""
        from tools.security_canvas.zig_roadmap_generator import generate_roadmap
        try:
            roadmap = generate_roadmap()
            return jsonify({"ok": True, **roadmap})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @bp.route("/api/zig/artifact")
    @sc_login_required
    def zig_api_artifact():
        """GET /security/api/zig/artifact — download ZIG gap assessment report."""
        from tools.security_canvas.zig_artifact_generator import generate_zig_artifact
        try:
            report = generate_zig_artifact()
            from flask import Response
            return Response(
                report["markdown"],
                mimetype="text/markdown",
                headers={"Content-Disposition": "attachment; filename=zig_assessment_report.md"},
            )
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    # ── ZIG External Targets ──────────────────────────────────────────────────

    @bp.route("/api/zig/targets", methods=["GET"])
    @sc_login_required
    def zig_api_targets_list():
        """GET /security/api/zig/targets — list all active ZIG targets."""
        from tools.security_canvas.db.init_db import get_connection
        try:
            conn = get_connection()
            rows = conn.execute(
                "SELECT id, name, description, system_type, classification, status, created_at "
                "FROM zig_targets ORDER BY name"
            ).fetchall()
            conn.close()
            return jsonify({"ok": True, "targets": [dict(r) for r in rows]})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @bp.route("/api/zig/targets", methods=["POST"])
    @sc_login_required
    def zig_api_targets_create():
        """POST /security/api/zig/targets — create a new ZIG target."""
        from tools.security_canvas.db.init_db import get_connection
        from datetime import datetime, timezone
        data, _err = _require_json()
        if _err:
            return _err
        target_id = data.get("id", "").strip()
        name = data.get("name", "").strip()
        if not target_id or not name:
            return jsonify({"ok": False, "error": "id and name are required"}), 400
        now = datetime.now(timezone.utc).isoformat()
        try:
            conn = get_connection()
            conn.execute(
                "INSERT INTO zig_targets (id, name, description, system_type, classification, status, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (target_id, name,
                 data.get("description", ""),
                 data.get("system_type", "general"),
                 data.get("classification", "CUI"),
                 data.get("status", "active"),
                 now, now),
            )
            conn.commit()
            conn.close()
            return jsonify({"ok": True, "id": target_id}), 201
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @bp.route("/api/zig/targets/<target_id>", methods=["GET"])
    @sc_login_required
    def zig_api_target_get(target_id):
        """GET /security/api/zig/targets/<id> — get single ZIG target."""
        from tools.security_canvas.db.init_db import get_connection
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT * FROM zig_targets WHERE id=%s", (target_id,)
            ).fetchone()
            conn.close()
            if not row:
                return jsonify({"ok": False, "error": "target not found"}), 404
            return jsonify({"ok": True, "target": dict(row)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @bp.route("/api/zig/targets/<target_id>/assess", methods=["POST"])
    @sc_login_required
    def zig_api_target_assess(target_id):
        """POST /security/api/zig/targets/<id>/assess — run ZIG assessment for a target."""
        from tools.security_canvas.zig_portfolio import get_target_assessment
        try:
            result = get_target_assessment(target_id)
            # Non-repudiation: target-scoped assessment runs are audited.
            _audit(
                "zig_assessment_run", "zig_target", target_id,
                details=f"scope=target; aggregate={result.get('aggregate', {}).get('score')}",
            )
            return jsonify({"ok": True, **result})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @bp.route("/api/zig/targets/<target_id>/activities/<activity_id>", methods=["PATCH"])
    @sc_login_required
    def zig_api_target_activity_update(target_id, activity_id):
        """PATCH /security/api/zig/targets/<id>/activities/<act_id> — update activity status."""
        from tools.security_canvas.zig_activity_tracker import set_activity_status
        data = request.get_json(force=True, silent=True) or {}
        status = data.get("status", "in_progress")
        try:
            evidence_note = data.get("evidence_note")
            result = set_activity_status(
                activity_id, status,
                target_id=target_id,
                evidence_note=evidence_note,
                completed_by=data.get("completed_by", "api"),
            )
            # Non-repudiation: per-target activity completion + evidence writes
            # are audited (entity_id is the target-scoped completion key).
            _audit(
                "zig_activity_completion", "zig_activity",
                f"{target_id}:{activity_id}",
                details=(f"target={target_id}; status={status}; "
                         f"evidence={'yes' if evidence_note else 'no'}"),
            )
            return jsonify({"ok": True, **result})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @bp.route("/api/zig/targets/<target_id>/ingest", methods=["POST"])
    @sc_login_required
    def zig_api_target_ingest(target_id):
        """POST /security/api/zig/targets/<id>/ingest — ingest scan results for a target.

        Body: {"source_type": "sbom|sast|survey|nmap|openapi", "payload": <string or object>}
        """
        import json

        from tools.security_canvas.constants import ZIG_INGEST_MAX_BYTES
        from tools.security_canvas.zig_external_adapter import (
            ingest_sbom, ingest_sast, ingest_survey, ingest_nmap, ingest_openapi,
        )
        data, _err = _require_json()
        if _err:
            return _err
        source_type = data.get("source_type", "").lower()
        payload = data.get("payload")
        if not source_type or payload is None:
            return jsonify({"ok": False, "error": "source_type and payload are required"}), 400

        dispatch = {
            "sbom": ingest_sbom,
            "sast": ingest_sast,
            "survey": ingest_survey,
            "nmap": ingest_nmap,
            "openapi": ingest_openapi,
        }
        if source_type not in dispatch:
            return jsonify({"ok": False,
                            "error": f"unknown source_type; valid: {list(dispatch)}"}), 400

        # Normalize payload to a string, then reject oversized payloads with
        # HTTP 413 BEFORE any parsing (bounds memory/CPU; DoS defense).
        if not isinstance(payload, str):
            payload = json.dumps(payload)
        payload_bytes = len(payload.encode("utf-8"))
        if payload_bytes > ZIG_INGEST_MAX_BYTES:
            return jsonify({
                "ok": False,
                "error": (f"payload too large: {payload_bytes} bytes exceeds "
                          f"limit of {ZIG_INGEST_MAX_BYTES} bytes"),
                "max_bytes": ZIG_INGEST_MAX_BYTES,
            }), 413

        try:
            result = dispatch[source_type](target_id, payload)
            return jsonify({"ok": True, **result})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    # ── ZIG Portfolio ─────────────────────────────────────────────────────────

    @bp.route("/zig/portfolio")
    @sc_login_required
    def zig_portfolio_page():
        """GET /security/zig/portfolio — multi-target portfolio dashboard."""
        from tools.security_canvas.zig_pillar_scorer import score_all_pillars, aggregate_zig_score
        from types import SimpleNamespace

        try:
            pillar_scores = score_all_pillars(target_id="icdev-self")
        except Exception:
            pillar_scores = []

        # Build template-compatible 'targets' list (one entry per ZIG pillar)
        targets = []
        for ps in pillar_scores:
            targets.append(SimpleNamespace(
                slug=ps.get("slug", ""),
                name=ps.get("name", ps.get("slug", "")),
                full_name=ps.get("full_name", ps.get("name", "")),
                color=ps.get("color", "#6366f1"),
                overall_score=ps.get("score", 0.0),
                maturity_level=ps.get("maturity_level", "preparation"),
                capability_count=ps.get("capability_count", 0),
                implemented_capabilities=ps.get("implemented_capabilities", 0),
                activity_count=ps.get("activity_count", 0),
                complete_activities=ps.get("complete_activities", 0),
                in_progress_activities=ps.get("in_progress_activities", 0),
            ))

        # Aggregate health
        agg = aggregate_zig_score(pillar_scores) if pillar_scores else {"score": 0.0}
        avg_score = agg.get("score", 0.0)
        maturity_dist_raw = {}
        for ps in pillar_scores:
            ml = ps.get("maturity_level", "preparation")
            maturity_dist_raw[ml] = maturity_dist_raw.get(ml, 0) + 1

        portfolio = SimpleNamespace(
            total_targets=len(targets),
            avg_score=avg_score,
            attention_count=sum(1 for t in targets if t.overall_score < 0.4),
            advanced_count=sum(1 for t in targets if t.maturity_level == "advanced"),
            maturity_dist=SimpleNamespace(
                preparation=maturity_dist_raw.get("preparation", 0),
                basic=maturity_dist_raw.get("basic", 0),
                intermediate=maturity_dist_raw.get("intermediate", 0),
                advanced=maturity_dist_raw.get("advanced", 0),
            ),
        )

        pillar_names = [t.name for t in targets]
        radar_datasets = [{
            "name": t.name,
            "color": t.color,
            "scores_by_pillar": [t.overall_score],
        } for t in targets]
        # Radar shows a single 'ICDEV Platform' ring across all pillars
        radar_datasets = [{
            "name": "ICDEV Platform",
            "color": "#6366f1",
            "scores_by_pillar": [t.overall_score for t in targets],
        }]

        return render_template(
            "security_canvas/zig/portfolio.html",
            portfolio=portfolio,
            targets=targets,
            pillar_names=pillar_names,
            radar_datasets=radar_datasets,
            classification="CUI // SP-CTI",
        )

    @bp.route("/api/zig/portfolio/health")
    @sc_login_required
    def zig_api_portfolio_health():
        """GET /security/api/zig/portfolio/health — portfolio health JSON."""
        from tools.security_canvas.zig_portfolio import get_portfolio_health
        try:
            return jsonify({"ok": True, **get_portfolio_health()})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @bp.route("/api/zig/portfolio/compare")
    @sc_login_required
    def zig_api_portfolio_compare():
        """GET /security/api/zig/portfolio/compare?targets=id1,id2 — radar comparison."""
        from tools.security_canvas.zig_portfolio import compare_targets
        target_param = request.args.get("targets", "")
        target_ids = [t.strip() for t in target_param.split(",") if t.strip()]
        if len(target_ids) < 2:
            return jsonify({"ok": False, "error": "provide at least 2 target IDs in ?targets="}), 400
        try:
            return jsonify({"ok": True, **compare_targets(target_ids)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    return bp
