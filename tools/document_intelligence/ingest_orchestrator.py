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
import os
import re
import sys
import time
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
    anomaly_report: dict[str, Any] | None = None

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
            "anomaly_report": self.anomaly_report,
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
_SUMMARY_INPUT_CHARS: int = int(os.environ.get("DIC_SUMMARY_INPUT_CHARS", "6000"))

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
_OCR_CLEANUP_MAX_CHARS: int = int(os.environ.get("DIC_OCR_CLEANUP_MAX_CHARS", "8000"))

# The corrected text must stay within this fraction of the original length in
# both directions; outside the band we assume the model dropped or invented
# content and discard the result, keeping the raw OCR text.
_OCR_CLEANUP_MIN_RATIO: float = float(os.environ.get("DIC_OCR_CLEANUP_MIN_RATIO", "0.5"))
_OCR_CLEANUP_MAX_RATIO: float = float(os.environ.get("DIC_OCR_CLEANUP_MAX_RATIO", "2.0"))

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
_METADATA_MIN_CONFIDENCE: float = float(os.environ.get("DIC_METADATA_MIN_CONFIDENCE", "0.70"))

_METADATA_MAX_TAGS: int = int(os.environ.get("DIC_METADATA_MAX_TAGS", "8"))
_METADATA_TAG_MAX_LEN: int = int(os.environ.get("DIC_METADATA_TAG_MAX_LEN", "40"))

# Date plausibility thresholds (aiify-opp-65: date_parsing hardcoded_threshold ->
# anomaly_detection). Dates outside [_DATE_MIN_YEAR, current+_DATE_MAX_YEAR_OFFSET]
# are flagged as implausible so callers can surface them as HITL review candidates.
_DATE_MIN_YEAR: int = int(os.environ.get("DIC_DATE_MIN_YEAR", "1900"))
_DATE_MAX_YEAR_OFFSET: int = int(os.environ.get("DIC_DATE_MAX_YEAR_OFFSET", "5"))


