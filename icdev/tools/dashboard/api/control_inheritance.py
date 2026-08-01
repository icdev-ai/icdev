#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Control Inheritance Visualizer."""

import sys
from pathlib import Path
from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists, sql_placeholder  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"

control_inheritance_api = Blueprint("control_inheritance_api", __name__, url_prefix="/api/control-inheritance")

# nav-comp-06: the responsibility split (inherited / shared / customer) below is a
# hardcoded FedRAMP *reference* model, not a measured posture for any specific
# system or authorization boundary. Every response is tagged with this so the UI
# can badge it "Typical inheritance (reference)" and no consumer mistakes the
# reference split for an assessed control-responsibility assignment.
DATA_SOURCE = "reference_model"

# ---------------------------------------------------------------------------
# Static inheritance model — FedRAMP typical responsibility by control family
# ---------------------------------------------------------------------------
INHERITANCE_MODEL = {
    "PE": {"default": "inherited", "label": "Physical & Environmental"},
    "MP": {"default": "inherited", "label": "Media Protection (physical)"},
    "PS": {"default": "shared", "label": "Personnel Security"},
    "AC": {"default": "customer", "label": "Access Control"},
    "AU": {"default": "shared", "label": "Audit & Accountability"},
    "AT": {"default": "customer", "label": "Awareness & Training"},
    "CM": {"default": "shared", "label": "Configuration Management"},
    "CP": {"default": "shared", "label": "Contingency Planning"},
    "IA": {"default": "shared", "label": "Identification & Authentication"},
    "IR": {"default": "shared", "label": "Incident Response"},
    "MA": {"default": "shared", "label": "Maintenance"},
    "PL": {"default": "customer", "label": "Planning"},
    "PM": {"default": "customer", "label": "Program Management"},
    "RA": {"default": "shared", "label": "Risk Assessment"},
    "SA": {"default": "shared", "label": "System & Services Acquisition"},
    "SC": {"default": "shared", "label": "System & Communications Protection"},
    "SI": {"default": "shared", "label": "System & Information Integrity"},
    "SR": {"default": "shared", "label": "Supply Chain Risk Management"},
}

