#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: ATO Package Builder.

An HTTP surface only. Every readiness number, control rollup, POA&M count and
checklist verdict is computed by ``tools.compliance.ato_packager`` — the same
module that assembles the ZIP — so a generated package can never describe a
readiness this API disagrees with (rmf-inert-01).
"""

from flask import Blueprint, jsonify, request

from tools.compliance.ato_packager import (
    collect_checklist,
    collect_controls_summary,
    collect_poam_summary,
    collect_readiness,
    collect_ssp_documents,
    generate_package,
    open_connection,
)

ato_package_api = Blueprint("ato_package_api", __name__, url_prefix="/api/ato-package")

__all__ = ["ato_package_api"]


def _get_db():
    """Return a connection to the compliance database."""
    return open_connection()


def _project_id():
    return request.args.get("project_id")


def _served(collector):
    """Run a collector against a fresh connection and shape the HTTP reply."""
    conn = _get_db()
    try:
        return jsonify(collector(conn, _project_id()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /api/ato-package/status — Package readiness for a project
# ---------------------------------------------------------------------------
@ato_package_api.route("/status", methods=["GET"])
def package_status():
    """Check ATO package readiness across all steps."""
    return _served(collect_readiness)


# ---------------------------------------------------------------------------
# GET /api/ato-package/ssp — SSP documents for a project
# ---------------------------------------------------------------------------
@ato_package_api.route("/ssp", methods=["GET"])
def ssp_documents():
    """Return SSP documents for a project."""
    return _served(collect_ssp_documents)


# ---------------------------------------------------------------------------
# GET /api/ato-package/controls-summary — Control implementation summary
# ---------------------------------------------------------------------------
@ato_package_api.route("/controls-summary", methods=["GET"])
def controls_summary():
    """Return control implementation summary grouped by family."""
    return _served(collect_controls_summary)


# ---------------------------------------------------------------------------
# GET /api/ato-package/poam-summary — POAM summary
# ---------------------------------------------------------------------------
@ato_package_api.route("/poam-summary", methods=["GET"])
def poam_summary():
    """Return POAM summary with severity/status counts and overdue items."""
    return _served(collect_poam_summary)


# ---------------------------------------------------------------------------
# GET /api/ato-package/checklist — Pre-submission checklist
# ---------------------------------------------------------------------------
@ato_package_api.route("/checklist", methods=["GET"])
def pre_submission_checklist():
    """Verify pre-submission requirements for ATO package."""
    return _served(collect_checklist)


# ---------------------------------------------------------------------------
# POST /api/ato-package/generate — Generate ATO package
# ---------------------------------------------------------------------------
@ato_package_api.route("/generate", methods=["POST"])
def generate_ato_package():
    """Generate an ATO package for a project.

    Answered 501 until rmf-inert-01: the generator it imported did not exist.
    """
    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id")
    package_type = data.get("package_type", "initial")

    if not project_id:
        return jsonify({"error": "project_id is required"}), 400

    try:
        result = generate_package(project_id=project_id, package_type=package_type)
    except ValueError as e:
        # Invalid package_type, or a missing project id that slipped the check.
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify(
        {
            "status": "success",
            "project_id": project_id,
            "package_type": package_type,
            "result": result,
        }
    )
