#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Document upload and requirements extraction tool.

Uploads DoD documents (SOW, CDD, CONOPS, SRD, SRS, etc.) and images
(whiteboard photos, wireframe screenshots, architecture diagrams), extracts
text content using regex or vision LLM fallback, and identifies requirement
statements using shall/must/should/will patterns.

Supports PDF (with page-by-page vision fallback for scanned docs), DOCX,
TXT, MD, and image formats (PNG, JPG, GIF, WebP) with graceful fallback
for optional dependencies.

Usage:
    # Upload a document
    python tools/requirements/document_extractor.py --session-id sess-abc \\
        --upload --file-path /path/to/sow.pdf --document-type sow --json

    # Upload an image (whiteboard photo, wireframe, etc.)
    python tools/requirements/document_extractor.py --session-id sess-abc \\
        --upload --file-path /path/to/whiteboard.png --document-type other --json

    # Extract requirements from an uploaded document
    python tools/requirements/document_extractor.py --document-id doc-abc \\
        --extract --json

    # Classify an uploaded image document
    python tools/requirements/document_extractor.py --document-id doc-abc \\
        --classify --json

    # List all documents for a session
    python tools/requirements/document_extractor.py --session-id sess-abc \\
        --list --json
"""

import argparse
import base64
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

logger = logging.getLogger("icdev.requirements.document_extractor")

# Image extensions supported for direct upload
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tiff", ".bmp"}

try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def log_event(**kwargs): return -1


def _get_connection(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _generate_id(prefix="doc"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# File reading helpers
# ---------------------------------------------------------------------------

def _compute_file_hash(file_path):
    """Compute SHA-256 hash of file content."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Vision-based extraction helpers
# ---------------------------------------------------------------------------

def _check_vision_available():
    """Check if a vision-capable LLM is available for document extraction."""
    try:
        from tools.llm import get_router
        router = get_router()
        provider, model_id, model_cfg = router.get_provider_for_function("document_vision")
        if provider is None:
            return False, ""
        return model_cfg.get("supports_vision", False), model_id
    except Exception:
        return False, ""


def _extract_text_from_image_via_vision(file_path):
    """Extract text content from an image using a vision LLM.

    Sends the image to a vision-capable model with a prompt to extract
    all visible text, preserving structure and formatting.

    Args:
        file_path: Path to the image file.

    Returns:
        Extracted text string, or placeholder if vision unavailable.
    """
    p = Path(file_path)
    available, model_id = _check_vision_available()
    if not available:
        return f"[Image file: {p.name} -- vision model not available for text extraction]"

    try:
        from tools.testing.screenshot_validator import encode_image
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest

        b64_data, media_type = encode_image(str(p))

        user_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64_data,
                },
            },
            {
                "type": "text",
                "text": (
                    "Extract ALL text content from this document image. "
                    "Preserve structure, headings, bullet points, and numbered lists. "
                    "If this contains requirement statements (shall/must/should/will), "
                    "preserve them exactly. Output only the extracted text."
                ),
            },
        ]

        router = get_router()
        request = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=(
                "You are a document text extraction assistant for a DoD requirements "
                "intake system. Extract all visible text from the provided image accurately. "
                "Preserve document structure, headings, and formatting."
            ),
            max_tokens=4096,
            temperature=0.1,
        )

        response = router.invoke("document_vision", request)
        text = response.content.strip()
        if text:
            logger.info("Vision extracted %d chars from image %s", len(text), p.name)
            return text
        return f"[Image file: {p.name} -- vision model returned no text]"

    except Exception as exc:
        logger.warning("Vision text extraction failed for %s: %s", p.name, exc)
        return f"[Image file: {p.name} -- vision extraction error: {exc}]"