CSP_PROFILES = {
    "aws_govcloud": {
        "name": "AWS GovCloud",
        "overrides": {"AU": "shared", "SC": "shared"},
    },
    "azure_government": {
        "name": "Azure Government",
        "overrides": {"IA": "shared"},
    },
    "gcp": {"name": "Google Cloud", "overrides": {}},
    "oci": {"name": "Oracle Cloud", "overrides": {}},
    "ibm": {"name": "IBM Cloud", "overrides": {}},
    "on_premise": {
        "name": "On-Premise",
        "overrides": {f: "customer" for f in ["PE", "MP", "PS"]},
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_db():
    """Return a sqlite3 connection with Row factory."""
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists (works for both SQLite and PostgreSQL)."""
    return table_exists(conn, table_name)


def _resolve_responsibility(family: str, csp: str) -> str:
    """Return the effective responsibility for *family* under *csp*."""
    base = INHERITANCE_MODEL.get(family, {}).get("default", "customer")
    profile = CSP_PROFILES.get(csp, {})
    return profile.get("overrides", {}).get(family, base)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@control_inheritance_api.route("/csps", methods=["GET"])
def list_csps():
    """List available CSP profiles."""
    profiles = []
    for key, val in CSP_PROFILES.items():
        profiles.append({"id": key, "name": val["name"]})
    return jsonify({"csps": profiles, "data_source": DATA_SOURCE})


@control_inheritance_api.route("/model", methods=["GET"])
def get_model():
    """Return the full inheritance model for a given CSP.

    Query params:
        csp (str): CSP profile key (default: aws_govcloud)
    """
    csp = request.args.get("csp", "aws_govcloud")

    conn = _get_db()
    try:
        has_controls = _table_exists(conn, "compliance_controls")

        families = []
        for code, info in INHERITANCE_MODEL.items():
            responsibility = _resolve_responsibility(code, csp)
            control_count = 0
            if has_controls:
                row = conn.execute(
                    "SELECT COUNT(*) FROM compliance_controls WHERE family = %s",
                    (code,),
                ).fetchone()
                control_count = row[0] if row else 0
            families.append(
                {
                    "family": code,
                    "label": info["label"],
                    "responsibility": responsibility,
                    "control_count": control_count,
                }
            )

        return jsonify(
            {
                "csp": csp,
                "csp_name": CSP_PROFILES.get(csp, {}).get("name", csp),
                "families": families,
                "data_source": DATA_SOURCE,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@control_inheritance_api.route("/summary", methods=["GET"])
def get_summary():
    """Summary counts of controls by responsibility category.

    Query params:
        project_id (str): optional project filter
        csp (str): CSP profile key (default: aws_govcloud)
    """
    csp = request.args.get("csp", "aws_govcloud")
    project_id = request.args.get("project_id")

    conn = _get_db()
    try:
        has_controls = _table_exists(conn, "compliance_controls")
        has_project = _table_exists(conn, "project_controls")

        counts = {"inherited": 0, "shared": 0, "customer": 0}
        implementation = {
            "inherited": {"implemented": 0, "total": 0},
            "shared": {"implemented": 0, "total": 0},
            "customer": {"implemented": 0, "total": 0},
        }

        if has_controls:
            rows = conn.execute("SELECT id, family FROM compliance_controls").fetchall()
            for row in rows:
                resp = _resolve_responsibility(row["family"], csp)
                counts[resp] = counts.get(resp, 0) + 1

                if has_project and project_id:
                    pc = conn.execute(
                        "SELECT implementation_status FROM project_controls WHERE project_id = %s AND control_id = %s",
                        (project_id, row["id"]),
                    ).fetchone()
                    if pc:
                        implementation[resp]["total"] += 1
                        if pc["implementation_status"] == "implemented":
                            implementation[resp]["implemented"] += 1
                    else:
                        implementation[resp]["total"] += 1
                else:
                    implementation[resp]["total"] += 1
        else:
            # No DB table — return counts from static model
            for code, info in INHERITANCE_MODEL.items():
                resp = _resolve_responsibility(code, csp)
                counts[resp] = counts.get(resp, 0) + 1

        total = sum(counts.values())
        summary = {}
        for cat in ("inherited", "shared", "customer"):
            summary[cat] = {
                "count": counts[cat],
                "percent": round(counts[cat] / total * 100, 1) if total else 0,
                "implemented": implementation[cat]["implemented"],
                "implementation_total": implementation[cat]["total"],
            }

        return jsonify(
            {
                "csp": csp,
                "project_id": project_id,
                "total_controls": total,
                "summary": summary,
                "data_source": DATA_SOURCE,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@control_inheritance_api.route("/controls", methods=["GET"])
def list_controls():
    """Detailed control list with inheritance overlay.

    Query params:
        project_id (str): optional
        csp (str): CSP profile (default: aws_govcloud)
        responsibility (str): filter — inherited|shared|customer
        family (str): filter — control family code
    """
    csp = request.args.get("csp", "aws_govcloud")
    project_id = request.args.get("project_id")
    resp_filter = request.args.get("responsibility")
    family_filter = request.args.get("family")

    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        has_controls = _table_exists(conn, "compliance_controls")
        has_project = _table_exists(conn, "project_controls")

        if not has_controls:
            return jsonify({"controls": [], "total": 0, "data_source": DATA_SOURCE})

        query = "SELECT id, family, title, description, impact_level FROM compliance_controls WHERE 1=1"
        params = []

        if family_filter:
            query += f" AND family = {ph}"
            params.append(family_filter)

        query += " ORDER BY family, id"
        rows = conn.execute(query, params).fetchall()

        controls = []
        for row in rows:
            resp = _resolve_responsibility(row["family"], csp)
            if resp_filter and resp != resp_filter:
                continue

            entry = {
                "control_id": row["id"],
                "family": row["family"],
                "title": row["title"],
                "description": row["description"],
                "impact_level": row["impact_level"],
                "responsibility": resp,
                "implementation_status": None,
                "responsible_role": None,
            }

            if has_project and project_id:
                pc = conn.execute(
                    "SELECT implementation_status, responsible_role "
                    "FROM project_controls "
                    "WHERE project_id = %s AND control_id = %s",
                    (project_id, row["id"]),
                ).fetchone()
                if pc:
                    entry["implementation_status"] = pc["implementation_status"]
                    entry["responsible_role"] = pc["responsible_role"]

            controls.append(entry)

        return jsonify({"controls": controls, "total": len(controls), "data_source": DATA_SOURCE})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@control_inheritance_api.route("/gap", methods=["GET"])
def gap_analysis():
    """Gap analysis: customer-responsible controls not yet implemented.

    Query params:
        project_id (str): required
        csp (str): CSP profile (default: aws_govcloud)
    """
    csp = request.args.get("csp", "aws_govcloud")
    project_id = request.args.get("project_id")

    if not project_id:
        return jsonify({"error": "project_id is required"}), 400

    conn = _get_db()
    try:
        has_controls = _table_exists(conn, "compliance_controls")
        has_project = _table_exists(conn, "project_controls")

        if not has_controls:
            return jsonify({"gaps": [], "total": 0, "data_source": DATA_SOURCE})

        rows = conn.execute(
            "SELECT id, family, title, description, impact_level FROM compliance_controls ORDER BY family, id"
        ).fetchall()

        gaps = []
        for row in rows:
            resp = _resolve_responsibility(row["family"], csp)
            # Only customer and shared controls are actionable gaps
            if resp == "inherited":
                continue

            status = None
            role = None
            if has_project:
                pc = conn.execute(
                    "SELECT implementation_status, responsible_role "
                    "FROM project_controls "
                    "WHERE project_id = %s AND control_id = %s",
                    (project_id, row["id"]),
                ).fetchone()
                if pc:
                    status = pc["implementation_status"]
                    role = pc["responsible_role"]

            # Gap = not implemented (missing, planned, or partially_implemented)
            if status in (None, "planned", "partially_implemented"):
                priority = "high" if resp == "customer" else "medium"
                gaps.append(
                    {
                        "control_id": row["id"],
                        "family": row["family"],
                        "title": row["title"],
                        "responsibility": resp,
                        "implementation_status": status or "not_started",
                        "responsible_role": role,
                        "priority": priority,
                    }
                )

        return jsonify(
            {
                "project_id": project_id,
                "csp": csp,
                "gaps": gaps,
                "total": len(gaps),
                "data_source": DATA_SOURCE,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()
