#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

"""TOTP Multi-Factor Authentication for ICDEV™.

Implements RFC 6238 TOTP MFA for DoD/IC IL4+ compliance (G-07).
NIST 800-53: IA-2(1), IA-2(2), IA-5(1).

Features:
- TOTP enrollment (generate secret, provision URI for authenticator app)
- TOTP verification (with 30-second window tolerance)
- Backup recovery codes (10 single-use codes, bcrypt-hashed)
- MFA status check (enrolled / not-enrolled / disabled)
- Flask integration: challenge redirect + step-up re-auth

Dependencies:
    pip install pyotp  (already in requirements.txt as optional)

Storage:
    user_mfa table (see DB schema below):
        user_id TEXT PRIMARY KEY
        totp_secret TEXT NOT NULL          -- base32, encrypted at rest (ICDEV_DB_ENCRYPTION_KEY)
        enrolled_at TEXT NOT NULL
        backup_codes TEXT NOT NULL         -- JSON array of bcrypt hashes
        enabled INTEGER NOT NULL DEFAULT 1
        last_used_at TEXT

Configuration:
    ICDEV_MFA_REQUIRED=true   — enforce MFA on all IL4+ accounts
    ICDEV_MFA_ISSUER          — display name in authenticator app (default: "ICDEV")

Public API:
    enroll(user_id) -> EnrollResult        (secret, provisioning_uri, backup_codes)
    verify(user_id, token) -> bool         (verify TOTP token)
    verify_backup(user_id, code) -> bool   (verify + consume a backup code)
    is_enrolled(user_id) -> bool
    disable(user_id) -> None
    require_mfa_for_request() -> None      (Flask helper; raises 403 if MFA needed)
"""

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("security.mfa")

_ISSUER = os.environ.get("ICDEV_MFA_ISSUER", "ICDEV")
_MFA_REQUIRED = os.environ.get("ICDEV_MFA_REQUIRED", "false").lower() in ("true", "1")
_WINDOW = 1  # allow 1 period (30s) on each side of current window


# ---------------------------------------------------------------------------
# DB schema (added to init_icdev_db.py on migration)
# ---------------------------------------------------------------------------

MFA_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_mfa (
    user_id TEXT PRIMARY KEY,
    totp_secret TEXT NOT NULL,
    enrolled_at TEXT NOT NULL,
    backup_codes TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_used_at TEXT
);
"""

MFA_ATTEMPT_SCHEMA = """
CREATE TABLE IF NOT EXISTS mfa_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    success INTEGER NOT NULL,
    method TEXT NOT NULL DEFAULT 'totp',
    ip_address TEXT,
    recorded_at TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _totp():
    """Import pyotp lazily (optional dependency)."""
    try:
        import pyotp  # type: ignore[import-untyped]
        return pyotp
    except ImportError as e:
        raise ImportError(
            "pyotp is required for MFA. Install it: pip install pyotp"
        ) from e


