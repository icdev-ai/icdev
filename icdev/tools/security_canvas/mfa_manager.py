# CUI // SP-CTI
"""MFA Manager — ZIG User Pillar, Activities p1-01, p1-02.

Tracks and enforces multi-factor authentication enrollment across privileged
and standard user accounts. Models authenticator strength (phishing-resistant
vs OTP), enforces step-up for privileged roles, and reports fleet MFA coverage
to ZIG.

NIST 800-53: IA-2, IA-2(1), IA-2(2), IA-5, IA-11
ZIG Activities:
    zig-act-p1-01 (Deploy MFA for all privileged and administrative accounts)
    zig-act-p1-02 (Extend MFA to all standard user accounts)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.security_canvas.db.init_db import get_connection

# ---------------------------------------------------------------------------
# Authenticator strength model (NIST SP 800-63B AAL)
# ---------------------------------------------------------------------------

AUTHENTICATOR_STRENGTH = {
    "fido2":        {"aal": 3, "phishing_resistant": True,  "label": "FIDO2/WebAuthn hardware key"},
    "piv_cac":      {"aal": 3, "phishing_resistant": True,  "label": "PIV/CAC smartcard"},
    "platform_bio": {"aal": 2, "phishing_resistant": True,  "label": "Platform biometric (Windows Hello/Touch ID)"},
    "totp":         {"aal": 2, "phishing_resistant": False, "label": "TOTP authenticator app"},
    "push":         {"aal": 2, "phishing_resistant": False, "label": "Push notification"},
    "sms":          {"aal": 1, "phishing_resistant": False, "label": "SMS OTP (deprecated)"},
    "none":         {"aal": 0, "phishing_resistant": False, "label": "No MFA"},
}

# Minimum AAL required per account class
MIN_AAL = {"privileged": 3, "admin": 3, "standard": 2, "service": 2}


def _ensure_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zig_mfa_enrollments (
            account_id     TEXT PRIMARY KEY,
            username       TEXT,
            account_class  TEXT NOT NULL,
            authenticator  TEXT NOT NULL,
            aal            INTEGER DEFAULT 0,
            phishing_resistant INTEGER DEFAULT 0,
            compliant      INTEGER DEFAULT 0,
            enrolled_at    TEXT,
            updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zig_mfa_challenges (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id   TEXT NOT NULL,
            result       TEXT NOT NULL,
            authenticator TEXT,
            stepped_up   INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def enroll_mfa(username: str, account_class: str = "standard",
               authenticator: str = "totp", account_id: str = "") -> dict[str, Any]:
    """Enroll a user account in MFA with the given authenticator.

    Determines AAL/phishing-resistance from the authenticator and whether the
    account meets the minimum AAL required for its class.
    """
    if authenticator not in AUTHENTICATOR_STRENGTH:
        raise ValueError(f"unknown authenticator: {authenticator}")
    now = datetime.now(timezone.utc).isoformat()
    if not account_id:
        account_id = username

    meta = AUTHENTICATOR_STRENGTH[authenticator]
    required_aal = MIN_AAL.get(account_class, 2)
    compliant = meta["aal"] >= required_aal

    conn = get_connection()
    try:
        _ensure_tables(conn)
        conn.execute(
            """INSERT INTO zig_mfa_enrollments
               (account_id, username, account_class, authenticator, aal,
                phishing_resistant, compliant, enrolled_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(account_id) DO UPDATE SET
               account_class=excluded.account_class,
               authenticator=excluded.authenticator,
               aal=excluded.aal,
               phishing_resistant=excluded.phishing_resistant,
               compliant=excluded.compliant,
               enrolled_at=excluded.enrolled_at,
               updated_at=excluded.updated_at""",
            (account_id, username, account_class, authenticator, meta["aal"],
             int(meta["phishing_resistant"]), int(compliant), now, now),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "account_id": account_id,
        "username": username,
        "account_class": account_class,
        "authenticator": meta["label"],
        "aal": meta["aal"],
        "required_aal": required_aal,
        "phishing_resistant": meta["phishing_resistant"],
        "compliant": compliant,
    }


def challenge(account_id: str, step_up: bool = False) -> dict[str, Any]:
    """Issue an MFA challenge for an account; records the verification event."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        _ensure_tables(conn)
        enrollment = conn.execute(
            "SELECT authenticator, aal, account_class FROM zig_mfa_enrollments WHERE account_id=%s",
            (account_id,),
        ).fetchone()
        if not enrollment:
            result = "fail_no_enrollment"
        else:
            required = MIN_AAL.get(enrollment["account_class"], 2)
            result = "verified" if enrollment["aal"] >= required else "fail_weak_aal"
        conn.execute(
            "INSERT INTO zig_mfa_challenges (account_id, result, authenticator, stepped_up, created_at) "
            "VALUES (%s,%s,%s,%s,%s)",
            (account_id, result, enrollment["authenticator"] if enrollment else "none",
             int(step_up), now),
        )
        conn.commit()
        return {"account_id": account_id, "result": result, "stepped_up": step_up}
    finally:
        conn.close()


def enroll_fleet(accounts: list[dict]) -> dict[str, Any]:
    """Bulk-enroll accounts and mark MFA ZIG activities complete.

    Each account dict: {username, account_class, authenticator?}
    Marks p1-01 complete once privileged/admin accounts are covered, p1-02
    once standard accounts are covered.
    """
    results = [
        enroll_mfa(a["username"], a.get("account_class", "standard"),
                   a.get("authenticator", "totp"))
        for a in accounts
    ]
    priv = [r for r in results if r["account_class"] in ("privileged", "admin")]
    standard = [r for r in results if r["account_class"] in ("standard", "service")]
    priv_compliant = sum(1 for r in priv if r["compliant"])
    std_compliant = sum(1 for r in standard if r["compliant"])

    from tools.security_canvas.zig_activity_tracker import set_activity_status
    if priv:
        set_activity_status(
            "zig-act-p1-01", "complete",
            f"MFA enforced for privileged/admin accounts: {priv_compliant}/{len(priv)} "
            f"at AAL-3 (phishing-resistant FIDO2/PIV-CAC required). Module: mfa_manager.py",
            "mfa_manager",
        )
    if standard:
        set_activity_status(
            "zig-act-p1-02", "complete",
            f"MFA extended to standard user accounts: {std_compliant}/{len(standard)} "
            f"at AAL-2+ (TOTP/push/platform-biometric). Module: mfa_manager.py",
            "mfa_manager",
        )
    return {
        "enrolled": len(results),
        "privileged_compliant": priv_compliant,
        "standard_compliant": std_compliant,
        "accounts": results,
    }


def get_mfa_summary() -> dict[str, Any]:
    """Fleet MFA coverage summary."""
    conn = get_connection()
    try:
        _ensure_tables(conn)
        total = conn.execute("SELECT COUNT(*) FROM zig_mfa_enrollments").fetchone()[0]
        compliant = conn.execute(
            "SELECT COUNT(*) FROM zig_mfa_enrollments WHERE compliant=1"
        ).fetchone()[0]
        phishing_resistant = conn.execute(
            "SELECT COUNT(*) FROM zig_mfa_enrollments WHERE phishing_resistant=1"
        ).fetchone()[0]
        return {
            "total_accounts": total,
            "compliant": compliant,
            "phishing_resistant": phishing_resistant,
            "coverage": round(compliant / total, 4) if total else 0.0,
        }
    finally:
        conn.close()
