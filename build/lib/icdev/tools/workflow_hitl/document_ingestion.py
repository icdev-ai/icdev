# CUI // SP-CTI
"""Document ingestion pipeline for HITL workflow templates, forms, style guides, and SOPs.

Accepts PDF, DOCX, HTML, and Markdown files. Extracts text with section structure,
chunks via the RAG chunker, embeds, and stores in rag_chunks with source_type='wf_doc'.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tools.db.storage import get_connection

logger = get_logger(__name__)

_STORAGE_DIR = Path("data/wf_ingested_files")


# ── Public API ────────────────────────────────────────────────────────────────

def ingest_file(
    doc_template_id: str,
    file_path: str | Path,
    filename: str | None = None,
    ingested_by: str | None = None,
) -> str:
    """Ingest a document file and store chunks in rag_chunks.

    Returns the wf_ingested_files.id on success.
    Raises ValueError for unsupported file types.
    Raises FileNotFoundError if file_path does not exist.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    fname = filename or path.name
    file_type = _detect_type(path, fname)
    content_hash = _sha256(path)
    file_size = path.stat().st_size

    # Dedup check
    existing = _find_existing(doc_template_id, content_hash)
    if existing and existing["ingestion_status"] == "completed":
        logger.info("ingest_file: dedup hit for %s (hash %s) → %s", fname, content_hash[:8], existing["id"])
        return existing["id"]

    ingest_id = f"wfi-file-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    # Copy file to storage
    storage_path = _store_file(path, ingest_id, file_type)

    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO wf_ingested_files
               (id, doc_template_id, filename, file_type, file_size_bytes,
                content_hash, storage_path, ingestion_status, ingested_by,
                ingested_at, updated_at)
               VALUES (%s,?,?,?,?,?,?,'processing',?,?,%s)""",
            (ingest_id, doc_template_id, fname, file_type, file_size,
             content_hash, str(storage_path), ingested_by, now, now),
        )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()

    try:
        pages = _extract_text(path, file_type)
        chunks_stored = _store_chunks(doc_template_id, ingest_id, fname, pages)
        _update_status(ingest_id, "completed",
                       page_count=len(pages),
                       section_count=sum(1 for p in pages if p.get("is_section_heading")),
                       chunk_count=chunks_stored)
        logger.info("ingest_file: %s → %d chunks stored (id=%s)", fname, chunks_stored, ingest_id)
    except Exception as exc:
        _update_status(ingest_id, "failed", error_message=str(exc))
        logger.exception("ingest_file: extraction failed for %s", fname)
        raise

    return ingest_id


def get_ingested_files(doc_template_id: str) -> list[dict]:
    """List all ingested files for a document template."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM wf_ingested_files WHERE doc_template_id=%s ORDER BY ingested_at DESC",
            (doc_template_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_template_chunk_ids(doc_template_ids: list[str]) -> list[str]:
    """Return rag_chunks.id values for all chunks from the given template IDs."""
    if not doc_template_ids:
        return []
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(doc_template_ids))
        rows = conn.execute(
            f"SELECT id FROM wf_ingested_files WHERE doc_template_id IN ({placeholders})",
            doc_template_ids,
        ).fetchall()
        ingest_ids = [r[0] for r in rows]
        if not ingest_ids:
            return []
        placeholders2 = ",".join("?" * len(ingest_ids))
        chunk_rows = conn.execute(
            f"SELECT id FROM rag_chunks WHERE source_type='wf_doc' AND source_id IN ({placeholders2})",
            ingest_ids,
        ).fetchall()
        return [r[0] for r in chunk_rows]
    finally:
        conn.close()


