# CUI // SP-CTI
"""Data Mesh CSP Bridge — AWS DataZone, Azure Purview, GCP Dataplex.

Graceful degradation: each adapter checks for its SDK via importlib.
All operations support dry_run=True (default).
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import datetime, timezone

from tools.data_canvas.db.init_db import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.data_canvas.data_mesh.csp")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_csp_status() -> dict:
    """Return availability + credential status for all 3 CSP adapters."""
    return {
        "aws": {
            "available": bool(importlib.util.find_spec("boto3")),
            "configured": bool(os.environ.get("AWS_DATAZONE_DOMAIN_ID")),
        },
        "azure": {
            "available": bool(importlib.util.find_spec("azure")),
            "configured": bool(os.environ.get("AZURE_PURVIEW_ENDPOINT")),
        },
        "gcp": {
            "available": bool(importlib.util.find_spec("google")),
            "configured": bool(os.environ.get("GCP_PROJECT_ID")),
        },
    }


def run_sync(provider: str, domain_ids: list[str], dry_run: bool = True) -> dict:
    """Route sync to the appropriate adapter and log to dm_csp_sync_log."""
    from tools.data_canvas.data_mesh.domain_manager import list_domains

    domains = [d for d in list_domains() if not domain_ids or d.get("id") in domain_ids]

    if provider == "aws_datazone":
        from tools.data_canvas.data_mesh.csp.aws_datazone import sync_domains_to_datazone
        result = sync_domains_to_datazone(domains, dry_run=dry_run)
    elif provider == "azure_purview":
        from tools.data_canvas.data_mesh.csp.azure_purview import sync_domains_to_purview
        result = sync_domains_to_purview(domains, dry_run=dry_run)
    elif provider == "gcp_dataplex":
        from tools.data_canvas.data_mesh.csp.gcp_dataplex import sync_domains_to_dataplex
        result = sync_domains_to_dataplex(domains, dry_run=dry_run)
    else:
        result = {"provider": provider, "status": "error",
                  "synced_count": 0, "errors": [f"Unknown provider: {provider}"],
                  "dry_run": dry_run}

    _log_sync(result, domain_ids)
    return result


def _log_sync(result: dict, domain_ids: list[str]) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO dm_csp_sync_log
                   (id, provider, domain_id, product_id, operation, status,
                    synced_count, error_detail, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    result.get("provider", "unknown"),
                    ",".join(domain_ids) if domain_ids else "",
                    "",
                    "sync_domains",
                    result.get("status", "unknown"),
                    result.get("synced_count", 0),
                    "; ".join(result.get("errors", [])),
                    _now(),
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_log_sync: best-effort INSERT into dm_csp_sync_log failed (non-blocking): %s", exc)
