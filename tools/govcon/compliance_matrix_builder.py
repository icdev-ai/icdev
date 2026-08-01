#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Compliance Matrix Builder -- parse RFP Section L/M/C and build cross-reference matrix.

Parses RFP Section L (instructions), M (evaluation criteria), and C (SOW/PWS) to
build a compliance matrix that maps every requirement to proposal outline sections.
Deterministic regex extraction (no LLM required, air-gap safe).

Architecture:
    - Section L parser: formatting instructions, page limits, volume structure,
      submission requirements
    - Section M parser: evaluation factors, subfactors, weights, rating scales
    - Section C/PWS parser: deliverable requirements (reuses shall-verb extraction)
    - Matrix builder: assembles full cross-reference matrix
    - Auto-mapper: keyword-based assignment of requirements to proposal sections
    - Coverage tracker: % of requirements addressed per volume
    - Amendment detector: diff new RFP version against original, highlight changes
    - Export: JSON, markdown table, CSV

DB table: pg_compliance_matrix (source_section IN ('L','M','C','attachment','amendment'))

Usage:
    python tools/govcon/compliance_matrix_builder.py --opportunity-id "opp-xxx" --parse-l "text..." --json
    python tools/govcon/compliance_matrix_builder.py --opportunity-id "opp-xxx" --parse-m "text..." --json
    python tools/govcon/compliance_matrix_builder.py --opportunity-id "opp-xxx" --parse-c "text..." --json
    python tools/govcon/compliance_matrix_builder.py --opportunity-id "opp-xxx" --build --json
    python tools/govcon/compliance_matrix_builder.py --opportunity-id "opp-xxx" --auto-map --json
    python tools/govcon/compliance_matrix_builder.py --opportunity-id "opp-xxx" --coverage --json
    python tools/govcon/compliance_matrix_builder.py --opportunity-id "opp-xxx" --detect-amendment --new-text "..." --amendment-version 2 --json
    python tools/govcon/compliance_matrix_builder.py --opportunity-id "opp-xxx" --export --format csv --json
    python tools/govcon/compliance_matrix_builder.py --opportunity-id "opp-xxx" --gate --json
