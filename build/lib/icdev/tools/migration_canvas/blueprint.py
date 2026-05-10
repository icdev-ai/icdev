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

    return bp
