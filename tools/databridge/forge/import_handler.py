# CUI // SP-CTI
"""Import Handler — download and register community connectors (D-CF-8).

Downloads a connector from the marketplace, validates it, and registers
in db_forge_connectors. Imported connectors start as 'sandboxed' and
must be promoted via the normal workflow.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import sqlite3
import uuid
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from tools.databridge.forge.static_validator import validate_connector_code

logger = get_logger("databridge.forge.import_handler")

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "icdev.db"


def import_community_connector(
    slug: str,
    tenant_id: str = "default",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Import a community connector from the marketplace.

    Uses asset_installer to download, then validates and registers.
    Imported connectors start in 'sandboxed' status.
    """
    db = db_path or str(DB_PATH)

    # Step 1: Download via asset_installer
    try:
        from tools.marketplace.asset_installer import install

        install_result = install(slug)
        if not install_result.get("status") == "ok":
            return {
                "status": "error",
                "error": f"Marketplace install failed: {install_result.get('error', 'unknown')}",
            }
    except ImportError:
        return {"status": "error", "error": "Marketplace asset_installer not available"}
    except Exception as exc:
        return {"status": "error", "error": f"Install error: {exc}"}

    # Step 2: Find the installed connector code
    install_path = install_result.get("install_path", "")
    if not install_path:
        return {"status": "error", "error": "No install path returned"}

    connector_file = _find_connector_file(install_path)
    if not connector_file:
        return {"status": "error", "error": f"No .py connector file found in {install_path}"}

    code = connector_file.read_text(encoding="utf-8")
    connector_name = slug.replace("databridge-connector-", "").replace("-", "_")

    # Step 3: Validate
    val = validate_connector_code(code, connector_name)
    if not val["passed"]:
        return {
            "status": "validation_failed",
            "error": f"Imported connector failed validation: {val['blocking_gates']}",
            "validation": val,
        }

    # Step 4: Store in db_forge_connectors
    import hashlib

    connector_id = f"forge-imp-{uuid.uuid4().hex[:12]}"
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).isoformat()

    try:
        conn = _get_conn(db)
        conn.execute(
            """INSERT INTO db_forge_connectors
               (id, connector_name, connector_type, base_class, protocol,
                generated_code, code_hash, version, status, published_slug,
                tenant_id, created_at, updated_at)
               VALUES (?, ?, 'saas_api', 'imported', 'rest', ?, ?, 1, 'sandboxed',
                       ?, ?, ?, ?)""",
            (connector_id, connector_name, code, code_hash, slug, tenant_id, now, now),
        )

        conn.execute(
            """INSERT INTO audit_trail (id, event_type, actor, action, details, created_at)
               VALUES (?, 'connector_forge_imported', 'import_handler', 'import', ?, ?)""",
            (
                f"audit-{uuid.uuid4().hex[:12]}",
                json.dumps({"connector_id": connector_id, "slug": slug, "connector_name": connector_name}),
                now,
            ),
        )

        conn.commit()
        conn.close()
    except Exception as exc:
        return {"status": "error", "error": f"DB store failed: {exc}"}

    return {
        "status": "ok",
        "connector_id": connector_id,
        "connector_name": connector_name,
        "slug": slug,
        "validation": val,
        "new_status": "sandboxed",
    }


def _find_connector_file(install_path: str) -> Optional[Path]:
    """Find the connector .py file in an installed directory."""
    path = Path(install_path)
    if path.is_file() and path.suffix == ".py":
        return path
    if path.is_dir():
        py_files = list(path.glob("*_connector.py")) + list(path.glob("*.py"))
        for f in py_files:
            if f.name != "__init__.py" and f.name != "manifest.json":
                return f
    return None


def _get_conn(db_path: str) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