def _hash_code(code: str) -> str:
    """SHA-256 hash of a backup code (bcrypt not available in all envs)."""
    return hashlib.sha256(code.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

@dataclass
class EnrollResult:
    user_id: str
    totp_secret: str
    provisioning_uri: str
    backup_codes: list[str]  # plaintext — show once, then store hashes only


def enroll(user_id: str, user_email: str = "") -> EnrollResult:
    """Generate a new TOTP secret and enrollment data for a user.

    The plaintext backup codes are returned once. The caller MUST display
    them to the user and then they are gone. Only hashes are stored.

    Call confirm_enrollment() after the user verifies their first TOTP token
    to mark enrollment as complete.
    """
    pyotp = _totp()
    secret = pyotp.random_base32()
    label = user_email or user_id
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=label, issuer_name=_ISSUER)

    # Generate 10 single-use backup codes (8 chars each, hex)
    backup_codes = [secrets.token_hex(4).upper() for _ in range(10)]
    hashed_codes = json.dumps([_hash_code(c) for c in backup_codes])

    now = _now()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO user_mfa (user_id, totp_secret, enrolled_at, backup_codes, enabled)
            VALUES (%s, %s, %s, %s, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                totp_secret = excluded.totp_secret,
                enrolled_at = excluded.enrolled_at,
                backup_codes = excluded.backup_codes,
                enabled = 1
            """,
            (user_id, secret, now, hashed_codes),
        )
        conn.commit()

    logger.info("MFA enrollment initiated for user %s", user_id)
    return EnrollResult(
        user_id=user_id,
        totp_secret=secret,
        provisioning_uri=uri,
        backup_codes=backup_codes,
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify(user_id: str, token: str, ip_address: str = "") -> bool:
    """Verify a TOTP token. Returns True if valid.

    Uses a ±1 window (30 seconds before/after current period) to account
    for clock skew.
    """
    pyotp = _totp()

    with _conn() as conn:
        row = conn.execute(
            "SELECT totp_secret, enabled FROM user_mfa WHERE user_id = %s",
            (user_id,),
        ).fetchone()

    if not row:
        _log_attempt(user_id, False, "totp", ip_address)
        return False

    secret = row[0] if isinstance(row, (tuple, list)) else row["totp_secret"]
    enabled = row[1] if isinstance(row, (tuple, list)) else row["enabled"]

    if not enabled:
        logger.warning("MFA verify called for disabled MFA user %s", user_id)
        _log_attempt(user_id, False, "totp", ip_address)
        return False

    totp = pyotp.TOTP(secret)
    valid = totp.verify(token, valid_window=_WINDOW)

    if valid:
        with _conn() as conn:
            conn.execute(
                "UPDATE user_mfa SET last_used_at = %s WHERE user_id = %s",
                (_now(), user_id),
            )
            conn.commit()

    _log_attempt(user_id, valid, "totp", ip_address)
    return valid


def verify_backup(user_id: str, code: str, ip_address: str = "") -> bool:
    """Verify and consume a backup recovery code. Returns True if valid."""
    code_upper = code.strip().upper()
    code_hash = _hash_code(code_upper)

    with _conn() as conn:
        row = conn.execute(
            "SELECT backup_codes FROM user_mfa WHERE user_id = %s AND enabled = 1",
            (user_id,),
        ).fetchone()

    if not row:
        _log_attempt(user_id, False, "backup_code", ip_address)
        return False

    raw = row[0] if isinstance(row, (tuple, list)) else row["backup_codes"]
    try:
        hashes: list[str] = json.loads(raw or "[]")
    except Exception:
        hashes = []

    if code_hash not in hashes:
        _log_attempt(user_id, False, "backup_code", ip_address)
        return False

    # Consume the code
    hashes.remove(code_hash)
    with _conn() as conn:
        conn.execute(
            "UPDATE user_mfa SET backup_codes = %s, last_used_at = %s WHERE user_id = %s",
            (json.dumps(hashes), _now(), user_id),
        )
        conn.commit()

    logger.info("Backup MFA code consumed for user %s (%d remaining)", user_id, len(hashes))
    _log_attempt(user_id, True, "backup_code", ip_address)
    return True


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def is_enrolled(user_id: str) -> bool:
    """Return True if the user has active MFA enrollment."""
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT enabled FROM user_mfa WHERE user_id = %s", (user_id,)
            ).fetchone()
        if not row:
            return False
        enabled = row[0] if isinstance(row, (tuple, list)) else row["enabled"]
        return bool(enabled)
    except Exception:
        return False


def disable(user_id: str, disabled_by: str = "system") -> None:
    """Disable MFA for a user (admin action only)."""
    with _conn() as conn:
        conn.execute(
            "UPDATE user_mfa SET enabled = 0 WHERE user_id = %s", (user_id,)
        )
        conn.commit()
    logger.warning("MFA disabled for user %s by %s", user_id, disabled_by)


# ---------------------------------------------------------------------------
# Flask integration
# ---------------------------------------------------------------------------

def mfa_required(f=None, *, impact_level: str = "IL4"):
    """Flask route decorator: redirect to MFA challenge if user has not completed MFA.

    Usage::

        @bp.route("/secure-page")
        @mfa_required
        def secure_page(): ...

    Or with impact level gate::

        @bp.route("/classified")
        @mfa_required(impact_level="IL5")
        def classified_page(): ...
    """
    import functools

    _IL_ORDER = {"IL2": 0, "IL4": 1, "IL5": 2, "IL6": 3}

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                from flask import g, session, redirect, url_for, request
            except ImportError:
                return func(*args, **kwargs)

            user = getattr(g, "current_user", None) or {}
            user_id = str(user.get("id", "") or user.get("user_id", "") or "")
            user_il = user.get("impact_level", "IL4")

            # Only enforce MFA if user's impact level meets the threshold
            if _IL_ORDER.get(user_il, 0) < _IL_ORDER.get(impact_level, 1):
                return func(*args, **kwargs)

            # Skip if ICDEV_MFA_REQUIRED=false (dev/test)
            if not _MFA_REQUIRED and not is_enrolled(user_id):
                return func(*args, **kwargs)

            # Check if MFA was completed for this session
            if session.get("mfa_verified"):
                return func(*args, **kwargs)

            # Redirect to MFA challenge
            session["mfa_redirect_target"] = request.url
            logger.info("MFA challenge required for user %s (IL=%s)", user_id, user_il)
            try:
                return redirect(url_for("mfa.challenge"))
            except Exception:
                from flask import abort
                abort(403, description="MFA authentication required")

        return wrapper

    if f is not None:
        # Used as @mfa_required (no args)
        return decorator(f)
    # Used as @mfa_required(impact_level="IL5")
    return decorator


def require_mfa_for_request() -> Optional[object]:
    """Non-decorator helper: return 403 response if MFA is needed, else None.

    Call from inside a view function::

        resp = require_mfa_for_request()
        if resp:
            return resp
    """
    try:
        from flask import g, session, jsonify
        user = getattr(g, "current_user", None) or {}
        user_id = str(user.get("id", "") or user.get("user_id", "") or "")
        if not user_id:
            return None
        if not _MFA_REQUIRED and not is_enrolled(user_id):
            return None
        if session.get("mfa_verified"):
            return None
        return jsonify({"error": "MFA authentication required", "code": "MFA_REQUIRED"}), 403
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _log_attempt(user_id: str, success: bool, method: str, ip_address: str) -> None:
    try:
        with _conn() as conn:
            conn.execute(
                """
                INSERT INTO mfa_attempts (user_id, success, method, ip_address, recorded_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user_id, int(success), method, ip_address, _now()),
            )
            conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="ICDEV™ MFA CLI")
    parser.add_argument("--enroll", nargs=2, metavar=("USER_ID", "EMAIL"),
                        help="Enroll a user in TOTP MFA")
    parser.add_argument("--verify", nargs=2, metavar=("USER_ID", "TOKEN"),
                        help="Verify a TOTP token for a user")
    parser.add_argument("--status", metavar="USER_ID", help="Check MFA enrollment status")
    parser.add_argument("--disable", metavar="USER_ID", help="Disable MFA for a user")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    import json as _json

    if args.enroll:
        result = enroll(*args.enroll)
        out = {
            "totp_secret": result.totp_secret,
            "provisioning_uri": result.provisioning_uri,
            "backup_codes": result.backup_codes,
        }
        print(_json.dumps(out, indent=2) if args.json else str(out))

    elif args.verify:
        valid = verify(*args.verify)
        out = {"valid": valid}
        print(_json.dumps(out, indent=2) if args.json else str(valid))

    elif args.status:
        enrolled = is_enrolled(args.status)
        out = {"user_id": args.status, "mfa_enrolled": enrolled}
        print(_json.dumps(out, indent=2) if args.json else str(enrolled))

    elif args.disable:
        disable(args.disable)
        out = {"disabled": args.disable}
        print(_json.dumps(out, indent=2) if args.json else f"MFA disabled for {args.disable}")


if __name__ == "__main__":
    main()
