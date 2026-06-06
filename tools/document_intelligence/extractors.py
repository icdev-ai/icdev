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

import io
import base64
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


# ── Cached easyocr reader (avoid re-loading model per page) ─────────────────
_EASYOCR_READER = None
_EASYOCR_TRIED = False


def _get_easyocr_reader():
    global _EASYOCR_READER, _EASYOCR_TRIED
    if _EASYOCR_TRIED:
        return _EASYOCR_READER
    _EASYOCR_TRIED = True
    try:
        import easyocr

        _EASYOCR_READER = easyocr.Reader(["en"], gpu=False)
    except Exception as exc:
        logger.debug("dic.extractors: easyocr not available: %s", exc)
        _EASYOCR_READER = None
    return _EASYOCR_READER


def _try_easyocr(img) -> str:
    reader = _get_easyocr_reader()
    if reader is None:
        return ""
    try:
        import numpy as np

        result = reader.readtext(np.array(img))
        return "\n".join(r[1] for r in result)
    except Exception:
        return ""


def _vision_ocr(img) -> str:
    """Send a PIL image to a local vision LLM for text extraction.

    Probes endpoints in priority order (env-configurable):
      1. Ollama      — OLLAMA_BASE_URL      → /api/tags + /api/chat
      2. vLLM        — VLLM_BASE_URL        → /v1/models + /v1/chat/completions
      3. LM Studio   — LM_STUDIO_BASE_URL   → /v1/models + /v1/chat/completions
      4. llama.cpp   — LLAMA_CPP_BASE_URL   → /v1/models + /v1/chat/completions

    Auto-detects API style per endpoint. Falls back to next endpoint if the
    current one has no vision-capable model or returns an error.
    """
    import json
    import os
    import urllib.request

    # Convert image to base64 PNG
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    image_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    prompt_text = (
        "Extract ALL text from this document page image. "
        "Preserve structure, headings, and formatting. "
        "Return only the extracted text, no commentary."
    )

    # Vision-capable model keywords (covers llava, gemma3/4, bakllava, moondream, glm-ocr, qwen-vl, etc.)
    vision_kw = (
        "llava", "gemma3", "gemma4", "gemma-3", "gemma-4",
        "bakllava", "moondream", "glm-ocr", "qwen-vl", "qwen2-vl",
        "phi4", "phi-4", "minicpm-v", "internvl", "deepseek-vl",
    )

    # Candidate endpoints from environment (same precedence as pdf_fallback.py)
    candidates: list[tuple[str, str]] = []
    for env_var, default in [
        ("OLLAMA_BASE_URL", "http://localhost:11434"),
        ("VLLM_BASE_URL", ""),
        ("LM_STUDIO_BASE_URL", ""),
        ("LLAMA_CPP_BASE_URL", ""),
    ]:
        url = os.environ.get(env_var, default)
        if url:
            candidates.append((url, env_var))

    def _detect_api(base: str) -> str:
        """Return 'ollama' or 'openai' based on probe endpoints."""
        try:
            urllib.request.urlopen(f"{base}/api/tags", timeout=2)
            return "ollama"
        except Exception:
            pass
        try:
            urllib.request.urlopen(f"{base}/v1/models", timeout=2)
            return "openai"
        except Exception:
            pass
        return "ollama"  # default guess

    for base_url, src in candidates:
        base = base_url.rstrip("/")
        api = _detect_api(base)

        try:
            if api == "ollama":
                resp = urllib.request.urlopen(f"{base}/api/tags", timeout=3)
                data = json.loads(resp.read())
                models = [m["name"] for m in data.get("models", [])]
                vision_models = [m for m in models if any(v in m.lower() for v in vision_kw)]
                if not vision_models:
                    continue
                model = vision_models[0]

                payload = json.dumps(
                    {
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": prompt_text,
                                "images": [image_b64],
                            }
                        ],
                        "stream": False,
                        "options": {"num_predict": 4096},
                    }
                ).encode()
                url = f"{base}/api/chat"
            else:
                # OpenAI-compatible (vLLM, LM Studio, llama.cpp)
                req = urllib.request.Request(
                    f"{base}/v1/models",
                    headers={"Content-Type": "application/json"},
                )
                resp = urllib.request.urlopen(req, timeout=3)
                data = json.loads(resp.read())
                models = [m.get("id", "") for m in data.get("data", [])]
                vision_models = [m for m in models if any(v in m.lower() for v in vision_kw)]
                if not vision_models:
                    continue
                model = vision_models[0]

                payload = json.dumps(
                    {
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt_text},
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:image/png;base64,{image_b64}",
                                        },
                                    },
                                ],
                            }
                        ],
                        "max_tokens": 4096,
                        "stream": False,
                    }
                ).encode()
                url = f"{base}/v1/chat/completions"

            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req, timeout=120)
            data = json.loads(resp.read())

            if api == "ollama":
                text = data.get("message", {}).get("content", "").strip()
            else:
                choices = data.get("choices", [])
                text = choices[0].get("message", {}).get("content", "").strip() if choices else ""

            if text:
                logger.info("dic.extractors: vision OCR via %s (%s) produced %d chars", src, model, len(text))
                return text
        except Exception as exc:
            logger.debug("dic.extractors: vision OCR failed on %s (%s): %s", src, base, exc)
            continue

    return ""


