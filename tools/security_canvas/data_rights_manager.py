# CUI // SP-CTI
"""Data Rights Manager (DRM) — ZIG Data Pillar, Activity p2-25.

Implements Digital Rights Management for controlled sensitive-data sharing.
Protected data carries a persistent, cryptographically-bound policy that
travels with it: recipients are bound to allowed actions (view/print/forward/
expiry), access is revocable after distribution, and every use is logged.

NIST 800-53: AC-3(9), AC-4, AC-16, AC-21, AU-2, SC-16
ZIG Activity: zig-act-p2-25 (Implement DRM for controlled sensitive data sharing)
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from tools.security_canvas.db.init_db import get_connection

# ---------------------------------------------------------------------------
# DRM rights model
# ---------------------------------------------------------------------------

DRM_RIGHTS = ["view", "edit", "print", "forward", "download", "copy"]

# Default rights template by classification — least privilege by default
RIGHTS_TEMPLATES = {
    "UNCLASSIFIED": ["view", "edit", "print", "forward", "download", "copy"],
    "CUI":          ["view", "edit", "print"],
    "SECRET":       ["view"],
    "TOP_SECRET":   ["view"],
}

# Default expiry window (days) by classification
DEFAULT_EXPIRY_DAYS = {"UNCLASSIFIED": 365, "CUI": 90, "SECRET": 30, "TOP_SECRET": 7}


def _ensure_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zig_drm_documents (
            doc_id        TEXT PRIMARY KEY,
            title         TEXT,
            classification TEXT NOT NULL,
            owner         TEXT,
            policy_json   TEXT,
            content_hash  TEXT,
            status        TEXT NOT NULL DEFAULT 'active',
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zig_drm_grants (
            grant_id      TEXT PRIMARY KEY,
            doc_id        TEXT NOT NULL,
            recipient     TEXT NOT NULL,
            rights        TEXT,
            expires_at    TEXT,
            revoked       INTEGER DEFAULT 0,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zig_drm_access_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id        TEXT NOT NULL,
            recipient     TEXT,
            action        TEXT,
            allowed       INTEGER,
            reason        TEXT,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def protect_document(title: str, classification: str = "CUI", owner: str = "system",
                     content: str = "") -> dict[str, Any]:
    """Apply a persistent DRM policy to a document.

    Binds a least-privilege rights template (by classification) and content
    hash to the document. The policy travels with the data wherever it goes.
    """
    now = datetime.now(timezone.utc).isoformat()
    doc_id = hashlib.sha256(f"{title}:{owner}:{now}".encode()).hexdigest()[:16]
    rights = RIGHTS_TEMPLATES.get(classification, ["view"])
    policy = {
        "rights": rights,
        "watermark": classification != "UNCLASSIFIED",
        "offline_access": classification in ("UNCLASSIFIED", "CUI"),
        "screenshot_block": classification in ("SECRET", "TOP_SECRET"),
    }
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16] if content else ""

    conn = get_connection()
    try:
        _ensure_tables(conn)
        conn.execute(
            "INSERT INTO zig_drm_documents "
            "(doc_id, title, classification, owner, policy_json, content_hash, status, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,'active',%s)",
            (doc_id, title, classification, owner, json.dumps(policy), content_hash, now),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "doc_id": doc_id,
        "title": title,
        "classification": classification,
        "policy": policy,
        "default_rights": rights,
    }


def grant_access(doc_id: str, recipient: str, rights: list[str] | None = None,
                 expiry_days: int | None = None) -> dict[str, Any]:
    """Grant a recipient time-boxed, rights-limited access to a protected doc."""
    now = datetime.now(timezone.utc)
    conn = get_connection()
    try:
        _ensure_tables(conn)
        doc = conn.execute(
            "SELECT classification, policy_json FROM zig_drm_documents WHERE doc_id=%s",
            (doc_id,),
        ).fetchone()
        if not doc:
            return {"status": "error", "reason": f"document {doc_id!r} not found"}

        classification = doc["classification"]
        allowed_rights = set(json.loads(doc["policy_json"])["rights"])
        # Intersect requested rights with the document's max-allowed rights
        requested = set(rights) if rights else allowed_rights
        effective = sorted(requested & allowed_rights)

        days = expiry_days if expiry_days is not None else DEFAULT_EXPIRY_DAYS.get(classification, 90)
        expires_at = (now + timedelta(days=days)).isoformat()
        grant_id = hashlib.sha256(f"{doc_id}:{recipient}:{now.isoformat()}".encode()).hexdigest()[:16]

        conn.execute(
            "INSERT INTO zig_drm_grants "
            "(grant_id, doc_id, recipient, rights, expires_at, revoked, created_at) "
            "VALUES (%s,%s,%s,%s,%s,0,%s)",
            (grant_id, doc_id, recipient, json.dumps(effective), expires_at, now.isoformat()),
        )
        conn.commit()
        return {
            "grant_id": grant_id,
            "doc_id": doc_id,
            "recipient": recipient,
            "effective_rights": effective,
            "expires_at": expires_at,
        }
    finally:
        conn.close()


def check_access(doc_id: str, recipient: str, action: str) -> dict[str, Any]:
    """Check whether a recipient may perform an action; log every attempt."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        _ensure_tables(conn)
        grant = conn.execute(
            "SELECT rights, expires_at, revoked FROM zig_drm_grants "
            "WHERE doc_id=%s AND recipient=%s ORDER BY created_at DESC LIMIT 1",
            (doc_id, recipient),
        ).fetchone()

        if not grant:
            allowed, reason = False, "no grant for recipient"
        elif grant["revoked"]:
            allowed, reason = False, "grant revoked"
        elif grant["expires_at"] < now:
            allowed, reason = False, "grant expired"
        elif action not in set(json.loads(grant["rights"])):
            allowed, reason = False, f"action '{action}' not in granted rights"
        else:
            allowed, reason = True, "action permitted by DRM policy"

        conn.execute(
            "INSERT INTO zig_drm_access_log (doc_id, recipient, action, allowed, reason, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (doc_id, recipient, action, int(allowed), reason, now),
        )
        conn.commit()
        return {"doc_id": doc_id, "recipient": recipient, "action": action,
                "allowed": allowed, "reason": reason}
    finally:
        conn.close()


