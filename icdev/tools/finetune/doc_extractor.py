#!/usr/bin/env python3
# CUI // SP-CTI
"""Document extractor — Word/PDF → training data pipeline (D-FT-11).

Extracts text from PDF and Word documents, chunks content, and prepares
it for training pair generation. Reuses RAG PDF pipeline (pdf_provider.py,
D-RAG-15) and RAG chunker (chunker.py, D-RAG-4).

Pipeline: upload → extract → chunk → store for pair generation

Usage:
    python tools/finetune/doc_extractor.py --extract --file /path/to/doc.pdf --dataset-id "ds-xxx" --json
    python tools/finetune/doc_extractor.py --extract --file /path/to/doc.docx --dataset-id "ds-xxx" --json
    python tools/finetune/doc_extractor.py --list --dataset-id "ds-xxx" --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.finetune.doc_extractor")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_hash(path: str) -> str:
    """SHA-256 hash of file content."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# -- PDF Extraction (reuses RAG pdf_provider.py, D-RAG-15) --------------------


def _extract_pdf(file_path: str) -> Dict[str, Any]:
    """Extract text from PDF via RAG pdf_provider chain."""
    try:
        from tools.rag.pdf_provider import extract_pdf

        result = extract_pdf(Path(file_path))
        if result.status == "success":
            pages_text = [p.text for p in result.pages if p.text.strip()]
            return {
                "success": True,
                "text": "\n\n".join(pages_text),
                "page_count": result.page_count,
                "provider": result.provider_used,
            }
        return {"success": False, "error": f"PDF extraction failed: {result.error}"}
    except ImportError:
        # Fallback to basic pypdf if RAG provider unavailable
        return _extract_pdf_basic(file_path)
    except Exception as e:
        return {"success": False, "error": f"PDF extraction error: {e}"}


