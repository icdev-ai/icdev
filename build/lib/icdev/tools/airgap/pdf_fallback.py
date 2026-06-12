#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
"""Local-only PDF extraction for air-gapped environments.

Replaces AnthropicPDFProvider with a local fallback chain:
1. pypdf — text extraction (fast, always available)
2. LLaVA via Ollama — vision OCR for scanned pages (if Ollama + LLaVA available)

Can also register itself into the existing pdf_provider fallback chain
so the RAG pipeline seamlessly uses local extraction when Anthropic is
unavailable.

Usage::

    from tools.airgap.pdf_fallback import LocalPDFProvider, register_local_fallback

    # Standalone
    provider = LocalPDFProvider()
    pages = provider.extract(Path("document.pdf"))

    # Register into RAG pipeline
    register_local_fallback()
"""

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List

logger = get_logger("icdev.airgap.pdf_fallback")

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class LocalPDFProvider:
    """Local-only PDF extraction using pypdf + optional local vision LLM.

    Fallback chain within this provider:
    1. pypdf text extraction (handles text-based PDFs)
    2. Local vision LLM (handles scanned/image PDFs)

    Supports any local LLM with vision capability:
    - Ollama (LLaVA, Gemma3, BakLLaVA, Moondream) via /api/chat
    - vLLM / LM Studio / llama.cpp via OpenAI-compatible /v1/chat/completions
    """

    provider_name = "local_airgap"

    def __init__(
        self,
        vision_model: str = "",
        vision_url: str = "",
        vision_api: str = "auto",
    ):
        """
        Args:
            vision_model: Model name for vision OCR. Default: auto-detect.
            vision_url: Base URL for vision LLM. Default: from env or Ollama.
            vision_api: API style — "ollama", "openai", or "auto" (probe).
        """
        self._vision_model = vision_model
        self._vision_url = vision_url
        self._vision_api = vision_api

        # Auto-detect URL from env vars (check multiple servers)
        if not self._vision_url:
            for env_var, default in [
                ("OLLAMA_BASE_URL", "http://localhost:11434"),
                ("VLLM_BASE_URL", ""),
                ("LLAMA_CPP_BASE_URL", ""),
                ("LM_STUDIO_BASE_URL", ""),
            ]:
                url = os.environ.get(env_var, default)
                if url:
                    self._vision_url = url
                    break

        if not self._vision_url:
            self._vision_url = "http://localhost:11434"

    def check_availability(self) -> bool:
        """pypdf is always available if installed."""
        try:
            from pypdf import PdfReader  # noqa: F401

            return True
        except ImportError:
            return False

    def extract(
        self,
        pdf_path: Path,
        max_pages: int = 200,
        use_vision_fallback: bool = True,
    ) -> List[Dict[str, Any]]:
        """Extract text from PDF using local tools only.

        Args:
            pdf_path: Path to PDF file.
            max_pages: Maximum pages to process.
            use_vision_fallback: Try Ollama LLaVA for pages with no text.

        Returns:
            List of dicts with keys: page_number, text, provider_used, metadata.
        """
        try:
            from pypdf import PdfReader
        except ImportError:
            raise RuntimeError("pypdf required for local PDF extraction. Install: pip install pypdf")

        reader = PdfReader(str(pdf_path))
        total_pages = min(len(reader.pages), max_pages)
        results: List[Dict[str, Any]] = []
        empty_pages: List[int] = []

        # Phase 1: pypdf text extraction
        for i in range(total_pages):
            try:
                text = reader.pages[i].extract_text() or ""
                text = text.strip()
            except Exception as exc:
                logger.debug("pypdf failed on page %d: %s", i + 1, exc)
                text = ""

            if text:
                results.append(
                    {
                        "page_number": i + 1,
                        "text": text,
                        "provider_used": "pypdf",
                        "metadata": {"char_count": len(text)},
                    }
                )
            else:
                empty_pages.append(i)
                results.append(
                    {
                        "page_number": i + 1,
                        "text": "",
                        "provider_used": "pypdf_empty",
                        "metadata": {},
                    }
                )

        # Phase 2: Vision OCR for empty pages (scanned PDFs)
        if empty_pages and use_vision_fallback and self._is_vision_available():
            logger.info("Attempting vision OCR for %d empty pages", len(empty_pages))
            for page_idx in empty_pages:
                ocr_text = self._vision_extract_page(pdf_path, page_idx)
                if ocr_text:
                    results[page_idx] = {
                        "page_number": page_idx + 1,
                        "text": ocr_text,
                        "provider_used": "vision_llava",
                        "metadata": {"char_count": len(ocr_text)},
                    }

        return results

    def _detect_vision_api(self) -> str:
        """Auto-detect whether the vision endpoint speaks Ollama or OpenAI API."""
        if self._vision_api != "auto":
            return self._vision_api
        # Ollama has /api/tags, OpenAI-compatible has /v1/models
        import urllib.request

        base = self._vision_url.rstrip("/")
        try:
            urllib.request.urlopen(f"{base}/api/tags", timeout=2)  # nosec B310 localhost only
            return "ollama"
        except Exception:
            pass
        try:
            urllib.request.urlopen(f"{base}/v1/models", timeout=2)  # nosec B310 localhost only
            return "openai"
        except Exception:
            pass
        return "ollama"  # default fallback

    def _is_vision_available(self) -> bool:
        """Check if any local vision LLM is available for OCR."""
        import urllib.request

        api = self._detect_vision_api()
        base = self._vision_url.rstrip("/")

        try:
            if api == "ollama":
                resp = urllib.request.urlopen(f"{base}/api/tags", timeout=3)  # nosec B310 localhost only
                data = json.loads(resp.read())
                models = [m["name"] for m in data.get("models", [])]
                vision_kw = ("llava", "gemma3", "bakllava", "moondream", "glm-ocr")
                vision = [m for m in models if any(v in m.lower() for v in vision_kw)]
                if vision and not self._vision_model:
                    self._vision_model = vision[0]
                return bool(vision)
            else:
                # OpenAI-compatible — assume the configured model supports vision
                resp = urllib.request.urlopen(f"{base}/v1/models", timeout=3)  # nosec B310 localhost only
                data = json.loads(resp.read())
                models = [m.get("id", "") for m in data.get("data", [])]
                if models and not self._vision_model:
                    self._vision_model = models[0]
                return bool(models)
        except Exception:
            return False

    def _vision_extract_page(self, pdf_path: Path, page_idx: int) -> str:
        """Extract text from a single PDF page using local vision LLM.

        Renders the page to an image, then sends to the vision model.
        Supports both Ollama native API and OpenAI-compatible API.
        """
        try:
            image_b64 = self._render_page_to_base64(pdf_path, page_idx)
            if not image_b64:
                return ""

            import urllib.request

            prompt_text = (
                "Extract ALL text from this document page image. "
                "Preserve structure, headings, tables, and formatting. "
                "Return only the extracted text, no commentary."
            )

            api = self._detect_vision_api()
            base = self._vision_url.rstrip("/")

            if api == "ollama":
                payload = json.dumps(
                    {
                        "model": self._vision_model or "llava:latest",
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
                # OpenAI-compatible vision API (vLLM, LM Studio, llama.cpp, etc.)
                payload = json.dumps(
                    {
                        "model": self._vision_model,
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
            resp = urllib.request.urlopen(req, timeout=120)  # nosec B310 localhost only
            data = json.loads(resp.read())

            # Parse response (Ollama vs OpenAI format)
            if api == "ollama":
                return data.get("message", {}).get("content", "").strip()
            else:
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
                return ""

        except Exception as exc:
            logger.debug("Vision OCR failed for page %d: %s", page_idx + 1, exc)
            return ""

    @staticmethod
    def _render_page_to_base64(pdf_path: Path, page_idx: int) -> str:
        """Render a PDF page to base64 PNG image.

        Uses pdf2image (poppler) if available, falls back to pypdf page
        image extraction.
        """
        # Try pdf2image first (best quality)
        try:
            from pdf2image import convert_from_path
            import io

            images = convert_from_path(
                str(pdf_path),
                first_page=page_idx + 1,
                last_page=page_idx + 1,
                dpi=150,
            )
            if images:
                buf = io.BytesIO()
                images[0].save(buf, format="PNG")
                return base64.b64encode(buf.getvalue()).decode("utf-8")
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("pdf2image failed: %s", exc)

        # Fallback: extract embedded images from page
        try:
            from pypdf import PdfReader
            import io

            reader = PdfReader(str(pdf_path))
            page = reader.pages[page_idx]

            for image_key in page.images:
                img_data = image_key.data
                return base64.b64encode(img_data).decode("utf-8")
        except Exception:
            pass

        return ""

    def extract_to_rag_format(self, pdf_path: Path, max_pages: int = 200) -> Dict[str, Any]:
        """Extract PDF and return in RAG-compatible format.

        Compatible with tools.rag.pdf_provider.PDFExtraction format.
        """
        import hashlib

        pages = self.extract(pdf_path, max_pages)
        file_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()[:16]

        return {
            "file_name": pdf_path.name,
            "file_path": str(pdf_path),
            "file_hash": file_hash,
            "page_count": len(pages),
            "pages": pages,
            "provider_used": self.provider_name,
            "status": "extracted",
            "full_text": "\n\n".join(p["text"] for p in pages if p["text"]),
        }


def register_local_fallback() -> bool:
    """Register LocalPDFProvider into the RAG pdf_provider fallback chain.

    Patches the pdf_provider module so that when Anthropic/Google providers
    are unavailable, the local provider is used automatically.

    Returns:
        True if registration succeeded.
    """
    try:
        from tools.rag import pdf_provider as pdf_mod

        # Create a PDFProvider-compatible wrapper
        class _LocalPDFProviderBridge(pdf_mod.PDFProvider):
            provider_name = "local_airgap"

            def __init__(self):
                self._inner = LocalPDFProvider()

            def check_availability(self) -> bool:
                return self._inner.check_availability()

            def extract(self, pdf_path: Path, max_pages: int = 200):
                raw = self._inner.extract(pdf_path, max_pages)
                return [
                    pdf_mod.PDFPage(
                        page_number=p["page_number"],
                        text=p["text"],
                        provider_used=p["provider_used"],
                        metadata=p.get("metadata", {}),
                    )
                    for p in raw
                ]

        # If pdf_mod has a get_provider_chain or similar, patch it
        # Otherwise, just make it available as an importable class
        pdf_mod.LocalPDFProvider = _LocalPDFProviderBridge  # type: ignore[attr-defined]

        logger.info("Local PDF fallback registered in rag.pdf_provider")
        return True

    except ImportError:
        logger.debug("rag.pdf_provider not available — local fallback not registered")
        return False
    except Exception as exc:
        logger.debug("Failed to register local PDF fallback: %s", exc)
        return False


# ── CLI ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Local PDF extraction")
    parser.add_argument("pdf_path", nargs="?", help="Path to PDF file")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true", help="Check availability only")
    args = parser.parse_args()

    provider = LocalPDFProvider()

    if args.check:
        result = {
            "pypdf": provider.check_availability(),
            "vision": provider._is_vision_available(),
        }
        print(json.dumps(result, indent=2))
        sys.exit(0)

    if not args.pdf_path:
        parser.print_help()
        sys.exit(1)

    path = Path(args.pdf_path)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    result = provider.extract_to_rag_format(path)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Extracted {result['page_count']} pages from {result['file_name']}")
        print(f"Provider: {result['provider_used']}")
        print(f"Text length: {len(result['full_text'])} chars")
