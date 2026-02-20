#!/usr/bin/env python3
# CUI // SP-CTI
"""User Binder — Identity binding between messaging channel users and ICDEV users.

Implements the binding ceremony:
  Connected: User sends /bind in channel -> challenge code -> user verifies in dashboard/API key
  Air-gapped: Admin pre-provisions bindings via CLI

Decision D136: User binding is mandatory before any command execution.
"""

import logging
import secrets
import sqlite3
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"

logger = logging.getLogger("icdev.gateway.user_binder")

# Graceful audit import
try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    def audit_log_event(**kwargs):
        logger.debug("audit_logger unavailable — skipping: %s", kwargs.get("action", ""))

# In-memory challenge store (challenges are ephemeral, no DB needed)
# Key: challenge_code -> {channel, channel_user_id, created_at, expires_at}
_ACTIVE_CHALLENGES: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open DB connection with row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Binding Ceremony — Connected Environment
# ---------------------------------------------------------------------------

def create_challenge(channel: str, channel_user_id: str,
                     ttl_minutes: int = 10) -> str:
    """Create a one-time challenge code for binding.

    Args:
        channel: Channel name (telegram, slack, etc.)
        channel_user_id: Platform-specific user ID
        ttl_minutes: Challenge validity in minutes

    Returns:
        Challenge code string (8-char hex)
    """
    # Clean expired challenges
    _cleanup_expired_challenges()

    # Generate challenge
    code = secrets.token_hex(4).upper()  # 8 char hex
    now = datetime.now(timezone.utc)

    _ACTIVE_CHALLENGES[code] = {
        "channel": channel,
        "channel_user_id": channel_user_id,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
    }

    logger.info("Challenge created for %s:%s — code=%s (expires in %d min)",
                channel, channel_user_id, code, ttl_minutes)
    return code


