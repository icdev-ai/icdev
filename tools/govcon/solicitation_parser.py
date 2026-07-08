"""
Solicitation Parser — PDF-first solicitation intake path (ground-sol-01).

Parses a solicitation (RFP/RFQ) PDF or DOCX into a structured dict analogous
to rfi_document_parser.py, so proposal drafting (response_drafter.py) and
citation validation (ground-prop-02) can ground against the real document
structure instead of free text.

Extracts:
  - solicitation_number, naics, set_aside, poc_name, poc_email, due_date
  - document_sections[] — UCF Section A–M headings present in the document
  - section_l_instructions[] — numbered Section L instruction items (L.x)
  - section_m_factors[] — evaluation factors, subfactors, weights, basis of award
  - clins[] — CLIN line items (Section B), via pdfplumber word coordinates
  - volume_structure[] — proposal volume breakdown from Section L
  - submission_requirements — page limits, font, copies, portal, deadline

Usage:
    python tools/govcon/solicitation_parser.py --input <file.pdf> --json
    python tools/govcon/solicitation_parser.py --input <file.docx> --json
    python tools/govcon/solicitation_parser.py --input <file.pdf> --output parsed.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.govcon.rfi_document_parser import _find_first, extract_text
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.solicitation_parser")

# ── Regex patterns for header field extraction ──────────────────────────────

_SOL_NUMBER_RE = re.compile(
    r"(?:Solicitation|RFP|RFQ)[\s\-#]*(?:Number|No\.?|#)?\s*[:–\-]?\s*"
    r"([A-Z0-9]{2,8}-\d{2}-[A-Z]-?[A-Z0-9]{4,6}|[A-Z0-9\-]{8,30})",
    re.IGNORECASE,
)
_NAICS_RE = re.compile(r"NAICS\s*(?:Code)?\s*[:–]?\s*(\d{6})", re.IGNORECASE)
_POC_EMAIL_RE = re.compile(r"[\w.\-]+@[\w.\-]+\.\w{2,6}")
_POC_NAME_RE = re.compile(
    r"(?:Point of Contact|POC|Contracting Officer|Contract Specialist)"
    r"\s*[:–]?\s*([A-Z][a-z]+ [A-Z][a-z]+)",
    re.IGNORECASE,
)
_SET_ASIDE_RE = re.compile(
    r"(Total Small Business Set-Aside|Small Business Set-Aside|8\(a\)\s+Set-Aside"
    r"|SDVOSB Set-Aside|WOSB Set-Aside|HUBZone Set-Aside|Full and Open Competition)",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"(?:Project\s+)?Title\s*:\s*([^\n]+)", re.IGNORECASE)
_DUE_DATE_RE = re.compile(
    r"(?:no later than|proposals?\s+(?:are\s+)?due|closing date|deadline)"
    r"[^\n]{0,40}?(\d{1,2}\s+\w+\s+\d{4}|\w+ \d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)

# ── UCF document section headings (SECTION A … SECTION M) ────────────────────

_UCF_SECTION_RE = re.compile(
    r"^\s*SECTION\s+([A-M])\s*[-–—:.]?\s*([^\n]*)$", re.MULTILINE | re.IGNORECASE
)

# ── Section L patterns ────────────────────────────────────────────────────────

_L_ITEM_RE = re.compile(r"^(L[.\-]\d+(?:\.\d+)*)\s*[-–—:.]?\s+([^\n]+)", re.MULTILINE)
_VOLUME_RE = re.compile(
    r"Volume\s+([IVX]+|\d+)\s*[-–—:.]?\s*([A-Z][^\n]{2,80})", re.IGNORECASE
)

# ── Section M patterns ────────────────────────────────────────────────────────

_M_FACTOR_RE = re.compile(
    r"(?:^|\n)\s*(?:Evaluation\s+)?Factor\s+(\d+)\s*[-–—:.]?\s*([^\n]+)", re.IGNORECASE
)
_M_SUBFACTOR_RE = re.compile(
    r"(?:^|\n)\s*Sub-?factor\s+(\d+(?:\.\d+)?)\s*[-–—:.]?\s*([^\n]+)", re.IGNORECASE
)
_M_WEIGHT_RE = re.compile(r"(?:weight(?:ed)?|worth)\s*[:.]?\s*(\d{1,3})\s*%", re.IGNORECASE)
_M_BASIS_RE = re.compile(
    r"\b(best[\s-]value\s+trade-?off|lowest\s+price\s+technically\s+acceptable|LPTA)\b",
    re.IGNORECASE,
)
_M_RELATIVE_RE = re.compile(
    r"((?:significantly|slightly|somewhat)\s+more\s+important\s+than[^\n.]*"
    r"|equal\s+(?:in\s+)?importance[^\n.]*|(?:descending\s+)?order\s+of\s+importance[^\n.]*)",
    re.IGNORECASE,
)

# ── CLIN patterns (Section B line items) ─────────────────────────────────────

_CLIN_NUM_RE = re.compile(r"^\d{4}[A-Z]{0,2}$")
_CLIN_LINE_RE = re.compile(r"^(\d{4}[A-Z]{0,2})\s+(\S.+)$", re.MULTILINE)
_CLIN_TYPE_RE = re.compile(r"\b(FFP|CPFF|CPIF|CPAF|T&M|FPIF|LH|IDIQ)\b")
_CLIN_UNITS = {"EA", "LO", "MO", "HR", "JB", "LT", "YR", "DA", "WK"}


def _extract_document_sections(text: str) -> list[dict]:
    """Extract UCF Section A–M headings present in the document.

    A table of contents lists the same headings before the body, so for each
    letter the LAST occurrence wins (the body heading follows the TOC entry).
    """
    by_letter: dict[str, dict] = {}
    for m in _UCF_SECTION_RE.finditer(text):
        letter = m.group(1).upper()
        title = m.group(2).strip().rstrip(".") or f"Section {letter}"
        # Strip TOC dot leaders / trailing page numbers ("... 45")
        title = re.sub(r"[.\s]{2,}\d{1,3}\s*$", "", title).strip()
        by_letter[letter] = {"letter": letter, "title": title, "_pos": m.start()}
    sections = sorted(by_letter.values(), key=lambda s: s["_pos"])
    for s in sections:
        s.pop("_pos")
    return sections


def _split_ucf_sections(text: str) -> dict[str, str]:
    """Slice the document into per-letter UCF section bodies.

    Uses the last heading occurrence per letter (skips TOC entries), then
    each section runs to the start of the next section heading in document
    order.
    """
    positions: dict[str, int] = {}
    for m in _UCF_SECTION_RE.finditer(text):
        positions[m.group(1).upper()] = m.start()
    if not positions:
        return {}
    ordered = sorted(positions.items(), key=lambda kv: kv[1])
    bodies = {}
    for i, (letter, start) in enumerate(ordered):
        end = ordered[i + 1][1] if i + 1 < len(ordered) else len(text)
        bodies[letter] = text[start:end]
    return bodies


def _extract_section_l_instructions(l_text: str) -> list[dict]:
    """Extract numbered Section L instruction items (L.1, L.4.2, …).

    Each item carries its heading line as `title` and the body up to the
    next L.x heading (first paragraph only) as `text`.
    """
    items = []
    matches = list(_L_ITEM_RE.finditer(l_text))
    for i, m in enumerate(matches):
        number = m.group(1).replace("-", ".")
        title = m.group(2).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(l_text)
        body = l_text[body_start:body_end].strip()
        body_first = re.split(r"\n{2,}", body)[0].replace("\n", " ").strip()
        items.append({
            "number": number,
            "title": title,
            "text": body_first[:1000],
        })
    return items


def _extract_volume_structure(l_text: str) -> list[dict]:
    """Extract proposal volume breakdown (Volume I — Technical, …)."""
    volumes = []
    seen = set()
    for m in _VOLUME_RE.finditer(l_text):
        vol = m.group(1).upper()
        if vol in seen:
            continue
        seen.add(vol)
        volumes.append({"volume": vol, "title": m.group(2).strip().rstrip(".")})
    return volumes


def _extract_section_m_factors(m_text: str) -> list[dict]:
    """Extract evaluation factors with subfactors and weights from Section M.

    Subfactors and weights are attached to the factor whose heading precedes
    them in the text.
    """
    # Prose like "Factor 1 is significantly more important than Factor 2"
    # also matches the heading pattern — a heading's name starts uppercase.
    factor_matches = [
        m for m in _M_FACTOR_RE.finditer(m_text) if m.group(2).strip()[:1].isupper()
    ]
    factors = []
    seen_numbers = set()
    for i, m in enumerate(factor_matches):
        if m.group(1) in seen_numbers:
            continue
        seen_numbers.add(m.group(1))
        start = m.end()
        end = factor_matches[i + 1].start() if i + 1 < len(factor_matches) else len(m_text)
        body = m_text[start:end]
        weight_m = _M_WEIGHT_RE.search(m.group(2)) or _M_WEIGHT_RE.search(body)
        subfactors = [
            {"number": sm.group(1), "name": sm.group(2).strip().rstrip(".")}
            for sm in _M_SUBFACTOR_RE.finditer(body)
        ]
        name = m.group(2).strip().rstrip(".")
        # Drop a trailing inline weight annotation from the name
        name = re.sub(r"\s*\(?\s*weight(?:ed)?\s*[:.]?\s*\d{1,3}\s*%\s*\)?\s*$", "", name, flags=re.IGNORECASE).strip()
        factors.append({
            "factor": m.group(1),
            "name": name,
            "weight_pct": int(weight_m.group(1)) if weight_m else None,
            "subfactors": subfactors,
        })
    return factors


def _extract_basis_of_award(m_text: str) -> str:
    """Classify the award basis stated in Section M."""
    m = _M_BASIS_RE.search(m_text)
    if not m:
        return ""
    label = m.group(1).lower()
    if "lpta" in label or "lowest price" in label:
        return "lpta"
    return "best_value_tradeoff"


def _extract_relative_importance(m_text: str) -> str:
    """Extract the relative-importance statement (e.g. 'Factor 1 is
    significantly more important than Factor 2')."""
    m = _M_RELATIVE_RE.search(m_text)
    return m.group(1).strip() if m else ""


def _clin_from_description(clin: str, desc_words: list[str], price_words: list[str]) -> dict:
    """Assemble a CLIN dict from bucketed description/pricing tokens."""
    description = " ".join(desc_words).strip()
    type_m = _CLIN_TYPE_RE.search(description)
    quantity = None
    unit = ""
    prices = []
    for tok in price_words:
        clean = tok.replace(",", "").lstrip("$")
        if tok.upper() in _CLIN_UNITS:
            unit = tok.upper()
        elif tok.startswith("$"):
            prices.append(tok)
        elif quantity is None and clean.isdigit():
            quantity = int(clean)
    return {
        "clin": clin,
        "description": description[:500],
        "contract_type": type_m.group(1) if type_m else "",
        "quantity": quantity,
        "unit": unit,
        "unit_price": prices[0] if prices else "",
        "amount": prices[1] if len(prices) > 1 else "",
    }


def _extract_clins_pdf_layout(path: Path) -> list[dict]:
    """Extract CLIN table rows from a PDF using word coordinates.

    Borderless CLIN schedules (Section B) render each row y-aligned: the CLIN
    number ("0001"), its description, and pricing columns share the same top
    coordinate in fixed columns. Plain-text extraction scrambles those
    columns, so this walks words line-by-line (mirrors
    rfi_document_parser._extract_questionnaire_pdf_layout):
      - a line whose word matches NNNN[AA] at the CLIN-column anchor starts a
        new line item;
      - remaining words go to description or pricing by their x position,
        using the QTY/UNIT header x when present.
    Returns [] when pdfplumber is unavailable or no table is detected, so
    callers can fall back to the text-regex path.
    """
    try:
        import pdfplumber
    except ImportError:
        return []

    from collections import Counter

    stop_re = re.compile(r"^SECTION\s+C\b", re.IGNORECASE)

    try:
        with pdfplumber.open(str(path)) as pdf:
            pages_words = [p.extract_words() for p in pdf.pages]
    except Exception as exc:
        logger.warning("pdfplumber extraction failed: %s", exc)
        return []

    anchors = [
        round(w["x0"])
        for words in pages_words
        for w in words
        if _CLIN_NUM_RE.match(w["text"])
    ]
    if not anchors:
        return []
    anchor = Counter(anchors).most_common(1)[0][0]
    desc_min = anchor + 15

    # Pricing column boundary: the QTY/UNIT header x when present, else fixed
    price_min = anchor + 280
    for words in pages_words:
        for w in words:
            if w["text"].upper() in {"QTY", "QUANTITY"}:
                price_min = round(w["x0"]) - 5
                break

    items: list[dict] = []
    current: dict | None = None
    stopped = False

    for words in pages_words:
        if stopped:
            break
        lines: dict[int, list] = {}
        for w in words:
            lines.setdefault(round(w["top"] / 3), []).append(w)
        for key in sorted(lines):
            line_words = sorted(lines[key], key=lambda w: w["x0"])
            line_text = " ".join(w["text"] for w in line_words)
            if "UNCLASSIFIED" in line_text:
                continue
            if stop_re.match(line_text):
                stopped = True
                break
            clin_idx = next(
                (i for i, w in enumerate(line_words)
                 if _CLIN_NUM_RE.match(w["text"]) and abs(w["x0"] - anchor) <= 6),
                None,
            )
            if clin_idx is not None:
                if current:
                    items.append(current)
                current = {"clin": line_words[clin_idx]["text"], "_desc": [], "_price": []}
                line_words = line_words[clin_idx + 1:]
            else:
                line_words = [w for w in line_words if w["x0"] >= desc_min]
            if current is None or not line_words:
                continue
            for w in line_words:
                if w["x0"] < desc_min:
                    continue
                bucket = "_desc" if w["x0"] < price_min else "_price"
                current[bucket].append(w["text"])
    if current:
        items.append(current)

    clins = []
    seen = set()
    for it in items:
        if it["clin"] in seen:
            continue
        seen.add(it["clin"])
        clins.append(_clin_from_description(it["clin"], it["_desc"], it["_price"]))
    return clins


def _extract_clins_text(text: str) -> list[dict]:
    """Extract CLIN line items from plain text (Section B region when found).

    Fallback for DOCX/TXT and PDFs where the coordinate path found no table.
    """
    region = text
    sections = _split_ucf_sections(text)
    if "B" in sections:
        region = sections["B"]

    clins = []
    seen = set()
    for m in _CLIN_LINE_RE.finditer(region):
        clin = m.group(1)
        if clin in seen:
            continue
        rest = m.group(2).strip()
        # Skip year-like or figure-like false positives with no letters after
        if not re.search(r"[A-Za-z]", rest):
            continue
        seen.add(clin)
        desc_words = []
        price_words = []
        for tok in rest.split():
            clean = tok.replace(",", "").lstrip("$")
            if tok.startswith("$") or tok.upper() in _CLIN_UNITS or clean.isdigit():
                price_words.append(tok)
            else:
                desc_words.append(tok)
        clins.append(_clin_from_description(clin, desc_words, price_words))
    return clins


def _extract_submission_requirements(text: str) -> dict:
    """Extract page limits, font, margins, copies, portal, and deadline."""
    req = {}

    page_match = re.search(
        r"(?:limited to (?:a total of )?|not\s+to\s+exceed\s+|maximum\s+of\s+|no\s+more\s+than\s+)"
        r"(\d+)[\s-]*page[s]?",
        text, re.IGNORECASE,
    )
    req["max_pages"] = int(page_match.group(1)) if page_match else None

    font_match = re.search(
        r"(?:minimum\s+)?(\d{1,2})[\s-]*point\s*(?:font|type|Times|Arial|Calibri)",
        text, re.IGNORECASE,
    )
    req["font_size_pt"] = int(font_match.group(1)) if font_match else 12

    margin_match = re.search(
        r"(\d+(?:\.\d+)?)[\s-]*(?:inch|in\.?|\")\s*margins?|margins?\s*(?:of|at least)?\s*"
        r"(\d+(?:\.\d+)?)[\s-]*(?:inch|in\.?|\")",
        text, re.IGNORECASE,
    )
    req["margins_in"] = float(margin_match.group(1) or margin_match.group(2)) if margin_match else None

    copies_match = re.search(r"(\d+)\s+(?:hard\s+)?cop(?:y|ies)", text, re.IGNORECASE)
    req["copies"] = int(copies_match.group(1)) if copies_match else None

    portal_match = re.search(
        r"(?:Submit|submitted|via|through)\s+(?:the\s+)?(SAM\.gov|PIEE|[A-Z]{2,10}\s+portal)",
        text, re.IGNORECASE,
    )
    req["submission_portal"] = portal_match.group(1).strip() if portal_match else ""

    file_match = re.search(r"[Ff]ile\s+naming[^:]*:\s*([^\n]+)", text)
    req["file_naming"] = file_match.group(1).strip() if file_match else ""

    req["due_date"] = _find_first(_DUE_DATE_RE, text)

    q_match = re.search(
        r"[Qq]uestion[s]?\s+(?:must\s+be\s+)?submitted.{0,220}?by[^\n]{0,30}?"
        r"(\d{1,2}\s+\w+\s+\d{4}|\w+ \d{1,2},\s*\d{4})",
        text, re.DOTALL,
    )
    req["questions_due_date"] = q_match.group(1).strip() if q_match else ""

    return req


def parse_solicitation(file_path: str) -> dict:
    """Parse a solicitation document and return a structured dict for the
    /proposals pipeline (drafting grounding + citation validation)."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Solicitation document not found: {file_path}")

    text = extract_text(path)
    if not text.strip():
        raise ValueError(
            f"Could not extract text from: {file_path}. Ensure pdfminer.six or pypdf is installed."
        )

    document_sections = _extract_document_sections(text)
    sections_map = _split_ucf_sections(text)
    # Combined-synopsis solicitations often have no SECTION headings; the
    # L.x / Factor N patterns are specific enough to scan the whole document.
    l_text = sections_map.get("L", text)
    m_text = sections_map.get("M", text)

    section_l_instructions = _extract_section_l_instructions(l_text)
    volume_structure = _extract_volume_structure(l_text)
    section_m_factors = _extract_section_m_factors(m_text)
    basis_of_award = _extract_basis_of_award(m_text)
    relative_importance = _extract_relative_importance(m_text)

    # PDFs: coordinate-based table extraction is authoritative (plain-text
    # extraction scrambles the borderless CLIN schedule's columns).
    clins = []
    if path.suffix.lower() == ".pdf":
        clins = _extract_clins_pdf_layout(path)
    if not clins:
        clins = _extract_clins_text(text)

    submission = _extract_submission_requirements(text)

    title_match = _TITLE_RE.search(text)
    title = title_match.group(1).strip() if title_match else path.stem.replace("_", " ")

    result = {
        "id": str(uuid.uuid4()),
        "source": "solicitation_document",
        "file_path": str(path.resolve()),
        "solicitation_number": _find_first(_SOL_NUMBER_RE, text),
        "naics": _find_first(_NAICS_RE, text),
        "title": title,
        "set_aside": _find_first(_SET_ASIDE_RE, text),
        "poc_name": _find_first(_POC_NAME_RE, text),
        "poc_email": _find_first(_POC_EMAIL_RE, text),
        "due_date": submission.get("due_date", ""),
        "questions_due_date": submission.get("questions_due_date", ""),
        "document_sections": document_sections,
        "section_l_instructions": section_l_instructions,
        "volume_structure": volume_structure,
        "section_m_factors": section_m_factors,
        "basis_of_award": basis_of_award,
        "relative_importance": relative_importance,
        "clins": clins,
        "submission_requirements": submission,
        "raw_text_length": len(text),
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(
        "Solicitation parsed",
        extra={
            "solicitation_number": result["solicitation_number"],
            "sections": len(document_sections),
            "l_instructions": len(section_l_instructions),
            "m_factors": len(section_m_factors),
            "clins": len(clins),
        },
    )
    return result


# Alias — mirrors rfi_document_parser's parse_rfi_document convention.
parse_solicitation_document = parse_solicitation


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse solicitation document into structured JSON")
    parser.add_argument("--input", required=True, help="Path to solicitation PDF or DOCX file")
    parser.add_argument("--output", help="Optional output JSON file path")
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    args = parser.parse_args()

    try:
        result = parse_solicitation(args.input)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        sys.exit(1)

    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({"status": "ok", "output": args.output}, indent=2))
    elif args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"Solicitation: {result['solicitation_number']} | Sections: {len(result['document_sections'])} | "
            f"L items: {len(result['section_l_instructions'])} | M factors: {len(result['section_m_factors'])} | "
            f"CLINs: {len(result['clins'])}"
        )


if __name__ == "__main__":
    main()