def _extract_pdf_pages_via_vision(file_path):
    """Extract text from PDF pages using vision LLM for pages with no text.

    Iterates pages via pypdf. For each page, attempts text extraction first.
    If a page yields no text (scanned/image-heavy), renders it and sends to
    the vision model for OCR-like extraction.

    Args:
        file_path: Path to the PDF file.

    Returns:
        Concatenated text from all pages with page markers.
    """
    p = Path(file_path)
    available, model_id = _check_vision_available()
    if not available:
        return f"[PDF file: {p.name} -- vision model not available for page extraction]"

    try:
        from pypdf import PdfReader
    except ImportError:
        return f"[PDF file: {p.name} -- requires pypdf for page-by-page extraction]"

    try:
        from tools.testing.screenshot_validator import encode_image
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest
    except ImportError as exc:
        return f"[PDF file: {p.name} -- vision dependencies not available: {exc}]"

    reader = PdfReader(str(p))
    all_pages = []
    vision_pages_used = 0

    for i, page in enumerate(reader.pages):
        page_num = i + 1
        text = page.extract_text() or ""

        if len(text.strip()) >= 50:
            # Sufficient text extracted directly
            all_pages.append(f"--- Page {page_num} ---\n{text.strip()}")
            continue

        # Page has no/little text — try vision extraction via page rendering
        # Attempt to render page to image using pdf2image or fitz
        page_image_b64 = None
        media_type = "image/png"

        try:
            # Try pdf2image (poppler-based)
            from pdf2image import convert_from_path
            images = convert_from_path(
                str(p), first_page=page_num, last_page=page_num, dpi=200,
            )
            if images:
                import io
                buf = io.BytesIO()
                images[0].save(buf, format="PNG")
                page_image_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("pdf2image failed for page %d: %s", page_num, exc)

        if page_image_b64 is None:
            try:
                # Try PyMuPDF (fitz)
                import fitz
                pdf_doc = fitz.open(str(p))
                pix = pdf_doc[i].get_pixmap(dpi=200)
                page_image_b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
                pdf_doc.close()
            except ImportError:
                pass
            except Exception as exc:
                logger.debug("PyMuPDF failed for page %d: %s", page_num, exc)

        if page_image_b64 is None:
            # No rendering library available — use whatever text we got
            if text.strip():
                all_pages.append(f"--- Page {page_num} ---\n{text.strip()}")
            else:
                all_pages.append(
                    f"--- Page {page_num} ---\n"
                    f"[No text extractable; pdf2image or PyMuPDF required for vision fallback]"
                )
            continue

        # Send rendered page image to vision model
        try:
            user_content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": page_image_b64,
                    },
                },
                {
                    "type": "text",
                    "text": (
                        f"Extract ALL text from this PDF page (page {page_num}). "
                        "Preserve structure, headings, tables, and bullet points. "
                        "Output only the extracted text."
                    ),
                },
            ]

            router = get_router()
            request = LLMRequest(
                messages=[{"role": "user", "content": user_content}],
                system_prompt=(
                    "You are a document OCR assistant. Extract all text from "
                    "the provided page image accurately."
                ),
                max_tokens=4096,
                temperature=0.1,
            )

            response = router.invoke("document_vision", request)
            page_text = response.content.strip()
            if page_text:
                all_pages.append(f"--- Page {page_num} (vision) ---\n{page_text}")
                vision_pages_used += 1
            else:
                all_pages.append(f"--- Page {page_num} ---\n[Vision returned no text]")
        except Exception as exc:
            logger.warning("Vision extraction failed for page %d: %s", page_num, exc)
            all_pages.append(
                f"--- Page {page_num} ---\n[Vision extraction error: {exc}]"
            )

    if vision_pages_used > 0:
        logger.info(
            "PDF %s: %d pages, %d required vision extraction",
            p.name, len(reader.pages), vision_pages_used,
        )

    if all_pages:
        return "\n\n".join(all_pages)
    return f"[PDF file: {p.name} -- no content extracted from any page]"


