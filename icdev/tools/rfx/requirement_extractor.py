#!/usr/bin/env python3
# CUI // SP-PROPIN
"""Requirement extractor: parse shall/should/must statements from RFI/RFP text.

Two extraction modes:
  1. Regex (fast, no LLM) — catches explicit modal verbs in each sentence.
  2. LLM-assisted (via llm_bridge) — catches implicit requirements and
     classifies section/priority more accurately.

Results are stored in rfx_requirements and rfx_requirement_status is
initialized to 'not_addressed' for each requirement × proposal pair.
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

# Modal verbs that signal a requirement
_SHALL_PATTERNS = [
    r"\bshall\b", r"\bmust\b", r"\bwill\b",
    r"\bshould\b", r"\bmay\b", r"\bshall not\b", r"\bmust not\b",
]
_REQ_REGEX = re.compile(
    "|".join(_SHALL_PATTERNS), re.IGNORECASE
)

# Section header patterns for RFI/RFP docs
_SECTION_L = re.compile(r"section\s+l\b", re.IGNORECASE)
_SECTION_M = re.compile(r"section\s+m\b", re.IGNORECASE)
_SOW = re.compile(r"\b(statement\s+of\s+work|sow)\b", re.IGNORECASE)
_CDRL = re.compile(r"\b(cdrl|data\s+item\s+description|dd\s+1423)\b", re.IGNORECASE)
_EVAL = re.compile(r"\b(evaluation\s+(criteria|factor)|best\s+value)\b", re.IGNORECASE)


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _detect_section(context: str) -> str:
    """Guess the RFP section from surrounding text."""
    if _SECTION_L.search(context):
        return "section_l"
    if _SECTION_M.search(context):
        return "section_m"
    if _SOW.search(context):
        return "sow"
    if _CDRL.search(context):
        return "cdrl"
    if _EVAL.search(context):
        return "evaluation_criteria"
    return "other"


def _detect_req_type(sentence: str) -> str:
    """Identify the primary modal verb in a sentence."""
    low = sentence.lower()
    if "shall not" in low or "must not" in low:
        return "shall"
    if "shall" in low:
        return "shall"
    if "must" in low:
        return "must"
    if "will" in low:
        return "will"
    if "should" in low:
        return "should"
    if "may" in low:
        return "may"
    return "other"


def _detect_priority(sentence: str, section: str) -> str:
    """Heuristic priority from modal verb + section."""
    req_type = _detect_req_type(sentence)
    if req_type in ("shall", "must") or section == "section_m":
        return "critical"
    if req_type == "will" or section == "section_l":
        return "high"
    if req_type == "should":
        return "medium"
    return "low"


def _detect_volume(section: str, sentence: str) -> Optional[str]:
    """Map requirement to a proposal volume."""
    low = sentence.lower()
    if "management" in low or "program management" in low:
        return "management"
    if "past performance" in low or "cpars" in low or "contract history" in low:
        return "past_performance"
    if "price" in low or "cost" in low or "pricing" in low:
        return "cost"
    if section in ("section_l", "sow"):
        return "technical"
    return None


# ── regex extraction ───────────────────────────────────────────────────────────

def extract_regex(text: str, doc_id: str,
                  proposal_id: Optional[str] = None) -> list[dict]:
    """Extract requirements via regex (fast, no LLM).

    Splits text into sentences and flags those containing modal verbs.
    Returns a list of requirement dicts (not yet stored to DB).
    """
    sentences = re.split(r"(?<=[.!?])\s+", text)
    requirements = []
    req_num = 1

    current_section_context = ""

    for i, sentence in enumerate(sentences):
        sentence = sentence.strip()
        if not sentence or len(sentence) < 20:
            continue

        # Update section context from surrounding text
        context_window = " ".join(sentences[max(0, i - 3):i + 1])
        section = _detect_section(context_window)

        if _REQ_REGEX.search(sentence):
            req_type = _detect_req_type(sentence)
            priority = _detect_priority(sentence, section)
            volume = _detect_volume(section, sentence)

            requirements.append({
                "id": str(uuid.uuid4()),
                "document_id": doc_id,
                "proposal_id": proposal_id,
                "req_number": f"REQ-{req_num:04d}",
                "section": section,
                "req_text": sentence,
                "req_type": req_type,
                "volume": volume,
                "priority": priority,
                "extracted_by": "ai",  # regex counts as automated
            })
            req_num += 1

    return requirements


# ── DB storage ─────────────────────────────────────────────────────────────────

def store_requirements(requirements: list[dict],
                       proposal_id: Optional[str] = None) -> int:
    """Insert extracted requirements into rfx_requirements.

    Also initializes rfx_requirement_status = 'not_addressed' for each
    requirement if proposal_id is given.

    Returns number of requirements inserted.
    """
    if not requirements:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    try:
        inserted = 0
        for req in requirements:
            # Defensive: skip empty or malformed requirements
            if not req.get("req_text") or not str(req.get("req_text", "")).strip():
                continue
            conn.execute("""
                INSERT OR IGNORE INTO rfx_requirements
                    (id, document_id, proposal_id, req_number, section,
                     req_text, req_type, volume, priority, extracted_by,
                     created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                req["id"], req["document_id"],
                req.get("proposal_id") or proposal_id,
                req.get("req_number"), req["section"],
                req["req_text"], req["req_type"],
                req.get("volume"), req["priority"],
                req.get("extracted_by", "ai"), now,
            ))

            if conn.total_changes > 0 and (req.get("proposal_id") or proposal_id):
                pid = req.get("proposal_id") or proposal_id
                conn.execute("""
                    INSERT OR IGNORE INTO rfx_requirement_status
                        (id, requirement_id, proposal_id, status, updated_at)
                    VALUES (?,?,?,'not_addressed',?)
                """, (str(uuid.uuid4()), req["id"], pid, now))
                inserted += 1
            else:
                inserted += 1

        conn.commit()
        return inserted
    finally:
        conn.close()