def delete_ingested_file(ingest_id: str) -> bool:
    """Delete an ingested file record and its rag_chunks."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM wf_ingested_files WHERE id=%s", (ingest_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM rag_chunks WHERE source_type='wf_doc' AND source_id=%s", (ingest_id,))
        conn.execute("DELETE FROM wf_ingested_files WHERE id=%s", (ingest_id,))
        try:
            conn.commit()
        except Exception:
            pass
        if row["storage_path"]:
            sp = Path(row["storage_path"])
            if sp.exists():
                sp.unlink(missing_ok=True)
        return True
    finally:
        conn.close()


# ── Text extraction ───────────────────────────────────────────────────────────

def _detect_type(path: Path, filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    mapping = {"pdf": "pdf", "docx": "docx", "doc": "docx",
                "html": "html", "htm": "html", "md": "md",
                "markdown": "md", "txt": "txt"}
    return mapping.get(ext, "txt")


def _extract_text(path: Path, file_type: str) -> list[dict]:
    """Extract text with page/section structure. Returns list of page dicts:
    {text, page, section_heading, level, is_section_heading}
    """
    if file_type == "pdf":
        return _extract_pdf(path)
    if file_type == "docx":
        return _extract_docx(path)
    if file_type == "html":
        return _extract_html(path)
    if file_type in ("md", "txt"):
        return _extract_text_file(path)
    return _extract_text_file(path)


def _extract_pdf(path: Path) -> list[dict]:
    try:
        from tools.rag.pdf_provider import extract_pdf
        result = extract_pdf(path)
        pages = []
        for pg in result.pages:
            text = pg.text if hasattr(pg, "text") else str(pg)
            pages.append({
                "text": text,
                "page": getattr(pg, "page_number", len(pages) + 1),
                "section_heading": _detect_heading_from_text(text),
                "level": 1,
                "is_section_heading": False,
            })
        return pages
    except ImportError:
        # Fallback: read as binary and attempt naive extraction
        logger.warning("pdf_provider unavailable — reading PDF as text")
        return _extract_text_file(path)


def _extract_docx(path: Path) -> list[dict]:
    import docx as python_docx
    doc = python_docx.Document(str(path))
    pages: list[dict] = []
    current_section = ""
    current_level = 0
    buffer: list[str] = []
    page_num = 1

    for para in doc.paragraphs:
        style = para.style.name if para.style else ""
        is_heading = style.startswith("Heading")
        level = int(style[-1]) if is_heading and style[-1].isdigit() else 0

        if is_heading and para.text.strip():
            if buffer:
                pages.append({
                    "text": "\n".join(buffer),
                    "page": page_num,
                    "section_heading": current_section,
                    "level": current_level,
                    "is_section_heading": False,
                })
                buffer = []
            pages.append({
                "text": para.text.strip(),
                "page": page_num,
                "section_heading": para.text.strip(),
                "level": level,
                "is_section_heading": True,
            })
            current_section = para.text.strip()
            current_level = level
        else:
            if para.text.strip():
                buffer.append(para.text.strip())
            # Rough page break detection
            for run in para.runs:
                if "\x0c" in run.text:
                    page_num += 1

    if buffer:
        pages.append({
            "text": "\n".join(buffer),
            "page": page_num,
            "section_heading": current_section,
            "level": current_level,
            "is_section_heading": False,
        })
    return pages


def _extract_html(path: Path) -> list[dict]:
    from bs4 import BeautifulSoup
    content = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(content, "html.parser")
    pages: list[dict] = []
    current_section = ""
    current_level = 0
    buffer: list[str] = []

    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td"]):
        if tag.name.startswith("h"):
            if buffer:
                pages.append({
                    "text": "\n".join(buffer),
                    "page": 1,
                    "section_heading": current_section,
                    "level": current_level,
                    "is_section_heading": False,
                })
                buffer = []
            heading_text = tag.get_text(strip=True)
            level = int(tag.name[1])
            pages.append({
                "text": heading_text,
                "page": 1,
                "section_heading": heading_text,
                "level": level,
                "is_section_heading": True,
            })
            current_section = heading_text
            current_level = level
        else:
            text = tag.get_text(separator=" ", strip=True)
            if text:
                buffer.append(text)

    if buffer:
        pages.append({
            "text": "\n".join(buffer),
            "page": 1,
            "section_heading": current_section,
            "level": current_level,
            "is_section_heading": False,
        })
    return pages


def _extract_text_file(path: Path) -> list[dict]:
    """Extract Markdown or plain text with heading detection."""
    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    pages: list[dict] = []
    current_section = ""
    current_level = 0
    buffer: list[str] = []

    heading_re = re.compile(r"^(#{1,6})\s+(.+)")

    for line in lines:
        m = heading_re.match(line)
        if m:
            if buffer:
                pages.append({
                    "text": "\n".join(buffer),
                    "page": 1,
                    "section_heading": current_section,
                    "level": current_level,
                    "is_section_heading": False,
                })
                buffer = []
            level = len(m.group(1))
            heading = m.group(2).strip()
            pages.append({
                "text": heading,
                "page": 1,
                "section_heading": heading,
                "level": level,
                "is_section_heading": True,
            })
            current_section = heading
            current_level = level
        else:
            if line.strip():
                buffer.append(line)

    if buffer:
        pages.append({
            "text": "\n".join(buffer),
            "page": 1,
            "section_heading": current_section,
            "level": current_level,
            "is_section_heading": False,
        })
    return pages


def _detect_heading_from_text(text: str) -> str:
    """Heuristic: first non-empty line of a PDF page may be a section heading."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and len(stripped) < 120:
            return stripped
    return ""


