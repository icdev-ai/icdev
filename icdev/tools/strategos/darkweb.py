#!/usr/bin/env python3
# CUI // SP-CTI
"""Strategos — Dark Web Monitor.

Provides signal retrieval and monitor-trigger logic for the dark web
intelligence feed at /strategos/darkweb.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# TOR availability check
# ---------------------------------------------------------------------------

def tor_available() -> bool:
    """Return True if a SOCKS5 proxy at the configured TOR port is reachable."""
    tor_host = os.environ.get("TOR_HOST", "127.0.0.1")
    tor_port = int(os.environ.get("TOR_PORT", "9050"))
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect((tor_host, tor_port))
        s.close()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Signal retrieval
# ---------------------------------------------------------------------------

def get_signals(status: Optional[str] = None, tenant_id: Optional[str] = None) -> list[dict]:
    """Return dark web signals from the DB, optionally filtered by status."""
    # Attempt to infer tenant from request context if not provided
    if tenant_id is None:
        try:
            from tools.saas.auth.middleware import get_current_tenant_id
            tenant_id = get_current_tenant_id()
        except Exception:
            pass
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            tenant_clause = (
                " AND (tenant_id = ? OR tenant_id IS NULL)"
                if tenant_id
                else " AND (tenant_id IS NULL OR tenant_id = '')"
            )
            tenant_params: tuple = (tenant_id,) if tenant_id else ()
            if status and status != "all":
                cur.execute(
                    "SELECT id, score, signal_type, title, source, collected_at, status "
                    "FROM strategos_darkweb_signals WHERE status = ?"
                    + tenant_clause
                    + " ORDER BY score DESC, collected_at DESC",
                    (status,) + tenant_params,
                )
            else:
                cur.execute(
                    "SELECT id, score, signal_type, title, source, collected_at, status "
                    "FROM strategos_darkweb_signals WHERE 1=1"
                    + tenant_clause
                    + " ORDER BY score DESC, collected_at DESC",
                    tenant_params,
                )
            rows = cur.fetchall()
    except Exception:
        rows = []

    now = datetime.now(timezone.utc)
    result = []
    for row in rows:
        sid, score, sig_type, title, source, collected_at, sig_status = row
        try:
            ts = datetime.fromisoformat(collected_at.replace("Z", "+00:00"))
            delta = now - ts
            minutes = int(delta.total_seconds() // 60)
            if minutes < 60:
                age = f"{minutes}m"
            elif minutes < 1440:
                age = f"{minutes // 60}h"
            else:
                age = f"{minutes // 1440}d"
        except Exception:
            age = "—"
        result.append({
            "id": sid,
            "score": round(float(score or 0), 3),
            "type": sig_type or "unknown",
            "title": title or "(no title)",
            "source": source or "—",
            "age": age,
            "status": sig_status or "new",
        })
    return result


# ---------------------------------------------------------------------------
# Monitor trigger
# ---------------------------------------------------------------------------

def run_monitor() -> dict:
    """Trigger a dark web monitor scan.  Returns immediately with job status."""
    script = os.path.join(os.path.dirname(__file__), "darkweb_runner.py")
    if os.path.exists(script):
        try:
            subprocess.Popen(
                ["python", script, "--once"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {"status": "triggered", "message": "Monitor scan launched."}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
    return {"status": "skipped", "message": "darkweb_runner.py not found — no-op."}
