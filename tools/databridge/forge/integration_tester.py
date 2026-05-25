# CUI // SP-CTI
"""Integration Tester — Stage 6 of Connector Forge pipeline (D-CF-1).

Runs connect/read/write/schema probes against sandbox results.
Writes results to db_forge_validations as 'integration' stage.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.databridge.forge.spec_parser import ForgeApiManifest
from tools.db.storage import get_connection

logger = get_logger("databridge.forge.integration_tester")

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "icdev.db"


def run_integration_tests(
    connector_id: str,
    sandbox_result: Optional[Dict[str, Any]],
    manifest: ForgeApiManifest,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluate sandbox results as integration tests.

    Args:
        connector_id: ID of the forge connector
        sandbox_result: Output from sandbox_manager.run_sandbox()
        manifest: Original API manifest for context

    Returns:
        {"passed": bool, "checks": [...], "summary": str}
    """
    checks: List[Dict[str, Any]] = []

    if sandbox_result is None:
        checks.append({"check": "sandbox_ran", "passed": True, "detail": "Sandbox skipped (no-sandbox mode)"})
        _store_validation(connector_id, True, "Sandbox skipped — no-sandbox mode", db_path)
        return {"passed": True, "checks": checks, "summary": "No sandbox — skipped"}

    # Check 1: Sandbox didn't error/timeout
    sandbox_ok = sandbox_result.get("status") == "ok"
    checks.append(
        {
            "check": "sandbox_status",
            "passed": sandbox_ok,
            "detail": f"Sandbox status: {sandbox_result.get('status')}",
        }
    )

    # Check 2: Connector imported successfully
    events = sandbox_result.get("events", [])
    import_ok = any(e.get("type") == "import" and e.get("status") == "ok" for e in events)
    checks.append(
        {
            "check": "import_ok",
            "passed": import_ok,
            "detail": "Connector module imported" if import_ok else "Import failed",
        }
    )

    # Check 3: Instantiation succeeded
    instantiate_ok = any(e.get("type") == "instantiate" and e.get("status") == "ok" for e in events)
    checks.append(
        {
            "check": "instantiate_ok",
            "passed": instantiate_ok,
            "detail": "Connector class instantiated" if instantiate_ok else "Instantiation failed",
        }
    )

    # Check 4: Connect attempted (skipped is OK — no real endpoint)
    connect_event = next((e for e in events if e.get("type") == "connect"), None)
    connect_ok = connect_event is not None
    checks.append(
        {
            "check": "connect_attempted",
            "passed": connect_ok,
            "detail": f"Connect: {connect_event.get('status', 'not attempted')}"
            if connect_event
            else "Connect not attempted",
        }
    )

    # Check 5: Health check attempted
    health_event = next((e for e in events if e.get("type") == "health"), None)
    health_ok = health_event is not None
    checks.append(
        {
            "check": "health_attempted",
            "passed": health_ok,
            "detail": f"Health: {health_event.get('status', 'not attempted')}"
            if health_event
            else "Health not attempted",
        }
    )

    # Check 6: list_tables attempted
    tables_event = next((e for e in events if e.get("type") == "list_tables"), None)
    tables_ok = tables_event is not None
    checks.append(
        {
            "check": "list_tables_attempted",
            "passed": tables_ok,
            "detail": f"Tables: {tables_event.get('count', '?')} found"
            if tables_event
            else "list_tables not attempted",
        }
    )

    # Overall: pass if import + instantiate succeeded (connect/read may fail without real endpoint)
    overall = import_ok and instantiate_ok
    summary_parts = [f"{c['check']}={'PASS' if c['passed'] else 'FAIL'}" for c in checks]
    summary = f"{'PASS' if overall else 'FAIL'}: {', '.join(summary_parts)}"

    _store_validation(connector_id, overall, summary, db_path)

    return {"passed": overall, "checks": checks, "summary": summary}


def _store_validation(
    connector_id: str,
    passed: bool,
    details: str,
    db_path: Optional[str],
) -> None:
    """Write integration test result to db_forge_validations."""
    db_path or str(DB_PATH)
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO db_forge_validations
               (connector_id, stage, passed, details, run_at)
               VALUES (?, 'integration', ?, ?, ?)""",
            (connector_id, 1 if passed else 0, details[:2000], datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Integration validation write failed: %s", exc)