def _detect_date_anomaly(date_str: str) -> str | None:
    """Return an anomaly signal if a parsed ISO date is implausible, else None.

    Checks the extracted date year against the configurable range
    [_DATE_MIN_YEAR, current_year + _DATE_MAX_YEAR_OFFSET].
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return "date_invalid_format"
    current_year = datetime.now(timezone.utc).year
    if dt.year < _DATE_MIN_YEAR:
        return f"date_too_old:year={dt.year}<{_DATE_MIN_YEAR}"
    max_year = current_year + _DATE_MAX_YEAR_OFFSET
    if dt.year > max_year:
        return f"date_too_future:year={dt.year}>{max_year}"
    return None


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

        date_anomaly: str | None = _detect_date_anomaly(date_str) if date_str else None

        return {
            "document_type": doc_type,
            "tags": tags,
            "date": date_str,
            "date_anomaly": date_anomaly,
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
_IDENTIFIER_MIN_CONFIDENCE: float = float(os.environ.get("DIC_IDENTIFIER_MIN_CONFIDENCE", "0.70"))

_IDENTIFIER_MAX_ITEMS: int = int(os.environ.get("DIC_IDENTIFIER_MAX_ITEMS", "8"))
_IDENTIFIER_VALUE_MAX_LEN: int = int(os.environ.get("DIC_IDENTIFIER_VALUE_MAX_LEN", "64"))

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


# Identifier extraction anomaly detection (aiify-opp-6 / aiify-rm-a3344-phase-7:
# hardcoded_threshold -> anomaly_detection). The external scan flagged paperless-ngx
# src/documents/barcodes.py — the barcode/QR reader that accepts or rejects a
# decoded barcode against hardcoded confidence thresholds.  A fixed cutoff is
# brittle: a high-contrast barcode always scores 1.0, while a faded one may
# score below threshold even when the decode is correct.
#
# The ICDEV analog lands here, applied to the LLM-based identifier extractor.
# The extractor already gates on the configurable _IDENTIFIER_MIN_CONFIDENCE
# floor; this adds a second-layer *pattern* check on the extraction result as a
# whole, detecting signals that the LLM may be over-generating or producing
# artificially uniform scores — anomalies that a single fixed threshold
# cannot catch:
#   cap_hit             – extracted exactly _IDENTIFIER_MAX_ITEMS items; the
#                         model may have kept generating rather than stopping at a
#                         lower natural count.
#   uniform_confidence  – all items share the same rounded per-item confidence
#                         (copy-paste behavior; real documents rarely have
#                         identical per-code scores).
#   over_confidence     – every item reports confidence ≥ 0.99 (suspiciously
#                         perfect scores signal the model is not genuinely
#                         discriminating between identifiers).
# The signal is advisory only — a HITL flag, never a blocker.
def _detect_identifier_anomaly(identifiers: list[dict]) -> str | None:
    """Return an anomaly signal if the identifier extraction result looks suspicious.

    Returns the first signal found from {cap_hit, uniform_confidence,
    over_confidence}, or None when no anomaly is detected.  Callers MUST treat
    this as advisory — a signal does not mean the identifiers are wrong, only
    that they warrant extra HITL scrutiny.
    """
    if not identifiers:
        return None
    if len(identifiers) >= _IDENTIFIER_MAX_ITEMS:
        return "cap_hit"
    confs = [round(float(item.get("confidence") or 0.0), 2) for item in identifiers]
    if len(confs) >= 2 and len(set(confs)) == 1:
        return "uniform_confidence"
    if all(c >= 0.99 for c in confs):
        return "over_confidence"
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
_CLASSIFY_MIN_CONFIDENCE: float = float(os.environ.get("DIC_CLASSIFY_MIN_CONFIDENCE", "0.70"))

# Bound how large a taxonomy we will offer the model, and how many labels a
# multi-label classification may return.
_CLASSIFY_MAX_LABELS: int = int(os.environ.get("DIC_CLASSIFY_MAX_LABELS", "60"))
_CLASSIFY_LABEL_MAX_LEN: int = int(os.environ.get("DIC_CLASSIFY_LABEL_MAX_LEN", "80"))
_CLASSIFY_MAX_SELECTED: int = int(os.environ.get("DIC_CLASSIFY_MAX_SELECTED", "5"))

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
# Classifier anomaly detection (aiify-rm-a3344-phase-18:
# hardcoded_threshold -> anomaly_detection). The external scan flagged
# paperless-ngx src/documents/classifier.py — the AUTO matching algorithm
# that gates document filing on fixed per-algorithm confidence thresholds.
# A static cutoff is brittle: a trivial taxonomy always looks high-confidence,
# a borderline decision sits just above the floor without any signal, and a
# cap-hit on multi-label may reflect over-generation rather than a real match.
#
# The ICDEV analog lands here, applied to _ai_classify_into_taxonomy results.
# Three advisory signals (never blocking, HITL-only):
#   max_labels_hit      – result contains exactly _CLASSIFY_MAX_SELECTED labels
#                         (cap hit; model may be filling the list rather than
#                         genuinely matching all selected labels).
#   borderline_confidence – overall confidence falls within a narrow band
#                         [floor, floor + _CLASSIFY_BORDER_BAND) just above the
#                         minimum; borderline decisions are more likely to flip
#                         under paraphrase.
#   trivial_taxonomy    – the caller's normalized taxonomy contained only one
#                         usable label; no real choice was made and the signal is
#                         not informative.
# --------------------------------------------------------------------------- #

# Width of the "borderline" band above _CLASSIFY_MIN_CONFIDENCE.  A confidence
# of exactly floor + epsilon is treated as a borderline signal; wider means more
# results flagged.
_CLASSIFY_BORDER_BAND: float = float(
    os.environ.get("DIC_CLASSIFY_BORDER_BAND", "0.05")
)


def _detect_classify_anomaly(result: dict, taxonomy) -> str | None:
    """Return the first anomaly signal found in a classify result, or None.

    Checks (in order): max_labels_hit, borderline_confidence, trivial_taxonomy.
    Returns the first matching signal string, or None when the result looks
    clean.  Callers MUST treat any returned signal as advisory — it does not
    mean the classification is wrong, only that it warrants extra HITL scrutiny.

    Args:
        result: the dict returned by ``_ai_classify_into_taxonomy`` — must
            contain ``"labels"`` (list) and ``"confidence"`` (float).
        taxonomy: the caller's raw taxonomy iterable, passed through
            ``_normalize_taxonomy`` to determine how many usable labels existed.
    """
    if not result:
        return None
    labels = result.get("labels") or []
    confidence = result.get("confidence")
    if not isinstance(confidence, (int, float)):
        return None

    # Cap hit: returned exactly the maximum allowed labels.
    if len(labels) >= _CLASSIFY_MAX_SELECTED:
        return "max_labels_hit"

    # Borderline: confidence just above the floor — more likely to flip.
    if confidence < _CLASSIFY_MIN_CONFIDENCE + _CLASSIFY_BORDER_BAND:
        return "borderline_confidence"

    # Trivial: only one label was on offer, so there was no real choice.
    if len(_normalize_taxonomy(taxonomy)) <= 1:
        return "trivial_taxonomy"

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
_CORRESPONDENCE_MIN_CONFIDENCE: float = float(os.environ.get("DIC_CORRESPONDENCE_MIN_CONFIDENCE", "0.70"))

# Bound recipient fan-out and the length of any single party / subject value.
_CORRESPONDENCE_MAX_RECIPIENTS: int = int(os.environ.get("DIC_CORRESPONDENCE_MAX_RECIPIENTS", "25"))
_CORRESPONDENCE_NAME_MAX_LEN: int = int(os.environ.get("DIC_CORRESPONDENCE_NAME_MAX_LEN", "120"))
_CORRESPONDENCE_SUBJECT_MAX_LEN: int = int(os.environ.get("DIC_CORRESPONDENCE_SUBJECT_MAX_LEN", "300"))

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

        sent_date_anomaly: str | None = _detect_date_anomaly(sent_date) if sent_date else None

        return {
            "from_name": from_name,
            "from_email": from_email,
            "to": recipients,
            "subject": subject,
            "sent_date": sent_date,
            "sent_date_anomaly": sent_date_anomaly,
            "confidence": round(confidence, 4),
        }
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Metadata anomaly detection (aiify-opp-77: hardcoded_threshold ->
# anomaly_detection). The serialisers.py analog in DIC: inspect extraction
# quality signals and flag documents that look suspicious or malformed so the
# ingest caller can surface them as HITL review candidates rather than silently
# storing low-quality content.
#
# Design mirrors the rest of the ingest enrichment pipeline:
#   - deterministic pre-checks first (empty text, binary content, ratio outliers)
#   - optional LLM scoring when a pre-check fires (best-effort, degrades to None)
#   - result is a HITL proposal in IngestOutcome.metadata["anomaly_report"];
#     never blocking, never raises
# --------------------------------------------------------------------------- #

# Minimum printable-character ratio below which content is flagged as binary-like.
_ANOMALY_MIN_PRINTABLE_RATIO: float = float(
    os.environ.get("DIC_ANOMALY_MIN_PRINTABLE_RATIO", "0.85")
)

# Minimum text length (stripped chars) before we flag "near-empty extraction".
_ANOMALY_MIN_TEXT_LEN: int = int(os.environ.get("DIC_ANOMALY_MIN_TEXT_LEN", "50"))

# Maximum chars-per-page ratio; above this a single page appears implausibly large.
_ANOMALY_MAX_CHARS_PER_PAGE: int = int(
    os.environ.get("DIC_ANOMALY_MAX_CHARS_PER_PAGE", "20000")
)

# Minimum chars-per-page ratio; below this most pages appear blank (OCR failure?).
_ANOMALY_MIN_CHARS_PER_PAGE: int = int(
    os.environ.get("DIC_ANOMALY_MIN_CHARS_PER_PAGE", "10")
)

# Duplex-scan artifact detection (aiify-opp-27: hardcoded_threshold ->
# anomaly_detection). The external scan flagged paperless-ngx
# src/documents/double_sided.py, which uses a hardcoded blank-page threshold
# (e.g. pixels below a fixed ink-density cutoff) to decide whether a page was
# a blank backing sheet produced by duplex scanning of single-sided documents.
# That cutoff is brittle: it ignores document type, scanner settings, and batch
# characteristics, so it both over-triggers (stamps / watermarks flagged blank)
# and under-triggers (light ink passes unchecked).
#
# The ICDEV analog lands here, in the DIC extraction-quality pre-check suite.
# Instead of a fixed absolute cutoff the detector fires when:
#   chars_per_page < _ANOMALY_MIN_CHARS_PER_PAGE × _ANOMALY_DUPLEX_CPP_RATIO
# That is: when average content per page is below a *ratio* of the already
# configurable chars-per-page floor, not a second independently hardcoded
# constant.  Both operands are env-var-backed so the operator can tune once
# and both thresholds adjust together.  The parity guard (page_count even, ≥4)
# is a low-cost discriminator: duplex scanning always produces an even page
# count, while odd totals rule out the artifact pattern entirely.  The LLM
# score call — already triggered whenever any pre-check fires — then provides
# the holistic assessment, so the decision is never left to a single number.
_ANOMALY_DUPLEX_CPP_RATIO: float = float(
    os.environ.get("DIC_ANOMALY_DUPLEX_CPP_RATIO", "3.0")
)

_ANOMALY_SYSTEM_PROMPT = (
    "You are a document quality inspector. Given signals about a document's "
    "extraction quality, rate the overall anomaly score from 0.0 (normal) to "
    "1.0 (highly suspicious / corrupt). Respond with a strict JSON object and "
    "nothing else: "
    '{"score": <0.0-1.0>, "verdict": "normal"|"suspicious"|"anomalous", '
    '"reason": "<one short sentence>"}. '
    "Use 0.0-0.3 for normal documents, 0.3-0.7 for suspicious, 0.7-1.0 for "
    "likely corrupt or malformed. "
    "A 'possible_duplex_artifact' signal means the document may have been "
    "scanned in duplex mode with blank backing pages included — score 0.4-0.7 "
    "unless other signals also fire."
)


def _ai_metadata_anomaly_detection(extraction: "Extraction", filename: str) -> dict | None:
    """Inspect extraction quality and flag anomalous documents via LLM scoring.

    Deterministic pre-checks run first; the LLM is only consulted when at least
    one pre-check fires, keeping this cheap for the common normal case.

    Returns a dict with ``score``, ``verdict``, ``reason``, and ``signals``, or
    ``None`` when all pre-checks pass (normal document) or on any failure. Callers
    MUST treat ``None`` as "no anomaly detected" and proceed with ingestion unchanged.
    """
    text = (extraction.text or "").strip()
    signals: list[str] = []

    # Pre-check 1: near-empty extraction.
    if len(text) < _ANOMALY_MIN_TEXT_LEN:
        signals.append(f"near_empty_text:len={len(text)}")

    # Pre-check 2: binary-like content (high proportion of non-printable chars).
    if text:
        printable = sum(1 for c in text if c.isprintable() or c in "\n\r\t")
        ratio = printable / len(text)
        if ratio < _ANOMALY_MIN_PRINTABLE_RATIO:
            signals.append(f"binary_content:printable_ratio={ratio:.2f}")

    # Pre-check 3: chars-per-page ratio outlier.
    if extraction.page_count > 0 and text:
        cpp = len(text) / extraction.page_count
        if cpp > _ANOMALY_MAX_CHARS_PER_PAGE:
            signals.append(f"chars_per_page_high:{cpp:.0f}>{_ANOMALY_MAX_CHARS_PER_PAGE}")
        elif cpp < _ANOMALY_MIN_CHARS_PER_PAGE:
            signals.append(f"chars_per_page_low:{cpp:.1f}<{_ANOMALY_MIN_CHARS_PER_PAGE}")

    # Pre-check 4: extraction warnings already surfaced by the provider.
    if extraction.warnings:
        signals.append(f"provider_warnings:{len(extraction.warnings)}")

    # Pre-check 5: possible duplex-scan artifact (aiify-opp-27).
    # Even page count ≥ 4 AND chars_per_page below the adaptive duplex floor
    # (a ratio of the configurable chars-per-page minimum rather than a second
    # independent hardcoded constant) indicates potential blank backing pages.
    if extraction.page_count >= 4 and extraction.page_count % 2 == 0 and text:
        cpp = len(text) / extraction.page_count
        duplex_floor = _ANOMALY_MIN_CHARS_PER_PAGE * _ANOMALY_DUPLEX_CPP_RATIO
        if cpp < duplex_floor:
            signals.append(
                f"possible_duplex_artifact:cpp={cpp:.1f}<{duplex_floor:.0f}"
                f",pages={extraction.page_count}(even)"
            )

    # No signals → normal document; skip the LLM call entirely.
    if not signals:
        return None

    # At least one signal — ask the LLM for a holistic quality score.
    signal_text = "; ".join(signals)
    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter
        import json as _json

        prompt = (
            f"File: {filename}\n"
            f"Provider: {extraction.provider}\n"
            f"Content-type: {extraction.content_type}\n"
            f"Pages: {extraction.page_count}\n"
            f"Text length (chars): {len(text)}\n"
            f"Quality signals: {signal_text}\n\n"
            "Rate the anomaly score for this document."
        )
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=_ANOMALY_SYSTEM_PROMPT,
            max_tokens=128,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("anomaly_detection", req)
        if not resp or not resp.content:
            raise ValueError("empty response")
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
        parsed = _json.loads(raw)
        score = float(parsed.get("score", 0.5))
        verdict = str(parsed.get("verdict", "suspicious"))
        reason = str(parsed.get("reason", ""))
        if verdict not in {"normal", "suspicious", "anomalous"}:
            verdict = "suspicious"
    except Exception:
        score = 0.5
        verdict = "suspicious"
        reason = "llm_unavailable"

    return {
        "score": round(score, 4),
        "verdict": verdict,
        "reason": reason,
        "signals": signals,
    }


# --------------------------------------------------------------------------- #
# Email-specific ingestion anomaly detection (aiify-opp-148: hardcoded_threshold
# -> anomaly_detection). The external scan flagged paperless-ngx
# src/paperless_mail/serialisers.py — the MailAccount / MailRule DRF serialisers
# that validate email ingestion inputs with static field limits (maximum age,
# attachment count caps, subject length bounds). Those fixed constants are
# brittle: they are tuned once at install time, don't adapt to the actual volume
# or content distribution of a mailbox, and silently over- or under-filter as
# mailbox behaviour evolves.
#
# The ICDEV analog lands here, alongside the existing extraction-quality checks.
# Instead of a single hardcoded ceiling the detector:
#   1. Evaluates five deterministic pre-checks (attachment count, attachment
#      size, email age, subject length, missing sender) against env-var-backed
#      thresholds — both operands are configurable so the operator tunes once.
#   2. Only calls the LLM when at least one pre-check fires, keeping the cost
#      zero for the common normal case.
#   3. Returns a structured anomaly report as a *proposal* — never blocking,
#      never stored without HITL confirmation.
#
# Caller: _enrich_ingest_outcome() adds the result (when not None) to
# IngestOutcome.metadata["email_anomaly_report"] so the HITL / API layer
# can surface it without changing the ingest result itself.
# --------------------------------------------------------------------------- #

_EMAIL_ANOMALY_MAX_ATTACHMENT_COUNT: int = int(
    os.environ.get("DIC_EMAIL_ANOMALY_MAX_ATTACHMENT_COUNT", "10")
)
_EMAIL_ANOMALY_MAX_ATTACHMENT_SIZE_MB: float = float(
    os.environ.get("DIC_EMAIL_ANOMALY_MAX_ATTACHMENT_SIZE_MB", "25.0")
)
_EMAIL_ANOMALY_MAX_AGE_DAYS: int = int(
    os.environ.get("DIC_EMAIL_ANOMALY_MAX_AGE_DAYS", "365")
)
_EMAIL_ANOMALY_MAX_SUBJECT_LEN: int = int(
    os.environ.get("DIC_EMAIL_ANOMALY_MAX_SUBJECT_LEN", "500")
)

_EMAIL_ANOMALY_SYSTEM_PROMPT = (
    "You are an email security and quality inspector. Given signals about an "
    "email's ingestion characteristics, rate the overall anomaly score from 0.0 "
    "(normal) to 1.0 (highly suspicious or malformed). Respond with a strict JSON "
    "object and nothing else: "
    '{"score": <0.0-1.0>, "verdict": "normal"|"suspicious"|"anomalous", '
    '"reason": "<one short sentence>"}. '
    "Score 0.0-0.3 for normal emails, 0.3-0.7 for suspicious, 0.7-1.0 for "
    "likely malicious, corrupt, or mis-classified emails."
)


def _ai_email_ingestion_anomaly_detection(
    correspondence: "dict | None",
    attachment_count: int = 0,
    attachment_size_bytes: int = 0,
    email_age_days: "float | None" = None,
) -> "dict | None":
    """Detect anomalous email ingestion patterns via configurable thresholds + LLM.

    Analog of the hardcoded field validators in paperless_mail/serialisers.py
    (aiify-opp-148). Replaces static ceilings with env-var-backed thresholds that
    trigger an LLM quality score only when a pre-check fires.

    Args:
        correspondence: structured envelope dict from _ai_extract_correspondence_fields
            (keys: from_name, from_email, to, subject, sent_date, confidence), or None.
        attachment_count: number of attachments on the email.
        attachment_size_bytes: total size of all attachments in bytes.
        email_age_days: age of the email in days at ingestion time, or None if unknown.

    Returns:
        dict with ``score`` (0.0–1.0), ``verdict``, ``reason``, and ``signals`` when
        at least one pre-check fires; ``None`` when all pre-checks pass (normal email).
        Never raises — degrades to ``None`` on any failure so the caller can proceed.
    """
    signals: list[str] = []

    # Pre-check 1: attachment count exceeds configurable ceiling.
    if attachment_count > _EMAIL_ANOMALY_MAX_ATTACHMENT_COUNT:
        signals.append(
            f"high_attachment_count:{attachment_count}>{_EMAIL_ANOMALY_MAX_ATTACHMENT_COUNT}"
        )

    # Pre-check 2: total attachment size exceeds configurable ceiling.
    size_mb = attachment_size_bytes / (1024 * 1024)
    if size_mb > _EMAIL_ANOMALY_MAX_ATTACHMENT_SIZE_MB:
        signals.append(
            f"large_attachments:{size_mb:.1f}MB>{_EMAIL_ANOMALY_MAX_ATTACHMENT_SIZE_MB}MB"
        )

    # Pre-check 3: email age outlier (analog of MailRule.maximum_age validator).
    if email_age_days is not None and email_age_days > _EMAIL_ANOMALY_MAX_AGE_DAYS:
        signals.append(
            f"stale_email:{email_age_days:.0f}d>{_EMAIL_ANOMALY_MAX_AGE_DAYS}d"
        )

    # Pre-check 4: subject length outlier (analog of subject max_length).
    subject = ""
    if correspondence:
        subject = str(correspondence.get("subject", "") or "")
        if len(subject) > _EMAIL_ANOMALY_MAX_SUBJECT_LEN:
            signals.append(
                f"long_subject:{len(subject)}>{_EMAIL_ANOMALY_MAX_SUBJECT_LEN}"
            )

    # Pre-check 5: completely missing sender (from_name AND from_email absent).
    if correspondence:
        from_email = str(correspondence.get("from_email", "") or "")
        from_name = str(correspondence.get("from_name", "") or "")
        if not from_email and not from_name:
            signals.append("missing_sender")

    # No signals → normal email; skip the LLM call entirely.
    if not signals:
        return None

    # At least one signal — ask the LLM for a holistic anomaly score.
    signal_text = "; ".join(signals)
    age_line = f"Age: {email_age_days:.0f} days\n" if email_age_days is not None else ""
    prompt = (
        f"Email signals: {signal_text}\n"
        f"Subject: {subject[:100]!r}\n"
        f"Attachments: {attachment_count} ({size_mb:.1f} MB total)\n"
        f"{age_line}"
        "Rate the anomaly score for this email."
    )
    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter
        import json as _json

        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=_EMAIL_ANOMALY_SYSTEM_PROMPT,
            max_tokens=128,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("anomaly_detection", req)
        if not resp or not resp.content:
            raise ValueError("empty response")
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
        parsed = _json.loads(raw)
        score = float(parsed.get("score", 0.5))
        verdict = str(parsed.get("verdict", "suspicious"))
        reason = str(parsed.get("reason", ""))
        if verdict not in {"normal", "suspicious", "anomalous"}:
            verdict = "suspicious"
    except Exception:
        score = 0.5
        verdict = "suspicious"
        reason = "llm_unavailable"

    return {
        "score": round(score, 4),
        "verdict": verdict,
        "reason": reason,
        "signals": signals,
    }


# --------------------------------------------------------------------------- #
# LLM workflow mutation proposals (aiify-opp-111: metadata_extraction ->
# llm_generation). The external scan flagged paperless-ngx
# src/documents/workflows/mutations.py — the workflow action executor that
# assigns document metadata (custom fields, storage path, correspondent,
# document type, tags) via static per-workflow mutation rules. The repo is
# ephemeral, so per the established aiify-opp pattern the augmentation lands
# in the analogous ICDEV subsystem (DIC).
#
# This is deliberately distinct from the existing enrichment helpers:
#   - aiify-opp-6086 (_ai_metadata_extraction): one-shot open-vocabulary
#     extraction (document_type from a fixed module-level enum, topic tags,
#     date). Fires at ingest time; the field set is hardcoded.
#   - aiify-opp-6043 (_ai_classify_into_taxonomy): filing into a
#     caller-supplied taxonomy of *existing* labels (``matching_algorithm =
#     AUTO`` analog). Single or multi-label; never invents a label.
#
# This helper models the workflow *mutation* layer — the caller passes a
# structured definition of the custom metadata fields their workflow wants to
# populate (field name, type, optional select options, optional description).
# The model proposes typed values for each field from the document text and
# additionally suggests a storage-path classification. It is the LLM analog of
# a hand-curated per-document-type mutation rule-set.
#
# Grounding + safety (mirrors the 6086/5988/6043/6100 designs):
#   - ``select`` fields: proposed value must be one of the caller-supplied
#     options (membership guard, case-folded); anything else is dropped.
#   - ``boolean`` fields: parsed from the model's string/bool token; only
#     True/False accepted.
#   - ``date`` fields: must be a real ISO (YYYY-MM-DD) calendar date or
#     dropped.
#   - ``integer``/``monetary`` fields: must parse to int/float; clamped to
#     sensible bounds.
#   - ``url`` fields: shape-validated (must contain ://); alphanumeric core
#     must appear in the source text (anti-hallucination).
#   - ``string`` fields: length-capped; alphanumeric core must appear in the
#     source text (anti-hallucination).
#   - ``storage_path``: path-style string, length-capped; no membership guard
#     (the value is proposed, not drawn from an existing set).
#   - a per-field confidence and an overall confidence gate: below
#     _MUTATION_MIN_CONFIDENCE the whole suggestion is discarded (HITL).
#   - the result is surfaced as a *proposal* under IngestOutcome.metadata
#     ["workflow_mutations"] — never silently written to dic_documents — so a
#     human or downstream workflow engine confirms before applying.
#   - any failure / unavailability degrades to None (air-gap safe); the caller
#     proceeds with ingestion unchanged.
# --------------------------------------------------------------------------- #

# Typed field kinds the mutation engine recognises. Constrains inputs so the
# caller's type annotation is validated before the LLM call, and prevents an
# out-of-set type from leaking into the prompt.
_MUTATION_FIELD_TYPES = frozenset(
    {"string", "integer", "monetary", "date", "boolean", "url", "select"}
)

# Only the leading slice carries the field-value signal; keep the call cheap.
_MUTATION_INPUT_CHARS = 6000

# Below this overall confidence the whole proposal is discarded (HITL path).
_MUTATION_MIN_CONFIDENCE: float = float(
    os.environ.get("DIC_MUTATION_MIN_CONFIDENCE", "0.70")
)

# Per-field confidence threshold (items below this are silently dropped).
_MUTATION_FIELD_MIN_CONFIDENCE: float = float(
    os.environ.get("DIC_MUTATION_FIELD_MIN_CONFIDENCE", "0.65")
)

# Bound on string/url values and storage-path length.
_MUTATION_STRING_MAX_LEN: int = int(os.environ.get("DIC_MUTATION_STRING_MAX_LEN", "256"))
_MUTATION_STORAGE_PATH_MAX_LEN: int = int(
    os.environ.get("DIC_MUTATION_STORAGE_PATH_MAX_LEN", "512")
)

# Cap how many custom fields the model is asked about in a single call.
_MUTATION_MAX_FIELDS: int = int(os.environ.get("DIC_MUTATION_MAX_FIELDS", "20"))

# Upper/lower bounds on integer and monetary values (reasonable document-domain
# range; the model cannot propose a value outside these bounds).
_MUTATION_INT_MIN: int = -1_000_000
_MUTATION_INT_MAX: int = 1_000_000_000
_MUTATION_MONETARY_MIN: float = 0.0
_MUTATION_MONETARY_MAX: float = 1_000_000_000.0

_MUTATION_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+\-.]*://\S{1,200}$")

# --------------------------------------------------------------------------- #
# Near-duplicate detection (aiify-opp-45: hardcoded_threshold ->
# anomaly_detection). The external scan flagged paperless-ngx
# src/documents/management/commands/document_fuzzy_match.py — a Django
# management command that performs fuzzy title/content matching between
# documents to surface near-duplicates. It uses a hardcoded similarity
# threshold (e.g. 0.75) to decide whether two documents are "similar enough"
# to flag. The repo is ephemeral, so per the established aiify-opp pattern
# the augmentation lands in the analogous ICDEV subsystem (DIC).
#
# This is deliberately distinct from the content-hash idempotency dedup already
# in the ingestion pipeline (exact SHA-256 equality) and from the intra-doc
# duplicate-block detection (aiify-opp-5984). It operates BETWEEN documents in
# the same collection and uses Jaccard similarity on title tokens as the
# proximity signal.
#
# Instead of a hardcoded threshold, IQR-based outlier detection is applied to
# the distribution of similarity scores across the collection window: a pair is
# flagged only when its score is anomalously high relative to the pack
# (> Q3 + _NEAR_DUP_IQR_FENCE × IQR). This adapts automatically to each
# collection's vocabulary and density — no manual threshold needs to be
# maintained.
#
# Grounding + safety (mirrors the 5984/6043 design & ICDEV AI-security posture):
#   - Only title tokens are compared (stored in dic_documents.title); raw chunk
#     text is never re-fetched, keeping the check fast and DB-light.
#   - The result is surfaced as a *proposal* under IngestOutcome.metadata
#     ["near_duplicates"] — never silently merged or archived — so a human
#     confirms the filing before any action is taken.
#   - A minimum-tokens guard (_NEAR_DUP_MIN_TOKENS) skips untitled / very short
#     documents where Jaccard scores are noise-dominated.
#   - The comparison window is capped (_NEAR_DUP_WINDOW) so the check is O(1)
#     in collection size.
#   - Any failure degrades to an empty list (air-gap safe); ingestion proceeds
#     unchanged.
# --------------------------------------------------------------------------- #

# Max number of existing documents to compare against (ordered by recency).
_NEAR_DUP_WINDOW: int = int(os.environ.get("DIC_NEAR_DUP_WINDOW", "200"))

# Minimum title-token count below which comparison is skipped (noise guard).
_NEAR_DUP_MIN_TOKENS: int = int(os.environ.get("DIC_NEAR_DUP_MIN_TOKENS", "4"))

# IQR fence multiplier: scores above Q3 + fence × IQR are flagged.
_NEAR_DUP_IQR_FENCE: float = float(os.environ.get("DIC_NEAR_DUP_IQR_FENCE", "1.5"))


def _detect_near_duplicate_titles(
    doc_id: str,
    title: str,
    collection_id: str,
    conn,
) -> list[dict]:
    """Detect near-duplicate documents by title similarity using IQR anomaly detection.

    The fuzzy-match analog of paperless document_fuzzy_match.py. Instead of a
    hardcoded similarity threshold, IQR-based outlier detection adapts to each
    collection's own score distribution: a candidate is flagged only when its
    Jaccard similarity is anomalously high relative to the pack.

    Args:
        doc_id: the newly-ingested document's id (excluded from comparison).
        title: the document's title string (token source).
        collection_id: limits comparison to documents in the same collection.
        conn: open DB connection (must be positioned after the new doc is written).

    Returns:
        List of ``{"doc_id": str, "filename": str, "title": str,
        "similarity": float}`` for candidates whose score clears the IQR fence,
        sorted descending by similarity. Empty when the collection is too small,
        the title is too short, or any error occurs. Never raises.
    """
    try:
        tokens_a = set((title or "").lower().split())
        if len(tokens_a) < _NEAR_DUP_MIN_TOKENS:
            return []

        rows = conn.execute(
            "SELECT doc_id, filename, title FROM dic_documents "
            "WHERE collection_id = ? AND doc_id != ? AND title IS NOT NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (collection_id, doc_id, _NEAR_DUP_WINDOW),
        ).fetchall()

        if len(rows) < 2:
            return []

        # Compute Jaccard similarity for each candidate against the new title.
        scored: list[tuple[str, str, str, float]] = []
        for row in rows:
            cand_id = row[0] if hasattr(row, "__getitem__") else row["doc_id"]
            cand_fname = row[1] if hasattr(row, "__getitem__") else row.get("filename", "") or ""
            cand_title = row[2] if hasattr(row, "__getitem__") else row.get("title", "") or ""
            tokens_b = set(cand_title.lower().split())
            if len(tokens_b) < _NEAR_DUP_MIN_TOKENS:
                continue
            union = tokens_a | tokens_b
            score = len(tokens_a & tokens_b) / len(union) if union else 0.0
            scored.append((cand_id, cand_fname, cand_title, score))

        if len(scored) < 2:
            return []

        # IQR-based outlier fence — no hardcoded similarity threshold.
        scores_only = sorted(s for _, _, _, s in scored)
        n = len(scores_only)
        q1 = scores_only[n // 4]
        q3 = scores_only[(3 * n) // 4]
        iqr = q3 - q1
        fence = q3 + _NEAR_DUP_IQR_FENCE * iqr

        candidates = [
            {
                "doc_id": cid,
                "filename": fname,
                "title": ctitle,
                "similarity": round(score, 4),
            }
            for cid, fname, ctitle, score in scored
            if score > fence
        ]
        return sorted(candidates, key=lambda c: c["similarity"], reverse=True)
    except Exception:
        return []


def _mutation_field_spec_line(field: dict) -> str:
    """Format a single field definition as a compact prompt-safe line."""
    name = str(field.get("name") or "").strip()
    ftype = str(field.get("type") or "string").strip().lower()
    desc = str(field.get("description") or "").strip()
    opts = field.get("options") or []
    parts = [f'"{name}" ({ftype})']
    if opts:
        choices = ", ".join(f'"{o}"' for o in opts[:20])
        parts.append(f"choices: [{choices}]")
    if desc:
        parts.append(f"— {desc[:120]}")
    return " ".join(parts)


def _ai_propose_workflow_mutations(
    text: str,
    custom_fields: list[dict],
    *,
    filename: str = "",
) -> dict | None:
    """Propose typed custom-field values and a storage path from ``text`` via LLM.

    The LLM-generation analog of paperless ``workflows/mutations.py``
    (aiify-opp-111): rather than hand-curated per-workflow mutation rules,
    the model reads the document text and proposes which values to assign to
    the caller's custom metadata fields.

    Args:
        text: full extracted document text; only the leading
            ``_MUTATION_INPUT_CHARS`` characters are sent (cheap, bounded).
        custom_fields: list of field definitions, each a dict with keys:
            ``name`` (str, required), ``type`` (one of the ``_MUTATION_FIELD_TYPES``;
            default ``"string"``), ``options`` (list[str] for ``select`` type),
            ``description`` (str, optional hint for the model).
            Fields with blank names or unsupported types are skipped.
            At most ``_MUTATION_MAX_FIELDS`` fields are passed to the model.
        filename: weak context; the model is told to ground on the text.

    Returns:
        ``{"mutations": [{"field": str, "value": Any, "confidence": float}],
        "storage_path": str | None, "confidence": float}`` where every
        ``value`` has been type-validated and (for string/url) grounding-
        checked against the source text. Returns ``None`` when the text is
        empty, no usable field definitions are provided, the model output is
        unusable, or overall confidence is below ``_MUTATION_MIN_CONFIDENCE``.
        Callers MUST treat ``None`` as "no mutations proposed" and proceed
        unchanged — this is a HITL proposal, never silently persisted.
    """
    snippet = (text or "").strip()
    if not snippet:
        return None

    # Normalize and bound the field list.
    valid_fields: list[dict] = []
    for fdef in custom_fields or []:
        if not isinstance(fdef, dict):
            continue
        name = str(fdef.get("name") or "").strip()
        if not name:
            continue
        ftype = str(fdef.get("type") or "string").strip().lower()
        if ftype not in _MUTATION_FIELD_TYPES:
            continue
        opts: list[str] = []
        if ftype == "select":
            opts = [str(o).strip() for o in (fdef.get("options") or []) if str(o).strip()]
            if not opts:
                continue  # select with no options is unusable
        valid_fields.append(
            {
                "name": name,
                "type": ftype,
                "options": opts,
                "description": str(fdef.get("description") or "").strip()[:120],
            }
        )
        if len(valid_fields) >= _MUTATION_MAX_FIELDS:
            break

    if not valid_fields:
        return None

    snippet = snippet[:_MUTATION_INPUT_CHARS]
    haystack = "".join(ch for ch in snippet if ch.isalnum()).lower()

    fields_block = "\n".join(
        f"  {i + 1}. {_mutation_field_spec_line(f)}"
        for i, f in enumerate(valid_fields)
    )
    system_prompt = (
        "You propose metadata field values for a single ingested document in a "
        "document-management workflow. You are given a list of custom fields "
        "(name, type, optional choices, optional description) and the document "
        "text. For each field, propose a typed value grounded in the text. Use "
        "ONLY information present in the text — never invent a value not supported "
        "by it. Respond with a strict JSON object and nothing else, of the form:\n"
        '{"mutations": [{"field": "<name>", "value": <typed value>, '
        '"confidence": <0..1>}], '
        '"storage_path": "<suggested/storage/path or null>", '
        '"confidence": <0..1 overall>}.\n'
        "Rules per type: string → a short quoted string; integer → a bare integer; "
        "monetary → a bare number (no currency symbol); date → YYYY-MM-DD or null; "
        "boolean → true or false; url → a full URL including scheme; "
        'select → one of the listed choices exactly. '
        "Omit a field from the mutations list rather than guessing. "
        "Return low confidence when evidence is weak."
    )

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
                        f"Custom fields to populate:\n{fields_block}\n\n"
                        "Document text (leading excerpt):\n"
                        f"{snippet}\n\n"
                        "Produce the mutations JSON."
                    ),
                }
            ],
            system_prompt=system_prompt,
            max_tokens=512,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("summarization", req)
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

        # Overall confidence gate.
        try:
            overall = float(parsed.get("confidence"))
        except (TypeError, ValueError):
            return None
        if overall < _MUTATION_MIN_CONFIDENCE:
            return None

        # Build a name→field-def lookup for validation.
        field_map = {f["name"]: f for f in valid_fields}

        mutations: list[dict] = []
        seen_names: set[str] = set()
        for item in parsed.get("mutations") or []:
            if not isinstance(item, dict):
                continue
            fname = str(item.get("field") or "").strip()
            if not fname or fname not in field_map or fname in seen_names:
                continue
            try:
                item_conf = float(item.get("confidence"))
            except (TypeError, ValueError):
                item_conf = overall
            if item_conf < _MUTATION_FIELD_MIN_CONFIDENCE:
                continue

            fdef = field_map[fname]
            ftype = fdef["type"]
            raw_val = item.get("value")
            validated = _validate_mutation_value(
                raw_val, ftype, fdef["options"], haystack
            )
            if validated is None:
                continue
            seen_names.add(fname)
            mutations.append(
                {"field": fname, "value": validated, "confidence": round(item_conf, 4)}
            )

        # Storage path: length-cap; no grounding guard (it is a proposed path,
        # not a value drawn from the document text).
        storage_path: str | None = None
        sp_raw = parsed.get("storage_path")
        if isinstance(sp_raw, str) and sp_raw.strip():
            sp = sp_raw.strip()
            if len(sp) <= _MUTATION_STORAGE_PATH_MAX_LEN:
                storage_path = sp

        if not mutations and storage_path is None:
            return None

        return {
            "mutations": mutations,
            "storage_path": storage_path,
            "confidence": round(overall, 4),
        }
    except Exception:
        return None


def _validate_mutation_value(raw_val, ftype: str, opts: list[str], haystack: str):
    """Type-validate and grounding-check a single proposed mutation value.

    Returns the validated, typed value or ``None`` when validation fails.
    ``haystack`` is the alphanumeric-only, lower-cased source text for
    grounding checks (string/url types only).
    """
    if raw_val is None:
        return None

    if ftype == "boolean":
        if isinstance(raw_val, bool):
            return raw_val
        s = str(raw_val).strip().lower()
        if s in {"true", "1", "yes"}:
            return True
        if s in {"false", "0", "no"}:
            return False
        return None

    if ftype == "integer":
        try:
            v = int(raw_val)
        except (TypeError, ValueError):
            try:
                v = int(float(raw_val))
            except (TypeError, ValueError):
                return None
        if not (_MUTATION_INT_MIN <= v <= _MUTATION_INT_MAX):
            return None
        return v

    if ftype == "monetary":
        try:
            v = float(str(raw_val).replace(",", "").replace("$", "").strip())
        except (TypeError, ValueError):
            return None
        if not (_MUTATION_MONETARY_MIN <= v <= _MUTATION_MONETARY_MAX):
            return None
        return round(v, 2)

    if ftype == "date":
        s = str(raw_val).strip()
        if not s:
            return None
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return s
        except ValueError:
            return None

    if ftype == "select":
        s = str(raw_val).strip()
        canon = {o.casefold(): o for o in opts}
        matched = canon.get(s.casefold())
        return matched  # None when not in options

    if ftype == "url":
        s = str(raw_val).strip()
        if not s or len(s) > _MUTATION_STRING_MAX_LEN:
            return None
        if not _MUTATION_URL_RE.match(s):
            return None
        core = "".join(ch for ch in s if ch.isalnum()).lower()
        if not core or core[:20] not in haystack:
            return None
        return s

    # default: string
    s = str(raw_val).strip()
    if not s or len(s) > _MUTATION_STRING_MAX_LEN:
        return None
    core = "".join(ch for ch in s if ch.isalnum()).lower()
    if not core or core[:20] not in haystack:
        return None
    return s


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
    detect_anomalies: bool = True,
    detect_near_duplicates: bool = True,
    workflow_custom_fields: list[dict] | None = None,
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
        detect_anomalies: when True (default), inspect the extraction for quality
            anomalies — near-empty text, binary-like content, implausible
            chars-per-page ratios — and optionally score them with the LLM
            (aiify-opp-77). Surfaced as a HITL proposal in
            ``IngestOutcome.anomaly_report``; never blocking. Failures degrade
            silently to no anomaly report.
        detect_near_duplicates: when True (default), compute Jaccard title
            similarity between this document and recently-ingested documents in
            the same collection, then use IQR-based anomaly detection to flag
            pairs whose similarity is anomalously high relative to the
            collection's own score distribution — no hardcoded threshold
            (aiify-opp-45). Surfaced as a HITL proposal under
            ``IngestOutcome.metadata["near_duplicates"]``; never blocking and
            never auto-merged. Failures degrade silently to an empty list.
        workflow_custom_fields: optional list of custom field definitions, each
            a dict with ``name`` (str), ``type`` (``string``/``integer``/
            ``monetary``/``date``/``boolean``/``url``/``select``), optional
            ``options`` (list[str] for ``select`` type), optional
            ``description`` (hint for the model). When supplied, best-effort
            LLM proposal of typed values for each field extracted from the
            document text — the ``workflows/mutations.py`` analog (aiify-
            opp-111). Values are type-validated and (for string/url) grounding-
            checked against the source text (anti-hallucination). An optional
            ``storage_path`` suggestion is also returned. Surfaced as a HITL
            proposal under ``IngestOutcome.metadata["workflow_mutations"]``;
            never silently persisted. Default ``None`` leaves the feature off.
            Failures degrade silently to no mutation proposals.
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

    # Metadata anomaly detection (aiify-opp-77): inspect extraction quality and
    # flag suspicious documents as a HITL proposal before any enrichment runs.
    anomaly_report: dict | None = None
    if detect_anomalies:
        _emit("anomaly_check", "Checking extraction quality…", 6)
        anomaly_report = _ai_metadata_anomaly_detection(extraction, p.name)

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
            id_anomaly = _detect_identifier_anomaly(ids)
            if id_anomaly:
                ai_metadata = {**ai_metadata, "identifier_anomaly": id_anomaly}

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
            cls_anomaly = _detect_classify_anomaly(cls_result, classify_taxonomy)
            if cls_anomaly:
                ai_metadata = {**ai_metadata, "classification_anomaly": cls_anomaly}

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

    # LLM workflow mutation proposals (best-effort): propose typed values for
    # caller-defined custom metadata fields extracted from the document text —
    # the workflows/mutations.py analog (aiify-opp-111). Values are
    # type-validated and grounding-checked; surfaced as a HITL proposal under
    # metadata["workflow_mutations"], never silently written.
    if workflow_custom_fields and text.strip():
        _emit("workflow_mutations", "Proposing workflow metadata mutations…", 9)
        wm = _ai_propose_workflow_mutations(
            text, workflow_custom_fields, filename=p.name
        )
        if wm:
            ai_metadata = {**ai_metadata, "workflow_mutations": wm}

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
                 page_count, created_at, tenant_id, classification, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id, collection_id, source_id, p.name, str(p),
                extraction.content_type, extraction.provider,
                extraction.title or ai_title or p.stem, p.stat().st_size, content_hash,
                extraction.page_count, now, tid, cls, ai_summary,
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

        # Near-duplicate detection (best-effort): flag documents in the same
        # collection whose title is anomalously similar to this one using
        # IQR-based outlier detection — the fuzzy-match management-command
        # analog (aiify-opp-45). Runs after commit so the new doc is queryable.
        if detect_near_duplicates and extraction.title:
            near_dups = _detect_near_duplicate_titles(
                doc_id, extraction.title, collection_id, conn
            )
            if near_dups:
                ai_metadata = {**ai_metadata, "near_duplicates": near_dups}

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
            anomaly_report=anomaly_report,
        )
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Batch ingestion with processing-time anomaly detection
# (aiify-opp-35: hardcoded_threshold -> anomaly_detection). The external scan
# flagged paperless-ngx src/documents/management/commands/base.py — the base
# class for Django management commands that process document batches using a
# hardcoded PROGRESS_STEP constant. The repo is ephemeral; per the established
# aiify-opp pattern the augmentation lands in the analogous ICDEV subsystem (DIC).
#
# Design:
#   - _BATCH_PROGRESS_STEP (env DIC_BATCH_PROGRESS_STEP, default 5%) replaces
#     the hardcoded constant; controls how often progress_cb fires.
#   - _BATCH_IQR_FENCE (env DIC_BATCH_IQR_FENCE, default 1.5) drives IQR-based
#     outlier detection on per-document elapsed times. A file whose processing
#     time exceeds Q3 + fence × IQR of the batch so far is flagged "anomalous"
#     and reported for HITL review — no fixed time limit needed.
#   - Failures do not abort the batch; they are recorded in per_file[].
#   - Detection is best-effort: fewer than 4 samples → no flagging (not enough
#     data for IQR to be meaningful).
# --------------------------------------------------------------------------- #

_BATCH_PROGRESS_STEP: int = int(os.environ.get("DIC_BATCH_PROGRESS_STEP", "5"))
_BATCH_IQR_FENCE: float = float(os.environ.get("DIC_BATCH_IQR_FENCE", "1.5"))


@dataclass
class BatchIngestResult:
    total: int
    succeeded: int
    failed: int
    per_file: list[dict]
    anomalous_paths: list[str]

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "per_file": self.per_file,
            "anomalous_paths": self.anomalous_paths,
        }


def _detect_processing_time_anomalies(
    elapsed_times: list[float],
    fence: float = 1.5,
) -> tuple[float, float]:
    """Return (lo, hi) IQR fences for elapsed processing times.

    Times above hi are anomalously slow. Returns (-inf, inf) when the sample
    has fewer than 4 values (not enough data for IQR).
    """
    if len(elapsed_times) < 4:
        return float("-inf"), float("inf")
    s = sorted(elapsed_times)
    n = len(s)
    q1 = s[n // 4]
    q3 = s[(3 * n) // 4]
    iqr = q3 - q1
    return q1 - fence * iqr, q3 + fence * iqr


def ingest_batch(
    paths: "list[str | Path]",
    collection_id: str,
    *,
    tenant_id: "str | None" = None,
    classification: "str | None" = None,
    created_by: "str | None" = None,
    embed: bool = True,
    bridge_kg: bool = True,
    summarize: bool = True,
    extract_metadata: bool = True,
    extract_identifiers: bool = True,
    extract_correspondence: bool = True,
    progress_cb=None,
) -> BatchIngestResult:
    """Ingest multiple files with IQR-based processing-time anomaly detection.

    Replaces the hardcoded PROGRESS_STEP pattern from paperless management
    command base class: progress is reported every DIC_BATCH_PROGRESS_STEP
    percent of the batch, and IQR outlier detection flags documents whose
    processing time is anomalously high relative to the batch so far.

    Args:
        paths: ordered list of file paths to ingest.
        collection_id: target collection.
        progress_cb: optional callable(done, total, anomalous_so_far) fired
            every _BATCH_PROGRESS_STEP% of the batch.

    Returns:
        BatchIngestResult with per-file outcomes and anomalous_paths list.
    """
    total = len(paths)
    per_file: list[dict] = []
    elapsed_times: list[float] = []
    anomalous_paths: list[str] = []
    succeeded = 0
    failed = 0
    step = max(1, int(total * _BATCH_PROGRESS_STEP / 100)) if total else 1

    for i, path in enumerate(paths, 1):
        path_str = str(path)
        t0 = time.monotonic()
        try:
            outcome = ingest_file(
                path_str,
                collection_id,
                tenant_id=tenant_id,
                classification=classification,
                created_by=created_by,
                embed=embed,
                bridge_kg=bridge_kg,
                summarize=summarize,
                extract_metadata=extract_metadata,
                extract_identifiers=extract_identifiers,
                extract_correspondence=extract_correspondence,
            )
            elapsed = time.monotonic() - t0
            elapsed_times.append(elapsed)
            _, hi = _detect_processing_time_anomalies(elapsed_times, _BATCH_IQR_FENCE)
            anomalous = elapsed > hi
            if anomalous:
                anomalous_paths.append(path_str)
            per_file.append({
                "path": path_str,
                "ok": True,
                "doc_id": outcome.doc_id,
                "elapsed_s": round(elapsed, 4),
                "anomalous": anomalous,
                "error": "",
            })
            succeeded += 1
        except Exception as exc:
            elapsed = time.monotonic() - t0
            elapsed_times.append(elapsed)
            per_file.append({
                "path": path_str,
                "ok": False,
                "doc_id": "",
                "elapsed_s": round(elapsed, 4),
                "anomalous": False,
                "error": str(exc),
            })
            failed += 1
            logger.warning("batch ingest failed for %s: %s", path_str, exc)

        if progress_cb and i % step == 0:
            progress_cb(i, total, list(anomalous_paths))

    return BatchIngestResult(
        total=total,
        succeeded=succeeded,
        failed=failed,
        per_file=per_file,
        anomalous_paths=anomalous_paths,
    )


# --------------------------------------------------------------------------- #
# Document import validation with size-anomaly detection
# (aiify-opp-47: hardcoded_threshold -> anomaly_detection). The external scan
# flagged paperless-ngx src/documents/management/commands/document_importer.py
# — a Django management command that imports documents from a .zip export
# archive using hardcoded file-size limits, minimum content-length constants,
# and fixed title-length caps. The repo is ephemeral; per the established
# aiify-opp pattern the augmentation lands in the analogous ICDEV subsystem
# (DIC).
#
# Design:
#   - _IMPORT_MAX_FILE_BYTES (env DIC_IMPORT_MAX_FILE_BYTES, default 100 MB)
#     replaces hardcoded max-size rejection; files above this are skipped and
#     recorded in the result as "rejected" without LLM involvement.
#   - _IMPORT_MIN_CONTENT_CHARS (env DIC_IMPORT_MIN_CONTENT_CHARS, default 10)
#     replaces minimum-content-length guards; files below this floor after
#     text extraction are flagged as near-empty rather than silently rejected.
#   - IQR-based file-size anomaly detection flags documents whose byte-size is
#     a statistical outlier relative to the rest of the import batch, so an
#     unusually large file in an otherwise small-file batch is caught even when
#     it's under the absolute cap.
#   - Best-effort LLM quality check (via LLMRouter) for borderline files that
#     pass size gates but triggered another quality signal; degrades gracefully
#     when the router is unavailable.
# --------------------------------------------------------------------------- #

_IMPORT_MAX_FILE_BYTES: int = int(
    os.environ.get("DIC_IMPORT_MAX_FILE_BYTES", str(100 * 1024 * 1024))
)
_IMPORT_MIN_CONTENT_CHARS: int = int(
    os.environ.get("DIC_IMPORT_MIN_CONTENT_CHARS", "10")
)
_IMPORT_SIZE_IQR_FENCE: float = float(
    os.environ.get("DIC_IMPORT_SIZE_IQR_FENCE", "1.5")
)

_IMPORT_QUALITY_SYSTEM_PROMPT = (
    "You are a document import quality inspector. "
    "Given metadata about a document being imported into a DIC collection, "
    "decide whether it is safe to import. "
    'Respond with a strict JSON object: {"safe": true|false, '
    '"verdict": "ok"|"suspicious"|"reject", "reason": "<one sentence>"}. '
    "Consider: unusually large file size relative to siblings, very short "
    "content after extraction, suspicious MIME types, or anomalous naming."
)


@dataclass
class ImportValidationResult:
    """Outcome for a single document validated during archive import."""

    path: str
    accepted: bool
    anomalous: bool
    file_bytes: int
    rejection_reason: str
    quality_verdict: str
    quality_reason: str

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "accepted": self.accepted,
            "anomalous": self.anomalous,
            "file_bytes": self.file_bytes,
            "rejection_reason": self.rejection_reason,
            "quality_verdict": self.quality_verdict,
            "quality_reason": self.quality_reason,
        }


@dataclass
class ArchiveImportResult:
    """Aggregate result from validate_import_documents()."""

    total: int
    accepted: int
    rejected: int
    anomalous_count: int
    per_file: list[ImportValidationResult]

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "anomalous_count": self.anomalous_count,
            "per_file": [r.to_dict() for r in self.per_file],
        }


def _detect_file_size_anomalies(
    sizes: list[int],
    fence: float = 1.5,
) -> tuple[float, float]:
    """Return IQR (lo, hi) fences for a list of file sizes in bytes.

    Files above ``hi`` are size outliers relative to the batch.
    Returns (-inf, inf) when fewer than 4 samples — not enough for IQR.
    """
    if len(sizes) < 4:
        return float("-inf"), float("inf")
    s = sorted(float(x) for x in sizes)
    n = len(s)
    q1 = s[n // 4]
    q3 = s[(3 * n) // 4]
    iqr = q3 - q1
    return q1 - fence * iqr, q3 + fence * iqr


def _llm_import_quality_check(
    path: str,
    file_bytes: int,
    content_chars: int,
    mime_type: str,
    signal: str,
    peer_sizes: list[int],
) -> tuple[str, str]:
    """Ask LLM whether a borderline import document is safe.

    Returns (verdict, reason). Degrades to ("suspicious", "llm_unavailable")
    on any error.
    """
    try:
        import json as _json

        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        median_bytes = sorted(peer_sizes)[len(peer_sizes) // 2] if peer_sizes else 0
        prompt = (
            f"File: {Path(path).name}\n"
            f"MIME type: {mime_type}\n"
            f"File size: {file_bytes:,} bytes "
            f"(batch median: {median_bytes:,} bytes)\n"
            f"Content chars after extraction: {content_chars}\n"
            f"Quality signal: {signal}\n\n"
            "Should this document be imported?"
        )
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=_IMPORT_QUALITY_SYSTEM_PROMPT,
            max_tokens=128,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("anomaly_detection", req)
        if not resp or not resp.content:
            raise ValueError("empty response")
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
        parsed = _json.loads(raw)
        verdict = str(parsed.get("verdict", "suspicious"))
        reason = str(parsed.get("reason", ""))
        if verdict not in {"ok", "suspicious", "reject"}:
            verdict = "suspicious"
        return verdict, reason
    except Exception:
        return "suspicious", "llm_unavailable"


def validate_import_documents(
    paths: "list[str | Path]",
    *,
    collection_id: str = "",
    mime_resolver: "callable | None" = None,
) -> ArchiveImportResult:
    """Validate a list of document paths for import into a DIC collection.

    Replaces the hardcoded size/content guards from the paperless
    document_importer management command with configurable, IQR-aware checks:

    1. Files above DIC_IMPORT_MAX_FILE_BYTES are unconditionally rejected.
    2. IQR outlier detection on the file-size distribution flags documents
       that are anomalously large *relative to this batch*, independent of the
       absolute cap.
    3. Files that pass size gates but have near-empty content (below
       DIC_IMPORT_MIN_CONTENT_CHARS) are flagged for HITL review via a
       best-effort LLM quality check.

    Args:
        paths: files to validate (not yet ingested; sizes are read from disk).
        collection_id: target DIC collection (informational; not validated here).
        mime_resolver: optional callable(path) -> str for MIME type; defaults
            to mimetypes.guess_type.

    Returns:
        ArchiveImportResult with per-file validation outcomes.
    """
    import mimetypes

    if mime_resolver is None:
        def mime_resolver(p: str) -> str:
            return mimetypes.guess_type(p)[0] or "application/octet-stream"

    results: list[ImportValidationResult] = []
    accepted_bytes: list[int] = []

    for p in paths:
        path_str = str(p)
        try:
            file_bytes = Path(path_str).stat().st_size
        except OSError:
            file_bytes = 0

        # Gate 1: absolute size cap (replaces hardcoded constant).
        if file_bytes > _IMPORT_MAX_FILE_BYTES:
            results.append(ImportValidationResult(
                path=path_str,
                accepted=False,
                anomalous=False,
                file_bytes=file_bytes,
                rejection_reason=(
                    f"exceeds_max_size:{file_bytes}>{_IMPORT_MAX_FILE_BYTES}"
                ),
                quality_verdict="reject",
                quality_reason="file_too_large",
            ))
            continue

        accepted_bytes.append(file_bytes)
        results.append(ImportValidationResult(
            path=path_str,
            accepted=True,
            anomalous=False,
            file_bytes=file_bytes,
            rejection_reason="",
            quality_verdict="ok",
            quality_reason="",
        ))

    # Gate 2: IQR-based size outlier detection on accepted files.
    _, hi = _detect_file_size_anomalies(accepted_bytes, _IMPORT_SIZE_IQR_FENCE)
    for res in results:
        if not res.accepted:
            continue
        if res.file_bytes > hi:
            res.anomalous = True
            mime = mime_resolver(res.path)
            # Attempt lightweight content-length check (non-binary only).
            try:
                content_chars = len(Path(res.path).read_text(
                    encoding="utf-8", errors="replace"
                ).strip())
            except Exception:
                content_chars = 0
            signal = (
                f"size_iqr_outlier:{res.file_bytes}>{hi:.0f}"
                + (
                    f";near_empty_content:chars={content_chars}"
                    if content_chars < _IMPORT_MIN_CONTENT_CHARS
                    else ""
                )
            )
            verdict, reason = _llm_import_quality_check(
                res.path, res.file_bytes, content_chars, mime, signal, accepted_bytes
            )
            res.quality_verdict = verdict
            res.quality_reason = reason
            if verdict == "reject":
                res.accepted = False
                res.rejection_reason = f"llm_rejected:{reason}"

        # Gate 3: near-empty content on non-anomalous files (best-effort).
        elif res.file_bytes > 0:
            try:
                text = Path(res.path).read_text(
                    encoding="utf-8", errors="replace"
                ).strip()
                if len(text) < _IMPORT_MIN_CONTENT_CHARS:
                    res.anomalous = True
                    mime = mime_resolver(res.path)
                    verdict, reason = _llm_import_quality_check(
                        res.path,
                        res.file_bytes,
                        len(text),
                        mime,
                        f"near_empty_content:chars={len(text)}<{_IMPORT_MIN_CONTENT_CHARS}",
                        accepted_bytes,
                    )
                    res.quality_verdict = verdict
                    res.quality_reason = reason
                    if verdict == "reject":
                        res.accepted = False
                        res.rejection_reason = f"llm_rejected:{reason}"
            except Exception:
                pass

    accepted_count = sum(1 for r in results if r.accepted)
    rejected_count = sum(1 for r in results if not r.accepted)
    anomalous_count = sum(1 for r in results if r.anomalous)

    if collection_id:
        logger.info(
            "import validation: collection=%s total=%d accepted=%d rejected=%d anomalous=%d",
            collection_id,
            len(results),
            accepted_count,
            rejected_count,
            anomalous_count,
        )

    return ArchiveImportResult(
        total=len(results),
        accepted=accepted_count,
        rejected=rejected_count,
        anomalous_count=anomalous_count,
        per_file=results,
    )


# --------------------------------------------------------------------------- #
# Collection-level consumption pipeline health anomaly detection
# (aiify-opp-38: hardcoded_threshold -> anomaly_detection). The external scan
# flagged paperless-ngx src/documents/management/commands/document_consumer.py
# — the top-level document consumption pipeline orchestrator. Its hardcoded
# thresholds govern the minimum OCR character count before success, the max
# consecutive-failure retry limit before quarantine, and the queue-depth
# warning ceiling when the consume directory backlog grows too large.
#
# The ICDEV analog lands in DIC ingest_orchestrator.py. The "document consumer"
# role maps to the full ingest pipeline: files arrive, are processed by
# ingest_file/ingest_batch, and outcomes are stored in dic_documents. Rather
# than fixed constants this detector queries dic_documents for recent ingestion
# history and applies IQR-based anomaly detection on page_count and byte_size
# distributions:
#
#   - page_count outliers flag documents with far too many or too few pages,
#     indicating scanning/splitting errors at the consumption stage.
#   - byte_size outliers flag corrupt/re-submitted/oversized documents that
#     passed the ingest-time gate but are anomalous in context.
#   - A configurable queue-depth ceiling (DIC_CONSUMER_MAX_QUEUE_DOCS)
#     replaces the hardcoded consume-directory backlog limit.
#
# Health verdict:
#   "healthy"  — outlier fraction < DIC_CONSUMER_WARN_RATIO (default 0.10)
#   "degraded" — ≥ warn but < DIC_CONSUMER_CRITICAL_RATIO (default 0.25)
#   "critical" — ≥ critical ratio or backlog ceiling breached
#
# Returns None when sample < 4 or on any error (air-gap safe).
# --------------------------------------------------------------------------- #

_CONSUMER_HEALTH_LOOKBACK: int = int(os.environ.get("DIC_CONSUMER_HEALTH_LOOKBACK", "100"))
_CONSUMER_HEALTH_IQR_FENCE: float = float(
    os.environ.get("DIC_CONSUMER_HEALTH_IQR_FENCE", "1.5")
)
_CONSUMER_WARN_RATIO: float = float(os.environ.get("DIC_CONSUMER_WARN_RATIO", "0.10"))
_CONSUMER_CRITICAL_RATIO: float = float(
    os.environ.get("DIC_CONSUMER_CRITICAL_RATIO", "0.25")
)
_CONSUMER_MAX_QUEUE_DOCS: int = int(os.environ.get("DIC_CONSUMER_MAX_QUEUE_DOCS", "500"))


@dataclass
class ConsumerHealthReport:
    collection_id: str
    doc_count: int
    outlier_count: int
    outlier_fraction: float
    verdict: str  # "healthy" | "degraded" | "critical"
    backlog_warning: bool
    outlier_doc_ids: list[str]
    signals: list[str]

    def to_dict(self) -> dict:
        return {
            "collection_id": self.collection_id,
            "doc_count": self.doc_count,
            "outlier_count": self.outlier_count,
            "outlier_fraction": round(self.outlier_fraction, 4),
            "verdict": self.verdict,
            "backlog_warning": self.backlog_warning,
            "outlier_doc_ids": self.outlier_doc_ids,
            "signals": self.signals,
        }


def detect_collection_anomalies(
    collection_id: str,
    *,
    tenant_id: "str | None" = None,
    limit: "int | None" = None,
    conn=None,
) -> "ConsumerHealthReport | None":
    """Detect anomalies in recently-ingested documents for a collection.

    Queries dic_documents for the most recent ``limit`` (default
    DIC_CONSUMER_HEALTH_LOOKBACK) documents and applies IQR-based outlier
    detection on page_count and byte_size, replacing the hardcoded quality
    thresholds in paperless-ngx document_consumer.py (aiify-opp-38).

    Returns None when the sample has fewer than 4 documents (insufficient for
    IQR) or on any error. All failures degrade gracefully — callers proceed
    with ingestion unchanged.
    """
    lookback = limit if limit is not None else _CONSUMER_HEALTH_LOOKBACK
    own_conn = conn is None
    try:
        if own_conn:
            conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT doc_id, page_count, byte_size
            FROM dic_documents
            WHERE collection_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (collection_id, lookback),
        )
        rows = cur.fetchall()
    except Exception:
        return None
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass

    if not rows or len(rows) < 4:
        return None

    doc_count = len(rows)
    page_counts: list[int] = [int(r["page_count"] or 1) for r in rows]
    byte_sizes: list[int] = [int(r["byte_size"] or 0) for r in rows]

    # Reuse the existing IQR helper (same logic as batch/import anomaly detectors).
    pc_lo, pc_hi = _detect_file_size_anomalies(page_counts, _CONSUMER_HEALTH_IQR_FENCE)
    bs_lo, bs_hi = _detect_file_size_anomalies(byte_sizes, _CONSUMER_HEALTH_IQR_FENCE)

    signals: list[str] = []
    outlier_doc_ids: list[str] = []

    for r in rows:
        doc_id = r["doc_id"]
        pc = int(r["page_count"] or 1)
        bs = int(r["byte_size"] or 0)
        is_outlier = False

        if pc > pc_hi:
            signals.append(f"page_count_high:{doc_id}:{pc}>{pc_hi:.0f}")
            is_outlier = True
        elif pc_lo > 0 and pc < pc_lo:
            signals.append(f"page_count_low:{doc_id}:{pc}<{pc_lo:.0f}")
            is_outlier = True

        if bs > bs_hi:
            signals.append(f"byte_size_high:{doc_id}:{bs}>{bs_hi:.0f}")
            is_outlier = True
        elif bs_lo > 0 and bs < bs_lo:
            signals.append(f"byte_size_low:{doc_id}:{bs}<{bs_lo:.0f}")
            is_outlier = True

        if is_outlier and doc_id not in outlier_doc_ids:
            outlier_doc_ids.append(doc_id)

    backlog_warning = doc_count >= _CONSUMER_MAX_QUEUE_DOCS
    if backlog_warning:
        signals.append(
            f"backlog_warning:collection_size={doc_count}>={_CONSUMER_MAX_QUEUE_DOCS}"
        )

    outlier_count = len(outlier_doc_ids)
    outlier_fraction = outlier_count / doc_count if doc_count else 0.0

    if outlier_fraction >= _CONSUMER_CRITICAL_RATIO or backlog_warning:
        verdict = "critical"
    elif outlier_fraction >= _CONSUMER_WARN_RATIO:
        verdict = "degraded"
    else:
        verdict = "healthy"

    logger.info(
        "consumer health: collection=%s docs=%d outliers=%d verdict=%s",
        collection_id,
        doc_count,
        outlier_count,
        verdict,
    )
    return ConsumerHealthReport(
        collection_id=collection_id,
        doc_count=doc_count,
        outlier_count=outlier_count,
        outlier_fraction=outlier_fraction,
        verdict=verdict,
        backlog_warning=backlog_warning,
        outlier_doc_ids=outlier_doc_ids,
        signals=signals,
    )


# --------------------------------------------------------------------------- #
# Post-update metadata re-enrichment (aiify-opp-89: metadata_extraction ->
# llm_generation). The external scan flagged paperless-ngx
# src/documents/signals/handlers.py — the Django-signals layer that fires after
# a Document model save/update and triggers classifier re-runs (metadata
# re-assignment: document_type, correspondent, tags). The repo is ephemeral; per
# the established aiify-opp pattern the augmentation lands in the analogous ICDEV
# subsystem (DIC).
#
# This is the DIC analog of those post-save signal handlers: it re-runs the full
# LLM metadata-extraction pipeline on a *previously ingested* document (by
# doc_id) rather than at initial ingest time. Use-cases:
#   - text corrected via HITL or re-OCR
#   - document promoted to a new collection (context shift)
#   - schema or taxonomy updated (re-classify against new labels)
#   - user explicitly requests a freshness pass
#
# Design mirrors ingest_file() enrichment — same grounding, same HITL-proposal-
# only semantics (never silently written), same air-gap safety.
# --------------------------------------------------------------------------- #

def re_enrich_metadata(
    doc_id: str,
    *,
    extract_identifiers: bool = True,
    extract_correspondence: bool = True,
    conn=None,
) -> "dict | None":
    """Re-run LLM metadata extraction on an already-ingested DIC document.

    Args:
        doc_id: The ``dic_documents.doc_id`` of the target document.
        extract_identifiers: Also run identifier extraction (opp-5988).
        extract_correspondence: Also run correspondence extraction (opp-6100).
        conn: Optional existing DB connection; managed internally if None.

    Returns:
        ``{"doc_id": str, "filename": str, "proposals": dict}`` on success, or
        ``None`` when the document is not found. ``proposals`` may be empty when
        no LLM is available or confidence is below threshold — callers MUST treat
        empty proposals as "no enrichment" and leave existing metadata unchanged.
    """
    own_conn = conn is None
    try:
        if own_conn:
            conn = get_connection()

        cur = conn.cursor()
        cur.execute(
            "SELECT filename, collection_id, tenant_id, classification FROM dic_documents WHERE doc_id = %s LIMIT 1",
            (doc_id,),
        )
        row = cur.fetchone()
        if row is None:
            logger.warning("re_enrich_metadata: doc_id %r not found", doc_id)
            return None

        filename = (row["filename"] if hasattr(row, "keys") else row[0]) or doc_id

        # Reconstruct text from rag_chunks ordered by chunk_index.
        cur.execute(
            """
            SELECT rc.content
            FROM dic_chunk_links dcl
            JOIN rag_chunks rc ON rc.id = dcl.rag_chunk_id
            WHERE dcl.doc_id = %s
            ORDER BY dcl.chunk_index ASC
            """,
            (doc_id,),
        )
        chunk_rows = cur.fetchall()
        if not chunk_rows:
            logger.info("re_enrich_metadata: no chunks for doc_id %r", doc_id)
            return {"doc_id": doc_id, "filename": filename, "proposals": {}}

        text = "\n".join(
            (r["content"] if hasattr(r, "keys") else r[0]) or ""
            for r in chunk_rows
        ).strip()

        if not text:
            return {"doc_id": doc_id, "filename": filename, "proposals": {}}

        # Run LLM extractors — same pipeline as ingest_file() enrichment.
        proposals: dict = {}

        md = _ai_metadata_extraction(text, filename)
        if md:
            proposals.update(md)

        if extract_identifiers:
            ids = _ai_extract_identifiers(text)
            if ids:
                proposals["identifiers"] = ids
                id_anomaly = _detect_identifier_anomaly(ids)
                if id_anomaly:
                    proposals["identifier_anomaly"] = id_anomaly

        if extract_correspondence:
            corr = _ai_extract_correspondence(text)
            if corr:
                proposals["correspondence"] = corr

        logger.info(
            "re_enrich_metadata: doc_id=%r filename=%r proposals=%s",
            doc_id, filename, list(proposals.keys()),
        )
        return {"doc_id": doc_id, "filename": filename, "proposals": proposals}

    except Exception as exc:
        logger.warning("re_enrich_metadata: failed for doc_id %r: %s", doc_id, exc)
        return None
    finally:
        if own_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
