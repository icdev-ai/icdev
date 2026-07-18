#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Infrastructure as Code Gallery."""

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists, sql_placeholder  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"

iac_api = Blueprint("iac_api", __name__, url_prefix="/api/iac")

IAC_ARTIFACT_TYPES = (
    "deployment_manifest",
    "schema_ddl",
    "scaffold_code",
    "rollback_script",
)

SUPPORTED_CSPS = [
    "aws_govcloud",
    "azure_government",
    "gcp",
    "oci",
    "ibm",
    "local",
]

SUPPORTED_IMPACT_LEVELS = ["IL2", "IL4", "IL5", "IL6"]

SUPPORTED_TEMPLATE_TYPES = ["terraform", "ansible", "k8s"]


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _table_exists(conn, name):
    """Check if a table exists (works for both SQLite and PostgreSQL)."""
    return table_exists(conn, name)


@iac_api.route("/stats", methods=["GET"])
def iac_stats():
    """GET /api/iac/stats -- Aggregate IaC statistics."""
    conn = _get_db()
    try:
        stats = {
            "total_artifacts": 0,
            "artifacts_by_type": {},
            "stig_coverage": {
                "total_findings": 0,
                "hardened": 0,
                "hardening_pct": 0.0,
            },
            "csp_distribution": {},
        }

        # -- Artifact counts from migration_artifacts --
        if _table_exists(conn, "migration_artifacts"):
            placeholders = ",".join("?" for _ in IAC_ARTIFACT_TYPES)
            row = conn.execute(
                f"SELECT COUNT(*) FROM migration_artifacts "  # nosec B608 -- table/column names are internal constants, not user input
                f"WHERE artifact_type IN ({placeholders})",
                IAC_ARTIFACT_TYPES,
            ).fetchone()
            stats["total_artifacts"] = row[0]

            # Breakdown by type
            rows = conn.execute(
                f"SELECT artifact_type, COUNT(*) FROM migration_artifacts "  # nosec B608 -- table/column names are internal constants, not user input
                f"WHERE artifact_type IN ({placeholders}) "
                f"GROUP BY artifact_type",
                IAC_ARTIFACT_TYPES,
            ).fetchall()
            stats["artifacts_by_type"] = {r[0]: r[1] for r in rows}

            # CSP distribution — parse from description or file_path
            all_artifacts = conn.execute(
                f"SELECT COALESCE(description, '') || ' ' || COALESCE(file_path, '') "  # nosec B608 -- table/column names are internal constants, not user input
                f"FROM migration_artifacts "
                f"WHERE artifact_type IN ({placeholders})",
                IAC_ARTIFACT_TYPES,
            ).fetchall()
            csp_counts = {}
            for (text,) in all_artifacts:
                text_lower = text.lower()
                for csp in SUPPORTED_CSPS:
                    if csp.replace("_", " ") in text_lower or csp in text_lower:
                        csp_counts[csp] = csp_counts.get(csp, 0) + 1
                        break
                else:
                    csp_counts["unspecified"] = csp_counts.get("unspecified", 0) + 1
            stats["csp_distribution"] = csp_counts

        # -- STIG coverage --
        if _table_exists(conn, "stig_findings"):
            row = conn.execute("SELECT COUNT(*) FROM stig_findings").fetchone()
            total = row[0]
            hardened_row = conn.execute(
                "SELECT COUNT(*) FROM stig_findings WHERE status = %s",
                ("NotAFinding",),
            ).fetchone()
            hardened = hardened_row[0]
            stats["stig_coverage"] = {
                "total_findings": total,
                "hardened": hardened,
                "hardening_pct": round((hardened / total * 100) if total > 0 else 0.0, 1),
            }

        return jsonify(stats)
    finally:
        conn.close()


