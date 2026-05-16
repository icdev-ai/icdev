#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""Parse solicitation documents to extract Section L (Instructions) and Section M
(Evaluation Criteria), plus deep "shredding" of all requirement-bearing sections.

Supports plain-text, PDF (via pypdf), and Word (via python-docx) input formats.
Extracts structured requirements, page limits, format rules, evaluation factors,
and relative importance weightings.  Results are stored in the proposals table
(section_l_parsed, section_m_parsed) and feed into the compliance-matrix generator.

Shredder mode (--shred) extracts requirements from ALL sections:
  - Section C / SOW (Statement of Work) — shall/must/will statements
  - Section F (Deliverables & Performance) — delivery schedules, milestones
  - Section H (Special Contract Requirements) — clauses, CDRLs
  - Section J (Attachments) — referenced document requirements
  - Section L (Instructions to Offerors) — proposal instructions
  - Section M (Evaluation Criteria) — evaluation factors

Each extracted requirement includes obligation level, source section, and
cross-references to other sections (FAR, DFARS, attachments).

Usage:
    python tools/proposal/section_parser.py --parse --file /path/to/rfp.pdf --proposal-id "prop-123" --json
    python tools/proposal/section_parser.py --shred --file /path/to/rfp.pdf --proposal-id "prop-123" --json
    python tools/proposal/section_parser.py --text "Section L ..." --json
    python tools/proposal/section_parser.py --matrix --proposal-id "prop-123" --json
    python tools/proposal/section_parser.py --get-matrix --proposal-id "prop-123" --json
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

# ---------------------------------------------------------------------------
# Optional imports — degrade gracefully
# ---------------------------------------------------------------------------
try:
    import yaml  # noqa: F401
except ImportError:  # pragma: no cover
    yaml = None

try:
    from pypdf import PdfReader  # type: ignore
except ImportError:  # pragma: no cover
    PdfReader = None

try:
    import docx as python_docx  # type: ignore
