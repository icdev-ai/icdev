#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Document upload and requirements extraction tool.

Uploads DoD documents (SOW, CDD, CONOPS, SRD, SRS, etc.), extracts text content,
and identifies requirement statements using shall/must/should/will patterns.
Supports PDF, DOCX, TXT, and MD with graceful fallback for optional dependencies.

Usage:
    # Upload a document
    python tools/requirements/document_extractor.py --session-id sess-abc \\
        --upload --file-path /path/to/sow.pdf --document-type sow --json

    # Extract requirements from an uploaded document
    python tools/requirements/document_extractor.py --document-id doc-abc \\
        --extract --json

    # List all documents for a session
    python tools/requirements/document_extractor.py --session-id sess-abc \\
        --list --json
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

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


def _read_file_content(file_path):
    """Read text content from a file.

    Supports .txt, .md (direct read), .pdf (pypdf with fallback),
    .docx (python-docx with fallback), and other text files.
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
    }
    mime_type = mime_map.get(p.suffix.lower(), "application/octet-stream")

    # Read raw text for storage
    raw_text = _read_file_content(file_path)

    # Map document_type for DB constraint compatibility
    # DB allows: sow, cdd, conops, srd, icd, ssp, use_case, brd, urd, rfp, rfi, other
    # Spec allows: sow, cdd, conops, srd, srs, other
    # Map srs -> other if not in DB constraint
    db_doc_type = document_type if document_type != "srs" else "other"

    conn.execute(
        """INSERT INTO intake_documents
           (id, session_id, document_type, file_name, file_path, file_hash,
            file_size_bytes, mime_type, extraction_status, extracted_sections,
            extracted_requirements_count, classification, uploaded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, 0, 'CUI', ?)""",
        (
            doc_id, session_id, db_doc_type, file_name, str(p.resolve()),
            file_hash, file_size, mime_type, datetime.now().isoformat(),
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

    return {
        "document_id": doc_id,
        "session_id": session_id,
        "file_path": str(p.resolve()),
        "file_hash": file_hash,
        "file_size": file_size,
        "status": "uploaded",
    }


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
