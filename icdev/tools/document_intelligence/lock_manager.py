# CUI // SP-CTI
"""DIC Section Lock Manager — pessimistic locking for collaborative editing.

Prevents two editors from clobbering the same section simultaneously.
Uses a `dic_section_locks` DB table with TTL-based expiry (default 300s).
No WebSocket required — clients renew locks via periodic PUT requests.

Usage::

    from tools.document_intelligence.lock_manager import acquire_lock, release_lock

    lock = acquire_lock("sec_abc123", "user_a")  # returns dict or None
    ok   = release_lock("sec_abc123", "user_a")  # True if released
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from tools.db.storage import get_connection

_DEFAULT_TTL = 300  # seconds


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expires_iso(ttl: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()


def _is_expired(expires_at: str) -> bool:
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > exp
    except Exception:
        return True


# Columns added after initial deployment (idempotent ALTER TABLE).
# SQLite does not support ADD COLUMN IF NOT EXISTS, so failures are ignored.
_ALTER_MIGRATIONS = [
    ("lock_id", "TEXT"),
    ("doc_id", "TEXT"),
    ("tenant_id", "TEXT"),
    ("classification", "TEXT DEFAULT 'CUI'"),
]


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dic_section_locks (
            lock_id       TEXT PRIMARY KEY,
            section_id    TEXT NOT NULL,
            locked_by     TEXT NOT NULL,
            locked_at     TEXT NOT NULL,
            expires_at    TEXT NOT NULL,
            doc_id        TEXT,
            tenant_id     TEXT,
            classification TEXT DEFAULT 'CUI'
        )
    """)
    # Best-effort add missing columns for backward compatibility.
    for col, dtype in _ALTER_MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE dic_section_locks ADD COLUMN {col} {dtype}")
        except Exception:
            pass
    conn.commit()


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


# ── Public API ────────────────────────────────────────────────────────────────

def acquire_lock(section_id: str, user_id: str, ttl_seconds: int = _DEFAULT_TTL,
                 doc_id: str = "") -> dict | None:
    """Acquire a lock on *section_id* for *user_id*.

    Returns the lock dict on success.
    Returns None if the section is already locked by a different user.
    If the caller already holds the lock, renews it and returns the updated dict.
    """
    with get_connection() as conn:
        _ensure_table(conn)
        existing = conn.execute(
            "SELECT * FROM dic_section_locks WHERE section_id = %s LIMIT 1",
            (section_id,),
        ).fetchone()

        if existing:
            row = _row_to_dict(existing)
            if _is_expired(row["expires_at"]):
                # Stale lock — delete and let the caller acquire below
                conn.execute(
                    "DELETE FROM dic_section_locks WHERE section_id = %s", (section_id,)
                )
                conn.commit()
            elif row["locked_by"] != user_id:
                # Held by someone else and not expired
                return None
            else:
                # Caller already holds it — renew
                new_exp = _expires_iso(ttl_seconds)
                conn.execute(
                    "UPDATE dic_section_locks SET expires_at = %s WHERE section_id = %s",
                    (new_exp, section_id),
                )
                conn.commit()
                row["expires_at"] = new_exp
                return row

        lock_id = f"lk_{uuid.uuid4().hex[:16]}"
        now = _now_iso()
        exp = _expires_iso(ttl_seconds)
        conn.execute(
            """INSERT INTO dic_section_locks
               (lock_id, section_id, locked_by, locked_at, expires_at, doc_id, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (lock_id, section_id, user_id, now, exp, doc_id or "", "CUI"),
        )
        conn.commit()
        return {
            "lock_id": lock_id,
            "section_id": section_id,
            "locked_by": user_id,
            "locked_at": now,
            "expires_at": exp,
        }


def release_lock(section_id: str, user_id: str) -> bool:
    """Release the lock on *section_id* if held by *user_id*.

    Returns True if released, False if not held or held by another user.
    """
    with get_connection() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT locked_by FROM dic_section_locks WHERE section_id = %s LIMIT 1",
            (section_id,),
        ).fetchone()
        if not row or dict(row)["locked_by"] != user_id:
            return False
        conn.execute(
            "DELETE FROM dic_section_locks WHERE section_id = %s AND locked_by = %s",
            (section_id, user_id),
        )
        conn.commit()
        return True


def renew_lock(section_id: str, user_id: str, ttl_seconds: int = _DEFAULT_TTL) -> bool:
    """Extend the TTL of an existing lock held by *user_id*.

    Returns True if renewed, False if lock not found or held by another user.
    """
    with get_connection() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT locked_by FROM dic_section_locks WHERE section_id = %s LIMIT 1",
            (section_id,),
        ).fetchone()
        if not row or dict(row)["locked_by"] != user_id:
            return False
        new_exp = _expires_iso(ttl_seconds)
        conn.execute(
            "UPDATE dic_section_locks SET expires_at = %s WHERE section_id = %s AND locked_by = %s",
            (new_exp, section_id, user_id),
        )
        conn.commit()
        return True


def get_lock(section_id: str) -> dict | None:
    """Return the current active lock for *section_id*, or None if unlocked/expired."""
    with get_connection() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM dic_section_locks WHERE section_id = %s LIMIT 1",
            (section_id,),
        ).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        if _is_expired(d["expires_at"]):
            conn.execute(
                "DELETE FROM dic_section_locks WHERE section_id = %s", (section_id,)
            )
            conn.commit()
            return None
        return d


def purge_expired_locks() -> int:
    """Delete all expired lock rows. Returns count removed."""
    now = _now_iso()
    with get_connection() as conn:
        _ensure_table(conn)
        cur = conn.execute(
            "DELETE FROM dic_section_locks WHERE expires_at < %s", (now,)
        )
        conn.commit()
        return cur.rowcount if cur else 0
