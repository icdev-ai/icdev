#!/usr/bin/env python3
# CUI // SP-CTI
"""FedRAMP 20x KSI Dashboard API (Phase 53, D338).

Provides REST endpoints for FedRAMP 20x KSI evidence generation,
summary, and authorization package status.
"""

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

# Add compliance tools to path for imports
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))

# Launched by path only this file's own directory is on sys.path, so the repo
# root must be inserted before the first-party imports below can resolve. The
# sys.path entry above adds a SIBLING package directory, not the root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection  # noqa: E402

DB_PATH = str(BASE_DIR / "data" / "icdev.db")

fedramp_20x_api = Blueprint("fedramp_20x_api", __name__, url_prefix="/api/fedramp-20x")


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


@fedramp_20x_api.route("/stats", methods=["GET"])
def fedramp_20x_stats():
    """Get FedRAMP 20x KSI summary statistics."""
    project_id = request.args.get("project_id", "")
    try:
        from fedramp_ksi_generator import generate_summary

        result = generate_summary(project_id, Path(DB_PATH))
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@fedramp_20x_api.route("/ksis", methods=["GET"])
def fedramp_20x_ksis():
    """Get all KSIs with maturity levels."""
    project_id = request.args.get("project_id", "")
    try:
        from fedramp_ksi_generator import generate_all_ksis

        result = generate_all_ksis(project_id, Path(DB_PATH))
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@fedramp_20x_api.route("/ksi/<ksi_id>", methods=["GET"])
def fedramp_20x_ksi_detail(ksi_id):
    """Get detail for a single KSI."""
    project_id = request.args.get("project_id", "")
    try:
        from fedramp_ksi_generator import generate_ksi

        result = generate_ksi(project_id, ksi_id, Path(DB_PATH))
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@fedramp_20x_api.route("/package", methods=["GET"])
def fedramp_20x_package():
    """Get authorization package readiness status."""
    project_id = request.args.get("project_id", "")
    try:
        from fedramp_authorization_packager import package_authorization

        result = package_authorization(project_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