def _ocr_image(img) -> str:
    """Run OCR on a PIL image. Tries easyocr → local vision LLM (Ollama/vLLM) → pytesseract."""
    # 1. easyocr (pure Python, PyTorch-based, air-gap safe if models cached)
    text = _try_easyocr(img)
    if text.strip():
        return text

    # 2. Local vision LLM (Ollama / vLLM / LM Studio / llama.cpp)
    try:
        text = _vision_ocr(img)
        if text.strip():
            return text
    except Exception:
        pass

    # 3. pytesseract (requires external Tesseract binary — not air-gap friendly)
    try:
        import pytesseract

        return pytesseract.image_to_string(img)
    except Exception:
        pass

    return ""


def _try_pdf_ocr(path: Path, total_pages: int, max_pages: int = 50) -> str:
    """Render PDF pages to images via pypdfium2 and OCR them. Returns aggregated text."""
    try:
        import pypdfium2 as pdfium
    except Exception:
        return ""

    pages_to_process = min(total_pages, max_pages)
    text_parts: list[str] = []

    try:
        pdf = pdfium.PdfDocument(str(path))
        for i in range(pages_to_process):
            try:
                bitmap = pdf[i].render(scale=2)
                pil_image = bitmap.to_pil()
                page_text = _ocr_image(pil_image)
                if page_text.strip():
                    text_parts.append(f"\n--- Page {i + 1} ---\n{page_text.strip()}")
            except Exception as exc:
                logger.warning("dic.extractors: OCR failed on page %s: %s", i + 1, exc)
    except Exception as exc:
        logger.warning("dic.extractors: PDF OCR rendering failed: %s", exc)

    return "\n".join(text_parts)


def _extract_pdf(path: Path) -> Extraction:
    """Extract text from PDF using pypdf (fast text path) + pypdfium2→OCR (scanned fallback)."""
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

        # Phase 2: OCR fallback for scanned/image PDFs.
        if not full_text.strip():
            ocr_text = _try_pdf_ocr(path, total_pages)
            if ocr_text.strip():
                return Extraction(
                    text=ocr_text,
                    provider="pypdf+ocr",
                    content_type="application/pdf",
                    page_count=total_pages,
                    title=path.stem,
                    warnings=["PDF text extracted via OCR (scanned/image PDF)."],
                )
            warnings.append(
                "PDF produced no extractable text — may be a scanned/image PDF. "
                "OCR unavailable: install easyocr, start Ollama/vLLM with a vision model (llava/gemma4), or install pytesseract."
            )

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


def _try_easyocr(img) -> str:
    reader = _get_easyocr_reader()
    if reader is None:
        return ""
    try:
        import numpy as np

        result = reader.readtext(np.array(img))
        return "\n".join(r[1] for r in result)
    except Exception:
        return ""


def _try_pytesseract(img) -> str:
    import pytesseract

    return pytesseract.image_to_string(img)


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


# --------------------------------------------------------------------------- #
# Extraction-quality anomaly detection (aiify-opp-6059: hardcoded_threshold ->
# anomaly_detection, modeled on the document-record serialisation/validation
# layer of a document-management backend). A serialiser-style validator keeps a
# single *hardcoded threshold* — accept any record whose text length is above N,
# reject the rest — which is blind to two silent-failure modes the absolute
# number can't see: (1) a record that came back structurally valid (a real
# page_count, no library error) yet yielded near-empty text — a scanned PDF
# whose OCR produced nothing, a DOCX whose body never decoded; and (2) a yield
# *cliff* where most of an ingested batch extracted richly and a few records
# fell off a statistical edge into near-empty noise. This layer lifts the
# implicit length cutoff into named per-page text-yield bands AND adds a
# yield-distribution outlier pass over the batch. The statistical detection is
# ALWAYS authoritative and LLM-free (air-gap safe); the optional LLM severity
# grade is best-effort enrichment that degrades silently to None. It NEVER
# changes which records were extracted — it only annotates ingestion quality.
# --------------------------------------------------------------------------- #

# Per-page text-yield bands (characters of extracted text per page). These
# replace the single magic "min length" cutoff a serialiser would hardcode:
# chars_per_page >= _YIELD_RICH → "rich"; >= _YIELD_SPARSE → "sparse"; below →
# "empty" (extraction effectively failed). Centralizing them means tuning the
# notion of "too little text" happens in one place, not scattered len() checks.
_YIELD_RICH = 200.0
_YIELD_SPARSE = 40.0