except ImportError:  # pragma: no cover
    python_docx = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db(db_path=None):
    """Return an SQLite connection with WAL + FK enabled."""
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    """UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _uid():
    """Short UUID for primary keys."""
    return str(uuid.uuid4())[:12]


def _audit(conn, event_type, action, entity_type=None, entity_id=None, details=None):
    """Append-only audit trail entry."""
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, entity_type, entity_id, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_type, "section_parser", action, entity_type, entity_id, details, _now()),
    )


# ---------------------------------------------------------------------------
# Document reading
# ---------------------------------------------------------------------------

def _read_pdf(file_path):
    """Extract text from PDF using pypdf."""
    if PdfReader is None:
        raise ImportError("pypdf is required for PDF parsing. Install with: pip install pypdf")
    reader = PdfReader(str(file_path))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def _read_docx(file_path):
    """Extract text from Word document using python-docx."""
    if python_docx is None:
        raise ImportError("python-docx is required for Word parsing. Install with: pip install python-docx")
    doc = python_docx.Document(str(file_path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


def _read_file(file_path):
    """Read document content based on file extension."""
    path = Path(file_path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _read_pdf(path)
    elif ext in (".docx", ".doc"):
        return _read_docx(path)
    elif ext in (".txt", ".md", ".text", ""):
        return path.read_text(encoding="utf-8", errors="replace")
    else:
        # Attempt plain-text read as fallback
        return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Section L / M regex patterns
# ---------------------------------------------------------------------------

# Headers that delimit Section L
_SECTION_L_PATTERNS = [
    re.compile(r"(?i)\bSECTION\s+L[\.\s:\-—]+", re.MULTILINE),
    re.compile(r"(?i)\bL[\.\s]+INSTRUCTIONS\s+TO\s+OFFERORS\b", re.MULTILINE),
    re.compile(r"(?i)\bINSTRUCTIONS\s+TO\s+OFFERORS\b", re.MULTILINE),
    re.compile(r"(?i)\bPROPOSAL\s+PREPARATION\s+INSTRUCTIONS\b", re.MULTILINE),
    re.compile(r"(?i)\bSECTION\s+L\b", re.MULTILINE),
]

# Headers that delimit Section M
_SECTION_M_PATTERNS = [
    re.compile(r"(?i)\bSECTION\s+M[\.\s:\-—]+", re.MULTILINE),
    re.compile(r"(?i)\bM[\.\s]+EVALUATION\s+(?:CRITERIA|FACTORS)\b", re.MULTILINE),
    re.compile(r"(?i)\bEVALUATION\s+(?:CRITERIA|FACTORS)\s+FOR\s+AWARD\b", re.MULTILINE),
    re.compile(r"(?i)\bEVALUATION\s+CRITERIA\b", re.MULTILINE),
    re.compile(r"(?i)\bSECTION\s+M\b", re.MULTILINE),
]

# Requirement keywords
_REQ_PATTERN = re.compile(
    r"(?:(?:The\s+)?(?:offeror|contractor|vendor|company|organization|Government)\s+)?"
    r"(?:shall|must|will|is\s+required\s+to|are\s+required\s+to)\s+"
    r"([^.;]{10,300})[.;]",
    re.IGNORECASE,
)

# Page limits
_PAGE_LIMIT_PATTERN = re.compile(
    r"(?:not\s+(?:to\s+)?exceed|no\s+more\s+than|maximum\s+of|limited\s+to|up\s+to)"
    r"\s+(\d+)\s*pages?",
    re.IGNORECASE,
)

# Font / margin requirements
_FORMAT_PATTERNS = {
    "font": re.compile(
        r"(?:font|typeface|type\s+face)[\s:]+([A-Za-z\s]+\d+[\s\-]*(?:point|pt))", re.IGNORECASE
    ),
    "margins": re.compile(
        r"(?:margin|margins)[\s:]+([^.;]{5,100})[.;]", re.IGNORECASE
    ),
    "spacing": re.compile(
        r"(?:(?:single|double|1\.5)\s*[\-]?\s*spac(?:ed|ing))", re.IGNORECASE
    ),
    "line_spacing": re.compile(
        r"(?:line\s+spacing|leading)[\s:]+([^.;]{3,60})[.;]", re.IGNORECASE
    ),
}

# Evaluation factors
_FACTOR_PATTERN = re.compile(
    r"(?:Factor|Criterion|Area)\s*(\d+|[IVXivx]+)[\s:\.\-—]+([^\n]{5,200})",
    re.IGNORECASE,
)

# Subfactor
_SUBFACTOR_PATTERN = re.compile(
    r"(?:Sub[\-\s]?factor|Sub[\-\s]?criterion|Element)\s*(\d+[\.\d]*|[a-z][\.\d]*)[\s:\.\-—]+([^\n]{5,200})",
    re.IGNORECASE,
)

# Relative importance
_IMPORTANCE_PATTERNS = [
    re.compile(r"(?i)(significantly\s+more\s+important\s+than)", re.MULTILINE),
    re.compile(r"(?i)(approximately\s+equal(?:\s+in\s+importance)?)", re.MULTILINE),
    re.compile(r"(?i)(more\s+important\s+than)", re.MULTILINE),
    re.compile(r"(?i)(equally\s+(?:important|weighted))", re.MULTILINE),
    re.compile(r"(?i)(descending\s+order\s+of\s+importance)", re.MULTILINE),
    re.compile(r"(?i)(when\s+combined[\s,]+(?:are\s+)?(?:approximately\s+)?equal\s+to)", re.MULTILINE),
]

# Volume assignment (within Section L)
_VOLUME_PATTERN = re.compile(
    r"(?:Volume|Vol\.?)\s*(\d+|[IVXivx]+)[\s:\-—]+([^\n]{3,120})",
    re.IGNORECASE,
)

# Numbered instruction items  (L.1, L.2, L-1, (1), etc.)
_INSTRUCTION_ITEM_PATTERN = re.compile(
    r"(?:L[\.\-]\s*)?(?:(\d+[\.\d]*)[\.\)\s]+|(\([a-z0-9]+\))\s+)([^\n]{10,400})",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Parsing logic
# ---------------------------------------------------------------------------

def _find_section(text, start_patterns, stop_patterns):
    """Return the text between the first match of a start pattern and the first
    match of a stop pattern (or end-of-text)."""
    start_pos = None
    for pat in start_patterns:
        m = pat.search(text)
        if m:
            if start_pos is None or m.start() < start_pos:
                start_pos = m.start()
    if start_pos is None:
        return ""
    # Find stop boundary
    stop_pos = len(text)
    for pat in stop_patterns:
        m = pat.search(text, pos=start_pos + 10)
        if m and m.start() < stop_pos:
            stop_pos = m.start()
    return text[start_pos:stop_pos].strip()


def parse_text(text):
    """Parse raw text for Section L and Section M content.

    Returns:
        dict with keys:
            sections_l: list of extracted L instructions
            sections_m: list of extracted M evaluation criteria
            format_requirements: dict of detected formatting rules
            raw_section_l: raw text of Section L block
            raw_section_m: raw text of Section M block
    """
    if not text or not text.strip():
        return {
            "sections_l": [],
            "sections_m": [],
            "format_requirements": {},
            "raw_section_l": "",
            "raw_section_m": "",
        }

    # --- Extract Section L block ---
    raw_l = _find_section(text, _SECTION_L_PATTERNS, _SECTION_M_PATTERNS)
    # If Section L not found, try the whole document (some RFPs inline instructions)
    search_text_l = raw_l if raw_l else text

    # --- Extract Section M block ---
    # Section M typically appears after L; stop patterns = next lettered section or EOF
    _section_n_patterns = [
        re.compile(r"(?i)\bSECTION\s+[N-Z][\.\s:\-—]+", re.MULTILINE),
    ]
    raw_m = _find_section(text, _SECTION_M_PATTERNS, _section_n_patterns)
    search_text_m = raw_m if raw_m else text

    # --- Parse Section L instructions ---
    sections_l = []
    seen_instructions = set()

    # Extract volume assignments
    volumes = {}
    for vm in _VOLUME_PATTERN.finditer(search_text_l):
        vol_num = vm.group(1).strip()
        vol_title = vm.group(2).strip()
        volumes[vol_num] = vol_title

    # Extract page limits per section/volume
    page_limits = []
    for pm in _PAGE_LIMIT_PATTERN.finditer(search_text_l):
        limit_val = int(pm.group(1))
        # Grab surrounding context to associate limit with a section
        ctx_start = max(0, pm.start() - 200)
        context = search_text_l[ctx_start:pm.end()]
        page_limits.append({"limit": limit_val, "context": context.strip()})

    # Extract format requirements
    format_reqs = {}
    for fmt_name, fmt_pat in _FORMAT_PATTERNS.items():
        fm = fmt_pat.search(search_text_l)
        if fm:
            format_reqs[fmt_name] = fm.group(0).strip()

    # Extract numbered instruction items
    for im in _INSTRUCTION_ITEM_PATTERN.finditer(search_text_l):
        num = (im.group(1) or im.group(2) or "").strip()
        instruction_text = im.group(3).strip()
        if instruction_text in seen_instructions:
            continue
        seen_instructions.add(instruction_text)

        # Try to associate page limits
        local_page_limit = None
        for pl in page_limits:
            if instruction_text[:40].lower() in pl["context"].lower():
                local_page_limit = pl["limit"]
                break

        # Determine volume
        volume = _guess_volume(instruction_text, volumes)

        sections_l.append({
            "id": f"L-{num}" if num else f"L-{_uid()[:6]}",
            "instruction_text": instruction_text,
            "volume": volume,
            "page_limit": local_page_limit,
            "format_requirements": format_reqs if format_reqs else None,
        })

    # If no numbered items found, extract requirement statements from L block
    if not sections_l:
        for idx, rm in enumerate(extract_requirements(search_text_l), start=1):
            sections_l.append({
                "id": f"L-{idx}",
                "instruction_text": rm,
                "volume": None,
                "page_limit": page_limits[0]["limit"] if page_limits else None,
                "format_requirements": format_reqs if format_reqs else None,
            })

    # --- Parse Section M evaluation criteria ---
    sections_m = []
    seen_factors = set()

    # Extract evaluation factors
    for fm in _FACTOR_PATTERN.finditer(search_text_m):
        factor_num = fm.group(1).strip()
        factor_text = fm.group(2).strip()
        factor_key = factor_text[:60].lower()
        if factor_key in seen_factors:
            continue
        seen_factors.add(factor_key)

        # Search for subfactors after this factor
        factor_end = fm.end()
        next_factor = _FACTOR_PATTERN.search(search_text_m, pos=factor_end)
        subfactor_region = search_text_m[factor_end:next_factor.start() if next_factor else len(search_text_m)]

        subfactors = []
        for sm in _SUBFACTOR_PATTERN.finditer(subfactor_region):
            subfactors.append({
                "id": sm.group(1).strip(),
                "text": sm.group(2).strip(),
            })

        # Detect weight/importance description
        weight_desc = _extract_importance(subfactor_region)

        sections_m.append({
            "id": f"M-{factor_num}",
            "factor": factor_text,
            "subfactors": subfactors if subfactors else None,
            "weight_description": weight_desc,
            "evaluation_standard": _extract_eval_standard(subfactor_region),
        })

    # If no structured factors found, try to extract evaluation-related requirements
    if not sections_m:
        eval_keywords = re.compile(
            r"(?:evaluat|assess|scor|rating|criterion|factor|weight|importance)",
            re.IGNORECASE,
        )
        for idx, line in enumerate(search_text_m.split("\n"), start=1):
            line = line.strip()
            if line and eval_keywords.search(line) and len(line) > 20:
                sections_m.append({
                    "id": f"M-{idx}",
                    "factor": line[:300],
                    "subfactors": None,
                    "weight_description": None,
                    "evaluation_standard": None,
                })
                if len(sections_m) >= 20:
                    break

    return {
        "sections_l": sections_l,
        "sections_m": sections_m,
        "format_requirements": format_reqs,
        "raw_section_l": raw_l[:5000] if raw_l else "",
        "raw_section_m": raw_m[:5000] if raw_m else "",
    }


def _guess_volume(text, volumes):
    """Heuristic volume assignment from instruction text."""
    text_lower = text.lower()
    vol_keywords = {
        "technical": ["technical", "approach", "solution", "methodology", "engineering"],
        "management": ["management", "staffing", "organization", "transition", "risk", "schedule"],
        "past_performance": ["past performance", "experience", "reference", "prior work", "relevant"],
        "cost": ["cost", "price", "pricing", "budget", "rate"],
    }
    for vol, keywords in vol_keywords.items():
        for kw in keywords:
            if kw in text_lower:
                return vol
    return None


def _extract_importance(text):
    """Extract relative importance descriptions from evaluation text."""
    for pat in _IMPORTANCE_PATTERNS:
        m = pat.search(text)
        if m:
            # Grab surrounding context
            ctx_start = max(0, m.start() - 80)
            ctx_end = min(len(text), m.end() + 80)
            return text[ctx_start:ctx_end].strip()
    return None


def _extract_eval_standard(text):
    """Extract evaluation standard phrases like 'adjectival', 'color', 'numerical'."""
    standards = re.compile(
        r"(?i)(adjectival|color[\s\-]*coded|numerical|acceptable[\s/]+unacceptable|"
        r"outstanding|good|acceptable|marginal|unacceptable|blue|purple|green|yellow|red|white)",
    )
    matches = standards.findall(text)
    if matches:
        return ", ".join(sorted(set(m.strip().lower() for m in matches)))
    return None


# ---------------------------------------------------------------------------
# Requirement extraction
# ---------------------------------------------------------------------------

def extract_requirements(text):
    """Extract all shall/must/will/required-to statements from text.

    Returns:
        list of requirement strings
    """
    if not text:
        return []
    requirements = []
    seen = set()
    for m in _REQ_PATTERN.finditer(text):
        full = m.group(0).strip()
        norm = full[:80].lower()
        if norm not in seen:
            seen.add(norm)
            requirements.append(full)
    return requirements


# ---------------------------------------------------------------------------
# Shredder — deep multi-section requirement extraction
# ---------------------------------------------------------------------------

# Section boundary patterns for all extractable sections
_SECTION_C_PATTERNS = [
    re.compile(r"(?i)\bSECTION\s+C[\.\s:\-—]+", re.MULTILINE),
    re.compile(r"(?i)\bC[\.\s]+(?:DESCRIPTION|STATEMENT\s+OF\s+WORK|SOW|SPECIFICATIONS)\b", re.MULTILINE),
    re.compile(r"(?i)\bSTATEMENT\s+OF\s+WORK\b", re.MULTILINE),
    re.compile(r"(?i)\bPERFORMANCE\s+WORK\s+STATEMENT\b", re.MULTILINE),
]

_SECTION_F_PATTERNS = [
    re.compile(r"(?i)\bSECTION\s+F[\.\s:\-—]+", re.MULTILINE),
    re.compile(r"(?i)\bF[\.\s]+DELIVER(?:IES|Y|ABLES)\b", re.MULTILINE),
    re.compile(r"(?i)\bDELIVER(?:IES|Y)\s+(?:OR|AND)\s+PERFORMANCE\b", re.MULTILINE),
]

_SECTION_H_PATTERNS = [
    re.compile(r"(?i)\bSECTION\s+H[\.\s:\-—]+", re.MULTILINE),
    re.compile(r"(?i)\bH[\.\s]+SPECIAL\s+CONTRACT\s+REQUIREMENTS\b", re.MULTILINE),
    re.compile(r"(?i)\bSPECIAL\s+CONTRACT\s+REQUIREMENTS\b", re.MULTILINE),
]

_SECTION_J_PATTERNS = [
    re.compile(r"(?i)\bSECTION\s+J[\.\s:\-—]+", re.MULTILINE),
    re.compile(r"(?i)\bJ[\.\s]+(?:LIST\s+OF\s+)?ATTACH(?:MENTS|ED\s+DOCUMENTS)\b", re.MULTILINE),
    re.compile(r"(?i)\bLIST\s+OF\s+ATTACHMENTS\b", re.MULTILINE),
]

# Cross-reference detection patterns
_XREF_PATTERNS = [
    re.compile(r"(?i)(?:see|refer\s+to|per|in\s+accordance\s+with|IAW|ref(?:erence)?)\s+Section\s+([A-Z](?:\.\d+)*)", re.MULTILINE),
    re.compile(r"(?i)(?:per|IAW|in\s+accordance\s+with)\s+(FAR\s+\d+\.\d+(?:\.\d+)?(?:-\d+)?)", re.MULTILINE),
    re.compile(r"(?i)(?:per|IAW|in\s+accordance\s+with)\s+(DFARS\s+\d+\.\d+(?:\.\d+)?(?:-\d+)?)", re.MULTILINE),
    re.compile(r"(?i)(?:see|refer\s+to|per|reference)\s+Attachment\s+([A-Z0-9]+(?:\s*[-–]\s*[A-Za-z0-9 ]+)?)", re.MULTILINE),
    re.compile(r"(?i)(?:see|refer\s+to|per|reference)\s+Exhibit\s+([A-Z0-9]+)", re.MULTILINE),
    re.compile(r"(?i)(CDRL\s+[A-Z]?\d+(?:-\d+)?)", re.MULTILINE),
    re.compile(r"(?i)(DD\s+Form\s+\d+)", re.MULTILINE),
    re.compile(r"(?i)(DI-[A-Z]+-\d+[A-Z]?)", re.MULTILINE),
]

# Obligation-level keywords
_OBLIGATION_PATTERNS = {
    "shall": re.compile(r"\bshall\b", re.IGNORECASE),
    "must": re.compile(r"\bmust\b", re.IGNORECASE),
    "will": re.compile(r"\bwill\b", re.IGNORECASE),
    "should": re.compile(r"\bshould\b", re.IGNORECASE),
    "may": re.compile(r"\bmay\b", re.IGNORECASE),
}

# CDRL / deliverable pattern
_CDRL_PATTERN = re.compile(
    r"(?:CDRL|Data\s+Item|Deliverable)\s*(?:#|No\.?|Number)?\s*[:\s]*([A-Z]?\d+(?:-\d+)?)",
    re.IGNORECASE,
)

# Delivery schedule pattern
_DELIVERY_PATTERN = re.compile(
    r"(?:deliver(?:ed|y|able)?|submit(?:ted)?|provide(?:d)?|due)\s+"
    r"(?:within|by|no\s+later\s+than|NLT)\s+"
    r"([^.;]{5,120})[.;]",
    re.IGNORECASE,
)


def _detect_obligation_level(text):
    """Determine the obligation level of a requirement statement.

    Returns one of: 'shall', 'must', 'will', 'should', 'may', 'unknown'.
    Priority order: shall > must > will > should > may.
    """
    for level in ("shall", "must", "will", "should", "may"):
        if _OBLIGATION_PATTERNS[level].search(text):
            return level
    return "unknown"


def _detect_cross_references(text):
    """Extract all cross-references from a requirement statement.

    Returns list of dicts with 'type' and 'reference' keys.
    """
    refs = []
    seen = set()
    for pat in _XREF_PATTERNS:
        for m in pat.finditer(text):
            ref_text = m.group(1) if m.lastindex else m.group(0)
            ref_text = ref_text.strip()
            if ref_text.lower() not in seen:
                seen.add(ref_text.lower())
                # Classify the reference type
                ref_lower = ref_text.lower()
                if "far " in ref_lower:
                    ref_type = "far_clause"
                elif "dfars" in ref_lower:
                    ref_type = "dfars_clause"
                elif "section" in ref_lower:
                    ref_type = "section"
                elif "attachment" in ref_lower or "exhibit" in ref_lower:
                    ref_type = "attachment"
                elif "cdrl" in ref_lower or "di-" in ref_lower:
                    ref_type = "cdrl"
                elif "dd form" in ref_lower:
                    ref_type = "form"
                else:
                    ref_type = "other"
                refs.append({"type": ref_type, "reference": ref_text})
    return refs


def _extract_sow_requirements(text):
    """Extract requirements from Section C / Statement of Work.

    Focuses on shall/must/will statements with SOW context.
    """
    requirements = []
    seen = set()

    for m in _REQ_PATTERN.finditer(text):
        full = m.group(0).strip()
        norm = full[:80].lower()
        if norm in seen:
            continue
        seen.add(norm)

        obligation = _detect_obligation_level(full)
        xrefs = _detect_cross_references(full)

        requirements.append({
            "requirement_text": full,
            "obligation_level": obligation,
            "cross_references": xrefs if xrefs else None,
            "source_section": "section_c",
        })

    return requirements


def _extract_special_requirements(text):
    """Extract requirements from Section H (Special Contract Requirements).

    Extracts clauses, CDRLs, and special provisions.
    """
    requirements = []
    seen = set()

    # Extract shall/must/will statements
    for m in _REQ_PATTERN.finditer(text):
        full = m.group(0).strip()
        norm = full[:80].lower()
        if norm in seen:
            continue
        seen.add(norm)

        obligation = _detect_obligation_level(full)
        xrefs = _detect_cross_references(full)

        requirements.append({
            "requirement_text": full,
            "obligation_level": obligation,
            "cross_references": xrefs if xrefs else None,
            "source_section": "section_h",
        })

    # Extract CDRLs referenced in Section H
    for m in _CDRL_PATTERN.finditer(text):
        cdrl_id = m.group(1).strip()
        ctx_start = max(0, m.start() - 100)
        ctx_end = min(len(text), m.end() + 200)
        context = text[ctx_start:ctx_end].strip()

        cdrl_key = f"cdrl_{cdrl_id}".lower()
        if cdrl_key not in seen:
            seen.add(cdrl_key)
            requirements.append({
                "requirement_text": f"CDRL {cdrl_id}: {context[:200]}",
                "obligation_level": "shall",
                "cross_references": [{"type": "cdrl", "reference": f"CDRL {cdrl_id}"}],
                "source_section": "section_h",
            })

    return requirements


def _extract_deliverables(text):
    """Extract requirements from Section F (Deliverables & Performance).

    Focuses on delivery schedules, milestones, and performance requirements.
    """
    requirements = []
    seen = set()

    # Standard shall/must/will statements
    for m in _REQ_PATTERN.finditer(text):
        full = m.group(0).strip()
        norm = full[:80].lower()
        if norm in seen:
            continue
        seen.add(norm)

        obligation = _detect_obligation_level(full)
        xrefs = _detect_cross_references(full)

        requirements.append({
            "requirement_text": full,
            "obligation_level": obligation,
            "cross_references": xrefs if xrefs else None,
            "source_section": "section_f",
        })

    # Delivery schedule items
    for m in _DELIVERY_PATTERN.finditer(text):
        full = m.group(0).strip()
        norm = full[:80].lower()
        if norm not in seen:
            seen.add(norm)
            requirements.append({
                "requirement_text": full,
                "obligation_level": "shall",
                "cross_references": _detect_cross_references(full) or None,
                "source_section": "section_f",
            })

    return requirements


def _extract_attachment_requirements(text):
    """Extract requirements from Section J (Attachments/Exhibits).

    Identifies referenced documents and their incorporation requirements.
    """
    requirements = []
    seen = set()

    # Extract shall/must/will statements
    for m in _REQ_PATTERN.finditer(text):
        full = m.group(0).strip()
        norm = full[:80].lower()
        if norm in seen:
            continue
        seen.add(norm)

        obligation = _detect_obligation_level(full)
        xrefs = _detect_cross_references(full)

        requirements.append({
            "requirement_text": full,
            "obligation_level": obligation,
            "cross_references": xrefs if xrefs else None,
            "source_section": "section_j",
        })

    # Detect attachment references with descriptions
    attach_pat = re.compile(
        r"(?:Attachment|Exhibit)\s+([A-Z0-9]+)\s*[\-–:]\s*([^\n]{5,200})",
        re.IGNORECASE,
    )
    for m in attach_pat.finditer(text):
        attach_id = m.group(1).strip()
        attach_desc = m.group(2).strip()
        norm = f"attach_{attach_id}".lower()
        if norm not in seen:
            seen.add(norm)
            requirements.append({
                "requirement_text": f"Attachment {attach_id}: {attach_desc}",
                "obligation_level": "shall",
                "cross_references": [{"type": "attachment", "reference": f"Attachment {attach_id}"}],
                "source_section": "section_j",
            })

    return requirements


def shred_solicitation(file_path, proposal_id=None, db_path=None):
    """Deep-extract ALL requirements from a solicitation document.

    Unlike parse_solicitation (which only extracts Section L/M), this
    "shreds" the entire document extracting requirements from:
      - Section C / SOW
      - Section F (Deliverables)
      - Section H (Special Requirements)
      - Section J (Attachments)
      - Section L (Instructions)
      - Section M (Evaluation Criteria)

    Each requirement includes obligation level, source section, and
    cross-references to other documents/sections.

    Args:
        file_path: Path to solicitation document (PDF, DOCX, TXT).
        proposal_id: If provided, store shredded requirements in DB.
        db_path: Override database path.

    Returns:
        dict with shredded requirements by section, totals, and metadata.
    """
    text = _read_file(file_path)
    all_requirements = []

    # --- Extract Section C / SOW ---
    _stop_after_c = [
        re.compile(r"(?i)\bSECTION\s+[D-Z][\.\s:\-—]+", re.MULTILINE),
    ]
    raw_c = _find_section(text, _SECTION_C_PATTERNS, _stop_after_c)
    sow_reqs = _extract_sow_requirements(raw_c) if raw_c else []
    all_requirements.extend(sow_reqs)

    # --- Extract Section F (Deliverables) ---
    _stop_after_f = [
        re.compile(r"(?i)\bSECTION\s+[G-Z][\.\s:\-—]+", re.MULTILINE),
    ]
    raw_f = _find_section(text, _SECTION_F_PATTERNS, _stop_after_f)
    deliverable_reqs = _extract_deliverables(raw_f) if raw_f else []
    all_requirements.extend(deliverable_reqs)

    # --- Extract Section H (Special Requirements) ---
    _stop_after_h = [
        re.compile(r"(?i)\bSECTION\s+[I-Z][\.\s:\-—]+", re.MULTILINE),
    ]
    raw_h = _find_section(text, _SECTION_H_PATTERNS, _stop_after_h)
    special_reqs = _extract_special_requirements(raw_h) if raw_h else []
    all_requirements.extend(special_reqs)

    # --- Extract Section J (Attachments) ---
    _stop_after_j = [
        re.compile(r"(?i)\bSECTION\s+[K-Z][\.\s:\-—]+", re.MULTILINE),
    ]
    raw_j = _find_section(text, _SECTION_J_PATTERNS, _stop_after_j)
    attachment_reqs = _extract_attachment_requirements(raw_j) if raw_j else []
    all_requirements.extend(attachment_reqs)

    # --- Extract Section L (Instructions) — reuse existing parser ---
    parsed = parse_text(text)
    for item in parsed.get("sections_l", []):
        inst_text = item.get("instruction_text", "")
        all_requirements.append({
            "requirement_text": inst_text,
            "obligation_level": _detect_obligation_level(inst_text),
            "cross_references": _detect_cross_references(inst_text) or None,
            "source_section": "section_l",
        })

    # --- Extract Section M (Evaluation Criteria) ---
    for item in parsed.get("sections_m", []):
        factor = item.get("factor", "")
        subfactors = item.get("subfactors") or []
        full_text = factor
        if subfactors:
            sf_text = "; ".join(sf.get("text", "") for sf in subfactors)
            full_text = f"{factor} [Subfactors: {sf_text}]"
        all_requirements.append({
            "requirement_text": full_text,
            "obligation_level": "shall",
            "cross_references": _detect_cross_references(full_text) or None,
            "source_section": "section_m",
        })

    # Deduplicate by first 80 chars of requirement text
    deduped = []
    seen_norms = set()
    for req in all_requirements:
        norm = req["requirement_text"][:80].lower()
        if norm not in seen_norms:
            seen_norms.add(norm)
            req["requirement_id"] = f"REQ-{_uid()[:8]}"
            deduped.append(req)

    # Count by section
    section_counts = {}
    for req in deduped:
        src = req["source_section"]
        section_counts[src] = section_counts.get(src, 0) + 1

    # Count by obligation level
    obligation_counts = {}
    for req in deduped:
        obl = req["obligation_level"]
        obligation_counts[obl] = obligation_counts.get(obl, 0) + 1

    # Count cross-references
    total_xrefs = sum(
        len(req.get("cross_references") or []) for req in deduped
    )

    result = {
        "source_file": str(file_path),
        "total_requirements": len(deduped),
        "section_counts": section_counts,
        "obligation_counts": obligation_counts,
        "total_cross_references": total_xrefs,
        "requirements": deduped,
        "format_requirements": parsed.get("format_requirements", {}),
        "shredded_at": _now(),
    }

    # Store in DB if proposal_id provided
    if proposal_id:
        conn = _get_db(db_path)
        try:
            # Ensure shredded_requirements table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS shredded_requirements (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL REFERENCES proposals(id),
                    requirement_id TEXT NOT NULL,
                    requirement_text TEXT NOT NULL,
                    obligation_level TEXT NOT NULL DEFAULT 'unknown'
                        CHECK(obligation_level IN ('shall', 'must', 'will',
                              'should', 'may', 'unknown')),
                    source_section TEXT NOT NULL
                        CHECK(source_section IN ('section_c', 'section_f',
                              'section_h', 'section_j', 'section_l',
                              'section_m', 'other')),
                    cross_references TEXT,
                    compliance_status TEXT NOT NULL DEFAULT 'not_addressed'
                        CHECK(compliance_status IN ('not_addressed',
                              'partially_addressed', 'fully_addressed',
                              'not_applicable')),
                    mapped_section_id TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_shreq_prop
                    ON shredded_requirements(proposal_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_shreq_source
                    ON shredded_requirements(source_section)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_shreq_obligation
                    ON shredded_requirements(obligation_level)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_shreq_status
                    ON shredded_requirements(compliance_status)
            """)

            # Insert requirements
            for req in deduped:
                conn.execute(
                    "INSERT INTO shredded_requirements "
                    "(id, proposal_id, requirement_id, requirement_text, "
                    "obligation_level, source_section, cross_references, "
                    "compliance_status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'not_addressed', ?)",
                    (
                        _uid(),
                        proposal_id,
                        req["requirement_id"],
                        req["requirement_text"],
                        req["obligation_level"],
                        req["source_section"],
                        json.dumps(req["cross_references"]) if req.get("cross_references") else None,
                        _now(),
                    ),
                )

            # Also run standard L/M parse and store
            conn.execute(
                "UPDATE proposals SET section_l_parsed = ?, section_m_parsed = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    json.dumps(parsed.get("sections_l", [])),
                    json.dumps(parsed.get("sections_m", [])),
                    _now(),
                    proposal_id,
                ),
            )

            _audit(conn, "proposal.shredded",
                   f"Shredded solicitation from {file_path}",
                   "proposal", proposal_id,
                   json.dumps({
                       "total": len(deduped),
                       "sections": section_counts,
                       "obligations": obligation_counts,
                       "cross_refs": total_xrefs,
                   }))
            conn.commit()
        finally:
            conn.close()

    return result


