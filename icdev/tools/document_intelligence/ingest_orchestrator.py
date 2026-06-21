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
import os  # noqa: F401 — kept for legacy batch-path env override; do not remove (task-3bc9eb0918-cc4ea61c-d3)
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
        assigned_to     TEXT,
        reviewed_by     TEXT,
        reviewed_at     TEXT,
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
    ("dic_sections", "assigned_to", "TEXT"),
    ("dic_sections", "reviewed_by", "TEXT"),
    ("dic_sections", "reviewed_at", "TEXT"),
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

def _embed_and_store(
    chunks: list,
    tenant_id: str,
    errors: list[str],
    progress_cb=None,
    out_id_map: dict | None = None,
) -> int:
    """Embed new chunks and upsert into the vector store. Returns count embedded.

    progress_cb: optional callable(embedded: int, total: int) called after each chunk.
    out_id_map: optional dict to fill with ``{chunk_index: final rag_chunk_id}``.
        When a chunk is a duplicate of an existing ``rag_chunks`` row, the map
        receives the *existing* id so DIC links stay consistent. New chunks get
        their freshly-upserted id.
    """
    if out_id_map is not None:
        out_id_map.clear()

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

    # Resolve each chunk to a canonical rag_chunk_id. Existing rows are reused by
    # content_hash so the DIC link always points to a real chunk.
    new_chunks: list[tuple[int, Any]] = []
    for idx, c in enumerate(chunks):
        existing = None
        try:
            existing = store.get_by_content_hash(getattr(c, "content_hash", ""))
        except Exception:
            pass
        if existing:
            if out_id_map is not None:
                out_id_map[idx] = existing.chunk_id
            # Carry the existing embedding forward so callers can use it.
            if getattr(existing, "embedding", None) is not None:
                c.embedding = existing.embedding
            continue
        new_chunks.append((idx, c))

    total = len(new_chunks)
    embedded = 0
    for idx, c in new_chunks:
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

    embeddable = [c for _, c in new_chunks if getattr(c, "embedding", None) is not None]
    if embeddable:
        try:
            store.upsert(embeddable)
        except Exception as e:
            errors.append(f"vector upsert failed: {e}")
        else:
            # Record the final chunk ids for every chunk that was upserted.
            if out_id_map is not None:
                for idx, c in new_chunks:
                    if getattr(c, "embedding", None) is not None:
                        out_id_map[idx] = c.chunk_id
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
        conn: optional DB connection (else an RLS-aware one is opened).
        progress_cb: optional callable(stage: str, detail: str, pct: int) for progress events.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"not a file: {path}")

    tid, cls = _resolve_context(tenant_id, classification)
    errors: list[str] = []

    def _emit(stage: str, detail: str, pct: int = 0, extra: dict | None = None) -> None:
        if progress_cb:
            try:
                progress_cb(stage, detail, pct, extra or {})
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
            dup_error = f"Duplicate detected — this file already exists as '{existing_filename}' in this collection. No new version created (idempotent)."
            _emit(
                "done",
                f"Idempotent — duplicate of {existing_doc_id}",
                100,
                extra={
                    "doc_id": existing_doc_id,
                    "chunks": existing_chunks,
                    "chunks_embedded": existing_chunks,
                    "kg_entities": 0,
                    "errors": errors + [dup_error],
                },
            )
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
                errors=errors + [dup_error],
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
        # Scope the content hash to the collection so identical text uploaded to
        # different collections gets distinct rag_chunks rows tied to each
        # collection's project_id. This makes vector search collection-filterable
        # and guarantees dic_chunk_links always reference retrievable chunks.
        for c in chunks:
            c.content_hash = _sha256(f"{collection_id}:{c.content}")
        _emit("chunking", f"{len(chunks)} chunks created", 20)

        # Warn when text is empty or near-empty so the UI can explain 0 chunks.
        if not text.strip():
            errors.append("Extracted text is empty — file may be image-based (scanned PDF), corrupted, or uses an unsupported encoding. Try OCR or re-export as text.")

        # 3) Embed + upsert into the vector store (same path ingest_source uses).
        # final_chunk_ids maps chunk index -> the actual rag_chunks.id that the
        # DIC link must reference. It resolves content-hash duplicates to existing
        # rows so search never follows a dangling link.
        final_chunk_ids: dict[int, str] = {}
        chunks_embedded = 0
        if embed and chunks:
            total_chunks = len(chunks)

            def _embed_progress(done: int, total: int) -> None:
                pct = 20 + int(done / max(total, 1) * 55)
                _emit("embedding", f"Embedding {done}/{total} chunks…", pct)

            _emit("embedding", f"Embedding 0/{total_chunks} chunks…", 20)
            chunks_embedded = _embed_and_store(
                chunks, tid, errors, progress_cb=_embed_progress, out_id_map=final_chunk_ids,
            )
            _emit("embedding", f"Embedded {chunks_embedded}/{total_chunks} chunks", 75)
            if not final_chunk_ids:
                errors.append(
                    "Vector store has no record of these chunks — search will be empty until embedding succeeds."
                )

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
            # final_chunk_ids contains the canonical rag_chunks.id (existing or
            # newly upserted). Fall back to the chunk's own id only when the map
            # is missing, and avoid writing a dangling link when embedding failed.
            rag_chunk_id = final_chunk_ids.get(i) or getattr(
                chunk, "chunk_id", f"{source_id}_chunk_{i}"
            )
            if not rag_chunk_id:
                continue
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
        if bridge_kg and chunks and final_chunk_ids:
            try:
                from tools.rag.rag_to_kg_ingester import ingest_chunk

                for i, chunk in enumerate(chunks):
                    cid = final_chunk_ids.get(i) or getattr(chunk, "chunk_id", None)
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

        _emit(
            "done",
            f"Done — {len(chunks)} chunks, {kg_entities} entities",
            100,
            extra={
                "doc_id": doc_id,
                "chunks": len(chunks),
                "chunks_embedded": chunks_embedded,
                "kg_entities": kg_entities,
                "errors": list(errors),
            },
        )
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