# ── Anomaly detection (batch-relative outliers) ───────────────────────────────
# The absolute bands grade each record against a fixed yardstick. They miss a
# record that is anomalous *for this batch*: the near-empty tail past a sharp
# yield cliff. The anomaly layer flags records whose per-page yield sits more
# than _ANOMALY_STDEV_K standard deviations BELOW the batch mean (low outliers)
# and that are not themselves already rich (abs ceiling). Pure statistics, no I/O.
_ANOMALY_STDEV_K = 1.5            # outlier if yield < mean - k * stdev
_ANOMALY_MIN_DOCS = 4            # need a real distribution before flagging outliers
_ANOMALY_ABS_CEIL = _YIELD_RICH  # never flag a record that is itself rich
_ANOMALY_SAMPLE = 5             # bound how many concrete outliers the LLM sees

# Deterministic severity cutoffs on the sparse/empty fraction of the batch.
_ANOMALY_SEV_HIGH_FRACTION = 0.50
_ANOMALY_SEV_MEDIUM_FRACTION = 0.25

_EXTRACTION_ANOMALY_SYSTEM_PROMPT = (
    "You are an ingestion-quality analyst grading how badly a batch of extracted "
    "document records came out. You are given the number of records, their "
    "per-page text-yield distribution, whether any record came back structurally "
    "valid yet near-empty (empty_extraction — a likely silent OCR/parse failure), "
    "the records that are low-yield statistical outliers (the near-empty tail past "
    "a yield cliff), and a deterministic baseline severity. A batch where records "
    "extracted to (almost) no text, or where most records are sparse, is the most "
    "severe. You may agree with or adjust the baseline, but justify any change. "
    'Respond ONLY with a JSON object: {"severity": "low|medium|high", "rationale": '
    '"<=160 chars", "top_concern": "<short phrase naming the main quality problem>"}. '
    "Never invent records beyond those provided."
)


def _extraction_fields(item: "Extraction | dict") -> dict:
    """Read the quality-relevant fields off an Extraction or its dict form.

    Pure accessor that tolerates both :class:`Extraction` instances and plain
    dicts (already-serialised records), so the detector works on freshly
    extracted objects and on records reloaded from storage alike. ``page_count``
    is floored at 1 to keep the per-page divisor safe even for a failed read
    that reported 0 pages.
    """
    if isinstance(item, dict):
        text = item.get("text") or ""
        provider = item.get("provider") or ""
        content_type = item.get("content_type") or ""
        page_count = item.get("page_count") or 0
        title = item.get("title") or ""
    else:
        text = getattr(item, "text", "") or ""
        provider = getattr(item, "provider", "") or ""
        content_type = getattr(item, "content_type", "") or ""
        page_count = getattr(item, "page_count", 0) or 0
        title = getattr(item, "title", "") or ""
    try:
        pages = max(int(page_count), 1)
    except (TypeError, ValueError):
        pages = 1
    chars = len(text)
    return {
        "title": title,
        "provider": provider,
        "content_type": content_type,
        "page_count": pages,
        "chars": chars,
        "chars_per_page": chars / pages,
    }


def _classify_yield(chars_per_page: float) -> str:
    """Map a record's per-page text yield to a band using the named thresholds.

    Pure function: ``chars_per_page >= _YIELD_RICH`` → ``"rich"``,
    ``>= _YIELD_SPARSE`` → ``"sparse"``, otherwise ``"empty"``.
    """
    if chars_per_page >= _YIELD_RICH:
        return "rich"
    if chars_per_page >= _YIELD_SPARSE:
        return "sparse"
    return "empty"


def _heuristic_extraction_anomaly_severity(sparse_count: int, total: int, has_empty: bool) -> str:
    """Deterministic extraction-anomaly severity — the always-available baseline.

    Pure function of how large a fraction of the batch is sparse/empty noise,
    escalated one notch when ANY record came back structurally valid yet
    near-empty (``has_empty``), since a silent total extraction failure is the
    worst case. Used directly when the LLM grade is unavailable and handed to the
    model as the reference baseline.
    """
    if total <= 0:
        return "low"
    fraction = (sparse_count / total) if total else 0.0
    if has_empty or fraction >= _ANOMALY_SEV_HIGH_FRACTION:
        return "high"
    if fraction >= _ANOMALY_SEV_MEDIUM_FRACTION:
        return "medium"
    return "low"


