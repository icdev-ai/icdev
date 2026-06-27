from __future__ import annotations
# CUI // SP-CTI
"""Flask blueprint: IaC generation API for canvas designs.

Mounts at /infra (registered in tools/dashboard/app.py).
Route: POST /infra/api/design/<design_id>/generate-iac
"""

import json
from pathlib import Path

from flask import Blueprint, jsonify, request

try:
    from tools.infra.iac_generator import generate_terraform

    _HAS_IAC = True
except ImportError:
    _HAS_IAC = False

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

bp = Blueprint("infra_iac", __name__)


def _load_design_from_db(design_id: str) -> dict | None:
    """Load graph_json from infra_canvas.db by design ID."""
    try:
        from tools.db.storage import get_connection

        conn = get_connection(_DATA_DIR / "infra_canvas.db")
        row = conn.execute(
            "SELECT graph_json FROM infra_designs WHERE id = %s", (design_id,)
        ).fetchone()
        conn.close()
        if row:
            return json.loads(row[0] or "{}")
    except Exception:
        pass
    return None


@bp.route("/api/design/<design_id>/generate-iac", methods=["POST"])
def generate_iac(design_id: str):
    """Generate Terraform HCL from a canvas design.

    Request body (optional):
      {"design": {"nodes": [...], "edges": [...]}}

    Falls back to loading the design from infra_canvas.db when body is absent.
    """
    if not _HAS_IAC:
        return jsonify({"error": "IaC generator unavailable"}), 503

    body = request.get_json(silent=True) or {}
    design_json = body.get("design") or _load_design_from_db(design_id)

    if not design_json:
        return jsonify({"error": f"Design '{design_id}' not found"}), 404

    try:
        hcl = generate_terraform(design_json)
        return jsonify({"design_id": design_id, "hcl": hcl, "format": "terraform"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
