# CUI // SP-CTI
"""ICDEV™ BI Dashboard Canvas — Flask Blueprint.

Natural-language-driven analyst/executive dashboards: describe a chart in
plain English, AI drafts a Viz Kernel spec (2D or 3D) from real data —
uploaded files or ICDEV internal sources via IQE — and the browser renders
it with ECharts. Mounted at /bi_dashboard/ inside the ICDEV dashboard.
"""
from __future__ import annotations

import importlib
import os
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, session

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.bi_dashboard")

_MOD_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _MOD_DIR.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"

_ENV_FLAG = "ICDEV_BI_DASHBOARD_ENABLED"


def _login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            if request.is_json or request.path.startswith("/bi_dashboard/api/") or request.method != "GET":
                return jsonify({"error": "Authentication required"}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


# ── Session-derived identity (cnr-bi-01/02) ──────────────────────────────
# The BI canvas persists dashboards/datasets with owner_id + tenant_id RLS
# columns. These helpers thread the authenticated user's identity into every
# store call so a user can only see/mutate their own rows and tenant data is
# actually separated. Identity comes from the auth middleware (flask.g.current_user,
# populated in tools/dashboard/auth.py::_auth_before_request); the raw session
# user_id is the fallback. Never trust request-supplied tenant/owner values.

def _current_user_id() -> str:
    return session.get("user_id") or ""


def _current_tenant() -> str:
    """Tenant of the authenticated user; 'default' when the user has none."""
    from flask import g
    user = getattr(g, "current_user", None) or {}
    try:
        tid = user.get("tenant_id")
    except AttributeError:  # sqlite3.Row supports mapping access but not .get
        tid = user["tenant_id"] if "tenant_id" in user.keys() else None
    return tid or "default"


def _current_role() -> str:
    from flask import g
    user = getattr(g, "current_user", None) or {}
    try:
        role = user.get("role")
    except AttributeError:
        role = user["role"] if "role" in user.keys() else None
    return role or ""


def _is_admin() -> bool:
    return _current_role() == "admin"


def _owns_or_admin(dashboard: dict) -> bool:
    """True if the current user owns this dashboard or is an admin."""
    if _is_admin():
        return True
    uid = _current_user_id()
    return bool(uid) and dashboard.get("owner_id") == uid


def create_bi_dashboard_blueprint() -> Blueprint | None:
    enabled = os.environ.get(_ENV_FLAG, "true").lower()
    if enabled not in ("true", "1", "yes"):
        logger.info("BI Dashboard disabled (%s=%s)", _ENV_FLAG, enabled)
        return None

    from tools.bi_dashboard.db.init_db import init_db
    try:
        init_db()
    except Exception as exc:
        logger.warning("BI Dashboard DB init failed: %s", exc)

    bp = Blueprint("bi_dashboard", __name__, template_folder=str(_TEMPLATE_DIR))

    # ══════════════════════════════════════════════════════════════════════
    # PAGE ROUTES
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/")
    @_login_required
    def index():
        from tools.bi_dashboard.dashboard_store import list_dashboards
        from tools.bi_dashboard.data_source import list_datasets
        tenant = _current_tenant()
        owner = None if _is_admin() else _current_user_id()
        dashboards = list_dashboards(tenant_id=tenant, owner_id=owner)
        datasets = list_datasets(tenant_id=tenant)
        return render_template("bi_dashboard/page.html", dashboards=dashboards, datasets=datasets)

    @bp.route("/<dashboard_id>")
    @_login_required
    def view_dashboard(dashboard_id):
        from tools.bi_dashboard.dashboard_store import get_dashboard
        dashboard = get_dashboard(dashboard_id, tenant_id=_current_tenant())
        # 404 (not 403) on cross-owner reads so we never leak existence.
        if dashboard is None or not _owns_or_admin(dashboard):
            return jsonify({"error": "dashboard not found"}), 404
        return render_template("bi_dashboard/dashboard.html", dashboard=dashboard)

    # ══════════════════════════════════════════════════════════════════════
    # API — data upload
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/upload", methods=["POST"])
    @_login_required
    def api_upload():
        from tools.bi_dashboard.constants import ALLOWED_UPLOAD_EXTENSIONS, MAX_UPLOAD_BYTES
        from tools.bi_dashboard.data_source import ingest_upload

        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify({"error": "file is required"}), 400

        # Enforce the allowlist at the route (defense-in-depth): reject
        # unsupported/dangerous extensions up front rather than relying on
        # parse_dataset to fail downstream.
        fname = file.filename.lower()
        if not fname.endswith(ALLOWED_UPLOAD_EXTENSIONS):
            return jsonify({
                "error": "unsupported file type; allowed: "
                         + ", ".join(ALLOWED_UPLOAD_EXTENSIONS),
            }), 400

        content = file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            return jsonify({"error": f"file exceeds {MAX_UPLOAD_BYTES} byte limit"}), 400

        result = ingest_upload(
            filename=file.filename, content_bytes=content, tenant_id=_current_tenant()
        )
        status = 200 if result.get("success") else 400
        return jsonify(result), status

    @bp.route("/api/datasets", methods=["GET"])
    @_login_required
    def api_list_datasets():
        from tools.bi_dashboard.data_source import list_datasets
        return jsonify({"datasets": list_datasets(tenant_id=_current_tenant())})

    # ══════════════════════════════════════════════════════════════════════
    # API — NL chart generation
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/generate", methods=["POST"])
    @_login_required
    def api_generate():
        from tools.bi_dashboard.data_source import get_dataset
        from tools.bi_dashboard.dashboard_store import log_generation
        from tools.bi_dashboard.spec_generator import generate_spec

        data = request.get_json(silent=True) or {}
        prompt = (data.get("prompt") or "").strip()
        source_id = data.get("source_id") or ""
        prior_structure = data.get("prior_structure")

        if not prompt:
            return jsonify({"error": "prompt is required"}), 400
        if not source_id:
            return jsonify({"error": "source_id is required"}), 400

        dataset = get_dataset(source_id, tenant_id=_current_tenant())
        if dataset is None:
            return jsonify({"error": f"unknown source_id {source_id!r}"}), 404

        spec, method, structure = generate_spec(prompt, dataset, prior_structure)
        log_generation(prompt=prompt, structure=structure, method=method,
                       tenant_id=_current_tenant())

        return jsonify({"spec": spec.to_dict(), "method": method, "structure": structure})

    # ══════════════════════════════════════════════════════════════════════
    # API — dashboard CRUD
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/dashboards", methods=["GET"])
    @_login_required
    def api_list_dashboards():
        from tools.bi_dashboard.dashboard_store import list_dashboards
        owner = None if _is_admin() else _current_user_id()
        return jsonify({"dashboards": list_dashboards(tenant_id=_current_tenant(), owner_id=owner)})

    @bp.route("/api/dashboards", methods=["POST"])
    @_login_required
    def api_create_dashboard():
        from tools.bi_dashboard.dashboard_store import create_dashboard
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "Untitled Dashboard").strip()
        tiles = data.get("tiles") or []
        owner_id = _current_user_id()
        dashboard_id = create_dashboard(
            title=title, tiles=tiles, owner_id=owner_id, tenant_id=_current_tenant()
        )
        return jsonify({"id": dashboard_id}), 201

    @bp.route("/api/dashboards/<dashboard_id>", methods=["GET"])
    @_login_required
    def api_get_dashboard(dashboard_id):
        from tools.bi_dashboard.dashboard_store import get_dashboard
        dashboard = get_dashboard(dashboard_id, tenant_id=_current_tenant())
        # 404 (not 403) on cross-owner reads so we never leak existence.
        if dashboard is None or not _owns_or_admin(dashboard):
            return jsonify({"error": "dashboard not found"}), 404
        return jsonify(dashboard)

    @bp.route("/api/dashboards/<dashboard_id>", methods=["PUT"])
    @_login_required
    def api_update_dashboard(dashboard_id):
        from tools.bi_dashboard.dashboard_store import get_dashboard, update_dashboard_tiles
        tenant = _current_tenant()
        dashboard = get_dashboard(dashboard_id, tenant_id=tenant)
        if dashboard is None:
            return jsonify({"error": "dashboard not found"}), 404
        if not _owns_or_admin(dashboard):
            return jsonify({"error": "forbidden"}), 403
        data = request.get_json(silent=True) or {}
        tiles = data.get("tiles") or []
        updated = update_dashboard_tiles(dashboard_id, tiles, tenant_id=tenant)
        if not updated:
            return jsonify({"error": "dashboard not found"}), 404
        return jsonify({"success": True})

    @bp.route("/api/dashboards/<dashboard_id>", methods=["DELETE"])
    @_login_required
    def api_delete_dashboard(dashboard_id):
        from tools.bi_dashboard.dashboard_store import delete_dashboard, get_dashboard
        tenant = _current_tenant()
        dashboard = get_dashboard(dashboard_id, tenant_id=tenant)
        if dashboard is None:
            return jsonify({"error": "dashboard not found"}), 404
        if not _owns_or_admin(dashboard):
            return jsonify({"error": "forbidden"}), 403
        deleted = delete_dashboard(dashboard_id, tenant_id=tenant)
        if not deleted:
            return jsonify({"error": "dashboard not found"}), 404
        return jsonify({"success": True})

    # ══════════════════════════════════════════════════════════════════════
    # API — export
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/export/<fmt>", methods=["POST"])
    @_login_required
    def api_export(fmt):
        from tools.viz.spec import spec_from_dict

        data = request.get_json(silent=True) or {}
        spec_dict = data.get("spec")
        if not spec_dict:
            return jsonify({"error": "spec is required"}), 400
        try:
            spec = spec_from_dict(spec_dict)
        except (TypeError, ValueError) as exc:
            return jsonify({"error": f"invalid spec: {exc}"}), 400

        if fmt == "png" and spec.kind == "chart":
            from tools.viz.render_png import chart_to_png
            path = chart_to_png(spec)
            return jsonify({"path": path})
        if fmt == "svg" and spec.kind == "chart":
            from tools.viz.render_svg import chart_to_svg
            return jsonify({"svg": chart_to_svg(spec)})

        return jsonify({"error": f"export format {fmt!r} not supported for kind {spec.kind!r}"}), 400

    @bp.route("/api/dashboards/<dashboard_id>/export/<fmt>", methods=["POST"])
    @_login_required
    def api_export_dashboard(dashboard_id, fmt):
        """Export a WHOLE dashboard (prem-rpt-01).

        The chart-level export above is per-spec and only ever handled ``kind ==
        "chart"``. Asking for the dashboard — the thing a customer actually reads — got
        a 400. You could build the report and never send it.
        """
        from tools.bi_dashboard.dashboard_store import get_dashboard
        from tools.bi_dashboard.export import export_dashboard, supported_formats

        dashboard = get_dashboard(dashboard_id, tenant_id=_current_tenant())
        if not dashboard or not _owns_or_admin(dashboard):
            return jsonify({"error": f"no dashboard {dashboard_id}"}), 404

        try:
            return jsonify(export_dashboard(dashboard, fmt))
        except ValueError as exc:
            return jsonify({
                "error": str(exc),
                "supported": supported_formats(),
            }), 400

    # ══════════════════════════════════════════════════════════════════════
    # API — IQE natural-language query widget
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/iqe-query", methods=["POST"])
    @_login_required
    def api_iqe_query():
        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        execute = data.get("execute", True)
        if not question:
            return jsonify({"error": "question is required"}), 400

        try:
            importlib.import_module("tools.iqe.adapters.bi_dashboard")
        except Exception:
            pass

        iqe_str = ""
        try:
            from tools.iqe.executor import execute_query
            from tools.iqe.nl_to_iqe import nl_to_iqe
            from tools.iqe.parser import parse as _parse

            collections = [
                "bi.uploaded_datasets", "bi.kg_timeseries", "bi.kg_coverage", "bi.sql_query",
            ]
            translation = nl_to_iqe(question, collections=collections)
            iqe_str = translation["iqe"]
            ast = _parse(iqe_str)
            results = execute_query(ast, None) if execute else []
            return jsonify({"iqe": iqe_str, "results": results, "row_count": len(results)})
        except Exception as exc:
            return jsonify({"error": str(exc), "iqe": iqe_str}), 500

    # ══════════════════════════════════════════════════════════════════════
    # API — misc
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/stats", methods=["GET"])
    def api_stats():
        return jsonify({
            "canvas": "bi_dashboard",
            "display_name": "BI Studio",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "classification": "CUI // SP-CTI",
        })

    return bp
