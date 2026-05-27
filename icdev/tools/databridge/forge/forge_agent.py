# CUI // SP-CTI
"""Connector Forge Agent — orchestrates 6-stage connector generation pipeline (D-CF-1).

Entry point for all forge operations. Coordinates:
  Stage 1: spec_parser.parse_spec()
  Stage 2: base_selector.select_base_class()
  Stage 3: code_generator.generate_connector_code()
  Stage 4: static_validator.validate_connector_code()
  Stage 5: sandbox_manager.run_sandbox()
  Stage 6: integration_tester.run_integration_tests()
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import sqlite3
import time
import uuid
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.databridge.forge.base_selector import select_base_class
from tools.databridge.forge.code_generator import generate_connector_code
from tools.databridge.forge.integration_tester import run_integration_tests
from tools.databridge.forge.sandbox_manager import run_sandbox
from tools.databridge.forge.spec_parser import ForgeApiManifest, parse_spec
from tools.databridge.forge.static_validator import validate_connector_code

logger = get_logger("databridge.forge.agent")

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "icdev.db"


def forge_from_spec(
    content: str,
    connector_name: str,
    display_name: str = "",
    input_type: str = "auto",
    source_url: str = "",
    use_llm: bool = True,
    run_sandbox_flag: bool = True,
    tenant_id: str = "default",
    project_id: str = "",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Full 6-stage pipeline from raw spec content.

    Returns: {status, connector_id, connector_name, validation, sandbox,
              integration, errors, warnings, duration_ms}
    """
    start = time.time()
    errors: List[str] = []
    db = db_path or str(DB_PATH)

    # Stage 1: Parse spec
    manifest = parse_spec(content, input_type, source_url)
    spec_id = _store_spec(manifest, content, input_type, tenant_id, db)
    _audit(
        db,
        "connector_forge_spec_parsed",
        json.dumps(
            {
                "spec_id": spec_id,
                "protocol": manifest.protocol,
                "endpoints": len(manifest.endpoints),
            }
        ),
    )

    # Stage 2: Select base class
    selection = select_base_class(manifest)

    # Stage 3: Generate code
    gen_result = generate_connector_code(
        manifest,
        selection,
        connector_name,
        display_name,
        use_llm,
        db_path,
    )
    code = gen_result["code"]

    # Store connector (status=sandboxed)
    connector_id = _store_connector(
        connector_name,
        selection.connector_type,
        selection.base_class,
        manifest.protocol,
        code,
        gen_result["hash"],
        spec_id,
        tenant_id,
        project_id,
        db,
    )
    _audit(
        db,
        "connector_forge_generated",
        json.dumps(
            {
                "connector_id": connector_id,
                "connector_name": connector_name,
                "method": gen_result["method"],
                "template": gen_result["template"],
            }
        ),
    )

    # Stage 4: Static validation
    val_result = validate_connector_code(code, connector_name)
    _store_validations(connector_id, val_result["gates"], db)
    _audit(
        db,
        "connector_forge_validated",
        json.dumps(
            {
                "connector_id": connector_id,
                "passed": val_result["passed"],
                "blocking_gates": val_result["blocking_gates"],
            }
        ),
    )

    if not val_result["passed"]:
        errors.extend([f"Gate {g} failed" for g in val_result["blocking_gates"]])
        return {
            "status": "validation_failed",
            "connector_id": connector_id,
            "connector_name": connector_name,
            "validation": val_result,
            "sandbox": None,
            "integration": None,
            "errors": errors,
            "warnings": manifest.parse_warnings + gen_result.get("warnings", []),
            "duration_ms": int((time.time() - start) * 1000),
        }

    # Stage 5: Sandbox
    sandbox_result = None
    if run_sandbox_flag:
        _audit(db, "connector_forge_sandbox_started", connector_id)
        sandbox_result = run_sandbox(connector_id, code, connector_name, db_path=db)
        _audit(
            db,
            "connector_forge_sandbox_completed",
            json.dumps(
                {
                    "connector_id": connector_id,
                    "sandbox_type": sandbox_result["sandbox_type"],
                    "status": sandbox_result["status"],
                }
            ),
        )

    # Stage 6: Integration test
    int_result = run_integration_tests(connector_id, sandbox_result, manifest, db)

    status = "sandboxed" if (sandbox_result and sandbox_result.get("status") == "ok") else "generated"
    return {
        "status": status,
        "connector_id": connector_id,
        "connector_name": connector_name,
        "validation": val_result,
        "sandbox": sandbox_result,
        "integration": int_result,
        "errors": errors,
        "warnings": manifest.parse_warnings + gen_result.get("warnings", []),
        "duration_ms": int((time.time() - start) * 1000),
    }