def _compute_extraction_anomalies(extractions: "list[Extraction | dict]") -> dict:
    """Pure statistical outlier pass over a batch of records — the authoritative heuristic.

    A *low outlier* is a record whose per-page text yield is below ``mean -
    _ANOMALY_STDEV_K * stdev`` AND not itself rich (``chars_per_page <
    _ANOMALY_ABS_CEIL``) — the near-empty tail past a yield cliff. ``has_empty``
    is True when any record fell into the ``"empty"`` band (extraction
    effectively produced no text). Below ``_ANOMALY_MIN_DOCS`` the distribution
    is too small to be meaningful, so only the band classification is evaluated
    and no statistical outliers are reported. No I/O, no LLM — safe to call
    anywhere (air-gap).

    Returns the count summary, the ranked outlier list, the ``has_empty`` flag,
    and the deterministic ``severity`` baseline.
    """
    recs = [_extraction_fields(e) for e in extractions]
    yields = [r["chars_per_page"] for r in recs]
    total = len(yields)

    sparse_count = sum(1 for r in recs if r["chars_per_page"] < _YIELD_RICH)
    has_empty = any(r["chars_per_page"] < _YIELD_SPARSE for r in recs)

    if total < _ANOMALY_MIN_DOCS:
        return {
            "anomaly_count": 0,
            "mean": round(sum(yields) / total, 2) if total else 0.0,
            "stdev": 0.0,
            "threshold": 0.0,
            "has_empty": has_empty,
            "anomalies": [],
            "sparse_count": sparse_count,
            "severity": _heuristic_extraction_anomaly_severity(sparse_count, total, has_empty),
            "total": total,
        }

    mean = sum(yields) / total
    variance = sum((y - mean) ** 2 for y in yields) / total
    stdev = variance ** 0.5
    threshold = mean - _ANOMALY_STDEV_K * stdev

    anomalies: list[dict] = []
    for r in recs:
        cpp = r["chars_per_page"]
        if cpp < threshold and cpp < _ANOMALY_ABS_CEIL:
            z = round((cpp - mean) / stdev, 3) if stdev > 0 else 0.0
            anomalies.append({
                "title": r["title"],
                "provider": r["provider"],
                "content_type": r["content_type"],
                "page_count": r["page_count"],
                "chars": r["chars"],
                "chars_per_page": round(cpp, 2),
                "z_score": z,
                "band": _classify_yield(cpp),
            })
    anomalies.sort(key=lambda a: a["chars_per_page"])

    return {
        "anomaly_count": len(anomalies),
        "mean": round(mean, 2),
        "stdev": round(stdev, 2),
        "threshold": round(threshold, 2),
        "has_empty": has_empty,
        "anomalies": anomalies,
        "sparse_count": sparse_count,
        "severity": _heuristic_extraction_anomaly_severity(sparse_count, total, has_empty),
        "total": total,
    }


def detect_extraction_anomalies(extractions: "list[Extraction | dict]", use_llm: bool = True) -> dict:
    """Flag a batch of extracted records for silent extraction failure / a yield cliff.

    Complements the absolute rich/sparse/empty bands: a batch can contain
    individually acceptable records yet expose a sharp yield cliff (a near-empty
    tail of low outliers), or fail outright (a record that came back valid but
    produced no text).

    The statistical detection (:func:`_compute_extraction_anomalies`) and the
    deterministic ``severity`` baseline are ALWAYS authoritative and never depend
    on the LLM. The returned ``ai_grade`` is best-effort severity enrichment and
    is ``None`` when the model is unavailable, ``use_llm`` is False, or there is
    nothing to grade.

    Args:
        extractions: The :class:`Extraction` records (or their dict form) from a
            batch ingest.
        use_llm: When False, skip the optional LLM grade entirely (air-gap / cost).

    Returns:
        {
          "anomaly_count": int,
          "mean": float, "stdev": float, "threshold": float,
          "has_empty": bool,
          "sparse_count": int,
          "anomalies": [{"title","provider","content_type","page_count","chars",
                         "chars_per_page","z_score","band"}],
          "severity": "low|medium|high",   # authoritative deterministic baseline
          "ai_grade": {...} | None,        # optional LLM refinement
        }
    """
    out = _compute_extraction_anomalies(extractions)
    ai_grade = None
    if use_llm:
        ai_grade = _ai_extraction_anomaly_severity(
            {"record_count": out["total"], "mean": out["mean"], "stdev": out["stdev"],
             "has_empty": out["has_empty"], "sparse_count": out["sparse_count"],
             "anomaly_count": out["anomaly_count"], "baseline_severity": out["severity"]},
            out["anomalies"],
        )
    out.pop("total", None)
    out["ai_grade"] = ai_grade
    return out