def _classify_image(file_path):
    """Classify an uploaded image using a vision LLM.

    Determines if the image is a whiteboard, diagram, wireframe, screenshot,
    form, table, flowchart, network diagram, architecture diagram, or other.

    Args:
        file_path: Path to the image file.

    Returns:
        dict with {category, confidence, description} or None if unavailable.
    """
    available, model_id = _check_vision_available()
    if not available:
        return None

    try:
        from tools.testing.screenshot_validator import encode_image
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest

        b64_data, media_type = encode_image(str(file_path))

        user_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64_data,
                },
            },
            {
                "type": "text",
                "text": (
                    "Classify this image into exactly ONE category from the list: "
                    "whiteboard, diagram, wireframe, screenshot, form, table, "
                    "flowchart, network_diagram, architecture_diagram, "
                    "handwritten_notes, other. "
                    "Respond with EXACTLY this JSON (no markdown, no extra text): "
                    '{"category": "string", "confidence": 0.0-1.0, '
                    '"description": "brief description of what the image shows"}'
                ),
            },
        ]

        router = get_router()
        request = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=(
                "You are an image classifier for a DoD document intake system. "
                "Classify images accurately into the requested categories."
            ),
            max_tokens=256,
            temperature=0.1,
        )

        response = router.invoke("document_vision", request)
        text = response.content.strip()

        # Strip markdown fences
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        result = json.loads(text)
        return {
            "category": str(result.get("category", "other")),
            "confidence": float(result.get("confidence", 0.0)),
            "description": str(result.get("description", "")),
        }

    except (json.JSONDecodeError, ValueError) as exc:
        logger.debug("Image classification JSON parse failed: %s", exc)
        return {"category": "other", "confidence": 0.3, "description": "Classification uncertain"}
    except Exception as exc:
        logger.warning("Image classification failed: %s", exc)
        return None


def classify_document(document_id, db_path=None):
    """Classify an uploaded image document using vision LLM.

    Args:
        document_id: ID of the uploaded document.
        db_path: Optional DB path override.

    Returns:
        dict with classification result.
    """
    conn = _get_connection(db_path)
    doc = conn.execute(
        "SELECT * FROM intake_documents WHERE id = ?", (document_id,)
    ).fetchone()
    if not doc:
        conn.close()
        raise ValueError(f"Document '{document_id}' not found.")

    doc_data = dict(doc)
    file_path = doc_data["file_path"]
    p = Path(file_path)

    if p.suffix.lower() not in IMAGE_EXTENSIONS:
        conn.close()
        return {
            "status": "skipped",
            "document_id": document_id,
            "reason": f"Not an image file: {p.suffix}",
        }

    classification = _classify_image(file_path)
    if classification is None:
        conn.close()
        return {
            "status": "skipped",
            "document_id": document_id,
            "reason": "Vision model not available for classification",
        }

    # Store classification in extracted_sections column
    conn.execute(
        "UPDATE intake_documents SET extracted_sections = ? WHERE id = ?",
        (json.dumps(classification), document_id),
    )
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "document_id": document_id,
        "classification": classification,
    }


# ---------------------------------------------------------------------------
# File reading helpers
# ---------------------------------------------------------------------------

def _read_file_content(file_path):
    """Read text content from a file.

    Supports .txt, .md (direct read), .pdf (pypdf with vision fallback),
    .docx (python-docx with fallback), image files (vision extraction),
    and other text files.
    """
    p = Path(file_path)
    suffix = p.suffix.lower()

    if suffix in (".txt", ".md"):
        return p.read_text(encoding="utf-8")

    elif suffix == ".pdf":
        # Try pypdf (PyPDF2 successor)
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(p))
            pages = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            if pages:
                return "\n\n".join(pages)
            # No text found — try vision-based page extraction
            logger.info("PDF %s has no extractable text, trying vision fallback", p.name)
            vision_result = _extract_pdf_pages_via_vision(str(p))
            if not vision_result.startswith("[PDF file:"):
                return vision_result
            return f"[PDF file: {p.name} -- no extractable text found]"
        except ImportError:
            return (
                f"[PDF file: {p.name} -- requires pypdf library. "
                f"Install with: pip install pypdf]"
            )
        except Exception as exc:
            return f"[PDF file: {p.name} -- extraction error: {exc}]"

    elif suffix == ".docx":
        # Try python-docx
        try:
            from docx import Document
            doc = Document(str(p))
            paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
            if paragraphs:
                return "\n\n".join(paragraphs)
            return f"[DOCX file: {p.name} -- no extractable text found]"
        except ImportError:
            return (
                f"[DOCX file: {p.name} -- requires python-docx library. "
                f"Install with: pip install python-docx]"
            )
        except Exception as exc:
            return f"[DOCX file: {p.name} -- extraction error: {exc}]"

    elif suffix in IMAGE_EXTENSIONS:
        # Image file — use vision LLM for text extraction
        return _extract_text_from_image_via_vision(str(p))

    else:
        # Attempt generic text read
        try:
            return p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"[Binary file: {p.name} -- unable to read as text]"