def verify_challenge(code: str, icdev_user_id: str,
                     tenant_id: str = "",
                     db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Verify a challenge code and create the binding.

    Called from dashboard or API when user enters the challenge code
    along with their ICDEV identity.

    Args:
        code: Challenge code from create_challenge()
        icdev_user_id: ICDEV user ID to bind to
        tenant_id: SaaS tenant ID
        db_path: Optional database path

    Returns:
        {"success": True/False, "binding_id": str, "error": str}
    """
    code = code.strip().upper()

    # Check challenge exists
    challenge = _ACTIVE_CHALLENGES.get(code)
    if not challenge:
        return {"success": False, "error": "Invalid or expired challenge code"}

    # Check expiry
    expires_at = datetime.fromisoformat(challenge["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        del _ACTIVE_CHALLENGES[code]
        return {"success": False, "error": "Challenge code expired"}

    channel = challenge["channel"]
    channel_user_id = challenge["channel_user_id"]

    # Create binding
    binding_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn = _get_db(db_path)
    try:
        # Check for existing binding
        existing = conn.execute(
            "SELECT id, binding_status FROM remote_user_bindings "
            "WHERE channel = ? AND channel_user_id = ?",
            (channel, channel_user_id)
        ).fetchone()

        if existing:
            if existing["binding_status"] == "active":
                return {"success": False,
                        "error": f"Channel user already bound (binding {existing['id']})"}
            # Update revoked/pending binding
            conn.execute(
                "UPDATE remote_user_bindings SET icdev_user_id = ?, tenant_id = ?, "
                "binding_status = 'active', bound_at = ? WHERE id = ?",
                (icdev_user_id, tenant_id, now, existing["id"])
            )
            binding_id = existing["id"]
        else:
            conn.execute(
                "INSERT INTO remote_user_bindings "
                "(id, channel, channel_user_id, icdev_user_id, tenant_id, "
                " binding_status, bound_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
                (binding_id, channel, channel_user_id, icdev_user_id,
                 tenant_id, now, now)
            )

        conn.commit()

        # Remove used challenge
        del _ACTIVE_CHALLENGES[code]

        logger.info("Binding created: %s -> %s:%s",
                     icdev_user_id, channel, channel_user_id)
        result = {"success": True, "binding_id": binding_id}

    except Exception as e:
        logger.error("Failed to create binding: %s", e)
        result = {"success": False, "error": str(e)}
    finally:
        conn.close()

    # Best-effort audit (outside DB transaction)
    if result.get("success"):
        try:
            audit_log_event(
                event_type="remote_binding_created",
                actor=icdev_user_id,
                action=f"Bound {channel}:{channel_user_id} to ICDEV user {icdev_user_id}",
            )
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# Binding Ceremony — Air-gapped (Admin pre-provision)
# ---------------------------------------------------------------------------

def provision_binding(channel: str, channel_user_id: str,
                      icdev_user_id: str, tenant_id: str = "",
                      db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Admin-provision a binding directly (air-gapped / CAC/PIV).

    Used when there's no interactive binding ceremony (e.g., admin
    pre-configures bindings from a known user list).

    Returns:
        {"success": True/False, "binding_id": str, "error": str}
    """
    binding_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn = _get_db(db_path)
    try:
        # Upsert
        existing = conn.execute(
            "SELECT id FROM remote_user_bindings "
            "WHERE channel = ? AND channel_user_id = ?",
            (channel, channel_user_id)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE remote_user_bindings SET icdev_user_id = ?, tenant_id = ?, "
                "binding_status = 'active', bound_at = ? WHERE id = ?",
                (icdev_user_id, tenant_id, now, existing["id"])
            )
            binding_id = existing["id"]
        else:
            conn.execute(
                "INSERT INTO remote_user_bindings "
                "(id, channel, channel_user_id, icdev_user_id, tenant_id, "
                " binding_status, bound_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
                (binding_id, channel, channel_user_id, icdev_user_id,
                 tenant_id, now, now)
            )

        conn.commit()
        result = {"success": True, "binding_id": binding_id}

    except Exception as e:
        logger.error("Failed to provision binding: %s", e)
        result = {"success": False, "error": str(e)}
    finally:
        conn.close()

    # Best-effort audit (outside DB transaction)
    if result.get("success"):
        try:
            audit_log_event(
                event_type="remote_binding_provisioned",
                actor="admin",
                action=f"Pre-provisioned {channel}:{channel_user_id} -> {icdev_user_id}",
            )
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# Lookup & Management
# ---------------------------------------------------------------------------

def resolve_binding(channel: str, channel_user_id: str,
                    db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Resolve a channel user to their ICDEV binding.

    Returns:
        Binding dict if active, None otherwise.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM remote_user_bindings "
            "WHERE channel = ? AND channel_user_id = ? AND binding_status = 'active'",
            (channel, channel_user_id)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def revoke_binding(binding_id: str, reason: str = "",
                   db_path: Optional[Path] = None) -> bool:
    """Revoke an active binding.

    Args:
        binding_id: Binding UUID to revoke
        reason: Optional reason for revocation

    Returns:
        True if revoked, False if not found
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_db(db_path)
    try:
        result = conn.execute(
            "UPDATE remote_user_bindings SET binding_status = 'revoked', "
            "revoked_at = ? WHERE id = ? AND binding_status = 'active'",
            (now, binding_id)
        )
        conn.commit()

        revoked = result.rowcount > 0
        if revoked:
            try:
                audit_log_event(
                    event_type="remote_binding_revoked",
                    actor="admin",
                    action=f"Revoked binding {binding_id}: {reason}",
                )
            except Exception:
                pass
        return revoked
    finally:
        conn.close()


def list_bindings(channel: str = "", status: str = "",
                  db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """List bindings, optionally filtered by channel and/or status.

    Returns:
        List of binding dicts.
    """
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM remote_user_bindings WHERE 1=1"
        params = []
        if channel:
            query += " AND channel = ?"
            params.append(channel)
        if status:
            query += " AND binding_status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cleanup_expired_challenges():
    """Remove expired challenges from in-memory store."""
    now = datetime.now(timezone.utc)
    expired = [
        code for code, data in _ACTIVE_CHALLENGES.items()
        if datetime.fromisoformat(data["expires_at"]) < now
    ]
    for code in expired:
        del _ACTIVE_CHALLENGES[code]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI for managing remote user bindings."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="ICDEV Remote User Binding Manager")
    parser.add_argument("--provision", action="store_true",
                        help="Pre-provision a binding (air-gapped)")
    parser.add_argument("--revoke", type=str, help="Revoke binding by ID")
    parser.add_argument("--list", action="store_true", help="List all bindings")
    parser.add_argument("--channel", type=str, default="", help="Filter by channel")
    parser.add_argument("--channel-user-id", type=str, default="")
    parser.add_argument("--icdev-user-id", type=str, default="")
    parser.add_argument("--tenant-id", type=str, default="")
    parser.add_argument("--status", type=str, default="")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.provision:
        result = provision_binding(
            channel=args.channel,
            channel_user_id=args.channel_user_id,
            icdev_user_id=args.icdev_user_id,
            tenant_id=args.tenant_id,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Binding {'created' if result['success'] else 'failed'}: "
                  f"{result.get('binding_id', result.get('error', ''))}")

    elif args.revoke:
        ok = revoke_binding(args.revoke)
        print(f"Revoked: {ok}")

    elif args.list:
        bindings = list_bindings(channel=args.channel, status=args.status)
        if args.json:
            print(json.dumps(bindings, indent=2, default=str))
        else:
            for b in bindings:
                print(f"  {b['id'][:8]}  {b['channel']:15s}  "
                      f"{b['channel_user_id']:20s}  -> {b.get('icdev_user_id', 'unbound'):20s}  "
                      f"[{b['binding_status']}]")


if __name__ == "__main__":
    main()