def _ai_extraction_anomaly_severity(summary: dict, anomalies: list[dict]) -> dict | None:
    """Grade extraction-quality-anomaly severity with the LLM, grounded on real data.

    Args:
        summary: Distribution facts — record_count, mean, stdev, has_empty,
            sparse_count, anomaly_count, and the deterministic
            ``baseline_severity``.
        anomalies: The concrete low-yield outlier records (titles, providers,
            content types, per-page yields). Only the leading ``_ANOMALY_SAMPLE``
            are sent, keeping the call cheap and bounded regardless of batch size.
            Injection scanning stays ON (``skip_injection_scan`` is NOT set)
            because record titles are derived from arbitrary ingested files.

    Returns:
        ``{"severity": "low|medium|high", "rationale": str, "top_concern": str}``
        on success, or ``None`` when there is nothing to grade (no outliers and no
        empty extraction), the model is unavailable, or the output is
        missing/blank/malformed/out-of-range. Callers MUST treat ``None`` as "use
        the deterministic heuristic" — this is best-effort enrichment, never a
        hard dependency.
    """
    if not anomalies and not summary.get("has_empty"):
        return None
    try:
        import json as _json

        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        lines = [
            f"Batch facts: {_json.dumps(summary, sort_keys=True)}",
            f"Deterministic baseline severity: {summary.get('baseline_severity')}",
            "Low-yield outlier records:",
        ]
        for a in anomalies[:_ANOMALY_SAMPLE]:
            lines.append(f"- {_json.dumps(a, default=str)}")

        req = LLMRequest(
            messages=[
                {"role": "user", "content": "\n".join(lines) + "\n\nGrade the severity."}
            ],
            system_prompt=_EXTRACTION_ANOMALY_SYSTEM_PROMPT,
            max_tokens=200,
            temperature=0.1,
            classification="CUI",
        )
        resp = LLMRouter().invoke("dic_extraction_anomaly_severity", req)
        if not resp or not resp.content:
            return None
        raw = resp.content.strip()
        # Tolerate fenced code blocks around the JSON object.
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        parsed = _json.loads(raw[start : end + 1])
        severity = str(parsed.get("severity") or "").strip().lower()
        if severity not in {"low", "medium", "high"}:
            return None
        return {
            "severity": severity,
            "rationale": str(parsed.get("rationale") or "").strip()[:200],
            "top_concern": str(parsed.get("top_concern") or "").strip()[:80],
        }
    except Exception:
        return None


def extract_batch_with_quality(
    paths: "list[str | Path]", use_llm: bool = True
) -> "tuple[list[Extraction], dict]":
    """Extract a batch of files and annotate it with an extraction-quality report.

    Identical extraction to calling :func:`extract_file` on each path — the
    returned record list is exactly what those calls would produce; the second
    element is the deterministic extraction-quality report from
    :func:`detect_extraction_anomalies` (silent-failure / yield-cliff flags plus
    an optional best-effort LLM severity grade). Quality assessment never changes
    what was extracted.
    """
    extractions = [extract_file(p) for p in paths]
    report = detect_extraction_anomalies(extractions, use_llm=use_llm)
    return extractions, report


# --------------------------------------------------------------------------- #
# OCR confidence anomaly detection (aiify-opp-6105: hardcoded_threshold ->
# anomaly_detection, modeled on the Tesseract OCR parser of a document-management
# backend). A tesseract-style parser keeps a single *hardcoded confidence cutoff*
# — accept any recognised word whose engine confidence is above N, drop the rest
# — which is blind to two silent OCR-quality failure modes the absolute number
# can't see: (1) a page that OCR'd into mostly low-confidence "garbled" tokens
# (a skewed/blurry scan, the wrong language model, a low-DPI fax) yet still
# returned *some* above-cutoff words, so the page reads as "fine"; and (2) a
# confidence *cliff* where most of a page recognised cleanly and a pocket of
# words collapsed into near-noise that the absolute cutoff alone never surfaces
# relative to the rest of the page. This layer lifts the implicit confidence
# cutoff into named per-word confidence bands AND adds a confidence-distribution
# outlier pass over the page's words. The statistical detection is ALWAYS
# authoritative and LLM-free (air-gap safe); the optional LLM severity grade is
# best-effort enrichment that degrades silently to None. It NEVER changes which
# words tesseract recognised — it only annotates OCR confidence quality.
# --------------------------------------------------------------------------- #

# Per-word confidence bands (Tesseract reports word confidence on a 0–100 scale).
# These replace the single magic "min confidence" cutoff a parser would hardcode:
# conf >= _OCR_CONF_HIGH → "confident"; >= _OCR_CONF_LOW → "uncertain"; below →
# "garbled" (the recognition is effectively noise). Centralizing them means
# tuning the notion of "too unsure" happens in one place, not scattered numbers.
_OCR_CONF_HIGH = 80.0
_OCR_CONF_LOW = 50.0

