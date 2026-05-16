#!/usr/bin/env python3
# CUI // SP-PROPIN
"""Exclusion list service: sensitive term masking and merge-back.

Maintains a list of sensitive terms (personnel names, program names,
proprietary capabilities, locations) and replaces them with typed
placeholders during AI generation. The original terms are restored
at export time via merge-back.

Placeholders follow the pattern [TYPE_N], e.g.:
  [PERSON_1], [PROGRAM_1], [LOCATION_1], [CAPABILITY_1]
"""

import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

_TYPE_PREFIXES = {
    "person": "PERSON",
    "program": "PROGRAM",
    "location": "LOCATION",
    "capability": "CAP",
    "organization": "ORG",
    "custom": "TERM",
}


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


# ── CRUD ──────────────────────────────────────────────────────────────────────

def add_term(sensitive_term: str, term_type: str = "custom",
             context_notes: Optional[str] = None,
             created_by: Optional[str] = None,
             case_sensitive: bool = False,
             whole_word: bool = True) -> dict:
    """Add a sensitive term to the exclusion list.

    Auto-generates a placeholder in the format [PREFIX_N] where N is the
    next sequence number for that term_type.
    """
    if term_type not in _TYPE_PREFIXES:
        term_type = "custom"
    prefix = _TYPE_PREFIXES[term_type]

    conn = _conn()
    try:
        # Check if term already exists
        existing = conn.execute(
            "SELECT id, placeholder FROM rfx_exclusion_list "
            "WHERE sensitive_term = ? AND is_active = 1",
            (sensitive_term,)
        ).fetchone()
        if existing:
            return {"status": "exists", "id": existing["id"],
                    "placeholder": existing["placeholder"]}

        # Auto-number the placeholder
        count_row = conn.execute(
            "SELECT COUNT(*) as n FROM rfx_exclusion_list WHERE term_type = ?",
            (term_type,)
        ).fetchone()
        n = (count_row["n"] or 0) + 1
        placeholder = f"[{prefix}_{n}]"

        entry_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO rfx_exclusion_list
                (id, sensitive_term, placeholder, term_type,
                 case_sensitive, whole_word, is_active,
                 context_notes, created_by, created_at)
            VALUES (?,?,?,?,?,?,1,?,?,?)
        """, (
            entry_id, sensitive_term, placeholder, term_type,
            int(case_sensitive), int(whole_word),
            context_notes, created_by, now,
        ))
        conn.commit()
        return {"status": "ok", "id": entry_id,
                "placeholder": placeholder, "term_type": term_type}
    finally:
        conn.close()


def list_terms(term_type: Optional[str] = None,
               active_only: bool = True) -> list[dict]:
    """List all exclusion list entries."""
    conn = _conn()
    try:
        sql = "SELECT * FROM rfx_exclusion_list WHERE 1=1"
        params: list = []
        if active_only:
            sql += " AND is_active = 1"
        if term_type:
            sql += " AND term_type = ?"
            params.append(term_type)
        sql += " ORDER BY term_type, created_at"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def remove_term(entry_id: str) -> bool:
    """Soft-delete an exclusion list entry."""
    conn = _conn()
    try:
        conn.execute(
            "UPDATE rfx_exclusion_list SET is_active = 0 WHERE id = ?",
            (entry_id,)
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


# ── masking ────────────────────────────────────────────────────────────────────

def apply_mask(text: str) -> tuple[str, dict[str, str]]:
    """Replace all active sensitive terms in text with placeholders.

    Returns (masked_text, mapping) where mapping is {placeholder: original}.
    """
    terms = list_terms(active_only=True)
    mapping: dict[str, str] = {}
    masked = text

    # Sort by length desc to handle longer terms first (prevents partial matches)
    terms.sort(key=lambda t: len(t["sensitive_term"]), reverse=True)

    for entry in terms:
        term = entry["sensitive_term"]
        placeholder = entry["placeholder"]
        flags = 0 if entry["case_sensitive"] else re.IGNORECASE

        if entry["whole_word"]:
            pattern = r"\b" + re.escape(term) + r"\b"
        else:
            pattern = re.escape(term)

        if re.search(pattern, masked, flags=flags):
            mapping[placeholder] = term
            masked = re.sub(pattern, placeholder, masked, flags=flags)

    return masked, mapping


def merge_back(text: str, mapping: Optional[dict[str, str]] = None) -> str:
    """Restore all placeholders to their original sensitive terms.

    If mapping is None, loads all active terms from the DB.
    """
    if mapping is None:
        terms = list_terms(active_only=True)
        mapping = {t["placeholder"]: t["sensitive_term"] for t in terms}

    result = text
    for placeholder, original in mapping.items():
        result = result.replace(placeholder, original)
    return result


def preview_mask(text: str) -> dict:
    """Preview what would be masked without modifying the DB."""
    masked, mapping = apply_mask(text)
    return {
        "original_length": len(text),
        "masked_length": len(masked),
        "replacements": len(mapping),
        "mapping": mapping,
        "masked_preview": masked[:500] + ("..." if len(masked) > 500 else ""),
    }
