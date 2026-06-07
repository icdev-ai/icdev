#!/usr/bin/env python3
"""DIC ingest orchestrator — route file -> provider -> RAG ingest + KG bridge + dic rows.

[TEMPLATE: CUI // SP-CTI]

Given a file path and a collection id this module:

1. Picks an extractor (provider) by file extension. If the dic-ingest-02
   provider package (``tools.document_intelligence.providers``) is installed it
   is preferred; otherwise a small built-in text/markup extractor is used.
2. REUSES the RAG layer: ``tools.rag.chunker.chunk_content`` to chunk, then the
   same embed + vector-store upsert path that ``ingestion_manager.ingest_source``
   uses (embedding provider via ``tools.llm`` + ``VectorStoreFactory``).
   ``ingest_source`` itself only ingests rows from registered ICDEV source
   tables (SOURCE_REGISTRY), not arbitrary files, so we reuse its building
   blocks rather than the function directly.
3. Bridges each chunk into the Knowledge Graph via
   ``tools.rag.rag_to_kg_ingester.ingest_chunk``.
4. Writes DIC bookkeeping rows: ``dic_documents`` + an initial
   ``dic_versions(origin='human_authored', status='approved')`` row, plus
   ``dic_chunk_links`` mapping each rag chunk back to the document and its
   page/section.

Every row is stamped with ``tenant_id``/``classification`` taken from the
caller's security context (Flask ``g.security_context`` when present) or
explicit overrides, so writes participate in RBAC+ABAC+RLS access control
(dic-authz-01).

Embedding and KG bridging are best-effort: if the vector store or embedding
provider is unavailable (e.g. air-gapped/headless without Ollama) the DIC rows
are still written and the failure is reported in the result, never raised.
"""
from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure repo root on path when run as a script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger
from tools.rag.chunker import chunk_content

logger = get_logger(__name__)

# Import built-in extractors (air-gap safe fallbacks for PDF/DOCX/XLSX/PPTX/images).
try:
    from tools.document_intelligence import extractors as _extractors
except Exception:
    _extractors = None  # type: ignore


# --------------------------------------------------------------------------- #
# Extraction wrapper (built-in extractors + optional provider package)
# --------------------------------------------------------------------------- #

@dataclass
class Extraction:
    """Normalized output of an extractor."""

    text: str
    provider: str
    content_type: str
    page_count: int = 1
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _select_extractor(path: Path) -> Extraction:
    """Pick a provider by extension and extract text.

    1. Built-in extractors (pypdf, python-docx, openpyxl, python-pptx, OCR, html, text).
    2. Optional dic-ingest-02 provider registry when importable.
    3. Last-resort utf-8 decode with a warning.
    """
    ext = path.suffix.lower()

    # 1) Built-in extractors (air-gap safe, no external cloud calls).
    if _extractors is not None:
        try:
            result = _extractors.extract_file(path)
            if result.warnings:
                logger.warning("dic: extraction warnings for %s: %s", path.name, result.warnings)
            return Extraction(
                text=result.text,
                provider=result.provider,
                content_type=result.content_type,
                page_count=result.page_count,
                title=result.title,
                warnings=result.warnings,
            )
        except Exception as exc:
            logger.warning("dic: built-in extractor failed for %s: %s", path.name, exc)

    # 2) Optional provider package (dic-ingest-02).
    provider = _try_provider_package(path, ext)
    if provider is not None:
        return provider

    # 3) Last-resort: best-effort utf-8 decode.
    raw = path.read_text(encoding="utf-8", errors="replace")
    return Extraction(
        text=raw,
        provider="builtin-fallback",
        content_type="application/octet-stream",
        page_count=1,
        title=path.stem,
        warnings=[f"Unrecognized extension '{ext}' — best-effort text decode."],
    )


