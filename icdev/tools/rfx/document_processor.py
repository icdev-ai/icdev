#!/usr/bin/env python3
# CUI // SP-PROPIN
"""Document processor: upload, extract text, chunk, store in SQLite.

Supports PDF (via pypdf), DOCX (via python-docx), and plain text.
Files are stored in data/rfx_uploads/. Text is chunked at ~500 words
with 50-word overlap for RAG retrieval.

No external services required — all processing is local Python.
"""

import hashlib
import json
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
UPLOAD_DIR = BASE_DIR / "data" / "rfx_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

CHUNK_WORDS = 500
CHUNK_OVERLAP = 50


# ── text extraction ────────────────────────────────────────────────────────────

def _extract_pdf(path: Path) -> tuple[str, int]:
    """Extract text from PDF. Returns (text, page_count)."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages:
            t = page.extract_text() or ""
            pages.append(t)
        return "\n\n".join(pages), len(pages)
    except Exception as e:
        return f"[PDF extraction error: {e}]", 0


def _extract_docx(path: Path) -> tuple[str, int]:
    """Extract text from DOCX. Returns (text, estimated_pages)."""
    try:
        from docx import Document
        doc = Document(str(path))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n\n".join(paragraphs)
        estimated_pages = max(1, len(text) // 3000)
        return text, estimated_pages
    except Exception as e:
        return f"[DOCX extraction error: {e}]", 0


def extract_text(file_path: Path, mime_type: str = "") -> tuple[str, int]:
    """Extract raw text from a document. Returns (text, page_count)."""
    suffix = file_path.suffix.lower()
    if suffix == ".pdf" or "pdf" in mime_type:
        return _extract_pdf(file_path)
    if suffix in (".docx", ".doc") or "word" in mime_type:
        return _extract_docx(file_path)
    # plain text fallback
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
        estimated_pages = max(1, len(text) // 3000)
        return text, estimated_pages
    except Exception as e:
        return f"[Text extraction error: {e}]", 0


# ── chunking ───────────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_words: int = CHUNK_WORDS,
               overlap_words: int = CHUNK_OVERLAP) -> list[dict]:
    """Split text into overlapping word-boundary chunks.

    Returns list of {"chunk_index": int, "content": str, "word_count": int}.
    """
    words = text.split()
    if not words:
        return []

    chunks = []
    idx = 0
    chunk_num = 0

    while idx < len(words):
        end = min(idx + chunk_words, len(words))
        chunk_words_list = words[idx:end]
        content = " ".join(chunk_words_list)
        chunks.append({
            "chunk_index": chunk_num,
            "content": content,
            "word_count": len(chunk_words_list),
        })
        chunk_num += 1
        if end >= len(words):
            break
        idx += chunk_words - overlap_words

    return chunks


# ── hashing ────────────────────────────────────────────────────────────────────

def file_hash(path: Path) -> str:
    """SHA-256 hash of file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


# ── public API ─────────────────────────────────────────────────────────────────

def store_upload(
    src_path: Path,
    doc_type: str = "other",
    proposal_id: Optional[str] = None,
    opportunity_id: Optional[str] = None,
    uploaded_by: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """Process an uploaded document and store it in rfx_documents.

    Copies the file to UPLOAD_DIR, extracts text, chunks, and writes
    document + chunk metadata to the DB. Embedding is deferred to
    rag_service.vectorize_document().

    Returns the new rfx_documents row as a dict.
    """
    now = datetime.now(timezone.utc).isoformat()
    doc_id = str(uuid.uuid4())

    # Copy file to upload dir
    dest_filename = f"{doc_id}_{src_path.name}"
    dest_path = UPLOAD_DIR / dest_filename
    dest_path.write_bytes(src_path.read_bytes())

    fhash = file_hash(dest_path)
    size = dest_path.stat().st_size
    mime = _guess_mime(dest_path)

    # Check for duplicate
    conn = _conn()
    try:
        existing = conn.execute(
            "SELECT id FROM rfx_documents WHERE file_hash = ?", (fhash,)
        ).fetchone()
        if existing:
            return {"status": "duplicate", "document_id": existing["id"],
                    "message": "Document already uploaded (same SHA-256 hash)."}

        # Extract text
        text, page_count = extract_text(dest_path, mime)

        # Chunk text
        chunks = chunk_text(text)

        # Insert document record
        conn.execute("""
            INSERT INTO rfx_documents
                (id, proposal_id, opportunity_id, filename, doc_type,
                 file_path, file_hash, mime_type, file_size_bytes,
                 content, page_count, chunk_count,
                 uploaded_by, notes, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            doc_id, proposal_id, opportunity_id,
            src_path.name, doc_type,
            str(dest_path.relative_to(BASE_DIR)),
            fhash, mime, size,
            text, page_count, len(chunks),
            uploaded_by, notes, now, now,
        ))

        # Insert chunks (no embedding yet)
        for c in chunks:
            conn.execute("""
                INSERT INTO rfx_document_chunks
                    (id, document_id, chunk_index, content, word_count,
                     metadata, created_at)
                VALUES (?,?,?,?,?,?,?)
            """, (
                str(uuid.uuid4()), doc_id,
                c["chunk_index"], c["content"], c["word_count"],
                json.dumps({"page_approx": c["chunk_index"] // 3 + 1}),
                now,
            ))

        conn.commit()
    finally:
        conn.close()

    return {
        "status": "ok",
        "document_id": doc_id,
        "filename": src_path.name,
        "file_hash": fhash,
        "page_count": page_count,
        "chunk_count": len(chunks),
        "file_size_bytes": size,
        "vectorized": False,
    }


def list_documents(proposal_id: Optional[str] = None,
                   doc_type: Optional[str] = None) -> list[dict]:
    """List rfx_documents, optionally filtered."""
    conn = _conn()
    try:
        sql = "SELECT * FROM rfx_documents WHERE 1=1"
        params: list = []
        if proposal_id:
            sql += " AND proposal_id = ?"
            params.append(proposal_id)
        if doc_type:
            sql += " AND doc_type = ?"
            params.append(doc_type)
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_document(doc_id: str) -> Optional[dict]:
    """Retrieve a single rfx_document by id."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM rfx_documents WHERE id = ?", (doc_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_document(doc_id: str) -> bool:
    """Delete a document and its chunks. Also removes the file on disk."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT file_path FROM rfx_documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if not row:
            return False
        # ON DELETE CASCADE handles rfx_document_chunks
        conn.execute("DELETE FROM rfx_documents WHERE id = ?", (doc_id,))
        conn.commit()
        # Remove file
        fp = BASE_DIR / row["file_path"]
        if fp.exists():
            fp.unlink()
        return True
    finally:
        conn.close()


# ── helpers ────────────────────────────────────────────────────────────────────

def _guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }.get(ext, "application/octet-stream")
