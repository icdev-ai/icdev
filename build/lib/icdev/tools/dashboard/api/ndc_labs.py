# CUI // SP-CTI
"""NDC Lab Backend Health API.

Exposes the lab_health probe results at /api/ndc/labs/health and
/api/ndc/labs/<name>/health for the /network/labs dashboard page.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger


from flask import Blueprint, jsonify

from tools.network.lab_health import load_backends, probe_all, probe_backend

logger = get_logger("icdev.dashboard.ndc_labs")

ndc_labs_api = Blueprint("ndc_labs_api", __name__, url_prefix="/api/ndc/labs")


@ndc_labs_api.route("/health", methods=["GET"])
def health():
    """Return all backend health statuses."""
    try:
        statuses = probe_all()
    except Exception as exc:  # noqa: BLE001
        logger.exception("probe_all failed")
        return jsonify({"ok": False, "error": str(exc), "backends": []}), 500

    summary = {
        "green": sum(1 for b in statuses if b.get("status") == "green"),
        "yellow": sum(1 for b in statuses if b.get("status") == "yellow"),
        "red": sum(1 for b in statuses if b.get("status") == "red"),
    }
    return jsonify({"ok": True, "summary": summary, "backends": statuses})


@ndc_labs_api.route("/<backend_name>/health", methods=["GET"])
def backend_health(backend_name: str):
    """Return health for a single backend."""
    match = next(
        (b for b in load_backends() if b.get("name") == backend_name),
        None,
    )
    if not match:
        return jsonify({"ok": False, "error": f"unknown backend '{backend_name}'"}), 404
    try:
        status = probe_backend(match)
    except Exception as exc:  # noqa: BLE001
        logger.exception("probe_backend %s failed", backend_name)
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "backend": status})