def _try_provider_package(path: Path, ext: str) -> Extraction | None:
    """Best-effort bridge to the dic-ingest-02 provider registry."""
    try:
        from tools.document_intelligence import providers as prov  # type: ignore
    except Exception:
        return None

    getter = getattr(prov, "get_provider_for_extension", None) or getattr(
        prov, "select_provider", None
    )
    if getter is None:
        return None
    try:
        provider = getter(ext)
        if provider is None:
            return None
        result = provider.extract(str(path))
        text = getattr(result, "text", None)
        if text is None and isinstance(result, dict):
            text = result.get("text", "")
        pages = getattr(result, "page_count", None)
        if pages is None and isinstance(result, dict):
            pages = result.get("page_count", 1)
        return Extraction(
            text=text or "",
            provider=getattr(provider, "name", provider.__class__.__name__),
            content_type=getattr(result, "content_type", "") or ext.lstrip("."),
            page_count=int(pages or 1),
            title=getattr(result, "title", "") or path.stem,
        )
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# DIC schema (idempotent; mirrors the canonical migration once it lands)
# --------------------------------------------------------------------------- #

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS dic_documents (
        doc_id          TEXT PRIMARY KEY,
        collection_id   TEXT NOT NULL,
        source_id       TEXT,
        filename        TEXT,
        filepath        TEXT,
        content_type    TEXT,
        provider        TEXT,
        title           TEXT,
        byte_size       INTEGER,
        content_sha256  TEXT,
        page_count      INTEGER DEFAULT 1,
        created_at      TEXT NOT NULL,
        tenant_id       TEXT,
        classification  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dic_versions (
        version_id      TEXT PRIMARY KEY,
        doc_id          TEXT NOT NULL,
        version_no      INTEGER NOT NULL DEFAULT 1,
        origin          TEXT NOT NULL DEFAULT 'human_authored',
        status          TEXT NOT NULL DEFAULT 'approved',
        assigned_to     TEXT,
        review_notes    TEXT,
        content_sha256  TEXT,
        created_at      TEXT NOT NULL,
        created_by      TEXT,
        tenant_id       TEXT,
        classification  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dic_chunk_links (
        link_id         TEXT PRIMARY KEY,
        doc_id          TEXT NOT NULL,
        version_id      TEXT NOT NULL,
        rag_chunk_id    TEXT NOT NULL,
        collection_id   TEXT,
        chunk_index     INTEGER NOT NULL,
        page            INTEGER,
        section         TEXT,
        created_at      TEXT NOT NULL,
        tenant_id       TEXT,
        classification  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dic_review_notes (
        note_id         TEXT PRIMARY KEY,
        item_id         TEXT NOT NULL,
        item_type       TEXT NOT NULL DEFAULT 'version',
        note_text       TEXT,
        reviewer_id     TEXT,
        created_at      TEXT NOT NULL,
        tenant_id       TEXT,
        classification  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dic_sections (
        section_id      TEXT PRIMARY KEY,
        version_id      TEXT NOT NULL,
        doc_id          TEXT NOT NULL,
        heading         TEXT NOT NULL,
        content         TEXT,
        citations_json  TEXT,
        status          TEXT DEFAULT 'draft',
        origin          TEXT DEFAULT 'ai_generated',
        created_at      TEXT NOT NULL,
        created_by      TEXT,
        assigned_to     TEXT,
        reviewed_by     TEXT,
        reviewed_at     TEXT,
        rev             INTEGER DEFAULT 1,
        tenant_id       TEXT,
        classification  TEXT
    )
    """,

]


# Columns that may need adding to existing tables (idempotent ALTER TABLE).
# SQLite does not support ADD COLUMN IF NOT EXISTS, so we catch OperationalError.
_ALTER_MIGRATIONS = [
    ("dic_versions", "assigned_to", "TEXT"),
    ("dic_versions", "review_notes", "TEXT"),
    ("dic_ssp_fragments", "assigned_to", "TEXT"),
    # Collaboration + optimistic-concurrency columns on dic_sections. These are
    # referenced by the section assignment / HITL-review / edit endpoints, so they
    # must exist on tables created before this schema revision.
    ("dic_sections", "assigned_to", "TEXT"),
    ("dic_sections", "reviewed_by", "TEXT"),
    ("dic_sections", "reviewed_at", "TEXT"),
    ("dic_sections", "rev", "INTEGER DEFAULT 1"),
]


def _ensure_schema(conn) -> None:
    cur = conn.cursor()
    for ddl in _SCHEMA:
        cur.execute(ddl)
    # Best-effort add missing columns for backward compatibility.
    for table, col, dtype in _ALTER_MIGRATIONS:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {dtype}")
        except Exception:
            pass
    conn.commit()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _doc_id(collection_id: str, filepath: str) -> str:
    h = hashlib.sha256(f"{collection_id}:{filepath}".encode()).hexdigest()[:16]
    return f"dic_doc_{h}"


def _resolve_context(tenant_id: str | None, classification: str | None) -> tuple[str, str]:
    """Resolve tenant_id/classification: explicit args > Flask security ctx > defaults."""
    tid, cls = tenant_id, classification
    if tid is None or cls is None:
        try:
            from flask import g, has_request_context

            if has_request_context():
                ctx = getattr(g, "security_context", None)
                if ctx is not None:
                    tid = tid or getattr(ctx, "tenant_id", None)
                    cls = cls or getattr(ctx, "classification", None)
        except Exception:
            pass
    return (tid or "default"), (cls or "UNCLASSIFIED")


@dataclass
class IngestOutcome:
    doc_id: str
    version_id: str
    collection_id: str
    source_id: str
    provider: str
    chunks: int
    chunks_embedded: int
    kg_entities: int
    kg_relationships: int
    tenant_id: str
    classification: str
    summary: str = ""
    ocr_cleaned: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "version_id": self.version_id,
            "collection_id": self.collection_id,
            "source_id": self.source_id,
            "provider": self.provider,
            "chunks": self.chunks,
            "chunks_embedded": self.chunks_embedded,
            "kg_entities": self.kg_entities,
            "kg_relationships": self.kg_relationships,
            "tenant_id": self.tenant_id,
            "classification": self.classification,
            "summary": self.summary,
            "ocr_cleaned": self.ocr_cleaned,
            "metadata": self.metadata,
            "errors": self.errors,
        }


# --------------------------------------------------------------------------- #
# Embedding + vector-store upsert (mirrors ingestion_manager.ingest_source path)
# --------------------------------------------------------------------------- #

def _embed_and_store(chunks: list, tenant_id: str, errors: list[str], progress_cb=None) -> int:
    """Embed new chunks and upsert into the vector store. Returns count embedded.

    progress_cb: optional callable(embedded: int, total: int) called after each chunk.
    """
    try:
        from tools.llm import get_embedding_provider
        from tools.rag.vector_store_factory import VectorStoreFactory
    except Exception as e:
        errors.append(f"embed deps unavailable: {e}")
        return 0

    provider = get_embedding_provider()
    if not provider:
        errors.append("no embedding provider available")
        return 0

    try:
        store = VectorStoreFactory.create(tenant_id=tenant_id)
    except Exception as e:
        errors.append(f"vector store unavailable: {e}")
        return 0

    # Dedup against existing content hashes.
    new_chunks = []
    for c in chunks:
        try:
            if store.get_by_content_hash(getattr(c, "content_hash", "")):
                continue
        except Exception:
            pass
        new_chunks.append(c)

    total = len(new_chunks)
    embedded = 0
    for c in new_chunks:
        try:
            if hasattr(provider, "embed"):
                c.embedding = provider.embed(c.content)
            else:
                resp = provider.embeddings.create(
                    input=c.content, model="nomic-embed-text"
                )
                c.embedding = resp.data[0].embedding
            embedded += 1
            if progress_cb:
                try:
                    progress_cb(embedded, total)
                except Exception:
                    pass
        except Exception as e:
            errors.append(f"embed chunk failed: {e}")

    embeddable = [c for c in new_chunks if getattr(c, "embedding", None) is not None]
    if embeddable:
        try:
            store.upsert(embeddable)
        except Exception as e:
            errors.append(f"vector upsert failed: {e}")
    return embedded


# --------------------------------------------------------------------------- #
# LLM document summarization (aiify-opp-6098: document_ingestion_pipeline ->
# llm_generation). During ingestion the extractor often yields no embedded
# title, so the document is stored under its bare filename stem. This optional
# helper distils the extracted text into a descriptive title + short abstract,
# grounded only in the document's own content so the model cannot invent facts.
# --------------------------------------------------------------------------- #

# Cap the text handed to the model so a large document stays within a cheap,
# fast summarization budget. The lede of a document carries the title/abstract
# signal, so the leading slice is sufficient and deterministic.
_SUMMARY_INPUT_CHARS = 6000

_DOC_SUMMARY_SYSTEM_PROMPT = (
    "You summarize a single ingested document for a document-management index. "
    "Use ONLY the provided text — never invent facts, names, or numbers not "
    "present in it. Respond with a strict JSON object and nothing else, of the "
    'form {"title": "<=12 word descriptive title", "summary": "1-3 sentence '
    'abstract"}. If the text is too short or empty to summarize, return '
    '{"title": "", "summary": ""}.'
)


def _ai_document_summary(text: str, filename: str, page_count: int) -> dict | None:
    """Distil extracted document text into a title + abstract via the LLM.

    Args:
        text: The full extracted document text. Only the leading
            ``_SUMMARY_INPUT_CHARS`` characters are sent to the model, keeping
            the call cheap and bounded regardless of document size.
        filename: Source filename, passed as weak context only (the model is
            told to ground on the text, not the name).
        page_count: Extracted page count, included as a grounding fact.

    Returns:
        ``{"title": str, "summary": str}`` on success, or ``None`` when
        summarization is unavailable, the text is empty, or anything fails.
        Callers MUST treat ``None`` as "no summary" and proceed with ingestion
        unchanged — this is a best-effort enrichment, never a hard dependency.
    """
    snippet = (text or "").strip()
    if not snippet:
        return None
    snippet = snippet[:_SUMMARY_INPUT_CHARS]
    try:
        import json as _json

        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        req = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Filename: {filename}\n"
                        f"Page count: {page_count}\n"
                        "Document text (leading excerpt):\n"
                        f"{snippet}\n\n"
                        "Produce the title and summary JSON."
                    ),
                }
            ],
            system_prompt=_DOC_SUMMARY_SYSTEM_PROMPT,
            max_tokens=256,
            temperature=0.2,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("summarization", req)
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
        title = str(parsed.get("title") or "").strip()
        summary = str(parsed.get("summary") or "").strip()
        if not title and not summary:
            return None
        return {"title": title, "summary": summary}
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# LLM OCR cleanup (aiify-opp-6118: ocr_extraction_pipeline -> llm_generation).
# Text recovered from a scanned page by the OCR fallback (easyocr / vision-LLM /
# pytesseract) is noisy: words split by line-break hyphenation, mis-segmented
# tokens, garbled characters, and broken paragraph structure. Before that text
# is chunked + embedded, this optional helper asks the LLM to *correct* the OCR
# artifacts — grounded ONLY on the OCR text, never adding, removing, or
# inventing content. A length-ratio guard rejects any result that drifts too far
# from the original so the model cannot silently drop or hallucinate content.
# Only fires for genuinely-OCR'd providers — clean digital text is left untouched.
# --------------------------------------------------------------------------- #

# Providers whose text came from OCR (noisy) rather than a digital text layer.
_OCR_PROVIDERS = {"pypdf+ocr", "ocr"}

# Cleanup is a single bounded LLM call that must round-trip the whole text. To
# keep it cheap and avoid truncating real content, skip cleanup above this size
# (large OCR docs are left as-is rather than partially corrected).
_OCR_CLEANUP_MAX_CHARS = 8000

# The corrected text must stay within this fraction of the original length in
# both directions; outside the band we assume the model dropped or invented
# content and discard the result, keeping the raw OCR text.
_OCR_CLEANUP_MIN_RATIO = 0.5
_OCR_CLEANUP_MAX_RATIO = 2.0

_OCR_CLEANUP_SYSTEM_PROMPT = (
    "You are correcting raw OCR output from a scanned document page. Fix only "
    "mechanical OCR errors: rejoin words split by line-break hyphenation, merge "
    "tokens that were wrongly broken apart, repair obviously garbled characters, "
    "and restore natural paragraph and line spacing. Do NOT add, remove, "
    "summarize, translate, reorder, or invent any content. Preserve every fact, "
    "number, name, and the original language exactly. Return ONLY the corrected "
    "text, with no commentary, preamble, or code fences."
)


def _ai_ocr_cleanup(text: str) -> str | None:
    """Correct OCR artifacts in ``text`` via the LLM, grounded on the text alone.

    Args:
        text: Raw text emitted by an OCR extractor. Only invoked by the
            orchestrator when the extraction provider is OCR-based and the text
            is at most ``_OCR_CLEANUP_MAX_CHARS`` characters.

    Returns:
        The corrected text on success, or ``None`` when cleanup is unavailable,
        the text is empty/too large, the model returns nothing, or the result
        fails the length-ratio grounding guard. Callers MUST treat ``None`` as
        "no cleanup" and proceed with the raw OCR text — this is a best-effort
        enrichment, never a hard dependency.
    """
    snippet = (text or "").strip()
    if not snippet:
        return None
    # Bound cost: never truncate a large document — skip cleanup instead.
    if len(snippet) > _OCR_CLEANUP_MAX_CHARS:
        return None
    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        req = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Correct the OCR errors in the following text:\n\n"
                        f"{snippet}"
                    ),
                }
            ],
            system_prompt=_OCR_CLEANUP_SYSTEM_PROMPT,
            max_tokens=4096,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("summarization", req)
        if not resp or not resp.content:
            return None
        cleaned = resp.content.strip()
        # Strip a stray fenced block if the model wrapped the text anyway.
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
        if not cleaned:
            return None
        # Grounding guard: a faithful correction stays close to the original
        # length. Reject runaway expansion or content drop.
        ratio = len(cleaned) / max(len(snippet), 1)
        if ratio < _OCR_CLEANUP_MIN_RATIO or ratio > _OCR_CLEANUP_MAX_RATIO:
            return None
        return cleaned
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# LLM metadata extraction (aiify-opp-6086: metadata_extraction ->
# llm_generation). The external scan flagged paperless-ngx
# src/documents/views.py:1135-1183 — manual metadata parsing/assignment — and
# recommended replacing it with an NLP entity extractor. The repo is ephemeral,
# so per the established aiify-opp pattern the augmentation lands in the
# analogous ICDEV subsystem (DIC). During ingestion DIC derives only a title
# (extractor / aiify-opp-6098 summary / filename stem); the document_type, topic
# tags, and document date are otherwise unset. This optional helper proposes
# those structured fields from the document's own text.
#
# Grounding + safety (mirrors the 6086 design doc & ICDEV AI-security posture):
#   - document_type is constrained to a fixed enum — values outside it are
#     dropped, so the model cannot invent a free-text type.
#   - tags are derived from the text, lower-cased, de-duplicated, length- and
#     count-capped.
#   - the date must be a real ISO (YYYY-MM-DD) calendar date or it is dropped.
#   - a single confidence score gates the whole suggestion: below
#     _METADATA_MIN_CONFIDENCE the result is discarded (HITL / manual fallback).
#   - the result is surfaced as a *proposal* on IngestOutcome.metadata — never
#     silently written to dic_documents — so a human confirms before it sticks.
# --------------------------------------------------------------------------- #

# Document types DIC recognizes. Constrains the model to a closed set so it
# cannot emit a hallucinated free-text type; anything else collapses to "other".
_METADATA_DOC_TYPES = (
    "policy", "procedure", "report", "contract", "memo", "specification",
    "manual", "correspondence", "form", "presentation", "plan", "other",
)

# Only the leading slice carries the type/date/topic signal; keep the call cheap
# and bounded regardless of document size.
_METADATA_INPUT_CHARS = 6000

# Below this confidence the whole suggestion is dropped and the fields stay
# unset for the deterministic / human path (HITL).
_METADATA_MIN_CONFIDENCE = 0.70

_METADATA_MAX_TAGS = 8
_METADATA_TAG_MAX_LEN = 40

_METADATA_SYSTEM_PROMPT = (
    "You extract structured metadata for a single ingested document, for a "
    "document-management index. Use ONLY the provided text — never invent "
    "facts, dates, or topics not present in it. Respond with a strict JSON "
    "object and nothing else, of the form "
    '{"document_type": "<one of: '
    + ", ".join(_METADATA_DOC_TYPES)
    + '>", "tags": ["<=8 short lower-case topic keywords drawn from the text"], '
    '"date": "<the document\'s own date as YYYY-MM-DD, or null>", '
    '"confidence": <0..1 overall confidence>}. '
    'Use "other" for document_type when none fits. Set date to null unless an '
    "explicit document date appears in the text. Return low confidence rather "
    "than guessing."
)


def _ai_metadata_extraction(text: str, filename: str) -> dict | None:
    """Propose structured document metadata from ``text`` via the LLM.

    Args:
        text: The full extracted document text. Only the leading
            ``_METADATA_INPUT_CHARS`` characters are sent to the model, keeping
            the call cheap and bounded regardless of document size.
        filename: Source filename, passed as weak context only (the model is
            told to ground on the text, not the name).

    Returns:
        ``{"document_type": str, "tags": list[str], "date": str|None,
        "confidence": float}`` on success, or ``None`` when extraction is
        unavailable, the text is empty, the model output is unusable, or the
        overall confidence is below ``_METADATA_MIN_CONFIDENCE``. Callers MUST
        treat ``None`` as "no metadata" and proceed with ingestion unchanged —
        this is a best-effort, HITL proposal, never a hard dependency, and is
        never silently persisted.
    """
    snippet = (text or "").strip()
    if not snippet:
        return None
    snippet = snippet[:_METADATA_INPUT_CHARS]
    try:
        import json as _json

        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        req = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Filename: {filename}\n"
                        "Document text (leading excerpt):\n"
                        f"{snippet}\n\n"
                        "Produce the metadata JSON."
                    ),
                }
            ],
            system_prompt=_METADATA_SYSTEM_PROMPT,
            max_tokens=256,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("summarization", req)
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

        # Confidence gate: drop the whole suggestion below threshold (HITL).
        try:
            confidence = float(parsed.get("confidence"))
        except (TypeError, ValueError):
            return None
        if confidence < _METADATA_MIN_CONFIDENCE:
            return None

        # document_type: constrain to the closed enum.
        doc_type = str(parsed.get("document_type") or "").strip().lower()
        if doc_type not in _METADATA_DOC_TYPES:
            doc_type = "other"

        # tags: derive from text, normalize, de-dupe, length- and count-cap.
        tags: list[str] = []
        seen: set[str] = set()
        for t in parsed.get("tags") or []:
            tag = str(t).strip().lower()[:_METADATA_TAG_MAX_LEN]
            if tag and tag not in seen:
                seen.add(tag)
                tags.append(tag)
            if len(tags) >= _METADATA_MAX_TAGS:
                break

        # date: keep only a real ISO calendar date.
        date_val = parsed.get("date")
        date_str: str | None = None
        if isinstance(date_val, str) and date_val.strip():
            candidate = date_val.strip()
            try:
                datetime.strptime(candidate, "%Y-%m-%d")
                date_str = candidate
            except ValueError:
                date_str = None

        return {
            "document_type": doc_type,
            "tags": tags,
            "date": date_str,
            "confidence": round(confidence, 4),
        }
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# LLM identifier extraction (aiify-opp-5988: ocr_extraction_pipeline ->
# llm_generation). The external scan flagged paperless-ngx
# src/documents/barcodes.py — the barcode/QR reader that scans a page image for
# machine-readable codes to (a) split a multi-page scan at separator barcodes
# and (b) assign an Archive Serial Number (ASN) from a barcode value. The repo
# is ephemeral, so per the established aiify-opp pattern the augmentation lands
# in the analogous ICDEV subsystem (DIC). Many ingested documents carry no
# physical barcode, yet the same identifiers a barcode would encode (ASN,
# invoice/contract/PO/reference numbers, control/tracking/case/serial numbers)
# are printed in the text. This optional helper extracts those structured
# identifiers from the document's own text — the LLM-generation analog of the
# barcode reader.
#
# Grounding + safety (mirrors the 6086/6098/6118 designs & ICDEV AI-security
# posture):
#   - kind is constrained to a fixed enum — values outside it are dropped, so
#     the model cannot invent a free-text identifier class.
#   - value must match a compact identifier shape (alphanumerics + - / . #),
#     never free prose, and its alphanumeric core MUST literally appear in the
#     source text — a hard anti-hallucination guard so the model can only
#     surface identifiers actually printed on the document.
#   - a per-item confidence gates each identifier; the whole call is dropped
#     below _IDENTIFIER_MIN_CONFIDENCE overall.
#   - results are surfaced as a *proposal* under IngestOutcome.metadata —
#     never silently written to dic_documents — so a human confirms (HITL).
# --------------------------------------------------------------------------- #

# Identifier classes DIC recognizes — the kinds of machine-readable codes a
# barcode/label would carry. Constrains the model to a closed set so it cannot
# emit a hallucinated free-text class; anything else is dropped.
_IDENTIFIER_KINDS = (
    "asn", "invoice_number", "contract_number", "po_number",
    "reference_number", "document_number", "tracking_number",
    "control_number", "case_number", "serial_number",
)

# Only the leading slice carries the identifier signal (codes are printed in
# headers/footers/cover pages); keep the call cheap and bounded.
_IDENTIFIER_INPUT_CHARS = 6000

# Below this overall confidence the whole suggestion is dropped (HITL fallback).
_IDENTIFIER_MIN_CONFIDENCE = 0.70

_IDENTIFIER_MAX_ITEMS = 8
_IDENTIFIER_VALUE_MAX_LEN = 64

# A barcode-style identifier is a compact alphanumeric token (letters, digits
# and the common separators - / . #), never a sentence. Reject anything else so
# the model cannot smuggle prose into an "identifier" field.
_IDENTIFIER_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-/.#]{0,62}[A-Za-z0-9]$")

_IDENTIFIER_SYSTEM_PROMPT = (
    "You extract structured document identifiers for a single ingested "
    "document, for a document-management index. These are the machine-readable "
    "codes a barcode or label would carry — e.g. an Archive Serial Number, "
    "invoice/contract/purchase-order/reference/document/tracking/control/case/"
    "serial numbers. Use ONLY the provided text — never invent, complete, or "
    "guess a code not printed verbatim in it. Respond with a strict JSON object "
    "and nothing else, of the form "
    '{"identifiers": [{"kind": "<one of: '
    + ", ".join(_IDENTIFIER_KINDS)
    + '>", "value": "<the code exactly as printed>", "confidence": <0..1>}], '
    '"confidence": <0..1 overall confidence>}. '
    "Each value must be a compact code copied character-for-character from the "
    "text, not a description. Return an empty identifiers list and low overall "
    "confidence rather than guessing."
)


def _ai_extract_identifiers(text: str) -> list[dict] | None:
    """Extract structured document identifiers from ``text`` via the LLM.

    The LLM-generation analog of paperless' barcode reader (aiify-opp-5988):
    surfaces the codes a barcode would carry (ASN, invoice/contract/PO/etc.
    numbers) when they are printed in the document text rather than encoded in a
    physical barcode.

    Args:
        text: The full extracted document text. Only the leading
            ``_IDENTIFIER_INPUT_CHARS`` characters are sent to the model,
            keeping the call cheap and bounded regardless of document size.

    Returns:
        A list of ``{"kind": str, "value": str, "confidence": float}`` items on
        success, or ``None`` when extraction is unavailable, the text is empty,
        the model output is unusable, the overall confidence is below
        ``_IDENTIFIER_MIN_CONFIDENCE``, or nothing survives the grounding
        guards. Callers MUST treat ``None``/empty as "no identifiers" and
        proceed with ingestion unchanged — this is a best-effort, HITL proposal,
        never a hard dependency, and is never silently persisted.
    """
    snippet = (text or "").strip()
    if not snippet:
        return None
    snippet = snippet[:_IDENTIFIER_INPUT_CHARS]
    # Alphanumeric-only, case-folded form of the source used for the membership
    # guard so an identifier still matches its printed core regardless of stray
    # OCR spacing or differing separators (- / . #).
    haystack = "".join(ch for ch in snippet if ch.isalnum()).lower()
    try:
        import json as _json

        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        req = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Document text (leading excerpt):\n"
                        f"{snippet}\n\n"
                        "Produce the identifiers JSON."
                    ),
                }
            ],
            system_prompt=_IDENTIFIER_SYSTEM_PROMPT,
            max_tokens=384,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("summarization", req)
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

        # Confidence gate: drop the whole suggestion below threshold (HITL).
        try:
            overall = float(parsed.get("confidence"))
        except (TypeError, ValueError):
            return None
        if overall < _IDENTIFIER_MIN_CONFIDENCE:
            return None

        identifiers: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for item in parsed.get("identifiers") or []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip().lower()
            if kind not in _IDENTIFIER_KINDS:
                continue
            value = str(item.get("value") or "").strip()
            if not value or len(value) > _IDENTIFIER_VALUE_MAX_LEN:
                continue
            # Shape guard: must be a compact code, not prose.
            if not _IDENTIFIER_VALUE_RE.match(value):
                continue
            # Grounding guard: the code's alphanumeric core must literally
            # appear in the source text (anti-hallucination).
            core = "".join(ch for ch in value if ch.isalnum()).lower()
            if not core or core not in haystack:
                continue
            # Per-item confidence gate.
            try:
                item_conf = float(item.get("confidence"))
            except (TypeError, ValueError):
                item_conf = overall
            if item_conf < _IDENTIFIER_MIN_CONFIDENCE:
                continue
            key = (kind, value.lower())
            if key in seen:
                continue
            seen.add(key)
            identifiers.append(
                {"kind": kind, "value": value, "confidence": round(item_conf, 4)}
            )
            if len(identifiers) >= _IDENTIFIER_MAX_ITEMS:
                break

        return identifiers or None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# LLM taxonomy classification (aiify-opp-6043: manual_classification_ui ->
# llm_generation). The external scan flagged paperless-ngx
# src/documents/models.py — the Correspondent / DocumentType / Tag models and
# their ``matching_algorithm`` (ANY / ALL / LITERAL / REGEX / FUZZY / AUTO) +
# ``match`` fields. Those back the manual-classification UI: a user hand-curates
# a taxonomy of labels and writes per-label matching rules so new documents are
# filed under an *existing* category. The repo is ephemeral, so per the
# established aiify-opp pattern the augmentation lands in the analogous ICDEV
# subsystem (DIC).
#
# This is deliberately distinct from the open-vocabulary metadata extraction in
# aiify-opp-6086: that proposes *new* metadata (a document_type from a fixed
# module-level enum, free topic tags, a date). This helper instead models the
# ``matching_algorithm = AUTO`` behaviour — soft classification of a document
# into a *caller-supplied, user-curated taxonomy of existing labels*. The model
# may only SELECT from the labels it is given; it can never invent one, and it
# returns "unmatched" when nothing fits (the manual-filing fallback). It is the
# LLM-generation replacement for hand-written per-label match rules.
#
# Grounding + safety (mirrors the 6086/5988 design & ICDEV AI-security posture):
#   - the candidate labels are passed verbatim and the result is intersected
#     back against that exact set — any label not offered is dropped, so the
#     model cannot fabricate a category.
#   - single-label mode keeps only the top selection; multi-label mode (the Tag
#     analog) is de-duplicated and count-capped.
#   - a confidence score gates the whole suggestion: below
#     _CLASSIFY_MIN_CONFIDENCE it is discarded (HITL / manual fallback), as is an
#     explicit "unmatched"/empty selection.
#   - only the leading _CLASSIFY_INPUT_CHARS of text are sent (cheap, bounded).
#   - the result is surfaced as a *proposal* under IngestOutcome.metadata
#     ["classification"] — never silently written to dic_documents — so a human
#     confirms the filing before it sticks.
#   - any failure / unavailability degrades to None (air-gap safe); the caller
#     proceeds with ingestion unchanged.
# --------------------------------------------------------------------------- #

# Only the leading slice carries the classification signal; keep the call cheap
# and bounded regardless of document size.
_CLASSIFY_INPUT_CHARS = 6000

# Below this confidence the whole suggestion is dropped for the manual path.
_CLASSIFY_MIN_CONFIDENCE = 0.70

# Bound how large a taxonomy we will offer the model, and how many labels a
# multi-label classification may return.
_CLASSIFY_MAX_LABELS = 60
_CLASSIFY_LABEL_MAX_LEN = 80
_CLASSIFY_MAX_SELECTED = 5

# Sentinel the model is told to use when no candidate label fits.
_CLASSIFY_UNMATCHED = "unmatched"

_CLASSIFY_SYSTEM_PROMPT = (
    "You file a single ingested document into an existing, user-curated "
    "taxonomy for a document-management index. You are given a fixed list of "
    "candidate labels and the document text. Choose ONLY from the candidate "
    "labels exactly as written — never invent, rename, merge, or split a label. "
    "Ground every choice in the provided text; do not guess from the filename. "
    f'If no candidate label fits, return "{_CLASSIFY_UNMATCHED}". Respond with a '
    "strict JSON object and nothing else, of the form "
    '{"labels": ["<labels chosen verbatim from the candidates, or '
    f'{_CLASSIFY_UNMATCHED}>"], "confidence": <0..1 overall confidence>}}. '
    "Return low confidence rather than forcing a poor match."
)


def _normalize_taxonomy(taxonomy) -> list[str]:
    """Clean a caller-supplied taxonomy: trim, drop blanks, length-cap, de-dupe.

    Order is preserved (first occurrence wins) and the list is bounded to
    ``_CLASSIFY_MAX_LABELS`` so an unbounded taxonomy can never blow up the
    prompt. Returns an empty list when nothing usable remains.
    """
    labels: list[str] = []
    seen: set[str] = set()
    for raw in taxonomy or []:
        label = str(raw).strip()
        if not label or len(label) > _CLASSIFY_LABEL_MAX_LEN:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
        if len(labels) >= _CLASSIFY_MAX_LABELS:
            break
    return labels


def _ai_classify_into_taxonomy(
    text: str, taxonomy, *, multi_label: bool = False, filename: str = ""
) -> dict | None:
    """Classify a document into a caller-supplied taxonomy of existing labels.

    The LLM-generation analog of paperless ``matching_algorithm = AUTO``: rather
    than hand-written per-label match rules, the model softly files the document
    under one (or, in ``multi_label`` mode, several) of the *existing* labels the
    caller passes in. It can only SELECT from those labels — never invent one —
    and returns ``None`` when nothing fits or confidence is too low, leaving the
    document for the manual / HITL path.

    Args:
        text: full extracted document text; only the leading
            ``_CLASSIFY_INPUT_CHARS`` are sent to the model (cheap, bounded).
        taxonomy: iterable of candidate label strings (the user's curated
            categories — e.g. existing correspondents / document types / tags).
            Normalized, de-duplicated and bounded before use.
        multi_label: when False (default) keep only the single best label (the
            Correspondent / DocumentType analog); when True keep a de-duplicated,
            count-capped set (the Tag analog).
        filename: weak context only; the model is told to ground on the text.

    Returns:
        ``{"labels": list[str], "confidence": float}`` where every entry of
        ``labels`` is drawn verbatim from ``taxonomy``, or ``None`` when there is
        no usable taxonomy, empty text, unusable model output, an "unmatched"
        result, or confidence below ``_CLASSIFY_MIN_CONFIDENCE``. Callers MUST
        treat ``None`` as "no classification" and proceed unchanged — this is a
        best-effort HITL proposal, never persisted automatically.
    """
    snippet = (text or "").strip()
    if not snippet:
        return None
    labels = _normalize_taxonomy(taxonomy)
    if not labels:
        return None
    snippet = snippet[:_CLASSIFY_INPUT_CHARS]
    try:
        import json as _json

        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        candidate_block = "\n".join(f"- {label}" for label in labels)
        req = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Filename: {filename}\n"
                        "Candidate labels (choose only from these, verbatim):\n"
                        f"{candidate_block}\n\n"
                        "Document text (leading excerpt):\n"
                        f"{snippet}\n\n"
                        "Produce the classification JSON."
                    ),
                }
            ],
            system_prompt=_CLASSIFY_SYSTEM_PROMPT,
            max_tokens=256,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("summarization", req)
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

        # Confidence gate: drop the whole suggestion below threshold (HITL).
        try:
            confidence = float(parsed.get("confidence"))
        except (TypeError, ValueError):
            return None
        if confidence < _CLASSIFY_MIN_CONFIDENCE:
            return None

        # Membership guard: intersect the model's picks back against the exact
        # taxonomy (case-insensitive), restoring the caller's canonical casing.
        # Anything not offered — including the "unmatched" sentinel — is dropped,
        # so the model can never fabricate a category.
        canonical = {label.casefold(): label for label in labels}
        selected: list[str] = []
        seen: set[str] = set()
        for item in parsed.get("labels") or []:
            key = str(item).strip().casefold()
            if not key or key == _CLASSIFY_UNMATCHED or key not in canonical:
                continue
            if key in seen:
                continue
            seen.add(key)
            selected.append(canonical[key])
            if not multi_label or len(selected) >= _CLASSIFY_MAX_SELECTED:
                break

        # An empty / "unmatched" selection is the manual-filing fallback.
        if not selected:
            return None

        return {"labels": selected, "confidence": round(confidence, 4)}
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# LLM correspondence extraction (aiify-opp-6100: regex_user_input ->
# nlp_extractor). The external scan flagged paperless-ngx
# src/paperless_mail/.../mail.py — the MailDocumentParser, which parses an email
# (.eml/.msg) by pulling its header/envelope fields (From, To/Cc, Subject, sent
# date) out of user-controlled message text. The recommended paradigm is to
# replace that brittle regex/header parsing with an NLP entity extractor. The
# repo is ephemeral, so per the established aiify-opp pattern the augmentation
# lands in the analogous ICDEV subsystem (DIC). When an ingested document is an
# email / piece of correspondence, its participants and envelope fields are only
# present as in-body text — DIC otherwise derives just a title/summary. This
# optional helper proposes those structured correspondence fields from the
# document's own text — the NLP-extractor analog of the email header parser.
#
# This is deliberately distinct from the open-vocabulary metadata extraction
# (6086: document_type / tags / date) and the identifier extraction (5988:
# barcode-style codes): it models the *email envelope* specifically — sender,
# recipients, subject, sent date.
#
# Grounding + safety (mirrors the 6086/5988/6043 designs & ICDEV AI-security
# posture):
#   - email addresses must match a compact e-mail shape AND their local/core
#     characters must literally appear in the source text — a hard
#     anti-hallucination guard, identical in spirit to the identifier core
#     membership check, so the model can only surface addresses actually printed
#     in the message.
#   - party display names and the subject are length-capped and must have an
#     alphanumeric core that literally appears in the text, so the model cannot
#     invent a participant or a subject line that is not in the message.
#   - recipients are de-duplicated and count-capped; the sent date must be a real
#     ISO (YYYY-MM-DD) calendar date or it is dropped.
#   - a single confidence score gates the whole suggestion: below
#     _CORRESPONDENCE_MIN_CONFIDENCE it is discarded (HITL / manual fallback).
#   - the result is surfaced as a *proposal* under IngestOutcome.metadata
#     ["correspondence"] — never silently written to dic_documents — so a human
#     confirms before it sticks. Any failure degrades to None (air-gap safe).
# --------------------------------------------------------------------------- #

# Only the leading slice carries the envelope signal (From/To/Subject/Date sit at
# the top of an email); keep the call cheap and bounded regardless of size.
_CORRESPONDENCE_INPUT_CHARS = 6000

# Below this confidence the whole suggestion is dropped for the HITL / manual path.
_CORRESPONDENCE_MIN_CONFIDENCE = 0.70

# Bound recipient fan-out and the length of any single party / subject value.
_CORRESPONDENCE_MAX_RECIPIENTS = 25
_CORRESPONDENCE_NAME_MAX_LEN = 120
_CORRESPONDENCE_SUBJECT_MAX_LEN = 300

# Compact RFC-5322-ish e-mail shape. Not a validator — a guard that the value is
# an address rather than free prose before the membership check runs.
_CORRESPONDENCE_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
)

_CORRESPONDENCE_SYSTEM_PROMPT = (
    "You extract the envelope fields of a single email / piece of correspondence "
    "for a document-management index. Use ONLY the provided text — never invent a "
    "sender, recipient, subject, address, or date not present in it. Respond with "
    "a strict JSON object and nothing else, of the form "
    '{"from_name": "<sender display name or \"\">", '
    '"from_email": "<sender email address or \"\">", '
    '"to": [{"name": "<recipient display name or \"\">", '
    '"email": "<recipient email address or \"\">"}], '
    '"subject": "<the email subject line, or \"\">", '
    '"sent_date": "<the message date as YYYY-MM-DD, or null>", '
    '"confidence": <0..1 overall confidence>}. '
    "Include Cc recipients in the to list. Leave a field empty rather than "
    "guessing, and return low confidence when the text is not actually an email."
)


def _ground_token(value: str, haystack: str, max_len: int) -> str:
    """Trim/length-cap ``value`` and keep it only if grounded in ``haystack``.

    Returns the cleaned value when its alphanumeric, case-folded core literally
    appears in ``haystack`` (the same membership guard the identifier extractor
    uses), else "" — so the model can never surface a name/subject that is not in
    the source text. ``haystack`` must already be the alnum-only, lower-cased form
    of the document text.
    """
    cleaned = (value or "").strip()[:max_len]
    if not cleaned:
        return ""
    core = "".join(ch for ch in cleaned if ch.isalnum()).lower()
    if not core or core not in haystack:
        return ""
    return cleaned


def _ai_extract_correspondence(text: str) -> dict | None:
    """Extract structured email envelope fields from ``text`` via the LLM.

    The NLP-extractor analog of paperless' email header parser (aiify-opp-6100):
    surfaces the sender, recipients, subject, and sent date of a piece of
    correspondence from its own text, grounded so the model can only report
    participants/subjects actually printed in the message.

    Args:
        text: The full extracted document text. Only the leading
            ``_CORRESPONDENCE_INPUT_CHARS`` characters are sent to the model,
            keeping the call cheap and bounded regardless of document size.

    Returns:
        ``{"from_name": str, "from_email": str, "to": list[{"name": str,
        "email": str}], "subject": str, "sent_date": str|None, "confidence":
        float}`` on success, or ``None`` when extraction is unavailable, the text
        is empty, the model output is unusable, the overall confidence is below
        ``_CORRESPONDENCE_MIN_CONFIDENCE``, or nothing survives the grounding
        guards (no sender, recipients, or subject left). Callers MUST treat
        ``None`` as "no correspondence fields" and proceed with ingestion
        unchanged — this is a best-effort, HITL proposal, never a hard
        dependency, and is never silently persisted.
    """
    snippet = (text or "").strip()
    if not snippet:
        return None
    snippet = snippet[:_CORRESPONDENCE_INPUT_CHARS]
    # Alphanumeric-only, case-folded form of the source for the membership guard
    # (tolerates stray spacing / punctuation differences between header and body).
    haystack = "".join(ch for ch in snippet if ch.isalnum()).lower()
    try:
        import json as _json

        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        req = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Document text (leading excerpt):\n"
                        f"{snippet}\n\n"
                        "Produce the correspondence JSON."
                    ),
                }
            ],
            system_prompt=_CORRESPONDENCE_SYSTEM_PROMPT,
            max_tokens=512,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("summarization", req)
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

        # Confidence gate: drop the whole suggestion below threshold (HITL).
        try:
            confidence = float(parsed.get("confidence"))
        except (TypeError, ValueError):
            return None
        if confidence < _CORRESPONDENCE_MIN_CONFIDENCE:
            return None

        def _email(value: str) -> str:
            """Keep an address only if it is e-mail-shaped AND grounded in text."""
            addr = (value or "").strip()
            if not addr or len(addr) > _CORRESPONDENCE_NAME_MAX_LEN:
                return ""
            if not _CORRESPONDENCE_EMAIL_RE.match(addr):
                return ""
            # Grounding: the address's alphanumeric core must appear in the text.
            core = "".join(ch for ch in addr if ch.isalnum()).lower()
            return addr if core and core in haystack else ""

        from_name = _ground_token(
            parsed.get("from_name"), haystack, _CORRESPONDENCE_NAME_MAX_LEN
        )
        from_email = _email(parsed.get("from_email"))

        recipients: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for item in parsed.get("to") or []:
            if not isinstance(item, dict):
                continue
            name = _ground_token(
                item.get("name"), haystack, _CORRESPONDENCE_NAME_MAX_LEN
            )
            email = _email(item.get("email"))
            # Drop a recipient that has neither a grounded name nor a grounded
            # address — it would be an ungrounded hallucination.
            if not name and not email:
                continue
            key = (name.lower(), email.lower())
            if key in seen:
                continue
            seen.add(key)
            recipients.append({"name": name, "email": email})
            if len(recipients) >= _CORRESPONDENCE_MAX_RECIPIENTS:
                break

        subject = _ground_token(
            parsed.get("subject"), haystack, _CORRESPONDENCE_SUBJECT_MAX_LEN
        )

        # sent_date: keep only a real ISO calendar date.
        date_val = parsed.get("sent_date")
        sent_date: str | None = None
        if isinstance(date_val, str) and date_val.strip():
            candidate = date_val.strip()
            try:
                datetime.strptime(candidate, "%Y-%m-%d")
                sent_date = candidate
            except ValueError:
                sent_date = None

        # Require at least one grounded envelope signal — otherwise this was not a
        # piece of correspondence (or nothing survived grounding): manual path.
        if not (from_name or from_email or recipients or subject):
            return None

        return {
            "from_name": from_name,
            "from_email": from_email,
            "to": recipients,
            "subject": subject,
            "sent_date": sent_date,
            "confidence": round(confidence, 4),
        }
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Date-parsing anomaly detection (aiify-opp-6048: hardcoded_threshold ->
# anomaly_detection). The external scan flagged paperless-ngx
# src/documents/plugins/date_parsing/__init__.py — the consumer plugin that
# parses dates out of a document's OCR text — and recommended an
# anomaly-detection paradigm over its fixed parsing rules. The repo is
# ephemeral, so per the established aiify-opp pattern the augmentation lands in
# the analogous ICDEV subsystem (DIC). DIC's _ai_metadata_extraction
# (aiify-opp-6086) already proposes a *single* document date; this complementary
# layer parses *every* candidate date the text contains and flags the
# anomalous ones — a date in the future, one implausibly far in the past, or a
# statistical outlier relative to the document's own cluster of dates (an OCR
# typo that turned 2023 into 2093, a mis-scanned year, a back-dated insert).
#
# Design (mirrors the freshness-anomaly sibling, aiify-opp-6042):
#   - the deterministic detector is the ALWAYS-authoritative baseline: pure
#     regex parsing + named-threshold / statistical-outlier rules, no network.
#   - the named thresholds below are exactly the "hardcoded_threshold" the scan
#     called out, lifted out of inline literals into one tunable place.
#   - the optional LLM layer only grades *severity* and explains the flagged
#     dates; it degrades silently to the heuristic severity and is never a hard
#     dependency.
#   - results are surfaced as a *proposal* under
#     IngestOutcome.metadata["date_anomalies"] — never silently written — so a
#     human confirms before anything sticks.
# --------------------------------------------------------------------------- #

# A parsed date more than this many days past "now" is treated as future-dated
# (a small tolerance absorbs timezone / clock skew on legitimately current docs).
_DATE_FUTURE_TOLERANCE_DAYS = 1

# Dates before this calendar year are implausible for an ingested business
# document and almost always an OCR/typo artifact (e.g. "1066", "0202").
_DATE_MIN_PLAUSIBLE_YEAR = 1900

# A parsed date more than this many standard deviations from the document's own
# date cluster is flagged as a statistical outlier. Kept at 2.0 in step with the
# freshness-anomaly sibling (_ANOMALY_STDEV_K) so a lone strong outlier — which
# inflates the stdev and can mask itself at higher k — is still caught.
_DATE_ANOMALY_STDEV_K = 2.0

# Need at least this many valid dates before a distribution is meaningful enough
# to call any of them an outlier.
_DATE_ANOMALY_MIN_SAMPLE = 4

# Bound how many flagged dates the optional LLM severity pass is shown.
_DATE_ANOMALY_LLM_SAMPLE = 6

# Only the leading slice of a document is scanned for dates — keeps the pass
# cheap and bounded regardless of document size.
_DATE_INPUT_CHARS = 20000

# Deterministic severity cutoffs on the anomalous fraction of parsed dates.
_DATE_SEV_HIGH_FRACTION = 0.34
_DATE_SEV_MEDIUM_FRACTION = 0.10

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

# Multi-format candidate-date patterns. Each capture group set maps to (y, m, d)
# via its handler below. Deterministic and offline — no third-party dateutil.
_DATE_PATTERNS = (
    # ISO 8601: 2024-03-09
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    # US slashes/dots: 03/09/2024 or 3.9.2024  (month first)
    (re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b"), "mdy"),
    # Long month, day, year: March 9, 2024 / Mar 9 2024
    (re.compile(rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I), "Mdy"),
    # Day, long month, year: 9 March 2024 / 09 Mar 2024
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_ALT})\.?,?\s+(\d{{4}})\b", re.I), "dMy"),
)

_DATE_ANOMALY_SYSTEM_PROMPT = (
    "You are a records-management analyst grading how concerning the dates "
    "parsed from a single document are. You are given every date found in the "
    "document, which ones a deterministic detector flagged as anomalous and "
    "why (future-dated, implausibly old, or a statistical outlier relative to "
    "the document's own dates), and a deterministic baseline severity. Dates "
    "far in the future or that dominate the document are most concerning; a "
    "lone slightly-off outlier is minor. You may agree with or adjust the "
    "baseline, but justify any change. Respond ONLY with a JSON object: "
    '{"severity": "low|medium|high", "rationale": "<=160 chars", '
    '"top_concern": "<the single most concerning date as YYYY-MM-DD>"}. Never '
    "invent dates beyond those provided."
)


def _parse_candidate_dates(text: str) -> list[dict]:
    """Extract every parseable calendar date from ``text`` (deterministic).

    Scans the leading ``_DATE_INPUT_CHARS`` for ISO, US-slash, and long-form
    (month-name) dates. Each hit is validated as a real calendar date — invalid
    combinations (month 13, day 31 in a 30-day month, …) are dropped — and
    de-duplicated by ISO value, keeping the first textual occurrence.

    Returns:
        A list of ``{"iso": "YYYY-MM-DD", "raw": <matched text>, "offset": int}``
        sorted by position in the text. Empty when nothing parses.
    """
    if not text:
        return []
    window = text[:_DATE_INPUT_CHARS]
    seen: set[str] = set()
    out: list[dict] = []
    for pattern, kind in _DATE_PATTERNS:
        for m in pattern.finditer(window):
            try:
                if kind == "ymd":
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                elif kind == "mdy":
                    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                elif kind == "Mdy":
                    mo = _MONTHS[m.group(1).lower()]
                    d, y = int(m.group(2)), int(m.group(3))
                else:  # dMy
                    d = int(m.group(1))
                    mo = _MONTHS[m.group(2).lower()]
                    y = int(m.group(3))
                # Validate as a real calendar date (rejects 2024-02-31 etc.).
                dt = datetime(y, mo, d, tzinfo=timezone.utc)
            except (ValueError, KeyError):
                continue
            iso = dt.strftime("%Y-%m-%d")
            if iso in seen:
                continue
            seen.add(iso)
            out.append({"iso": iso, "raw": m.group(0), "offset": m.start()})
    out.sort(key=lambda r: r["offset"])
    return out


def _heuristic_date_anomaly_severity(anomaly_count: int, total: int) -> str:
    """Deterministic anomaly severity — the always-available baseline.

    Pure function of how large a fraction of the parsed dates are anomalous.
    """
    if total <= 0 or anomaly_count <= 0:
        return "low"
    fraction = anomaly_count / total
    if fraction >= _DATE_SEV_HIGH_FRACTION:
        return "high"
    if fraction >= _DATE_SEV_MEDIUM_FRACTION:
        return "medium"
    return "low"


def _detect_date_anomalies(parsed: list[dict], now_iso: str | None = None) -> dict:
    """Flag anomalous dates among ``parsed`` (deterministic, authoritative).

    Three rules, each lifting a named threshold out of inline magic numbers:
      • future-dated — more than ``_DATE_FUTURE_TOLERANCE_DAYS`` past now;
      • implausibly-old — calendar year before ``_DATE_MIN_PLAUSIBLE_YEAR``;
      • cluster-outlier — more than ``_DATE_ANOMALY_STDEV_K`` standard deviations
        from the mean of the document's own dates (only with at least
        ``_DATE_ANOMALY_MIN_SAMPLE`` dates and a non-degenerate spread).

    Args:
        parsed: output of :func:`_parse_candidate_dates`.
        now_iso: reference "now" as an ISO string; defaults to the current UTC
            time. Injectable so callers/tests are deterministic.

    Returns:
        ``{"total": int, "anomaly_count": int, "anomalies": [...],
           "mean_year": float, "stdev_days": float,
           "baseline_severity": "low|medium|high"}``. ``anomalies`` items carry
        ``{"iso", "raw", "reason"}`` where reason is one of ``future_dated`` /
        ``implausibly_old`` / ``cluster_outlier``.
    """
    total = len(parsed)
    empty = {
        "total": total, "anomaly_count": 0, "anomalies": [],
        "mean_year": 0.0, "stdev_days": 0.0, "baseline_severity": "low",
    }
    if total == 0:
        return empty

    try:
        now = (
            datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
            if now_iso else datetime.now(timezone.utc)
        )
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    except Exception:
        now = datetime.now(timezone.utc)
    future_cutoff = now.timestamp() + _DATE_FUTURE_TOLERANCE_DAYS * 86400.0

    ordinals: list[float] = []
    years: list[int] = []
    for r in parsed:
        dt = datetime.fromisoformat(r["iso"]).replace(tzinfo=timezone.utc)
        r["_ts"] = dt.timestamp()
        r["_year"] = dt.year
        ordinals.append(dt.timestamp())
        years.append(dt.year)

    mean = sum(ordinals) / len(ordinals)
    variance = sum((o - mean) ** 2 for o in ordinals) / len(ordinals)
    stdev = variance ** 0.5

    anomalies: list[dict] = []
    for r in parsed:
        reason: str | None = None
        if r["_ts"] > future_cutoff:
            reason = "future_dated"
        elif r["_year"] < _DATE_MIN_PLAUSIBLE_YEAR:
            reason = "implausibly_old"
        elif (
            total >= _DATE_ANOMALY_MIN_SAMPLE
            and stdev > 0
            and abs(r["_ts"] - mean) > _DATE_ANOMALY_STDEV_K * stdev
        ):
            reason = "cluster_outlier"
        if reason:
            anomalies.append({"iso": r["iso"], "raw": r["raw"], "reason": reason})

    for r in parsed:  # strip scratch fields so the result is JSON-clean
        r.pop("_ts", None)
        r.pop("_year", None)

    return {
        "total": total,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        "mean_year": round(sum(years) / len(years), 1),
        "stdev_days": round(stdev / 86400.0, 1),
        "baseline_severity": _heuristic_date_anomaly_severity(len(anomalies), total),
    }


def _ai_date_anomaly_assessment(summary: dict, anomalies: list[dict]) -> dict | None:
    """Grade parsed-date-anomaly severity with the LLM, grounded on real data.

    Best-effort enrichment only. Returns
    ``{"severity": "low|medium|high", "rationale": str, "top_concern": str}`` on
    success, or ``None`` when there is nothing to grade, the model is
    unavailable, or the output is missing/blank/malformed/out-of-range. Callers
    MUST treat ``None`` as "use the deterministic baseline".
    """
    if not anomalies or summary.get("anomaly_count", 0) <= 0:
        return None
    try:
        import json as _json

        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        lines = [
            f"Document date facts: {_json.dumps(summary, sort_keys=True)}",
            f"Deterministic baseline severity: {summary.get('baseline_severity')}",
            "Flagged dates:",
        ]
        for a in anomalies[:_DATE_ANOMALY_LLM_SAMPLE]:
            lines.append(f"- {_json.dumps(a, default=str)}")

        req = LLMRequest(
            messages=[
                {"role": "user", "content": "\n".join(lines) + "\n\nGrade the severity."}
            ],
            system_prompt=_DATE_ANOMALY_SYSTEM_PROMPT,
            max_tokens=200,
            temperature=0.1,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("dic_date_anomaly_assessment", req)
        if not resp or not resp.content:
            return None
        raw = resp.content.strip()
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
            "top_concern": str(parsed.get("top_concern") or "").strip()[:40],
        }
    except Exception:
        return None


def assess_document_dates(text: str, now_iso: str | None = None) -> dict | None:
    """Parse a document's dates and flag the anomalous ones (HITL proposal).

    Orchestrates :func:`_parse_candidate_dates` + :func:`_detect_date_anomalies`
    (always authoritative) and layers best-effort LLM severity grading on top.

    Args:
        text: the document's extracted text.
        now_iso: injectable reference "now" (ISO string) for deterministic tests.

    Returns:
        ``None`` when no anomalies were found (nothing to surface), else
        ``{"dates": [...all parsed dates...], "anomalies": [...],
           "total": int, "anomaly_count": int, "severity": str,
           "rationale": str, "top_concern": str}``. ``severity`` is the LLM grade
        when available, otherwise the deterministic baseline.
    """
    parsed = _parse_candidate_dates(text or "")
    summary = _detect_date_anomalies(parsed, now_iso=now_iso)
    if summary["anomaly_count"] <= 0:
        return None

    ai = _ai_date_anomaly_assessment(
        {
            "total": summary["total"],
            "anomaly_count": summary["anomaly_count"],
            "mean_year": summary["mean_year"],
            "stdev_days": summary["stdev_days"],
            "baseline_severity": summary["baseline_severity"],
        },
        summary["anomalies"],
    )
    severity = ai["severity"] if ai else summary["baseline_severity"]
    return {
        "dates": parsed,
        "anomalies": summary["anomalies"],
        "total": summary["total"],
        "anomaly_count": summary["anomaly_count"],
        "severity": severity,
        "rationale": (ai or {}).get("rationale", ""),
        "top_concern": (ai or {}).get("top_concern", ""),
    }


# --------------------------------------------------------------------------- #
# Duplicate-content anomaly detection (aiify-opp-5984: hardcoded_threshold ->
# anomaly_detection). The external scan flagged paperless-ngx
# docker/rootfs/usr/local/bin/deduplicate.py — the helper that strips duplicate
# content out of a scanned document — and recommended an anomaly-detection
# paradigm over its fixed dedup rules. The repo is ephemeral, so per the
# established aiify-opp pattern the augmentation lands in the analogous ICDEV
# subsystem (DIC).
#
# DIC already dedups *whole files* across a collection by exact content hash
# (the idempotency check below). That misses the *intra*-document case a
# page-deduper targets: the same paragraph or page repeated WITHIN one file — a
# scanner double-feed, a duplicated insert, a copy-paste artifact, an OCR pass
# that emitted a page twice. This complementary layer segments the incoming
# document's own text into content blocks and flags blocks that recur
# anomalously often, the way duplicate pages would.
#
# Design (mirrors the date-anomaly sibling, aiify-opp-6048):
#   - the deterministic detector is the ALWAYS-authoritative baseline: pure
#     block segmentation + named-threshold / statistical-outlier rules, offline.
#   - the named thresholds below are exactly the "hardcoded_threshold" the scan
#     called out, lifted out of inline literals into one tunable place.
#   - short blocks (page numbers, a "CONFIDENTIAL" banner, running headers) are
#     excluded by a minimum-length floor — those recur legitimately on every
#     page and are not duplication anomalies.
#   - the optional LLM layer only grades *severity* and explains the flagged
#     blocks; it degrades silently to the heuristic severity and is never a hard
#     dependency.
#   - results are surfaced as a *proposal* under
#     IngestOutcome.metadata["duplicate_blocks"] — never silently dropped or
#     auto-deduped — so a human confirms before anything sticks.
# --------------------------------------------------------------------------- #

# Only the leading slice of a document is segmented — keeps the pass cheap and
# bounded regardless of document size.
_DUP_INPUT_CHARS = 200000

# A normalized block shorter than this many characters is ignored: page numbers,
# banners, and running headers/footers recur legitimately and are not the
# repeated *content* a deduper targets. Substantial paragraphs/pages are.
_DUP_BLOCK_MIN_CHARS = 64

# A block must appear at least this many times to count as duplicated content.
_DUP_BLOCK_MIN_REPEATS = 2

# A block whose repeat count is more than this many standard deviations above
# the document's per-block mean is an over-repetition outlier (escalated reason).
# Kept at 2.0 in step with the date-anomaly sibling (_DATE_ANOMALY_STDEV_K).
_DUP_ANOMALY_STDEV_K = 2.0

# Need at least this many distinct significant blocks before the repeat-count
# distribution is meaningful enough to call any block a statistical outlier.
_DUP_ANOMALY_MIN_SAMPLE = 4

# Bound how many duplicate clusters the optional LLM severity pass is shown.
_DUP_LLM_SAMPLE = 6

# Deterministic severity cutoffs on the duplicated fraction of document content.
_DUP_SEV_HIGH_FRACTION = 0.34
_DUP_SEV_MEDIUM_FRACTION = 0.10

# Block boundaries: a form-feed (page break) or a blank line. Extractors join
# pages on one or the other, so this catches both duplicate paragraphs and
# duplicate pages.
_DUP_BLOCK_SPLIT = re.compile(r"\f|\n[ \t]*\n")

_DUP_BLOCK_SYSTEM_PROMPT = (
    "You are a records-management analyst grading how concerning the duplicated "
    "content in a single document is. You are given how many content blocks the "
    "document has, which blocks recurred and how many times, what fraction of "
    "the document is redundant, and a deterministic baseline severity. A whole "
    "page or large paragraph repeated many times (a scanner double-feed or a "
    "duplicated insert) is most concerning; one paragraph quoted twice is minor. "
    "You may agree with or adjust the baseline, but justify any change. Respond "
    "ONLY with a JSON object: {\"severity\": \"low|medium|high\", \"rationale\": "
    "\"<=160 chars\", \"top_concern\": \"<the most-repeated snippet, <=60 chars>\"}. "
    "Never invent blocks beyond those provided."
)


def _segment_blocks(text: str) -> list[dict]:
    """Split ``text`` into normalized significant content blocks (deterministic).

    Segments the leading ``_DUP_INPUT_CHARS`` on page breaks / blank lines,
    normalizes each block (whitespace collapsed, lower-cased) so cosmetic
    re-flow differences do not hide a duplicate, and keeps only blocks of at
    least ``_DUP_BLOCK_MIN_CHARS`` so running headers/footers and page numbers
    are not mistaken for duplicated content.

    Returns:
        A list of ``{"norm": <normalized text>, "raw": <first 200 raw chars>,
        "len": int}`` in document order. Empty when nothing qualifies.
    """
    if not text:
        return []
    window = text[:_DUP_INPUT_CHARS]
    out: list[dict] = []
    for raw in _DUP_BLOCK_SPLIT.split(window):
        norm = " ".join(raw.split()).lower()
        if len(norm) < _DUP_BLOCK_MIN_CHARS:
            continue
        out.append({"norm": norm, "raw": raw.strip()[:200], "len": len(norm)})
    return out


def _heuristic_dup_severity(dup_fraction: float, cluster_count: int) -> str:
    """Deterministic duplicate-content severity — the always-available baseline.

    Pure function of how large a fraction of the document is redundant.
    """
    if cluster_count <= 0:
        return "low"
    if dup_fraction >= _DUP_SEV_HIGH_FRACTION:
        return "high"
    if dup_fraction >= _DUP_SEV_MEDIUM_FRACTION:
        return "medium"
    return "low"


def _detect_duplicate_blocks(blocks: list[dict]) -> dict:
    """Flag content blocks that recur within a document (deterministic, authoritative).

    Clusters ``blocks`` by normalized content and flags any that appear at least
    ``_DUP_BLOCK_MIN_REPEATS`` times. A cluster whose repeat count is more than
    ``_DUP_ANOMALY_STDEV_K`` standard deviations above the document's per-block
    mean (only with at least ``_DUP_ANOMALY_MIN_SAMPLE`` distinct blocks and a
    non-degenerate spread) is escalated from ``duplicate_block`` to
    ``anomalous_repeat``.

    Args:
        blocks: output of :func:`_segment_blocks`.

    Returns:
        ``{"total_blocks", "distinct_blocks", "duplicate_clusters",
           "anomaly_count", "clusters": [...], "duplicate_fraction",
           "mean_repeats", "stdev_repeats", "baseline_severity"}``. ``clusters``
        items carry ``{"snippet", "repeats", "chars", "reason"}`` sorted by
        repeat count, where reason is ``duplicate_block`` or ``anomalous_repeat``.
    """
    total_blocks = len(blocks)
    empty = {
        "total_blocks": total_blocks, "distinct_blocks": 0,
        "duplicate_clusters": 0, "anomaly_count": 0, "clusters": [],
        "duplicate_fraction": 0.0, "mean_repeats": 0.0, "stdev_repeats": 0.0,
        "baseline_severity": "low",
    }
    if total_blocks == 0:
        return empty

    # Cluster by normalized content, preserving first-seen order and raw sample.
    groups: dict[str, dict] = {}
    order: list[str] = []
    for b in blocks:
        g = groups.get(b["norm"])
        if g is None:
            groups[b["norm"]] = {"raw": b["raw"], "count": 1, "len": b["len"]}
            order.append(b["norm"])
        else:
            g["count"] += 1

    counts = [groups[n]["count"] for n in order]
    distinct = len(counts)
    mean = sum(counts) / distinct
    variance = sum((c - mean) ** 2 for c in counts) / distinct
    stdev = variance ** 0.5
    total_chars = sum(groups[n]["len"] * groups[n]["count"] for n in order) or 1

    clusters: list[dict] = []
    dup_chars = 0
    for n in order:
        g = groups[n]
        if g["count"] < _DUP_BLOCK_MIN_REPEATS:
            continue
        dup_chars += (g["count"] - 1) * g["len"]  # redundant copies only
        reason = "duplicate_block"
        if (
            distinct >= _DUP_ANOMALY_MIN_SAMPLE
            and stdev > 0
            and (g["count"] - mean) > _DUP_ANOMALY_STDEV_K * stdev
        ):
            reason = "anomalous_repeat"
        clusters.append(
            {"snippet": g["raw"], "repeats": g["count"], "chars": g["len"], "reason": reason}
        )
    clusters.sort(key=lambda c: c["repeats"], reverse=True)

    return {
        "total_blocks": total_blocks,
        "distinct_blocks": distinct,
        "duplicate_clusters": len(clusters),
        "anomaly_count": sum(1 for c in clusters if c["reason"] == "anomalous_repeat"),
        "clusters": clusters,
        "duplicate_fraction": round(dup_chars / total_chars, 4),
        "mean_repeats": round(mean, 2),
        "stdev_repeats": round(stdev, 2),
        "baseline_severity": _heuristic_dup_severity(dup_chars / total_chars, len(clusters)),
    }


def _ai_dup_block_assessment(summary: dict, clusters: list[dict]) -> dict | None:
    """Grade duplicate-content severity with the LLM, grounded on real counts.

    Best-effort enrichment only. Returns
    ``{"severity": "low|medium|high", "rationale": str, "top_concern": str}`` on
    success, or ``None`` when there is nothing to grade, the model is
    unavailable, or the output is missing/blank/malformed/out-of-range. Callers
    MUST treat ``None`` as "use the deterministic baseline".
    """
    if not clusters or summary.get("duplicate_clusters", 0) <= 0:
        return None
    try:
        import json as _json

        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        lines = [
            f"Document duplication facts: {_json.dumps(summary, sort_keys=True)}",
            f"Deterministic baseline severity: {summary.get('baseline_severity')}",
            "Duplicated blocks:",
        ]
        for c in clusters[:_DUP_LLM_SAMPLE]:
            lines.append(f"- {_json.dumps(c, default=str)}")

        req = LLMRequest(
            messages=[
                {"role": "user", "content": "\n".join(lines) + "\n\nGrade the severity."}
            ],
            system_prompt=_DUP_BLOCK_SYSTEM_PROMPT,
            max_tokens=200,
            temperature=0.1,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("dic_duplicate_block_assessment", req)
        if not resp or not resp.content:
            return None
        raw = resp.content.strip()
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
            "top_concern": str(parsed.get("top_concern") or "").strip()[:60],
        }
    except Exception:
        return None


def assess_duplicate_blocks(text: str) -> dict | None:
    """Detect duplicated content blocks in a document (HITL proposal).

    Orchestrates :func:`_segment_blocks` + :func:`_detect_duplicate_blocks`
    (always authoritative) and layers best-effort LLM severity grading on top.

    Args:
        text: the document's extracted text.

    Returns:
        ``None`` when no block recurs (nothing to surface), else
        ``{"clusters": [...], "total_blocks": int, "distinct_blocks": int,
           "duplicate_clusters": int, "anomaly_count": int,
           "duplicate_fraction": float, "severity": str, "rationale": str,
           "top_concern": str}``. ``severity`` is the LLM grade when available,
        otherwise the deterministic baseline.
    """
    blocks = _segment_blocks(text or "")
    summary = _detect_duplicate_blocks(blocks)
    if summary["duplicate_clusters"] <= 0:
        return None

    ai = _ai_dup_block_assessment(
        {
            "total_blocks": summary["total_blocks"],
            "distinct_blocks": summary["distinct_blocks"],
            "duplicate_clusters": summary["duplicate_clusters"],
            "anomaly_count": summary["anomaly_count"],
            "duplicate_fraction": summary["duplicate_fraction"],
            "baseline_severity": summary["baseline_severity"],
        },
        summary["clusters"],
    )
    severity = ai["severity"] if ai else summary["baseline_severity"]
    return {
        "clusters": summary["clusters"],
        "total_blocks": summary["total_blocks"],
        "distinct_blocks": summary["distinct_blocks"],
        "duplicate_clusters": summary["duplicate_clusters"],
        "anomaly_count": summary["anomaly_count"],
        "duplicate_fraction": summary["duplicate_fraction"],
        "severity": severity,
        "rationale": (ai or {}).get("rationale", ""),
        "top_concern": (ai or {}).get("top_concern", ""),
    }


# --------------------------------------------------------------------------- #
# Ingest-workload anomaly detection (aiify-opp-6097: hardcoded_threshold ->
# anomaly_detection). The external scan flagged paperless-ngx
# src/paperless/celery.py — the Celery task-queue / worker config — and
# recommended an anomaly-detection paradigm over its fixed numeric thresholds
# (the hardcoded task time/size limits a worker config sets to fence off
# runaway tasks). The repo is ephemeral, so per the established aiify-opp
# pattern the augmentation lands in the analogous ICDEV subsystem (DIC).
#
# The DIC analog of a Celery worker guard is the ingest pipeline's *workload
# profile*: every ingested file becomes a background job (extract -> OCR ->
# embed -> KG bridge) whose cost is driven by the file. A worker config caps
# task time/size to stop one pathological payload from hammering the pool; the
# document-level analog is detecting a pathological *ingest cost profile* up
# front so a human is warned before the file silently overloads the pipeline:
#   - sparse_extraction — a substantial file that yields almost no text (an
#     image-only / scanned / corrupt PDF) will hammer the OCR worker for no
#     content; the classic runaway a task time-limit fences off.
#   - sparse_pages — many pages but near-zero text per page: scanned imagery
#     that OCR could not (or did not) recover.
#   - payload_explosion — extracted text far larger than the file's own bytes,
#     implying a decompression/expansion blow-up (an archive- or zip-bomb-like
#     payload) that balloons downstream chunk/embed work.
#
# Design (mirrors the date- and duplicate-anomaly siblings, aiify-opp 6048 /
# 5984):
#   - the deterministic detector is the ALWAYS-authoritative baseline: pure
#     ratio math + named-threshold rules, offline, no network.
#   - the named thresholds below are exactly the "hardcoded_threshold" the scan
#     called out, lifted out of inline literals into one tunable place.
#   - small files are excluded by a minimum-size floor — extraction-yield
#     ratios on tiny notes are noise, and a short legitimate memo is not a
#     runaway job.
#   - the optional LLM layer only grades *severity* and explains the flagged
#     profile; it degrades silently to the heuristic severity and is never a
#     hard dependency.
#   - results are surfaced as a *proposal* under
#     IngestOutcome.metadata["workload_anomaly"] only when something is
#     anomalous — never silently acted on — so a human reviews before the file
#     is trusted to the pipeline.
# --------------------------------------------------------------------------- #

# Below this file size, extraction-yield ratios are too noisy to judge — a short
# legitimate note is small AND text-light without being a runaway job. 50 KiB.
_WORKLOAD_MIN_FILE_BYTES = 51200

# A file at/above the size floor that yields fewer than this many characters per
# KiB is almost certainly image-only / scanned / corrupt — a "sparse extraction"
# that will hammer the OCR worker for little or no content.
_WORKLOAD_MIN_CHARS_PER_KB = 2.0

# Pages averaging fewer than this many characters are image-only / scanned
# (OCR did not recover their text). Only assessed when the page count is known.
_WORKLOAD_MIN_CHARS_PER_PAGE = 50.0

# Extracted text more than this many characters per KiB — i.e. several times the
# file's own byte size — implies a decompression/expansion blow-up (archive- or
# zip-bomb-like) rather than ordinary document text (~1 char/byte ≈ 1024/KiB).
_WORKLOAD_MAX_CHARS_PER_KB = 4096.0

# Smallest file worth checking for a payload explosion — below this even a large
# expansion ratio is a handful of bytes and not worth surfacing. 1 KiB.
_WORKLOAD_EXPLOSION_MIN_BYTES = 1024

# A sparse extraction this far below the chars/KiB floor on a file this many
# times the size floor is an essentially-empty large payload — the worst case.
_WORKLOAD_SEVERE_CHARS_PER_KB = _WORKLOAD_MIN_CHARS_PER_KB / 4.0  # 0.5
_WORKLOAD_SEVERE_FILE_BYTES = _WORKLOAD_MIN_FILE_BYTES * 4        # 200 KiB

_WORKLOAD_SYSTEM_PROMPT = (
    "You are a document-pipeline operations analyst grading how concerning a "
    "single file's ingest workload profile is. You are given the file's size, "
    "how much text was extracted, its page count, the derived characters-per-KiB "
    "and characters-per-page ratios, which deterministic rules a detector "
    "flagged and why (sparse_extraction = a large file that yielded almost no "
    "text and will hammer OCR; sparse_pages = many pages with near-zero text "
    "each; payload_explosion = extracted text far larger than the file itself), "
    "and a deterministic baseline severity. A large file that extracted no text, "
    "or a payload that explodes far beyond its byte size, is most concerning; a "
    "mildly text-light file is minor. You may agree with or adjust the baseline, "
    "but justify any change. Respond ONLY with a JSON object: "
    '{"severity": "low|medium|high", "rationale": "<=160 chars", '
    '"top_concern": "<the single most concerning rule name>"}. Never invent '
    "metrics beyond those provided."
)


def _heuristic_workload_severity(flags: list[dict]) -> str:
    """Deterministic ingest-workload severity — the always-available baseline.

    Pure function of which rules fired and how extreme they are. A payload
    explosion, or an essentially-empty large file, is ``high``; any other
    flagged profile is ``medium``. ``low`` is never returned because the caller
    only invokes this when at least one rule has fired.
    """
    if not flags:
        return "low"
    for f in flags:
        if f.get("rule") == "payload_explosion":
            return "high"
        if (
            f.get("rule") == "sparse_extraction"
            and f.get("byte_size", 0) >= _WORKLOAD_SEVERE_FILE_BYTES
            and f.get("metric", _WORKLOAD_MIN_CHARS_PER_KB) < _WORKLOAD_SEVERE_CHARS_PER_KB
        ):
            return "high"
    return "medium"


def _detect_workload_anomaly(
    byte_size: int, text_len: int, page_count: int
) -> dict:
    """Flag pathological ingest-cost profiles (deterministic, authoritative).

    Three rules, each lifting a named threshold out of inline magic numbers:
      • sparse_extraction — file ≥ :data:`_WORKLOAD_MIN_FILE_BYTES` yet under
        :data:`_WORKLOAD_MIN_CHARS_PER_KB` characters per KiB.
      • sparse_pages — page count known and average characters per page under
        :data:`_WORKLOAD_MIN_CHARS_PER_PAGE` (only on files past the size floor).
      • payload_explosion — file past :data:`_WORKLOAD_EXPLOSION_MIN_BYTES` whose
        text exceeds :data:`_WORKLOAD_MAX_CHARS_PER_KB` characters per KiB.

    Args:
        byte_size: the file's size on disk in bytes.
        text_len: number of characters extracted from the file.
        page_count: number of pages (0/unknown skips the per-page rule).

    Returns:
        ``{"byte_size": int, "text_len": int, "page_count": int,
           "chars_per_kb": float, "chars_per_page": float|None,
           "flags": [{"rule", "metric", "threshold", "byte_size", "detail"}...],
           "anomaly_count": int, "baseline_severity": str}``. ``anomaly_count``
        is 0 (and ``baseline_severity`` ``"low"``) when nothing is anomalous.
    """
    byte_size = max(int(byte_size or 0), 0)
    text_len = max(int(text_len or 0), 0)
    page_count = max(int(page_count or 0), 0)

    chars_per_kb = (text_len * 1024.0 / byte_size) if byte_size > 0 else 0.0
    chars_per_page = (text_len / page_count) if page_count > 0 else None

    flags: list[dict] = []

    if byte_size >= _WORKLOAD_MIN_FILE_BYTES and chars_per_kb < _WORKLOAD_MIN_CHARS_PER_KB:
        flags.append({
            "rule": "sparse_extraction",
            "metric": round(chars_per_kb, 2),
            "threshold": _WORKLOAD_MIN_CHARS_PER_KB,
            "byte_size": byte_size,
            "detail": (
                f"{byte_size} byte file yielded only {text_len} chars "
                f"({round(chars_per_kb, 2)}/KiB) — likely image-only/scanned/corrupt."
            ),
        })

    if (
        page_count > 0
        and byte_size >= _WORKLOAD_MIN_FILE_BYTES
        and chars_per_page is not None
        and chars_per_page < _WORKLOAD_MIN_CHARS_PER_PAGE
    ):
        flags.append({
            "rule": "sparse_pages",
            "metric": round(chars_per_page, 1),
            "threshold": _WORKLOAD_MIN_CHARS_PER_PAGE,
            "byte_size": byte_size,
            "detail": (
                f"{page_count} pages but only {round(chars_per_page, 1)} chars/page "
                "— scanned imagery OCR did not recover."
            ),
        })

    if byte_size >= _WORKLOAD_EXPLOSION_MIN_BYTES and chars_per_kb > _WORKLOAD_MAX_CHARS_PER_KB:
        flags.append({
            "rule": "payload_explosion",
            "metric": round(chars_per_kb, 1),
            "threshold": _WORKLOAD_MAX_CHARS_PER_KB,
            "byte_size": byte_size,
            "detail": (
                f"Extracted {text_len} chars from a {byte_size} byte file "
                f"({round(chars_per_kb, 1)}/KiB) — decompression/expansion blow-up."
            ),
        })

    return {
        "byte_size": byte_size,
        "text_len": text_len,
        "page_count": page_count,
        "chars_per_kb": round(chars_per_kb, 2),
        "chars_per_page": round(chars_per_page, 1) if chars_per_page is not None else None,
        "flags": flags,
        "anomaly_count": len(flags),
        "baseline_severity": _heuristic_workload_severity(flags),
    }


def _ai_workload_severity(summary: dict) -> dict | None:
    """Grade ingest-workload anomaly severity with the LLM, grounded on metrics.

    Best-effort enrichment only. Returns
    ``{"severity": "low|medium|high", "rationale": str, "top_concern": str}`` on
    success, or ``None`` when there is nothing to grade, the model is
    unavailable, or the output is missing/blank/malformed/out-of-range. Callers
    MUST treat ``None`` as "use the deterministic baseline".
    """
    if summary.get("anomaly_count", 0) <= 0:
        return None
    try:
        import json as _json

        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        facts = {
            k: summary.get(k)
            for k in (
                "byte_size", "text_len", "page_count",
                "chars_per_kb", "chars_per_page", "baseline_severity",
            )
        }
        lines = [
            f"Ingest workload facts: {_json.dumps(facts, sort_keys=True)}",
            f"Deterministic baseline severity: {summary.get('baseline_severity')}",
            "Flagged rules:",
        ]
        for f in summary.get("flags", []):
            lines.append(f"- {_json.dumps(f, default=str)}")

        req = LLMRequest(
            messages=[
                {"role": "user", "content": "\n".join(lines) + "\n\nGrade the severity."}
            ],
            system_prompt=_WORKLOAD_SYSTEM_PROMPT,
            max_tokens=200,
            temperature=0.1,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("dic_workload_anomaly_assessment", req)
        if not resp or not resp.content:
            return None
        raw = resp.content.strip()
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
            "top_concern": str(parsed.get("top_concern") or "").strip()[:40],
        }
    except Exception:
        return None


def assess_ingest_workload(
    byte_size: int, text_len: int, page_count: int
) -> dict | None:
    """Flag a pathological ingest-cost profile for a file (HITL proposal).

    Orchestrates :func:`_detect_workload_anomaly` (always authoritative) and
    layers best-effort LLM severity grading on top. Unlike the text-content
    detectors this is deliberately evaluated even when no text was extracted —
    an empty extraction on a large file is precisely the anomaly it catches.

    Args:
        byte_size: the file's size on disk in bytes.
        text_len: number of characters extracted from the file.
        page_count: number of pages (0/unknown skips the per-page rule).

    Returns:
        ``None`` when the profile is unremarkable (nothing to surface), else
        ``{"byte_size": int, "text_len": int, "page_count": int,
           "chars_per_kb": float, "chars_per_page": float|None, "flags": [...],
           "anomaly_count": int, "severity": str, "rationale": str,
           "top_concern": str}``. ``severity`` is the LLM grade when available,
        otherwise the deterministic baseline.
    """
    summary = _detect_workload_anomaly(byte_size, text_len, page_count)
    if summary["anomaly_count"] <= 0:
        return None

    ai = _ai_workload_severity(summary)
    severity = ai["severity"] if ai else summary["baseline_severity"]
    return {
        "byte_size": summary["byte_size"],
        "text_len": summary["text_len"],
        "page_count": summary["page_count"],
        "chars_per_kb": summary["chars_per_kb"],
        "chars_per_page": summary["chars_per_page"],
        "flags": summary["flags"],
        "anomaly_count": summary["anomaly_count"],
        "severity": severity,
        "rationale": (ai or {}).get("rationale", ""),
        "top_concern": (ai or {}).get("top_concern", ""),
    }


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

def ingest_file(
    path: str,
    collection_id: str,
    *,
    tenant_id: str | None = None,
    classification: str | None = None,
    created_by: str | None = None,
    embed: bool = True,
    bridge_kg: bool = True,
    summarize: bool = True,
    clean_ocr: bool = True,
    extract_metadata: bool = True,
    extract_identifiers: bool = True,
    classify_taxonomy: list[str] | None = None,
    classify_multi_label: bool = False,
    extract_correspondence: bool = True,
    detect_date_anomalies: bool = True,
    detect_duplicate_blocks: bool = True,
    detect_workload_anomaly: bool = True,
    conn=None,
    progress_cb=None,
) -> IngestOutcome:
    """Route a file through provider -> RAG ingest -> KG bridge -> DIC rows.

    Args:
        path: file to ingest.
        collection_id: target RAG/DIC collection.
        tenant_id/classification: security stamp; default from security context.
        created_by: user id recorded on the initial version row.
        embed: when True, embed + upsert chunks into the vector store.
        bridge_kg: when True, extract entities/relationships into the KG.
        summarize: when True, best-effort LLM title/abstract enrichment grounded
            in the extracted text (aiify-opp-6098). Failures degrade silently.
        clean_ocr: when True, best-effort LLM correction of noisy OCR text
            (aiify-opp-6118) — only fires when the extractor used an OCR path
            and the text fits the cleanup budget; grounded on the OCR text and
            length-ratio guarded so it can never drop or invent content.
            Failures degrade silently to the raw OCR text.
        extract_metadata: when True, best-effort LLM extraction of structured
            document metadata — document_type (closed enum), topic tags, and the
            document date (aiify-opp-6086) — grounded in the text and confidence
            gated. Surfaced as a HITL proposal on ``IngestOutcome.metadata``;
            never silently persisted. Failures degrade silently to no metadata.
        extract_identifiers: when True, best-effort LLM extraction of structured
            document identifiers — the codes a barcode/label would carry (ASN,
            invoice/contract/PO/reference/document/tracking/control/case/serial
            numbers) when printed in the text rather than encoded in a physical
            barcode (aiify-opp-5988). Each value is shape-validated and must
            appear verbatim in the source text (anti-hallucination), and the
            result is confidence gated. Surfaced as a HITL proposal under
            ``IngestOutcome.metadata["identifiers"]``; never silently persisted.
            Failures degrade silently to no identifiers.
        classify_taxonomy: optional list of existing, user-curated category
            labels (correspondents / document types / tags). When supplied,
            best-effort LLM classification of the document into that exact
            taxonomy — the ``matching_algorithm = AUTO`` analog (aiify-opp-6043).
            The model may only select from the labels given (never invent one)
            and returns nothing when none fit. Default ``None`` leaves the
            feature off (no taxonomy → no classification). Surfaced as a HITL
            proposal under ``IngestOutcome.metadata["classification"]``; never
            silently persisted. Failures degrade silently to no classification.
        classify_multi_label: when True, ``classify_taxonomy`` may return several
            labels (the Tag analog); when False (default) only the single best
            label is kept (the Correspondent / DocumentType analog).
        extract_correspondence: when True, best-effort LLM extraction of email
            envelope fields — sender, recipients (To/Cc), subject, sent date —
            from the document's own text (aiify-opp-6100), the NLP-extractor
            analog of an email header parser. Addresses are e-mail-shape and
            grounding guarded (must appear verbatim in the text) and names/subject
            must have a grounded core (anti-hallucination); the result is
            confidence gated. Surfaced as a HITL proposal under
            ``IngestOutcome.metadata["correspondence"]``; never silently
            persisted. Failures degrade silently to no correspondence fields.
        detect_date_anomalies: when True, deterministically parse every calendar
            date in the document's text and flag the anomalous ones — future
            dated, implausibly old, or a statistical outlier vs the document's
            own date cluster (aiify-opp-6048), the anomaly-detection analog of
            paperless's date-parsing plugin. The detector is offline and always
            authoritative; an optional best-effort LLM pass only grades severity.
            Surfaced as a HITL proposal under
            ``IngestOutcome.metadata["date_anomalies"]`` only when at least one
            anomaly is found; never silently persisted. Degrades silently.
        detect_duplicate_blocks: when True, segment the document's text into
            content blocks and flag blocks (paragraphs/pages) that recur within
            the file — a scanner double-feed, duplicated insert, or copy-paste
            artifact (aiify-opp-5984), the anomaly-detection analog of
            paperless's page deduper. Complements the cross-file exact-hash dedup
            (which only catches whole-file duplicates). Short blocks (headers,
            page numbers) are excluded by a length floor. The detector is offline
            and always authoritative; an optional best-effort LLM pass only
            grades severity. Surfaced as a HITL proposal under
            ``IngestOutcome.metadata["duplicate_blocks"]`` only when a block
            recurs; never silently deduped. Degrades silently.
        detect_workload_anomaly: when True, check the file's ingest-cost profile
            and flag a pathological one — a large file that extracted almost no
            text (a scanned/corrupt OCR hammer), near-zero text per page, or a
            payload that explodes far beyond its own byte size (aiify-opp-6097),
            the anomaly-detection analog of a Celery worker's hardcoded task
            time/size limit. Unlike the content detectors it is evaluated even
            when no text was extracted — an empty extraction on a large file is
            precisely the anomaly. The detector is offline and always
            authoritative; an optional best-effort LLM pass only grades
            severity. Surfaced as a HITL proposal under
            ``IngestOutcome.metadata["workload_anomaly"]`` only when the profile
            is anomalous. Degrades silently.
        conn: optional DB connection (else an RLS-aware one is opened).
        progress_cb: optional callable(stage: str, detail: str, pct: int) for progress events.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"not a file: {path}")

    tid, cls = _resolve_context(tenant_id, classification)
    errors: list[str] = []

    def _emit(stage: str, detail: str, pct: int = 0) -> None:
        if progress_cb:
            try:
                progress_cb(stage, detail, pct)
            except Exception:
                pass

    # 1) Extract.
    _emit("extracting", f"Reading {p.name}…", 5)
    extraction = _select_extractor(p)
    text = extraction.text or ""

    # Surface extraction warnings as outcome errors so the UI can display them.
    if extraction.warnings:
        errors.extend(extraction.warnings)

    # Compute content hash on the RAW extracted text for deterministic dedup —
    # OCR cleanup below is non-deterministic, so hashing pre-cleanup keeps the
    # same scanned file idempotent across ingests.
    content_hash = _sha256(text)

    # LLM OCR cleanup (best-effort): correct noisy OCR output before chunking.
    # Only fires for OCR-derived providers; the raw text is kept on any failure.
    ocr_cleaned = False
    if clean_ocr and text.strip() and extraction.provider in _OCR_PROVIDERS:
        _emit("ocr_cleanup", "Correcting OCR artifacts…", 7)
        corrected = _ai_ocr_cleanup(text)
        if corrected:
            text = corrected
            ocr_cleaned = True

    # LLM enrichment (best-effort): a title + abstract grounded in the text.
    ai_title, ai_summary = "", ""
    if summarize and text.strip():
        _emit("summarizing", "Generating title and abstract…", 8)
        ai = _ai_document_summary(text, p.name, extraction.page_count)
        if ai:
            ai_title, ai_summary = ai.get("title", ""), ai.get("summary", "")

    # LLM metadata extraction (best-effort): structured document_type / tags /
    # date proposed from the text, grounded + confidence-gated. Surfaced as a
    # HITL proposal only — never silently written. (aiify-opp-6086)
    ai_metadata: dict = {}
    if extract_metadata and text.strip():
        _emit("metadata", "Extracting document metadata…", 9)
        md = _ai_metadata_extraction(text, p.name)
        if md:
            ai_metadata = md

    # LLM identifier extraction (best-effort): the codes a barcode would carry
    # (ASN, invoice/contract/PO/etc. numbers), extracted from the text when no
    # physical barcode is present. Shape- + membership-guarded and confidence
    # gated; surfaced as a HITL proposal under metadata["identifiers"], never
    # silently written. (aiify-opp-5988)
    if extract_identifiers and text.strip():
        _emit("identifiers", "Extracting document identifiers…", 9)
        ids = _ai_extract_identifiers(text)
        if ids:
            ai_metadata = {**ai_metadata, "identifiers": ids}

    # LLM taxonomy classification (best-effort): file the document under one (or
    # several, when multi-label) of the caller's existing curated labels — the
    # matching_algorithm=AUTO analog. The model may only pick from the offered
    # taxonomy; surfaced as a HITL proposal under metadata["classification"],
    # never silently written. Off unless a taxonomy is supplied. (aiify-opp-6043)
    if classify_taxonomy and text.strip():
        _emit("classifying", "Classifying into taxonomy…", 9)
        cls_result = _ai_classify_into_taxonomy(
            text, classify_taxonomy, multi_label=classify_multi_label, filename=p.name
        )
        if cls_result:
            ai_metadata = {**ai_metadata, "classification": cls_result}

    # LLM correspondence extraction (best-effort): the email envelope fields
    # (sender, recipients, subject, sent date) pulled from the document text — the
    # NLP-extractor analog of an email header parser. Address/name/subject grounded
    # and confidence gated; surfaced as a HITL proposal under
    # metadata["correspondence"], never silently written. (aiify-opp-6100)
    if extract_correspondence and text.strip():
        _emit("correspondence", "Extracting correspondence fields…", 9)
        corr = _ai_extract_correspondence(text)
        if corr:
            ai_metadata = {**ai_metadata, "correspondence": corr}

    # Date-anomaly detection (deterministic + best-effort LLM severity): parse
    # every date in the text and flag future-dated / implausibly-old / outlier
    # dates the way an OCR typo or a back-dated insert would produce. Always
    # authoritative offline baseline; surfaced as a HITL proposal under
    # metadata["date_anomalies"] only when an anomaly is found. (aiify-opp-6048)
    if detect_date_anomalies and text.strip():
        _emit("date_anomalies", "Scanning document dates…", 9)
        da = assess_document_dates(text)
        if da:
            ai_metadata = {**ai_metadata, "date_anomalies": da}

    # Duplicate-content anomaly detection (deterministic + best-effort LLM
    # severity): segment the text and flag blocks (paragraphs/pages) that recur
    # within the document the way a scanner double-feed or duplicated insert
    # would. Complements the cross-file exact-hash dedup below, which only
    # catches whole-file duplicates. Always-authoritative offline baseline;
    # surfaced as a HITL proposal under metadata["duplicate_blocks"] only when a
    # block recurs. (aiify-opp-5984)
    if detect_duplicate_blocks and text.strip():
        _emit("duplicate_blocks", "Scanning for duplicated content…", 9)
        dup = assess_duplicate_blocks(text)
        if dup:
            ai_metadata = {**ai_metadata, "duplicate_blocks": dup}

    # Ingest-workload anomaly detection (deterministic + best-effort LLM
    # severity): flag a pathological ingest-cost profile — a large file that
    # extracted almost no text (a scanned/corrupt OCR hammer), near-zero text
    # per page, or a payload that explodes far beyond its own byte size — the
    # way a Celery worker's task time/size limit fences off a runaway job.
    # Deliberately NOT gated on text being non-empty: an empty extraction on a
    # large file is exactly the anomaly. Surfaced as a HITL proposal under
    # metadata["workload_anomaly"] only when something is anomalous. (aiify-opp-6097)
    if detect_workload_anomaly:
        _emit("workload_anomaly", "Checking ingest workload profile…", 9)
        try:
            wl = assess_ingest_workload(p.stat().st_size, len(text), extraction.page_count or 0)
        except Exception:
            wl = None
        if wl:
            ai_metadata = {**ai_metadata, "workload_anomaly": wl}

    # ── Duplicate detection + bookkeeping ─────────────────────────────────────
    # Open a single DB connection (or reuse caller's) for dedup + writes.
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        _ensure_schema(conn)

        # ── Dedup: content-hash based idempotency ───────────────────────────────
        dup_row = conn.execute(
            "SELECT doc_id, filename FROM dic_documents WHERE content_sha256 = ? AND collection_id = ? LIMIT 1",
            (content_hash, collection_id),
        ).fetchone()
        if dup_row:
            existing_doc_id = dup_row[0] if hasattr(dup_row, "__getitem__") else dup_row["doc_id"]
            existing_filename = dup_row[1] if hasattr(dup_row, "__getitem__") else dup_row.get("filename", "")
            ver_row = conn.execute(
                "SELECT version_id, version_no FROM dic_versions WHERE doc_id = ? ORDER BY version_no DESC LIMIT 1",
                (existing_doc_id,),
            ).fetchone()
            if ver_row:
                version_id = ver_row[0] if hasattr(ver_row, "__getitem__") else ver_row["version_id"]
            else:
                version_id = f"{existing_doc_id}_v1"
            # Report the existing chunk count so callers see consistent metrics.
            chunk_row = conn.execute(
                "SELECT COUNT(*) FROM dic_chunk_links WHERE version_id = ?",
                (version_id,),
            ).fetchone()
            existing_chunks = chunk_row[0] if chunk_row else 0
            _emit("done", f"Idempotent — duplicate of {existing_doc_id}", 100)
            return IngestOutcome(
                doc_id=existing_doc_id,
                version_id=version_id,
                collection_id=collection_id,
                source_id=existing_doc_id,
                provider=extraction.provider,
                chunks=existing_chunks,
                chunks_embedded=existing_chunks,
                kg_entities=0,
                kg_relationships=0,
                tenant_id=tid,
                classification=cls,
                errors=errors + [f"Duplicate detected — this file already exists as '{existing_filename}' in this collection. No new version created (idempotent)."],
            )

        doc_id = _doc_id(collection_id, str(p))
        source_id = doc_id  # rag source id for these chunks

        # 2) Chunk (reuse chunker). chunk_content returns VectorChunk objects whose
        #    .chunk_id is the canonical rag_chunks id used by the vector store + KG.
        _emit("chunking", "Splitting into chunks…", 15)
        chunks = chunk_content(
            text,
            source_type="dic_document",
            source_id=source_id,
            source_table="dic_documents",
            metadata={"filename": p.name, "collection_id": collection_id},
            tenant_id=tid,
            project_id=collection_id,
            classification=cls,
        )
        _emit("chunking", f"{len(chunks)} chunks created", 20)

        # Warn when text is empty or near-empty so the UI can explain 0 chunks.
        if not text.strip():
            errors.append("Extracted text is empty — file may be image-based (scanned PDF), corrupted, or uses an unsupported encoding. Try OCR or re-export as text.")

        # 3) Embed + upsert into the vector store (same path ingest_source uses).
        chunks_embedded = 0
        if embed and chunks:
            total_chunks = len(chunks)

            def _embed_progress(done: int, total: int) -> None:
                pct = 20 + int(done / max(total, 1) * 55)
                _emit("embedding", f"Embedding {done}/{total} chunks…", pct)

            _emit("embedding", f"Embedding 0/{total_chunks} chunks…", 20)
            chunks_embedded = _embed_and_store(chunks, tid, errors, progress_cb=_embed_progress)
            _emit("embedding", f"Embedded {chunks_embedded}/{total_chunks} chunks", 75)

        # 4) DIC bookkeeping rows.
        now = _now()
        version_id = f"{doc_id}_v1"
        cur = conn.cursor()

        cur.execute(
            """
            INSERT OR REPLACE INTO dic_documents
                (doc_id, collection_id, source_id, filename, filepath,
                 content_type, provider, title, byte_size, content_sha256,
                 page_count, created_at, tenant_id, classification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id, collection_id, source_id, p.name, str(p),
                extraction.content_type, extraction.provider,
                extraction.title or ai_title or p.stem, p.stat().st_size, content_hash,
                extraction.page_count, now, tid, cls,
            ),
        )

        cur.execute(
            """
            INSERT OR REPLACE INTO dic_versions
                (version_id, doc_id, version_no, origin, status,
                 content_sha256, created_at, created_by, tenant_id, classification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id, doc_id, 1, "human_authored", "approved",
                content_hash, now, created_by, tid, cls,
            ),
        )

        # Refresh chunk links for this version.
        cur.execute("DELETE FROM dic_chunk_links WHERE version_id = ?", (version_id,))
        for i, chunk in enumerate(chunks):
            rag_chunk_id = getattr(chunk, "chunk_id", f"{source_id}_chunk_{i}")
            chunk_index = getattr(chunk, "chunk_index", i)
            md = getattr(chunk, "metadata", None) or {}
            page = md.get("page")
            section = md.get("section") or md.get("heading")
            link_id = f"{version_id}_link_{i}"
            cur.execute(
                """
                INSERT OR REPLACE INTO dic_chunk_links
                    (link_id, doc_id, version_id, rag_chunk_id, collection_id,
                     chunk_index, page, section, created_at, tenant_id, classification)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link_id, doc_id, version_id, rag_chunk_id, collection_id,
                    chunk_index, page, section, now, tid, cls,
                ),
            )
        conn.commit()

        # 5) KG bridge (best-effort). ingest_chunk reads rag_chunks by id, so this
        #    only finds content when embedding upserted the chunk above.
        _emit("kg_bridge", "Extracting entities and relationships…", 78)
        kg_entities = 0
        kg_rels = 0
        if bridge_kg and chunks and chunks_embedded:
            try:
                from tools.rag.rag_to_kg_ingester import ingest_chunk

                for chunk in chunks:
                    cid = getattr(chunk, "chunk_id", None)
                    if not cid:
                        continue
                    try:
                        summary = ingest_chunk(conn, cid)
                    except Exception as e:
                        errors.append(f"kg chunk failed: {e}")
                        continue
                    kg_entities += int(summary.get("nodes_written", 0) or 0)
                    kg_rels += int(summary.get("edges_written", 0) or 0)
            except Exception as e:
                errors.append(f"kg bridge unavailable: {e}")

        _emit("done", f"Done — {len(chunks)} chunks, {kg_entities} entities", 100)
        return IngestOutcome(
            doc_id=doc_id,
            version_id=version_id,
            collection_id=collection_id,
            source_id=source_id,
            provider=extraction.provider,
            chunks=len(chunks),
            chunks_embedded=chunks_embedded,
            kg_entities=kg_entities,
            kg_relationships=kg_rels,
            tenant_id=tid,
            classification=cls,
            summary=ai_summary,
            ocr_cleaned=ocr_cleaned,
            metadata=ai_metadata,
            errors=errors,
        )
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass
