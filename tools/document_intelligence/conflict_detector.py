# CUI // SP-CTI
"""DIC Section Conflict Detector — optimistic-concurrency check on content saves.

When a user opens a section for editing the client captures a content fingerprint
(CRC32 hex).  On save the fingerprint is sent back.  If the DB copy has changed
since then, a 409 is returned with the current content so the client can show a
resolution modal.

CRC32 (zlib.crc32) is used deliberately — this is a change-detection fingerprint,
not a cryptographic hash, and avoids SIPA's _CRYPTO_HASHLIB false-positive rule.

Usage::

    from tools.document_intelligence.conflict_detector import (
        compute_hash, get_section_state, check_conflict,
    )

    h = compute_hash("section text here")
    result = check_conflict(conn, section_id, expected_hash=h)
    if result["conflict"]:
        # 409 — someone else saved while you were editing
        ...
"""
from __future__ import annotations

import zlib


def compute_hash(content: str) -> str:
    """Return a hex CRC32 fingerprint of *content*."""
    return format(zlib.crc32(content.encode("utf-8", errors="replace")) & 0xFFFFFFFF, "08x")


def get_section_state(conn, section_id: str) -> dict | None:
    """Fetch the current content + hash for *section_id*.

    Returns ``{"content": ..., "hash": ...}`` or None if the section doesn't exist.
    Uses the caller's existing connection — no new connection opened.
    """
    row = conn.execute(
        "SELECT content FROM dic_sections WHERE section_id = %s LIMIT 1",
        (section_id,),
    ).fetchone()
    if row is None:
        return None
    current_content = dict(row).get("content") or ""
    return {"content": current_content, "hash": compute_hash(current_content)}


def check_conflict(conn, section_id: str, expected_hash: str) -> dict:
    """Compare *expected_hash* against the live DB content hash.

    Returns::

        {
          "conflict": bool,
          "current_hash": str,        # live DB hash
          "current_content": str,     # live DB content (for merge UI)
        }
    """
    state = get_section_state(conn, section_id)
    if state is None:
        # Section not found — treat as no conflict so save can proceed
        return {"conflict": False, "current_hash": expected_hash, "current_content": ""}
    current_hash = state["hash"]
    return {
        "conflict": current_hash != expected_hash,
        "current_hash": current_hash,
        "current_content": state["content"],
    }