def _extract_pdf_basic(file_path: str) -> Dict[str, Any]:
    """Basic PDF extraction via pypdf (air-gap fallback)."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text and text.strip():
                pages.append(text.strip())
        return {
            "success": True,
            "text": "\n\n".join(pages),
            "page_count": len(reader.pages),
            "provider": "pypdf",
        }
    except ImportError:
        return {"success": False, "error": "No PDF library available. Install pypdf."}
    except Exception as e:
        return {"success": False, "error": f"pypdf error: {e}"}


# -- Word Document Extraction --------------------------------------------------


def _extract_docx(file_path: str) -> Dict[str, Any]:
    """Extract text from Word document via python-docx."""
    try:
        from docx import Document

        doc = Document(file_path)
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        return {
            "success": True,
            "text": "\n\n".join(paragraphs),
            "paragraph_count": len(paragraphs),
            "provider": "python-docx",
        }
    except ImportError:
        return {"success": False, "error": "python-docx not installed. Install: pip install python-docx"}
    except Exception as e:
        return {"success": False, "error": f"docx extraction error: {e}"}


# -- Chunking (reuses RAG chunker.py, D-RAG-4) --------------------------------


def _chunk_text(
    text: str,
    source_id: str = "",
    source_type: str = "document",
    classification: str = "CUI",
) -> List[Dict[str, Any]]:
    """Chunk extracted text using RAG adaptive chunker."""
    try:
        from tools.rag.chunker import chunk_content

        chunks = chunk_content(
            content=text,
            source_type=source_type,
            source_id=source_id,
            classification=classification,
        )
        return [
            {
                "chunk_id": c.chunk_id,
                "content": c.content,
                "content_hash": c.content_hash,
                "chunk_index": c.chunk_index,
                "total_chunks": c.total_chunks,
            }
            for c in chunks
        ]
    except ImportError:
        # Fallback: simple paragraph-based chunking
        return _chunk_text_simple(text, source_id)


def _chunk_text_simple(
    text: str,
    source_id: str = "",
    max_chunk_size: int = 2000,
    overlap: int = 200,
) -> List[Dict[str, Any]]:
    """Simple fallback chunker when RAG chunker is unavailable."""
    chunks = []
    sentences = text.replace("\n\n", "\n").split("\n")

    current_chunk = ""
    chunk_idx = 0

    for sentence in sentences:
        if len(current_chunk) + len(sentence) > max_chunk_size and current_chunk:
            content_hash = hashlib.sha256(current_chunk.encode()).hexdigest()[:16]
            chunks.append(
                {
                    "chunk_id": f"ftchunk-{source_id}-{chunk_idx}",
                    "content": current_chunk.strip(),
                    "content_hash": content_hash,
                    "chunk_index": chunk_idx,
                    "total_chunks": 0,  # Updated after loop
                }
            )
            # Keep overlap
            overlap_text = current_chunk[-overlap:] if len(current_chunk) > overlap else ""
            current_chunk = overlap_text + sentence + "\n"
            chunk_idx += 1
        else:
            current_chunk += sentence + "\n"

    # Final chunk
    if current_chunk.strip():
        content_hash = hashlib.sha256(current_chunk.encode()).hexdigest()[:16]
        chunks.append(
            {
                "chunk_id": f"ftchunk-{source_id}-{chunk_idx}",
                "content": current_chunk.strip(),
                "content_hash": content_hash,
                "chunk_index": chunk_idx,
                "total_chunks": 0,
            }
        )

    # Update total_chunks
    for c in chunks:
        c["total_chunks"] = len(chunks)

    return chunks


# -- Main Pipeline -------------------------------------------------------------


def extract_document(
    file_path: str,
    dataset_id: str = "",
    classification: str = "CUI",
    tenant_id: str = "",
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Extract and chunk a document for fine-tuning pair generation.

    Returns chunks ready for pair_generator.py to process.
    """
    path = Path(file_path)
    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}

    suffix = path.suffix.lower()

    # Extract text based on file type
    if suffix == ".pdf":
        extraction = _extract_pdf(file_path)
    elif suffix in (".docx", ".doc"):
        extraction = _extract_docx(file_path)
    elif suffix in (".txt", ".md", ".rst"):
        try:
            text = path.read_text(encoding="utf-8")
            extraction = {"success": True, "text": text, "provider": "plaintext"}
        except Exception as e:
            return {"success": False, "error": f"Text read error: {e}"}
    else:
        return {"success": False, "error": f"Unsupported file type: {suffix}. Supported: .pdf, .docx, .txt, .md"}

    if not extraction.get("success"):
        return extraction

    text = extraction["text"]
    if not text.strip():
        return {"success": False, "error": "Document contains no extractable text"}

    # Generate document ID
    doc_id = f"ftdoc-{uuid.uuid4().hex[:12]}"

    # Chunk the text
    chunks = _chunk_text(
        text=text,
        source_id=doc_id,
        source_type="document_extraction",
        classification=classification,
    )

    if not chunks:
        return {"success": False, "error": "Document produced no chunks after splitting"}

    # Store extraction record in DB if dataset_id provided
    if dataset_id and db_path is not None or dataset_id:
        conn = _get_db(db_path)
        try:
            # Record in a lightweight way (reuse ingestion_log pattern)
            _file_hash(file_path)
            conn.execute(
                """INSERT OR IGNORE INTO rag_ingestion_log
                   (source_table, source_id, chunk_count, status, started_at, completed_at)
                   VALUES (%s, %s, %s, 'completed', %s, %s)""",
                (f"ft_document:{dataset_id}", doc_id, len(chunks), _now(), _now()),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            # Ingestion log is optional
            logger.warning("extract_document: best-effort INSERT into rag_ingestion_log failed (non-blocking): %s", exc)
        finally:
            conn.close()

    return {
        "success": True,
        "document_id": doc_id,
        "file_name": path.name,
        "file_type": suffix,
        "file_hash": _file_hash(file_path) if path.exists() else "",
        "text_length": len(text),
        "chunk_count": len(chunks),
        "chunks": chunks,
        "provider": extraction.get("provider", ""),
        "dataset_id": dataset_id,
    }


def extract_directory(
    dir_path: str,
    dataset_id: str = "",
    classification: str = "CUI",
    tenant_id: str = "",
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Extract all supported documents from a directory."""
    supported = {".pdf", ".docx", ".doc", ".txt", ".md", ".rst"}
    path = Path(dir_path)

    if not path.is_dir():
        return {"success": False, "error": f"Directory not found: {dir_path}"}

    results = []
    total_chunks = 0
    errors = []

    for f in sorted(path.iterdir()):
        if f.suffix.lower() in supported and f.is_file():
            result = extract_document(
                file_path=str(f),
                dataset_id=dataset_id,
                classification=classification,
                tenant_id=tenant_id,
                project_id=project_id,
                db_path=db_path,
            )
            if result.get("success"):
                total_chunks += result["chunk_count"]
                results.append(
                    {
                        "file": f.name,
                        "document_id": result["document_id"],
                        "chunks": result["chunk_count"],
                    }
                )
            else:
                errors.append({"file": f.name, "error": result.get("error", "")})

    return {
        "success": True,
        "documents_processed": len(results),
        "total_chunks": total_chunks,
        "documents": results,
        "errors": errors,
    }


# -- CLI -----------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Fine-tuning document extractor")
    parser.add_argument("--extract", action="store_true", help="Extract document")
    parser.add_argument("--extract-dir", action="store_true", help="Extract all docs from directory")
    parser.add_argument("--file", type=str, default="")
    parser.add_argument("--dir", type=str, default="")
    parser.add_argument("--dataset-id", type=str, default="")
    parser.add_argument("--classification", type=str, default="CUI")
    parser.add_argument("--tenant-id", type=str, default="")
    parser.add_argument("--project-id", type=str, default="")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.extract and args.file:
        result = extract_document(
            file_path=args.file,
            dataset_id=args.dataset_id,
            classification=args.classification,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
        )
    elif args.extract_dir and args.dir:
        result = extract_directory(
            dir_path=args.dir,
            dataset_id=args.dataset_id,
            classification=args.classification,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
        )
    else:
        result = {"success": False, "error": "Specify --extract --file or --extract-dir --dir"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