"""

import argparse
import csv
import difflib
import hashlib
import io
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.compliance_matrix_builder")

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    from tools.audit.audit_logger import log_event as audit_log_event

    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def audit_log_event(**kwargs):  # type: ignore[misc]
        return -1


# =========================================================================
# CONSTANTS
# =========================================================================

# ---------- Section L regex patterns ----------

_RE_L_HEADER = re.compile(r"(?:section\s+)?[Ll]\s*[-\u2013\u2014.:]\s*(.+?)(?:\n|$)", re.IGNORECASE)
_RE_PAGE_LIMIT = re.compile(
    r"(?:page\s+limit|not\s+to?\s+exceed|maximum\s+of|limited\s+to|no\s+more\s+than)"
    r"\s*[:.]?\s*(\d+)\s*pages?",
    re.IGNORECASE,
)
_RE_FONT_SIZE = re.compile(
    r"(?:font\s+size|typeface|type\s+size|minimum\s+font)\s*[:.]?\s*(\d+)\s*(?:point|pt)",
    re.IGNORECASE,
)
_RE_FONT_FAMILY = re.compile(
    r"(?:font|typeface|type\s+face)\s*[:.]?\s*"
    r"(Times\s+New\s+Roman|Arial|Calibri|Courier(?:\s+New)?|Cambria|Georgia)",
    re.IGNORECASE,
)
_RE_MARGIN = re.compile(
    r"(?:margin|margins)\s*[:.]?\s*(?:at\s+least\s+|minimum\s+)?(\d+(?:\.\d+)?)\s*(?:inch|in\.?|\")",
    re.IGNORECASE,
)
_RE_LINE_SPACING = re.compile(
    r"(?:line\s+spacing|spacing|spaced)\s*[:.]?\s*(single|double|1\.5|1\.0|2\.0)",
    re.IGNORECASE,
)
_RE_VOLUME_STRUCTURE = re.compile(
    r"(?:volume\s+[IVX\d]+|book\s+\d+|tab\s+\d+)\s*[-\u2013\u2014.:]\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_RE_SUBMISSION_REQ = re.compile(
    r"(?:submit|submission|deliver|provide|furnish)\s+.*?"
    r"(?:copies?|originals?|electronic|hard\s*copy|CD|USB|email|portal|SAM)",
    re.IGNORECASE,
)
_RE_WORD_LIMIT = re.compile(
    r"(?:word\s+limit|not\s+to?\s+exceed|maximum\s+of|limited\s+to)\s*[:.]?\s*"
    r"(\d[\d,]*)\s*words?",
    re.IGNORECASE,
)

# ---------- Section M regex patterns ----------

_RE_M_FACTOR = re.compile(
    r"(?:factor|criterion|criteria)\s*(\d+)\s*[-\u2013\u2014.:]\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_RE_M_WEIGHT = re.compile(r"(?:weight|importance|worth)\s*[:.]?\s*(\d+)\s*%", re.IGNORECASE)
_RE_M_SUBFACTOR = re.compile(
    r"(?:subfactor|sub-factor|sub\s+factor)\s*(\d+[.\d]*)\s*[-\u2013\u2014.:]\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_RE_M_RATING_SCALE = re.compile(
    r"\b(outstanding|excellent|good|acceptable|satisfactory|marginal|"
    r"unacceptable|unsatisfactory|deficient|significant\s+weakness|weakness)\b",
    re.IGNORECASE,
)
_RE_M_RELATIVE_IMPORTANCE = re.compile(
    r"((?:significantly|slightly|somewhat)\s+more\s+important\s+than"
    r"|equal\s+(?:in\s+)?importance|descending\s+order\s+of\s+importance"
    r"|order\s+of\s+importance)",
    re.IGNORECASE,
)
_RE_M_TRADEOFF = re.compile(
    r"\b(tradeoff|trade-off|best[\s-]value|lowest\s+price\s+technically\s+acceptable|LPTA)\b",
    re.IGNORECASE,
)

# ---------- Section C / PWS patterns ----------

_RE_C_OBLIGATION = re.compile(
    r"\b(?:shall|must|is\s+required\s+to"
    r"|will\s+(?:provide|deliver|maintain|ensure|demonstrate|comply|support|perform"
    r"|develop|implement|manage|operate|establish|create|produce|prepare|submit"
    r"|conduct|execute|coordinate|monitor|report))\b",
    re.IGNORECASE,
)
_RE_C_DELIVERABLE = re.compile(
    r"\b(?:deliverable|CDRL|DID|CDRL\s+\w+|data\s+item|work\s+product"
    r"|monthly\s+report|weekly\s+report|status\s+report|final\s+report"
    r"|technical\s+report|plan|SOPs?|SOP|procedures?)\b",
    re.IGNORECASE,
)
_RE_C_SECTION_HEADER = re.compile(r"^(?:C\.|PWS\s+)?(\d+(?:\.\d+)*)\s+(.+?)$", re.MULTILINE)

# ---------- Sentence splitting ----------

_RE_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n{2,}")


# =========================================================================
# DATABASE HELPERS
# =========================================================================


def _get_db():
    """Return a database connection."""
    conn = get_connection(db_path=str(DB_PATH))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass  # PostgreSQL doesn't need this
    return conn


def _now():
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_id(prefix="cmx"):
    """Generate a short unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _content_hash(text):
    """SHA-256 content hash for dedup."""
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def _audit(conn, action, details="", actor="compliance_matrix_builder"):
    """Append-only audit trail entry (NIST AU)."""
    try:
        conn.execute(
            "INSERT INTO audit_trail "
            "(created_at, event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), _now(), "govcon.compliance_matrix", actor, action, details, "govcon"),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


# =========================================================================
# SECTION L PARSER
# =========================================================================


def parse_section_l(text: str, opportunity_id: str) -> List[Dict]:
    """Extract formatting instructions, page limits, volume structure from Section L.

    Args:
        text: Raw Section L text.
        opportunity_id: Parent opportunity ID.

    Returns:
        List of extracted requirement dicts ready for DB insertion.
    """
    if not text or not text.strip():
        return []

    results = []
    seen_hashes = set()
    now = _now()

    # --- Page limits ---
    for match in _RE_PAGE_LIMIT.finditer(text):
        _start = max(0, match.start() - 120)
        _end = min(len(text), match.end() + 80)
        context = text[_start:_end].strip()
        req_text = f"Page limit: {match.group(1)} pages. Context: {context}"
        h = _content_hash(req_text)
        if h not in seen_hashes:
            seen_hashes.add(h)
            results.append(
                _make_requirement(
                    opportunity_id,
                    req_text,
                    "L",
                    now,
                    evaluation_factor="formatting",
                    notes=f"page_limit={match.group(1)}",
                )
            )

    # --- Word limits ---
    for match in _RE_WORD_LIMIT.finditer(text):
        _start = max(0, match.start() - 120)
        _end = min(len(text), match.end() + 80)
        context = text[_start:_end].strip()
        word_count = match.group(1).replace(",", "")
        req_text = f"Word limit: {word_count} words. Context: {context}"
        h = _content_hash(req_text)
        if h not in seen_hashes:
            seen_hashes.add(h)
            results.append(
                _make_requirement(
                    opportunity_id,
                    req_text,
                    "L",
                    now,
                    evaluation_factor="formatting",
                    notes=f"word_limit={word_count}",
                )
            )

    # --- Font requirements ---
    for match in _RE_FONT_SIZE.finditer(text):
        _start = max(0, match.start() - 80)
        _end = min(len(text), match.end() + 60)
        context = text[_start:_end].strip()
        req_text = f"Font size: {match.group(1)} pt. Context: {context}"
        h = _content_hash(req_text)
        if h not in seen_hashes:
            seen_hashes.add(h)
            results.append(
                _make_requirement(
                    opportunity_id,
                    req_text,
                    "L",
                    now,
                    evaluation_factor="formatting",
                    notes=f"font_size={match.group(1)}",
                )
            )

    for match in _RE_FONT_FAMILY.finditer(text):
        req_text = f"Font family: {match.group(1)}"
        h = _content_hash(req_text)
        if h not in seen_hashes:
            seen_hashes.add(h)
            results.append(
                _make_requirement(
                    opportunity_id,
                    req_text,
                    "L",
                    now,
                    evaluation_factor="formatting",
                    notes=f"font_family={match.group(1)}",
                )
            )

    # --- Margins ---
    for match in _RE_MARGIN.finditer(text):
        req_text = f"Margin requirement: {match.group(1)} inch minimum"
        h = _content_hash(req_text)
        if h not in seen_hashes:
            seen_hashes.add(h)
            results.append(
                _make_requirement(
                    opportunity_id,
                    req_text,
                    "L",
                    now,
                    evaluation_factor="formatting",
                    notes=f"margin_inches={match.group(1)}",
                )
            )

    # --- Line spacing ---
    for match in _RE_LINE_SPACING.finditer(text):
        req_text = f"Line spacing: {match.group(1)}"
        h = _content_hash(req_text)
        if h not in seen_hashes:
            seen_hashes.add(h)
            results.append(
                _make_requirement(
                    opportunity_id,
                    req_text,
                    "L",
                    now,
                    evaluation_factor="formatting",
                    notes=f"line_spacing={match.group(1)}",
                )
            )

    # --- Volume structure ---
    for match in _RE_VOLUME_STRUCTURE.finditer(text):
        vol_name = match.group(1).strip()
        # Grab 200 chars after the header for context
        _end = min(len(text), match.end() + 200)
        context = text[match.start() : _end].strip()
        req_text = f"Volume: {vol_name}. {context}"
        h = _content_hash(req_text)
        if h not in seen_hashes:
            seen_hashes.add(h)
            results.append(
                _make_requirement(
                    opportunity_id,
                    req_text,
                    "L",
                    now,
                    evaluation_factor="volume_structure",
                    assigned_volume=vol_name,
                )
            )

    # --- Submission requirements ---
    for match in _RE_SUBMISSION_REQ.finditer(text):
        _start = max(0, match.start() - 20)
        _end = min(len(text), match.end() + 40)
        sentence = text[_start:_end].strip()
        if len(sentence) < 20:
            continue
        req_text = sentence
        h = _content_hash(req_text)
        if h not in seen_hashes:
            seen_hashes.add(h)
            results.append(
                _make_requirement(
                    opportunity_id,
                    req_text,
                    "L",
                    now,
                    evaluation_factor="submission",
                )
            )

    # --- General L section headers for anything not caught above ---
    for match in _RE_L_HEADER.finditer(text):
        header_text = match.group(1).strip()
        if len(header_text) < 10:
            continue
        req_text = f"Section L instruction: {header_text}"
        h = _content_hash(req_text)
        if h not in seen_hashes:
            seen_hashes.add(h)
            results.append(
                _make_requirement(
                    opportunity_id,
                    req_text,
                    "L",
                    now,
                    evaluation_factor="instruction",
                )
            )

    return results


# =========================================================================
# SECTION M PARSER
# =========================================================================


def parse_section_m(text: str, opportunity_id: str) -> List[Dict]:
    """Extract evaluation factors, subfactors, weights, rating scales from Section M.

    Args:
        text: Raw Section M text.
        opportunity_id: Parent opportunity ID.

    Returns:
        List of extracted requirement dicts.
    """
    if not text or not text.strip():
        return []

    results = []
    seen_hashes = set()
    now = _now()

    # --- Evaluation methodology (tradeoff vs LPTA) ---
    tradeoff_matches = _RE_M_TRADEOFF.findall(text)
    if tradeoff_matches:
        method = tradeoff_matches[0].strip()
        req_text = f"Evaluation methodology: {method}"
        h = _content_hash(req_text)
        if h not in seen_hashes:
            seen_hashes.add(h)
            results.append(
                _make_requirement(
                    opportunity_id,
                    req_text,
                    "M",
                    now,
                    evaluation_factor="evaluation_methodology",
                    notes=f"method={method}",
                )
            )

    # --- Evaluation factors ---
    for match in _RE_M_FACTOR.finditer(text):
        factor_num = match.group(1).strip()
        factor_name = match.group(2).strip()
        # Look for weight near the factor definition (within 200 chars)
        _end = min(len(text), match.end() + 200)
        nearby = text[match.start() : _end]
        weight_match = _RE_M_WEIGHT.search(nearby)
        weight = float(weight_match.group(1)) if weight_match else None

        req_text = f"Evaluation Factor {factor_num}: {factor_name}"
        h = _content_hash(req_text)
        if h not in seen_hashes:
            seen_hashes.add(h)
            results.append(
                _make_requirement(
                    opportunity_id,
                    req_text,
                    "M",
                    now,
                    evaluation_factor=f"Factor {factor_num}: {factor_name}",
                    evaluation_weight=weight,
                    notes=f"factor_number={factor_num}",
                )
            )

    # --- Subfactors ---
    for match in _RE_M_SUBFACTOR.finditer(text):
        sub_num = match.group(1).strip()
        sub_name = match.group(2).strip()
        _end = min(len(text), match.end() + 200)
        nearby = text[match.start() : _end]
        weight_match = _RE_M_WEIGHT.search(nearby)
        weight = float(weight_match.group(1)) if weight_match else None

        req_text = f"Subfactor {sub_num}: {sub_name}"
        h = _content_hash(req_text)
        if h not in seen_hashes:
            seen_hashes.add(h)
            results.append(
                _make_requirement(
                    opportunity_id,
                    req_text,
                    "M",
                    now,
                    evaluation_factor=f"Subfactor {sub_num}: {sub_name}",
                    evaluation_weight=weight,
                    notes=f"subfactor_number={sub_num}",
                )
            )

    # --- Rating scale detection ---
    rating_terms = set()
    for match in _RE_M_RATING_SCALE.finditer(text):
        rating_terms.add(match.group(1).strip().lower())
    if rating_terms:
        sorted_ratings = sorted(rating_terms)
        req_text = f"Rating scale: {', '.join(sorted_ratings)}"
        h = _content_hash(req_text)
        if h not in seen_hashes:
            seen_hashes.add(h)
            results.append(
                _make_requirement(
                    opportunity_id,
                    req_text,
                    "M",
                    now,
                    evaluation_factor="rating_scale",
                    notes=f"scales={json.dumps(sorted_ratings)}",
                )
            )

    # --- Relative importance statements ---
    for match in _RE_M_RELATIVE_IMPORTANCE.finditer(text):
        _start = max(0, match.start() - 100)
        _end = min(len(text), match.end() + 100)
        context = text[_start:_end].strip()
        req_text = f"Relative importance: {context}"
        h = _content_hash(req_text)
        if h not in seen_hashes:
            seen_hashes.add(h)
            results.append(
                _make_requirement(
                    opportunity_id,
                    req_text,
                    "M",
                    now,
                    evaluation_factor="relative_importance",
                )
            )

    return results


# =========================================================================
# SECTION C / PWS PARSER
# =========================================================================


def parse_section_c(text: str, opportunity_id: str) -> List[Dict]:
    """Extract deliverable requirements from Section C / PWS.

    Reuses obligation-verb extraction (shall/must/will) and adds deliverable
    detection. Sentences are split and each obligation statement is captured
    with its section context.

    Args:
        text: Raw Section C / PWS text.
        opportunity_id: Parent opportunity ID.

    Returns:
        List of extracted requirement dicts.
    """
    if not text or not text.strip():
        return []

    results = []
    seen_hashes = set()
    now = _now()

    # Build a section-header lookup for context
    section_headers = {}
    for match in _RE_C_SECTION_HEADER.finditer(text):
        section_headers[match.start()] = {
            "number": match.group(1),
            "title": match.group(2).strip(),
        }
    header_positions = sorted(section_headers.keys())

    def _find_section(pos):
        """Find the nearest preceding section header for a position."""
        best = None
        for hp in header_positions:
            if hp <= pos:
                best = section_headers[hp]
            else:
                break
        return best

    # Split into sentences and extract obligation statements
    sentences = _RE_SENTENCE_SPLIT.split(text)
    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 20:
            continue

        obligation_match = _RE_C_OBLIGATION.search(sentence)
        if not obligation_match:
            continue

        h = _content_hash(sentence)
        if h in seen_hashes:
            continue
        seen_hashes.add(h)

        # Determine if it's a deliverable requirement
        is_deliverable = bool(_RE_C_DELIVERABLE.search(sentence))

        # Find section context
        pos = text.find(sentence)
        section_ctx = _find_section(pos) if pos >= 0 else None
        section_ref = ""
        if section_ctx:
            section_ref = f"C.{section_ctx['number']}"

        notes_parts = []
        if is_deliverable:
            notes_parts.append("deliverable=true")
        if section_ref:
            notes_parts.append(f"section={section_ref}")

        results.append(
            _make_requirement(
                opportunity_id,
                sentence,
                "C",
                now,
                evaluation_factor="deliverable" if is_deliverable else "performance",
                notes="; ".join(notes_parts) if notes_parts else None,
            )
        )

    return results


# =========================================================================
# REQUIREMENT DICT BUILDER
# =========================================================================


def _make_requirement(
    opportunity_id: str,
    requirement_text: str,
    source_section: str,
    timestamp: str,
    evaluation_factor: Optional[str] = None,
    evaluation_weight: Optional[float] = None,
    assigned_volume: Optional[str] = None,
    assigned_section: Optional[str] = None,
    compliance_status: str = "gap",
    amendment_version: int = 0,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a requirement dict matching pg_compliance_matrix schema."""
    return {
        "id": _gen_id("cmx"),
        "opportunity_id": opportunity_id,
        "requirement_id": _gen_id("req"),
        "requirement_text": requirement_text[:2000],
        "source_section": source_section,
        "evaluation_factor": evaluation_factor,
        "evaluation_weight": evaluation_weight,
        "assigned_volume": assigned_volume,
        "assigned_section": assigned_section,
        "compliance_status": compliance_status,
        "amendment_version": amendment_version,
        "notes": notes,
        "created_at": timestamp,
        "updated_at": timestamp,
        "_content_hash": _content_hash(requirement_text),
    }


# =========================================================================
# STORE PARSED REQUIREMENTS
# =========================================================================


def _store_requirements(requirements: List[Dict], conn=None) -> Dict:
    """Store parsed requirements into pg_compliance_matrix.

    Deduplicates by content_hash per opportunity.

    Returns:
        Dict with stored_count, duplicate_count, total.
    """
    close_conn = False
    if conn is None:
        conn = _get_db()
        close_conn = True

    stored = 0
    duplicates = 0

    for req in requirements:
        opp_id = req["opportunity_id"]

        # Dedup check: same text for same opportunity
        existing = conn.execute(
            "SELECT id FROM pg_compliance_matrix WHERE opportunity_id = %s AND requirement_text = %s",
            (opp_id, req["requirement_text"][:2000]),
        ).fetchone()

        if existing:
            duplicates += 1
            continue

        conn.execute(
            "INSERT INTO pg_compliance_matrix "
            "(id, opportunity_id, requirement_id, requirement_text, source_section, "
            "evaluation_factor, evaluation_weight, assigned_volume, assigned_section, "
            "compliance_status, amendment_version, notes, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                req["id"],
                req["opportunity_id"],
                req["requirement_id"],
                req["requirement_text"],
                req["source_section"],
                req.get("evaluation_factor"),
                req.get("evaluation_weight"),
                req.get("assigned_volume"),
                req.get("assigned_section"),
                req.get("compliance_status", "gap"),
                req.get("amendment_version", 0),
                req.get("notes"),
                req["created_at"],
                req["updated_at"],
            ),
        )
        stored += 1

    conn.commit()
    if close_conn:
        conn.close()

    return {"stored_count": stored, "duplicate_count": duplicates}


# =========================================================================
# MATRIX BUILDER
# =========================================================================


def build_matrix(opportunity_id: str) -> Dict:
    """Assemble the full compliance matrix from all stored L/M/C requirements.

    Args:
        opportunity_id: The opportunity to build the matrix for.

    Returns:
        Dict with matrix rows, counts by section, and summary stats.
    """
    conn = _get_db()

    rows = conn.execute(
        "SELECT * FROM pg_compliance_matrix WHERE opportunity_id = %s ORDER BY source_section, created_at",
        (opportunity_id,),
    ).fetchall()

    if not rows:
        conn.close()
        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "total": 0,
            "matrix": [],
            "by_section": {"L": 0, "M": 0, "C": 0},
            "by_status": {"addressed": 0, "partial": 0, "gap": 0, "na": 0},
        }

    matrix = [dict(r) for r in rows]
    by_section = {"L": 0, "M": 0, "C": 0, "attachment": 0, "amendment": 0}
    by_status = {"addressed": 0, "partial": 0, "gap": 0, "na": 0}

    for item in matrix:
        src = item.get("source_section", "")
        status = item.get("compliance_status", "gap")
        if src in by_section:
            by_section[src] += 1
        if status in by_status:
            by_status[status] += 1

    _audit(conn, "build_matrix", f"Built matrix for {opportunity_id}: {len(matrix)} requirements")
    conn.close()

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "total": len(matrix),
        "matrix": matrix,
        "by_section": by_section,
        "by_status": by_status,
    }


# =========================================================================
# AUTO-MAPPER: requirements -> proposal sections
# =========================================================================

# Keyword-to-volume mapping heuristics
_VOLUME_KEYWORDS = {
    "technical": [
        "technical",
        "approach",
        "architecture",
        "system",
        "design",
        "solution",
        "methodology",
        "technology",
        "engineering",
        "implementation",
        "integration",
        "software",
        "hardware",
        "infrastructure",
        "cloud",
        "devops",
        "devsecops",
        "cyber",
        "security",
        "zero trust",
        "agile",
        "development",
    ],
    "management": [
        "management",
        "staffing",
        "personnel",
        "key personnel",
        "organization",
        "transition",
        "phase-in",
        "quality",
        "risk",
        "schedule",
        "plan",
        "project management",
        "program management",
        "governance",
        "oversight",
        "communication",
        "subcontract",
        "small business",
    ],
    "past_performance": [
        "past performance",
        "experience",
        "relevant experience",
        "similar",
        "reference",
        "contract",
        "previous",
        "prior work",
        "demonstrated",
    ],
    "cost": [
        "cost",
        "price",
        "pricing",
        "budget",
        "labor",
        "rate",
        "ODC",
        "other direct cost",
        "travel",
        "materials",
        "CLIN",
        "line item",
        "fixed price",
        "time and materials",
        "cost plus",
    ],
    "staffing": [
        "staffing",
        "resume",
        "personnel",
        "qualifications",
        "labor category",
        "key personnel",
        "certification",
        "clearance",
        "education",
        "experience",
    ],
}


def auto_map_sections(opportunity_id: str) -> Dict:
    """Auto-map requirements to proposal volumes/sections using keyword matching.

    Assigns `assigned_volume` based on keyword overlap between requirement text
    and volume archetypes.  Does NOT overwrite existing manual assignments.

    Args:
        opportunity_id: The opportunity to auto-map.

    Returns:
        Dict with mapped_count, already_mapped_count, unmapped_count.
    """
    conn = _get_db()

    rows = conn.execute(
        "SELECT id, requirement_text, assigned_volume, assigned_section, source_section "
        "FROM pg_compliance_matrix WHERE opportunity_id = %s",
        (opportunity_id,),
    ).fetchall()

    # Also load existing proposal volumes for the opportunity
    volumes = conn.execute(
        "SELECT id, volume_type, title FROM proposal_volumes WHERE opportunity_id = %s",
        (opportunity_id,),
    ).fetchall()
    volume_map = {v["volume_type"]: v for v in volumes if v.get("volume_type")}

    mapped = 0
    already_mapped = 0
    unmapped = 0

    for row in rows:
        # Skip if already manually assigned
        if row["assigned_volume"] and row["assigned_volume"].strip():
            already_mapped += 1
            continue

        text_lower = (row["requirement_text"] or "").lower()
        best_volume = None
        best_score = 0

        for vol_type, keywords in _VOLUME_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > best_score:
                best_score = score
                best_volume = vol_type

        if best_volume and best_score >= 1:
            # Try to find matching proposal volume
            assigned_section = None
            if best_volume in volume_map:
                assigned_section = volume_map[best_volume].get("title")

            conn.execute(
                "UPDATE pg_compliance_matrix SET assigned_volume = %s, "
                "assigned_section = %s, updated_at = %s WHERE id = %s",
                (best_volume, assigned_section, _now(), row["id"]),
            )
            mapped += 1
        else:
            unmapped += 1

    conn.commit()
    _audit(conn, "auto_map", f"Auto-mapped {mapped} requirements for {opportunity_id}")
    conn.close()

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "mapped_count": mapped,
        "already_mapped_count": already_mapped,
        "unmapped_count": unmapped,
    }


# =========================================================================
# COVERAGE TRACKER
# =========================================================================


def get_coverage(opportunity_id: str) -> Dict:
    """Calculate % of requirements addressed per volume.

    Args:
        opportunity_id: The opportunity to check.

    Returns:
        Dict with per-volume and per-section coverage rates.
    """
    conn = _get_db()

    rows = conn.execute(
        "SELECT source_section, assigned_volume, compliance_status FROM pg_compliance_matrix WHERE opportunity_id = %s",
        (opportunity_id,),
    ).fetchall()

    conn.close()

    if not rows:
        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "total": 0,
            "overall_coverage": 0.0,
            "by_volume": {},
            "by_section": {},
        }

    # Per-volume coverage
    vol_totals = {}
    vol_addressed = {}
    for r in rows:
        vol = r["assigned_volume"] or "unassigned"
        vol_totals[vol] = vol_totals.get(vol, 0) + 1
        if r["compliance_status"] in ("addressed", "partial"):
            vol_addressed[vol] = vol_addressed.get(vol, 0) + 1

    by_volume = {}
    for vol in vol_totals:
        total = vol_totals[vol]
        addressed = vol_addressed.get(vol, 0)
        by_volume[vol] = {
            "total": total,
            "addressed": addressed,
            "coverage_pct": round((addressed / total) * 100, 1) if total > 0 else 0.0,
        }

    # Per-section coverage
    sec_totals = {}
    sec_addressed = {}
    for r in rows:
        sec = r["source_section"] or "other"
        sec_totals[sec] = sec_totals.get(sec, 0) + 1
        if r["compliance_status"] in ("addressed", "partial"):
            sec_addressed[sec] = sec_addressed.get(sec, 0) + 1

    by_section = {}
    for sec in sec_totals:
        total = sec_totals[sec]
        addressed = sec_addressed.get(sec, 0)
        by_section[sec] = {
            "total": total,
            "addressed": addressed,
            "coverage_pct": round((addressed / total) * 100, 1) if total > 0 else 0.0,
        }

    # Overall
    total_all = len(rows)
    addressed_all = sum(1 for r in rows if r["compliance_status"] in ("addressed", "partial"))
    overall_pct = round((addressed_all / total_all) * 100, 1) if total_all > 0 else 0.0

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "total": total_all,
        "addressed": addressed_all,
        "overall_coverage": overall_pct,
        "by_volume": by_volume,
        "by_section": by_section,
    }


# =========================================================================
# AMENDMENT DETECTOR
# =========================================================================


def detect_amendments(
    opportunity_id: str,
    new_text: str,
    amendment_version: int = 1,
) -> Dict:
    """Diff new RFP version against existing matrix, flag changes.

    Compares new_text against existing requirement texts. Uses difflib for
    unified diff computation. New requirements are added with the given
    amendment_version; changed requirements are flagged.

    Args:
        opportunity_id: The opportunity to diff against.
        new_text: The full text of the amended RFP section(s).
        amendment_version: Integer version number for this amendment.

    Returns:
        Dict with new_requirements, changed_requirements, removed_requirements,
        and diff_summary.
    """
    conn = _get_db()

    # Fetch existing requirements for this opportunity
    existing_rows = conn.execute(
        "SELECT id, requirement_text, source_section, compliance_status "
        "FROM pg_compliance_matrix WHERE opportunity_id = %s",
        (opportunity_id,),
    ).fetchall()
    existing_list = [r["requirement_text"] for r in existing_rows]

    # Parse new text through all three parsers
    new_l = parse_section_l(new_text, opportunity_id)
    new_m = parse_section_m(new_text, opportunity_id)
    new_c = parse_section_c(new_text, opportunity_id)
    all_new = new_l + new_m + new_c
    new_texts = [r["requirement_text"] for r in all_new]

    # Find new, changed, removed via sequence matching
    matcher = difflib.SequenceMatcher(None, existing_list, new_texts)
    new_reqs = []
    changed_reqs = []
    removed_reqs = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            # New requirements in the amendment
            for j in range(j1, j2):
                req = all_new[j]
                req["amendment_version"] = amendment_version
                req["notes"] = (req.get("notes") or "") + f"; amendment_v{amendment_version}_added"
                new_reqs.append(req)
        elif tag == "delete":
            # Requirements removed in amendment
            for i in range(i1, i2):
                row = existing_rows[i]
                removed_reqs.append(
                    {
                        "id": row["id"],
                        "requirement_text": row["requirement_text"],
                        "source_section": row["source_section"],
                    }
                )
        elif tag == "replace":
            # Changed requirements
            for i in range(i1, i2):
                old_text = existing_list[i] if i < len(existing_list) else ""
                # Find closest new text for this replacement
                new_idx = j1 + (i - i1)
                new_t = new_texts[new_idx] if new_idx < j2 else ""
                if old_text != new_t:
                    diff_lines = list(
                        difflib.unified_diff(
                            old_text.splitlines(),
                            new_t.splitlines(),
                            fromfile="original",
                            tofile=f"amendment_v{amendment_version}",
                            lineterm="",
                        )
                    )
                    changed_reqs.append(
                        {
                            "original_id": existing_rows[i]["id"] if i < len(existing_rows) else None,
                            "original_text": old_text[:500],
                            "new_text": new_t[:500],
                            "diff": "\n".join(diff_lines[:50]),
                        }
                    )

    # Store new requirements
    if new_reqs:
        store_result = _store_requirements(new_reqs, conn)
    else:
        store_result = {"stored_count": 0, "duplicate_count": 0}

    _audit(
        conn,
        "detect_amendment",
        f"Amendment v{amendment_version} for {opportunity_id}: "
        f"{len(new_reqs)} new, {len(changed_reqs)} changed, {len(removed_reqs)} removed",
    )
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "amendment_version": amendment_version,
        "new_requirements": len(new_reqs),
        "changed_requirements": len(changed_reqs),
        "removed_requirements": len(removed_reqs),
        "stored": store_result,
        "changes": changed_reqs[:20],  # Limit output for readability
        "removed": removed_reqs[:20],
    }


# =========================================================================
# EXPORT
# =========================================================================


def export_matrix(opportunity_id: str, fmt: str = "json") -> str:
    """Export compliance matrix as JSON, markdown table, or CSV.

    Args:
        opportunity_id: The opportunity to export.
        fmt: Output format -- 'json', 'markdown', or 'csv'.

    Returns:
        String in requested format.
    """
    matrix_data = build_matrix(opportunity_id)
    if matrix_data.get("total", 0) == 0:
        return json.dumps({"status": "ok", "message": "No requirements found", "data": ""})

    items = matrix_data["matrix"]

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "ID",
                "Source Section",
                "Requirement",
                "Evaluation Factor",
                "Weight",
                "Assigned Volume",
                "Assigned Section",
                "Compliance Status",
                "Amendment",
                "Notes",
            ]
        )
        for item in items:
            writer.writerow(
                [
                    item.get("id", ""),
                    item.get("source_section", ""),
                    item.get("requirement_text", "")[:200],
                    item.get("evaluation_factor", ""),
                    item.get("evaluation_weight", ""),
                    item.get("assigned_volume", ""),
                    item.get("assigned_section", ""),
                    item.get("compliance_status", ""),
                    item.get("amendment_version", 0),
                    item.get("notes", ""),
                ]
            )
        return output.getvalue()

    elif fmt == "markdown":
        lines = [
            f"# Compliance Matrix -- {opportunity_id}",
            "",
            f"Total Requirements: {matrix_data['total']}",
            f"By Section: L={matrix_data['by_section'].get('L', 0)} "
            f"M={matrix_data['by_section'].get('M', 0)} "
            f"C={matrix_data['by_section'].get('C', 0)}",
            "",
            "| # | Section | Requirement | Factor | Volume | Status |",
            "|---|---------|-------------|--------|--------|--------|",
        ]
        for i, item in enumerate(items, 1):
            req_short = (item.get("requirement_text", "")[:60]).replace("|", "\\|")
            lines.append(
                f"| {i} "
                f"| {item.get('source_section', '')} "
                f"| {req_short} "
                f"| {item.get('evaluation_factor', '') or ''} "
                f"| {item.get('assigned_volume', '') or ''} "
                f"| {item.get('compliance_status', '')} |"
            )
        return "\n".join(lines)

    else:
        # JSON -- return the full matrix data
        return json.dumps(matrix_data, indent=2, default=str)


# =========================================================================
# GATE EVALUATION
# =========================================================================


def evaluate_gate(opportunity_id: str) -> Dict:
    """Evaluate compliance matrix gate.

    Gate criteria:
        PASS: 0 requirements with status='gap' that have source_section='M'
        WARN: Any requirements with status='partial'
        FAIL: Any Section M requirements with status='gap'

    Args:
        opportunity_id: The opportunity to evaluate.

    Returns:
        Dict with gate_result ('pass', 'warn', 'fail'), details, counts.
    """
    conn = _get_db()

    # Count Section M gaps (FAIL condition)
    m_gaps = conn.execute(
        "SELECT COUNT(*) as c FROM pg_compliance_matrix "
        "WHERE opportunity_id = %s AND source_section = 'M' AND compliance_status = 'gap'",
        (opportunity_id,),
    ).fetchone()
    m_gap_count = m_gaps["c"] if m_gaps else 0

    # Count all partials (WARN condition)
    partials = conn.execute(
        "SELECT COUNT(*) as c FROM pg_compliance_matrix WHERE opportunity_id = %s AND compliance_status = 'partial'",
        (opportunity_id,),
    ).fetchone()
    partial_count = partials["c"] if partials else 0

    # Count total and addressed
    totals = conn.execute(
        "SELECT COUNT(*) as total, "
        "SUM(CASE WHEN compliance_status = 'addressed' THEN 1 ELSE 0 END) as addressed, "
        "SUM(CASE WHEN compliance_status = 'gap' THEN 1 ELSE 0 END) as gaps "
        "FROM pg_compliance_matrix WHERE opportunity_id = %s",
        (opportunity_id,),
    ).fetchone()

    total_count = totals["total"] if totals else 0
    addressed_count = totals["addressed"] if totals else 0
    total_gaps = totals["gaps"] if totals else 0

    conn.close()

    findings = []

    if m_gap_count > 0:
        gate_result = "fail"
        findings.append(
            f"BLOCKING: {m_gap_count} Section M evaluation criteria have status='gap'. "
            f"All evaluation factors must be addressed."
        )
    elif partial_count > 0:
        gate_result = "warn"
        findings.append(
            f"WARNING: {partial_count} requirements have status='partial'. Review and address before submission."
        )
    elif total_count == 0:
        gate_result = "warn"
        findings.append("WARNING: No requirements in compliance matrix. Parse L/M/C sections first.")
    else:
        gate_result = "pass"
        findings.append(f"All {total_count} requirements addressed. No Section M gaps.")

    return {
        "status": "ok",
        "gate_result": gate_result,
        "opportunity_id": opportunity_id,
        "total_requirements": total_count,
        "addressed": addressed_count,
        "partial": partial_count,
        "gaps": total_gaps,
        "section_m_gaps": m_gap_count,
        "findings": findings,
    }


# =========================================================================
# CLI
# =========================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Compliance Matrix Builder -- parse RFP L/M/C and build cross-reference matrix"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--parse-l", metavar="TEXT", help="Parse Section L text and store requirements")
    group.add_argument("--parse-l-file", metavar="FILE", help="Parse Section L from a file")
    group.add_argument("--parse-m", metavar="TEXT", help="Parse Section M text and store requirements")
    group.add_argument("--parse-m-file", metavar="FILE", help="Parse Section M from a file")
    group.add_argument("--parse-c", metavar="TEXT", help="Parse Section C/PWS text and store requirements")
    group.add_argument("--parse-c-file", metavar="FILE", help="Parse Section C/PWS from a file")
    group.add_argument("--build", action="store_true", help="Build full compliance matrix from stored requirements")
    group.add_argument("--auto-map", action="store_true", help="Auto-map requirements to proposal volumes/sections")
    group.add_argument("--coverage", action="store_true", help="Show coverage percentage per volume and section")
    group.add_argument("--detect-amendment", action="store_true", help="Diff new text against existing matrix")
    group.add_argument("--export", action="store_true", help="Export compliance matrix")
    group.add_argument("--gate", action="store_true", help="Evaluate compliance matrix gate")

    parser.add_argument("--opportunity-id", required=True, help="Opportunity ID (required)")
    parser.add_argument("--new-text", metavar="TEXT", help="New RFP text for amendment detection")
    parser.add_argument("--new-text-file", metavar="FILE", help="New RFP text file for amendment detection")
    parser.add_argument("--amendment-version", type=int, default=1, help="Amendment version number (default: 1)")
    parser.add_argument(
        "--format", choices=["json", "markdown", "csv"], default="json", help="Export format (default: json)"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")

    args = parser.parse_args()
    opp_id = args.opportunity_id

    # --- Parse Section L ---
    if args.parse_l or args.parse_l_file:
        text = args.parse_l
        if args.parse_l_file:
            text = Path(args.parse_l_file).read_text(encoding="utf-8")
        requirements = parse_section_l(text, opp_id)
        store_result = _store_requirements(requirements)
        result = {
            "status": "ok",
            "action": "parse_section_l",
            "opportunity_id": opp_id,
            "extracted": len(requirements),
            **store_result,
        }

    # --- Parse Section M ---
    elif args.parse_m or args.parse_m_file:
        text = args.parse_m
        if args.parse_m_file:
            text = Path(args.parse_m_file).read_text(encoding="utf-8")
        requirements = parse_section_m(text, opp_id)
        store_result = _store_requirements(requirements)
        result = {
            "status": "ok",
            "action": "parse_section_m",
            "opportunity_id": opp_id,
            "extracted": len(requirements),
            **store_result,
        }

    # --- Parse Section C ---
    elif args.parse_c or args.parse_c_file:
        text = args.parse_c
        if args.parse_c_file:
            text = Path(args.parse_c_file).read_text(encoding="utf-8")
        requirements = parse_section_c(text, opp_id)
        store_result = _store_requirements(requirements)
        result = {
            "status": "ok",
            "action": "parse_section_c",
            "opportunity_id": opp_id,
            "extracted": len(requirements),
            **store_result,
        }

    # --- Build matrix ---
    elif args.build:
        result = build_matrix(opp_id)

    # --- Auto-map ---
    elif args.auto_map:
        result = auto_map_sections(opp_id)

    # --- Coverage ---
    elif args.coverage:
        result = get_coverage(opp_id)

    # --- Amendment detection ---
    elif args.detect_amendment:
        new_text = args.new_text
        if args.new_text_file:
            new_text = Path(args.new_text_file).read_text(encoding="utf-8")
        if not new_text:
            result = {"status": "error", "message": "--new-text or --new-text-file required"}
        else:
            result = detect_amendments(opp_id, new_text, args.amendment_version)

    # --- Export ---
    elif args.export:
        exported = export_matrix(opp_id, args.format)
        if args.format in ("csv", "markdown"):
            # For CSV/markdown, print directly and exit
            print(exported)
            return
        result = json.loads(exported) if isinstance(exported, str) else exported

    # --- Gate ---
    elif args.gate:
        result = evaluate_gate(opp_id)
        # Set exit code for gate evaluation
        if result.get("gate_result") == "fail":
            _print_output(result, args)
            sys.exit(1)
        _print_output(result, args)
        sys.exit(0)

    else:
        result = {"status": "error", "message": "No action specified"}

    _print_output(result, args)


def _print_output(result, args):
    """Print result in requested format."""
    if args.json or not args.human:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_human(result)


def _print_human(result):
    """Human-readable output."""
    if result.get("status") == "error":
        print(f"\n  ERROR: {result.get('message', 'Unknown error')}\n")
        return

    print(f"\n  {'=' * 60}")
    action = result.get("action", "")

    if action.startswith("parse_section"):
        section = action.replace("parse_section_", "").upper()
        print(f"  Section {section} Parse Results")
        print(f"  {'=' * 60}")
        print(f"  Opportunity:  {result.get('opportunity_id', '')}")
        print(f"  Extracted:    {result.get('extracted', 0)}")
        print(f"  Stored (new): {result.get('stored_count', 0)}")
        print(f"  Duplicates:   {result.get('duplicate_count', 0)}")

    elif "matrix" in result:
        print(f"  Compliance Matrix -- {result.get('opportunity_id', '')}")
        print(f"  {'=' * 60}")
        print(f"  Total Requirements: {result.get('total', 0)}")
        by_sec = result.get("by_section", {})
        print(f"  By Section:  L={by_sec.get('L', 0)}  M={by_sec.get('M', 0)}  C={by_sec.get('C', 0)}")
        by_st = result.get("by_status", {})
        print(
            f"  By Status:   Addressed={by_st.get('addressed', 0)}  "
            f"Partial={by_st.get('partial', 0)}  "
            f"Gap={by_st.get('gap', 0)}  N/A={by_st.get('na', 0)}"
        )

    elif "overall_coverage" in result:
        print(f"  Coverage Report -- {result.get('opportunity_id', '')}")
        print(f"  {'=' * 60}")
        print(f"  Total Requirements: {result.get('total', 0)}")
        print(f"  Overall Coverage:   {result.get('overall_coverage', 0):.1f}%")
        print("\n  By Volume:")
        for vol, data in result.get("by_volume", {}).items():
            bar_len = int(data["coverage_pct"] / 5)
            bar = "=" * bar_len
            print(f"    {vol:20s} [{bar:<20s}] {data['coverage_pct']:.1f}% ({data['addressed']}/{data['total']})")
        print("\n  By Section:")
        for sec, data in result.get("by_section", {}).items():
            bar_len = int(data["coverage_pct"] / 5)
            bar = "=" * bar_len
            print(
                f"    Section {sec:10s} [{bar:<20s}] {data['coverage_pct']:.1f}% ({data['addressed']}/{data['total']})"
            )

    elif "gate_result" in result:
        gate = result["gate_result"].upper()
        icon = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}.get(gate, "[????]")
        print(f"  Gate Evaluation: {icon} {gate}")
        print(f"  {'=' * 60}")
        print(f"  Opportunity:       {result.get('opportunity_id', '')}")
        print(f"  Total:             {result.get('total_requirements', 0)}")
        print(f"  Addressed:         {result.get('addressed', 0)}")
        print(f"  Partial:           {result.get('partial', 0)}")
        print(f"  Gaps:              {result.get('gaps', 0)}")
        print(f"  Section M Gaps:    {result.get('section_m_gaps', 0)}")
        for finding in result.get("findings", []):
            print(f"\n  {finding}")

    elif "new_requirements" in result:
        print(f"  Amendment Detection -- {result.get('opportunity_id', '')}")
        print(f"  {'=' * 60}")
        print(f"  Amendment Version: {result.get('amendment_version', 0)}")
        print(f"  New Requirements:     {result.get('new_requirements', 0)}")
        print(f"  Changed Requirements: {result.get('changed_requirements', 0)}")
        print(f"  Removed Requirements: {result.get('removed_requirements', 0)}")
        changes = result.get("changes", [])
        if changes:
            print(f"\n  Changes (showing first {min(len(changes), 5)}):")
            for c in changes[:5]:
                print(f"    OLD: {(c.get('original_text') or '')[:60]}")
                print(f"    NEW: {(c.get('new_text') or '')[:60]}")
                print()

    elif "mapped_count" in result:
        print(f"  Auto-Map Results -- {result.get('opportunity_id', '')}")
        print(f"  {'=' * 60}")
        print(f"  Mapped:          {result.get('mapped_count', 0)}")
        print(f"  Already Mapped:  {result.get('already_mapped_count', 0)}")
        print(f"  Unmapped:        {result.get('unmapped_count', 0)}")

    else:
        print(f"  Result: {json.dumps(result, indent=2, default=str)}")

    print()


if __name__ == "__main__":
    main()

# CUI // SP-CTI