# ── Anomaly detection (page-relative confidence outliers) ─────────────────────
# The absolute bands grade each word against a fixed yardstick. They miss a word
# that is anomalous *for this page*: the low-confidence pocket past a sharp
# confidence cliff. The anomaly layer flags words whose confidence sits more than
# _OCR_ANOMALY_STDEV_K standard deviations BELOW the page mean (low outliers) and
# that are not themselves already confident (abs ceiling). Pure statistics, no I/O.
_OCR_ANOMALY_STDEV_K = 1.5            # outlier if conf < mean - k * stdev
_OCR_ANOMALY_MIN_WORDS = 8           # need a real distribution before flagging outliers
_OCR_ANOMALY_ABS_CEIL = _OCR_CONF_HIGH  # never flag a word that is itself confident
_OCR_ANOMALY_SAMPLE = 8             # bound how many concrete outliers the LLM sees

# Deterministic severity cutoffs on the uncertain / garbled fractions of the page.
# A few low-confidence words among thousands is normal OCR noise, so severity is
# driven by the *fraction* of the page that is uncertain/garbled, not a bare flag.
_OCR_SEV_HIGH_UNCERTAIN_FRACTION = 0.50
_OCR_SEV_MEDIUM_UNCERTAIN_FRACTION = 0.25
_OCR_SEV_HIGH_GARBLED_FRACTION = 0.25
_OCR_SEV_MEDIUM_GARBLED_FRACTION = 0.10

_OCR_ANOMALY_SYSTEM_PROMPT = (
    "You are an OCR-quality analyst grading how badly a page came out of an OCR "
    "engine. You are given the number of recognised words, their per-word "
    "confidence distribution (0-100 scale), the fraction that is uncertain or "
    "garbled, the lowest-confidence outlier words (the near-noise pocket past a "
    "confidence cliff), and a deterministic baseline severity. A page where most "
    "words are garbled, or where a large share is uncertain, is the most severe — "
    "the text it produced cannot be trusted. You may agree with or adjust the "
    "baseline, but justify any change. "
    'Respond ONLY with a JSON object: {"severity": "low|medium|high", "rationale": '
    '"<=160 chars", "top_concern": "<short phrase naming the main quality problem>"}. '
    "Never invent words beyond those provided."
)


def _ocr_word_fields(item: "dict | tuple | list") -> dict:
    """Read ``text`` and ``conf`` off an OCR word, tolerating dicts or pairs.

    Pure accessor that accepts either a ``{"text", "conf"}`` dict (the form
    :func:`_extract_word_confidences` emits) or a ``(text, conf)`` pair, so the
    detector works on freshly parsed words and on reloaded records alike.
    Confidence is coerced to float and clamped to ``[0, 100]``; a non-numeric or
    Tesseract "no text" sentinel (``-1``) floors to ``0.0``.
    """
    if isinstance(item, dict):
        text = item.get("text") or ""
        raw_conf = item.get("conf")
    else:
        text = item[0] if len(item) > 0 else ""
        raw_conf = item[1] if len(item) > 1 else None
    try:
        conf = float(raw_conf)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < 0:
        conf = 0.0
    if conf > 100:
        conf = 100.0
    return {"text": str(text), "conf": conf}


def _classify_ocr_confidence(conf: float) -> str:
    """Map a word's OCR confidence to a band using the named thresholds.

    Pure function: ``conf >= _OCR_CONF_HIGH`` → ``"confident"``,
    ``>= _OCR_CONF_LOW`` → ``"uncertain"``, otherwise ``"garbled"``.
    """
    if conf >= _OCR_CONF_HIGH:
        return "confident"
    if conf >= _OCR_CONF_LOW:
        return "uncertain"
    return "garbled"


def _heuristic_ocr_anomaly_severity(uncertain_count: int, garbled_count: int, total: int) -> str:
    """Deterministic OCR-anomaly severity — the always-available baseline.

    Pure function of how large a fraction of the page's words are uncertain or
    garbled. Garbled (near-noise) words weigh more heavily than merely uncertain
    ones, since a high garbled share means the recognised text cannot be trusted.
    Used directly when the LLM grade is unavailable and handed to the model as the
    reference baseline.
    """
    if total <= 0:
        return "low"
    uncertain_fraction = uncertain_count / total
    garbled_fraction = garbled_count / total
    if (garbled_fraction >= _OCR_SEV_HIGH_GARBLED_FRACTION
            or uncertain_fraction >= _OCR_SEV_HIGH_UNCERTAIN_FRACTION):
        return "high"
    if (garbled_fraction >= _OCR_SEV_MEDIUM_GARBLED_FRACTION
            or uncertain_fraction >= _OCR_SEV_MEDIUM_UNCERTAIN_FRACTION):
        return "medium"
    return "low"