def get_shredded_requirements(proposal_id, source_section=None,
                               obligation_level=None, db_path=None):
    """Retrieve shredded requirements for a proposal with optional filters.

    Args:
        proposal_id: Proposal ID.
        source_section: Filter by source section (section_c, section_f, etc.).
        obligation_level: Filter by obligation (shall, must, will, etc.).
        db_path: Override database path.

    Returns:
        dict with requirements list and counts.
    """
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM shredded_requirements WHERE proposal_id = ?"
        params = [proposal_id]

        if source_section:
            query += " AND source_section = ?"
            params.append(source_section)
        if obligation_level:
            query += " AND obligation_level = ?"
            params.append(obligation_level)

        query += " ORDER BY source_section, requirement_id"
        rows = conn.execute(query, params).fetchall()

        reqs = []
        for r in rows:
            req = dict(r)
            if req.get("cross_references"):
                try:
                    req["cross_references"] = json.loads(req["cross_references"])
                except (json.JSONDecodeError, TypeError):
                    pass
            reqs.append(req)

        return {
            "proposal_id": proposal_id,
            "count": len(reqs),
            "filters": {
                "source_section": source_section,
                "obligation_level": obligation_level,
            },
            "requirements": reqs,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Top-level parse function
# ---------------------------------------------------------------------------

def parse_solicitation(file_path, proposal_id=None, db_path=None):
    """Parse an RFP document and optionally store results in the database.

    Args:
        file_path: Path to the solicitation document (PDF, DOCX, or TXT).
        proposal_id: If provided, store parsed sections in the proposals table.
        db_path: Override database path.

    Returns:
        dict with parsed Section L, Section M, requirements, and metadata.
    """
    text = _read_file(file_path)
    result = parse_text(text)
    result["source_file"] = str(file_path)
    result["requirements"] = extract_requirements(text)
    result["parsed_at"] = _now()

    if proposal_id:
        conn = _get_db(db_path)
        try:
            conn.execute(
                "UPDATE proposals SET section_l_parsed = ?, section_m_parsed = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    json.dumps(result["sections_l"]),
                    json.dumps(result["sections_m"]),
                    _now(),
                    proposal_id,
                ),
            )
            _audit(conn, "proposal.parsed", f"Parsed solicitation from {file_path}",
                   "proposal", proposal_id,
                   json.dumps({"l_count": len(result["sections_l"]),
                               "m_count": len(result["sections_m"]),
                               "req_count": len(result["requirements"])}))
            conn.commit()
        finally:
            conn.close()

    return result


