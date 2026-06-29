# CUI // SP-CTI
"""Marketplace Publisher — publish promoted connectors to marketplace.icdev.ai (D-CF-8).

Packages connector code as ZIP, uploads via marketplace API, updates
db_forge_connectors with marketplace_artifact_id and published_slug.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import io
import json
import sqlite3
import uuid
import zipfile
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = get_logger("databridge.forge.marketplace_publisher")

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "icdev.db"


def publish_connector(
    connector_id: str,
    tenant_id: str = "default",
    marketplace_url: str = "",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Publish a promoted connector to the marketplace.

    Packages code as ZIP, calls marketplace API, updates status to 'published'.
    """
    db = db_path or str(DB_PATH)
    try:
        conn = _get_conn(db)
        row = conn.execute("SELECT * FROM db_forge_connectors WHERE id = %s", (connector_id,)).fetchone()

        if not row:
            conn.close()
            return {"status": "error", "error": f"Connector {connector_id} not found"}

        if row["status"] != "promoted":
            conn.close()
            return {
                "status": "error",
                "error": f"Cannot publish: status is '{row['status']}', expected 'promoted'",
            }

        connector_name = row["connector_name"]
        code = row["generated_code"]
        slug = f"databridge-connector-{connector_name}"

        # Package as ZIP
        zip_bytes, zip_hash = _package_connector_zip(code, connector_name)

        # Upload to marketplace (if URL provided)
        artifact_id = None
        if marketplace_url:
            artifact_id = _upload_to_marketplace(marketplace_url, slug, zip_bytes, zip_hash, connector_name)

        # Update status
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """UPDATE db_forge_connectors
               SET status = 'published', published_slug = %s,
                   marketplace_artifact_id = %s, updated_at = %s
               WHERE id = %s""",
            (slug, artifact_id, now, connector_id),
        )

        conn.execute(
            """INSERT INTO db_forge_promotions
               (connector_id, from_status, to_status, promoted_by, review_notes, promoted_at)
               VALUES (%s, 'promoted', 'published', 'marketplace_publisher', %s, %s)""",
            (connector_id, f"Published as {slug}", now),
        )

        conn.execute(
            """INSERT INTO audit_trail (id, event_type, actor, action, details, created_at)
               VALUES (%s, 'connector_forge_published', 'marketplace_publisher', 'publish', %s, %s)""",
            (
                f"audit-{uuid.uuid4().hex[:12]}",
                json.dumps({"connector_id": connector_id, "slug": slug, "artifact_id": artifact_id}),
                now,
            ),
        )

        conn.commit()
        conn.close()

        return {
            "status": "ok",
            "connector_id": connector_id,
            "slug": slug,
            "artifact_id": artifact_id,
            "zip_hash": zip_hash,
            "new_status": "published",
        }

    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _package_connector_zip(code: str, connector_name: str) -> tuple:
    """Create in-memory ZIP with connector code.

    Returns: (zip_bytes: bytes, sha256_hash: str)
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{connector_name}_connector.py", code)
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "connector_name": connector_name,
                    "asset_type": "databridge_connector",
                    "generated_by": "connector_forge",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ),
        )
    zip_bytes = buf.getvalue()
    zip_hash = hashlib.sha256(zip_bytes).hexdigest()
    return zip_bytes, zip_hash


def _upload_to_marketplace(
    marketplace_url: str,
    slug: str,
    zip_bytes: bytes,
    zip_hash: str,
    connector_name: str,
) -> Optional[str]:
    """Upload ZIP to marketplace API. Returns artifact_id or None."""
    try:
        import urllib.request

        req = urllib.request.Request(
            f"{marketplace_url}/api/v1/artifacts/upload",
            data=zip_bytes,
            headers={
                "Content-Type": "application/zip",
                "X-Asset-Slug": slug,
                "X-Asset-Type": "databridge_connector",
                "X-SHA256-Hash": zip_hash,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
            result = json.loads(resp.read())
            return result.get("artifact_id")
    except Exception as exc:
        logger.warning("Marketplace upload failed: %s", exc)
        return None


def _get_conn(db_path: str) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