# ---------------------------------------------------------------------------
# Requirement extraction logic
# ---------------------------------------------------------------------------

# Requirement type detection keywords
_REQ_TYPE_KEYWORDS = {
    "security": [
        "authenticate", "authorize", "encrypt", "cac", "piv", "mfa",
        "fips", "stig", "access control", "audit log", "credential",
        "certificate", "pki", "rbac", "permission", "classification",
        "clearance", "password", "token", "session timeout",
    ],
    "performance": [
        "response time", "latency", "throughput", "concurrent",
        "availability", "uptime", "sla", "load", "capacity",
        "transactions per second", "bandwidth", "millisecond",
    ],
    "interface": [
        "integrate", "interface", "api", "rest", "soap", "feed",
        "import", "export", "connect", "external system", "third-party",
        "web service", "message queue", "protocol",
    ],
    "data": [
        "database", "data store", "retention", "backup", "archive",
        "migrate data", "data format", "schema", "cui data",
        "record", "table", "storage", "replication",
    ],
    "operational": [
        "deployment", "install", "configure", "maintain", "monitor",
        "support", "train", "operate", "documentation", "helpdesk",
        "disaster recovery", "failover", "continuity",
    ],
}


def _detect_requirement_type(text):
    """Determine requirement type from keywords in text."""
    lower = text.lower()
    for rtype, keywords in _REQ_TYPE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return rtype
    return "functional"


def _detect_priority(text):
    """Determine priority based on modal verbs.

    shall/must -> P1 (critical)
    should     -> P2 (high)
    will/may   -> P3 (medium)
    """
    lower = text.lower()
    if any(kw in lower for kw in ["shall", "must", "is required to"]):
        return "critical"
    elif "should" in lower:
        return "high"
    elif any(kw in lower for kw in ["will", "may", "can"]):
        return "medium"
    return "medium"


