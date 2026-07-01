"""
RFI Document Parser — Phase 59 Extension (PDF-first intake path).

Parses an RFI PDF or DOCX into a structured JSON object matching the SAM.gov
opportunity format used by the existing govcon pipeline, so capability_mapper.py
and response_drafter.py can reuse without modification.

Extracts:
  - rfi_number, naics, poc_name, poc_email, due_date
  - objectives[] — capability gap objectives (lettered A-F or numbered)
  - questionnaire_parts[] — {part, item_number, topic, question}
  - submission_requirements — page limits, format, file naming, deadline

Usage:
    python tools/govcon/rfi_document_parser.py --input <file.pdf> --json
    python tools/govcon/rfi_document_parser.py --input <file.docx> --json
    python tools/govcon/rfi_document_parser.py --input <file.pdf> --output parsed.json
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

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.rfi_document_parser")

# ── Regex patterns for field extraction ──────────────────────────────────────

_RFI_NUMBER_RE = re.compile(
    r"RFI[\s\-#]*(?:Number|#|No\.?)?\s*[:–\-]?\s*([A-Z0-9\-]{5,30})", re.IGNORECASE
)
_NAICS_RE = re.compile(r"NAICS\s*[:–]?\s*(\d{6})", re.IGNORECASE)
_POC_EMAIL_RE = re.compile(r"[\w.\-]+@[\w.\-]+\.\w{2,6}")
_POC_NAME_RE = re.compile(
    r"(?:Point of Contact|POC|Contracting Officer)\s*[:–]?\s*([A-Z][a-z]+ [A-Z][a-z]+)", re.IGNORECASE
)
_DUE_DATE_RE = re.compile(
    r"(?:no later than|by|due|deadline)[^\d]*(\d{1,2}[\s/]\w+[\s/]\d{4}|\w+ \d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)
_PAGE_LIMIT_RE = re.compile(r"(\d+)[\s-]*page[s]?\s*(?:limit|maximum|max)", re.IGNORECASE)
_OBJECTIVE_RE = re.compile(
    r"([A-F])\.\s{0,3}([A-Z][^:\n]{5,80}):\s+([^\n].+?)(?=\n[A-F]\.\s|\nNSA seeks|\nThis RFI|\Z)",
    re.DOTALL,
)
_PART_RE = re.compile(
    r"Part\s+(\d+)[:\s]+([^\n]+)", re.IGNORECASE
)
_ITEM_RE = re.compile(
    r"(\d+\.\d+)\s+([A-Za-z /&]+)\s{2,}(.+?)(?=\n\d+\.\d+|\Z)", re.DOTALL
)


def _extract_text_from_pdf(path: Path) -> str:
    """Extract raw text from PDF. Tries pdfminer first, falls back to pypdf."""
    text = ""
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        text = pdfminer_extract(str(path))
        if text and len(text.strip()) > 100:
            return text
    except ImportError:
        pass

    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages)
        if text and len(text.strip()) > 100:
            return text
    except ImportError:
        pass

    try:
        import PyPDF2
        with open(path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(pages)
    except ImportError:
        logger.warning("No PDF extraction library available. Install pdfminer.six or pypdf.")

    return text


def _extract_text_from_docx(path: Path) -> str:
    """Extract raw text from DOCX."""
    try:
        from docx import Document
        doc = Document(str(path))
        return "\n".join(para.text for para in doc.paragraphs)
    except ImportError:
        logger.warning("python-docx not installed. Run: pip install python-docx")
        return ""


def extract_text(path: Path) -> str:
    """Extract text from PDF or DOCX."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_text_from_pdf(path)
    elif suffix in {".docx", ".doc"}:
        return _extract_text_from_docx(path)
    else:
        return path.read_text(encoding="utf-8", errors="replace")


def _find_first(pattern: re.Pattern, text: str, group: int = 1) -> str:
    m = pattern.search(text)
    if not m:
        return ""
    try:
        return m.group(group).strip()
    except IndexError:
        return m.group(0).strip()


def _extract_objectives(text: str) -> list[dict]:
    """Extract lettered capability objectives (A. Title: Description...)."""
    objectives = []
    # Try strict format (A. Title: Description)
    for m in _OBJECTIVE_RE.finditer(text):
        letter, title, body = m.group(1), m.group(2).strip(), m.group(3).strip()
        # Trim body to first paragraph only (before double newline)
        body_first = re.split(r"\n{2,}", body)[0].replace("\n", " ").strip()
        objectives.append({
            "id": f"obj-{letter.lower()}",
            "letter": letter,
            "title": title,
            "description": body_first,
        })

    if objectives:
        return objectives

    # Fallback: scan for lines starting with capital letter + period
    pattern = re.compile(r"\n([A-F])\.\s+(.+?)(?=\n[A-F]\.|$)", re.DOTALL)
    for m in pattern.finditer(text):
        letter, body = m.group(1), m.group(2).strip()
        # First sentence as title, rest as description
        sentences = re.split(r"(?<=[.!?])\s+", body, maxsplit=1)
        title = sentences[0] if sentences else body[:80]
        description = sentences[1] if len(sentences) > 1 else body
        objectives.append({
            "id": f"obj-{letter.lower()}",
            "letter": letter,
            "title": title[:120],
            "description": description[:500].replace("\n", " ").strip(),
        })

    return objectives


