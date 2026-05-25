#!/usr/bin/env python3
# CUI // SP-CTI
"""PDF extraction provider ABC with 4 backends (D-RAG-15).

Fallback chain: Anthropic → Google → Vision(LLaVA) → pypdf text-only.
Air-gapped environments use pypdf (always available) with optional
LLaVA vision for scanned PDFs.

Uses D66 ABC provider pattern.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger("icdev.rag.pdf_provider")


@dataclass
class PDFPage:
    """Extracted content from a single PDF page."""

    page_number: int
    text: str
    provider_used: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PDFExtraction:
    """Full extraction result for a PDF file."""

    file_name: str
    file_path: str
    file_hash: str
    page_count: int
    pages: List[PDFPage]
    provider_used: str
    status: str = "extracted"
    error: str = ""

    @property
    def full_text(self) -> str:
        """Concatenate all page texts."""
        return "\n\n".join(p.text for p in self.pages if p.text)


# ---------------------------------------------------------------------------
# PDFProvider ABC (D66 pattern)
# ---------------------------------------------------------------------------


class PDFProvider(ABC):
    """Abstract base class for PDF text extraction."""

    provider_name: str = "base"

    @abstractmethod
    def extract(self, pdf_path: Path, max_pages: int = 200) -> List[PDFPage]:
        """Extract text from PDF, return per-page results."""

    @abstractmethod
    def check_availability(self) -> bool:
        """Check if this provider is available."""

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"


# ---------------------------------------------------------------------------
# Anthropic PDF Provider (native PDF support)
# ---------------------------------------------------------------------------


class AnthropicPDFProvider(PDFProvider):
    """Anthropic native PDF support via messages API (D-RAG-15).

    Uses base64-encoded PDF → messages API with document type.
    Requires anthropic SDK and API key.
    """

    provider_name = "anthropic"

    def __init__(self, api_key: str = "", model: str = "claude-sonnet-4-20250514"):
        self._api_key = api_key
        self._model = model

    def check_availability(self) -> bool:
        import importlib.util

        if importlib.util.find_spec("anthropic") is None:
            return False
        key = self._resolve_key()
        return bool(key)

    def _resolve_key(self) -> str:
        if self._api_key:
            return self._api_key
        try:
            from tools.rag.secret_ref import resolve_secret

            return resolve_secret("env:ANTHROPIC_API_KEY")
        except Exception:
            pass
        import os

        return os.environ.get("ANTHROPIC_API_KEY", "")

    def extract(self, pdf_path: Path, max_pages: int = 200) -> List[PDFPage]:
        import base64

        from tools.llm.anthropic_provider import AnthropicLLMProvider

        key = self._resolve_key()
        if not key:
            raise RuntimeError("No Anthropic API key available")

        pdf_data = pdf_path.read_bytes()
        b64 = base64.standard_b64encode(pdf_data).decode("utf-8")

        client = AnthropicLLMProvider(api_key=key)._get_client()
        message = client.messages.create(
            model=self._model,
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Extract all text content from this PDF document. "
                                "Return the text organized by page, with each page "
                                "prefixed by [PAGE N]. Preserve structure, headings, "
                                "and tables where possible."
                            ),
                        },
                    ],
                }
            ],
        )

        raw_text = message.content[0].text if message.content else ""
        return self._parse_pages(raw_text)

    def _parse_pages(self, text: str) -> List[PDFPage]:
        """Parse LLM output with [PAGE N] markers into PDFPage list."""
        import re

        pages: List[PDFPage] = []
        parts = re.split(r"\[PAGE\s+(\d+)\]", text)

        if len(parts) < 3:
            # No page markers — treat as single page
            if text.strip():
                pages.append(PDFPage(page_number=1, text=text.strip(), provider_used=self.provider_name))
            return pages

        for i in range(1, len(parts), 2):
            page_num = int(parts[i])
            content = parts[i + 1].strip() if i + 1 < len(parts) else ""
            if content:
                pages.append(PDFPage(page_number=page_num, text=content, provider_used=self.provider_name))

        return pages


# ---------------------------------------------------------------------------
# Google PDF Provider (Gemini multimodal)
# ---------------------------------------------------------------------------


class GooglePDFProvider(PDFProvider):
    """Google Gemini PDF extraction (D-RAG-15).

    Uses Gemini multimodal with inline PDF data.
    Requires google-generativeai SDK and API key.
    """

    provider_name = "google"

    def __init__(self, api_key: str = "", model: str = "gemini-2.0-flash"):
        self._api_key = api_key
        self._model = model

    def check_availability(self) -> bool:
        try:
            import google.generativeai  # noqa: F401

            key = self._resolve_key()
            return bool(key)
        except ImportError:
            return False

    def _resolve_key(self) -> str:
        if self._api_key:
            return self._api_key
        try:
            from tools.rag.secret_ref import resolve_secret

            return resolve_secret("env:GOOGLE_API_KEY")
        except Exception:
            pass
        import os

        return os.environ.get("GOOGLE_API_KEY", "")

    def extract(self, pdf_path: Path, max_pages: int = 200) -> List[PDFPage]:
        import google.generativeai as genai

        key = self._resolve_key()
        if not key:
            raise RuntimeError("No Google API key available")

        genai.configure(api_key=key)
        model = genai.GenerativeModel(self._model)

        pdf_data = pdf_path.read_bytes()
        response = model.generate_content(
            [
                {
                    "mime_type": "application/pdf",
                    "data": pdf_data,
                },
                (
                    "Extract all text content from this PDF document. "
                    "Return the text organized by page, with each page "
                    "prefixed by [PAGE N]. Preserve structure and tables."
                ),
            ]
        )

        raw_text = response.text if response else ""
        return AnthropicPDFProvider._parse_pages(None, raw_text)  # Reuse parser


# ---------------------------------------------------------------------------
# Vision PDF Provider (LLaVA via Ollama — page-by-page)
# ---------------------------------------------------------------------------


class VisionPDFProvider(PDFProvider):
    """Page-by-page vision extraction via LLaVA/Ollama (D-RAG-15).

    Reuses the approach from document_extractor.py: render each page
    as image, send to vision LLM for OCR.
    """

    provider_name = "vision_llava"

    def __init__(self, model: str = "llava:latest", base_url: str = ""):
        self._model = model
        self._base_url = base_url

    def check_availability(self) -> bool:
        try:
            from tools.http.client import request

            base = self._base_url or self._get_base_url()
            resp = request("GET", f"{base}/api/tags", timeout=3)
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                return any("llava" in m.get("name", "").lower() for m in models)
        except Exception:
            pass
        return False

    def _get_base_url(self) -> str:
        if self._base_url:
            return self._base_url
        import os

        return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/v1").rstrip("/")

    def extract(self, pdf_path: Path, max_pages: int = 200) -> List[PDFPage]:
        try:
            from pypdf import PdfReader
        except ImportError:
            raise RuntimeError("pypdf required for vision extraction (page rendering)")

        import base64

        from tools.http.client import request as _http_request

        reader = PdfReader(str(pdf_path))
        pages: List[PDFPage] = []
        base_url = self._get_base_url()

        for i, page in enumerate(reader.pages[:max_pages]):
            # Try text extraction first
            text = page.extract_text() or ""
            if len(text.strip()) > 50:
                pages.append(
                    PDFPage(
                        page_number=i + 1,
                        text=text.strip(),
                        provider_used="pypdf_text",
                        metadata={"fallback": True},
                    )
                )
                continue

            # Vision fallback for pages with no/little text (scanned)
            try:
                # Use pdf2image if available, otherwise skip vision for this page
                from pdf2image import convert_from_path

                images = convert_from_path(str(pdf_path), first_page=i + 1, last_page=i + 1, dpi=150)
                if not images:
                    continue

                import io

                buf = io.BytesIO()
                images[0].save(buf, format="PNG")
                img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

                resp = _http_request(
                    "POST",
                    f"{base_url}/api/generate",
                    json={
                        "model": self._model,
                        "prompt": "Extract all text content from this page image. Preserve layout.",
                        "images": [img_b64],
                        "stream": False,
                    },
                    timeout=60,
                )
                if resp.status_code == 200:
                    text = resp.json().get("response", "").strip()
                    if text:
                        pages.append(
                            PDFPage(
                                page_number=i + 1,
                                text=text,
                                provider_used=self.provider_name,
                            )
                        )
            except ImportError:
                # pdf2image not available — use pypdf text even if sparse
                if text.strip():
                    pages.append(
                        PDFPage(
                            page_number=i + 1,
                            text=text.strip(),
                            provider_used="pypdf_text",
                            metadata={"sparse": True},
                        )
                    )
            except Exception as exc:
                logger.debug("Vision extraction failed for page %d: %s", i + 1, exc)
                if text.strip():
                    pages.append(
                        PDFPage(
                            page_number=i + 1,
                            text=text.strip(),
                            provider_used="pypdf_text",
                        )
                    )

        return pages


# ---------------------------------------------------------------------------
# PyPDF Provider (text-only, air-gap baseline)
# ---------------------------------------------------------------------------


class PyPDFProvider(PDFProvider):
    """pypdf text-only extraction — air-gap baseline (D-RAG-15).

    Always available if pypdf is installed. No network calls.
    """

    provider_name = "pypdf_text"

    def check_availability(self) -> bool:
        try:
            from pypdf import PdfReader  # noqa: F401

            return True
        except ImportError:
            return False

    def extract(self, pdf_path: Path, max_pages: int = 200) -> List[PDFPage]:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        pages: List[PDFPage] = []

        for i, page in enumerate(reader.pages[:max_pages]):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(
                    PDFPage(
                        page_number=i + 1,
                        text=text.strip(),
                        provider_used=self.provider_name,
                    )
                )

        return pages


# ---------------------------------------------------------------------------
# Provider chain factory
# ---------------------------------------------------------------------------


def _diagnose_provider_unavailable(name: str, config: Optional[dict] = None) -> str:
    """Return a human-readable reason why a named provider isn't usable.

    Used by extract_pdf when the provider chain is empty so operators get an
    actionable error instead of just 'install pypdf'. Each branch isolates the
    SDK import + credential check so a single missing dependency doesn't
    poison the whole diagnostic.
    """
    cfg = config or {}
    if name == "anthropic":
        import importlib.util

        if importlib.util.find_spec("anthropic") is None:
            return "anthropic SDK not installed (pip install anthropic)"
        import os as _os
        if not (cfg.get("anthropic", {}).get("api_key") or _os.environ.get("ANTHROPIC_API_KEY")):
            return "ANTHROPIC_API_KEY not set"
        return "available but excluded by chain order"
    if name == "google":
        try:
            import google.generativeai  # noqa: F401
        except ImportError:
            return "google-generativeai SDK not installed"
        import os as _os
        if not (cfg.get("google", {}).get("api_key") or _os.environ.get("GOOGLE_API_KEY")):
            return "GOOGLE_API_KEY not set"
        return "available but excluded by chain order"
    if name == "vision_llava":
        try:
            import requests  # noqa: F401
        except ImportError:
            return "requests not installed"
        return "Ollama not reachable or llava model not pulled (`ollama pull llava`)"
    if name == "pypdf_text":
        try:
            from pypdf import PdfReader  # noqa: F401
            return "available — should have been added to chain (config issue?)"
        except ImportError:
            return "pypdf not installed (pip install pypdf>=4.0) — REQUIRED for air-gap baseline"
    return "unknown provider"


def get_pdf_provider_chain(config: Optional[dict] = None) -> List[PDFProvider]:
    """Build PDF provider chain from config (D-RAG-15).

    Args:
        config: PDF config section from rag_config.yaml.

    Returns:
        Ordered list of available PDFProviders (fallback chain).
    """
    cfg = config or {}
    chain_order = cfg.get(
        "provider_chain",
        [
            "anthropic",
            "google",
            "vision_llava",
            "pypdf_text",
        ],
    )

    provider_map = {
        "anthropic": lambda: AnthropicPDFProvider(
            api_key=cfg.get("anthropic", {}).get("api_key", ""),
            model=cfg.get("anthropic", {}).get("model", "claude-sonnet-4-20250514"),
        ),
        "google": lambda: GooglePDFProvider(
            api_key=cfg.get("google", {}).get("api_key", ""),
            model=cfg.get("google", {}).get("model", "gemini-2.0-flash"),
        ),
        "vision_llava": lambda: VisionPDFProvider(
            model=cfg.get("vision_llava", {}).get("model", "llava:latest"),
        ),
        "pypdf_text": lambda: PyPDFProvider(),
    }

    chain: List[PDFProvider] = []
    for name in chain_order:
        factory = provider_map.get(name)
        if factory:
            try:
                provider = factory()
                if provider.check_availability():
                    chain.append(provider)
                    logger.debug("PDF provider available: %s", name)
                else:
                    logger.debug("PDF provider not available: %s", name)
            except Exception as exc:
                logger.debug("PDF provider init failed for %s: %s", name, exc)

    return chain


def extract_pdf(
    pdf_path: Path,
    config: Optional[dict] = None,
    max_pages: int = 200,
) -> PDFExtraction:
    """Extract text from a PDF using the best available provider.

    Tries providers in chain order, returns first successful extraction.

    Args:
        pdf_path: Path to PDF file.
        config: PDF config section from rag_config.yaml.
        max_pages: Maximum pages to extract.

    Returns:
        PDFExtraction with extracted pages and metadata.
    """
    cfg = config or {}
    max_pages = cfg.get("max_pages", max_pages)
    # SEC: Default max size reduced from 50MB to 25MB to limit memory usage
    max_size_mb = cfg.get("max_file_size_mb", 25)

    path = Path(pdf_path)
    if not path.exists():
        return PDFExtraction(
            file_name=path.name,
            file_path=str(path),
            file_hash="",
            page_count=0,
            pages=[],
            provider_used="none",
            status="failed",
            error=f"File not found: {path}",
        )

    # Check file size
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > max_size_mb:
        return PDFExtraction(
            file_name=path.name,
            file_path=str(path),
            file_hash="",
            page_count=0,
            pages=[],
            provider_used="none",
            status="failed",
            error=f"File too large: {size_mb:.1f}MB > {max_size_mb}MB limit",
        )

    # Compute file hash for dedup (D-RAG-5)
    # SEC: Use chunked reading for hash to avoid loading entire file into memory
    hash_obj = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            hash_obj.update(block)
    file_hash = hash_obj.hexdigest()

    chain = get_pdf_provider_chain(cfg)
    if not chain:
        # OPT-33: actionable diagnostic — tell the operator which providers
        # were tried and why each was excluded so they can fix the right thing
        # in their environment (air-gap vs cloud, missing SDK vs missing key).
        diagnostics = []
        for name in ("anthropic", "google", "vision_llava", "pypdf_text"):
            reason = _diagnose_provider_unavailable(name, cfg)
            diagnostics.append(f"  - {name}: {reason}")
        msg = (
            "No PDF extraction provider available. Tried:\n"
            + "\n".join(diagnostics)
            + "\nMinimum air-gap fix: pip install pypdf>=4.0"
        )
        logger.error(msg)
        return PDFExtraction(
            file_name=path.name,
            file_path=str(path),
            file_hash=file_hash,
            page_count=0,
            pages=[],
            provider_used="none",
            status="failed",
            error=msg,
        )

    last_error = ""
    for provider in chain:
        try:
            pages = provider.extract(path, max_pages=max_pages)
            if pages:
                logger.info(
                    "Extracted %d pages from %s via %s",
                    len(pages),
                    path.name,
                    provider.provider_name,
                )
                return PDFExtraction(
                    file_name=path.name,
                    file_path=str(path),
                    file_hash=file_hash,
                    page_count=len(pages),
                    pages=pages,
                    provider_used=provider.provider_name,
                )
        except Exception as exc:
            last_error = f"{provider.provider_name}: {exc}"
            logger.debug("PDF provider %s failed: %s", provider.provider_name, exc)
            continue

    return PDFExtraction(
        file_name=path.name,
        file_path=str(path),
        file_hash=file_hash,
        page_count=0,
        pages=[],
        provider_used="none",
        status="failed",
        error=f"All providers failed. Last: {last_error}",
    )
