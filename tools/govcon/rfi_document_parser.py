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


def _questionnaire_region(text: str) -> str:
    """Slice text to the vendor questionnaire section so that submission
    instructions (5.x) and Q&A instructions (6.x) headings are not mistaken
    for questionnaire items."""
    start_m = re.search(r"Request Information", text, re.IGNORECASE)
    end_m = re.search(r"Submission Instructions", text, re.IGNORECASE)
    start = start_m.start() if start_m else 0
    end = end_m.start() if end_m and end_m.start() > start else len(text)
    return text[start:end]


def _extract_questionnaire_parts(text: str) -> list[dict]:
    """Extract Part N / item N.M / topic / question table rows."""
    parts = []
    region = _questionnaire_region(text)

    # Find table-like question sections: "N.M   Topic   Question text"
    item_pattern = re.compile(
        r"(\d\.\d)\s{2,}([^\n]{3,50})\s{2,}(.+?)(?=\n\d\.\d|\nPart \d|\Z)",
        re.DOTALL,
    )
    seen = set()
    for m in item_pattern.finditer(region):
        item_num = m.group(1)
        if item_num in seen:
            continue
        seen.add(item_num)
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


def _extract_questionnaire_pdf_layout(path: Path) -> list[dict]:
    """Extract questionnaire rows from a PDF using word coordinates.

    Borderless questionnaire tables render each row y-aligned: the item
    number (e.g. "2.3"), its topic, and the first line of its question share
    the same top coordinate in fixed columns. Plain-text extraction scrambles
    those columns, so this walks words line-by-line instead:
      - a line whose first word matches N.M at the item-column anchor starts
        a new item;
      - lines starting left of the topic column (part labels, body text,
        page furniture) are skipped;
      - remaining words go to topic or question by their x position.
    Returns [] when pdfplumber is unavailable or no table is detected, so
    callers can fall back to the text-regex path.
    """
    try:
        import pdfplumber
    except ImportError:
        return []

    from collections import Counter

    item_re = re.compile(r"^\d{1,2}\.\d{1,2}$")
    stop_re = re.compile(r"^\d\s+Submission Instructions", re.IGNORECASE)

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
        if item_re.match(w["text"])
    ]
    if not anchors:
        return []
    anchor = Counter(anchors).most_common(1)[0][0]
    topic_min = anchor + 15
    question_min = anchor + 140

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
            # The item word may not lead the line: a part's first row starts
            # with the part label in the leftmost column ("Part 1: … 1.1 …").
            item_idx = next(
                (i for i, w in enumerate(line_words)
                 if item_re.match(w["text"]) and abs(w["x0"] - anchor) <= 6),
                None,
            )
            if item_idx is not None:
                if current:
                    items.append(current)
                current = {"item_number": line_words[item_idx]["text"], "_topic": [], "_question": []}
                line_words = line_words[item_idx + 1:]
            else:
                # Drop part-label-column words but keep the row's topic/question
                # words — continuation lines often share y with the part label.
                line_words = [w for w in line_words if w["x0"] >= topic_min]
            if current is None or not line_words:
                continue
            for w in line_words:
                if w["x0"] < topic_min:
                    continue
                bucket = "_topic" if w["x0"] < question_min else "_question"
                current[bucket].append(w["text"])
    if current:
        items.append(current)

    parts = []
    seen = set()
    for it in items:
        num = it["item_number"]
        if num in seen:
            continue
        seen.add(num)
        parts.append({
            "part": f"Part {num.split('.')[0]}",
            "item_number": num,
            "topic": " ".join(it["_topic"]).strip() or f"Section {num}",
            "question": " ".join(it["_question"]).strip()[:1000],
        })
    return parts


_DOC_SECTION_RE = re.compile(r"^\s*(\d)\s{1,4}([A-Z][^\n]{3,80}?)\s*$", re.MULTILINE)


def _extract_document_sections(text: str) -> list[dict]:
    """Extract the RFI's top-level numbered section headings
    (e.g. "1 Introduction & Disclaimer" … "6 Questions regarding the RFI").
    Only an ascending run starting at 1 is accepted, which filters out
    stray numbered lines in body text."""
    sections = []
    expected = 1
    for m in _DOC_SECTION_RE.finditer(text):
        num = int(m.group(1))
        if num != expected:
            continue
        title = m.group(2).strip()
        # Headings that wrap onto a second line end mid-phrase (e.g.
        # "Request Information (Company Profile /") — join the next line.
        if title.endswith(("/", "(", "&", "-")):
            tail = text[m.end():m.end() + 120].strip().splitlines()
            if tail:
                title = f"{title} {tail[0].strip()}"
        sections.append({"number": num, "title": title})
        expected += 1
    return sections


def _extract_submission_requirements(text: str) -> dict:
    """Extract page limits, format, file naming, and deadline."""
    req = {}

    page_match = re.search(
        r"limited to (?:a total of )?(\d+)[\s-]*page[s]?", text, re.IGNORECASE
    ) or _PAGE_LIMIT_RE.search(text)
    req["max_pages"] = int(page_match.group(1)) if page_match else None

    # Appendix pages
    appendix_match = re.search(r"(\d+)[\s-]*page[s]?\s*(?:technical\s+)?appendix", text, re.IGNORECASE)
    req["max_appendix_pages"] = int(appendix_match.group(1)) if appendix_match else None

    # Font size — require font context so e.g. "…00358\nPoint of Contact" can't match
    font_match = re.search(
        r"(?:minimum\s+)?(\d{1,2})[\s-]*point\s*(?:font|type|Times|Arial|Calibri)",
        text, re.IGNORECASE,
    )
    req["font_size_pt"] = int(font_match.group(1)) if font_match else 11

    # File naming convention
    file_match = re.search(r"[Ff]ile\s+naming[^:]*:\s*([^\n]+)", text)
    req["file_naming"] = file_match.group(1).strip() if file_match else ""

    # Submission portal
    portal_match = re.search(r"(?:Submit|via|through)\s+([A-Z]+\s+portal)", text, re.IGNORECASE)
    req["submission_portal"] = portal_match.group(1).strip() if portal_match else ""

    # Due date — allow an intervening time-of-day ("no later than 11:59 PM ET on 10 August 2026")
    due_match = re.search(
        r"(?:no later than|deadline)[^\n]{0,40}?(\d{1,2}\s+\w+\s+\d{4}|\w+ \d{1,2},\s*\d{4})",
        text, re.IGNORECASE,
    )
    req["due_date"] = due_match.group(1).strip() if due_match else _find_first(_DUE_DATE_RE, text)

    # Questions due date — tolerate portal/email text between "submitted" and the date
    q_match = re.search(
        r"[Qq]uestion[s]?\s+(?:must\s+be\s+)?submitted.{0,220}?by[^\n]{0,30}?"
        r"(\d{1,2}\s+\w+\s+\d{4}|\w+ \d{1,2},\s*\d{4})",
        text, re.DOTALL,
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
    document_sections = _extract_document_sections(text)
    # PDFs: coordinate-based table extraction is authoritative (plain-text
    # extraction scrambles the borderless questionnaire table's columns).
    questionnaire_parts = []
    if path.suffix.lower() == ".pdf":
        questionnaire_parts = _extract_questionnaire_pdf_layout(path)
    if len(questionnaire_parts) < 3:
        questionnaire_parts = _extract_questionnaire_parts(text) or questionnaire_parts
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
        "document_sections": document_sections,
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


# Alias — rfi_canvas_blueprint.py imports this name.
parse_rfi_document = parse_rfi


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
