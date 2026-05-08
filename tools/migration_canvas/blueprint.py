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
import logging
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

logger = logging.getLogger("icdev.migration_canvas")

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


def create_migration_blueprint():
    """Create and return the Migration Design Canvas Blueprint.

    Returns None if ICDEV_MIGRATION_CANVAS_ENABLED is false.
    """
    enabled = os.environ.get("ICDEV_MIGRATION_CANVAS_ENABLED", "true").lower()
    if enabled not in ("true", "1", "yes"):
        logger.info("Migration Canvas disabled (ICDEV_MIGRATION_CANVAS_ENABLED=%s)", enabled)
        return None

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
                    "INSERT INTO mc_audit (design_id, user, action, detail, created_at) VALUES (?,?,?,?,?)",
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
    @mdc_login_required
    def mc_index():
        """Migration Design Canvas dashboard — list designs + recent assessments."""
        with get_connection() as conn:
            designs = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT id, name, description, migration_type, classification, "
                    "created_at, updated_at "
                    "FROM migration_designs ORDER BY updated_at DESC"
                ).fetchall()
            ]
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
                    "SELECT id, name, category, description, tags FROM mc_templates ORDER BY category, name"
                ).fetchall()
            ]
        return render_template(
            "migration_canvas/index.html",
            designs=designs,
            recent_assessments=recent_assessments,
            templates=templates,
            migration_types=MIGRATION_TYPES,
        )

    @bp.route("/canvas/<design_id>")
    @mdc_login_required
    def mc_canvas(design_id):
        """Open existing migration design canvas."""
        with get_connection() as conn:
            design = _row_to_dict(
                conn.execute("SELECT * FROM migration_designs WHERE id=?", (design_id,)).fetchone()
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
                    "SELECT name, graph_json FROM mc_templates WHERE id=?",
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
                    "SELECT id, name FROM migration_designs WHERE template_id=? LIMIT 1",
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
                "VALUES (?,?,?,?,?,?,?,?,?)",
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
            row = conn.execute("SELECT * FROM migration_designs WHERE id=?", (design_id,)).fetchone()
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
            existing = conn.execute("SELECT id FROM migration_designs WHERE id=?", (design_id,)).fetchone()
            if not existing:
                return jsonify({"error": "Design not found"}), 404
            conn.execute(
                "UPDATE migration_designs SET name=?, description=?, migration_type=?, "
                "graph_json=?, classification=?, updated_at=? WHERE id=?",
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
            conn.execute("DELETE FROM migration_designs WHERE id=?", (design_id,))
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
        with get_connection() as conn:
            row = conn.execute("SELECT graph_json FROM migration_designs WHERE id=?", (design_id,)).fetchone()
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
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
        return jsonify({
            "assessment_id": assessment_id,
            **result,
            "readiness": readiness,
        })

    @bp.route("/api/designs/<design_id>/gaps", methods=["GET"])
    @mdc_login_required
    def mc_api_gaps(design_id):
        """Detect gaps in a migration design."""
        with get_connection() as conn:
            row = conn.execute("SELECT graph_json FROM migration_designs WHERE id=?", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "Design not found"}), 404
        gaps = detect_migration_gaps(row["graph_json"])
        return jsonify({"design_id": design_id, "gaps": gaps, "total": len(gaps)})

    @bp.route("/api/designs/<design_id>/readiness", methods=["GET"])
    @mdc_login_required
    def mc_api_readiness(design_id):
        """Compute migration readiness score."""
        with get_connection() as conn:
            row = conn.execute("SELECT graph_json FROM migration_designs WHERE id=?", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "Design not found"}), 404
        readiness = compute_readiness_score(row["graph_json"])
        return jsonify({"design_id": design_id, **readiness})

    @bp.route("/api/designs/<design_id>/stats", methods=["GET"])
    @mdc_login_required
    def mc_api_stats(design_id):
        """Get design statistics."""
        with get_connection() as conn:
            row = conn.execute("SELECT graph_json FROM migration_designs WHERE id=?", (design_id,)).fetchone()
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
            row = conn.execute("SELECT * FROM mc_templates WHERE id=?", (template_id,)).fetchone()
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
                "FROM mc_versions WHERE design_id=? ORDER BY version_number DESC",
                (design_id,),
            ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/versions/<design_id>", methods=["POST"])
    @mdc_login_required
    def mc_api_create_version(design_id):
        """Create a version snapshot of the current design."""
        with get_connection() as conn:
            design = conn.execute("SELECT graph_json FROM migration_designs WHERE id=?", (design_id,)).fetchone()
            if not design:
                return jsonify({"error": "Design not found"}), 404
            max_ver = conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) FROM mc_versions WHERE design_id=?",
                (design_id,),
            ).fetchone()[0]
            ver_id = str(_uuid.uuid4())
            data = request.get_json(force=True, silent=True) or {}
            conn.execute(
                "INSERT INTO mc_versions (id, design_id, version_number, graph_json, change_summary, user_id, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
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
            row = conn.execute("SELECT * FROM mc_runbooks WHERE id=?", (runbook_id,)).fetchone()
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
                    "SELECT * FROM mc_oracle_predictions WHERE design_id=? ORDER BY created_at DESC LIMIT 50",
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

        with get_connection() as conn:
            conn.execute(
                "INSERT INTO mc_net_sessions "
                "(id, design_id, src_model, tgt_model, src_device_name, tgt_device_name, src_site, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, data.get("design_id"), src_model, tgt_model,
                 data.get("src_device_name", ""), data.get("tgt_device_name", ""),
                 data.get("src_site", ""), now_isoformat(), now_isoformat()),
            )
            # Link back on design if provided
            if data.get("design_id"):
                conn.execute(
                    "UPDATE migration_designs SET network_session_id=? WHERE id=?",
                    (sid, data["design_id"]),
                )
            conn.commit()

        try:
            _nm._update_kg(sid, data.get("design_id"))
        except Exception:
            pass

        _audit(sid, "net_session_created", f"src={src_model} tgt={tgt_model}")
        return jsonify({"id": sid, "src_model": src_model, "tgt_model": tgt_model})

    @bp.route("/api/network-migration/<sid>", methods=["GET"])
    @mdc_login_required
    def mc_net_api_get(sid):
        """Get full session with all sub-table data."""
        with get_connection() as conn:
            sess = _row_to_dict(conn.execute("SELECT * FROM mc_net_sessions WHERE id=?", (sid,)).fetchone())
            if not sess:
                return jsonify({"error": "Session not found"}), 404
            port_map = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM mc_net_port_map WHERE session_id=? ORDER BY rowid", (sid,)).fetchall()]
            sess["port_map"] = port_map
            sess["compat_checks"] = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM mc_net_compat_checks WHERE session_id=? ORDER BY severity, category", (sid,)).fetchall()]
            sess["test_cases"] = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM mc_net_test_cases WHERE session_id=? ORDER BY phase, seq_no", (sid,)).fetchall()]
            sess["cutover_steps"] = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM mc_net_cutover_steps WHERE session_id=? ORDER BY seq_no", (sid,)).fetchall()]
            sess["erb"] = _row_to_dict(conn.execute(
                "SELECT * FROM mc_net_erb_metadata WHERE session_id=?", (sid,)).fetchone())
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
            "hardware_profiles": hw,
            "parsed_interfaces": parsed_ifs,
        })

    @bp.route("/api/network-migration/<sid>", methods=["PATCH"])
    @mdc_login_required
    def mc_net_api_update(sid):
        """Update top-level session fields (device names, site, status)."""
        data = request.get_json(force=True, silent=True) or {}
        allowed = {"src_device_name", "tgt_device_name", "src_site", "status", "src_model", "tgt_model"}
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return jsonify({"error": "No valid fields"}), 400
        set_clause = ", ".join(f"{k}=?" for k in fields)
        with get_connection() as conn:
            conn.execute(
                f"UPDATE mc_net_sessions SET {set_clause}, updated_at=? WHERE id=?",  # nosec B608
                list(fields.values()) + [now_isoformat(), sid],
            )
            conn.commit()
        return jsonify({"ok": True})

    @bp.route("/api/network-migration/<sid>/hardware-profiles", methods=["GET"])
    @mdc_login_required
    def mc_net_api_hw_profiles(sid):
        """Fetch hardware profiles for source and target models."""
        with get_connection() as conn:
            sess = _row_to_dict(conn.execute("SELECT src_model, tgt_model FROM mc_net_sessions WHERE id=?", (sid,)).fetchone())
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
                "UPDATE mc_net_sessions SET src_config_raw=?, config_parsed=1, updated_at=? WHERE id=?",
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

        # Accept either multipart file upload or base64 JSON
        if request.files.get("file"):
            f = request.files["file"]
            suffix = _os.path.splitext(f.filename or "diagram.png")[1].lower() or ".png"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                f.save(tmp.name)
                tmp_path = tmp.name
            topology_name = request.form.get("name", f"migration-{sid}-import")
            cleanup = True
        else:
            data = request.get_json(force=True, silent=True) or {}
            if not data.get("content"):
                return jsonify({"error": "Provide a file upload or base64 content"}), 400
            import base64 as _b64
            suffix = {"png":".png","jpg":".jpg","jpeg":".jpg","pdf":".pdf",
                      "drawio":".drawio","vsdx":".vsdx"}.get(data.get("format","png"), ".png")
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(_b64.b64decode(data["content"]))
                tmp_path = tmp.name
            topology_name = data.get("name", f"migration-{sid}-import")
            cleanup = True

        try:
            # Call NDC ingest pipeline
            from tools.network.network_ingester import ingest_diagram
            topology = ingest_diagram(tmp_path, project_id=None, topology_name=topology_name)
        except Exception as exc:
            logger.warning("Diagram ingest failed: %s", exc)
            topology = {"nodes": [], "edges": [], "error": str(exc)}
        finally:
            if cleanup:
                try:
                    _os.unlink(tmp_path)
                except Exception:
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
                    "UPDATE mc_net_sessions SET diagram_topology_json=?, updated_at=? WHERE id=?",
                    (topo_json, now_isoformat(), sid),
                )
                conn.commit()
            except Exception:
                # Column may not exist in older schemas — alter table
                try:
                    conn.execute("ALTER TABLE mc_net_sessions ADD COLUMN diagram_topology_json TEXT")
                    conn.execute(
                        "UPDATE mc_net_sessions SET diagram_topology_json=?, updated_at=? WHERE id=?",
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
                sess = _row_to_dict(conn.execute("SELECT design_id FROM mc_net_sessions WHERE id=?", (sid,)).fetchone())
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
                "SELECT * FROM mc_net_port_map WHERE session_id=? ORDER BY rowid", (sid,)
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
                sess = _row_to_dict(conn.execute("SELECT * FROM mc_net_sessions WHERE id=?", (sid,)).fetchone())
            raw_config = sess.get("src_config_raw", "")
            if not raw_config:
                return jsonify({"error": "No config imported yet — call /parse-config first"}), 400
            parsed = _nm.parse_source_config(raw_config)
            hw = _nm.fetch_hardware_profiles(sess["src_model"], sess["tgt_model"])
            rows = _nm._generate_port_map(parsed["interfaces"], hw["target"])

        with get_connection() as conn:
            conn.execute("DELETE FROM mc_net_port_map WHERE session_id=?", (sid,))
            for r in rows:
                conn.execute(
                    "INSERT INTO mc_net_port_map (session_id, src_interface, src_speed_gbps, src_media, "
                    "src_optic_type, src_ip_address, src_description, src_circuit_id, tgt_interface, "
                    "tgt_speed_gbps, tgt_optic_required, optic_change, speed_mismatch, cable_id, "
                    "far_end_device, far_end_port, notes, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                "SELECT * FROM mc_net_port_map WHERE session_id=? ORDER BY rowid", (sid,)).fetchall()]
        return jsonify({"ok": True, "count": len(rows), "port_map": saved})

    @bp.route("/api/network-migration/<sid>/compat-check", methods=["POST"])
    @mdc_login_required
    def mc_net_api_compat_check(sid):
        """Run auto-compatibility checks and persist results."""
        data = request.get_json(force=True, silent=True) or {}

        with get_connection() as conn:
            sess = _row_to_dict(conn.execute("SELECT * FROM mc_net_sessions WHERE id=?", (sid,)).fetchone())
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
            conn.execute("DELETE FROM mc_net_compat_checks WHERE session_id=? AND auto_detected=1", (sid,))
            for c in checks:
                conn.execute(
                    "INSERT INTO mc_net_compat_checks (session_id, category, check_name, expected, actual, "
                    "severity, status, override_reason, auto_detected) VALUES (?,?,?,?,?,?,?,?,?)",
                    (sid, c["category"], c["check_name"], c["expected"], c["actual"],
                     c["severity"], c["status"], c.get("override_reason",""), 1),
                )
            conn.commit()

        _audit(sid, "compat_check_run", f"{len(checks)} checks; {sum(1 for c in checks if c['status']=='fail')} fails")
        return jsonify({"checks": checks, "total": len(checks),
                        "blockers": sum(1 for c in checks if c["status"]=="fail" and not c.get("override_reason"))})

    @bp.route("/api/network-migration/<sid>/convert-config", methods=["POST"])
    @mdc_login_required
    def mc_net_api_convert_config(sid):
        """Convert source config to target config and run commit-check simulation."""
        with get_connection() as conn:
            sess = _row_to_dict(conn.execute("SELECT * FROM mc_net_sessions WHERE id=?", (sid,)).fetchone())
            port_map = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM mc_net_port_map WHERE session_id=?", (sid,)).fetchall()]
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
                "SELECT * FROM mc_net_test_cases WHERE session_id=? ORDER BY phase, seq_no", (sid,)
            ).fetchall()
            if not rows and auto_seed:
                # Get vendor from session config
                sess = _row_to_dict(conn.execute("SELECT src_config_raw FROM mc_net_sessions WHERE id=?", (sid,)).fetchone())
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
                        "VALUES (?,?,?,?,?,?)",
                        (sid, t["phase"], t["seq_no"], t["test_name"], t["procedure"], t["expected_result"]),
                    )
                conn.commit()
                rows = conn.execute(
                    "SELECT * FROM mc_net_test_cases WHERE session_id=? ORDER BY phase, seq_no", (sid,)
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
                    "UPDATE mc_net_test_cases SET passed=?, actual_result=?, notes=?, executed_at=?, updated_at=? WHERE id=? AND session_id=?",
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
                "SELECT * FROM mc_net_cutover_steps WHERE session_id=? ORDER BY seq_no", (sid,)
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
                sess = _row_to_dict(conn.execute("SELECT src_config_raw FROM mc_net_sessions WHERE id=?", (sid,)).fetchone())
                port_map = [_row_to_dict(r) for r in conn.execute(
                    "SELECT * FROM mc_net_port_map WHERE session_id=?", (sid,)).fetchall()]
            parsed = _nm.parse_source_config(sess.get("src_config_raw","")) if sess.get("src_config_raw") else {}
            steps = _nm.build_cutover_sequence(port_map, data.get("strategy","traffic_volume_asc"), parsed)

        with get_connection() as conn:
            conn.execute("DELETE FROM mc_net_cutover_steps WHERE session_id=?", (sid,))
            for s in steps:
                conn.execute(
                    "INSERT INTO mc_net_cutover_steps (session_id, seq_no, circuit_id, interface, description, "
                    "drain_action, cutover_action, verify_action, rollback_action, duration_min, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, s.get("seq_no",0), s.get("circuit_id",""), s.get("interface",""), s.get("description",""),
                     s.get("drain_action",""), s.get("cutover_action",""), s.get("verify_action",""),
                     s.get("rollback_action",""), s.get("duration_min",5), s.get("status","pending")),
                )
            conn.commit()

        _audit(sid, "cutover_steps_saved", f"{len(steps)} steps")
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
            existing = conn.execute("SELECT id FROM mc_net_erb_metadata WHERE session_id=?", (sid,)).fetchone()
            if existing:
                set_clause = ", ".join(f"{k}=?" for k in fields)
                conn.execute(
                    f"UPDATE mc_net_erb_metadata SET {set_clause}, updated_at=? WHERE session_id=?",  # nosec B608
                    list(fields.values()) + [now_isoformat(), sid],
                )
            else:
                fields["id"] = eid
                fields["session_id"] = sid
                fields["created_at"] = now_isoformat()
                fields["updated_at"] = now_isoformat()
                cols = ", ".join(fields.keys())
                placeholders = ", ".join("?" * len(fields))
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
                "UPDATE mc_net_erb_metadata SET sop_id=?, approval_status='pending', updated_at=? WHERE session_id=?",
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

    @bp.route("/api/network-migration/<sid>/export-diagram", methods=["GET"])
    @mdc_login_required
    def mc_net_api_export_diagram(sid):
        """Export port diagram as SVG or DrawIO JSON."""
        fmt = request.args.get("format", "svg")
        with get_connection() as conn:
            sess = _row_to_dict(conn.execute("SELECT * FROM mc_net_sessions WHERE id=?", (sid,)).fetchone())
            port_map = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM mc_net_port_map WHERE session_id=? ORDER BY rowid", (sid,)).fetchall()]
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

    return bp
