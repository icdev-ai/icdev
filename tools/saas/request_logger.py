#!/usr/bin/env python3
"""ICDEV SaaS -- Request Logger.

CUI // SP-CTI

Logs every API request to the usage_records and audit_platform tables
in the platform database.  Registers as Flask before_request / after_request
middleware so timing is automatic.

Usage:
    from tools.saas.request_logger import register_request_logger
    register_request_logger(app)
"""

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("saas.request_logger")

PLATFORM_DB_PATH = Path(
    os.environ.get("PLATFORM_DB_PATH", str(BASE_DIR / "data" / "platform.db"))
)


# ---------------------------------------------------------------------------
# Core logging function
# ---------------------------------------------------------------------------
def log_request(
    tenant_id,
    user_id,
    endpoint,
    method,
    status_code,
    duration_ms,
    tokens_used=0,
    metadata=None,
):
    """Log an API request to the usage_records table.

    Args:
        tenant_id:   Tenant UUID.
        user_id:     User UUID (may be None for tenant-level keys).
        endpoint:    Request path (e.g. /api/v1/projects).
        method:      HTTP method (GET, POST, etc.).
        status_code: HTTP response status code.
        duration_ms: Request duration in milliseconds.
        tokens_used: LLM tokens consumed (default 0).
        metadata:    Optional dict of extra context.
    """
    try:
        conn = sqlite3.connect(str(PLATFORM_DB_PATH))
        conn.execute(
            """INSERT INTO usage_records
               (tenant_id, user_id, endpoint, method, tokens_used,
                status_code, duration_ms, metadata, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tenant_id,
                user_id,
                endpoint,
                method,
                tokens_used,
                status_code,
                duration_ms,
                json.dumps(metadata) if metadata else "{}",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        logger.debug(
            "Logged request: tenant=%s endpoint=%s status=%s duration=%dms",
            tenant_id, endpoint, status_code, duration_ms,
        )
    except Exception as exc:
        # Never let logging failures break the actual request
        logger.debug("Request log write error: %s", exc)


# ---------------------------------------------------------------------------
# Flask middleware registration
# ---------------------------------------------------------------------------
def register_request_logger(app):
    """Register before_request / after_request hooks on a Flask app.

    Sets g._request_start on entry and writes a usage record on exit.
    Only logs requests that have a g.tenant_id (i.e., authenticated).
    """
    from flask import g, request as flask_request

    @app.before_request
    def _start_timer():
        g._request_start = time.time()

    @app.after_request
    def _log_request(response):
        start = getattr(g, "_request_start", None)
        duration_ms = int((time.time() - start) * 1000) if start else 0

        tenant_id = getattr(g, "tenant_id", None)
        user_id = getattr(g, "user_id", None)

        if tenant_id:
            log_request(
                tenant_id=tenant_id,
                user_id=user_id,
                endpoint=flask_request.path,
                method=flask_request.method,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        return response
