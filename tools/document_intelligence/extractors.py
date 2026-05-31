# CUI // SP-CTI
"""DIC built-in file extractors — air-gap safe fallbacks for multimodal ingestion.

Every extractor returns an :class:`Extraction` dataclass with normalized text, provider
name, content type, and page count. Extractors are best-effort: if a required library
is missing the extractor returns ``text=""`` and logs a warning so the orchestrator
can report the failure rather than silently producing 0 chunks.

Supported formats:
  PDF   → pypdf (pure-Python, air-gap baseline)
  DOCX  → python-docx (best-effort)
  XLSX  → openpyxl (best-effort)
  PPTX  → python-pptx (best-effort)
  PNG   → pytesseract / easyocr (best-effort)
  HTML  → built-in strip-html
  TXT   → built-in read-text
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


@dataclass
class Extraction:
    """Normalized output of a file extractor."""

    text: str
    provider: str
    content_type: str
    page_count: int = 1
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Extractor implementations
# --------------------------------------------------------------------------- #

def _strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = re.sub(r"&nbsp;", " ", raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _extract_text(path: Path) -> Extraction:
    raw = path.read_text(encoding="utf-8", errors="replace")
    return Extraction(
        text=raw,
        provider="builtin-text",
        content_type="text/plain",
        page_count=1,
        title=path.stem,
    )


def _extract_html(path: Path) -> Extraction:
    raw = path.read_text(encoding="utf-8", errors="replace")
    text = _strip_html(raw)
    return Extraction(
        text=text,
        provider="builtin-html",
        content_type="text/html",
        page_count=1,
        title=path.stem,
    )


def _extract_pdf(path: Path) -> Extraction:
    """Extract text from PDF using pypdf (pure-Python, air-gap safe)."""
    try:
        from pypdf import PdfReader
    except Exception as exc:
        logger.warning("dic.extractors: pypdf not available: %s", exc)
        return Extraction(
            text="",
            provider="pypdf-missing",
            content_type="application/pdf",
            page_count=0,
            title=path.stem,
            warnings=["pypdf not installed — run: pip install pypdf>=4.0"],
        )

    text_parts: list[str] = []
    try:
        reader = PdfReader(str(path))
        total_pages = len(reader.pages)
        for i, page in enumerate(reader.pages):
            try:
                page_text = page.extract_text() or ""
                if page_text.strip():
                    text_parts.append(f"\n--- Page {i + 1} ---\n{page_text.strip()}")
            except Exception as exc:
                logger.warning("dic.extractors: page %s extraction error: %s", i + 1, exc)

        full_text = "\n".join(text_parts)
        warnings: list[str] = []
        if not full_text.strip():
            warnings.append("PDF produced no extractable text — may be a scanned/image PDF. OCR not configured.")

        return Extraction(
            text=full_text,
            provider="pypdf",
            content_type="application/pdf",
            page_count=total_pages,
            title=path.stem,
            warnings=warnings,
        )
    except Exception as exc:
        logger.warning("dic.extractors: PDF read failed: %s", exc)
        return Extraction(
            text="",
            provider="pypdf-error",
            content_type="application/pdf",
            page_count=0,
            title=path.stem,
            warnings=[f"PDF read failed: {exc}"],
        )


def _extract_docx(path: Path) -> Extraction:
    """Extract text from DOCX using python-docx."""
    try:
        import docx
    except Exception:
        return Extraction(
            text="",
            provider="docx-missing",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            page_count=0,
            title=path.stem,
            warnings=["python-docx not installed — run: pip install python-docx"],
        )

    try:
        document = docx.Document(str(path))
        paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
        # Also extract text from tables
        table_texts: list[str] = []
        for table in document.tables:
            for row in table.rows:
                row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if row_text:
                    table_texts.append(row_text)

        all_text = "\n\n".join(paragraphs)
        if table_texts:
            all_text += "\n\n--- Tables ---\n" + "\n".join(table_texts)

        return Extraction(
            text=all_text,
            provider="python-docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            page_count=max(1, len(paragraphs) // 40),  # rough estimate
            title=path.stem,
            warnings=[""],
        )
    except Exception as exc:
        return Extraction(
            text="",
            provider="docx-error",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            page_count=0,
            title=path.stem,
            warnings=[f"DOCX read failed: {exc}"],
        )


def _extract_xlsx(path: Path) -> Extraction:
    """Extract text from XLSX using openpyxl."""
    try:
        import openpyxl
    except Exception:
        return Extraction(
            text="",
            provider="xlsx-missing",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            page_count=0,
            title=path.stem,
            warnings=["openpyxl not installed — run: pip install openpyxl"],
        )

    try:
        wb = openpyxl.load_workbook(str(path), data_only=True)
        sheet_texts: list[str] = []
        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            rows: list[str] = []
            for row in sheet.iter_rows(values_only=True):
                row_vals = [str(v) for v in row if v is not None]
                if row_vals:
                    rows.append(" | ".join(row_vals))
            if rows:
                sheet_texts.append(f"--- Sheet: {sheet_name} ---\n" + "\n".join(rows))

        return Extraction(
            text="\n\n".join(sheet_texts),
            provider="openpyxl",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            page_count=len(wb.sheetnames),
            title=path.stem,
            warnings=[""],
        )
    except Exception as exc:
        return Extraction(
            text="",
            provider="xlsx-error",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            page_count=0,
            title=path.stem,
            warnings=[f"XLSX read failed: {exc}"],
        )


def _extract_pptx(path: Path) -> Extraction:
    """Extract text from PPTX using python-pptx."""
    try:
        import pptx
    except Exception:
        return Extraction(
            text="",
            provider="pptx-missing",
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            page_count=0,
            title=path.stem,
            warnings=["python-pptx not installed — run: pip install python-pptx"],
        )

    try:
        prs = pptx.Presentation(str(path))
        slide_texts: list[str] = []
        for i, slide in enumerate(prs.slides, start=1):
            texts: list[str] = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    texts.append(shape.text.strip())
            if texts:
                slide_texts.append(f"--- Slide {i} ---\n" + "\n".join(texts))

        return Extraction(
            text="\n\n".join(slide_texts),
            provider="python-pptx",
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            page_count=len(prs.slides),
            title=path.stem,
            warnings=[""],
        )
    except Exception as exc:
        return Extraction(
            text="",
            provider="pptx-error",
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            page_count=0,
            title=path.stem,
            warnings=[f"PPTX read failed: {exc}"],
        )


def _extract_image(path: Path) -> Extraction:
    """Extract text from images using OCR (best-effort)."""
    warnings: list[str] = []
    try:
        from PIL import Image
    except Exception:
        return Extraction(
            text="",
            provider="pillow-missing",
            content_type="image/unknown",
            page_count=0,
            title=path.stem,
            warnings=["Pillow not installed — run: pip install Pillow"],
        )

    try:
        img = Image.open(str(path))
        # Try pytesseract first, then easyocr, then give up gracefully
        ocr_text = ""
        for ocr_name, ocr_fn in [
            ("pytesseract", _try_pytesseract),
            ("easyocr", _try_easyocr),
        ]:
            try:
                ocr_text = ocr_fn(img)
                if ocr_text.strip():
                    break
            except Exception:
                continue
        else:
            warnings.append("OCR not available — install pytesseract or easyocr for image text extraction")

        return Extraction(
            text=ocr_text,
            provider="ocr",
            content_type=f"image/{img.format.lower() if img.format else 'unknown'}",
            page_count=1,
            title=path.stem,
            warnings=warnings,
        )
    except Exception as exc:
        return Extraction(
            text="",
            provider="image-error",
            content_type="image/unknown",
            page_count=0,
            title=path.stem,
            warnings=[f"Image read failed: {exc}"],
        )


def _try_pytesseract(img) -> str:
    import pytesseract
    return pytesseract.image_to_string(img)


def _try_easyocr(img) -> str:
    import easyocr  # type: ignore
    import numpy as np
    reader = easyocr.Reader(["en"])
    result = reader.readtext(np.array(img))
    return "\n".join(r[1] for r in result)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

_EXTRACTORS: dict[str, callable] = {
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
    ".xlsx": _extract_xlsx,
    ".pptx": _extract_pptx,
    ".png": _extract_image,
    ".jpg": _extract_image,
    ".jpeg": _extract_image,
    ".tiff": _extract_image,
    ".tif": _extract_image,
    ".bmp": _extract_image,
    ".gif": _extract_image,
    ".html": _extract_html,
    ".htm": _extract_html,
    ".txt": _extract_text,
    ".md": _extract_text,
    ".markdown": _extract_text,
    ".rst": _extract_text,
    ".log": _extract_text,
    ".csv": _extract_text,
    ".tsv": _extract_text,
    ".json": _extract_text,
    ".yaml": _extract_text,
    ".yml": _extract_text,
    ".xml": _extract_text,
    ".py": _extract_text,
    ".sql": _extract_text,
    ".ini": _extract_text,
    ".cfg": _extract_text,
    ".toml": _extract_text,
}


def get_extractor(ext: str) -> callable | None:
    """Return the extractor function for a given file extension (lowercased)."""
    return _EXTRACTORS.get(ext.lower())


def extract_file(path: str | Path) -> Extraction:
    """Route a file path to the correct extractor.

    Falls back to a raw utf-8 decode with a warning if the extension is unknown.
    """
    p = Path(path)
    ext = p.suffix.lower()
    extractor = get_extractor(ext)
    if extractor is not None:
        return extractor(p)

    # Unknown extension — best-effort utf-8 decode
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
        return Extraction(
            text=raw,
            provider="builtin-fallback",
            content_type="application/octet-stream",
            page_count=1,
            title=p.stem,
            warnings=[f"Unrecognized extension '{ext}' — best-effort text decode. Install a provider for {ext} files."],
        )
    except Exception as exc:
        return Extraction(
            text="",
            provider="fallback-error",
            content_type="application/octet-stream",
            page_count=0,
            title=p.stem,
            warnings=[f"File read failed: {exc}"],
        )