@iac_api.route("/artifacts", methods=["GET"])
def list_artifacts():
    """GET /api/iac/artifacts -- List IaC artifacts with optional filters."""
    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        if not _table_exists(conn, "migration_artifacts"):
            return jsonify({"artifacts": [], "total": 0, "page": 1, "per_page": 25})

        plan_id = request.args.get("plan_id")
        artifact_type = request.args.get("artifact_type")
        page = max(int(request.args.get("page", "1")), 1)
        per_page = min(max(int(request.args.get("per_page", "25")), 1), 100)

        placeholders = ",".join(ph for _ in IAC_ARTIFACT_TYPES)
        base_where = f"artifact_type IN ({placeholders})"
        params = list(IAC_ARTIFACT_TYPES)

        if plan_id:
            base_where += f" AND plan_id = {ph}"
            params.append(plan_id)

        if artifact_type and artifact_type in IAC_ARTIFACT_TYPES:
            base_where += f" AND artifact_type = {ph}"
            params.append(artifact_type)

        # Total count
        count_row = conn.execute(
            f"SELECT COUNT(*) FROM migration_artifacts WHERE {base_where}",  # nosec B608 -- table/column names are internal constants, not user input
            params,
        ).fetchone()
        total = count_row[0]

        # Paginated results
        offset = (page - 1) * per_page
        query_params = params + [per_page, offset]
        rows = conn.execute(
            f"SELECT id, plan_id, artifact_type, file_path, description, "  # nosec B608 -- table/column names are internal constants, not user input
            f"created_at FROM migration_artifacts "
            f"WHERE {base_where} ORDER BY created_at DESC "
            f"LIMIT %s OFFSET %s",
            query_params,
        ).fetchall()

        artifacts = []
        for r in rows:
            artifacts.append(
                {
                    "id": r[0],
                    "plan_id": r[1],
                    "artifact_type": r[2],
                    "file_path": r[3],
                    "description": r[4],
                    "created_at": r[5],
                }
            )

        return jsonify(
            {
                "artifacts": artifacts,
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        )
    finally:
        conn.close()


@iac_api.route("/artifacts/<int:artifact_id>", methods=["GET"])
def artifact_detail(artifact_id):
    """GET /api/iac/artifacts/<artifact_id> -- Artifact detail with file content."""
    conn = _get_db()
    try:
        if not _table_exists(conn, "migration_artifacts"):
            return jsonify({"error": "migration_artifacts table not found"}), 404

        row = conn.execute(
            "SELECT id, plan_id, artifact_type, file_path, description, "
            "created_at FROM migration_artifacts WHERE id = %s",
            (artifact_id,),
        ).fetchone()

        if not row:
            return jsonify({"error": "Artifact not found"}), 404

        artifact = {
            "id": row[0],
            "plan_id": row[1],
            "artifact_type": row[2],
            "file_path": row[3],
            "description": row[4],
            "created_at": row[5],
            "file_content": None,
        }

        # Try to read file content if file_path exists
        if row[3]:
            file_path = Path(row[3])
            if not file_path.is_absolute():
                file_path = BASE_DIR / file_path
            if file_path.is_file():
                try:
                    artifact["file_content"] = file_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    artifact["file_content"] = None

        return jsonify(artifact)
    finally:
        conn.close()


@iac_api.route("/stig-coverage", methods=["GET"])
def stig_coverage():
    """GET /api/iac/stig-coverage -- STIG hardening status by target type."""
    conn = _get_db()
    try:
        if not _table_exists(conn, "stig_findings"):
            return jsonify(
                {
                    "targets": [],
                    "summary": {
                        "total": 0,
                        "hardened": 0,
                        "hardening_pct": 0.0,
                    },
                }
            )

        rows = conn.execute(
            "SELECT target_type, status, COUNT(*) FROM stig_findings GROUP BY target_type, status"
        ).fetchall()

        targets = {}
        grand_total = 0
        grand_hardened = 0

        for target_type, status, count in rows:
            t = target_type or "unknown"
            if t not in targets:
                targets[t] = {
                    "target_type": t,
                    "Open": 0,
                    "NotAFinding": 0,
                    "Not_Applicable": 0,
                    "other": 0,
                    "total": 0,
                    "hardening_pct": 0.0,
                }
            if status in ("Open", "NotAFinding", "Not_Applicable"):
                targets[t][status] += count
            else:
                targets[t]["other"] += count
            targets[t]["total"] += count
            grand_total += count
            if status == "NotAFinding":
                grand_hardened += count

        # Calculate per-target hardening percentages
        for t in targets.values():
            applicable = t["total"] - t["Not_Applicable"]
            t["hardening_pct"] = round(
                (t["NotAFinding"] / applicable * 100) if applicable > 0 else 0.0,
                1,
            )

        return jsonify(
            {
                "targets": list(targets.values()),
                "summary": {
                    "total": grand_total,
                    "hardened": grand_hardened,
                    "hardening_pct": round(
                        (grand_hardened / grand_total * 100) if grand_total > 0 else 0.0,
                        1,
                    ),
                },
            }
        )
    finally:
        conn.close()


@iac_api.route("/generate", methods=["POST"])
def generate_iac():
    """POST /api/iac/generate -- Generate IaC artifacts for a project."""
    data = request.get_json(force=True, silent=True) or {}

    project_id = data.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    csp = data.get("csp", "aws_govcloud")
    if csp not in SUPPORTED_CSPS:
        return jsonify({"error": f"Unsupported CSP: {csp}"}), 400

    impact_level = data.get("impact_level", "IL4")
    if impact_level not in SUPPORTED_IMPACT_LEVELS:
        return jsonify({"error": f"Unsupported impact level: {impact_level}"}), 400

    template_type = data.get("template_type", "terraform")
    if template_type not in SUPPORTED_TEMPLATE_TYPES:
        return jsonify({"error": f"Unsupported template type: {template_type}"}), 400

    try:
        from tools.deployment.iac_generator import generate_iac as _generate

        result = _generate(
            project_id=project_id,
            csp=csp,
            impact_level=impact_level,
            template_type=template_type,
        )
        return jsonify(result)
    except ImportError:
        return jsonify(
            {
                "error": "iac_generator module not available",
            }
        ), 501
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@iac_api.route("/templates", methods=["GET"])
def list_templates():
    """GET /api/iac/templates -- List available IaC template types."""
    return jsonify(
        {
            "csps": [
                {"id": "aws_govcloud", "label": "AWS GovCloud"},
                {"id": "azure_government", "label": "Azure Government"},
                {"id": "gcp", "label": "Google Cloud Platform"},
                {"id": "oci", "label": "Oracle Cloud Infrastructure"},
                {"id": "ibm", "label": "IBM Cloud"},
                {"id": "local", "label": "Local / On-Premise"},
            ],
            "impact_levels": [
                {"id": "IL2", "label": "IL2 — Public"},
                {"id": "IL4", "label": "IL4 — CUI / GovCloud"},
                {"id": "IL5", "label": "IL5 — CUI / Dedicated"},
                {"id": "IL6", "label": "IL6 — SECRET / SIPR"},
            ],
            "template_types": [
                {"id": "terraform", "label": "Terraform"},
                {"id": "ansible", "label": "Ansible"},
                {"id": "k8s", "label": "Kubernetes / OpenShift"},
            ],
        }
    )