def _compute_ocr_confidence_anomalies(words: "list[dict | tuple]") -> dict:
    """Pure statistical outlier pass over a page's OCR words — the authoritative heuristic.

    A *low outlier* is a word whose confidence is below ``mean -
    _OCR_ANOMALY_STDEV_K * stdev`` AND not itself confident (``conf <
    _OCR_ANOMALY_ABS_CEIL``) — the near-noise pocket past a confidence cliff.
    ``uncertain_count`` counts words below the "confident" band; ``garbled_count``
    counts words in the "garbled" band (recognition effectively noise). Below
    ``_OCR_ANOMALY_MIN_WORDS`` the distribution is too small to be meaningful, so
    only the band classification is evaluated and no statistical outliers are
    reported. No I/O, no LLM — safe to call anywhere (air-gap).

    Returns the count summary, the ranked outlier list, the garbled/uncertain
    counts, and the deterministic ``severity`` baseline.
    """
    recs = [_ocr_word_fields(w) for w in words]
    # Drop blank tokens (Tesseract emits whitespace rows with no recognised text).
    recs = [r for r in recs if r["text"].strip()]
    confs = [r["conf"] for r in recs]
    total = len(confs)

    uncertain_count = sum(1 for c in confs if c < _OCR_CONF_HIGH)
    garbled_count = sum(1 for c in confs if c < _OCR_CONF_LOW)

    if total < _OCR_ANOMALY_MIN_WORDS:
        return {
            "anomaly_count": 0,
            "mean": round(sum(confs) / total, 2) if total else 0.0,
            "stdev": 0.0,
            "threshold": 0.0,
            "uncertain_count": uncertain_count,
            "garbled_count": garbled_count,
            "anomalies": [],
            "severity": _heuristic_ocr_anomaly_severity(uncertain_count, garbled_count, total),
            "total": total,
        }

    mean = sum(confs) / total
    variance = sum((c - mean) ** 2 for c in confs) / total
    stdev = variance ** 0.5
    threshold = mean - _OCR_ANOMALY_STDEV_K * stdev

    anomalies: list[dict] = []
    for r in recs:
        conf = r["conf"]
        if conf < threshold and conf < _OCR_ANOMALY_ABS_CEIL:
            z = round((conf - mean) / stdev, 3) if stdev > 0 else 0.0
            anomalies.append({
                "text": r["text"][:60],
                "conf": round(conf, 2),
                "z_score": z,
                "band": _classify_ocr_confidence(conf),
            })
    anomalies.sort(key=lambda a: a["conf"])

    return {
        "anomaly_count": len(anomalies),
        "mean": round(mean, 2),
        "stdev": round(stdev, 2),
        "threshold": round(threshold, 2),
        "uncertain_count": uncertain_count,
        "garbled_count": garbled_count,
        "anomalies": anomalies,
        "severity": _heuristic_ocr_anomaly_severity(uncertain_count, garbled_count, total),
        "total": total,
    }


def _ai_ocr_anomaly_severity(summary: dict, anomalies: list[dict]) -> dict | None:
    """Grade OCR-confidence-anomaly severity with the LLM, grounded on real data.

    Args:
        summary: Distribution facts — word_count, mean, stdev, uncertain_count,
            garbled_count, anomaly_count, and the deterministic
            ``baseline_severity``.
        anomalies: The concrete low-confidence outlier words (recognised text +
            per-word confidence). Only the leading ``_OCR_ANOMALY_SAMPLE`` are
            sent, keeping the call cheap and bounded regardless of page size.
            Injection scanning stays ON (``skip_injection_scan`` is NOT set)
            because the recognised text is derived from arbitrary scanned images.

    Returns:
        ``{"severity": "low|medium|high", "rationale": str, "top_concern": str}``
        on success, or ``None`` when there is nothing to grade (no outliers and no
        garbled words), the model is unavailable, or the output is
        missing/blank/malformed/out-of-range. Callers MUST treat ``None`` as "use
        the deterministic heuristic" — this is best-effort enrichment, never a
        hard dependency.
    """
    if not anomalies and not summary.get("garbled_count"):
        return None
    try:
        import json as _json

        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        lines = [
            f"Page facts: {_json.dumps(summary, sort_keys=True)}",
            f"Deterministic baseline severity: {summary.get('baseline_severity')}",
            "Low-confidence outlier words:",
        ]
        for a in anomalies[:_OCR_ANOMALY_SAMPLE]:
            lines.append(f"- {_json.dumps(a, default=str)}")

        req = LLMRequest(
            messages=[
                {"role": "user", "content": "\n".join(lines) + "\n\nGrade the severity."}
            ],
            system_prompt=_OCR_ANOMALY_SYSTEM_PROMPT,
            max_tokens=200,
            temperature=0.1,
            classification="CUI",
        )
        resp = LLMRouter().invoke("dic_ocr_confidence_anomaly_severity", req)
        if not resp or not resp.content:
            return None
        raw = resp.content.strip()
        # Tolerate fenced code blocks around the JSON object.
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        parsed = _json.loads(raw[start : end + 1])
        severity = str(parsed.get("severity") or "").strip().lower()
        if severity not in {"low", "medium", "high"}:
            return None
        return {
            "severity": severity,
            "rationale": str(parsed.get("rationale") or "").strip()[:200],
            "top_concern": str(parsed.get("top_concern") or "").strip()[:80],
        }
    except Exception:
        return None