def _extract_questionnaire_parts(text: str) -> list[dict]:
    """Extract Part N / item N.M / topic / question table rows."""
    parts = []

    # Find table-like question sections: "N.M   Topic   Question text"
    item_pattern = re.compile(
        r"(\d\.\d)\s{2,}([^\n]{3,50})\s{2,}(.+?)(?=\n\d\.\d|\nPart \d|\Z)",
        re.DOTALL,
    )
    for m in item_pattern.finditer(text):
        item_num = m.group(1)
        topic = m.group(2).strip()
        question = m.group(3).replace("\n", " ").strip()
        # Derive part number from item (e.g. "2.3" → Part 2)
        part_num = item_num.split(".")[0]
        parts.append({
            "part": f"Part {part_num}",
            "item_number": item_num,
            "topic": topic,
            "question": question[:1000],
        })

    return parts


def _extract_submission_requirements(text: str) -> dict:
    """Extract page limits, format, file naming, and deadline."""
    req = {}

    page_match = _PAGE_LIMIT_RE.search(text)
    req["max_pages"] = int(page_match.group(1)) if page_match else None

    # Appendix pages
    appendix_match = re.search(r"(\d+)[\s-]*page[s]?\s*(?:technical\s+)?appendix", text, re.IGNORECASE)
    req["max_appendix_pages"] = int(appendix_match.group(1)) if appendix_match else None

    # Font size
    font_match = re.search(r"(\d+)[\s-]*point", text, re.IGNORECASE)
    req["font_size_pt"] = int(font_match.group(1)) if font_match else 11

    # File naming convention
    file_match = re.search(r"[Ff]ile\s+naming[^:]*:\s*([^\n]+)", text)
    req["file_naming"] = file_match.group(1).strip() if file_match else ""

    # Submission portal
    portal_match = re.search(r"(?:Submit|via|through)\s+([A-Z]+\s+portal)", text, re.IGNORECASE)
    req["submission_portal"] = portal_match.group(1).strip() if portal_match else ""

    # Due date
    req["due_date"] = _find_first(_DUE_DATE_RE, text)

    # Questions due date
    q_match = re.search(
        r"[Qq]uestion[s]?\s+(?:must\s+be\s+)?submitted[^\d]*(\d{1,2}[:/]\d{1,2}[:/]\d{4}|\w+ \d{1,2},\s*\d{4})",
        text,
    )
    req["questions_due_date"] = q_match.group(1).strip() if q_match else ""

    return req


def parse_rfi(file_path: str) -> dict:
    """
    Parse an RFI document and return a structured dict compatible with
    the SAM.gov opportunity format used by capability_mapper.py.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"RFI document not found: {file_path}")

    text = extract_text(path)
    if not text.strip():
        raise ValueError(f"Could not extract text from: {file_path}. Ensure pdfminer.six or pypdf is installed.")

    rfi_number = _find_first(_RFI_NUMBER_RE, text)
    naics = _find_first(_NAICS_RE, text)
    poc_email = _find_first(_POC_EMAIL_RE, text)
    poc_name = _find_first(_POC_NAME_RE, text)
    objectives = _extract_objectives(text)
    questionnaire_parts = _extract_questionnaire_parts(text)
    submission = _extract_submission_requirements(text)

    # Build title — prefer "AI/ML" or "Name of RFI:" line; avoid FOUO markers
    title_match = re.search(
        r"Name of RFI\s*:\s*([^\n]+)|(?:^|\n)(AI/ML[^\n]{5,80})\n", text, re.IGNORECASE
    )
    if title_match:
        title = (title_match.group(1) or title_match.group(2) or "").strip()
    else:
        title = path.stem.replace("_", " ")

    result = {
        "id": str(uuid.uuid4()),
        "source": "rfi_document",
        "file_path": str(path.resolve()),
        "rfi_number": rfi_number,
        "naics": naics,
        "title": title,
        "poc_name": poc_name,
        "poc_email": poc_email,
        "due_date": submission.get("due_date", ""),
        "questions_due_date": submission.get("questions_due_date", ""),
        "objectives": objectives,
        "questionnaire_parts": questionnaire_parts,
        "submission_requirements": submission,
        "raw_text_length": len(text),
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(
        "RFI parsed",
        extra={
            "rfi_number": rfi_number,
            "objectives": len(objectives),
            "questionnaire_items": len(questionnaire_parts),
        },
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse RFI document into structured JSON")
    parser.add_argument("--input", required=True, help="Path to RFI PDF or DOCX file")
    parser.add_argument("--output", help="Optional output JSON file path")
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    args = parser.parse_args()

    try:
        result = parse_rfi(args.input)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        sys.exit(1)

    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({"status": "ok", "output": args.output}, indent=2))
    elif args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"RFI: {result['rfi_number']} | Objectives: {len(result['objectives'])} | Items: {len(result['questionnaire_parts'])}")


if __name__ == "__main__":
    main()
