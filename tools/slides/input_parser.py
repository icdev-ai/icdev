# CUI // SP-CTI
"""Input parser — text/PDF/DOCX ingestion for user-uploaded content.

Converts uploaded files into plain text for the slide generation pipeline.
Uses pypdf and python-docx (already in requirements.txt).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def parse_text(content: str) -> dict[str, Any]:
    """Wrap plain text as a slide source."""
    return {
        "source": "upload",
        "content": content.strip(),
        "summary": content.strip()[:500],
    }


def parse_file(file_path: str | Path) -> dict[str, Any]:
    """Parse a text, PDF, or DOCX file into a source dict."""
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix in (".txt", ".md"):
        content = path.read_text(encoding="utf-8", errors="ignore").strip()
        return parse_text(content)

    if suffix == ".pdf":
        return _parse_pdf(path)

    if suffix in (".docx", ".doc"):
        return _parse_docx(path)

    # Fallback: try reading as text
    try:
        content = path.read_text(encoding="utf-8", errors="ignore").strip()
        return parse_text(content)
    except Exception as e:
        return {
            "source": "upload",
            "content": "",
            "summary": f"Could not parse {path.name}: {e}",
            "error": str(e),
        }


def _parse_pdf(path: Path) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text.strip())
        content = "\n\n".join(pages)
        return {
            "source": "upload",
            "filename": path.name,
            "page_count": len(reader.pages),
            "content": content,
            "summary": content[:500],
        }
    except ImportError:
        return parse_text("PDF parsing requires pypdf. Install with: pip install pypdf")
    except Exception as e:
        return {"source": "upload", "content": "", "summary": str(e), "error": str(e)}


def _parse_docx(path: Path) -> dict[str, Any]:
    try:
        from docx import Document
        doc = Document(str(path))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        content = "\n".join(paragraphs)
        return {
            "source": "upload",
            "filename": path.name,
            "content": content,
            "summary": content[:500],
        }
    except ImportError:
        return parse_text("DOCX parsing requires python-docx. Install with: pip install python-docx")
    except Exception as e:
        return {"source": "upload", "content": "", "summary": str(e), "error": str(e)}