# ---------------------------------------------------------------------------
# Compliance matrix generation
# ---------------------------------------------------------------------------

def generate_compliance_matrix(sections_l, sections_m, proposal_id, db_path=None):
    """Create compliance_matrices entries from parsed Section L and Section M.

    Each requirement from L and each evaluation criterion from M becomes a row
    in the compliance_matrices table with initial status 'not_addressed'.

    Returns:
        list of created matrix entry dicts.
    """
    conn = _get_db(db_path)
    entries = []
    try:
        # From Section L instructions
        for item in (sections_l or []):
            entry_id = _uid()
            req_id = item.get("id", _uid()[:6])
            req_text = item.get("instruction_text", "")
            volume = item.get("volume")
            conn.execute(
                "INSERT INTO compliance_matrices "
                "(id, proposal_id, requirement_id, requirement_text, source, volume, compliance_status, created_at) "
                "VALUES (?, ?, ?, ?, 'section_l', ?, 'not_addressed', ?)",
                (entry_id, proposal_id, req_id, req_text, volume, _now()),
            )
            entries.append({
                "id": entry_id,
                "proposal_id": proposal_id,
                "requirement_id": req_id,
                "requirement_text": req_text,
                "source": "section_l",
                "volume": volume,
                "compliance_status": "not_addressed",
            })

        # From Section M evaluation factors
        for item in (sections_m or []):
            entry_id = _uid()
            req_id = item.get("id", _uid()[:6])
            factor = item.get("factor", "")
            req_text = factor
            # Include subfactors in requirement text
            subfactors = item.get("subfactors") or []
            if subfactors:
                sf_text = "; ".join(sf.get("text", "") for sf in subfactors)
                req_text = f"{factor} [Subfactors: {sf_text}]"
            conn.execute(
                "INSERT INTO compliance_matrices "
                "(id, proposal_id, requirement_id, requirement_text, source, compliance_status, created_at) "
                "VALUES (?, ?, ?, ?, 'section_m', 'not_addressed', ?)",
                (entry_id, proposal_id, req_id, req_text, _now()),
            )
            entries.append({
                "id": entry_id,
                "proposal_id": proposal_id,
                "requirement_id": req_id,
                "requirement_text": req_text,
                "source": "section_m",
                "compliance_status": "not_addressed",
            })

        _audit(conn, "compliance.matrix_generated",
               f"Generated compliance matrix with {len(entries)} entries",
               "proposal", proposal_id,
               json.dumps({"l_count": len(sections_l or []),
                            "m_count": len(sections_m or []),
                            "total": len(entries)}))
        conn.commit()
    finally:
        conn.close()

    return entries


