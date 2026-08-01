# CUI // SP-CTI
"""ICDEV™ Network Canvas — Ingestion API Routes.

REST API endpoints for file upload, config ingestion, NMS adapter
management, and ingestion audit log queries.

Registered on the NDC Flask blueprint via ``register_ingestion_routes(bp)``.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from tools.db.storage import get_connection
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = get_logger("icdev.network.routes.ingestion")

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
_UPLOAD_DIR = _ICDEV_ROOT / "data" / "ndc_uploads"


def register_ingestion_routes(bp: Blueprint) -> None:
    """Register ingestion API routes on the NDC blueprint."""

    # ── File upload (multipart/form-data) ────────────────────────────────

    @bp.route("/api/network/ingest", methods=["POST"])
    def nc_api_ingest_file():
        """Ingest a file via upload. Accepts multipart/form-data.

        Form fields:
            file: The file to ingest (required)
            project_id: Project context (default: 'default')
            topology_id: Link to topology (optional)
        """
        from tools.network.ingestion_pipeline import ingest_file

        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "Empty filename"}), 400

        project_id = request.form.get("project_id", "default")
        topology_id = request.form.get("topology_id") or None

        # Save to upload directory
        _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        file_path = _UPLOAD_DIR / f.filename
        f.save(str(file_path))

        result = ingest_file(
            str(file_path),
            channel="upload",
            project_id=project_id,
            topology_id=topology_id,
        )
        status_code = 200 if "error" not in result else 422
        return jsonify(result), status_code

    # ── Config text ingestion ────────────────────────────────────────────

    @bp.route("/api/network/ingest/config", methods=["POST"])
    def nc_api_ingest_config():
        """Ingest device config text directly.

        JSON body:
            config_text: Raw config (required)
            device_ip: Device IP (optional)
            hostname: Device hostname (optional)
            topology_id: Link to topology (optional)
        """
        from tools.network.config_parser import ingest_config

        data = request.get_json(silent=True) or {}
        config_text = data.get("config_text", "")
        if not config_text:
            return jsonify({"error": "config_text is required"}), 400

        result = ingest_config(
            config_text,
            device_ip=data.get("device_ip", ""),
            hostname=data.get("hostname", ""),
            topology_id=data.get("topology_id"),
            project_id=data.get("project_id", "default"),
        )
        return jsonify(result)

    # ── NMS adapter pull ─────────────────────────────────────────────────

    @bp.route("/api/network/ingest/nms", methods=["POST"])
    def nc_api_ingest_nms():
        """Trigger a pull from a configured NMS adapter.

        JSON body:
            adapter: Adapter name (required, e.g. 'netbox')
            url: NMS URL (required)
            token: Auth token (required)
            site: Filter by site (optional)
            pull_type: 'all' | 'devices' | 'configs' | 'topology' (default: 'all')
        """
        data = request.get_json(silent=True) or {}
        adapter_name = data.get("adapter", "")
        if not adapter_name:
            return jsonify({"error": "adapter name is required"}), 400

        try:
            from tools.network.nms_adapter import NMSAdapterRegistry
            import tools.network.adapters  # noqa: F401 — registers adapters

            kwargs = {}  # type: Dict[str, Any]
            for key in ("url", "token", "username", "password", "timeout", "verify_ssl"):
                if key in data:
                    kwargs[key] = data[key]

            adapter = NMSAdapterRegistry.get(adapter_name, **kwargs)
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 400

        pull_type = data.get("pull_type", "all")
        site = data.get("site")

        try:
            if pull_type == "all":
                result = adapter.pull_all(site=site)
            elif pull_type == "devices":
                result = {"devices": adapter.pull_devices(site=site)}
            elif pull_type == "configs":
                result = {"configs": adapter.pull_configs()}
            elif pull_type == "topology":
                result = {"topology": adapter.pull_topology()}
            else:
                return jsonify({"error": "Unknown pull_type: %s" % pull_type}), 400
        except NotImplementedError as exc:
            return jsonify({"error": str(exc)}), 501
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        return jsonify({"adapter": adapter_name, "pull_type": pull_type, "result": result})

    # ── NMS adapter listing ──────────────────────────────────────────────

    @bp.route("/api/network/adapters", methods=["GET"])
    def nc_api_list_adapters():
        """List all registered NMS adapters."""
        try:
            from tools.network.nms_adapter import NMSAdapterRegistry
            import tools.network.adapters  # noqa: F401

            return jsonify({"adapters": NMSAdapterRegistry.list_adapters()})
        except ImportError:
            return jsonify({"adapters": []})

    @bp.route("/api/network/adapters/test", methods=["POST"])
    def nc_api_test_adapter():
        """Test NMS adapter connectivity.

        JSON body:
            adapter: Adapter name (required)
            url: NMS URL (required)
            token: Auth token (required)
        """
        data = request.get_json(silent=True) or {}
        adapter_name = data.get("adapter", "")
        if not adapter_name:
            return jsonify({"error": "adapter name is required"}), 400

        try:
            from tools.network.nms_adapter import NMSAdapterRegistry
            import tools.network.adapters  # noqa: F401

            kwargs = {}  # type: Dict[str, Any]
            for key in ("url", "token", "username", "password"):
                if key in data:
                    kwargs[key] = data[key]

            adapter = NMSAdapterRegistry.get(adapter_name, **kwargs)
            result = adapter.test_connection()
            return jsonify({"adapter": adapter_name, "test": result})
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 400
        except NotImplementedError as exc:
            return jsonify({"error": str(exc)}), 501
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Ingestion log ────────────────────────────────────────────────────

    @bp.route("/api/network/ingest/log", methods=["GET"])
    def nc_api_ingestion_log():
        """Query ingestion audit log.

        Query params:
            limit: Max entries (default: 50)
            channel: Filter by channel
            status: Filter by status
        """

        limit = request.args.get("limit", "50", type=str)
        channel = request.args.get("channel", "")
        status_filter = request.args.get("status", "")

        db_path = str(_ICDEV_ROOT / "data" / "network_canvas.db")
        try:
            conn = get_connection(str(db_path))

            sql = "SELECT * FROM nc_ingestion_log"
            params = []  # type: list
            wheres = []  # type: list
            if channel:
                wheres.append("channel=%s")
                params.append(channel)
            if status_filter:
                wheres.append("status=%s")
                params.append(status_filter)
            if wheres:
                sql += " WHERE " + " AND ".join(wheres)
            sql += " ORDER BY created_at DESC LIMIT %s"
            params.append(int(limit))

            rows = conn.execute(sql, params).fetchall()
            conn.close()

            entries = [dict(r) for r in rows]
            return jsonify({"entries": entries, "count": len(entries)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Ingestion status ─────────────────────────────────────────────────

    @bp.route("/api/network/ingest/status", methods=["GET"])
    def nc_api_ingestion_status():
        """Summary of ingestion activity."""

        db_path = str(_ICDEV_ROOT / "data" / "network_canvas.db")
        try:
            conn = get_connection(str(db_path))

            total = conn.execute("SELECT COUNT(*) as c FROM nc_ingestion_log").fetchone()["c"]
            by_status = conn.execute(
                "SELECT status, COUNT(*) as c FROM nc_ingestion_log GROUP BY status"
            ).fetchall()
            by_channel = conn.execute(
                "SELECT channel, COUNT(*) as c FROM nc_ingestion_log GROUP BY channel"
            ).fetchall()
            by_type = conn.execute(
                "SELECT file_type, COUNT(*) as c FROM nc_ingestion_log GROUP BY file_type"
            ).fetchall()
            docs = conn.execute("SELECT COUNT(*) as c FROM nc_documents").fetchone()["c"]
            conn.close()

            return jsonify({
                "total_ingestions": total,
                "documents_stored": docs,
                "by_status": {r["status"]: r["c"] for r in by_status},
                "by_channel": {r["channel"]: r["c"] for r in by_channel},
                "by_type": {(r["file_type"] or "unknown"): r["c"] for r in by_type},
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
