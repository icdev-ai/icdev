# CUI // SP-CTI
"""CloudForge Canvas event emitter — canvas_events on resource provision and runbook execution.

CloudForge canvas is not yet built; this module provides the event emission
API so DIC integration is ready when the canvas ships. Callers inject these
functions at the relevant state-change points.

Injection points (future CloudForge canvas):
  - resource_provisioned: after a cloud resource (VPC, VM, bucket, IAM role) is created.
  - runbook_executed: after a CloudForge runbook completes (success or failure).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_canvas_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_SOURCE = "cloudforge"
_TARGET = "dic"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(event_type: str, payload: dict[str, Any],
          tenant_id: str = "", classification: str = "CUI") -> bool:
    try:
        event_id = f"ce-{uuid.uuid4().hex[:16]}"
        # canvas_events (migration 039) has no tenant_id/classification
        # columns; carry the security context inside payload_json and use
        # the RLS-disabled canvas connection (mirrors tools/canvas/event_bus.py,
        # PR #720) so get_connection()'s predicate injection can't raise
        # UndefinedColumn on PostgreSQL.
        payload = {**payload, "_security_context":
                   {"tenant_id": tenant_id, "clearance": classification}}
        with get_canvas_connection() as conn:
            conn.execute(
                """
                INSERT INTO canvas_events
                    (id, source_canvas, target_canvas, event_type,
                     payload_json, created_at, consumed_at)
                VALUES (%s, %s, %s, %s, %s, %s, NULL)
                """,
                (event_id, _SOURCE, _TARGET, event_type,
                 json.dumps(payload), _now()),
            )
            conn.commit()
        logger.debug("cloudforge_event_emitter: emitted %s (%s)", event_type, event_id)
        return True
    except Exception as exc:
        logger.warning("cloudforge_event_emitter: failed to emit %s: %s", event_type, exc)
        return False


def emit_resource_provisioned(
    resource_id: str,
    resource_type: str,
    cloud_provider: str = "",
    region: str = "",
    project_id: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit cloudforge.resource_provisioned when a cloud resource is successfully created."""
    return _emit(
        "cloudforge.resource_provisioned",
        {
            "device_id": project_id or resource_id,
            "resource_id": resource_id,
            "resource_type": resource_type,
            "cloud_provider": cloud_provider,
            "region": region,
            "change_summary": (
                f"CloudForge: {resource_type} '{resource_id}' provisioned "
                f"on {cloud_provider or 'cloud'} ({region or 'unknown region'})"
            ),
            "affected_segments": [resource_type, cloud_provider],
        },
        tenant_id=tenant_id,
        classification=classification,
    )


def emit_runbook_executed(
    runbook_id: str,
    runbook_name: str,
    status: str,
    cloud_provider: str = "",
    project_id: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit cloudforge.runbook_executed when a CloudForge runbook completes."""
    return _emit(
        "cloudforge.runbook_executed",
        {
            "device_id": project_id or runbook_id,
            "runbook_id": runbook_id,
            "runbook_name": runbook_name,
            "status": status,
            "cloud_provider": cloud_provider,
            "change_summary": (
                f"CloudForge runbook '{runbook_name}' completed with status={status}"
            ),
            "affected_segments": [runbook_name],
        },
        tenant_id=tenant_id,
        classification=classification,
    )
