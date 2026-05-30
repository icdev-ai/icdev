# CUI // SP-CTI
"""GovLift — External Interface Integration Registry and Health Module.

Manages the govlift_integrations table with security-hardened schema and
provides health-check utilities for external system interfaces.

NIST controls: CA-3 (ISA), SA-9 (external services), SC-8 (TLS), AU-2 (logging).

CLI:
    python tools/govlift/integrations.py --health-check --json
    python tools/govlift/integrations.py --list --json
    python tools/govlift/integrations.py --update servicenow --status connected
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

from tools.db.storage import get_connection, translate_sql


# ── Schema migration (adds security columns to existing table) ─────────────

_SECURITY_COLUMNS = [
    ("tls_enabled",        "INTEGER DEFAULT 1"),
    ("auth_method",        "TEXT DEFAULT 'api_key'"),
    ("timeout_seconds",    "INTEGER DEFAULT 30"),
    ("retry_max",          "INTEGER DEFAULT 3"),
    ("nist_ca3_isa_id",    "TEXT DEFAULT ''"),
    ("health_status",      "TEXT DEFAULT 'unknown'"),
    ("health_checked_at",  "TEXT"),
    ("health_latency_ms",  "INTEGER"),
]

_INT_STATUS_VALUES = ("connected", "degraded", "disconnected", "unknown")
_AUTH_METHODS      = ("api_key", "mtls", "oauth2", "none")


def ensure_schema() -> None:
    """Idempotent — add security columns to govlift_integrations if absent."""
    conn = get_connection()
    try:
        for col, col_def in _SECURITY_COLUMNS:
            try:
                stmt = translate_sql(
                    f"ALTER TABLE govlift_integrations ADD COLUMN {col} {col_def}"
                )
                conn.execute(stmt)
                conn.commit()
            except Exception as exc:
                # PostgreSQL aborts the transaction on error — rollback before continuing
                try:
                    conn.rollback()
                except Exception:
                    pass
                msg = str(exc).lower()
                if "already exists" in msg or "duplicate column" in msg:
                    pass  # already migrated
                else:
                    raise
    finally:
        conn.close()


# ── Read helpers ────────────────────────────────────────────────────────────

def list_integrations() -> list[dict]:
    """Return all integration records ordered by system_name."""
    ensure_schema()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM govlift_integrations ORDER BY system_name ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_integration(system_name: str) -> Optional[dict]:
    """Return a single integration record or None."""
    ensure_schema()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM govlift_integrations WHERE system_name = %s",
            (system_name,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── Write helpers ───────────────────────────────────────────────────────────

def update_integration_status(
    system_name: str,
    status: str,
    error_message: str = "",
    sync_increment: bool = False,
) -> dict:
    """Update status and optionally increment sync_count; return updated row."""
    if status not in _INT_STATUS_VALUES:
        raise ValueError(f"status must be one of {_INT_STATUS_VALUES}")
    ensure_schema()
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        if sync_increment:
            conn.execute(
                translate_sql(
                    "UPDATE govlift_integrations "
                    "SET status = %s, last_sync = %s, sync_count = sync_count + 1, "
                    "    error_message = %s "
                    "WHERE system_name = %s"
                ),
                (status, now, error_message, system_name),
            )
        else:
            conn.execute(
                translate_sql(
                    "UPDATE govlift_integrations "
                    "SET status = %s, error_message = %s "
                    "WHERE system_name = %s"
                ),
                (status, error_message, system_name),
            )
        conn.commit()
        return get_integration(system_name) or {}
    finally:
        conn.close()


def record_health_check(
    system_name: str,
    health_status: str,
    latency_ms: Optional[int] = None,
) -> None:
    """Persist health-check result to govlift_integrations."""
    ensure_schema()
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            translate_sql(
                "UPDATE govlift_integrations "
                "SET health_status = %s, health_checked_at = %s, health_latency_ms = %s "
                "WHERE system_name = %s"
            ),
            (health_status, now, latency_ms, system_name),
        )
        conn.commit()
    finally:
        conn.close()


# ── Health check ─────────────────────────────────────────────────────────────

def probe_integration(record: dict) -> dict:
    """
    Lightweight connectivity probe for one integration.

    Returns: {system_name, reachable, latency_ms, health_status, error}

    Uses a TCP connect + TLS handshake only — never sends auth credentials
    to avoid replay risk. This is infrastructure-layer health check, not
    a full API call.
    """
    import socket
    import ssl
    from urllib.parse import urlparse

    system_name = record.get("system_name", "")
    endpoint    = record.get("endpoint", "")

    result = {
        "system_name": system_name,
        "endpoint": endpoint,
        "reachable": False,
        "latency_ms": None,
        "health_status": "unknown",
        "error": None,
    }

    if not endpoint:
        result["error"] = "no endpoint configured"
        return result

    parsed = urlparse(endpoint)
    host   = parsed.hostname or ""
    port   = parsed.port or (443 if parsed.scheme == "https" else 80)
    use_tls = parsed.scheme == "https"

    try:
        start = time.monotonic()
        ctx   = ssl.create_default_context() if use_tls else None

        with socket.create_connection((host, port), timeout=5) as raw_sock:
            if use_tls and ctx:
                with ctx.wrap_socket(raw_sock, server_hostname=host):
                    pass  # TLS handshake only
            elapsed_ms = int((time.monotonic() - start) * 1000)

        result["reachable"]     = True
        result["latency_ms"]    = elapsed_ms
        result["health_status"] = "connected" if elapsed_ms < 3000 else "degraded"

    except ssl.SSLCertVerificationError as exc:
        result["error"]         = f"TLS cert invalid: {exc}"
        result["health_status"] = "disconnected"
    except (socket.timeout, ConnectionRefusedError, OSError) as exc:
        result["error"]         = str(exc)
        result["health_status"] = "disconnected"
    except Exception as exc:  # noqa: BLE001
        result["error"]         = str(exc)
        result["health_status"] = "unknown"

    return result


def run_health_check() -> dict:
    """
    Probe all registered integrations and persist results.

    Returns: {checked_at, integrations: [...], summary: {connected, degraded, disconnected, unknown}}
    """
    records  = list_integrations()
    results  = []
    summary  = {"connected": 0, "degraded": 0, "disconnected": 0, "unknown": 0}

    for rec in records:
        probe = probe_integration(rec)
        record_health_check(
            rec["system_name"],
            probe["health_status"],
            probe["latency_ms"],
        )
        results.append(probe)
        hs = probe["health_status"]
        if hs in summary:
            summary[hs] += 1
        else:
            summary["unknown"] += 1

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "classification": "CUI // SP-CTI",
        "integrations": results,
        "summary": summary,
    }


def get_status_summary() -> dict:
    """Return summary from stored health_status values (no live probe)."""
    records  = list_integrations()
    summary  = {"connected": 0, "degraded": 0, "disconnected": 0, "unknown": 0}
    for rec in records:
        hs = rec.get("health_status") or rec.get("status") or "unknown"
        if hs in summary:
            summary[hs] += 1
        else:
            summary["unknown"] += 1
    return {
        "classification": "CUI // SP-CTI",
        "total": len(records),
        "summary": summary,
        "integrations": records,
    }


# ── CLI ────────────────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="GovLift external interface integration registry"
    )
    parser.add_argument("--health-check", action="store_true",
                        help="Probe all integrations and report status")
    parser.add_argument("--list", action="store_true",
                        help="List integration records from DB")
    parser.add_argument("--update", metavar="SYSTEM_NAME",
                        help="Update integration status")
    parser.add_argument("--status", choices=_INT_STATUS_VALUES,
                        help="New status for --update")
    parser.add_argument("--error", default="",
                        help="Error message for --update")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON")
    args = parser.parse_args()

    if args.health_check:
        result = run_health_check()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Health check at {result['checked_at']}")
            for i in result["integrations"]:
                sym = "✓" if i["reachable"] else "✗"
                print(f"  {sym} {i['system_name']:<20} {i['health_status']:<15} "
                      f"{(str(i['latency_ms']) + 'ms') if i['latency_ms'] else ''}")
            s = result["summary"]
            print(f"\nSummary: {s['connected']} connected, {s['degraded']} degraded, "
                  f"{s['disconnected']} disconnected, {s['unknown']} unknown")
        sys.exit(0)

    if args.list:
        data = get_status_summary()
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            for rec in data["integrations"]:
                print(f"  {rec['system_name']:<25} status={rec.get('status')}"
                      f"  health={rec.get('health_status')}")
        sys.exit(0)

    if args.update:
        if not args.status:
            parser.error("--status required with --update")
        result = update_integration_status(args.update, args.status, args.error)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Updated {args.update} → {args.status}")
        sys.exit(0)

    parser.print_help()


if __name__ == "__main__":
    _cli()