def get_compliance_matrix(proposal_id, db_path=None):
    """Retrieve the compliance matrix for a proposal.

    Returns:
        list of matrix entry dicts.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, proposal_id, requirement_id, requirement_text, source, "
            "volume, section_number, section_title, compliance_status, notes, created_at "
            "FROM compliance_matrices WHERE proposal_id = ? ORDER BY source, requirement_id",
            (proposal_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_compliance_status(matrix_entry_id, status, notes=None, db_path=None):
    """Update a compliance matrix entry's status.

    Args:
        matrix_entry_id: The compliance_matrices row id.
        status: One of 'not_addressed', 'partially_addressed', 'fully_addressed', 'not_applicable'.
        notes: Optional notes.

    Returns:
        dict with update confirmation.
    """
    valid = {"not_addressed", "partially_addressed", "fully_addressed", "not_applicable"}
    if status not in valid:
        return {"error": f"Invalid status '{status}'. Must be one of: {sorted(valid)}"}

    conn = _get_db(db_path)
    try:
        cursor = conn.execute(
            "UPDATE compliance_matrices SET compliance_status = ?, notes = ? WHERE id = ?",
            (status, notes, matrix_entry_id),
        )
        if cursor.rowcount == 0:
            return {"error": f"Matrix entry '{matrix_entry_id}' not found"}
        _audit(conn, "compliance.status_updated",
               f"Updated matrix entry {matrix_entry_id} to {status}",
               "compliance_matrix", matrix_entry_id,
               json.dumps({"status": status, "notes": notes}))
        conn.commit()
        return {"id": matrix_entry_id, "compliance_status": status, "notes": notes, "updated": True}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Parse solicitation documents for Section L/M and generate compliance matrices."
    )
    parser.add_argument("--parse", action="store_true", help="Parse a solicitation file (Section L/M only)")
    parser.add_argument("--shred", action="store_true", help="Deep-extract ALL requirements from solicitation")
    parser.add_argument("--get-shredded", action="store_true", help="Retrieve shredded requirements")
    parser.add_argument("--file", help="Path to solicitation document (PDF, DOCX, TXT)")
    parser.add_argument("--text", help="Raw text to parse directly")
    parser.add_argument("--matrix", action="store_true", help="Generate compliance matrix from parsed data")
    parser.add_argument("--matrix-from-shredded", action="store_true", help="Generate compliance matrix from shredded requirements")
    parser.add_argument("--get-matrix", action="store_true", help="Retrieve compliance matrix")
    parser.add_argument("--update-status", help="Matrix entry ID to update status")
    parser.add_argument("--status", help="New compliance status")
    parser.add_argument("--notes", help="Notes for status update")
    parser.add_argument("--source-section", help="Filter by source section (section_c, section_f, etc.)")
    parser.add_argument("--obligation", help="Filter by obligation level (shall, must, will, etc.)")
    parser.add_argument("--proposal-id", help="Proposal ID")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = {}

    if args.text:
        result = parse_text(args.text)
    elif args.shred and args.file:
        result = shred_solicitation(args.file, proposal_id=args.proposal_id)
    elif args.get_shredded and args.proposal_id:
        result = get_shredded_requirements(
            args.proposal_id,
            source_section=args.source_section,
            obligation_level=args.obligation,
        )
    elif args.matrix_from_shredded and args.proposal_id:
        # Generate compliance matrix from shredded requirements
        shredded = get_shredded_requirements(args.proposal_id)
        reqs = shredded.get("requirements", [])
        conn = _get_db()
        try:
            entries = []
            for req in reqs:
                entry_id = _uid()
                # Map source_section to compliance_matrix source
                source_map = {
                    "section_c": "sow",
                    "section_f": "other",
                    "section_h": "other",
                    "section_j": "other",
                    "section_l": "section_l",
                    "section_m": "section_m",
                }
                source = source_map.get(req.get("source_section"), "other")
                conn.execute(
                    "INSERT INTO compliance_matrices "
                    "(id, proposal_id, requirement_id, requirement_text, source, "
                    "compliance_status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 'not_addressed', ?)",
                    (entry_id, args.proposal_id, req.get("requirement_id", _uid()[:6]),
                     req["requirement_text"], source, _now()),
                )
                entries.append({
                    "id": entry_id,
                    "requirement_id": req.get("requirement_id"),
                    "source": source,
                    "requirement_text": req["requirement_text"][:100],
                })
            _audit(conn, "compliance.matrix_from_shredded",
                   f"Generated matrix from {len(entries)} shredded requirements",
                   "proposal", args.proposal_id,
                   json.dumps({"total": len(entries)}))
            conn.commit()
        finally:
            conn.close()
        result = {"proposal_id": args.proposal_id, "entries_created": len(entries), "entries": entries}
    elif args.parse and args.file:
        result = parse_solicitation(args.file, proposal_id=args.proposal_id)
    elif args.matrix and args.proposal_id:
        # Load parsed data from proposal, then generate matrix
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT section_l_parsed, section_m_parsed FROM proposals WHERE id = ?",
                (args.proposal_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            result = {"error": f"Proposal '{args.proposal_id}' not found"}
        else:
            l_data = json.loads(row["section_l_parsed"] or "[]")
            m_data = json.loads(row["section_m_parsed"] or "[]")
            entries = generate_compliance_matrix(l_data, m_data, args.proposal_id)
            result = {"proposal_id": args.proposal_id, "entries_created": len(entries), "entries": entries}
    elif args.get_matrix and args.proposal_id:
        entries = get_compliance_matrix(args.proposal_id)
        result = {"proposal_id": args.proposal_id, "count": len(entries), "entries": entries}
    elif args.update_status and args.status:
        result = update_compliance_status(args.update_status, args.status, notes=args.notes)
    else:
        parser.print_help()
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if "error" in result:
            print(f"ERROR: {result['error']}", file=sys.stderr)
            sys.exit(1)
        if "shredded_at" in result:
            print(f"Solicitation Shredder Results:")
            print(f"  Total requirements: {result['total_requirements']}")
            print(f"  Cross-references: {result['total_cross_references']}")
            print(f"\n  By section:")
            for sec, cnt in result.get("section_counts", {}).items():
                print(f"    {sec}: {cnt}")
            print(f"\n  By obligation:")
            for obl, cnt in result.get("obligation_counts", {}).items():
                print(f"    {obl}: {cnt}")
            print(f"\n  Requirements:")
            for req in result.get("requirements", [])[:20]:
                obl = req.get("obligation_level", "?")
                src = req.get("source_section", "?")
                xrefs = req.get("cross_references") or []
                xref_str = f" [{len(xrefs)} xrefs]" if xrefs else ""
                print(f"    [{src}|{obl}] {req['requirement_text'][:90]}{xref_str}")
            if result["total_requirements"] > 20:
                print(f"    ... and {result['total_requirements'] - 20} more")
        elif "sections_l" in result:
            print(f"Section L instructions: {len(result['sections_l'])}")
            for item in result["sections_l"]:
                print(f"  [{item['id']}] {item['instruction_text'][:100]}")
                if item.get("page_limit"):
                    print(f"       Page limit: {item['page_limit']}")
            print(f"\nSection M evaluation criteria: {len(result['sections_m'])}")
            for item in result["sections_m"]:
                print(f"  [{item['id']}] {item['factor'][:100]}")
                if item.get("weight_description"):
                    print(f"       Weight: {item['weight_description'][:80]}")
            if result.get("format_requirements"):
                print(f"\nFormat requirements:")
                for k, v in result["format_requirements"].items():
                    print(f"  {k}: {v}")
            if result.get("requirements"):
                print(f"\nTotal requirements extracted: {len(result['requirements'])}")
        elif "entries_created" in result:
            print(f"Compliance matrix generated: {result['entries_created']} entries")
        elif "count" in result:
            print(f"Compliance matrix ({result['count']} entries):")
            for e in result["entries"]:
                status_mark = {"not_addressed": "[ ]", "partially_addressed": "[~]",
                               "fully_addressed": "[X]", "not_applicable": "[-]"}
                mark = status_mark.get(e["compliance_status"], "[?]")
                print(f"  {mark} [{e['source']}] {e['requirement_text'][:90]}")
        elif "updated" in result:
            print(f"Updated {result['id']} -> {result['compliance_status']}")
        else:
            print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