# ── Chunk storage ─────────────────────────────────────────────────────────────

def _store_chunks(
    doc_template_id: str,
    ingest_id: str,
    filename: str,
    pages: list[dict],
) -> int:
    """Chunk page dicts and insert into rag_chunks. Returns chunk count."""
    try:
        from tools.rag.chunker import chunk_content
        # Build a single text blob with section markers for the chunker
        full_text = "\n\n".join(
            p["text"] for p in pages if p["text"].strip()
        )
        chunks = chunk_content(
            content=full_text,
            source_type="wf_doc",
            source_id=ingest_id,
            source_table="wf_ingested_files",
        )
    except Exception:
        # Fallback: simple paragraph splitting
        chunks = _naive_chunk(pages, ingest_id)

    now = datetime.now(timezone.utc).isoformat()
    section_map = _build_section_map(pages)

    conn = get_connection()
    try:
        stored = 0
        for i, chunk in enumerate(chunks):
            chunk_id = getattr(chunk, "chunk_id", None) or f"{ingest_id}-c{i:04d}"
            content = getattr(chunk, "content", str(chunk))
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            # Find matching section for this chunk's position
            section_heading = _find_section(section_map, content)
            metadata = json.dumps({
                "wf_document_template_id": doc_template_id,
                "ingest_id": ingest_id,
                "filename": filename,
                "section_heading": section_heading,
                "chunk_index": i,
            })
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO rag_chunks
                       (id, content, content_hash, source_type, source_id, source_table,
                        chunk_index, total_chunks, metadata, tier, classification,
                        created_at, updated_at)
                       VALUES (%s,?,?,'wf_doc',?,?,?,?,?,'warm','CUI',?,%s)""",
                    (chunk_id, content, content_hash, ingest_id, "wf_ingested_files",
                     i, len(chunks), metadata, now, now),
                )
                stored += 1
            except Exception as exc:
                logger.warning("chunk insert failed (idx=%d): %s", i, exc)
        try:
            conn.commit()
        except Exception:
            pass
        return stored
    finally:
        conn.close()


def _naive_chunk(pages: list[dict], ingest_id: str, max_chars: int = 1200) -> list[dict]:
    """Fallback: split on paragraphs up to max_chars."""
    chunks = []
    buffer = []
    buf_len = 0
    for page in pages:
        if page["is_section_heading"]:
            if buffer:
                chunks.append({"content": "\n".join(buffer), "chunk_id": f"{ingest_id}-nc{len(chunks):04d}"})
                buffer = []
                buf_len = 0
        text = page["text"]
        if buf_len + len(text) > max_chars and buffer:
            chunks.append({"content": "\n".join(buffer), "chunk_id": f"{ingest_id}-nc{len(chunks):04d}"})
            buffer = []
            buf_len = 0
        buffer.append(text)
        buf_len += len(text)
    if buffer:
        chunks.append({"content": "\n".join(buffer), "chunk_id": f"{ingest_id}-nc{len(chunks):04d}"})
    return chunks


def _build_section_map(pages: list[dict]) -> list[tuple[str, str]]:
    """Build (excerpt, section_heading) pairs for fuzzy matching."""
    return [(p["text"][:60], p["section_heading"]) for p in pages if p["section_heading"]]


def _find_section(section_map: list[tuple[str, str]], content: str) -> str:
    for excerpt, heading in section_map:
        if excerpt.strip() and excerpt.strip()[:40] in content:
            return heading
    return section_map[-1][1] if section_map else ""


# ── Storage helpers ───────────────────────────────────────────────────────────

def _store_file(source: Path, ingest_id: str, file_type: str) -> Path:
    _STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    dest = _STORAGE_DIR / f"{ingest_id}.{file_type}"
    import shutil
    shutil.copy2(source, dest)
    return dest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _find_existing(doc_template_id: str, content_hash: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM wf_ingested_files WHERE doc_template_id=%s AND content_hash=%s LIMIT 1",
            (doc_template_id, content_hash),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _update_status(
    ingest_id: str,
    status: str,
    page_count: int = 0,
    section_count: int = 0,
    chunk_count: int = 0,
    error_message: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE wf_ingested_files SET ingestion_status=%s, page_count=%s, section_count=%s,
               chunk_count=%s, error_message=%s, updated_at=%s WHERE id=%s""",
            (status, page_count, section_count, chunk_count, error_message, now, ingest_id),
        )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()
