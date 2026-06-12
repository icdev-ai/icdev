# CUI // SP-CTI
"""Dashboard API: ATO Compliance Dashboard.

Provides REST endpoints for control tracking views, RMF workflow stages,
artifact generation status, and NIST 800-53 crosswalk data.

NIST 800-53 Controls: SA-11, CM-3, CA-7, RA-5
"""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Blueprint, jsonify

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.ato_compliance.dashboard import (  # noqa: E402
    get_artifact_status,
    get_control_summary,
    get_crosswalk_summary,
    get_posture_score,
    get_rmf_stages,
)

DB_PATH = BASE_DIR / "data" / "icdev.db"

ato_compliance_api = Blueprint(
    "ato_compliance_api",
    __name__,
    url_prefix="/api/ato-compliance",
)


def _get_db():
    """Return a connection to icdev.db."""
    conn = get_connection(db_path=str(DB_PATH))
    return conn


# ---------------------------------------------------------------------------
# GET /api/ato-compliance/dashboard/<project_id>
# ---------------------------------------------------------------------------

@ato_compliance_api.route("/dashboard/<project_id>", methods=["GET"])
def ato_dashboard(project_id: str):
    """Aggregate ATO posture: score, controls, RMF stages, artifacts, crosswalk."""
    conn = _get_db()
    try:
        posture = get_posture_score(project_id, conn=conn)
        controls = get_control_summary(project_id, conn=conn)
        stages = get_rmf_stages(project_id, conn=conn)
        artifacts = get_artifact_status(project_id, conn=conn)
        crosswalk = get_crosswalk_summary(project_id, conn=conn)

        return jsonify(
            {
                "project_id": project_id,
                "posture_score": posture,
                "controls": controls,
                "rmf_stages": stages,
                "artifacts": artifacts,
                "crosswalk": crosswalk,
            }
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# GET /api/ato-compliance/controls/<project_id>
# ---------------------------------------------------------------------------

@ato_compliance_api.route("/controls/<project_id>", methods=["GET"])
def controls_view(project_id: str):
    """NIST 800-53 control implementation status per family."""
    conn = _get_db()
    try:
        result = get_control_summary(project_id, conn=conn)
        return jsonify(result)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# GET /api/ato-compliance/rmf/<project_id>
# ---------------------------------------------------------------------------

@ato_compliance_api.route("/rmf/<project_id>", methods=["GET"])
def rmf_stages_view(project_id: str):
    """RMF workflow stage lifecycle (categorize → select → ... → monitor)."""
    conn = _get_db()
    try:
        stages = get_rmf_stages(project_id, conn=conn)
        return jsonify({"project_id": project_id, "stages": stages})
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# GET /api/ato-compliance/artifacts/<project_id>
# ---------------------------------------------------------------------------

@ato_compliance_api.route("/artifacts/<project_id>", methods=["GET"])
def artifacts_view(project_id: str):
    """ATO artifact readiness: SSP, POAM, STIG findings, SBOM."""
    conn = _get_db()
    try:
        result = get_artifact_status(project_id, conn=conn)
        return jsonify(result)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# GET /api/ato-compliance/crosswalk/<project_id>
# ---------------------------------------------------------------------------

@ato_compliance_api.route("/crosswalk/<project_id>", methods=["GET"])
def crosswalk_view(project_id: str):
    """NIST 800-53 crosswalk coverage per framework (FedRAMP, CMMC, IL4, etc.)."""
    conn = _get_db()
    try:
        result = get_crosswalk_summary(project_id, conn=conn)
        return jsonify(result)
    finally:
        try:
            conn.close()
        except Exception:
            pass