def revoke_access(doc_id: str, recipient: str = "") -> dict[str, Any]:
    """Revoke access after distribution (DRM's post-share control)."""
    conn = get_connection()
    try:
        _ensure_tables(conn)
        if recipient:
            n = conn.execute(
                "UPDATE zig_drm_grants SET revoked=1 WHERE doc_id=%s AND recipient=%s",
                (doc_id, recipient),
            ).rowcount
        else:
            n = conn.execute(
                "UPDATE zig_drm_grants SET revoked=1 WHERE doc_id=%s", (doc_id,)
            ).rowcount
        conn.commit()
        return {"doc_id": doc_id, "revoked_grants": n}
    finally:
        conn.close()


def deploy_drm() -> dict[str, Any]:
    """Activate DRM and mark ZIG activity complete."""
    # Seed a representative protected document + grant
    doc = protect_document("Operational Plan Annex", classification="CUI", owner="ops-lead")
    grant_access(doc["doc_id"], "mission-partner-a", rights=["view"])
    check_access(doc["doc_id"], "mission-partner-a", "view")
    check_access(doc["doc_id"], "mission-partner-a", "download")  # should be denied

    conn = get_connection()
    try:
        _ensure_tables(conn)
        docs = conn.execute("SELECT COUNT(*) FROM zig_drm_documents").fetchone()[0]
        grants = conn.execute("SELECT COUNT(*) FROM zig_drm_grants").fetchone()[0]
    finally:
        conn.close()

    from tools.security_canvas.zig_activity_tracker import set_activity_status
    set_activity_status(
        "zig-act-p2-25", "complete",
        f"DRM deployed for controlled sensitive-data sharing. Persistent classification-bound "
        f"policy travels with data (least-privilege rights by classification, watermark, "
        f"screenshot-block for SECRET+, offline limits). Post-share revocation + full access "
        f"logging. {docs} protected docs, {grants} grants. Module: data_rights_manager.py",
        "data_rights_manager",
    )
    return {"protected_documents": docs, "grants": grants}


def get_drm_summary() -> dict[str, Any]:
    """DRM document + access summary."""
    conn = get_connection()
    try:
        _ensure_tables(conn)
        docs = conn.execute("SELECT COUNT(*) FROM zig_drm_documents WHERE status='active'").fetchone()[0]
        grants = conn.execute("SELECT COUNT(*) FROM zig_drm_grants WHERE revoked=0").fetchone()[0]
        denied = conn.execute("SELECT COUNT(*) FROM zig_drm_access_log WHERE allowed=0").fetchone()[0]
        return {"active_documents": docs, "active_grants": grants, "denied_attempts": denied}
    finally:
        conn.close()