def detect_ocr_confidence_anomalies(words: "list[dict | tuple]", use_llm: bool = True) -> dict:
    """Flag a page of OCR words for low-confidence noise / a confidence cliff.

    Complements the absolute confident/uncertain/garbled bands: a page can read as
    acceptable on its absolute counts yet expose a sharp confidence cliff (a
    near-noise pocket of low outliers), or degrade outright (a large garbled
    share that makes the recognised text untrustworthy).

    The statistical detection (:func:`_compute_ocr_confidence_anomalies`) and the
    deterministic ``severity`` baseline are ALWAYS authoritative and never depend
    on the LLM. The returned ``ai_grade`` is best-effort severity enrichment and
    is ``None`` when the model is unavailable, ``use_llm`` is False, or there is
    nothing to grade.

    Args:
        words: Per-word OCR records — ``{"text", "conf"}`` dicts or ``(text, conf)``
            pairs, where ``conf`` is the engine confidence on a 0–100 scale.
        use_llm: When False, skip the optional LLM grade entirely (air-gap / cost).

    Returns:
        {
          "anomaly_count": int,
          "mean": float, "stdev": float, "threshold": float,
          "uncertain_count": int, "garbled_count": int,
          "anomalies": [{"text","conf","z_score","band"}],
          "severity": "low|medium|high",   # authoritative deterministic baseline
          "ai_grade": {...} | None,        # optional LLM refinement
        }
    """
    out = _compute_ocr_confidence_anomalies(words)
    ai_grade = None
    if use_llm:
        ai_grade = _ai_ocr_anomaly_severity(
            {"word_count": out["total"], "mean": out["mean"], "stdev": out["stdev"],
             "uncertain_count": out["uncertain_count"], "garbled_count": out["garbled_count"],
             "anomaly_count": out["anomaly_count"], "baseline_severity": out["severity"]},
            out["anomalies"],
        )
    out.pop("total", None)
    out["ai_grade"] = ai_grade
    return out


def _extract_word_confidences(img) -> "tuple[list[dict], str]":
    """Run Tesseract on a PIL image and return per-word confidences + reconstructed text.

    Uses ``pytesseract.image_to_data`` (the structured, confidence-bearing call)
    rather than ``image_to_string``, so the per-word engine confidence used by the
    anomaly detector is captured in the SAME pass that produces the text. Words are
    grouped back into lines by their (block, paragraph, line) indices to preserve
    layout. Returns ``([], "")`` when pytesseract or its native binary is
    unavailable — the caller falls back to the plain string path.
    """
    try:
        import pytesseract
        from pytesseract import Output
    except Exception:
        return [], ""

    try:
        data = pytesseract.image_to_data(img, output_type=Output.DICT)
    except Exception as exc:
        logger.debug("dic.extractors: image_to_data failed: %s", exc)
        return [], ""

    words: list[dict] = []
    line_parts: list[str] = []
    text_lines: list[str] = []
    last_key: tuple | None = None
    n = len(data.get("text", []))
    for i in range(n):
        token = (data["text"][i] or "").strip()
        if not token:
            continue
        key = (data.get("block_num", [0] * n)[i],
               data.get("par_num", [0] * n)[i],
               data.get("line_num", [0] * n)[i])
        if last_key is not None and key != last_key and line_parts:
            text_lines.append(" ".join(line_parts))
            line_parts = []
        last_key = key
        line_parts.append(token)
        words.append(_ocr_word_fields({"text": token, "conf": data["conf"][i]}))
    if line_parts:
        text_lines.append(" ".join(line_parts))

    return words, "\n".join(text_lines)


def ocr_image_with_quality(img, use_llm: bool = True) -> "tuple[str, dict]":
    """OCR a PIL image and annotate it with an OCR-confidence-quality report.

    Returns the recognised text together with the deterministic OCR-confidence
    report from :func:`detect_ocr_confidence_anomalies` (low-confidence / garbled
    flags plus an optional best-effort LLM severity grade). Quality assessment
    never changes which words Tesseract recognised. If the structured
    confidence-bearing path is unavailable (no pytesseract binary), the text falls
    back to the existing OCR cascade and the report is an empty-page baseline.
    """
    words, text = _extract_word_confidences(img)
    if not words:
        # No confidence-bearing path available — keep text fidelity via the
        # existing cascade; the report degrades to a clean empty-page baseline.
        text = _ocr_image(img)
    report = detect_ocr_confidence_anomalies(words, use_llm=use_llm)
    return text, report
