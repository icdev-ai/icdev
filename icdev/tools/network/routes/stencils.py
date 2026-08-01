# CUI // SP-CTI
"""NDC Stencil Library Routes.

REST API + page route for the Visio stencil import system.
Registered on the NDC blueprint via register_stencil_routes(bp).

Endpoints:
  GET  /stencils                        — stencil manager page
  GET  /api/stencils/vendors            — list supported vendors
  GET  /api/stencils/catalog/<vendor>   — available stencils from vendor catalog
  POST /api/stencils/import-url         — download + import stencil from URL
  POST /api/stencils/upload             — file upload + import
  GET  /api/stencils/libraries          — list imported libraries
  DELETE /api/stencils/libraries/<id>   — delete a library + its shapes
  GET  /api/stencils/shapes             — list shapes (filters: vendor, library_id, q)
  GET  /api/stencils/shapes/<id>/icon   — serve shape icon (PNG/SVG)
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import base64
import io
from pathlib import Path

from flask import Blueprint, jsonify, request, render_template, send_file

logger = get_logger("icdev.network.routes.stencils")

_ICDEV_ROOT = Path(__file__).resolve().parents[3]


def register_stencil_routes(bp: Blueprint) -> None:
    """Register all stencil API and page routes on the NDC blueprint."""

    def _get_conn():
        from tools.network.db.init_db import get_connection
        return get_connection()

    # ── Page ─────────────────────────────────────────────────────────────────

    @bp.route("/stencils")
    def nc_stencils_page():
        from tools.network.stencil_catalog import get_vendor_list
        vendors = get_vendor_list()
        return render_template("network/stencils.html", vendors=vendors)

    # ── Vendor catalog ────────────────────────────────────────────────────────

    @bp.route("/api/stencils/vendors", methods=["GET"])
    def nc_api_stencil_vendors():
        from tools.network.stencil_catalog import get_vendor_list
        return jsonify({"vendors": get_vendor_list()})

    @bp.route("/api/stencils/catalog/<vendor>", methods=["GET"])
    def nc_api_stencil_catalog(vendor: str):
        from tools.network.stencil_catalog import get_catalog, VENDOR_META
        if vendor not in VENDOR_META and vendor != "custom":
            return jsonify({"error": "Unknown vendor"}), 400
        items = get_catalog(vendor)
        return jsonify({"vendor": vendor, "items": items, "count": len(items)})

    # ── Import by URL ─────────────────────────────────────────────────────────

    @bp.route("/api/stencils/import-url", methods=["POST"])
    def nc_api_stencil_import_url():
        from tools.network.stencil_importer import import_from_url
        body = request.get_json(force=True, silent=True) or {}
        url = (body.get("url") or "").strip()
        vendor = (body.get("vendor") or "custom").strip().lower()
        lib_name = (body.get("name") or Path(url).stem or "Imported Stencil").strip()
        category = (body.get("category") or "").strip()

        if not url:
            return jsonify({"error": "url is required"}), 400

        try:
            result = import_from_url(url, vendor, lib_name, category)
            return jsonify({"ok": True, **result})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            logger.exception("Stencil URL import failed")
            return jsonify({"error": f"Import failed: {exc}"}), 500

    # ── File upload ───────────────────────────────────────────────────────────

    @bp.route("/api/stencils/upload", methods=["POST"])
    def nc_api_stencil_upload():
        from tools.network.stencil_importer import import_from_bytes
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"error": "No file provided"}), 400

        filename = Path(f.filename).name
        allowed_exts = {".vssx", ".vsdx", ".vss", ".zip"}
        if Path(filename).suffix.lower() not in allowed_exts:
            return jsonify({"error": f"Unsupported file type. Allowed: {', '.join(allowed_exts)}"}), 400

        vendor = (request.form.get("vendor") or "custom").strip().lower()
        lib_name = (request.form.get("name") or Path(filename).stem).strip()
        category = (request.form.get("category") or "").strip()

        raw = f.read()
        if len(raw) > 256 * 1024 * 1024:  # 256 MB limit
            return jsonify({"error": "File too large (max 256 MB)"}), 413

        try:
            result = import_from_bytes(raw, filename, vendor, lib_name, category)
            return jsonify({"ok": True, **result})
        except Exception as exc:
            logger.exception("Stencil upload import failed")
            return jsonify({"error": f"Import failed: {exc}"}), 500

    # ── Libraries CRUD ────────────────────────────────────────────────────────

    @bp.route("/api/stencils/libraries", methods=["GET"])
    def nc_api_stencil_libraries():
        conn = _get_conn()
        try:
            vendor_filter = request.args.get("vendor", "")
            if vendor_filter:
                rows = conn.execute(
                    "SELECT * FROM nc_stencil_libraries WHERE vendor=%s ORDER BY imported_at DESC",
                    (vendor_filter,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM nc_stencil_libraries ORDER BY imported_at DESC"
                ).fetchall()
            libs = [dict(r) for r in rows]
            return jsonify({"libraries": libs, "count": len(libs)})
        finally:
            conn.close()

    @bp.route("/api/stencils/libraries/<lib_id>", methods=["DELETE"])
    def nc_api_stencil_library_delete(lib_id: str):
        conn = _get_conn()
        try:
            lib = conn.execute(
                "SELECT id FROM nc_stencil_libraries WHERE id=%s", (lib_id,)
            ).fetchone()
            if not lib:
                return jsonify({"error": "Library not found"}), 404
            conn.execute("DELETE FROM nc_stencil_shapes WHERE library_id=%s", (lib_id,))
            conn.execute("DELETE FROM nc_stencil_libraries WHERE id=%s", (lib_id,))
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    # ── Shapes ────────────────────────────────────────────────────────────────

    @bp.route("/api/stencils/shapes", methods=["GET"])
    def nc_api_stencil_shapes():
        conn = _get_conn()
        try:
            vendor = request.args.get("vendor", "")
            lib_id = request.args.get("library_id", "")
            q = request.args.get("q", "").strip()
            limit = min(int(request.args.get("limit", 100)), 500)
            offset = int(request.args.get("offset", 0))

            where_clauses = []
            params: list = []

            if vendor:
                where_clauses.append(
                    "s.library_id IN (SELECT id FROM nc_stencil_libraries WHERE vendor=%s)"
                )
                params.append(vendor)
            if lib_id:
                where_clauses.append("s.library_id=%s")
                params.append(lib_id)
            if q:
                where_clauses.append("(s.name LIKE %s OR s.category LIKE %s)")
                params.extend([f"%{q}%", f"%{q}%"])

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            rows = conn.execute(
                f"SELECT s.id, s.library_id, s.name, s.name_u, s.category, "
                f"s.icon_type, l.vendor, l.name AS library_name "
                f"FROM nc_stencil_shapes s "
                f"JOIN nc_stencil_libraries l ON l.id=s.library_id "
                f"{where_sql} "
                f"ORDER BY l.vendor, s.category, s.name "
                f"LIMIT %s OFFSET %s",
                params + [limit, offset],
            ).fetchall()
            shapes = [dict(r) for r in rows]
            return jsonify({"shapes": shapes, "count": len(shapes), "offset": offset})
        finally:
            conn.close()

    @bp.route("/api/stencils/shapes/<shape_id>/icon", methods=["GET"])
    def nc_api_stencil_shape_icon(shape_id: str):
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT icon_data, icon_type FROM nc_stencil_shapes WHERE id=%s",
                (shape_id,),
            ).fetchone()
        finally:
            conn.close()

        if not row or not row["icon_data"]:
            return jsonify({"error": "Icon not found"}), 404

        try:
            raw = base64.b64decode(row["icon_data"])
        except Exception:
            return jsonify({"error": "Invalid icon data"}), 500

        icon_type = row["icon_type"] or "png"
        mime = "image/svg+xml" if icon_type == "svg" else "image/png"
        return send_file(
            io.BytesIO(raw),
            mimetype=mime,
            max_age=86400,
        )