def extract_and_store(doc_id: str,
                      proposal_id: Optional[str] = None) -> dict:
    """Full pipeline: load doc text, extract, store requirements.

    Returns summary dict with counts by section and priority.
    """
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT content FROM rfx_documents WHERE id = ?", (doc_id,)
        ).fetchone()
    finally:
        conn.close()

    if not row or not row["content"]:
        return {"error": "Document not found or has no text", "doc_id": doc_id}

    requirements = extract_regex(row["content"], doc_id, proposal_id)
    stored = store_requirements(requirements, proposal_id)

    # Summarize
    by_section: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for r in requirements:
        by_section[r["section"]] = by_section.get(r["section"], 0) + 1
        by_priority[r["priority"]] = by_priority.get(r["priority"], 0) + 1

    return {
        "doc_id": doc_id,
        "proposal_id": proposal_id,
        "total_extracted": len(requirements),
        "stored": stored,
        "by_section": by_section,
        "by_priority": by_priority,
    }


# ── queries ────────────────────────────────────────────────────────────────────

def get_requirements(proposal_id: Optional[str] = None,
                     doc_id: Optional[str] = None,
                     section: Optional[str] = None,
                     priority: Optional[str] = None) -> list[dict]:
    """Retrieve requirements with optional filters, joined with status."""
    conn = _conn()
    try:
        sql = """
            SELECT
                r.id, r.document_id, r.proposal_id, r.req_number, r.section,
                r.req_text, r.req_type, r.volume, r.priority, r.extracted_by,
                COALESCE(rs.status, 'not_addressed') as status
            FROM rfx_requirements r
            LEFT JOIN rfx_requirement_status rs
                ON rs.requirement_id = r.id
                AND rs.proposal_id = r.proposal_id
            WHERE 1=1
        """
        params: list = []
        if proposal_id:
            sql += " AND r.proposal_id = ?"
            params.append(proposal_id)
        if doc_id:
            sql += " AND r.document_id = ?"
            params.append(doc_id)
        if section:
            sql += " AND r.section = ?"
            params.append(section)
        if priority:
            sql += " AND r.priority = ?"
            params.append(priority)
        sql += " ORDER BY r.section, r.req_number"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def get_compliance_status(proposal_id: str) -> dict:
    """Summarize requirement address status for a proposal."""
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT rs.status, COUNT(*) as cnt
            FROM rfx_requirement_status rs
            WHERE rs.proposal_id = ?
            GROUP BY rs.status
        """, (proposal_id,)).fetchall()

        total = conn.execute(
            "SELECT COUNT(*) as n FROM rfx_requirements WHERE proposal_id = ?",
            (proposal_id,)
        ).fetchone()["n"]

        status_counts = {r["status"]: r["cnt"] for r in rows}
        addressed = status_counts.get("addressed", 0)
        partial = status_counts.get("partial", 0)

        pct = round((addressed + partial * 0.5) / total * 100, 1) if total else 0

        return {
            "proposal_id": proposal_id,
            "total_requirements": total,
            "status_counts": status_counts,
            "completion_pct": pct,
        }
    finally:
        conn.close()


def update_requirement_status(requirement_id: str, proposal_id: str,
                               status: str,
                               section_id: Optional[str] = None,
                               ai_section_id: Optional[str] = None,
                               notes: Optional[str] = None,
                               updated_by: Optional[str] = None) -> bool:
    """Update the address status of a requirement for a proposal."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute("""
            UPDATE rfx_requirement_status
            SET status = ?, section_id = ?, ai_section_id = ?,
                compliance_notes = ?, updated_by = ?, updated_at = ?
            WHERE requirement_id = ? AND proposal_id = ?
        """, (status, section_id, ai_section_id, notes, updated_by,
              now, requirement_id, proposal_id))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()
