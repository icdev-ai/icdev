
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""ICDEV™ Migration Design Canvas — Flask Blueprint integration.

Fully self-contained Blueprint mounted at /migration-canvas/ inside the
ICDEV dashboard.  Uses ICDEV's auth system, separate migration_canvas.db,
and feature flag ICDEV_MIGRATION_CANVAS_ENABLED.

Usage in ICDEV dashboard app.py:
    from tools.migration_canvas.blueprint import create_migration_blueprint
    bp = create_migration_blueprint()
    if bp:
        app.register_blueprint(bp, url_prefix="/migration-canvas")
"""

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

logger = get_logger("icdev.migration_canvas")

_MC_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _MC_DIR.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"

from tools.migration_canvas.constants import (  # noqa: E402
    MIGRATION_OBJECTS,
    MC_COMPLIANCE_RULES,
    MIGRATION_TYPES,
    SOP_TYPES,
)
from tools.migration_canvas.migration_engine import (  # noqa: E402
    assess_migration_design,
    detect_migration_gaps,
    compute_readiness_score,
    get_design_stats,
)
from tools.canvas.ai_trace_mixin import record_canvas_decision  # noqa: E402


def create_migration_blueprint():
    """Create and return the Migration Design Canvas Blueprint.

    Returns None if ICDEV_MIGRATION_CANVAS_ENABLED is false.
    """
    enabled = os.environ.get("ICDEV_MIGRATION_CANVAS_ENABLED", "true").lower()
    if enabled not in ("true", "1", "yes"):
        logger.info("Migration Canvas disabled (ICDEV_MIGRATION_CANVAS_ENABLED=%s)", enabled)
        return None

    # Security: warn loudly if authentication is being bypassed outside CI/E2E.
    # ICDEV_AUTH_BYPASS short-circuits @mdc_login_required — safe only in
    # automated test/CI runs, never in a production or shared deployment.
    if os.environ.get("ICDEV_AUTH_BYPASS", "").lower() in ("1", "true", "yes"):
        _ci = any(
            os.environ.get(v)
            for v in ("CI", "GITHUB_ACTIONS", "ICDEV_E2E", "PYTEST_CURRENT_TEST")
        )
        if not _ci:
            logger.warning(
                "SECURITY: ICDEV_AUTH_BYPASS is set but no CI/E2E marker "
                "(CI / GITHUB_ACTIONS / ICDEV_E2E / PYTEST_CURRENT_TEST) was "
                "detected — Migration Canvas authentication is DISABLED. "
                "Unset ICDEV_AUTH_BYPASS outside automated test runs."
            )

    try:
        from tools.migration_canvas.db.init_db import init_db
        init_db()
    except Exception as exc:
        logger.warning("Migration Canvas DB init failed: %s", exc)

    bp = Blueprint(
        "migration_canvas",
        __name__,
        template_folder=str(_TEMPLATE_DIR),
    )

    # ── Auth wrapper ──────────────────────────────────────────────────────
    def mdc_login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            # E2E / CI bypass — consistent with tools/dashboard/auth.py, which
            # injects a synthetic admin (g.current_user) but not session['user_id'].
            import os as _os
            if _os.environ.get("ICDEV_AUTH_BYPASS", "").lower() in ("1", "true", "yes"):
                return f(*args, **kwargs)
            if not session.get("user_id"):
                if (
                    request.is_json
                    or request.path.startswith("/migration-canvas/api/")
                    or request.method in ("DELETE", "POST", "PUT")
                ):
                    return jsonify({"error": "Authentication required"}), 401
                return redirect("/login")
            return f(*args, **kwargs)
        return decorated

    # ── DB helpers ────────────────────────────────────────────────────────
    from tools.migration_canvas.db.init_db import get_connection
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
                    "INSERT INTO mc_audit (design_id, user, action, detail, created_at) VALUES (%s,%s,%s,%s,%s)",
                    (design_id, user_id, action, detail, now_isoformat()),
                )
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("_audit: best-effort INSERT into mc_audit failed (non-blocking): %s", exc)
        # Bridge to main icdev.db audit_trail for compliance chain
        try:
            from tools.db.storage import get_connection as _icdev_conn
            import json as _json
            import uuid as _uuid
            with _icdev_conn() as _ic:
                _ic.execute(
                    "INSERT INTO audit_trail (id, event_type, actor, action, details, classification, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        str(_uuid.uuid4()),
                        "migration_canvas",
                        user_id or "system",
                        action,
                        _json.dumps({"session_id": design_id, "detail": detail}),
                        "CUI // SP-CTI",
                        now_isoformat(),
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)

    def _notify(title: str, body: str, severity: str = "info"):
        """Fire-and-forget notification — never raises."""
        try:
            from tools.notifications.adapters.telegram import send as _tg_send
            _tg_send(title, body, severity=severity)
        except Exception:
            pass

    def _row_to_dict(row):
        return dict(row) if row else {}

    # ====================================================================
    # PAGE ROUTES
    # ====================================================================

    @bp.route("/")
    @mdc_login_required
    def mc_index():
        """Migration Design Canvas dashboard — list designs + recent assessments."""
        # Bound the designs list — previously unbounded, so a large table would
        # load every row on each page hit. Paginate via ?page=&per_page=.
        try:
            page = max(int(request.args.get("page", 1)), 1)
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = min(max(int(request.args.get("per_page", 50)), 1), 200)
        except (TypeError, ValueError):
            per_page = 50
        offset = (page - 1) * per_page

        with get_connection() as conn:
            # Fetch per_page+1 to detect a next page without a second COUNT
            # round-trip; the three reads below all share this one connection.
            design_rows = conn.execute(
                "SELECT id, name, description, migration_type, classification, "
                "created_at, updated_at "
                "FROM migration_designs ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                (per_page + 1, offset),
            ).fetchall()
            has_next = len(design_rows) > per_page
            designs = [_row_to_dict(r) for r in design_rows[:per_page]]
            recent_assessments = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT a.id, a.design_id, a.assessment_type, a.score, "
                    "a.grade, a.readiness_score, a.created_at, d.name AS design_name "
                    "FROM mc_assessments a "
                    "JOIN migration_designs d ON a.design_id = d.id "
                    "ORDER BY a.created_at DESC LIMIT 10"
                ).fetchall()
            ]
            templates = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT id, name, category, description, tags "
                    "FROM mc_templates ORDER BY category, name LIMIT 200"
                ).fetchall()
            ]
        return render_template(
            "migration_canvas/index.html",
            designs=designs,
            recent_assessments=recent_assessments,
            templates=templates,
            migration_types=MIGRATION_TYPES,
            page=page,
            per_page=per_page,
            has_next=has_next,
            has_prev=page > 1,
        )

    @bp.route("/canvas/<design_id>")
    @mdc_login_required
    def mc_canvas(design_id):
        """Open existing migration design canvas."""
        with get_connection() as conn:
            design = _row_to_dict(
                conn.execute("SELECT * FROM migration_designs WHERE id=%s", (design_id,)).fetchone()
            )
        if not design:
            abort(404)
        return render_template(
            "migration_canvas/canvas.html",
            design_id=design_id,
            design=design,
            migration_objects=MIGRATION_OBJECTS,
            compliance_rules=MC_COMPLIANCE_RULES,
        )

    @bp.route("/canvas/new")
    @mdc_login_required
    def mc_new_canvas():
        """New migration design canvas, optionally from template."""
        template_id = request.args.get("template")
        graph_json = '{"nodes":[],"edges":[]}'
        name = "New Migration Design"
        if template_id:
            with get_connection() as conn:
                tpl = conn.execute(
                    "SELECT name, graph_json FROM mc_templates WHERE id=%s",
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
            "migration_canvas/canvas.html",
            design_id="new",
            design=design,
            migration_objects=MIGRATION_OBJECTS,
            compliance_rules=MC_COMPLIANCE_RULES,
        )

    @bp.route("/templates")
    @mdc_login_required
    def mc_templates():
        """Template gallery page."""
        with get_connection() as conn:
            templates = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT id, name, category, description, tags FROM mc_templates ORDER BY category, name"
                ).fetchall()
            ]
        return render_template("migration_canvas/templates.html", templates=templates)

    @bp.route("/assessments")
    @mdc_login_required
    def mc_assessments():
        """Assessment history page."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT a.id, a.design_id, a.assessment_type, a.score, "
                "a.grade, a.cat1_findings, a.cat2_findings, a.cat3_findings, "
                "a.readiness_score, a.created_at, d.name AS design_name "
                "FROM mc_assessments a "
                "LEFT JOIN migration_designs d ON a.design_id = d.id "
                "ORDER BY a.created_at DESC LIMIT 50"
            ).fetchall()
            assessments = [_row_to_dict(r) for r in rows]
        return render_template("migration_canvas/assessments.html", assessments=assessments)

    @bp.route("/sops")
    @mdc_login_required
    def mc_sops_page():
        """SOP management page."""
        from tools.migration_canvas.sops import get_all_sops
        sops = get_all_sops()
        return render_template(
            "migration_canvas/sops.html",
            sops=sops,
            sop_types=SOP_TYPES,
        )

    @bp.route("/compliance-wizard")
    @mdc_login_required
    def mc_compliance_wizard():
        """Compliance gate wizard page."""
        return render_template("migration_canvas/compliance_wizard.html")

    @bp.route("/api/compliance-gate", methods=["POST"])
    @mdc_login_required
    def mc_api_compliance_gate():
        """Run the migration compliance gate check."""
        from tools.migration_canvas.compliance_gate import check_migration_compliance
        body = request.get_json(force=True, silent=True) or {}
        result = check_migration_compliance(
            il_level=body.get("il_level", ""),
            target_env=body.get("target_env", ""),
            migration_type=body.get("migration_type"),
            frameworks=body.get("frameworks"),
        )
        return jsonify(result)

    # ====================================================================
    # API ROUTES — Designs CRUD
    # ====================================================================

    @bp.route("/api/designs", methods=["POST"])
    @mdc_login_required
    def mc_api_create_design():
        """Create a new migration design."""
        data = request.get_json(force=True, silent=True) or {}
        template_id = data.get("template_id")
        if template_id:
            with get_connection() as conn:
                ex = conn.execute(
                    "SELECT id, name FROM migration_designs WHERE template_id=%s LIMIT 1",
                    (template_id,),
                ).fetchone()
            if ex:
                return jsonify({"id": ex["id"], "name": ex["name"]}), 200
        design_id = str(_uuid.uuid4())
        name = data.get("name", "Untitled Migration Design")
        now = now_isoformat()
        default_graph = json.dumps({"nodes": [], "edges": []})
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO migration_designs "
                "(id, name, description, migration_type, graph_json, template_id, "
                "classification, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    design_id,
                    name,
                    data.get("description", ""),
                    data.get("migration_type", "application"),
                    data.get("graph_json", default_graph),
                    data.get("template_id"),
                    data.get("classification", "CUI"),
                    now,
                    now,
                ),
            )
            conn.commit()
        _audit(design_id, "create", f"Created migration design: {name}")
        # Cross-canvas hooks: notify SDC, QDC, and KG builder
        try:
            from tools.security_canvas.agent import on_mdc_design_saved

            on_mdc_design_saved(design_id)
        except Exception:
            pass  # Security Canvas is optional
        try:
            from tools.qdc_canvas.agent import on_mdc_design_saved as qdc_on_mdc

            qdc_on_mdc(design_id)
        except Exception:
            pass  # QDC is optional
        try:
            from tools.canvas.kg_builder import rebuild_canvas_kg

            rebuild_canvas_kg("mdc", design_id)
        except Exception:
            pass  # KG builder is optional
        return jsonify({"id": design_id, "name": name}), 201

    @bp.route("/api/designs/<design_id>", methods=["GET"])
    @mdc_login_required
    def mc_api_get_design(design_id):
        """Get a single migration design."""
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM migration_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "Design not found"}), 404
        return jsonify(_row_to_dict(row))

    @bp.route("/api/designs/<design_id>", methods=["PUT"])
    @mdc_login_required
    def mc_api_update_design(design_id):
        """Update a migration design."""
        data = request.get_json(force=True, silent=True) or {}
        now = now_isoformat()
        with get_connection() as conn:
            existing = conn.execute("SELECT id FROM migration_designs WHERE id=%s", (design_id,)).fetchone()
            if not existing:
                return jsonify({"error": "Design not found"}), 404
            conn.execute(
                "UPDATE migration_designs SET name=%s, description=%s, migration_type=%s, "
                "graph_json=%s, classification=%s, updated_at=%s WHERE id=%s",
                (
                    data.get("name", "Untitled"),
                    data.get("description", ""),
                    data.get("migration_type", "application"),
                    data.get("graph_json", '{"nodes":[],"edges":[]}'),
                    data.get("classification", "CUI"),
                    now,
                    design_id,
                ),
            )
            conn.commit()
        _audit(design_id, "update", f"Updated design: {data.get('name', design_id)}")
        # Cross-canvas hooks: notify SDC, QDC, and KG builder
        try:
            from tools.security_canvas.agent import on_mdc_design_saved

            on_mdc_design_saved(design_id)
        except Exception:
            pass  # Security Canvas is optional
        try:
            from tools.qdc_canvas.agent import on_mdc_design_saved as qdc_on_mdc

            qdc_on_mdc(design_id)
        except Exception:
            pass  # QDC is optional
        try:
            from tools.canvas.kg_builder import rebuild_canvas_kg

            rebuild_canvas_kg("mdc", design_id)
        except Exception:
            pass  # KG builder is optional
        return jsonify({"id": design_id, "status": "updated"})

    @bp.route("/api/designs/<design_id>", methods=["DELETE"])
    @mdc_login_required
    def mc_api_delete_design(design_id):
        """Delete a migration design."""
        with get_connection() as conn:
            conn.execute("DELETE FROM migration_designs WHERE id=%s", (design_id,))
            conn.commit()
        _audit(design_id, "delete", "Design deleted")
        return jsonify({"status": "deleted"})

    @bp.route("/api/designs", methods=["DELETE"])
    @mdc_login_required
    def mc_api_delete_all_designs():
        """Delete all migration designs."""
        with get_connection() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM migration_designs").fetchall()]
            conn.execute("DELETE FROM migration_designs")
            conn.commit()
        return jsonify({"deleted": len(ids)})

    # ====================================================================
    # API ROUTES — Assessment & Analysis
    # ====================================================================

    @bp.route("/api/designs/<design_id>/assess", methods=["POST"])
    @mdc_login_required
    def mc_api_assess(design_id):
        """Run compliance assessment on a migration design."""
        data = request.get_json(force=True, silent=True) or {}
        use_cot = data.get("use_cot", False)
        chain_mode = "cot" if use_cot else ""
        with get_connection() as conn:
            row = conn.execute("SELECT graph_json FROM migration_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "Design not found"}), 404

        result = assess_migration_design(row["graph_json"])
        readiness = compute_readiness_score(row["graph_json"])

        assessment_id = str(_uuid.uuid4())
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO mc_assessments "
                "(id, design_id, assessment_type, findings_json, score, grade, "
                "cat1_findings, cat2_findings, cat3_findings, readiness_score, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    assessment_id,
                    design_id,
                    "full",
                    json.dumps(result["findings"]),
                    result["score"],
                    result["grade"],
                    result["cat1_count"],
                    result["cat2_count"],
                    result["cat3_count"],
                    readiness["overall"],
                    now_isoformat(),
                ),
            )
            conn.commit()

        _audit(design_id, "assess", f"Assessment score: {result['score']} ({result['grade']})")
        record_canvas_decision(
            canvas_type="mc",
            record_id=design_id,
            decision_type="readiness_assessment",
            decision=f"Grade {result.get('grade','?')} — Score {result.get('score',0)}, Readiness {readiness.get('overall',0)}%",
            rationale=f"CAT1={result.get('cat1_count',0)} CAT2={result.get('cat2_count',0)} CAT3={result.get('cat3_count',0)}",
            model_used=None,
            confidence=result.get("score", 0) / 100.0 if result.get("score") else None,
        )
        return jsonify({
            "assessment_id": assessment_id,
            **result,
            "readiness": readiness,
            "chain_mode": chain_mode,
        })

    @bp.route("/api/designs/<design_id>/gaps", methods=["GET"])
    @mdc_login_required
    def mc_api_gaps(design_id):
        """Detect gaps in a migration design."""
        with get_connection() as conn:
            row = conn.execute("SELECT graph_json FROM migration_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "Design not found"}), 404
        gaps = detect_migration_gaps(row["graph_json"])
        return jsonify({"design_id": design_id, "gaps": gaps, "total": len(gaps)})

    @bp.route("/api/designs/<design_id>/readiness", methods=["GET"])
    @mdc_login_required
    def mc_api_readiness(design_id):
        """Compute migration readiness score."""
        with get_connection() as conn:
            row = conn.execute("SELECT graph_json FROM migration_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "Design not found"}), 404
        readiness = compute_readiness_score(row["graph_json"])
        return jsonify({"design_id": design_id, **readiness})

    @bp.route("/api/designs/<design_id>/stats", methods=["GET"])
    @mdc_login_required
    def mc_api_stats(design_id):
        """Get design statistics."""
        with get_connection() as conn:
            row = conn.execute("SELECT graph_json FROM migration_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "Design not found"}), 404
        stats = get_design_stats(row["graph_json"])
        return jsonify({"design_id": design_id, **stats})

    # ====================================================================
    # API ROUTES — Templates & Snippets
    # ====================================================================

    @bp.route("/api/templates", methods=["GET"])
    @mdc_login_required
    def mc_api_list_templates():
        """List available migration templates."""
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM mc_templates ORDER BY category, name").fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/templates/<template_id>", methods=["GET"])
    @mdc_login_required
    def mc_api_get_template(template_id):
        """Get a single template."""
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM mc_templates WHERE id=%s", (template_id,)).fetchone()
        if not row:
            return jsonify({"error": "Template not found"}), 404
        return jsonify(_row_to_dict(row))

    @bp.route("/api/snippets", methods=["GET"])
    @mdc_login_required
    def mc_api_list_snippets():
        """List available migration snippets."""
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM mc_snippets ORDER BY category, name").fetchall()
        return jsonify({"snippets": [_row_to_dict(r) for r in rows]})

    @bp.route("/api/objects", methods=["GET"])
    @mdc_login_required
    def mc_api_objects():
        """Return the palette object definitions."""
        return jsonify(MIGRATION_OBJECTS)

    @bp.route("/api/rules", methods=["GET"])
    @mdc_login_required
    def mc_api_rules():
        """Return compliance rules."""
        return jsonify(MC_COMPLIANCE_RULES)

    # ====================================================================
    # API ROUTES — Versions
    # ====================================================================

    @bp.route("/api/versions/<design_id>", methods=["GET"])
    @mdc_login_required
    def mc_api_list_versions(design_id):
        """List version history for a design."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, design_id, version_number, change_summary, user_id, created_at "
                "FROM mc_versions WHERE design_id=%s ORDER BY version_number DESC",
                (design_id,),
            ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/versions/<design_id>", methods=["POST"])
    @mdc_login_required
    def mc_api_create_version(design_id):
        """Create a version snapshot of the current design."""
        with get_connection() as conn:
            design = conn.execute("SELECT graph_json FROM migration_designs WHERE id=%s", (design_id,)).fetchone()
            if not design:
                return jsonify({"error": "Design not found"}), 404
            max_ver = conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) FROM mc_versions WHERE design_id=%s",
                (design_id,),
            ).fetchone()[0]
            ver_id = str(_uuid.uuid4())
            data = request.get_json(force=True, silent=True) or {}
            conn.execute(
                "INSERT INTO mc_versions (id, design_id, version_number, graph_json, change_summary, user_id, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    ver_id,
                    design_id,
                    max_ver + 1,
                    design["graph_json"],
                    data.get("change_summary", ""),
                    session.get("user_id", ""),
                    now_isoformat(),
                ),
            )
            conn.commit()
        _audit(design_id, "version", f"Created version {max_ver + 1}")
        return jsonify({"version_id": ver_id, "version_number": max_ver + 1}), 201

    # ====================================================================
    # API ROUTES — SOPs
    # ====================================================================

    @bp.route("/api/sops", methods=["GET"])
    @mdc_login_required
    def mc_api_list_sops():
        from tools.migration_canvas.sops import get_all_sops
        sop_type = request.args.get("sop_type")
        status = request.args.get("approval_status")
        return jsonify(get_all_sops(sop_type=sop_type, approval_status=status))

    @bp.route("/api/sops", methods=["POST"])
    @mdc_login_required
    def mc_api_create_sop():
        from tools.migration_canvas.sops import create_sop
        data = request.get_json(force=True, silent=True) or {}
        sop = create_sop(data)
        return jsonify(sop), 201

    @bp.route("/api/sops/<sop_id>", methods=["GET"])
    @mdc_login_required
    def mc_api_get_sop(sop_id):
        from tools.migration_canvas.sops import get_sop_by_id
        sop = get_sop_by_id(sop_id)
        if not sop:
            return jsonify({"error": "SOP not found"}), 404
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>", methods=["PUT"])
    @mdc_login_required
    def mc_api_update_sop(sop_id):
        from tools.migration_canvas.sops import update_sop
        data = request.get_json(force=True, silent=True) or {}
        sop = update_sop(sop_id, data)
        if not sop:
            return jsonify({"error": "SOP not found"}), 404
        return jsonify(sop)

    @bp.route("/api/sops/<sop_id>", methods=["DELETE"])
    @mdc_login_required
    def mc_api_delete_sop(sop_id):
        from tools.migration_canvas.sops import delete_sop
        ok = delete_sop(sop_id)
        if not ok:
            return jsonify({"error": "SOP not found"}), 404
        return jsonify({"status": "deleted"})

    @bp.route("/api/sops/<sop_id>/submit", methods=["POST"])
    @mdc_login_required
    def mc_api_submit_sop(sop_id):
        from tools.migration_canvas.sops import submit_sop
        return jsonify(submit_sop(sop_id))

    @bp.route("/api/sops/<sop_id>/approve", methods=["POST"])
    @mdc_login_required
    def mc_api_approve_sop(sop_id):
        from tools.migration_canvas.sops import approve_sop
        return jsonify(approve_sop(sop_id, approved_by=session.get("user_id", "")))

    @bp.route("/api/sops/<sop_id>/reject", methods=["POST"])
    @mdc_login_required
    def mc_api_reject_sop(sop_id):
        from tools.migration_canvas.sops import reject_sop
        data = request.get_json(force=True, silent=True) or {}
        return jsonify(reject_sop(sop_id, reason=data.get("reason", "")))

    # ====================================================================
    # API ROUTES — Runbooks
    # ====================================================================

    @bp.route("/api/runbooks", methods=["GET"])
    @mdc_login_required
    def mc_api_list_runbooks():
        """List migration runbooks."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM mc_runbooks ORDER BY severity DESC, title"
            ).fetchall()
        result = []
        for r in rows:
            d = _row_to_dict(r)
            try:
                d["steps"] = json.loads(d.get("steps_json", "[]"))
            except (json.JSONDecodeError, TypeError):
                d["steps"] = []
            result.append(d)
        return jsonify(result)

    @bp.route("/api/runbooks/<runbook_id>", methods=["GET"])
    @mdc_login_required
    def mc_api_get_runbook(runbook_id):
        """Get a single runbook."""
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM mc_runbooks WHERE id=%s", (runbook_id,)).fetchone()
        if not row:
            return jsonify({"error": "Runbook not found"}), 404
        d = _row_to_dict(row)
        try:
            d["steps"] = json.loads(d.get("steps_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["steps"] = []
        return jsonify(d)

    # ====================================================================
    # API ROUTES — Oracle Anticipatory Intelligence
    # ====================================================================

    @bp.route("/api/oracle/migration", methods=["GET"])
    @mdc_login_required
    def mc_api_oracle_migration():
        """Run Oracle Migration Lens — anticipatory risk predictions."""
        try:
            from tools.oracle.lenses.lens_migration import MigrationLens
            lens = MigrationLens()
            predictions = lens.run()
            lens.persist(predictions)
            return jsonify({
                "predictions": [p.to_dict() for p in predictions],
                "count": len(predictions),
            })
        except Exception as exc:
            logger.warning("Oracle Migration Lens failed: %s", exc)
            return jsonify({"predictions": [], "count": 0, "error": str(exc)})

    @bp.route("/api/oracle/predictions", methods=["GET"])
    @mdc_login_required
    def mc_api_oracle_predictions():
        """List stored Oracle predictions for migration designs."""
        design_id = request.args.get("design_id")
        with get_connection() as conn:
            if design_id:
                rows = conn.execute(
                    "SELECT * FROM mc_oracle_predictions WHERE design_id=%s ORDER BY created_at DESC LIMIT 50",
                    (design_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM mc_oracle_predictions ORDER BY created_at DESC LIMIT 50"
                ).fetchall()
        result = []
        for r in rows:
            d = _row_to_dict(r)
            try:
                d["recommendations"] = json.loads(d.get("recommendations", "[]"))
            except (json.JSONDecodeError, TypeError):
                d["recommendations"] = []
            try:
                d["data"] = json.loads(d.get("data_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                d["data"] = {}
            result.append(d)
        return jsonify(result)

    # ====================================================================
    # NETWORK DEVICE MIGRATION — Page Routes
    # ====================================================================

    @bp.route("/network-migration/new")
    @mdc_login_required
    def mc_net_migration_new():
        """Network migration wizard — new session."""
        return render_template("migration_canvas/network_wizard.html", session_id=None)

    @bp.route("/network-migration/<session_id>")
    @mdc_login_required
    def mc_net_migration_wizard(session_id):
        """Network migration wizard — resume existing session."""
        return render_template("migration_canvas/network_wizard.html", session_id=session_id)

    @bp.route("/network-migration/<session_id>/port-diagram")
    @mdc_login_required
    def mc_net_port_diagram(session_id):
        """Physical port diagram view for a migration session."""
        return render_template("migration_canvas/port_diagram.html", session_id=session_id)

    # ====================================================================
    # NETWORK DEVICE MIGRATION — API Routes
    # ====================================================================

    from tools.migration_canvas import network_migration as _nm

    @bp.route("/api/network-migration", methods=["POST"])
    @mdc_login_required
    def mc_net_api_create():
        """Create a new network migration session (optionally linked to a design)."""
        data = request.get_json(force=True, silent=True) or {}
        sid = "nmig-" + _uuid.uuid4().hex[:12]
        src_model = data.get("src_model", "")
        tgt_model = data.get("tgt_model", "")
        engineer_context = data.get("engineer_context", "") or ""
        selected_coa = data.get("selected_coa", "") or ""

        with get_connection() as conn:
            conn.execute(
                "INSERT INTO mc_net_sessions "
                "(id, design_id, src_model, tgt_model, src_device_name, tgt_device_name, src_site, "
                "engineer_context, selected_coa, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (sid, data.get("design_id"), src_model, tgt_model,
                 data.get("src_device_name", ""), data.get("tgt_device_name", ""),
                 data.get("src_site", ""), engineer_context, selected_coa,
                 now_isoformat(), now_isoformat()),
            )
            # Link back on design if provided
            if data.get("design_id"):
                conn.execute(
                    "UPDATE migration_designs SET network_session_id=%s WHERE id=%s",
                    (sid, data["design_id"]),
                )
            conn.commit()

        try:
            _nm.seed_coa_questions(sid)
        except Exception:
            pass

        try:
            _nm._update_kg(sid, data.get("design_id"))
        except Exception:
            pass

        _audit(sid, "net_session_created", f"src={src_model} tgt={tgt_model}")
        _notify("Network Migration Started", f"Session {sid}: {src_model} → {tgt_model or 'TBD'}", "info")
        return jsonify({"id": sid, "src_model": src_model, "tgt_model": tgt_model})

    @bp.route("/api/network-migration/<sid>", methods=["GET"])
    @mdc_login_required
    def mc_net_api_get(sid):
        """Get full session with all sub-table data."""
        with get_connection() as conn:
            sess = _row_to_dict(conn.execute("SELECT * FROM mc_net_sessions WHERE id=%s", (sid,)).fetchone())
            if not sess:
                return jsonify({"error": "Session not found"}), 404
            port_map = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM mc_net_port_map WHERE session_id=%s ORDER BY id", (sid,)).fetchall()]
            sess["port_map"] = port_map
            sess["compat_checks"] = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM mc_net_compat_checks WHERE session_id=%s ORDER BY severity, category", (sid,)).fetchall()]
            sess["test_cases"] = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM mc_net_test_cases WHERE session_id=%s ORDER BY phase, seq_no", (sid,)).fetchall()]
            sess["cutover_steps"] = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM mc_net_cutover_steps WHERE session_id=%s ORDER BY seq_no", (sid,)).fetchall()]
            sess["erb"] = _row_to_dict(conn.execute(
                "SELECT * FROM mc_net_erb_metadata WHERE session_id=%s", (sid,)).fetchone())
            sess["config_map"] = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM mc_net_config_map WHERE session_id=%s ORDER BY src_section_type, created_at",
                (sid,)).fetchall()]
            sess["config_map_questions"] = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM mc_net_config_questions WHERE session_id=%s ORDER BY question_key",
                (sid,)).fetchall()]
        hw = {}
        try:
            hw = _nm.fetch_hardware_profiles(sess.get("src_model", ""), sess.get("tgt_model", ""))
        except Exception:
            pass
        # Parse stored config for interface list (used by port diagram)
        parsed_ifs = []
        if sess.get("src_config_raw") and sess.get("config_parsed"):
            try:
                parsed = _nm.parse_source_config(sess["src_config_raw"])
                parsed_ifs = parsed.get("interfaces", [])
            except Exception:
                pass
        return jsonify({
            "session": sess,
            "port_map": port_map,
            "compat_checks": sess["compat_checks"],
            "test_cases": sess["test_cases"],
            "cutover_steps": sess["cutover_steps"],
            "erb": sess["erb"],
            "config_map": sess["config_map"],
            "config_map_questions": sess["config_map_questions"],
            "hardware_profiles": hw,
            "parsed_interfaces": parsed_ifs,
        })

    @bp.route("/api/network-migration/<sid>", methods=["PATCH"])
    @mdc_login_required
    def mc_net_api_update(sid):
        """Update top-level session fields (device names, site, status)."""
        data = request.get_json(force=True, silent=True) or {}
        allowed = {"src_device_name", "tgt_device_name", "src_site", "status", "src_model", "tgt_model", "engineer_context", "selected_coa"}
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return jsonify({"error": "No valid fields"}), 400
        set_clause = ", ".join(f"{k}=%s" for k in fields)
        with get_connection() as conn:
            conn.execute(
                f"UPDATE mc_net_sessions SET {set_clause}, updated_at=%s WHERE id=%s",  # nosec B608
                list(fields.values()) + [now_isoformat(), sid],
            )
            conn.commit()
        return jsonify({"ok": True})

    @bp.route("/api/network-migration/<sid>/hardware-profiles", methods=["GET"])
    @mdc_login_required
    def mc_net_api_hw_profiles(sid):
        """Fetch hardware profiles for source and target models."""
        with get_connection() as conn:
            sess = _row_to_dict(conn.execute("SELECT src_model, tgt_model FROM mc_net_sessions WHERE id=%s", (sid,)).fetchone())
        if not sess:
            return jsonify({"error": "Session not found"}), 404
        src = request.args.get("src_model", sess.get("src_model", ""))
        tgt = request.args.get("tgt_model", sess.get("tgt_model", ""))
        result = _nm.fetch_hardware_profiles(src, tgt)
        return jsonify(result)

    @bp.route("/api/network-migration/hardware-catalog", methods=["GET"])
    @mdc_login_required
    def mc_net_api_hw_catalog():
        """List all hardware profiles from nc_hardware_profiles for device selection dropdowns."""
        vendor = request.args.get("vendor", "")
        device_type = request.args.get("device_type", "router")
        _CATALOG_COLS = (
            "id, vendor, model, model_family, device_type, form_factor, rack_units, "
            "throughput_gbps, power_typical_w, power_max_w, routing_table_size, arp_table_size, "
            "ports_json, eol_date, tags"
        )
        with _nm._nc_conn() as conn:
            if vendor:
                rows = conn.execute(
                    f"SELECT {_CATALOG_COLS} FROM nc_hardware_profiles "  # nosec B608
                    "WHERE LOWER(vendor)=LOWER(?) AND device_type=? ORDER BY vendor, model",
                    (vendor, device_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {_CATALOG_COLS} FROM nc_hardware_profiles "  # nosec B608
                    "WHERE device_type=? ORDER BY vendor, model",
                    (device_type,),
                ).fetchall()
        return jsonify({"devices": [_row_to_dict(r) for r in rows]})

    @bp.route("/api/network-migration/<sid>/parse-config", methods=["POST"])
    @mdc_login_required
    def mc_net_api_parse_config(sid):
        """Parse uploaded/pasted device config and store result in the session."""
        data = request.get_json(force=True, silent=True) or {}
        raw_config = data.get("config_text", "")
        if not raw_config.strip():
            return jsonify({"error": "config_text is required"}), 400

        parsed = _nm.parse_source_config(raw_config)

        with get_connection() as conn:
            conn.execute(
                "UPDATE mc_net_sessions SET src_config_raw=%s, config_parsed=1, updated_at=%s WHERE id=%s",
                (raw_config, now_isoformat(), sid),
            )
            conn.commit()

        # Index config into RAG (best-effort)
        try:
            _nm._index_to_rag(
                f"Network migration session {sid} source config ({parsed.get('vendor','unknown')}):\n{raw_config[:4000]}",
                f"net_migration_config:{sid}", sid,
            )
        except Exception:
            pass

        _audit(sid, "config_parsed", f"vendor={parsed['vendor']} ifaces={len(parsed['interfaces'])} bgp_peers={len(parsed['bgp_neighbors'])}")
        return jsonify(parsed)

    @bp.route("/api/network-migration/<sid>/import-diagram", methods=["POST"])
    @mdc_login_required
    def mc_net_api_import_diagram(sid):
        """Import network diagram (PNG/JPG/PDF/Visio/DrawIO) into the migration session.

        Calls NDC's ingest_diagram() to extract device/topology data, then:
        - Stores topology nodes in the session as a device inventory
        - Indexes the extracted topology into RAG
        - Updates the migration KG with device nodes and connections
        - Returns extracted devices and connections for the wizard UI
        """
        import tempfile
        import os as _os

        # Create the temp file and run ingest inside one try/finally so the
        # delete=False temp file is always unlinked — even if the write/save
        # itself fails. tmp_path is assigned right after creation (before the
        # write) so a mid-write failure still leaves it set for cleanup.
        tmp_path: str | None = None
        try:
            # Accept either multipart file upload or base64 JSON
            if request.files.get("file"):
                f = request.files["file"]
                suffix = _os.path.splitext(f.filename or "diagram.png")[1].lower() or ".png"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp_path = tmp.name
                    f.save(tmp.name)
                topology_name = request.form.get("name", f"migration-{sid}-import")
            else:
                data = request.get_json(force=True, silent=True) or {}
                if not data.get("content"):
                    return jsonify({"error": "Provide a file upload or base64 content"}), 400
                import base64 as _b64
                suffix = {"png":".png","jpg":".jpg","jpeg":".jpg","pdf":".pdf",
                          "drawio":".drawio","vsdx":".vsdx"}.get(data.get("format","png"), ".png")
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp_path = tmp.name
                    tmp.write(_b64.b64decode(data["content"]))
                topology_name = data.get("name", f"migration-{sid}-import")

            # Call NDC ingest pipeline
            from tools.network.network_ingester import ingest_diagram
            topology = ingest_diagram(tmp_path, project_id=None, topology_name=topology_name)
        except Exception as exc:
            logger.warning("Diagram ingest failed: %s", exc)
            topology = {"nodes": [], "edges": [], "error": str(exc)}
        finally:
            if tmp_path:
                try:
                    _os.unlink(tmp_path)
                except OSError:
                    pass

        nodes = topology.get("nodes") or []
        edges = topology.get("edges") or []
        confidence = topology.get("confidence", 0)

        # Build a device-inventory-style list for the wizard
        devices = []
        for node in nodes:
            props = node.get("properties") or {}
            ip = props.get("ip", "") or props.get("ip_address", "") or node.get("label", "")
            devices.append({
                "id": node.get("id", ""),
                "name": node.get("label", ""),
                "device_type": node.get("type", "unknown"),
                "vendor": props.get("vendor", ""),
                "model": props.get("model", ""),
                "ip_address": ip,
                "x": node.get("x", 0),
                "y": node.get("y", 0),
            })

        connections = []
        node_id_to_name = {n.get("id",""):n.get("label","") for n in nodes}
        for edge in edges:
            connections.append({
                "source": node_id_to_name.get(edge.get("source",""), edge.get("source","")),
                "target": node_id_to_name.get(edge.get("target",""), edge.get("target","")),
                "link_type": edge.get("type", ""),
                "label": edge.get("label", ""),
            })

        # Store extracted topology JSON in session (diagram_topology_json column — add if missing)
        topo_json = json.dumps({"nodes": nodes, "edges": edges, "confidence": confidence})
        with get_connection() as conn:
            try:
                conn.execute(
                    "UPDATE mc_net_sessions SET diagram_topology_json=%s, updated_at=%s WHERE id=%s",
                    (topo_json, now_isoformat(), sid),
                )
                conn.commit()
            except Exception:
                # Column may not exist in older schemas — alter table
                try:
                    conn.execute("ALTER TABLE mc_net_sessions ADD COLUMN diagram_topology_json TEXT")
                    conn.execute(
                        "UPDATE mc_net_sessions SET diagram_topology_json=%s, updated_at=%s WHERE id=%s",
                        (topo_json, now_isoformat(), sid),
                    )
                    conn.commit()
                except Exception as e2:
                    logger.warning("Could not save diagram_topology_json: %s", e2)

        # Index to RAG: full topology context for scenarios/what-if
        try:
            rag_text = (
                f"Network topology imported for migration session {sid} from diagram '{topology_name}'.\n"
                f"Extracted {len(nodes)} devices and {len(edges)} connections. "
                f"Confidence: {confidence:.0%}.\n\n"
                "Devices:\n" + "\n".join(
                    f"  - {d['name']} ({d['device_type']}) IP={d['ip_address']} Vendor={d['vendor']} Model={d['model']}"
                    for d in devices
                ) + "\n\nConnections:\n" + "\n".join(
                    f"  - {c['source']} --[{c['link_type']}]--> {c['target']} ({c['label']})"
                    for c in connections
                )
            )
            _nm._index_to_rag(rag_text, f"net_migration_diagram:{sid}", sid)
        except Exception:
            pass

        # Update KG: add diagram topology nodes/edges to migration KG
        try:
            with get_connection() as conn:
                sess = _row_to_dict(conn.execute("SELECT design_id FROM mc_net_sessions WHERE id=%s", (sid,)).fetchone())
            graph_nodes = [
                {"id": f"dev-{d['id']}", "label": d["name"], "type": d["device_type"],
                 "properties": {"ip": d["ip_address"], "vendor": d["vendor"], "model": d["model"],
                                "source": "diagram_import", "session_id": sid}}
                for d in devices
            ]
            graph_edges = [
                {"source": f"dev-{e.get('source','')}", "target": f"dev-{e.get('target','')}",
                 "label": e.get("label",""), "type": e.get("type","")}
                for e in edges
            ]
            from tools.canvas.kg_builder import rebuild_canvas_kg
            rebuild_canvas_kg("mdc", sess.get("design_id") or sid,
                              extra_nodes=graph_nodes, extra_edges=graph_edges)
        except Exception as kg_exc:
            logger.debug("KG update after diagram import: %s", kg_exc)

        _audit(sid, "diagram_imported", f"topology={topology_name} nodes={len(nodes)} edges={len(edges)} confidence={confidence:.2f}")
        return jsonify({
            "ok": True,
            "topology_name": topology_name,
            "devices": devices,
            "connections": connections,
            "confidence": confidence,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "rag_indexed": True,
            "kg_updated": True,
        })

    @bp.route("/api/network-migration/<sid>/port-map", methods=["GET"])
    @mdc_login_required
    def mc_net_api_get_port_map(sid):
        """Get current port mapping rows."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM mc_net_port_map WHERE session_id=%s ORDER BY id", (sid,)
            ).fetchall()
        return jsonify({"port_map": [_row_to_dict(r) for r in rows]})

    @bp.route("/api/network-migration/<sid>/port-map", methods=["POST"])
    @mdc_login_required
    def mc_net_api_save_port_map(sid):
        """Save / replace port mapping rows (accepts auto-generated or user-edited map)."""
        data = request.get_json(force=True, silent=True) or {}
        rows = data.get("port_map", [])  # list of port map row dicts

        if not rows:
            # Auto-generate: parse config from session + fetch hw profile
            with get_connection() as conn:
                sess = _row_to_dict(conn.execute("SELECT * FROM mc_net_sessions WHERE id=%s", (sid,)).fetchone())
            raw_config = sess.get("src_config_raw", "")
            if not raw_config:
                return jsonify({"error": "No config imported yet — call /parse-config first"}), 400
            parsed = _nm.parse_source_config(raw_config)
            hw = _nm.fetch_hardware_profiles(sess["src_model"], sess["tgt_model"])
            rows = _nm._generate_port_map(parsed["interfaces"], hw["target"])

        with get_connection() as conn:
            conn.execute("DELETE FROM mc_net_port_map WHERE session_id=%s", (sid,))
            for r in rows:
                conn.execute(
                    "INSERT INTO mc_net_port_map (session_id, src_interface, src_speed_gbps, src_media, "
                    "src_optic_type, src_ip_address, src_description, src_circuit_id, tgt_interface, "
                    "tgt_speed_gbps, tgt_optic_required, optic_change, speed_mismatch, cable_id, "
                    "far_end_device, far_end_port, notes, status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (sid, r.get("src_interface",""), r.get("src_speed_gbps",0), r.get("src_media",""),
                     r.get("src_optic_type",""), r.get("src_ip_address",""), r.get("src_description",""),
                     r.get("src_circuit_id",""), r.get("tgt_interface",""), r.get("tgt_speed_gbps",0),
                     r.get("tgt_optic_required",""), 1 if r.get("optic_change") else 0,
                     1 if r.get("speed_mismatch") else 0, r.get("cable_id",""),
                     r.get("far_end_device",""), r.get("far_end_port",""),
                     r.get("notes",""), r.get("status","mapped")),
                )
            conn.commit()

        _audit(sid, "port_map_saved", f"{len(rows)} rows")
        # Re-fetch from DB to return normalized rows with db column names
        with get_connection() as conn:
            saved = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM mc_net_port_map WHERE session_id=%s ORDER BY id", (sid,)).fetchall()]
        return jsonify({"ok": True, "count": len(rows), "port_map": saved})

    @bp.route("/api/network-migration/<sid>/compat-check", methods=["POST"])
    @mdc_login_required
    def mc_net_api_compat_check(sid):
        """Run auto-compatibility checks and persist results."""
        data = request.get_json(force=True, silent=True) or {}

        with get_connection() as conn:
            sess = _row_to_dict(conn.execute("SELECT * FROM mc_net_sessions WHERE id=%s", (sid,)).fetchone())
        if not sess:
            return jsonify({"error": "Session not found"}), 404

        hw = _nm.fetch_hardware_profiles(sess["src_model"], sess["tgt_model"])
        parsed = _nm.parse_source_config(sess.get("src_config_raw", "")) if sess.get("src_config_raw") else {}

        checks = _nm._check_compatibility(hw["source"], hw["target"], parsed)

        # Apply any manual overrides from request body
        overrides = {r["check_name"]: r.get("override_reason", "") for r in data.get("overrides", [])}
        for c in checks:
            if c["check_name"] in overrides:
                c["override_reason"] = overrides[c["check_name"]]
                if overrides[c["check_name"]]:
                    c["status"] = "overridden"

        with get_connection() as conn:
            conn.execute("DELETE FROM mc_net_compat_checks WHERE session_id=%s AND auto_detected=1", (sid,))
            for c in checks:
                conn.execute(
                    "INSERT INTO mc_net_compat_checks (session_id, category, check_name, expected, actual, "
                    "severity, status, override_reason, auto_detected) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (sid, c["category"], c["check_name"], c["expected"], c["actual"],
                     c["severity"], c["status"], c.get("override_reason",""), 1),
                )
            conn.commit()

        _audit(sid, "compat_check_run", f"{len(checks)} checks; {sum(1 for c in checks if c['status']=='fail')} fails")
        return jsonify({"checks": checks, "total": len(checks),
                        "blockers": sum(1 for c in checks if c["status"]=="fail" and not c.get("override_reason"))})

    # ── COA selection (engineer context + yes/no questions + recommendation) ────

    @bp.route("/api/network-migration/<sid>/coa-questions", methods=["GET"])
    @mdc_login_required
    def mc_net_api_get_coa_questions(sid):
        """Get yes/no questions used to recommend a Course of Action."""
        return jsonify(_nm.get_coa_questions(sid))

    @bp.route("/api/network-migration/<sid>/coa-questions", methods=["POST"])
    @mdc_login_required
    def mc_net_api_save_coa_questions(sid):
        """Save COA question answers and return a fresh recommendation."""
        data = request.get_json(force=True, silent=True) or {}
        answers = data.get("answers", {})
        _nm.save_coa_answers(sid, answers)
        result = _nm.recommend_coa(sid)
        return jsonify(result)

    @bp.route("/api/network-migration/<sid>/recommend-coa", methods=["GET"])
    @mdc_login_required
    def mc_net_api_recommend_coa(sid):
        """Return the current COA recommendation."""
        return jsonify(_nm.recommend_coa(sid))

    @bp.route("/api/network-migration/<sid>/select-coa", methods=["POST"])
    @mdc_login_required
    def mc_net_api_select_coa(sid):
        """Accept or override the recommended COA."""
        data = request.get_json(force=True, silent=True) or {}
        coa = data.get("coa", "").strip().lower()
        context = data.get("engineer_context", "")
        if coa not in ("coa_a", "coa_b", "coa_c"):
            return jsonify({"error": "coa must be one of coa_a, coa_b, coa_c"}), 400
        result = _nm.select_coa(sid, coa, context=context)
        _audit(sid, "coa_selected", coa)
        return jsonify(result)

    @bp.route("/api/network-migration/<sid>/topology", methods=["GET", "POST"])
    @mdc_login_required
    def mc_net_api_topology(sid):
        """GET returns stored topology JSON; POST rebuilds/refreshes it."""
        if request.method == "POST":
            try:
                result = _nm.build_topology(sid, refresh=True)
                _audit(sid, "topology_refreshed", f"nodes={len(result.get('graph_json', {}).get('nodes', []))}")
                return jsonify(result)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 404
            except Exception as exc:
                logger.exception("Topology build failed for %s", sid)
                return jsonify({"error": str(exc)}), 500
        # GET
        with get_connection() as conn:
            row = conn.execute(
                "SELECT topology_json, topology_neighbors_json FROM mc_net_sessions WHERE id=%s", (sid,)
            ).fetchone()
        if not row:
            return jsonify({"error": "Session not found"}), 404
        return jsonify({
            "session_id": sid,
            "graph_json": json.loads(row[0] or "{}"),
            "neighbors": json.loads(row[1] or "[]"),
            "source": "stored",
        })

    # ── Config mapping (AI-assisted, HITL-reviewed) ───────────────────────────

    @bp.route("/api/network-migration/<sid>/config-map/questions", methods=["GET"])
    @mdc_login_required
    def mc_net_api_get_config_questions(sid):
        """Get yes/no questions for config mapping."""
        return jsonify(_nm.generate_config_map_questions(sid))

    @bp.route("/api/network-migration/<sid>/config-map/questions", methods=["POST"])
    @mdc_login_required
    def mc_net_api_save_config_questions(sid):
        """Save user answers and regenerate proposals."""
        data = request.get_json(force=True, silent=True) or {}
        answers = data.get("answers", {})
        result = _nm.propose_config_mapping(sid, answers=answers)
        return jsonify(result)

    @bp.route("/api/network-migration/<sid>/config-map/generate", methods=["POST"])
    @mdc_login_required
    def mc_net_api_generate_config_map(sid):
        """Generate (or regenerate) AI config mapping proposals."""
        data = request.get_json(force=True, silent=True) or {}
        use_llm = data.get("use_llm", True)
        answers = data.get("answers", {})
        result = _nm.propose_config_mapping(sid, answers=answers, use_llm=use_llm)
        return jsonify(result)

    @bp.route("/api/network-migration/<sid>/config-map", methods=["GET"])
    @mdc_login_required
    def mc_net_api_get_config_map(sid):
        """List persisted config mapping proposals."""
        return jsonify(_nm.get_config_map(sid))

    @bp.route("/api/network-migration/<sid>/config-map/<mid>/decide", methods=["POST"])
    @mdc_login_required
    def mc_net_api_decide_config_map(sid, mid):
        """Approve / reject / skip a single mapping row."""
        data = request.get_json(force=True, silent=True) or {}
        decision = data.get("decision", "pending")
        note = data.get("note", "")
        result = _nm.decide_config_map_row(sid, mid, decision, note)
        if result.get("error"):
            return jsonify(result), 400
        return jsonify(result)

    @bp.route("/api/network-migration/<sid>/config-map/apply", methods=["POST"])
    @mdc_login_required
    def mc_net_api_apply_config_map(sid):
        """Apply approved mapping rows to produce target config."""
        result = _nm.apply_approved_config_map(sid)
        if result.get("error"):
            return jsonify(result), 400
        _audit(sid, "config_map_applied", f"approved={result.get('approved_count',0)}")
        return jsonify(result)

    @bp.route("/api/network-migration/<sid>/config-map/export", methods=["GET"])
    @mdc_login_required
    def mc_net_api_export_config_map(sid):
        """Export the config map as JSON."""
        data = _nm.get_config_map(sid)
        return jsonify(data)

    @bp.route("/api/network-migration/<sid>/convert-config", methods=["POST"])
    @mdc_login_required
    def mc_net_api_convert_config(sid):
        """Convert source config to target config and run commit-check simulation."""
        with get_connection() as conn:
            sess = _row_to_dict(conn.execute("SELECT * FROM mc_net_sessions WHERE id=%s", (sid,)).fetchone())
            port_map = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM mc_net_port_map WHERE session_id=%s", (sid,)).fetchall()]
        if not sess or not sess.get("src_config_raw"):
            return jsonify({"error": "No config imported — call /parse-config first"}), 400

        parsed = _nm.parse_source_config(sess["src_config_raw"])
        hw = _nm.fetch_hardware_profiles(sess["src_model"], sess["tgt_model"])

        converted = _nm.convert_config(
            sess["src_config_raw"], port_map,
            src_vendor=parsed.get("vendor", ""),
            tgt_model=sess.get("tgt_model", ""),
        )
        commit_findings = _nm.simulate_commit_check(
            converted["target"], parsed.get("vendor", ""), hw["target"]
        )

        try:
            _nm._index_to_rag(
                f"Net migration {sid} converted config:\n{converted['target'][:4000]}",
                f"net_migration_converted_config:{sid}", sid,
            )
        except Exception:
            pass

        return jsonify({
            "source_config": converted["source"],
            "target_config": converted["target"],
            "diff": converted["diff"],
            "commit_check": commit_findings,
            "has_errors": any(f["status"] == "fail" for f in commit_findings),
        })

    @bp.route("/api/network-migration/<sid>/test-cases", methods=["GET"])
    @mdc_login_required
    def mc_net_api_get_tests(sid):
        """Get test cases, optionally seeding defaults if empty."""
        auto_seed = request.args.get("seed", "false").lower() == "true"
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM mc_net_test_cases WHERE session_id=%s ORDER BY phase, seq_no", (sid,)
            ).fetchall()
            if not rows and auto_seed:
                # Get vendor from session config
                sess = _row_to_dict(conn.execute("SELECT src_config_raw FROM mc_net_sessions WHERE id=%s", (sid,)).fetchone())
                vendor = ""
                if sess.get("src_config_raw"):
                    try:
                        vendor = _nm.parse_source_config(sess["src_config_raw"]).get("vendor", "")
                    except Exception:
                        pass
                seeded = _nm.seed_test_cases(vendor)
                for t in seeded:
                    conn.execute(
                        "INSERT INTO mc_net_test_cases (session_id, phase, seq_no, test_name, procedure, expected_result) "
                        "VALUES (%s,%s,%s,%s,%s,%s)",
                        (sid, t["phase"], t["seq_no"], t["test_name"], t["procedure"], t["expected_result"]),
                    )
                conn.commit()
                rows = conn.execute(
                    "SELECT * FROM mc_net_test_cases WHERE session_id=%s ORDER BY phase, seq_no", (sid,)
                ).fetchall()
        return jsonify({"test_cases": [_row_to_dict(r) for r in rows]})

    @bp.route("/api/network-migration/<sid>/test-cases", methods=["POST"])
    @mdc_login_required
    def mc_net_api_save_tests(sid):
        """Save test case results (bulk update passed/actual_result)."""
        data = request.get_json(force=True, silent=True) or {}
        # Accept 'test_cases' (full objects from wizard) or legacy 'results'
        updates = data.get("test_cases") or data.get("results", [])
        with get_connection() as conn:
            for u in updates:
                conn.execute(
                    "UPDATE mc_net_test_cases SET passed=%s, actual_result=%s, notes=%s, executed_at=%s, updated_at=%s WHERE id=%s AND session_id=%s",
                    (u.get("passed"), u.get("actual_result",""), u.get("notes",""),
                     now_isoformat(), now_isoformat(), u["id"], sid),
                )
            conn.commit()
        return jsonify({"ok": True, "updated": len(updates)})

    @bp.route("/api/network-migration/<sid>/cutover-steps", methods=["GET"])
    @mdc_login_required
    def mc_net_api_get_cutover(sid):
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM mc_net_cutover_steps WHERE session_id=%s ORDER BY seq_no", (sid,)
            ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/network-migration/<sid>/cutover-steps", methods=["POST"])
    @mdc_login_required
    def mc_net_api_save_cutover(sid):
        """Generate or save cutover sequence steps."""
        data = request.get_json(force=True, silent=True) or {}
        steps = data.get("steps")

        if not steps:
            # Auto-generate from port map
            with get_connection() as conn:
                sess = _row_to_dict(conn.execute("SELECT src_config_raw FROM mc_net_sessions WHERE id=%s", (sid,)).fetchone())
                port_map = [_row_to_dict(r) for r in conn.execute(
                    "SELECT * FROM mc_net_port_map WHERE session_id=%s", (sid,)).fetchall()]
            parsed = _nm.parse_source_config(sess.get("src_config_raw","")) if sess.get("src_config_raw") else {}
            steps = _nm.build_cutover_sequence(port_map, data.get("strategy","traffic_volume_asc"), parsed)

        with get_connection() as conn:
            conn.execute("DELETE FROM mc_net_cutover_steps WHERE session_id=%s", (sid,))
            for s in steps:
                conn.execute(
                    "INSERT INTO mc_net_cutover_steps (session_id, seq_no, circuit_id, interface, description, "
                    "drain_action, cutover_action, verify_action, rollback_action, duration_min, status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (sid, s.get("seq_no",0), s.get("circuit_id",""), s.get("interface",""), s.get("description",""),
                     s.get("drain_action",""), s.get("cutover_action",""), s.get("verify_action",""),
                     s.get("rollback_action",""), s.get("duration_min",5), s.get("status","pending")),
                )
            conn.commit()

        _audit(sid, "cutover_steps_saved", f"{len(steps)} steps")
        _notify("Cutover Plan Saved", f"Session {sid}: {len(steps)} cutover steps recorded", "info")
        return jsonify({"ok": True, "count": len(steps), "steps": steps})

    @bp.route("/api/network-migration/<sid>/erb-metadata", methods=["POST"])
    @mdc_login_required
    def mc_net_api_save_erb(sid):
        """Save ERB/CCB metadata fields."""
        data = request.get_json(force=True, silent=True) or {}
        eid = "erb-" + _uuid.uuid4().hex[:8]
        allowed = {
            "change_type","risk_tier","business_justification","impact_summary",
            "rollback_plan","mw_start","mw_end","go_nogo_criteria","requestor"
        }
        fields = {k: v for k, v in data.items() if k in allowed}

        with get_connection() as conn:
            existing = conn.execute("SELECT id FROM mc_net_erb_metadata WHERE session_id=%s", (sid,)).fetchone()
            if existing:
                set_clause = ", ".join(f"{k}=%s" for k in fields)
                conn.execute(
                    f"UPDATE mc_net_erb_metadata SET {set_clause}, updated_at=%s WHERE session_id=%s",  # nosec B608
                    list(fields.values()) + [now_isoformat(), sid],
                )
            else:
                fields["id"] = eid
                fields["session_id"] = sid
                fields["created_at"] = now_isoformat()
                fields["updated_at"] = now_isoformat()
                cols = ", ".join(fields.keys())
                placeholders = ", ".join(["%s"] * len(fields))
                conn.execute(f"INSERT INTO mc_net_erb_metadata ({cols}) VALUES ({placeholders})", list(fields.values()))  # nosec B608
            conn.commit()

        return jsonify({"ok": True})

    @bp.route("/api/network-migration/<sid>/erb-package", methods=["GET"])
    @mdc_login_required
    def mc_net_api_erb_package(sid):
        """Get assembled ERB/CCB package dict."""
        pkg = _nm.generate_erb_package(sid)
        try:
            _nm._index_to_rag(
                f"ERB/CCB package for migration {sid}: risk={pkg['risk_assessment']['risk_tier']} "
                f"interfaces={pkg['impact_analysis']['total_interfaces']}",
                f"net_migration_erb:{sid}", sid,
            )
        except Exception:
            pass
        return jsonify(pkg)

    @bp.route("/api/network-migration/<sid>/erb-package/pdf")
    @mdc_login_required
    def mc_net_api_erb_pdf(sid):
        """Render print-ready ERB/CCB HTML page (Ctrl+P → Save as PDF)."""
        package = _nm.generate_erb_package(sid)
        return render_template("migration_canvas/network_erb_print.html", package=package, session_id=sid)

    @bp.route("/api/network-migration/<sid>/erb-submit", methods=["POST"])
    @mdc_login_required
    def mc_net_api_erb_submit(sid):
        """Submit ERB/CCB package into mc_sops approval workflow."""
        pkg = _nm.generate_erb_package(sid)
        sess = pkg.get("session", {})
        sop_id = "sop-net-erb-" + sid[:8]
        title = f"Network Migration ERB/CCB: {sess.get('src_model','')} → {sess.get('tgt_model','')} ({sess.get('src_device_name','')or sid})"

        steps_json = json.dumps([
            {"step": 1, "action": "NOC Lead Review", "owner": "noc_lead", "status": "pending"},
            {"step": 2, "action": "Network Architect Approval", "owner": "net_arch", "status": "pending"},
            {"step": 3, "action": "Change Manager Sign-off", "owner": "change_mgr", "status": "pending"},
        ])

        from tools.migration_canvas.sops import create_sop
        create_sop({
            "id": sop_id,
            "title": title,
            "sop_type": "network_erb_ccb",
            "description": f"ERB/CCB change request for {sess.get('src_model','')} → {sess.get('tgt_model','')} migration.",
            "purpose": "Obtain formal approval before executing network device migration.",
            "scope": f"Device: {sess.get('src_device_name','')}, Site: {sess.get('src_site','')}",
            "steps": steps_json,
            "nist_controls": json.dumps(["CM-2", "CM-3", "CM-6", "SA-10"]),
            "approval_status": "pending",
        })
        # Update ERB metadata with sop_id
        with get_connection() as conn:
            conn.execute(
                "UPDATE mc_net_erb_metadata SET sop_id=%s, approval_status='pending', updated_at=%s WHERE session_id=%s",
                (sop_id, now_isoformat(), sid),
            )
            conn.commit()

        _audit(sid, "erb_submitted", f"sop_id={sop_id}")
        return jsonify({"ok": True, "sop_id": sop_id, "title": title})

    @bp.route("/api/network-migration/<sid>/readiness", methods=["GET"])
    @mdc_login_required
    def mc_net_api_readiness(sid):
        """Get readiness score and blockers list."""
        return jsonify(_nm.compute_readiness(sid))

    # ── NMCE: Inventory page + API ────────────────────────────────────────

    @bp.route("/network-migration/")
    @mdc_login_required
    def mc_net_inventory_page():
        """Network inventory dashboard — list all devices, start migrations."""
        devices = []
        try:
            devices = _nm.get_network_inventory()
        except Exception as exc:
            logger.warning("Inventory fetch failed: %s", exc)
        eol_1yr = sum(1 for d in devices if _is_eol_within(d.get("eol_date", ""), 1))
        eol_2yr = sum(1 for d in devices if _is_eol_within(d.get("eol_date", ""), 2))
        with get_connection() as conn:
            active_count = conn.execute(
                "SELECT COUNT(*) FROM mc_net_sessions WHERE status NOT IN ('complete','archived')"
            ).fetchone()[0]
        return render_template(
            "migration_canvas/network_inventory.html",
            devices=devices,
            total=len(devices),
            eol_1yr=eol_1yr,
            eol_2yr=eol_2yr,
            active_count=active_count,
        )

    def _is_eol_within(eol_date: str, years: int) -> bool:
        if not eol_date:
            return False
        try:
            from datetime import date
            eol = date.fromisoformat(eol_date[:10])
            delta = (eol - date.today()).days
            return 0 <= delta <= years * 365
        except Exception:
            return False

    @bp.route("/api/network-migration/inventory", methods=["GET"])
    @mdc_login_required
    def mc_net_api_inventory():
        """Return network device inventory with EOL and config status."""
        site = request.args.get("site", "")
        device_type = request.args.get("device_type", "")
        vendor = request.args.get("vendor", "")
        try:
            eol_years = int(request.args.get("eol_within_years", 0))
        except (ValueError, TypeError):
            eol_years = 0
        devices = _nm.get_network_inventory(site=site, device_type=device_type,
                                             vendor=vendor, eol_within_years=eol_years)
        eol_soon = sum(1 for d in devices if _is_eol_within(d.get("eol_date", ""), 2))
        return jsonify({"devices": devices, "total": len(devices), "eol_soon_count": eol_soon})

    # ── NMCE: Session creation enhancement (auto-load DB config) ─────────

    @bp.route("/api/network-migration/create-from-inventory", methods=["POST"])
    @mdc_login_required
    def mc_net_api_create_from_inventory():
        """Create a network migration session from an inventory device_id.

        Auto-loads config from ni_device_configs if available, parses it,
        and returns config_auto_loaded flag + parsed_summary.
        """
        data = request.get_json(force=True, silent=True) or {}
        device_id = data.get("device_id", "")
        src_model = data.get("src_model", "")
        sid = "nmig-" + _uuid.uuid4().hex[:12]
        now = now_isoformat()

        # Auto-load config from DB
        config_raw = ""
        config_auto_loaded = False
        parsed_summary: dict = {}
        if device_id:
            config_raw = _nm.load_device_config_from_db(device_id) or ""
            if config_raw:
                config_auto_loaded = True
                try:
                    parsed = _nm.parse_source_config(config_raw)
                    parsed_summary = {
                        "vendor": parsed.get("vendor", ""),
                        "hostname": parsed.get("hostname", ""),
                        "interface_count": parsed.get("raw_interface_count", 0),
                        "bgp_peer_count": len(parsed.get("bgp_neighbors", [])),
                        "ospf_area_count": len(parsed.get("ospf_areas", [])),
                        "lag_count": parsed.get("lag_count", 0),
                    }
                except Exception:
                    pass

        with get_connection() as conn:
            conn.execute(
                "INSERT INTO mc_net_sessions "
                "(id, design_id, src_model, tgt_model, src_device_name, tgt_device_name, "
                "src_site, src_config_raw, config_parsed, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (sid, data.get("design_id"), src_model, data.get("tgt_model", ""),
                 data.get("src_device_name", ""), data.get("tgt_device_name", ""),
                 data.get("src_site", ""),
                 config_raw, 1 if config_auto_loaded else 0, now, now),
            )
            conn.commit()

        _audit(sid, "net_session_created_from_inventory",
               f"device_id={device_id} config_auto_loaded={config_auto_loaded}")
        _notify("Network Migration Started", f"Session {sid}: {src_model} (from inventory)", "info")
        return jsonify({
            "id": sid,
            "src_model": src_model,
            "config_auto_loaded": config_auto_loaded,
            "parsed_summary": parsed_summary,
        }), 201

    @bp.route("/api/network-migration/<sid>/upload-config", methods=["POST"])
    @mdc_login_required
    def mc_net_api_upload_config(sid):
        """Unified config ingestion: file upload, paste, or reload from DB.

        Accepts multipart/form-data (file field) or JSON body:
          {config_text: str, source: 'paste'|'upload'|'db', device_id: str}
        """

        config_text = ""
        source = "upload"

        if request.files.get("file"):
            f = request.files["file"]
            config_text = f.read().decode("utf-8", errors="replace")
            source = "upload"
        else:
            body = request.get_json(force=True, silent=True) or {}
            source = body.get("source", "paste")
            if source == "db":
                device_id = body.get("device_id", "")
                config_text = _nm.load_device_config_from_db(device_id) or ""
                if not config_text:
                    return jsonify({"error": "No config found in database for this device"}), 404
            else:
                config_text = body.get("config_text", "")

        if not config_text.strip():
            return jsonify({"error": "config_text is empty"}), 400

        parsed = _nm.parse_source_config(config_text)

        with get_connection() as conn:
            conn.execute(
                "UPDATE mc_net_sessions SET src_config_raw=%s, config_parsed=1, updated_at=%s WHERE id=%s",
                (config_text, now_isoformat(), sid),
            )
            conn.commit()

        _audit(sid, "config_uploaded", f"source={source} vendor={parsed.get('vendor','?')} "
               f"ifaces={len(parsed.get('interfaces',[]))}")
        return jsonify({**parsed, "config_source": source, "ok": True})

    # ── NMCE: AI routes ───────────────────────────────────────────────────

    @bp.route("/api/network-migration/<sid>/ai-recommend", methods=["POST"])
    @mdc_login_required
    def mc_net_api_ai_recommend(sid):
        """AI hardware recommendation for target device selection."""
        data = request.get_json(force=True, silent=True) or {}
        device_info = data.get("device_info", {})
        engineer_notes = data.get("engineer_notes", "")
        if not device_info:
            with get_connection() as conn:
                sess = conn.execute(
                    "SELECT src_model FROM mc_net_sessions WHERE id=%s", (sid,)
                ).fetchone()
            if sess:
                device_info = {"model": sess[0], "device_type": "router"}
        result = _nm.recommend_hardware(device_info, engineer_notes, sid)
        return jsonify(result)

    @bp.route("/api/network-migration/<sid>/ai-assist", methods=["POST"])
    @mdc_login_required
    def mc_net_api_ai_assist(sid):
        """Contextual AI assistant for migration questions."""
        data = request.get_json(force=True, silent=True) or {}
        prompt = data.get("prompt", "").strip()
        if not prompt:
            return jsonify({"error": "prompt is required"}), 400
        result = _nm.ai_assist(sid, prompt)
        return jsonify(result)

    @bp.route("/api/network-migration/<sid>/protocol-plan", methods=["GET"])
    @mdc_login_required
    def mc_net_api_get_protocol_plan(sid):
        """Get stored per-protocol migration plans."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT protocol, migration_steps_json, risk_level, ai_notes, status "
                "FROM mc_net_protocol_plans WHERE session_id=%s", (sid,)
            ).fetchall()
        protocols = {}
        for r in rows:
            steps = []
            try:
                steps = json.loads(r[1] or "[]")
            except Exception:
                pass
            protocols[r[0]] = {
                "steps": steps,
                "risk_level": r[2],
                "ai_notes": r[3] or "",
                "status": r[4],
            }
        return jsonify({"protocols": protocols})

    @bp.route("/api/network-migration/<sid>/protocol-plan", methods=["POST"])
    @mdc_login_required
    def mc_net_api_gen_protocol_plan(sid):
        """Generate per-protocol migration plans from parsed source config."""
        result = _nm.plan_protocol_migration(sid)
        if "error" in result:
            return jsonify(result), 400
        _audit(sid, "protocol_plan_generated",
               f"protocols={list(result.get('protocols', {}).keys())}")
        return jsonify(result)

    @bp.route("/api/network-migration/<sid>/parallel-timeline", methods=["GET"])
    @mdc_login_required
    def mc_net_api_get_timeline(sid):
        """Get stored parallel operation timeline milestones."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, milestone_name, description, days_before_cutover, phase, "
                "owner, duration_hours, status, notes "
                "FROM mc_net_parallel_timelines WHERE session_id=%s "
                "ORDER BY days_before_cutover", (sid,)
            ).fetchall()
        return jsonify({"milestones": [dict(r) for r in rows]})

    @bp.route("/api/network-migration/<sid>/parallel-timeline", methods=["POST"])
    @mdc_login_required
    def mc_net_api_gen_timeline(sid):
        """Generate parallel operation milestone timeline."""
        milestones = _nm.build_parallel_timeline(sid)
        _audit(sid, "parallel_timeline_generated", f"milestones={len(milestones)}")
        _notify("Cutover Timeline Ready", f"Session {sid}: {len(milestones)}-milestone parallel operation timeline generated", "info")
        return jsonify({"milestones": milestones, "count": len(milestones)})

    @bp.route("/api/network-migration/<sid>/export-diagram", methods=["GET"])
    @mdc_login_required
    def mc_net_api_export_diagram(sid):
        """Export port diagram as SVG or DrawIO JSON."""
        fmt = request.args.get("format", "svg")
        with get_connection() as conn:
            sess = _row_to_dict(conn.execute("SELECT * FROM mc_net_sessions WHERE id=%s", (sid,)).fetchone())
            port_map = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM mc_net_port_map WHERE session_id=%s ORDER BY id", (sid,)).fetchall()]
        hw = _nm.fetch_hardware_profiles(sess.get("src_model",""), sess.get("tgt_model",""))

        if fmt == "drawio":
            diagram = _build_drawio_diagram(sess, port_map, hw)
            return jsonify({"format": "drawio", "diagram": diagram})
        # SVG (default)
        svg = _build_svg_diagram(sess, port_map, hw)
        from flask import Response
        return Response(svg, mimetype="image/svg+xml",
                        headers={"Content-Disposition": f"inline; filename=port-diagram-{sid}.svg"})

    def _build_svg_diagram(sess, port_map, hw):
        """Build a minimal SVG front-panel diagram string server-side."""
        src_model = sess.get("src_model", "Source")
        tgt_model = sess.get("tgt_model", "Target")
        src_ports = [r for r in port_map if r.get("status") != "no-migration"]

        colors = {"mapped": "#22c55e", "unmapped": "#ef4444", "no-migration": "#6b7280", "pending": "#f59e0b"}

        port_w, port_h, gap = 30, 24, 4
        cols = 12
        rows_count = max(1, -(-len(src_ports) // cols))
        panel_w = cols * (port_w + gap) + gap
        panel_h = rows_count * (port_h + gap) + gap + 28

        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{panel_w * 2 + 80}" height="{panel_h + 60}" '
            f'font-family="monospace" font-size="10">',
            f'<rect x="0" y="0" width="{panel_w * 2 + 80}" height="{panel_h + 60}" fill="#0f172a" rx="8"/>',
            f'<text x="{panel_w//2}" y="20" text-anchor="middle" fill="#94a3b8">{src_model}</text>',
            f'<text x="{panel_w + 80 + panel_w//2}" y="20" text-anchor="middle" fill="#94a3b8">{tgt_model}</text>',
        ]

        for idx, row in enumerate(src_ports):
            col = idx % cols
            r = idx // cols
            x = gap + col * (port_w + gap)
            y = 28 + gap + r * (port_h + gap)
            color = colors.get(row.get("status", "pending"), "#f59e0b")
            tip = row.get("src_interface", "") + " → " + (row.get("tgt_interface", "") or "?")
            svg_parts.append(f'<rect x="{x}" y="{y}" width="{port_w}" height="{port_h}" fill="{color}" rx="3"><title>{tip}</title></rect>')
            label = (row.get("src_interface","") or "").split("/")[-1][:4]
            svg_parts.append(f'<text x="{x + port_w//2}" y="{y + port_h//2 + 4}" text-anchor="middle" fill="#fff" font-size="8">{label}</text>')

        # Arrow
        svg_parts.append(f'<text x="{panel_w + 40}" y="{panel_h//2 + 28}" text-anchor="middle" fill="#64748b" font-size="24">&#x2192;</text>')

        svg_parts.append("</svg>")
        return "\n".join(svg_parts)

    def _build_drawio_diagram(sess, port_map, hw):
        """Build a DrawIO-compatible XML string for the port mapping."""
        src_model = sess.get("src_model", "Source")
        tgt_model = sess.get("tgt_model", "Target")
        cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
        cid = 2

        cells.append(
            f'<mxCell id="{cid}" value="{src_model}" style="rounded=1;fillColor=#1e293b;fontColor=#94a3b8;strokeColor=#334155;" vertex="1" parent="1"><mxGeometry x="20" y="20" width="200" height="60" as="geometry"/></mxCell>'
        )
        src_cell = cid
        cid += 1

        cells.append(
            f'<mxCell id="{cid}" value="{tgt_model}" style="rounded=1;fillColor=#1e293b;fontColor=#94a3b8;strokeColor=#334155;" vertex="1" parent="1"><mxGeometry x="400" y="20" width="200" height="60" as="geometry"/></mxCell>'
        )
        tgt_cell = cid
        cid += 1

        for row in port_map:
            if row.get("status") == "no-migration":
                continue
            label = f'{row.get("src_interface","")} → {row.get("tgt_interface","")}'
            color = "#22c55e" if row.get("status") == "mapped" else "#ef4444"
            cells.append(
                f'<mxCell id="{cid}" value="{label}" style="edgeStyle=orthogonalEdgeStyle;strokeColor={color};" edge="1" source="{src_cell}" target="{tgt_cell}" parent="1"><mxGeometry relative="1" as="geometry"/></mxCell>'
            )
            cid += 1

        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<mxGraphModel><root>' + "".join(cells) + "</root></mxGraphModel>"
        )
        return xml

    # ── Device ingestion routes ──────────────────────────────────────────────

    @bp.route("/api/network-migration/import/topologies", methods=["GET"])
    @mdc_login_required
    def mc_net_api_import_topologies():
        """Return list of existing topologies for the topology-import selector."""
        try:
            topos = _nm.list_topologies()
            return jsonify({"topologies": topos, "count": len(topos)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/network-migration/import/csv", methods=["POST"])
    @mdc_login_required
    def mc_net_api_import_csv():
        """Bulk-import devices from an uploaded CSV or JSON file.

        Accepts multipart file upload (field: 'file') or JSON body with
        {'file_content': '...', 'filename': '...', 'topology_id': optional}.
        """
        try:
            topology_id = None
            if request.content_type and "multipart" in request.content_type:
                f = request.files.get("file")
                if not f:
                    return jsonify({"error": "No file uploaded"}), 400
                filename = f.filename or "upload.csv"
                allowed = {".csv", ".json", ".txt"}
                from pathlib import Path as _P
                if _P(filename).suffix.lower() not in allowed:
                    return jsonify({"error": f"Unsupported file type: {filename}"}), 400
                content = f.read()
                topology_id = request.form.get("topology_id") or None
            else:
                body = request.get_json(force=True) or {}
                raw = body.get("file_content", "")
                if not raw:
                    return jsonify({"error": "file_content required"}), 400
                content = raw.encode("utf-8") if isinstance(raw, str) else raw
                filename = body.get("filename", "upload.csv")
                topology_id = body.get("topology_id") or None

            result = _nm.ingest_devices_csv(content, topology_id=topology_id, filename=filename)
            if "error" in result:
                return jsonify(result), 400
            _audit(None, "import_csv", f"imported {result.get('created',0)} new, {result.get('updated',0)} updated")
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/network-migration/import/netbox", methods=["POST"])
    @mdc_login_required
    def mc_net_api_import_netbox():
        """Sync devices from NetBox into ni_devices.

        Body: {'topology_id': optional, 'test_only': bool}
        """
        try:
            body = request.get_json(force=True) or {}
            topology_id = body.get("topology_id") or None
            test_only = bool(body.get("test_only", False))
            result = _nm.ingest_devices_netbox(topology_id=topology_id, test_only=test_only)
            if "error" in result:
                return jsonify(result), 400
            if not test_only:
                _audit(None, "import_netbox", f"synced {result.get('total',0)} devices")
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/network-migration/import/topology", methods=["POST"])
    @mdc_login_required
    def mc_net_api_import_topology():
        """Re-ingest nodes from an existing topology diagram into ni_devices.

        Body: {'topology_id': required}
        """
        try:
            body = request.get_json(force=True) or {}
            src_id = body.get("topology_id", "")
            if not src_id:
                return jsonify({"error": "topology_id required"}), 400
            result = _nm.ingest_devices_topology(src_id)
            if "error" in result:
                return jsonify(result), 400
            _audit(None, "import_topology", f"ingested {result.get('total',0)} nodes from {src_id}")
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ══════════════════════════════════════════════════════════════════════
    # SERVER MIGRATION CANVAS
    # ══════════════════════════════════════════════════════════════════════

    try:
        from tools.migration_canvas import server_migration as _sm
    except Exception as _sm_exc:
        logger.warning("server_migration import failed: %s", _sm_exc)
        _sm = None  # type: ignore

    # ── Page Routes ───────────────────────────────────────────────────────

    @bp.route("/server-migration/")
    @mdc_login_required
    def mc_srv_index():
        try:
            conn = get_connection()
            rows = list(conn.execute(
                "SELECT id, src_hostname, src_ip, migration_type, tgt_platform, "
                "status, readiness_score, created_at FROM mc_srv_sessions ORDER BY created_at DESC LIMIT 100"
            ))
            conn.close()
            sessions = [dict(zip(
                ["id", "src_hostname", "src_ip", "migration_type", "tgt_platform",
                 "status", "readiness_score", "created_at"], r
            )) for r in rows]
        except Exception:
            sessions = []
        return render_template(
            "migration_canvas/server_wizard.html",
            page="index",
            sessions=sessions,
        )

    @bp.route("/server-migration/new")
    @mdc_login_required
    def mc_srv_wizard_new():
        from tools.migration_canvas.constants import (
            SERVER_MIGRATION_TYPES, SERVER_PLATFORMS,
            SERVER_COMPAT_CATEGORIES, CUTOVER_PHASES, MIGRATION_TOOLS,
        )
        return render_template(
            "migration_canvas/server_wizard.html",
            page="new",
            sid=None,
            srv_session=None,
            SERVER_MIGRATION_TYPES=SERVER_MIGRATION_TYPES,
            SERVER_PLATFORMS=SERVER_PLATFORMS,
            SERVER_COMPAT_CATEGORIES=SERVER_COMPAT_CATEGORIES,
            CUTOVER_PHASES=CUTOVER_PHASES,
            MIGRATION_TOOLS=MIGRATION_TOOLS,
        )

    @bp.route("/server-migration/<sid>")
    @mdc_login_required
    def mc_srv_wizard(sid):
        from tools.migration_canvas.constants import (
            SERVER_MIGRATION_TYPES, SERVER_PLATFORMS,
            SERVER_COMPAT_CATEGORIES, CUTOVER_PHASES, MIGRATION_TOOLS,
        )
        try:
            conn = get_connection()
            row = conn.execute("SELECT * FROM mc_srv_sessions WHERE id=%s", (sid,)).fetchone()
            conn.close()
            if not row:
                abort(404)
            cols = [d[0] for d in conn.execute("SELECT * FROM mc_srv_sessions WHERE 1=0").description] if False else [
                "id", "migration_type", "src_hostname", "src_ip", "src_os", "src_hypervisor",
                "tgt_platform", "tgt_region", "tgt_instance_id", "readiness_score",
                "status", "classification", "notes", "created_at", "updated_at",
            ]
            srv_session = dict(zip(cols, row))
        except Exception:
            abort(404)
        return render_template(
            "migration_canvas/server_wizard.html",
            page="wizard",
            sid=sid,
            srv_session=srv_session,
            SERVER_MIGRATION_TYPES=SERVER_MIGRATION_TYPES,
            SERVER_PLATFORMS=SERVER_PLATFORMS,
            SERVER_COMPAT_CATEGORIES=SERVER_COMPAT_CATEGORIES,
            CUTOVER_PHASES=CUTOVER_PHASES,
            MIGRATION_TOOLS=MIGRATION_TOOLS,
        )

    @bp.route("/server-migration/<sid>/inventory/import")
    @mdc_login_required
    def mc_srv_inventory_import(sid):
        """Dedicated inventory import page — upload zone + format tabs."""
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT id, src_hostname, migration_type, tgt_platform, status "
                "FROM mc_srv_sessions WHERE id=%s", (sid,)
            ).fetchone()
            conn.close()
            srv_session = dict(zip(
                ["id", "src_hostname", "migration_type", "tgt_platform", "status"], row
            )) if row else {"id": sid, "src_hostname": sid, "migration_type": "", "tgt_platform": "", "status": "draft"}
        except Exception:
            srv_session = {"id": sid, "src_hostname": sid, "migration_type": "", "tgt_platform": "", "status": "draft"}
        return render_template(
            "migration_canvas/server_inventory_import.html",
            sid=sid,
            srv_session=srv_session,
        )

    # ── Session CRUD ──────────────────────────────────────────────────────

    @bp.route("/api/server-migration", methods=["POST"])
    @mdc_login_required
    def mc_srv_api_create():
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            sid = _sm._session_id()
            now = _sm._now()
            conn = get_connection()
            conn.execute(
                """INSERT INTO mc_srv_sessions
                   (id, migration_type, src_hostname, src_ip, src_os, src_hypervisor,
                    tgt_platform, tgt_region, status, classification, notes, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    sid,
                    body.get("migration_type", ""),
                    body.get("src_hostname", ""),
                    body.get("src_ip", ""),
                    body.get("src_os", ""),
                    body.get("src_hypervisor", ""),
                    body.get("tgt_platform", ""),
                    body.get("tgt_region", ""),
                    "discovery",
                    body.get("classification", "CUI // SP-CTI"),
                    body.get("notes", ""),
                    now, now,
                ),
            )
            conn.commit()
            conn.close()
            _audit(None, "srv_create_session", sid)
            return jsonify({"id": sid, "status": "discovery", "created_at": now}), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>", methods=["GET"])
    @mdc_login_required
    def mc_srv_api_get(sid):
        try:
            conn = get_connection()
            row = conn.execute("SELECT * FROM mc_srv_sessions WHERE id=%s", (sid,)).fetchone()
            if not row:
                conn.close()
                return jsonify({"error": "not found"}), 404
            cols = [d[0] for d in conn.execute("SELECT * FROM mc_srv_sessions LIMIT 0").description]
            result = dict(zip(cols, row))
            conn.close()
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>", methods=["PATCH"])
    @mdc_login_required
    def mc_srv_api_update(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            allowed = {
                "migration_type", "src_hostname", "src_ip", "src_os", "src_hypervisor",
                "tgt_platform", "tgt_region", "tgt_instance_id", "status",
                "classification", "notes",
            }
            updates = {k: v for k, v in body.items() if k in allowed}
            if not updates:
                return jsonify({"error": "no valid fields"}), 400
            updates["updated_at"] = _sm._now()
            set_clause = ", ".join(f"{k}=%s" for k in updates)
            vals = list(updates.values()) + [sid]
            conn = get_connection()
            conn.execute(f"UPDATE mc_srv_sessions SET {set_clause} WHERE id=%s", vals)  # nosec B608 – cols from hardcoded allowlist; values parameterized
            conn.commit()
            conn.close()
            return jsonify({"ok": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>", methods=["DELETE"])
    @mdc_login_required
    def mc_srv_api_delete(sid):
        try:
            conn = get_connection()
            tables = [
                "mc_srv_erb_metadata", "mc_srv_test_cases", "mc_srv_cutover_steps",
                "mc_srv_storage_map", "mc_srv_nic_map", "mc_srv_dependencies",
                "mc_srv_rightsizing", "mc_srv_compat_checks", "mc_srv_performance",
                "mc_srv_services", "mc_srv_inventory", "mc_srv_sessions",
            ]
            for tbl in tables:
                if tbl == "mc_srv_sessions":
                    conn.execute(f"DELETE FROM {tbl} WHERE id=%s", (sid,))  # nosec B608 – tbl from hardcoded list above
                else:
                    conn.execute(f"DELETE FROM {tbl} WHERE session_id=%s", (sid,))  # nosec B608 – tbl from hardcoded list above
            conn.commit()
            conn.close()
            _audit(None, "srv_delete_session", sid)
            return jsonify({"ok": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Inventory ─────────────────────────────────────────────────────────

    @bp.route("/api/server-migration/<sid>/inventory", methods=["GET"])
    @mdc_login_required
    def mc_srv_api_inventory_get(sid):
        try:
            conn = get_connection()
            inv_row = conn.execute("SELECT * FROM mc_srv_inventory WHERE session_id=%s", (sid,)).fetchone()
            inv_cols = [d[0] for d in conn.execute("SELECT * FROM mc_srv_inventory LIMIT 0").description]
            inventory = dict(zip(inv_cols, inv_row)) if inv_row else {}
            nics = [
                dict(zip([d[0] for d in conn.execute("SELECT * FROM mc_srv_nic_map LIMIT 0").description], r))
                for r in conn.execute("SELECT * FROM mc_srv_nic_map WHERE session_id=%s", (sid,))
            ]
            storage = [
                dict(zip([d[0] for d in conn.execute("SELECT * FROM mc_srv_storage_map LIMIT 0").description], r))
                for r in conn.execute("SELECT * FROM mc_srv_storage_map WHERE session_id=%s", (sid,))
            ]
            services = [
                dict(zip([d[0] for d in conn.execute("SELECT * FROM mc_srv_services LIMIT 0").description], r))
                for r in conn.execute("SELECT * FROM mc_srv_services WHERE session_id=%s", (sid,))
            ]
            conn.close()
            return jsonify({"inventory": inventory, "nics": nics, "storage": storage, "services": services})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/inventory", methods=["POST"])
    @mdc_login_required
    def mc_srv_api_inventory_post(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            raw = body.get("raw", "")
            fmt = body.get("fmt", "manual")
            if raw:
                parsed = _sm.parse_server_inventory(raw, fmt)
                if not parsed.get("ok"):
                    return jsonify(parsed), 400
                inv = parsed["inventory"]
                nics = parsed.get("nics", [])
                disks = parsed.get("disks", [])
                services = parsed.get("services", [])
            else:
                inv = body.get("inventory", {})
                nics = body.get("nics", [])
                disks = body.get("disks", [])
                services = body.get("services", [])

            now = _sm._now()
            conn = get_connection()
            conn.execute("DELETE FROM mc_srv_inventory WHERE session_id=%s", (sid,))
            conn.execute(
                """INSERT INTO mc_srv_inventory
                   (session_id, vcpus, ram_gb, disk_count, total_disk_gb, disk_type,
                    nic_count, primary_nic_gbps, os_family, os_name, os_arch,
                    bios_type, virtualization_ext, raw_export_json, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    sid,
                    inv.get("vcpus", 0),
                    inv.get("ram_gb", 0),
                    inv.get("disk_count", len(disks)),
                    inv.get("total_disk_gb", sum(d.get("size_gb", 0) for d in disks)),
                    inv.get("disk_type", ""),
                    inv.get("nic_count", len(nics)),
                    inv.get("primary_nic_gbps", 1.0),
                    inv.get("os_family", ""),
                    inv.get("os_name", ""),
                    inv.get("os_arch", "x86_64"),
                    inv.get("bios_type", "UEFI"),
                    inv.get("virtualization_ext", ""),
                    json.dumps(inv),
                    now,
                ),
            )
            # Replace NIC map
            conn.execute("DELETE FROM mc_srv_nic_map WHERE session_id=%s", (sid,))
            for i, nic in enumerate(nics):
                conn.execute(
                    "INSERT INTO mc_srv_nic_map (session_id,src_nic,src_speed_gbps,src_mac,src_vlan,src_ip,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (sid, nic.get("name", f"eth{i}"), nic.get("speed_gbps", 1.0),
                     nic.get("mac", ""), nic.get("vlan", ""), nic.get("ip", ""), now),
                )
            # Replace storage map
            conn.execute("DELETE FROM mc_srv_storage_map WHERE session_id=%s", (sid,))
            for i, disk in enumerate(disks):
                conn.execute(
                    "INSERT INTO mc_srv_storage_map (session_id,src_disk,src_size_gb,src_type,src_mount,src_filesystem,src_used_gb,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (sid, disk.get("name", f"disk{i}"), disk.get("size_gb", 0),
                     disk.get("type", "SSD"), disk.get("mount", ""), disk.get("filesystem", ""),
                     disk.get("used_gb", 0), now),
                )
            # Save services from parsed input
            if services:
                conn.execute("DELETE FROM mc_srv_services WHERE session_id=%s", (sid,))
                for svc in services:
                    conn.execute(
                        "INSERT INTO mc_srv_services (session_id,service_name,service_role,port,protocol,status,auto_detected,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (sid, svc.get("service_name", ""), svc.get("service_role", ""),
                         svc.get("port", 0), svc.get("protocol", "tcp"),
                         svc.get("status", "running"), int(svc.get("auto_detected", 0)), now),
                    )
            conn.execute("UPDATE mc_srv_sessions SET updated_at=%s WHERE id=%s", (now, sid))
            conn.commit()
            conn.close()
            return jsonify({"ok": True, "nics": len(nics), "disks": len(disks), "services": len(services)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/services", methods=["POST"])
    @mdc_login_required
    def mc_srv_api_services_post(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            services = body.get("services", [])
            now = _sm._now()
            conn = get_connection()
            conn.execute("DELETE FROM mc_srv_services WHERE session_id=%s", (sid,))
            for svc in services:
                conn.execute(
                    """INSERT INTO mc_srv_services
                       (session_id, service_name, service_role, port, protocol, status, auto_detected, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        sid, svc.get("service_name", ""), svc.get("service_role", ""),
                        svc.get("port", 0), svc.get("protocol", "tcp"),
                        svc.get("status", "running"), int(svc.get("auto_detected", 0)), now,
                    ),
                )
            conn.commit()
            conn.close()
            return jsonify({"ok": True, "services": len(services)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Performance ───────────────────────────────────────────────────────

    @bp.route("/api/server-migration/<sid>/performance", methods=["GET"])
    @mdc_login_required
    def mc_srv_api_perf_get(sid):
        try:
            conn = get_connection()
            row = conn.execute("SELECT * FROM mc_srv_performance WHERE session_id=%s", (sid,)).fetchone()
            cols = [d[0] for d in conn.execute("SELECT * FROM mc_srv_performance LIMIT 0").description]
            result = dict(zip(cols, row)) if row else {}
            conn.close()
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/performance", methods=["POST"])
    @mdc_login_required
    def mc_srv_api_perf_post(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            now = _sm._now()
            conn = get_connection()
            conn.execute("DELETE FROM mc_srv_performance WHERE session_id=%s", (sid,))
            conn.execute(
                """INSERT INTO mc_srv_performance
                   (session_id, cpu_avg_pct, cpu_peak_pct, ram_avg_pct, ram_peak_pct,
                    disk_iops_avg, disk_iops_peak, net_mbps_avg, net_mbps_peak,
                    sample_period_days, source, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    sid,
                    body.get("cpu_avg_pct", 0), body.get("cpu_peak_pct", 0),
                    body.get("ram_avg_pct", 0), body.get("ram_peak_pct", 0),
                    body.get("disk_iops_avg", 0), body.get("disk_iops_peak", 0),
                    body.get("net_mbps_avg", 0), body.get("net_mbps_peak", 0),
                    body.get("sample_period_days", 30), body.get("source", "manual"), now,
                ),
            )
            conn.execute("UPDATE mc_srv_sessions SET updated_at=%s WHERE id=%s", (now, sid))
            conn.commit()
            conn.close()
            return jsonify({"ok": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Instance Catalog + Recommendations ───────────────────────────────

    @bp.route("/api/server-migration/instance-catalog", methods=["GET"])
    @mdc_login_required
    def mc_srv_api_catalog():
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            filters = {
                "min_vcpus": request.args.get("min_vcpus", type=int),
                "max_vcpus": request.args.get("max_vcpus", type=int),
                "min_ram_gb": request.args.get("min_ram_gb", type=float),
                "max_ram_gb": request.args.get("max_ram_gb", type=float),
                "govcloud_only": request.args.get("govcloud_only", "").lower() in ("1", "true"),
                "family": request.args.get("family"),
                "cost_tier": request.args.get("cost_tier"),
                "use_case": request.args.get("use_case"),
                "eol_status": request.args.get("eol_status"),
            }
            provider = request.args.get("provider")
            instances = _sm.get_cloud_instances(provider, {k: v for k, v in filters.items() if v is not None})
            return jsonify({"instances": instances, "count": len(instances)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/catalog-sync", methods=["POST"])
    @mdc_login_required
    def mc_srv_api_catalog_sync():
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            providers = body.get("providers")
            force = bool(body.get("force", False))
            result = _sm.sync_cloud_catalog(providers=providers, force=force)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/recommend", methods=["POST"])
    @mdc_login_required
    def mc_srv_api_recommend(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            result = _sm.compute_rightsizing(sid)
            if "error" in result:
                return jsonify(result), 400
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Compatibility Checks ──────────────────────────────────────────────

    @bp.route("/api/server-migration/<sid>/compat-check", methods=["POST"])
    @mdc_login_required
    def mc_srv_api_compat_run(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            checks = _sm.run_compatibility_checks(sid)
            cat1 = [c for c in checks if c.get("severity") == "cat1" and c.get("status") == "fail"]
            return jsonify({"checks": checks, "cat1_count": len(cat1)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/compat-check", methods=["GET"])
    @mdc_login_required
    def mc_srv_api_compat_list(sid):
        try:
            conn = get_connection()
            rows = list(conn.execute(
                "SELECT * FROM mc_srv_compat_checks WHERE session_id=%s ORDER BY severity, category",
                (sid,),
            ))
            cols = [d[0] for d in conn.execute("SELECT * FROM mc_srv_compat_checks LIMIT 0").description]
            conn.close()
            checks = [dict(zip(cols, r)) for r in rows]
            return jsonify({"checks": checks})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/compat-check/<int:cid>/override", methods=["POST"])
    @mdc_login_required
    def mc_srv_api_compat_override(sid, cid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            reason = body.get("reason", "").strip()
            if not reason:
                return jsonify({"error": "override reason required"}), 400
            conn = get_connection()
            conn.execute(
                "UPDATE mc_srv_compat_checks SET status='override', override_reason=%s WHERE id=%s AND session_id=%s",
                (reason, cid, sid),
            )
            conn.commit()
            conn.close()
            return jsonify({"ok": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── NIC & Storage Maps ────────────────────────────────────────────────

    @bp.route("/api/server-migration/<sid>/nic-map", methods=["POST"])
    @mdc_login_required
    def mc_srv_api_nic_map(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            nics = body.get("nics", [])
            now = _sm._now()
            conn = get_connection()
            conn.execute("DELETE FROM mc_srv_nic_map WHERE session_id=%s", (sid,))
            for nic in nics:
                conn.execute(
                    """INSERT INTO mc_srv_nic_map
                       (session_id, src_nic, src_speed_gbps, src_mac, src_vlan, src_ip,
                        tgt_nic, tgt_vlan, tgt_ip, ip_change, requires_dhcp, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        sid, nic.get("src_nic", ""), nic.get("src_speed_gbps", 1.0),
                        nic.get("src_mac", ""), nic.get("src_vlan", ""), nic.get("src_ip", ""),
                        nic.get("tgt_nic", ""), nic.get("tgt_vlan", ""), nic.get("tgt_ip", ""),
                        int(nic.get("ip_change", 0)), int(nic.get("requires_dhcp", 0)), now,
                    ),
                )
            conn.execute("UPDATE mc_srv_sessions SET updated_at=%s WHERE id=%s", (now, sid))
            conn.commit()
            conn.close()
            return jsonify({"ok": True, "nics": len(nics)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/storage-map", methods=["POST"])
    @mdc_login_required
    def mc_srv_api_storage_map(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            disks = body.get("disks", [])
            now = _sm._now()
            conn = get_connection()
            conn.execute("DELETE FROM mc_srv_storage_map WHERE session_id=%s", (sid,))
            for disk in disks:
                src_gb = disk.get("src_size_gb", 0) or 0
                tgt_gb = disk.get("tgt_size_gb", 0) or src_gb
                pct = round((tgt_gb - src_gb) / src_gb * 100, 1) if src_gb else 0
                conn.execute(
                    """INSERT INTO mc_srv_storage_map
                       (session_id, src_disk, src_size_gb, src_type, src_mount, src_filesystem,
                        src_used_gb, tgt_volume, tgt_size_gb, tgt_type, tgt_iops_provisioned,
                        size_increase_pct, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        sid, disk.get("src_disk", ""), src_gb, disk.get("src_type", "SSD"),
                        disk.get("src_mount", ""), disk.get("src_filesystem", ""),
                        disk.get("src_used_gb", 0), disk.get("tgt_volume", ""),
                        tgt_gb, disk.get("tgt_type", ""), disk.get("tgt_iops_provisioned", 0),
                        pct, now,
                    ),
                )
            conn.execute("UPDATE mc_srv_sessions SET updated_at=%s WHERE id=%s", (now, sid))
            conn.commit()
            conn.close()
            return jsonify({"ok": True, "disks": len(disks)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Dependencies ──────────────────────────────────────────────────────

    @bp.route("/api/server-migration/<sid>/dependencies", methods=["GET"])
    @mdc_login_required
    def mc_srv_api_deps_get(sid):
        try:
            conn = get_connection()
            rows = list(conn.execute(
                "SELECT * FROM mc_srv_dependencies WHERE session_id=%s ORDER BY criticality DESC",
                (sid,),
            ))
            cols = [d[0] for d in conn.execute("SELECT * FROM mc_srv_dependencies LIMIT 0").description]
            conn.close()
            return jsonify({"dependencies": [dict(zip(cols, r)) for r in rows]})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/dependencies", methods=["POST"])
    @mdc_login_required
    def mc_srv_api_deps_post(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            deps = body.get("dependencies", [])
            now = _sm._now()
            conn = get_connection()
            conn.execute("DELETE FROM mc_srv_dependencies WHERE session_id=%s", (sid,))
            for dep in deps:
                conn.execute(
                    """INSERT INTO mc_srv_dependencies
                       (session_id, dep_hostname, dep_ip, dep_role, dep_type,
                        dep_port, criticality, migration_order, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        sid, dep.get("dep_hostname", ""), dep.get("dep_ip", ""),
                        dep.get("dep_role", ""), dep.get("dep_type", "outbound"),
                        dep.get("dep_port", 0), dep.get("criticality", "medium"),
                        dep.get("migration_order", 0), now,
                    ),
                )
            conn.commit()
            conn.close()
            return jsonify({"ok": True, "dependencies": len(deps)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Cutover Steps ─────────────────────────────────────────────────────

    @bp.route("/api/server-migration/<sid>/cutover-steps", methods=["GET"])
    @mdc_login_required
    def mc_srv_api_cutover_get(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            seed = request.args.get("seed", "").lower() in ("1", "true")
            if seed:
                conn = get_connection()
                existing = conn.execute(
                    "SELECT COUNT(*) FROM mc_srv_cutover_steps WHERE session_id=%s", (sid,)
                ).fetchone()[0]
                conn.close()
                if not existing:
                    conn2 = get_connection()
                    row = conn2.execute(
                        "SELECT migration_type FROM mc_srv_sessions WHERE id=%s", (sid,)
                    ).fetchone()
                    conn2.close()
                    mtype = row[0] if row else "p2v_cloud"
                    _sm.generate_default_cutover_steps(sid, mtype)
            conn = get_connection()
            rows = list(conn.execute(
                "SELECT * FROM mc_srv_cutover_steps WHERE session_id=%s ORDER BY phase, seq_no",
                (sid,),
            ))
            cols = [d[0] for d in conn.execute("SELECT * FROM mc_srv_cutover_steps LIMIT 0").description]
            conn.close()
            return jsonify({"steps": [dict(zip(cols, r)) for r in rows]})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/cutover-steps", methods=["POST"])
    @mdc_login_required
    def mc_srv_api_cutover_post(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            action = body.get("action", "replace")
            if action == "seed":
                mtype = body.get("migration_type", "p2v_cloud")
                steps = _sm.generate_default_cutover_steps(sid, mtype)
                return jsonify({"ok": True, "steps": len(steps)})
            steps = body.get("steps", [])
            now = _sm._now()
            conn = get_connection()
            conn.execute("DELETE FROM mc_srv_cutover_steps WHERE session_id=%s", (sid,))
            for step in steps:
                conn.execute(
                    """INSERT INTO mc_srv_cutover_steps
                       (session_id, phase, seq_no, description, action, verify_action,
                        rollback_action, owner, duration_min, status, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        sid, step.get("phase", "cutover"), step.get("seq_no", 0),
                        step.get("description", ""), step.get("action", ""),
                        step.get("verify_action", ""), step.get("rollback_action", ""),
                        step.get("owner", ""), step.get("duration_min", 15),
                        step.get("status", "pending"), now,
                    ),
                )
            conn.execute("UPDATE mc_srv_sessions SET updated_at=%s WHERE id=%s", (now, sid))
            conn.commit()
            conn.close()
            return jsonify({"ok": True, "steps": len(steps)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Test Cases ────────────────────────────────────────────────────────

    @bp.route("/api/server-migration/<sid>/test-cases", methods=["GET"])
    @mdc_login_required
    def mc_srv_api_tests_get(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            seed = request.args.get("seed", "").lower() in ("1", "true")
            if seed:
                conn = get_connection()
                existing = conn.execute(
                    "SELECT COUNT(*) FROM mc_srv_test_cases WHERE session_id=%s", (sid,)
                ).fetchone()[0]
                conn.close()
                if not existing:
                    _sm.generate_default_test_cases(sid)
            conn = get_connection()
            rows = list(conn.execute(
                "SELECT * FROM mc_srv_test_cases WHERE session_id=%s ORDER BY phase, seq_no",
                (sid,),
            ))
            cols = [d[0] for d in conn.execute("SELECT * FROM mc_srv_test_cases LIMIT 0").description]
            conn.close()
            return jsonify({"test_cases": [dict(zip(cols, r)) for r in rows]})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/test-cases", methods=["POST"])
    @mdc_login_required
    def mc_srv_api_tests_post(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            action = body.get("action", "update")
            if action == "seed":
                cases = _sm.generate_default_test_cases(sid)
                return jsonify({"ok": True, "test_cases": len(cases)})
            # Update a single test result
            tc_id = body.get("id")
            if not tc_id:
                return jsonify({"error": "id required for update"}), 400
            passed = body.get("passed")
            actual = body.get("actual_result", "")
            conn = get_connection()
            conn.execute(
                "UPDATE mc_srv_test_cases SET passed=%s, actual_result=%s, executed_at=%s WHERE id=%s AND session_id=%s",
                (passed, actual, _sm._now(), tc_id, sid),
            )
            conn.commit()
            conn.close()
            return jsonify({"ok": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── ERB & Readiness ───────────────────────────────────────────────────

    @bp.route("/api/server-migration/<sid>/erb", methods=["GET"])
    @mdc_login_required
    def mc_srv_api_erb_get(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            package = _sm.build_erb_package(sid)
            return jsonify(package)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/erb", methods=["POST"])
    @mdc_login_required
    def mc_srv_api_erb_post(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            now = _sm._now()
            erb_id = body.get("id") or f"erb-{_uuid.uuid4().hex[:10]}"
            conn = get_connection()
            conn.execute("DELETE FROM mc_srv_erb_metadata WHERE session_id=%s", (sid,))
            conn.execute(
                """INSERT INTO mc_srv_erb_metadata
                   (id, session_id, change_type, risk_tier, business_justification,
                    technical_summary, impact_summary, rollback_plan, mw_start, mw_end,
                    go_nogo_criteria, requestor, approver, approval_status, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    erb_id, sid,
                    body.get("change_type", ""), body.get("risk_tier", "medium"),
                    body.get("business_justification", ""), body.get("technical_summary", ""),
                    body.get("impact_summary", ""), body.get("rollback_plan", ""),
                    body.get("mw_start", ""), body.get("mw_end", ""),
                    json.dumps(body.get("go_nogo_criteria", [])),
                    body.get("requestor", ""), body.get("approver", ""),
                    body.get("approval_status", "pending"), now,
                ),
            )
            conn.execute("UPDATE mc_srv_sessions SET updated_at=%s WHERE id=%s", (now, sid))
            conn.commit()
            conn.close()
            return jsonify({"ok": True, "id": erb_id})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/readiness", methods=["GET"])
    @mdc_login_required
    def mc_srv_api_readiness(sid):
        if _sm is None:
            return jsonify({"error": "server_migration module unavailable"}), 503
        try:
            result = _sm.compute_readiness_score(sid)
            result["score"] = result.get("overall", 0)
            result["cat1_blockers"] = result.get("blockers", [])
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/guidance/<int:step>", methods=["GET"])
    @mdc_login_required
    def mc_srv_api_guidance(step):
        from tools.migration_canvas.dossier_advisor import get_guidance_for_step
        migration_type = request.args.get("type")
        items = get_guidance_for_step(step, migration_type=migration_type)
        return jsonify({"ok": True, "items": items})

    # ══════════════════════════════════════════════════════════════════════
    # WAVE PLANNER — Phase 4
    # ══════════════════════════════════════════════════════════════════════

    try:
        from tools.migration_canvas import wave_planner as _wp
    except Exception as _wp_exc:
        logger.warning("wave_planner import failed: %s", _wp_exc)
        _wp = None  # type: ignore

    @bp.route("/server-migration/<sid>/waves")
    @mdc_login_required
    def mc_srv_wave_planner(sid):
        srv_session = None
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT id, src_hostname, migration_type, tgt_platform, status, readiness_score "
                "FROM mc_srv_sessions WHERE id=%s", (sid,)
            ).fetchone()
            conn.close()
            if row:
                srv_session = dict(zip(
                    ["id", "src_hostname", "migration_type", "tgt_platform", "status", "readiness_score"],
                    row,
                ))
        except Exception:
            pass
        srv_session = srv_session or {"id": sid, "src_hostname": sid, "readiness_score": 0}
        return render_template(
            "migration_canvas/wave_planner.html",
            sid=sid,
            srv_session=srv_session,
        )

    @bp.route("/api/server-migration/<sid>/waves", methods=["GET"])
    @mdc_login_required
    def mc_srv_waves_get(sid):
        if _wp is None:
            return jsonify({"error": "wave_planner module unavailable"}), 503
        try:
            waves = _wp.get_waves(sid)
            graph = _wp.build_graph(sid)
            return jsonify({"waves": waves, "graph": graph})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/waves", methods=["POST"])
    @mdc_login_required
    def mc_srv_waves_post(sid):
        if _wp is None:
            return jsonify({"error": "wave_planner module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            wave = _wp.upsert_wave(sid, body)
            return jsonify({"ok": True, "wave": wave})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/waves/<wid>", methods=["DELETE"])
    @mdc_login_required
    def mc_srv_waves_delete(sid, wid):
        if _wp is None:
            return jsonify({"error": "wave_planner module unavailable"}), 503
        try:
            removed = _wp.delete_wave(wid, sid)
            return jsonify({"ok": removed})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/waves/auto-assign", methods=["POST"])
    @mdc_login_required
    def mc_srv_waves_auto_assign(sid):
        if _wp is None:
            return jsonify({"error": "wave_planner module unavailable"}), 503
        try:
            result = _wp.auto_assign_waves(sid)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/waves/graph", methods=["GET"])
    @mdc_login_required
    def mc_srv_waves_graph(sid):
        if _wp is None:
            return jsonify({"error": "wave_planner module unavailable"}), 503
        try:
            graph = _wp.build_graph(sid)
            return jsonify(graph)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Wave Backout / Recovery Section (crx-mig-01 gap #2) ───────────────────

    @bp.route("/api/server-migration/<sid>/waves/<wid>/backout", methods=["GET"])
    @mdc_login_required
    def mc_srv_wave_backout_get(sid, wid):
        if _wp is None:
            return jsonify({"error": "wave_planner module unavailable"}), 503
        try:
            section = _wp.get_backout_section(sid, wid)
            if section is None:
                # Return the template default (not yet persisted) for editing.
                waves = {w["id"]: w for w in _wp.get_waves(sid)}
                section = _wp.generate_backout_section(waves.get(wid, {"id": wid}))
                section["approved"] = False
                section["persisted"] = False
            else:
                section["persisted"] = True
            return jsonify(section)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/waves/<wid>/backout", methods=["POST"])
    @mdc_login_required
    def mc_srv_wave_backout_post(sid, wid):
        if _wp is None:
            return jsonify({"error": "wave_planner module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            section = _wp.upsert_backout_section(sid, wid, body or None)
            return jsonify({"ok": True, "backout": section})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/waves/<wid>/backout/approve", methods=["POST"])
    @mdc_login_required
    def mc_srv_wave_backout_approve(sid, wid):
        if _wp is None:
            return jsonify({"error": "wave_planner module unavailable"}), 503
        try:
            body = request.get_json(silent=True) or {}
            section = _wp.approve_backout_section(sid, wid, body.get("user", ""))
            if section is None:
                return jsonify({"error": "No backout section to approve"}), 404
            return jsonify({"ok": True, "backout": section})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Post-Migration Workload Validation + Wave Close Gate (crx-mig-01 gap #3) ─

    try:
        from tools.migration_canvas import workload_validator as _wv
    except Exception as _wv_exc:  # noqa: BLE001
        logger.warning("workload_validator import failed: %s", _wv_exc)
        _wv = None  # type: ignore

    @bp.route("/api/server-migration/<sid>/waves/<wid>/validate", methods=["POST"])
    @mdc_login_required
    def mc_srv_wave_validate(sid, wid):
        if _wv is None:
            return jsonify({"error": "workload_validator module unavailable"}), 503
        try:
            body = request.get_json(force=True) or {}
            workload = body.get("workload", body)
            result = _wv.run_workload_validation(sid, wid, workload)
            return jsonify({"ok": True, "validation": result})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/waves/<wid>/validation-status", methods=["GET"])
    @mdc_login_required
    def mc_srv_wave_validation_status(sid, wid):
        if _wv is None:
            return jsonify({"error": "workload_validator module unavailable"}), 503
        try:
            return jsonify(_wv.wave_validation_status(sid, wid))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/server-migration/<sid>/waves/<wid>/close", methods=["POST"])
    @mdc_login_required
    def mc_srv_wave_close(sid, wid):
        if _wv is None:
            return jsonify({"error": "workload_validator module unavailable"}), 503
        try:
            body = request.get_json(silent=True) or {}
            result = _wv.close_wave(
                sid, wid,
                user=body.get("user", ""),
                force=bool(body.get("force", False)),
                override_reason=body.get("override_reason", ""),
            )
            code = 200 if result.get("ok") else 409
            return jsonify(result), code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Hypervisor Live Import ────────────────────────────────────────────────

    @bp.route("/api/srv/<session_id>/hypervisor-pull", methods=["POST"])
    @mdc_login_required
    def srv_hypervisor_pull(session_id):
        """Pull live VM inventory from a hypervisor and import into the session."""
        from tools.migration_canvas.server_migration import import_from_hypervisor
        body = request.json or {}
        adapter = body.get("adapter_type", "")
        host = body.get("host", "")
        user = body.get("user", "")
        password = body.get("password", "")
        if not all([adapter, host, user, password]):
            return jsonify({"error": "adapter_type, host, user, password required"}), 400
        try:
            result = import_from_hypervisor(
                session_id, adapter, host, user, password,
                datacenter=body.get("datacenter"),
                cluster=body.get("cluster"),
            )
            return jsonify(result)
        except Exception as exc:
            logger.warning("Hypervisor pull error: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/srv/<session_id>/hypervisor-sessions", methods=["GET"])
    @mdc_login_required
    def srv_hypervisor_sessions(session_id):
        """List prior hypervisor pull sessions for this migration session."""
        from tools.migration_canvas.db.init_db import get_connection
        with get_connection() as db:
            rows = db.execute(
                "SELECT id, adapter_type, host, pulled_at, vm_count, status, error_msg "
                "FROM mc_srv_hypervisor_sessions WHERE session_id=%s ORDER BY pulled_at DESC",
                (session_id,),
            ).fetchall()
        return jsonify({"sessions": [dict(r) for r in rows]})

    # ── Advanced Cloud Catalog ────────────────────────────────────────────────

    @bp.route("/api/srv/cloud-catalog/advanced", methods=["GET"])
    @mdc_login_required
    def srv_cloud_catalog_advanced():
        """Return cloud instances with spot/reserved/savings-plan pricing."""
        from tools.migration_canvas.server_migration import get_cloud_instances_advanced
        provider = request.args.get("provider", "all")
        pricing = request.args.get("pricing", "on_demand")
        vcpu_min = int(request.args.get("vcpu_min", 0))
        ram_min = float(request.args.get("ram_min", 0))
        instances = get_cloud_instances_advanced(
            provider, pricing_model=pricing, vcpu_min=vcpu_min, ram_min=ram_min,
        )
        return jsonify({"instances": instances, "pricing_model": pricing, "total": len(instances)})

    # ── Post-Migration Validation (server) ───────────────────────────────────

    @bp.route("/api/srv/<session_id>/validate", methods=["POST"])
    @mdc_login_required
    def srv_validate(session_id):
        """Run post-migration validation checks against target servers."""
        from tools.migration_canvas.post_migration_validator import run_validation_suite
        body = request.json or {}
        targets = body.get("targets", [])
        if not targets:
            return jsonify({"error": "targets list required"}), 400
        try:
            result = run_validation_suite(session_id, targets)
            return jsonify(result)
        except Exception as exc:
            logger.warning("Post-migration validation error: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/srv/<session_id>/validation-runs", methods=["GET"])
    @mdc_login_required
    def srv_validation_runs(session_id):
        """List prior post-migration validation runs for this session."""
        from tools.migration_canvas.db.init_db import get_connection
        with get_connection() as db:
            rows = db.execute(
                "SELECT id, run_at, check_type, target, status, detail, elapsed_ms "
                "FROM mc_srv_post_migration_tests WHERE session_id=%s ORDER BY run_at DESC LIMIT 200",
                (session_id,),
            ).fetchall()
        return jsonify({"results": [dict(r) for r in rows], "total": len(rows)})

    # ── Vendor EOL Sync (network) ────────────────────────────────────────────

    @bp.route("/api/net/eol-sync", methods=["POST"])
    @mdc_login_required
    def net_eol_sync():
        """Trigger a vendor EOL database sync."""
        from tools.migration_canvas.eol_sync import sync_eol_database
        body = request.json or {}
        vendors = body.get("vendors")
        force = body.get("force", False)
        try:
            result = sync_eol_database(vendors=vendors, force=force)
            return jsonify(result)
        except Exception as exc:
            logger.warning("EOL sync error: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/net/eol/<session_id>/flags", methods=["GET"])
    @mdc_login_required
    def net_eol_flags(session_id):
        """Return EOL flags for all devices in a network migration session."""
        from tools.migration_canvas.eol_sync import flag_eol_devices
        from tools.migration_canvas.db.init_db import get_connection
        with get_connection() as db:
            rows = db.execute(
                "SELECT id, src_model AS model, src_device_name AS hostname "
                "FROM mc_net_sessions WHERE id=%s",
                (session_id,),
            ).fetchall()
            port_devices = db.execute(
                "SELECT DISTINCT far_end_device AS model FROM mc_net_port_map "
                "WHERE session_id=%s AND far_end_device != ''",
                (session_id,),
            ).fetchall()
        devices = [dict(r) for r in rows] + [{"model": r[0]} for r in port_devices]
        flagged = flag_eol_devices(devices)
        return jsonify({"devices": flagged, "session_id": session_id})

    # ── Vendor Migration Paths ────────────────────────────────────────────────

    @bp.route("/api/net/migration-paths", methods=["GET"])
    @mdc_login_required
    def net_migration_paths():
        """List all vendor migration paths."""
        from tools.migration_canvas.vendor_migration_paths import list_all_paths
        paths = list_all_paths()
        # Strip sensitive detail — return summary
        summary = [
            {
                "id": p.get("id"),
                "source_vendor": p.get("source_vendor"),
                "source_family": p.get("source_family"),
                "target_vendor": p.get("target_vendor"),
                "target_family": p.get("target_family"),
                "migration_type": p.get("migration_type"),
                "complexity": p.get("complexity"),
                "estimated_hours": p.get("estimated_hours"),
            }
            for p in paths
        ]
        return jsonify({"paths": summary, "total": len(summary)})

    @bp.route("/api/net/migration-paths/<source_vendor>/<source_family>", methods=["GET"])
    @mdc_login_required
    def net_migration_path_targets(source_vendor, source_family):
        """List compatible migration targets for a source device."""
        from tools.migration_canvas.vendor_migration_paths import (
            list_compatible_targets, get_migration_path,
        )
        target_vendor = request.args.get("target_vendor", "")
        if target_vendor:
            path = get_migration_path(source_vendor, source_family, target_vendor)
            if not path:
                return jsonify({"error": "No migration path found"}), 404
            return jsonify(path)
        targets = list_compatible_targets(source_vendor, source_family)
        return jsonify({"targets": targets, "total": len(targets)})

    # ── Post-Migration Config Validation (network) ───────────────────────────

    @bp.route("/api/net/<session_id>/validate-config", methods=["POST"])
    @mdc_login_required
    def net_validate_config(session_id):
        """Run post-migration config diff for a network migration session."""
        from tools.migration_canvas.network_config_validator import validate_migration_completeness
        try:
            result = validate_migration_completeness(session_id)
            return jsonify(result)
        except Exception as exc:
            logger.warning("Network config validation error: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/net/<session_id>/validation", methods=["GET"])
    @mdc_login_required
    def net_validation_result(session_id):
        """Get the most recent config validation result for a session."""
        from tools.migration_canvas.db.init_db import get_connection
        with get_connection() as db:
            row = db.execute(
                "SELECT * FROM mc_net_config_validation WHERE session_id=%s ORDER BY run_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        if not row:
            return jsonify({"message": "No validation runs yet"}), 404
        return jsonify(dict(row))

    @bp.route("/api/iqe-query", methods=["POST"])
    @mdc_login_required
    def mc_api_iqe_query():
        """IQE structured query — translate NL to IQE and execute against MC migration data."""
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import IQESyntaxError, parse
        from tools.iqe.executor import execute_query
        import tools.iqe.adapters.mc  # noqa: F401 — registers mc.* collections

        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400

        collections = ["mc.designs", "mc.waves", "mc.assessments"]
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
            logger.warning("MC IQE query error: %s", exc)
            return jsonify({"error": str(exc), "iqe": iqe_str}), 500

    # ====================================================================
    # APPLICATION MIGRATION MODULE API
    # ====================================================================

    @bp.route("/api/app-inventory", methods=["GET"])
    @mdc_login_required
    def app_inventory_list():
        """List app inventory records with optional filters."""
        session_id = request.args.get("session_id")
        criticality = request.args.get("criticality")
        environment = request.args.get("environment")
        with get_connection() as db:
            sql = "SELECT * FROM mc_app_inventory WHERE 1=1"
            params: list = []
            if session_id:
                sql += " AND session_id=%s"
                params.append(session_id)
            if criticality:
                sql += " AND criticality=%s"
                params.append(criticality)
            if environment:
                sql += " AND environment=%s"
                params.append(environment)
            sql += " ORDER BY created_at DESC"
            rows = [dict(r) for r in db.execute(sql, params).fetchall()]
        return jsonify({"apps": rows, "count": len(rows)})

    @bp.route("/api/app-inventory", methods=["POST"])
    @mdc_login_required
    def app_inventory_create():
        """Create a new app inventory record."""
        data = request.get_json(silent=True) or {}
        if not data.get("name"):
            return jsonify({"error": "name is required"}), 400
        app_id = str(_uuid.uuid4())
        now = now_isoformat()
        with get_connection() as db:
            db.execute(
                """INSERT INTO mc_app_inventory
                   (id, session_id, name, version, language, framework, app_type,
                    owner, team, criticality, environment, stig_category,
                    license_type, license_expiry, source_repo, artifact_url,
                    dependencies_json, migration_strategy, migration_status,
                    notes, classification, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    app_id,
                    data.get("session_id"),
                    data["name"],
                    data.get("version"),
                    data.get("language"),
                    data.get("framework"),
                    data.get("app_type"),
                    data.get("owner"),
                    data.get("team"),
                    data.get("criticality"),
                    data.get("environment"),
                    data.get("stig_category"),
                    data.get("license_type"),
                    data.get("license_expiry"),
                    data.get("source_repo"),
                    data.get("artifact_url"),
                    data.get("dependencies_json", "[]"),
                    data.get("migration_strategy"),
                    data.get("migration_status", "pending"),
                    data.get("notes"),
                    data.get("classification", "CUI"),
                    now, now,
                ),
            )
        _audit(app_id, "APP_CREATED", data.get("name", ""))
        return jsonify({"ok": True, "id": app_id}), 201

    @bp.route("/api/app-inventory/<app_id>", methods=["GET"])
    @mdc_login_required
    def app_inventory_get(app_id):
        """Get a single app inventory record."""
        with get_connection() as db:
            row = db.execute("SELECT * FROM mc_app_inventory WHERE id=%s", (app_id,)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify(dict(row))

    @bp.route("/api/app-inventory/<app_id>", methods=["PUT"])
    @mdc_login_required
    def app_inventory_update(app_id):
        """Update an app inventory record."""
        data = request.get_json(silent=True) or {}
        allowed = {
            "name", "version", "language", "framework", "app_type", "owner", "team",
            "criticality", "environment", "stig_category", "license_type", "license_expiry",
            "source_repo", "artifact_url", "dependencies_json", "migration_strategy",
            "migration_status", "notes", "classification",
        }
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return jsonify({"error": "No valid fields to update"}), 400
        updates["updated_at"] = now_isoformat()
        set_clause = ", ".join(f"{k}=%s" for k in updates)
        vals = list(updates.values()) + [app_id]
        with get_connection() as db:
            db.execute(f"UPDATE mc_app_inventory SET {set_clause} WHERE id=%s", vals)  # nosec B608 – cols from hardcoded allowlist; values parameterized
        _audit(app_id, "APP_UPDATED", str(list(updates.keys())))
        return jsonify({"ok": True})

    @bp.route("/api/app-inventory/<app_id>", methods=["DELETE"])
    @mdc_login_required
    def app_inventory_delete(app_id):
        """Delete an app inventory record."""
        with get_connection() as db:
            db.execute("DELETE FROM mc_app_inventory WHERE id=%s", (app_id,))
        _audit(app_id, "APP_DELETED", "")
        return jsonify({"ok": True})

    @bp.route("/api/app-inventory/import", methods=["POST"])
    @mdc_login_required
    def app_inventory_import_csv():
        """Bulk import apps from CSV body or JSON list."""
        import csv
        import io
        data = request.get_json(silent=True)
        if data and isinstance(data, list):
            rows_data = data
        else:
            raw = request.get_data(as_text=True)
            reader = csv.DictReader(io.StringIO(raw))
            rows_data = list(reader)
        if not rows_data:
            return jsonify({"error": "No rows provided"}), 400
        inserted = 0
        now = now_isoformat()
        with get_connection() as db:
            for row in rows_data:
                app_id = str(_uuid.uuid4())
                name = row.get("name", "").strip()
                if not name:
                    continue
                db.execute(
                    """INSERT INTO mc_app_inventory
                       (id, name, version, language, framework, app_type,
                        owner, criticality, environment, stig_category,
                        license_type, classification, created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        app_id, name,
                        row.get("version", ""),
                        row.get("language", ""),
                        row.get("framework", ""),
                        row.get("app_type", ""),
                        row.get("owner", ""),
                        row.get("criticality", "medium"),
                        row.get("environment", "production"),
                        row.get("stig_category", "na"),
                        row.get("license_type", ""),
                        row.get("classification", "CUI"),
                        now, now,
                    ),
                )
                inserted += 1
        return jsonify({"ok": True, "inserted": inserted})

    # ── App-to-Server Bindings ───────────────────────────────────────────────

    @bp.route("/api/app-inventory/<app_id>/servers", methods=["POST"])
    @mdc_login_required
    def app_bind_server(app_id):
        """Bind an app to a server."""
        data = request.get_json(silent=True) or {}
        server_id = data.get("server_id")
        if not server_id:
            return jsonify({"error": "server_id required"}), 400
        binding_id = str(_uuid.uuid4())
        now = now_isoformat()
        with get_connection() as db:
            try:
                db.execute(
                    """INSERT INTO mc_app_server_bindings
                       (id, app_id, server_id, role, port, protocol, notes, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (binding_id, app_id, server_id,
                     data.get("role", "primary"),
                     data.get("port"),
                     data.get("protocol"),
                     data.get("notes"),
                     now),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc):
                    return jsonify({"error": "Binding already exists"}), 409
                raise
        return jsonify({"ok": True, "id": binding_id}), 201

    @bp.route("/api/app-inventory/<app_id>/servers", methods=["GET"])
    @mdc_login_required
    def app_list_servers(app_id):
        """List servers bound to an app."""
        with get_connection() as db:
            rows = db.execute(
                "SELECT * FROM mc_app_server_bindings WHERE app_id=%s ORDER BY created_at",
                (app_id,),
            ).fetchall()
        return jsonify({"bindings": [dict(r) for r in rows]})

    @bp.route("/api/app-inventory/<app_id>/servers/<server_id>", methods=["DELETE"])
    @mdc_login_required
    def app_unbind_server(app_id, server_id):
        """Remove app-server binding."""
        with get_connection() as db:
            db.execute(
                "DELETE FROM mc_app_server_bindings WHERE app_id=%s AND server_id=%s",
                (app_id, server_id),
            )
        return jsonify({"ok": True})

    @bp.route("/api/server-migration/inventory/<server_id>/apps", methods=["GET"])
    @mdc_login_required
    def server_list_apps(server_id):
        """List apps bound to a server."""
        with get_connection() as db:
            rows = db.execute(
                """SELECT b.*, a.name as app_name, a.criticality, a.migration_strategy
                   FROM mc_app_server_bindings b
                   JOIN mc_app_inventory a ON a.id = b.app_id
                   WHERE b.server_id=%s ORDER BY b.created_at""",
                (server_id,),
            ).fetchall()
        return jsonify({"apps": [dict(r) for r in rows]})

    # ── App Dependency Graph ─────────────────────────────────────────────────

    @bp.route("/api/app-inventory/<app_id>/dependencies", methods=["POST"])
    @mdc_login_required
    def app_dep_create(app_id):
        """Add a dependency edge from this app."""
        data = request.get_json(silent=True) or {}
        if not data.get("target_app_id") and not data.get("target_server_id"):
            return jsonify({"error": "target_app_id or target_server_id required"}), 400
        dep_id = str(_uuid.uuid4())
        now = now_isoformat()
        with get_connection() as db:
            db.execute(
                """INSERT INTO mc_app_dependencies
                   (id, source_app_id, target_app_id, target_server_id, target_service,
                    dep_type, protocol, port, latency_sla_ms, notes, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    dep_id, app_id,
                    data.get("target_app_id"),
                    data.get("target_server_id"),
                    data.get("target_service"),
                    data.get("dep_type", "hard"),
                    data.get("protocol"),
                    data.get("port"),
                    data.get("latency_sla_ms"),
                    data.get("notes"),
                    now,
                ),
            )
        return jsonify({"ok": True, "id": dep_id}), 201

    @bp.route("/api/app-inventory/<app_id>/dependencies", methods=["GET"])
    @mdc_login_required
    def app_dep_list(app_id):
        """List direct dependencies for an app."""
        with get_connection() as db:
            rows = db.execute(
                "SELECT * FROM mc_app_dependencies WHERE source_app_id=%s ORDER BY created_at",
                (app_id,),
            ).fetchall()
        return jsonify({"dependencies": [dict(r) for r in rows]})

    @bp.route("/api/app-inventory/<app_id>/dependencies/<dep_id>", methods=["DELETE"])
    @mdc_login_required
    def app_dep_delete(app_id, dep_id):
        """Remove a dependency edge."""
        with get_connection() as db:
            db.execute(
                "DELETE FROM mc_app_dependencies WHERE id=%s AND source_app_id=%s",
                (dep_id, app_id),
            )
        return jsonify({"ok": True})

    @bp.route("/api/app-inventory/<app_id>/dependency-graph", methods=["GET"])
    @mdc_login_required
    def app_dep_graph(app_id):
        """Transitive closure of app dependencies (BFS, max depth 10)."""
        with get_connection() as db:
            visited_apps: set = set()
            nodes: list = []
            edges: list = []
            queue = [app_id]
            depth = 0
            while queue and depth < 10:
                next_queue: list = []
                for aid in queue:
                    if aid in visited_apps:
                        continue
                    visited_apps.add(aid)
                    row = db.execute(
                        "SELECT id, name, app_type FROM mc_app_inventory WHERE id=%s", (aid,)
                    ).fetchone()
                    if row:
                        nodes.append({"id": row["id"], "name": row["name"], "type": row["app_type"] or "app"})
                    deps = db.execute(
                        "SELECT * FROM mc_app_dependencies WHERE source_app_id=%s", (aid,)
                    ).fetchall()
                    for dep in deps:
                        edges.append({
                            "source": aid,
                            "target": dep["target_app_id"] or dep["target_server_id"],
                            "dep_type": dep["dep_type"],
                            "service": dep["target_service"],
                        })
                        if dep["target_app_id"] and dep["target_app_id"] not in visited_apps:
                            next_queue.append(dep["target_app_id"])
                queue = next_queue
                depth += 1
        return jsonify({"nodes": nodes, "edges": edges})

    @bp.route("/api/app-inventory/migration-order", methods=["GET"])
    @mdc_login_required
    def app_migration_order():
        """Topological sort of apps by hard dependencies (no-dep apps first)."""
        with get_connection() as db:
            all_apps = {r["id"]: dict(r) for r in db.execute("SELECT * FROM mc_app_inventory").fetchall()}
            hard_deps = db.execute(
                "SELECT source_app_id, target_app_id FROM mc_app_dependencies "
                "WHERE dep_type='hard' AND target_app_id IS NOT NULL"
            ).fetchall()
        in_degree: dict = {aid: 0 for aid in all_apps}
        dependents: dict = {aid: [] for aid in all_apps}
        for dep in hard_deps:
            src, tgt = dep["source_app_id"], dep["target_app_id"]
            if src in in_degree and tgt in in_degree:
                in_degree[src] += 1
                dependents[tgt].append(src)
        queue = [aid for aid, deg in in_degree.items() if deg == 0]
        order: list = []
        wave = 1
        while queue:
            next_q: list = []
            for aid in sorted(queue):
                order.append({**all_apps[aid], "suggested_wave": wave})
                for dep_src in dependents.get(aid, []):
                    in_degree[dep_src] -= 1
                    if in_degree[dep_src] == 0:
                        next_q.append(dep_src)
            queue = next_q
            wave += 1
        remaining = [aid for aid, deg in in_degree.items() if deg > 0]
        for aid in remaining:
            order.append({**all_apps[aid], "suggested_wave": wave, "cycle_detected": True})
        return jsonify({"order": order, "total": len(order)})

    # ── Data Migration Planner ───────────────────────────────────────────────

    @bp.route("/api/data-migration", methods=["POST"])
    @mdc_login_required
    def data_migration_create():
        """Create a data migration plan record."""
        data = request.get_json(silent=True) or {}
        dm_id = str(_uuid.uuid4())
        now = now_isoformat()
        with get_connection() as db:
            db.execute(
                """INSERT INTO mc_data_migration
                   (id, session_id, app_id, source_type, source_host, source_db, source_schema,
                    target_type, target_host, target_db, target_schema, migration_method,
                    estimated_size_gb, estimated_duration_minutes, validation_query,
                    cutover_type, rollback_procedure, status, notes, classification, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    dm_id,
                    data.get("session_id"),
                    data.get("app_id"),
                    data.get("source_type"),
                    data.get("source_host"),
                    data.get("source_db"),
                    data.get("source_schema"),
                    data.get("target_type"),
                    data.get("target_host"),
                    data.get("target_db"),
                    data.get("target_schema"),
                    data.get("migration_method"),
                    data.get("estimated_size_gb"),
                    data.get("estimated_duration_minutes"),
                    data.get("validation_query"),
                    data.get("cutover_type"),
                    data.get("rollback_procedure"),
                    data.get("status", "planned"),
                    data.get("notes"),
                    data.get("classification", "CUI"),
                    now,
                ),
            )
        return jsonify({"ok": True, "id": dm_id}), 201

    @bp.route("/api/data-migration/<dm_id>", methods=["GET"])
    @mdc_login_required
    def data_migration_get(dm_id):
        """Get a data migration plan by ID."""
        with get_connection() as db:
            row = db.execute("SELECT * FROM mc_data_migration WHERE id=%s", (dm_id,)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify(dict(row))

    @bp.route("/api/data-migration/<dm_id>/status", methods=["PUT"])
    @mdc_login_required
    def data_migration_update_status(dm_id):
        """Update status of a data migration plan."""
        data = request.get_json(silent=True) or {}
        status = data.get("status")
        if not status:
            return jsonify({"error": "status required"}), 400
        now = now_isoformat()
        updates = {"status": status}
        if status == "in_progress":
            updates["started_at"] = now
        elif status in ("done", "failed"):
            updates["completed_at"] = now
        set_clause = ", ".join(f"{k}=%s" for k in updates)
        vals = list(updates.values()) + [dm_id]
        with get_connection() as db:
            db.execute(f"UPDATE mc_data_migration SET {set_clause} WHERE id=%s", vals)  # nosec B608 – cols from hardcoded updates dict; values parameterized
        return jsonify({"ok": True})

    # ── ServiceNow CMDB Import ───────────────────────────────────────────────

    @bp.route("/api/app-inventory/import/servicenow", methods=["GET"])
    @mdc_login_required
    def app_inventory_import_servicenow():
        """Pull cmdb_ci_appl records from ServiceNow and import into mc_app_inventory."""
        base_url = request.args.get("base_url", "")
        username = request.args.get("username", "")
        password = request.args.get("password", "")
        bearer = request.args.get("bearer_token", "")
        if not base_url:
            return jsonify({"error": "base_url required"}), 400
        try:
            from tools.databridge.connectors.servicenow_connector import ServiceNowCMDBConnector
            connector = ServiceNowCMDBConnector()
            cfg: dict = {"base_url": base_url}
            if bearer:
                cfg["bearer_token"] = bearer
            elif username:
                cfg.update({"username": username, "password": password})
            else:
                return jsonify({"error": "Authentication required (bearer_token or username/password)"}), 400
            connector._config = cfg
            connector._base_url = base_url.rstrip("/")
            connector._auth_headers = connector._build_auth_headers(cfg)
            connector._connected = True
            apps = connector.fetch_table("applications")
            inserted = 0
            now = now_isoformat()
            with get_connection() as db:
                for snow_app in apps:
                    mapped = connector.map_app_to_inventory(snow_app)
                    app_id = str(_uuid.uuid4())
                    db.execute(
                        """INSERT INTO mc_app_inventory
                           (id, name, version, owner, team, criticality, notes,
                            classification, created_at, updated_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (app_id, mapped["name"], mapped.get("version", ""),
                         mapped.get("owner", ""), mapped.get("team", ""),
                         mapped.get("criticality", "medium"), mapped.get("notes", ""),
                         "CUI", now, now),
                    )
                    inserted += 1
            return jsonify({"ok": True, "imported": inserted})
        except Exception as exc:
            logger.warning("ServiceNow import error: %s", exc)
            return jsonify({"error": str(exc)}), 500

    # ── Cloud Application Migration (CAM) project hub ────────────────────────

    @bp.route("/projects")
    @mdc_login_required
    def cam_projects():
        """CAM project hub — list all cloud migration projects."""
        from tools.migration_canvas.cam_engine import get_projects
        projects = get_projects()
        return render_template("migration_canvas/cam_projects.html", projects=projects)

    @bp.route("/projects/<project_id>")
    @mdc_login_required
    def cam_project_detail(project_id):
        """CAM project detail — phases, SOPs, app inventory, AI opportunities."""
        from tools.migration_canvas.cam_engine import get_project_detail
        project = get_project_detail(project_id=project_id)
        if not project:
            abort(404)
        return render_template("migration_canvas/cam_project_detail.html", project=project)

    @bp.route("/api/projects")
    @mdc_login_required
    def cam_api_projects():
        from tools.migration_canvas.cam_engine import get_projects
        return jsonify({"ok": True, "projects": get_projects()})

    @bp.route("/api/projects/<project_id>")
    @mdc_login_required
    def cam_api_project_detail(project_id):
        from tools.migration_canvas.cam_engine import get_project_detail
        project = get_project_detail(project_id=project_id)
        if not project:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True, "project": project})

    @bp.route("/api/projects/<project_id>/refactor-jobs")
    @mdc_login_required
    def cam_api_refactor_jobs(project_id):
        """List all refactor jobs for a CAM project."""
        from tools.migration_canvas.cam_refactor_engine import get_jobs
        jobs = get_jobs(project_id)
        return jsonify({"ok": True, "project_id": project_id, "jobs": jobs, "total": len(jobs)})

    @bp.route("/api/projects/<project_id>/refactor", methods=["POST"])
    @mdc_login_required
    def cam_api_refactor_dispatch(project_id):
        """Dispatch (and optionally run) refactor jobs for a CAM project."""
        from tools.migration_canvas.cam_refactor_engine import dispatch, run_all
        data = request.get_json(silent=True) or {}
        component = data.get("component_name")
        dry_run = bool(data.get("dry_run", False))
        run = bool(data.get("run", True))
        try:
            if run and not dry_run:
                result = run_all(project_id, component_name=component, dry_run=False)
            else:
                queued = dispatch(project_id, component_name=component, dry_run=dry_run)
                result = {"dry_run": dry_run, "queued": len(queued), "jobs": queued}
            return jsonify({"ok": True, "project_id": project_id, **result})
        except Exception as exc:
            logger.exception("cam_api_refactor_dispatch error: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @bp.route("/api/projects/<project_id>/refactor-jobs/<job_id>")
    @mdc_login_required
    def cam_api_refactor_job_detail(project_id, job_id):
        """Return detail for a single refactor job, including artifacts."""
        from tools.migration_canvas.cam_refactor_engine import get_job_detail
        job = get_job_detail(job_id)
        if not job or job.get("project_id") != project_id:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True, "job": job})

    @bp.route("/api/projects/<project_id>/refactor-jobs/<job_id>/run", methods=["POST"])
    @mdc_login_required
    def cam_api_refactor_run_job(project_id, job_id):
        """Execute a single queued refactor job."""
        from tools.migration_canvas.cam_refactor_engine import run_job, get_job_detail
        job_check = get_job_detail(job_id)
        if not job_check or job_check.get("project_id") != project_id:
            return jsonify({"ok": False, "error": "not found"}), 404
        result = run_job(job_id)
        return jsonify({"ok": True, "job": result})

    @bp.route("/api/ai-trace")
    @mdc_login_required
    def mc_api_ai_trace():
        """Return recent AI decisions made by MC assessment engines."""
        limit = min(int(request.args.get("limit", 50)), 200)
        record_id = request.args.get("record_id")
        try:
            from tools.db.storage import get_connection as _gc
            with _gc() as _conn:
                if record_id:
                    rows = _conn.execute(
                        "SELECT * FROM canvas_ai_decisions WHERE canvas_type='mc' AND record_id=%s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (record_id, limit),
                    ).fetchall()
                else:
                    rows = _conn.execute(
                        "SELECT * FROM canvas_ai_decisions WHERE canvas_type='mc' "
                        "ORDER BY created_at DESC LIMIT %s",
                        (limit,),
                    ).fetchall()
            return jsonify({"ok": True, "canvas": "mc", "decisions": [dict(r) for r in rows]})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    def _compute_migration_governance(graph_data: dict) -> dict:
        """Migration governance check — AWS MAP / Azure MAP / DoD DECC aligned."""
        nodes = graph_data.get("nodes", [])
        types = [n.get("type", "").lower() for n in nodes]
        labels = [str(n.get("label", "")).lower() for n in nodes]

        def _any(*pfx): return any(any(t.startswith(p) for p in pfx) for t in types)
        def _lbl(*kws): return any(kw in l for l in labels for kw in kws)

        CHECKS = [
            ("Source Environment Mapped",        "Readiness Assessment", "CAT1", _any("src-","on-prem-","legacy-") or _lbl("source","on-prem","legacy","current state","as-is")),
            ("Target Environment Defined",        "Readiness Assessment", "CAT1", _any("tgt-","cloud-","aws-","az-","gcp-","oci-") or _lbl("target","destination","to-be","future state","cloud")),
            ("Migration Waves Defined",           "Readiness Assessment", "CAT2", _lbl("wave","phase","batch","sprint","migration group")),
            ("Dependency Mapping Complete",       "Data Mapping",         "CAT1", _lbl("depend","upstream","downstream","coupling","service map","cmdb")),
            ("Data Classification & Inventory",  "Data Mapping",         "CAT1", _lbl("data map","inventory","catalog","schema","table","dataset","pii","classified")),
            ("Network Connectivity Validated",    "Readiness Assessment", "CAT2", _lbl("network","vpn","direct connect","expressroute","connectivity","bandwidth")),
            ("IAM / Access Rights Mapped",        "Data Mapping",         "CAT2", _lbl("iam","role","permission","access right","service account","identity")),
            ("Data Validation Gates Defined",     "Testing Gates",        "CAT1", _lbl("validation","checksum","reconcil","data integrity","row count","hash")),
            ("Smoke / Integration Tests Defined", "Testing Gates",        "CAT1", _lbl("smoke test","integration test","regression","test plan","qa gate","uat")),
            ("Performance / Load Testing",        "Testing Gates",        "CAT2", _lbl("load test","performance test","stress test","benchmark","throughput","latency")),
            ("Rollback Plan Documented",          "Risk Management",      "CAT1", _lbl("rollback","fallback","revert","back-out","contingency","undo")),
            ("Cutover Plan & Runbook",            "Cutover Planning",     "CAT1", _lbl("cutover","go-live","cutover plan","runbook","maintenance window","freeze")),
            ("Business Continuity / DR",          "Risk Management",      "CAT2", _lbl("bcp","dr","business continuity","rto","rpo","failover","disaster")),
            ("Change Freeze Window Defined",      "Cutover Planning",     "CAT2", _lbl("change freeze","maintenance window","change window","blackout")),
            ("Stakeholder Sign-off Gates",        "Cutover Planning",     "CAT3", _lbl("sign-off","approval","stakeholder","go/no-go","decision gate")),
            ("Compliance / ATO Mapping",          "Risk Management",      "CAT2", _lbl("ato","fedramp","cmmc","rmf","compliance","accreditation","stig")),
            ("Monitoring Post-Migration",         "Risk Management",      "CAT2", _lbl("monitor","alert","post-migration","hyper care","observ","dashboard")),
            ("Cost Estimation & Tracking",        "Readiness Assessment", "CAT3", _lbl("cost","budget","tco","roi","finops","spend estimate")),
        ]

        PILLARS = ["Readiness Assessment", "Data Mapping", "Testing Gates", "Cutover Planning", "Risk Management"]
        WEIGHTS = {"CAT1": 3, "CAT2": 2, "CAT3": 1}
        MATURITY = [
            (0,  "L1 — Initial",    "Ad-hoc migration with no formal governance."),
            (30, "L2 — Developing", "Basic inventory and dependencies mapped."),
            (55, "L3 — Defined",    "Structured plan with documented gates."),
            (70, "L4 — Managed",    "Tested rollback and compliance validated."),
            (85, "L5 — Optimised",  "Automated validation with continuous monitoring."),
        ]

        check_results, total_w, passed_w = [], 0, 0
        cats = {p: {"passed": 0, "total": 0, "pct": 0} for p in PILLARS}
        for title, pillar, sev, passed in CHECKS:
            w = WEIGHTS[sev]; total_w += w
            status = "pass" if passed else "fail"
            if passed: passed_w += w
            cats.setdefault(pillar, {"passed": 0, "total": 0, "pct": 0})
            cats[pillar]["total"] += 1
            if passed: cats[pillar]["passed"] += 1
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
            "assessed_at": now_isoformat(),
        }

    @bp.route("/api/designs/<design_id>/governance", methods=["POST"])
    @mdc_login_required
    def mdc_api_governance(design_id):
        """Run migration governance framework check."""
        import uuid as _uuid_mod
        with get_connection() as conn:
            row = conn.execute("SELECT graph_json FROM migration_designs WHERE id=%s", (design_id,)).fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404
            try:
                graph_data = json.loads(row["graph_json"]) if isinstance(row["graph_json"], str) else row["graph_json"]
            except (json.JSONDecodeError, TypeError):
                return jsonify({"error": "Invalid graph data"}), 400

            result = _compute_migration_governance(graph_data)

            assess_id = str(_uuid_mod.uuid4())
            conn.execute(
                "INSERT INTO mc_assessments "
                "(id, design_id, assessment_type, findings_json, score, grade, "
                "cat1_findings, cat2_findings, cat3_findings, readiness_score, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (assess_id, design_id, "governance",
                 json.dumps([{"title": c["title"], "severity": c["severity"], "status": c["status"]}
                             for c in result["checks"]]),
                 result["score"], result["grade"],
                 sum(1 for c in result["checks"] if c["severity"] == "CAT1" and c["status"] == "fail"),
                 sum(1 for c in result["checks"] if c["severity"] == "CAT2" and c["status"] == "fail"),
                 sum(1 for c in result["checks"] if c["severity"] == "CAT3" and c["status"] == "fail"),
                 result["score"], now_isoformat()),
            )
        return jsonify(result)

    return bp
