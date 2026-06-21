#!/usr/bin/env python3
# CUI // SP-CTI
"""MarkItDown adapter for DIC — multi-format to structured Markdown conversion.

Wraps Microsoft's markitdown library (pip install markitdown) as a DIC extractor.
Gracefully degrades to built-in extractors when markitdown is not installed.

Supports: DOCX, PPTX, XLSX, PDF, HTML, images (with LLM descriptions), audio,
          YouTube URLs, and any format markitdown handles.

Usage (programmatic):
    from tools.document_intelligence.converters.markitdown_adapter import convert
    result = convert(Path("report.docx"))
    print(result.text)  # Structured Markdown

Usage (format detection):
    from tools.document_intelligence.converters.markitdown_adapter import is_available, SUPPORTED_EXTENSIONS
    if is_available():
        print(SUPPORTED_EXTENSIONS)
"""

from __future__ import annotations

import logging
from tools.logging.icdev_logger import get_logger
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# Extensions where MarkItDown produces higher-fidelity output than built-ins
# (structured Markdown with headers/tables preserved vs. plain text extraction)
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({
    ".docx", ".pptx", ".xlsx",
    ".pdf",
    ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
    ".mp3", ".wav", ".m4a",
    ".zip",
    ".xls", ".csv",
    ".msg", ".eml",
    ".epub",
})

# Extensions where built-in extractors are preferred (more air-gap safe)
_PREFER_BUILTIN: frozenset[str] = frozenset({".txt", ".md", ".py", ".json", ".yaml", ".yml"})

_markitdown_available: bool | None = None


def is_available() -> bool:
    """Return True if markitdown is importable."""
    global _markitdown_available
    if _markitdown_available is None:
        try:
            import markitdown  # noqa: F401
            _markitdown_available = True
        except ImportError:
            _markitdown_available = False
            logger.debug(
                "markitdown not installed — DIC enhanced extraction unavailable. "
                "Install: pip install markitdown"
            )
    return _markitdown_available


def convert(path: Path, llm_client=None) -> "Extraction":
    """Convert a file to Markdown via MarkItDown.

    Args:
        path: Path to the file to convert.
        llm_client: Optional LLM client for image descriptions (markitdown feature).
                    If None, image alt-text is used as fallback.

    Returns:
        Extraction with structured Markdown text, or empty Extraction on failure.
        Falls back gracefully when markitdown is not installed.
    """
    from tools.document_intelligence.extractors import Extraction  # local import avoids circular

    if not is_available():
        return Extraction(
            text="",
            provider="markitdown-unavailable",
            content_type="application/octet-stream",
            page_count=0,
            title=path.stem,
            warnings=["markitdown not installed — install via: pip install markitdown"],
        )

    p = Path(path)
    if not p.exists():
        return Extraction(
            text="",
            provider="markitdown",
            content_type="application/octet-stream",
            page_count=0,
            title=p.stem,
            warnings=[f"File not found: {path}"],
        )

    try:
        from markitdown import MarkItDown

        md = MarkItDown(llm_client=llm_client)
        result = md.convert(str(p))
        text = result.text_content or ""

        ext = p.suffix.lower()
        content_type = _ext_to_mime(ext)

        return Extraction(
            text=text,
            provider="markitdown",
            content_type=content_type,
            page_count=max(1, text.count("\n\n") // 10 + 1),  # rough page estimate
            title=result.title or p.stem,
            metadata={"markitdown_provider": "markitdown", "char_count": len(text)},
            warnings=[],
        )

    except Exception as exc:
        logger.warning("markitdown_adapter: conversion failed for %s: %s", path, exc)
        return Extraction(
            text="",
            provider="markitdown-error",
            content_type="application/octet-stream",
            page_count=0,
            title=p.stem,
            warnings=[f"markitdown conversion failed: {exc}"],
        )


def should_use_markitdown(ext: str) -> bool:
    """Return True if MarkItDown should be preferred over built-in for this extension."""
    return (
        is_available()
        and ext.lower() in SUPPORTED_EXTENSIONS
        and ext.lower() not in _PREFER_BUILTIN
    )


def _ext_to_mime(ext: str) -> str:
    _MAP = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pdf": "application/pdf",
        ".html": "text/html",
        ".htm": "text/html",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".csv": "text/csv",
        ".xls": "application/vnd.ms-excel",
        ".msg": "application/vnd.ms-outlook",
        ".epub": "application/epub+zip",
    }
    return _MAP.get(ext.lower(), "application/octet-stream")