def _extract_requirement_sentences(text):
    """Extract sentences that contain requirement indicator keywords.

    Matches:
    - "shall" statements (mandatory)
    - "must" statements (mandatory)
    - "should" statements (desired)
    - "will" statements (intent)
    """
    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', text.replace("\n", " "))
    requirements = []

    requirement_patterns = [
        re.compile(r'\bshall\b', re.IGNORECASE),
        re.compile(r'\bmust\b', re.IGNORECASE),
        re.compile(r'\bshould\b', re.IGNORECASE),
        re.compile(r'\bwill\b', re.IGNORECASE),
    ]

    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 10:
            continue

        for pattern in requirement_patterns:
            if pattern.search(sentence):
                req_type = _detect_requirement_type(sentence)
                priority = _detect_priority(sentence)
                requirements.append({
                    "raw_text": sentence,
                    "requirement_type": req_type,
                    "priority": priority,
                })
                break  # Only match once per sentence

    return requirements


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def upload_document(session_id, file_path, document_type, db_path=None):
    """Upload a document for requirement extraction.

    Args:
        session_id: Intake session ID.
        file_path: Path to the document file.
        document_type: One of sow, cdd, conops, srd, srs, other.
        db_path: Optional DB path override.

    Returns:
        dict with document_id, session_id, file metadata, and status.
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    conn = _get_connection(db_path)

    # Verify session exists
    session = conn.execute(
        "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not session:
        conn.close()
        raise ValueError(f"Session '{session_id}' not found.")

    session_data = dict(session)

    # Compute file metadata
    doc_id = _generate_id("doc")
    file_hash = _compute_file_hash(file_path)
    file_size = p.stat().st_size
    file_name = p.name

    # MIME type mapping
    mime_map = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".tiff": "image/tiff",
        ".bmp": "image/bmp",
    }
    mime_type = mime_map.get(p.suffix.lower(), "application/octet-stream")

    # Read raw text for storage
    raw_text = _read_file_content(file_path)

    # Auto-classify images at upload time
    image_classification = None
    if p.suffix.lower() in IMAGE_EXTENSIONS:
        image_classification = _classify_image(file_path)

    # Map document_type for DB constraint compatibility
    # DB allows: sow, cdd, conops, srd, icd, ssp, use_case, brd, urd, rfp, rfi, other
    # Spec allows: sow, cdd, conops, srd, srs, other
    # Map srs -> other if not in DB constraint
    db_doc_type = document_type if document_type != "srs" else "other"

    extracted_sections = None
    if image_classification:
        extracted_sections = json.dumps(image_classification)

    conn.execute(
        """INSERT INTO intake_documents
           (id, session_id, document_type, file_name, file_path, file_hash,
            file_size_bytes, mime_type, extraction_status, extracted_sections,
            extracted_requirements_count, classification, uploaded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, 0, 'CUI', ?)""",
        (
            doc_id, session_id, db_doc_type, file_name, str(p.resolve()),
            file_hash, file_size, mime_type, extracted_sections,
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()

    if _HAS_AUDIT:
        log_event(
            event_type="document_uploaded",
            actor="icdev-requirements-analyst",
            action=f"Uploaded {document_type.upper()} document: {file_name}",
            project_id=session_data.get("project_id"),
            details={
                "session_id": session_id,
                "document_id": doc_id,
                "file_name": file_name,
                "file_hash": file_hash,
            },
        )

    result = {
        "document_id": doc_id,
        "session_id": session_id,
        "file_path": str(p.resolve()),
        "file_hash": file_hash,
        "file_size": file_size,
        "status": "uploaded",
    }
    if image_classification:
        result["image_classification"] = image_classification
    return result


def extract_requirements(document_id, db_path=None):
    """Extract requirement statements from an uploaded document.

    Reads the document text from intake_documents, identifies requirement
    sentences using shall/must/should/will patterns, classifies each by
    type and priority, and inserts into intake_requirements.

    Args:
        document_id: ID of the uploaded document.
        db_path: Optional DB path override.

    Returns:
        dict with document_id, count of requirements extracted, requirement
        list, and breakdowns by type and priority.
    """
    conn = _get_connection(db_path)

    # Load document
    doc = conn.execute(
        "SELECT * FROM intake_documents WHERE id = ?", (document_id,)
    ).fetchone()
    if not doc:
        conn.close()
        raise ValueError(f"Document '{document_id}' not found.")

    doc_data = dict(doc)
    session_id = doc_data["session_id"]
    file_path = doc_data["file_path"]

    # Update status to extracting
    conn.execute(
        "UPDATE intake_documents SET extraction_status = 'extracting' WHERE id = ?",
        (document_id,),
    )
    conn.commit()

    # Read file content
    raw_text = _read_file_content(file_path)

    # Extract requirement sentences
    extracted_stmts = _extract_requirement_sentences(raw_text)

    # Insert each requirement into intake_requirements
    inserted_reqs = []
    by_type = {}
    by_priority = {}

    for stmt in extracted_stmts:
        req_id = f"req-{uuid.uuid4().hex[:12]}"
        raw = stmt["raw_text"]
        req_type = stmt["requirement_type"]
        priority = stmt["priority"]

        conn.execute(
            """INSERT INTO intake_requirements
               (id, session_id, raw_text, refined_text, requirement_type,
                priority, source_document, clarity_score,
                gaps, acceptance_criteria, status, classification, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0.5, NULL, NULL, 'draft', 'CUI', ?)""",
            (
                req_id, session_id, raw, raw, req_type,
                priority, document_id, datetime.now().isoformat(),
            ),
        )

        inserted_reqs.append({
            "id": req_id,
            "raw_text": raw,
            "requirement_type": req_type,
            "priority": priority,
            "source": "document",
            "source_reference": document_id,
        })

        # Count by type
        by_type[req_type] = by_type.get(req_type, 0) + 1
        # Count by priority
        by_priority[priority] = by_priority.get(priority, 0) + 1

    # Update document: extraction complete
    conn.execute(
        """UPDATE intake_documents
           SET extraction_status = 'extracted', extracted_requirements_count = ?
           WHERE id = ?""",
        (len(inserted_reqs), document_id),
    )

    # Update session total requirement count
    total_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM intake_requirements WHERE session_id = ?",
        (session_id,),
    ).fetchone()["cnt"]
    conn.execute(
        "UPDATE intake_sessions SET total_requirements = ?, updated_at = ? WHERE id = ?",
        (total_count, datetime.now().isoformat(), session_id),
    )

    conn.commit()
    conn.close()

    if _HAS_AUDIT:
        log_event(
            event_type="document_extracted",
            actor="icdev-requirements-analyst",
            action=f"Extracted {len(inserted_reqs)} requirements from document {document_id}",
            details={
                "document_id": document_id,
                "session_id": session_id,
                "count": len(inserted_reqs),
                "by_type": by_type,
            },
        )

    return {
        "status": "ok",
        "document_id": document_id,
        "requirements_extracted": len(inserted_reqs),
        "requirements": inserted_reqs,
        "by_type": by_type,
        "by_priority": by_priority,
    }


def list_documents(session_id, db_path=None):
    """List all documents uploaded to a session.

    Args:
        session_id: Intake session ID.
        db_path: Optional DB path override.

    Returns:
        dict with session_id and list of document records.
    """
    conn = _get_connection(db_path)

    session = conn.execute(
        "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not session:
        conn.close()
        raise ValueError(f"Session '{session_id}' not found.")

    rows = conn.execute(
        "SELECT * FROM intake_documents WHERE session_id = ? ORDER BY uploaded_at",
        (session_id,),
    ).fetchall()
    documents = [dict(r) for r in rows]
    conn.close()

    return {
        "status": "ok",
        "session_id": session_id,
        "total_documents": len(documents),
        "documents": documents,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Document Requirements Extractor"
    )
    parser.add_argument("--session-id", help="Intake session ID")
    parser.add_argument("--document-id", help="Document ID (for extraction)")
    parser.add_argument(
        "--upload", action="store_true",
        help="Upload a document",
    )
    parser.add_argument("--file-path", help="Path to the document file")
    parser.add_argument(
        "--document-type",
        choices=["sow", "cdd", "conops", "srd", "srs", "other"],
        default="other",
        help="Type of the document",
    )
    parser.add_argument(
        "--extract", action="store_true",
        help="Extract requirements from an uploaded document",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List all documents for a session",
    )
    parser.add_argument(
        "--classify", action="store_true",
        help="Classify an uploaded image document using vision LLM",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        result = None

        if args.upload and args.session_id and args.file_path:
            # Upload document
            result = upload_document(
                args.session_id, args.file_path, args.document_type,
            )

        elif args.extract and args.document_id:
            # Extract requirements from document
            result = extract_requirements(args.document_id)

        elif args.classify and args.document_id:
            # Classify image document
            result = classify_document(args.document_id)

        elif args.list and args.session_id:
            # List documents
            result = list_documents(args.session_id)

        else:
            parser.print_help()
            return

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if args.upload:
                print(
                    f"Document uploaded: {result.get('document_id')} "
                    f"({result.get('file_size', 0)} bytes, "
                    f"hash: {result.get('file_hash', '?')[:12]}...)"
                )
            elif args.extract:
                print(
                    f"Extracted {result.get('requirements_extracted', 0)} "
                    f"requirements from document {result.get('document_id')}"
                )
                by_type = result.get("by_type", {})
                if by_type:
                    print("  By type: " + ", ".join(
                        f"{k}={v}" for k, v in sorted(by_type.items())
                    ))
                by_priority = result.get("by_priority", {})
                if by_priority:
                    print("  By priority: " + ", ".join(
                        f"{k}={v}" for k, v in sorted(by_priority.items())
                    ))
            elif args.classify:
                cls = result.get("classification", {})
                if cls:
                    print(f"Image classification for {result.get('document_id')}:")
                    print(f"  Category: {cls.get('category', '?')}")
                    print(f"  Confidence: {cls.get('confidence', 0):.2f}")
                    print(f"  Description: {cls.get('description', '')}")
                else:
                    print(f"Classification: {result.get('reason', result.get('status', '?'))}")
            elif args.list:
                docs = result.get("documents", [])
                print(f"Documents for session {args.session_id}: {len(docs)}")
                for doc in docs:
                    print(
                        f"  [{doc.get('document_type', '?').upper()}] "
                        f"{doc.get('file_name', '?')} "
                        f"({doc.get('extraction_status', '?')})"
                    )
            else:
                print(json.dumps(result, indent=2, default=str))

    except (ValueError, FileNotFoundError) as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"Error: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