def get_forge_status(connector_id: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Get status of a forge connector by ID."""
    db = db_path or str(DB_PATH)
    try:
        conn = _get_conn(db)
        row = conn.execute("SELECT * FROM db_forge_connectors WHERE id = ?", (connector_id,)).fetchone()
        conn.close()
        if row:
            return dict(row)
        return {"error": f"Connector {connector_id} not found"}
    except Exception as exc:
        return {"error": str(exc)}


def forge_validate(connector_id: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Re-validate an existing forge connector."""
    db = db_path or str(DB_PATH)
    try:
        conn = _get_conn(db)
        row = conn.execute(
            "SELECT connector_name, generated_code FROM db_forge_connectors WHERE id = ?",
            (connector_id,),
        ).fetchone()
        conn.close()
        if not row:
            return {"error": f"Connector {connector_id} not found"}
        return validate_connector_code(row["generated_code"], row["connector_name"])
    except Exception as exc:
        return {"error": str(exc)}


def list_forge_connectors(
    status: Optional[str] = None,
    tenant_id: str = "default",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """List all forge connectors with optional status filter."""
    db = db_path or str(DB_PATH)
    try:
        conn = _get_conn(db)
        sql = "SELECT id, connector_name, connector_type, base_class, protocol, status, created_at FROM db_forge_connectors WHERE tenant_id = ?"  # noqa: E501
        params: List[Any] = [tenant_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return {"connectors": [dict(r) for r in rows], "count": len(rows)}
    except sqlite3.OperationalError:
        return {"connectors": [], "count": 0}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _get_conn(db_path: str) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _store_spec(
    manifest: ForgeApiManifest,
    raw: str,
    input_type: str,
    tenant_id: str,
    db_path: str,
) -> str:
    """Store parsed spec in db_forge_specs."""
    spec_id = f"spec-{uuid.uuid4().hex[:12]}"
    try:
        conn = _get_conn(db_path)
        conn.execute(
            """INSERT INTO db_forge_specs
               (id, input_type, input_source, raw_input, parsed_manifest,
                detected_protocol, target_base_class, tenant_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                spec_id,
                input_type,
                manifest.base_url,
                raw[:50000],
                json.dumps(
                    {
                        "title": manifest.title,
                        "protocol": manifest.protocol,
                        "endpoints": len(manifest.endpoints),
                        "auth_methods": manifest.auth_methods,
                    }
                ),
                manifest.protocol,
                None,
                tenant_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Spec store failed: %s", exc)
    return spec_id


def _store_connector(
    connector_name: str,
    connector_type: str,
    base_class: str,
    protocol: str,
    code: str,
    code_hash: str,
    spec_id: str,
    tenant_id: str,
    project_id: str,
    db_path: str,
) -> str:
    """Store generated connector in db_forge_connectors."""
    connector_id = f"forge-{uuid.uuid4().hex[:12]}"
    try:
        conn = _get_conn(db_path)
        conn.execute(
            """INSERT INTO db_forge_connectors
               (id, connector_name, connector_type, base_class, protocol,
                generated_code, code_hash, version, status, spec_id,
                tenant_id, project_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'sandboxed', ?, ?, ?, ?, ?)""",
            (
                connector_id,
                connector_name,
                connector_type,
                base_class,
                protocol,
                code,
                code_hash,
                spec_id,
                tenant_id,
                project_id or None,
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Connector store failed: %s", exc)
    return connector_id


def _store_validations(
    connector_id: str,
    gates: List[Dict[str, Any]],
    db_path: str,
) -> None:
    """Store validation gate results in db_forge_validations."""
    try:
        conn = _get_conn(db_path)
        now = datetime.now(timezone.utc).isoformat()
        for gate in gates:
            conn.execute(
                """INSERT INTO db_forge_validations
                   (connector_id, stage, passed, details, duration_ms, run_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    connector_id,
                    gate["stage"],
                    1 if gate["passed"] else 0,
                    gate["details"][:2000],
                    gate.get("duration_ms", 0),
                    now,
                ),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Validation store failed: %s", exc)


def _audit(db_path: str, event_type: str, details: str) -> None:
    """Append audit trail entry."""
    try:
        conn = _get_conn(db_path)
        conn.execute(
            """INSERT INTO audit_trail (id, event_type, actor, action, details, created_at)
               VALUES (?, ?, 'connector_forge_agent', ?, ?, ?)""",
            (
                f"audit-{uuid.uuid4().hex[:12]}",
                event_type,
                event_type,
                details[:5000],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("Audit write failed (table may not exist): %s", exc)
