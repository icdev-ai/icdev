#!/usr/bin/env python3
# CUI // SP-CTI
"""OSCAL API Blueprint — REST endpoints for OSCAL ecosystem (D302-D306).

Provides /api/oscal/* endpoints for the dashboard OSCAL page:
tool detection, validation log, artifact browser, and catalog lookup.
"""

import os
import sqlite3
from tools.db.storage import get_connection
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    from tools.compat.db_utils import get_db_connection
except ImportError:
    get_db_connection = None

oscal_api = Blueprint("oscal_api", __name__, url_prefix="/api/oscal")


class _PGCompatConn:
    """Silently pre-translate ? → %s for PG so translate_sql never warns."""
    def __init__(self, conn):
        self._conn = conn
        self._pg = getattr(conn, "_backend", "sqlite") == "postgresql"
    def _fix(self, sql):
        return sql.replace("?", "%s") if self._pg and "?" in sql else sql
    def execute(self, sql, params=()):
        return self._conn.execute(self._fix(sql), params)
    def executemany(self, sql, seq):
        return self._conn.executemany(self._fix(sql), seq)
    def commit(self): return self._conn.commit()
    def rollback(self): return self._conn.rollback()
    def close(self): return self._conn.close()
    def __getattr__(self, name): return getattr(self._conn, name)


def _get_db() -> sqlite3.Connection:
    if get_db_connection:
        return _PGCompatConn(get_db_connection(DB_PATH))
    conn = get_connection(db_path=str(DB_PATH))
    return _PGCompatConn(conn)


# ── Tool Detection ──────────────────────────────────────────────


@oscal_api.route("/status", methods=["GET"])
def oscal_status():
    """Detect available OSCAL ecosystem tools."""
    try:
        from tools.compliance.oscal_tools import detect_oscal_tools

        result = detect_oscal_tools()
        return jsonify(result)
    except ImportError:
        return jsonify(
            {
                "java_available": False,
                "oscal_cli_available": False,
                "oscal_pydantic_available": False,
                "nist_catalog_available": False,
                "error": "oscal_tools module not available",
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@oscal_api.route("/detect", methods=["POST"])
def detect_tools():
    """Force re-detection of OSCAL tools."""
    try:
        from tools.compliance.oscal_tools import detect_oscal_tools

        result = detect_oscal_tools()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Validation Log ──────────────────────────────────────────────


@oscal_api.route("/validations", methods=["GET"])
def list_validations():
    """List recent OSCAL validation log entries."""
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))
    project_id = request.args.get("project_id")

    try:
        conn = _get_db()
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()

        rows = conn.execute(
            f"SELECT * FROM oscal_validation_log {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",  # nosec B608 -- table/column names are internal constants, not user input
            params + (limit, offset),
        ).fetchall()

        total = conn.execute(
            f"SELECT COUNT(*) FROM oscal_validation_log {where}",
            params,  # nosec B608 -- table/column names are internal constants, not user input
        ).fetchone()[0]

        conn.close()
        return jsonify({"validations": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset})
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


# ── Artifacts ───────────────────────────────────────────────────


@oscal_api.route("/artifacts", methods=["GET"])
def list_artifacts():
    """List OSCAL artifacts."""
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))
    project_id = request.args.get("project_id")

    try:
        conn = _get_db()
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()

        rows = conn.execute(
            f"SELECT * FROM oscal_artifacts {where} ORDER BY generated_at DESC LIMIT %s OFFSET %s",  # nosec B608 -- table/column names are internal constants, not user input
            params + (limit, offset),
        ).fetchall()

        total = conn.execute(
            f"SELECT COUNT(*) FROM oscal_artifacts {where}",
            params,  # nosec B608 -- table/column names are internal constants, not user input
        ).fetchone()[0]

        conn.close()
        return jsonify({"artifacts": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset})
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


# ── Catalog ─────────────────────────────────────────────────────


@oscal_api.route("/catalog/stats", methods=["GET"])
def catalog_stats():
    """Get OSCAL catalog statistics."""
    try:
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

        adapter = OscalCatalogAdapter()
        stats = adapter.get_catalog_stats()
        return jsonify(stats)
    except ImportError:
        return jsonify({"error": "oscal_catalog_adapter not available"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@oscal_api.route("/catalog/<control_id>", methods=["GET"])
def catalog_lookup(control_id: str):
    """Look up a specific control from the OSCAL catalog."""
    try:
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

        adapter = OscalCatalogAdapter()
        control = adapter.get_control(control_id)
        if not control:
            return jsonify({"error": f"Control {control_id} not found"}), 404
        return jsonify({"control": control})
    except ImportError:
        return jsonify({"error": "oscal_catalog_adapter not available"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Generation ──────────────────────────────────────────────────

# Artifact type -> oscal_generator entry point. `assessment_plan` is the
# continuous-assessment model; `all` runs every one of the five in sequence.
GENERATOR_FUNCTIONS = {
    "ssp": "generate_oscal_ssp",
    "poam": "generate_oscal_poam",
    "assessment_results": "generate_oscal_assessment_results",
    "assessment_plan": "generate_oscal_assessment_plan",
    "component_definition": "generate_oscal_component_definition",
    "all": "generate_all_oscal",
}


@oscal_api.route("/generate", methods=["POST"])
def generate_artifact():
    """Generate an OSCAL artifact for a project.

    An unknown artifact type is REFUSED with the accepted list, never
    silently defaulted to `ssp`: a caller asking for an assessment-plan and
    receiving an SSP with a 200 has no way to learn that it did not get one.
    """
    data = request.get_json(force=True, silent=True) or {}
    project_id = data.get("project_id")
    artifact = data.get("artifact", "ssp")

    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    func_name = GENERATOR_FUNCTIONS.get(artifact)
    if not func_name:
        return (
            jsonify(
                {
                    "error": f"Unknown artifact type: {artifact}",
                    "accepted": sorted(GENERATOR_FUNCTIONS),
                }
            ),
            400,
        )

    try:
        from tools.compliance import oscal_generator

        result = getattr(oscal_generator, func_name)(
            project_id,
            output_dir=data.get("output_dir"),
            db_path=str(DB_PATH),
        )
        return jsonify({"artifact": artifact, "result": result})
    except ImportError:
        return jsonify({"error": "oscal_generator module not available"}), 503
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Validation Action ───────────────────────────────────────────


@oscal_api.route("/validate", methods=["POST"])
def validate_file():
    """Deep-validate an OSCAL file (3-layer pipeline)."""
    data = request.get_json(force=True, silent=True) or {}
    file_path = data.get("file_path")

    if not file_path:
        return jsonify({"error": "file_path required"}), 400

    try:
        from tools.compliance.oscal_tools import validate_oscal_deep

        result = validate_oscal_deep(file_path)
        return jsonify(result)
    except ImportError:
        return jsonify({"error": "oscal_tools module not available"}), 503
    except FileNotFoundError:
        return jsonify({"error": f"File not found: {file_path}"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
