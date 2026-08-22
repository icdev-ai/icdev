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
import json
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
from tools.document_intelligence.collection_registry import ensure_collection
from tools.logging.icdev_logger import get_logger
from tools.rag.chunker import chunk_content
from tools.rag.chunking_templates import suggest_template


def _resolve_chunk_template(text: str, explicit: str | None) -> tuple[str, str]:
    """Resolve which chunking template a DIC document should use (oss2-fix-02, D2).

    An explicit ``chunk_template`` always wins. Otherwise ``suggest_template`` scores
    the text against each template's detect patterns; it is advisory and safely
    returns the default (``general``) when nothing clears its ``min_score``, so a
    confident OSCAL/STIG/RFP match gets structural chunking while ambiguous text is
    unchanged. Returns ``(template_name, reason)``; the reason is surfaced to the
    operator (progress event + persisted chunk metadata) rather than applied silently.
    """
    if explicit:
        return explicit, "explicit"
    sugg = suggest_template(text)
    return (sugg.get("suggested") or "general"), f"auto ({sugg.get('reason', 'detected')})"

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
    # Declared here because ingest_file WRITES this table (via ensure_collection):
    # a document whose collection has no row is invisible in the Collections UI,
    # which enumerates dic_collections rather than dic_documents. Mirrors the
    # SQLite branch of tools/document_intelligence/db/init_db.py.
    """
    CREATE TABLE IF NOT EXISTS dic_collections (
        collection_id   TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        description     TEXT DEFAULT '',
        owner_id        TEXT DEFAULT '',
        retention_days  INTEGER DEFAULT 90,
        classification  TEXT DEFAULT 'CUI',
        tenant_id       TEXT DEFAULT 'default',
        created_at      TEXT DEFAULT (datetime('now'))
    )
    """,
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
        -- The cited chunk's content hash AT LINK TIME. This is the evidence
        -- baseline: if rag_chunks.content_hash later differs, the source this
        -- document was built from has changed underneath it (see
        -- packs/evidence_currency.py). Without it there is no baseline and
        -- evidence drift is undetectable. Migration 267 adds it to existing DBs.
        chunk_hash      TEXT,
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
    # Migration 230 — tech writer workspace
    ("dic_documents", "template_type", "TEXT"),
    ("dic_documents", "writeguard_mode", "TEXT DEFAULT 'default'"),
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
            # Record the id ACTUALLY persisted for each chunk, not the freshly
            # generated c.chunk_id. upsert() dedups by content_hash and KEEPS the
            # existing row under its ORIGINAL id, so when a chunk's content was
            # already ingested (a re-ingest, or the same text in this collection),
            # its real id is not c.chunk_id. Trusting c.chunk_id there makes the
            # caller write a dic_chunk_link to a rag_chunks row that was never
            # inserted -- a dangling link, so chunks_for_version returns nothing and
            # a paper that ingested "successfully" can never be cited. Re-resolve by
            # content_hash to the row the store actually holds.
            if out_id_map is not None:
                for idx, c in new_chunks:
                    if getattr(c, "embedding", None) is None:
                        continue
                    # Record ONLY an id the store confirms is actually in rag_chunks.
                    # get_by_content_hash resolves both the freshly-inserted row and a
                    # content-hash duplicate kept under its original id. If it returns
                    # nothing (upsert dedup-skipped an empty embedding, or the row did
                    # not persist), record NOTHING -- the caller then writes no link
                    # rather than a dangling one. Never fall back to c.chunk_id, which
                    # is the id upsert may have skipped.
                    try:
                        resolved = store.get_by_content_hash(getattr(c, "content_hash", ""))
                    except Exception:
                        resolved = None
                    if resolved and getattr(resolved, "chunk_id", None):
                        out_id_map[idx] = resolved.chunk_id
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

# Date plausibility guardrails.
_DATE_MIN_YEAR = 1900
_DATE_MAX_YEAR_OFFSET = 50

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

        date_anomaly = _detect_date_anomaly(date_str) if date_str else None
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

# Width of the confidence band above the minimum that is considered "borderline"
# by the anomaly detector. A result just above the floor is flagged for review.
_CLASSIFY_BORDER_BAND = 0.05

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


def _detect_classify_anomaly(result, taxonomy) -> str | None:
    """Surface quality signals from an automatic classification result.

    Returns a short anomaly key when the result is suspect:

    * ``max_labels_hit`` — the model returned exactly the allowed cap of labels,
      suggesting it may be over-filing.
    * ``borderline_confidence`` — confidence is at or inside the band just above
      ``_CLASSIFY_MIN_CONFIDENCE``.
    * ``trivial_taxonomy`` — the taxonomy only contains one usable label, so no
      real choice was made.

    Returns ``None`` for clean results, missing results, or malformed input.
    """
    if not isinstance(result, dict):
        return None
    labels = result.get("labels") or []
    try:
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None

    normalized = _normalize_taxonomy(taxonomy)
    if not labels or not normalized:
        return None
    if len(normalized) <= 1:
        return "trivial_taxonomy"
    if len(labels) >= _CLASSIFY_MAX_SELECTED:
        return "max_labels_hit"
    if confidence <= _CLASSIFY_MIN_CONFIDENCE + _CLASSIFY_BORDER_BAND:
        return "borderline_confidence"
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
    detect_near_duplicates: bool = False,
    detect_anomalies: bool = True,
    workflow_custom_fields: list[dict] | None = None,
    chunk_template: str | None = None,
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

    # Metadata anomaly detection (best-effort): flag duplex-scan artifacts etc.
    if detect_anomalies and text.strip():
        anomaly = _ai_metadata_anomaly_detection(
            Extraction(
                text=text,
                provider=extraction.provider,
                content_type=extraction.content_type,
                page_count=extraction.page_count,
                title=extraction.title or ai_title or p.stem,
            ),
            p.name,
        )
        if anomaly:
            ai_metadata = {**ai_metadata, "metadata_anomaly": anomaly}

    # Workflow mutation proposals (best-effort): caller-supplied custom fields.
    if workflow_custom_fields and text.strip():
        _emit("workflow", "Proposing workflow mutations…", 9)
        wm = _ai_propose_workflow_mutations(text, workflow_custom_fields)
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
            "SELECT doc_id, filename FROM dic_documents WHERE content_sha256 = %s AND collection_id = %s LIMIT 1",
            (content_hash, collection_id),
        ).fetchone()
        if dup_row:
            existing_doc_id = dup_row[0] if hasattr(dup_row, "__getitem__") else dup_row["doc_id"]
            existing_filename = dup_row[1] if hasattr(dup_row, "__getitem__") else dup_row.get("filename", "")
            ver_row = conn.execute(
                "SELECT version_id, version_no FROM dic_versions WHERE doc_id = %s ORDER BY version_no DESC LIMIT 1",
                (existing_doc_id,),
            ).fetchone()
            if ver_row:
                version_id = ver_row[0] if hasattr(ver_row, "__getitem__") else ver_row["version_id"]
            else:
                version_id = f"{existing_doc_id}_v1"
            # Report the existing chunk count so callers see consistent metrics.
            chunk_row = conn.execute(
                "SELECT COUNT(*) FROM dic_chunk_links WHERE version_id = %s",
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
        #
        # oss2-fix-02 (D2): template chunking (oscal_catalog/stig_checklist/rfp_sow/…)
        # shipped but the DIC pipeline it was built for never passed template=, so
        # every document fell back to general sliding-window chunking and structured
        # controls were split mid-control. Resolve a template here: an explicit
        # chunk_template always wins; otherwise auto-detect. suggest_template is
        # advisory and safely returns the default ("general") when nothing scores
        # above its min_score, so a confident OSCAL/STIG match gets structural
        # chunking while ambiguous text is unchanged. The choice is surfaced to the
        # operator (progress + persisted template_type) so it is not silent.
        resolved_template, template_reason = _resolve_chunk_template(text, chunk_template)
        _emit("chunking", f"Splitting into chunks (template: {resolved_template})…", 15)
        chunks = chunk_content(
            text,
            source_type="dic_document",
            source_id=source_id,
            source_table="dic_documents",
            metadata={
                "filename": p.name,
                "collection_id": collection_id,
                # Record which template was used (and why) on every chunk, so the
                # choice is auditable without touching the dic_documents insert.
                "chunk_template": resolved_template,
                "chunk_template_reason": template_reason,
            },
            tenant_id=tid,
            project_id=collection_id,
            classification=cls,
            template=resolved_template,
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

        # The container must exist before the document, or the document is
        # ingested successfully and then is unreachable in the Collections UI —
        # which enumerates dic_collections, not dic_documents. Same transaction,
        # so the pair lands together.
        ensure_collection(conn, collection_id, tenant_id=tid, classification=cls)

        cur.execute(
            """
            INSERT OR REPLACE INTO dic_documents
                (doc_id, collection_id, source_id, filename, filepath,
                 content_type, provider, title, byte_size, content_sha256,
                 page_count, created_at, tenant_id, classification)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                version_id, doc_id, 1, "human_authored", "approved",
                content_hash, now, created_by, tid, cls,
            ),
        )

        # Refresh chunk links for this version.
        cur.execute("DELETE FROM dic_chunk_links WHERE version_id = %s", (version_id,))
        for i, chunk in enumerate(chunks):
            # final_chunk_ids contains the canonical rag_chunks.id the vector store
            # ACTUALLY persisted (existing row on a content-hash dedup, or the
            # freshly upserted one). Only link to that. The old `or chunk.chunk_id`
            # fallback wrote a link to an id that was never inserted whenever
            # embedding was skipped/failed or the chunk was deduped under a different
            # id -- a dangling link that makes chunks_for_version return nothing, so
            # the document could never be cited. No verified id => no link.
            rag_chunk_id = final_chunk_ids.get(i)
            if not rag_chunk_id:
                continue
            chunk_index = getattr(chunk, "chunk_index", i)
            md = getattr(chunk, "metadata", None) or {}
            page = md.get("page")
            section = md.get("section") or md.get("heading")
            link_id = f"{version_id}_link_{i}"
            # Capture the cited chunk's hash AT LINK TIME — the evidence baseline
            # this document was built from. A later divergence from
            # rag_chunks.content_hash means the source changed underneath the
            # document (packs/evidence_currency.py). content_hash is
            # collection-scoped above, matching the resolved rag_chunks row.
            chunk_hash = getattr(chunk, "content_hash", None)
            cur.execute(
                """
                INSERT OR REPLACE INTO dic_chunk_links
                    (link_id, doc_id, version_id, rag_chunk_id, collection_id,
                     chunk_index, page, section, chunk_hash, created_at,
                     tenant_id, classification)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    link_id, doc_id, version_id, rag_chunk_id, collection_id,
                    chunk_index, page, section, chunk_hash, now, tid, cls,
                ),
            )
        conn.commit()

        # Inter-document cross-reference extraction (dmx-ref-01, best-effort).
        # Deterministic regex over the extracted text — records "see Section N of
        # <Doc>" style references into dic_cross_references for later resolution
        # and cascade flagging. Never fails the ingest.
        try:
            from tools.document_intelligence.cross_reference_tracker import (
                store_references_from_text,
            )

            store_references_from_text(
                conn, doc_id, text, source_section="",
                tenant_id=tid, classification=cls,
            )
            conn.commit()
        except Exception as e:  # pragma: no cover - defensive
            errors.append(f"cross-reference extraction failed: {e}")

        # Near-duplicate title detection (best-effort): compare this document's
        # title against existing titles in the same collection.
        if detect_near_duplicates:
            title_for_dup = extraction.title or ai_title or p.stem
            near = _detect_near_duplicate_titles(doc_id, title_for_dup, collection_id, conn)
            if near:
                ai_metadata = {**ai_metadata, "near_duplicates": near}

        # 5) KG bridge (best-effort). ingest_chunk reads rag_chunks by id, so this
        #    only finds content when embedding upserted the chunk above.
        _emit("kg_bridge", "Extracting entities and relationships…", 78)
        kg_entities = 0
        kg_rels = 0
        if bridge_kg and chunks and final_chunk_ids:
            try:
                from tools.rag.rag_to_kg_ingester import ingest_chunk

                for i, chunk in enumerate(chunks):
                    # Only bridge a chunk the vector store actually persisted (same rule as the DIC links
                    # above). ingest_chunk reads rag_chunks by id, so a c.chunk_id fallback here would hand
                    # it an id that was never inserted.
                    cid = final_chunk_ids.get(i)
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


# --------------------------------------------------------------------------- #
# Anomaly / batch / import / lifecycle / workflow helpers
# --------------------------------------------------------------------------- #

# Consumer pre-validation (per-file) — env-controllable, no hardcoded floors.
_CONSUMER_MIN_FILE_BYTES = 4
_CONSUMER_MAX_FILENAME_LEN = 255

# ZIP-based formats are skipped for MIME mismatch because their magic bytes are
# identical to a generic ZIP archive (DOCX/XLSX/PPTX are all ZIP containers).
_CONSUMER_MIME_SKIP_EXTENSIONS = {".zip", ".docx", ".xlsx", ".pptx"}
_CONSUMER_EXTENSION_TO_MIME = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".zip": "application/zip",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

# Consumer outcome (post-ingest) thresholds.
_OUTCOME_MIN_EMBED_RATIO = 0.5
_OUTCOME_MAX_ERRORS = 5

# Collection-level pipeline health thresholds.
_CONSUMER_WARN_RATIO = 0.10
_CONSUMER_CRITICAL_RATIO = 0.25
_CONSUMER_MAX_QUEUE_DOCS = 500

# Bulk import validation thresholds.
_IMPORT_MAX_FILE_BYTES = 100 * 1024 * 1024
_IMPORT_SIZE_IQR_FENCE = 1.5
_IMPORT_MIN_CONTENT_CHARS = 20

# LLM label-match criteria suggester.
_LABEL_MATCH_MIN_CONFIDENCE = 0.70
_LABEL_MATCH_MAX_KEYWORDS = 10

# LLM workflow action parameter extractor.
_ACTION_PARAMS_MIN_CONFIDENCE = 0.70
_ACTION_PARAMS_MAX_ASSIGNMENTS = 10
_ACTION_PARAMS_MAX_REMOVALS = 10
_ALLOWED_ACTION_FIELDS = {
    "correspondent",
    "tag",
    "document_type",
    "storage_path",
    "custom_field",
}

# Near-duplicate title detection.
_NEAR_DUP_MIN_TOKENS = 5

# Lifecycle assignment anomaly thresholds.
_LIFECYCLE_MIN_CONFIDENCE = 0.70
_LIFECYCLE_CONFIDENCE_EPSILON = 0.05
_LIFECYCLE_MAX_ASSIGNMENTS = 10


# --------------------------------------------------------------------------- #
# Shared IQR fence helper
# --------------------------------------------------------------------------- #

def _median(values: list[float]) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    if n % 2:
        return values[n // 2]
    return (values[n // 2 - 1] + values[n // 2]) / 2.0


def _iqr_fences(values: list[float], fence: float = 1.5) -> tuple[float, float]:
    """Return (lower, upper) Tukey fences for a numeric sample.

    Samples with fewer than 4 values return infinite fences so that nothing is
    flagged as anomalous.
    """
    if len(values) < 4:
        return (float("-inf"), float("inf"))
    s = sorted(float(v) for v in values)
    n = len(s)
    if n % 2:
        lower = s[: n // 2]
        upper = s[n // 2 + 1 :]
    else:
        lower = s[: n // 2]
        upper = s[n // 2 :]
    q1 = _median(lower)
    q3 = _median(upper)
    iqr = q3 - q1
    return (q1 - fence * iqr, q3 + fence * iqr)


# --------------------------------------------------------------------------- #
# Batch ingest + processing-time anomaly detection
# --------------------------------------------------------------------------- #

def _detect_processing_time_anomalies(times: list[float], fence: float = 1.5) -> tuple[float, float]:
    return _iqr_fences(times, fence)


@dataclass
class BatchIngestResult:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    per_file: list[dict] = field(default_factory=list)
    anomalous_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "per_file": list(self.per_file),
            "anomalous_paths": list(self.anomalous_paths),
        }


def ingest_batch(
    files: list,
    collection_id: str,
    *,
    tenant_id: str | None = None,
    classification: str | None = None,
    embed: bool = False,
    bridge_kg: bool = False,
    summarize: bool = False,
    clean_ocr: bool = False,
    extract_metadata: bool = False,
    extract_identifiers: bool = False,
    classify_taxonomy: list[str] | None = None,
    extract_correspondence: bool = False,
    progress_cb=None,
) -> BatchIngestResult:
    """Ingest a list of files, recording per-file timing and anomaly flags."""
    import time

    result = BatchIngestResult()
    if not files:
        return result
    times: list[float] = []
    for i, path in enumerate(files):
        p = Path(path)
        start = time.monotonic()
        try:
            outcome = ingest_file(
                str(p),
                collection_id,
                tenant_id=tenant_id,
                classification=classification,
                embed=embed,
                bridge_kg=bridge_kg,
                summarize=summarize,
                clean_ocr=clean_ocr,
                extract_metadata=extract_metadata,
                extract_identifiers=extract_identifiers,
                classify_taxonomy=classify_taxonomy,
                extract_correspondence=extract_correspondence,
            )
            elapsed = time.monotonic() - start
            times.append(elapsed)
            entry = {
                "path": str(p),
                "ok": True,
                "doc_id": outcome.doc_id,
                "elapsed_s": round(elapsed, 4),
                "anomalous": False,
                "error": "",
            }
            result.succeeded += 1
        except Exception as exc:
            elapsed = time.monotonic() - start
            times.append(elapsed)
            entry = {
                "path": str(p),
                "ok": False,
                "doc_id": "",
                "elapsed_s": round(elapsed, 4),
                "anomalous": False,
                "error": str(exc),
            }
            result.failed += 1
        result.per_file.append(entry)
        result.total += 1
        if progress_cb:
            try:
                progress_cb(i + 1, len(files), result.anomalous_paths)
            except Exception:
                pass

    lo, hi = _detect_processing_time_anomalies(times)
    for entry in result.per_file:
        if entry["ok"] and entry["elapsed_s"] > hi:
            entry["anomalous"] = True
            result.anomalous_paths.append(entry["path"])
    return result


# --------------------------------------------------------------------------- #
# Consumer file pre-validation anomaly
# --------------------------------------------------------------------------- #

def _detect_mime_from_header(path: Path) -> str | None:
    """Best-effort MIME detection from file magic bytes."""
    try:
        with path.open("rb") as f:
            header = f.read(16)
    except Exception:
        return None
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
        return "image/gif"
    if header.startswith(b"%PDF"):
        return "application/pdf"
    if header.startswith(b"PK\x03\x04"):
        return "application/zip"
    try:
        header.decode("utf-8")
        return "text/plain"
    except UnicodeDecodeError:
        return "application/octet-stream"


def detect_consumer_file_anomaly(path) -> dict | None:
    """Flag empty/corrupt files, oversized filenames, and MIME/extension mismatches."""
    p = Path(path)
    signals: list[str] = []
    file_bytes = 0
    try:
        file_bytes = p.stat().st_size
    except Exception:
        signals.append("empty_or_corrupt: unable to read file")
    else:
        if file_bytes < _CONSUMER_MIN_FILE_BYTES:
            signals.append(f"empty_or_corrupt: file is only {file_bytes} bytes")

    if len(p.name) > _CONSUMER_MAX_FILENAME_LEN:
        signals.append(
            f"filename_too_long: {len(p.name)} chars exceeds {_CONSUMER_MAX_FILENAME_LEN}"
        )

    ext = p.suffix.lower()
    expected_mime = _CONSUMER_EXTENSION_TO_MIME.get(ext)
    if expected_mime and ext not in _CONSUMER_MIME_SKIP_EXTENSIONS:
        detected = _detect_mime_from_header(p)
        if detected and detected != expected_mime:
            signals.append(
                f"mime_extension_mismatch: expected {expected_mime}, got {detected}"
            )

    if signals:
        return {
            "source": "consumer_pre_validation",
            "signals": signals,
            "file_bytes": file_bytes,
            "filename": p.name,
        }
    return None


# --------------------------------------------------------------------------- #
# Consumer outcome anomaly
# --------------------------------------------------------------------------- #

def detect_consumer_outcome_anomaly(
    chunks: int,
    chunks_embedded: int,
    errors: list,
    text_was_nonempty: bool,
    embed_requested: bool = True,
) -> dict | None:
    """Detect post-ingest consumption problems (zero chunks, low embed rate, errors)."""
    try:
        signals: list[str] = []
        if text_was_nonempty and chunks == 0:
            signals.append("zero_chunks")
        if embed_requested and chunks > 0:
            ratio = chunks_embedded / chunks
            if ratio < _OUTCOME_MIN_EMBED_RATIO:
                signals.append(f"low_embed_rate: {chunks_embedded}/{chunks}")
        if len(errors) > _OUTCOME_MAX_ERRORS:
            signals.append(f"high_error_count: {len(errors)}")
        if not signals:
            return None
        return {"source": "consumer_outcome_anomaly", "signals": signals}
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Collection-level pipeline health anomaly
# --------------------------------------------------------------------------- #

@dataclass
class ConsumerHealthReport:
    verdict: str
    doc_count: int
    outlier_count: int
    backlog_warning: bool
    collection_id: str = ""
    outlier_fraction: float = 0.0
    signals: list[str] = field(default_factory=list)
    outlier_doc_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "collection_id": self.collection_id,
            "verdict": self.verdict,
            "doc_count": self.doc_count,
            "outlier_count": self.outlier_count,
            "outlier_fraction": self.outlier_fraction,
            "backlog_warning": self.backlog_warning,
            "signals": list(self.signals),
            "outlier_doc_ids": list(self.outlier_doc_ids),
        }


def detect_collection_anomalies(
    collection_id: str, conn=None, limit: int = 100
) -> ConsumerHealthReport | None:
    """IQR-based outlier detection on page_count / byte_size for a collection."""
    own = conn is None
    if own:
        conn = get_connection()
    try:
        try:
            _ensure_schema(conn)
        except Exception:
            return None
        cur = conn.cursor()
        cur.execute(
            "SELECT doc_id, page_count, byte_size FROM dic_documents "
            "WHERE collection_id = %s ORDER BY created_at DESC LIMIT %s",
            (collection_id, limit),
        )
        rows = cur.fetchall()
        if len(rows) < 4:
            return None
        doc_ids: list[str] = []
        page_counts: list[int] = []
        byte_sizes: list[int] = []
        for row in rows:
            if isinstance(row, dict):
                doc_id = row.get("doc_id", "")
                pc = row.get("page_count", 0)
                bs = row.get("byte_size", 0)
            elif hasattr(row, "__getitem__"):
                doc_id = row[0]
                pc = row[1]
                bs = row[2]
            else:
                doc_id = getattr(row, "doc_id", "")
                pc = getattr(row, "page_count", 0)
                bs = getattr(row, "byte_size", 0)
            doc_ids.append(doc_id)
            page_counts.append(int(pc or 0))
            byte_sizes.append(int(bs or 0))

        pc_lo, pc_hi = _iqr_fences(page_counts)
        bs_lo, bs_hi = _iqr_fences(byte_sizes)

        outlier_doc_ids: list[str] = []
        signals: list[str] = []
        for i, doc_id in enumerate(doc_ids):
            if page_counts[i] > pc_hi:
                signals.append(f"page_count_high: {page_counts[i]}")
                if doc_id not in outlier_doc_ids:
                    outlier_doc_ids.append(doc_id)
            if byte_sizes[i] > bs_hi:
                signals.append(f"byte_size_high: {byte_sizes[i]}")
                if doc_id not in outlier_doc_ids:
                    outlier_doc_ids.append(doc_id)

        outlier_count = len(outlier_doc_ids)
        doc_count = len(rows)
        backlog_warning = doc_count >= _CONSUMER_MAX_QUEUE_DOCS
        if backlog_warning:
            signals.append("backlog_warning")

        ratio = outlier_count / doc_count if doc_count else 0.0
        if ratio >= _CONSUMER_CRITICAL_RATIO or backlog_warning:
            verdict = "critical"
        elif ratio >= _CONSUMER_WARN_RATIO:
            verdict = "degraded"
        else:
            verdict = "healthy"

        return ConsumerHealthReport(
            verdict=verdict,
            outlier_count=outlier_count,
            doc_count=doc_count,
            backlog_warning=backlog_warning,
            collection_id=collection_id,
            outlier_fraction=ratio,
            signals=signals,
            outlier_doc_ids=outlier_doc_ids,
        )
    finally:
        if own:
            try:
                conn.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Bulk import validation
# --------------------------------------------------------------------------- #

@dataclass
class ImportValidationResult:
    path: str
    accepted: bool
    anomalous: bool
    rejection_reason: str = ""


@dataclass
class ArchiveImportResult:
    total: int
    accepted: int
    rejected: int
    per_file: list[ImportValidationResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "per_file": [
                {
                    "path": r.path,
                    "accepted": r.accepted,
                    "anomalous": r.anomalous,
                    "rejection_reason": r.rejection_reason,
                }
                for r in self.per_file
            ],
        }


def _detect_file_size_anomalies(
    sizes: list[float], fence: float = _IMPORT_SIZE_IQR_FENCE
) -> tuple[float, float]:
    return _iqr_fences(sizes, fence)


def _llm_import_quality_check(text: str) -> tuple[str, str]:
    """Best-effort LLM quality check for imported files.

    Returns (status, detail). Default is clean; callers may override by
    monkeypatching this function in tests.
    """
    return ("clean", "")


def validate_import_documents(paths: list) -> ArchiveImportResult:
    """Validate a list of file paths for import: size gate, content gate, IQR anomaly."""
    total = len(paths)
    accepted_paths: list[Path] = []
    sizes: list[int] = []
    per_file: list[ImportValidationResult] = []

    for path in paths:
        p = Path(path)
        try:
            size = p.stat().st_size
        except Exception:
            size = 0
        if size > _IMPORT_MAX_FILE_BYTES:
            per_file.append(
                ImportValidationResult(
                    path=str(p),
                    accepted=False,
                    anomalous=False,
                    rejection_reason=f"exceeds_max_size: {size} bytes",
                )
            )
            continue
        accepted_paths.append(p)
        sizes.append(size)

    lo, hi = _detect_file_size_anomalies(sizes)
    size_by_path = {str(p): s for p, s in zip(accepted_paths, sizes)}

    for p in accepted_paths:
        size = size_by_path[str(p)]
        anomalous = size > hi
        rejection_reason = ""
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
        if len(text) < _IMPORT_MIN_CONTENT_CHARS:
            anomalous = True
            rejection_reason = "near_empty"
        status, detail = _llm_import_quality_check(text)
        if status != "clean":
            anomalous = True
            rejection_reason = detail or status
        if anomalous and not rejection_reason:
            rejection_reason = "anomalous"
        per_file.append(
            ImportValidationResult(
                path=str(p),
                accepted=True,
                anomalous=anomalous,
                rejection_reason=rejection_reason,
            )
        )

    accepted = sum(1 for r in per_file if r.accepted)
    rejected = total - accepted
    return ArchiveImportResult(
        total=total, accepted=accepted, rejected=rejected, per_file=per_file
    )


# --------------------------------------------------------------------------- #
# LLM label-match criteria suggester
# --------------------------------------------------------------------------- #

_LABEL_MATCH_SYSTEM_PROMPT = (
    "You suggest match criteria for a document-management label. "
    "Given the label name and optional description, propose keywords, required "
    "phrases, contextual signals, and a match mode (any/all/contextual). "
    "Respond with a strict JSON object: "
    '{"keywords": [...], "required_phrases": [...], "contextual_signals": [...], '
    '"match_mode": "any|all|contextual", "confidence": 0..1}. '
    "Ground every term in the provided label/description; never invent."
)


def _ai_suggest_label_match_criteria(
    label_name: str, description: str | None = None
) -> dict | None:
    """Use an LLM to propose grounded label match criteria."""
    text = (label_name or "").strip()
    if not text:
        return None
    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        ctx = f"Label: {label_name}\nDescription: {description or ''}"
        req = LLMRequest(
            messages=[{"role": "user", "content": ctx}],
            system_prompt=_LABEL_MATCH_SYSTEM_PROMPT,
            max_tokens=256,
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
        parsed = json.loads(raw[start : end + 1])
        try:
            confidence = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None
        if confidence < _LABEL_MATCH_MIN_CONFIDENCE:
            return None

        match_mode = str(parsed.get("match_mode") or "any").lower()
        if match_mode not in {"any", "all", "contextual"}:
            match_mode = "any"

        haystack = (label_name + " " + (description or "")).casefold()

        def _ground(items):
            out: list[str] = []
            seen: set[str] = set()
            for item in items or []:
                term = str(item).strip().lower()
                if not term or term in seen:
                    continue
                seen.add(term)
                if term in haystack:
                    out.append(term)
                if len(out) >= _LABEL_MATCH_MAX_KEYWORDS:
                    break
            return out

        keywords = _ground(parsed.get("keywords"))
        required = _ground(parsed.get("required_phrases"))
        contextual = _ground(parsed.get("contextual_signals"))
        if not keywords and not required and not contextual:
            return None
        return {
            "keywords": keywords,
            "required_phrases": required,
            "contextual_signals": contextual,
            "match_mode": match_mode,
            "confidence": round(confidence, 4),
            "origin": "ai_generated",
        }
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# LLM workflow action parameter extractor
# --------------------------------------------------------------------------- #

_WORKFLOW_ACTION_PARAMS_SYSTEM_PROMPT = (
    "You extract structured parameters for a document-management workflow action. "
    "Given an action description and optional document context, return a strict JSON "
    "object: {\"action_type\": \"assignment|removal|notification|custom\", "
    "\"assignments\": [{\"field\": \"...\", \"value\": \"...\"}], "
    "\"removals\": [{\"field\": \"...\", \"value\": \"...\"}], "
    "\"rationale\": \"...\", \"confidence\": 0..1}. "
    "Only use field names and values that appear in the description/context. "
    "A removal value of '*' means clear all values for that field."
)


def _ai_extract_workflow_action_params(
    action_description: str,
    doc_context: dict | None = None,
    available_fields: list[str] | None = None,
) -> dict | None:
    """Use an LLM to extract grounded workflow action parameters."""
    text = (action_description or "").strip()
    if not text:
        return None
    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        ctx_parts = [f"Action: {action_description}"]
        if doc_context:
            ctx_parts.append(
                "Document context: " + json.dumps(doc_context, ensure_ascii=False)
            )
        if available_fields:
            ctx_parts.append("Available fields: " + ", ".join(available_fields))
        req = LLMRequest(
            messages=[{"role": "user", "content": "\n".join(ctx_parts)}],
            system_prompt=_WORKFLOW_ACTION_PARAMS_SYSTEM_PROMPT,
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
        parsed = json.loads(raw[start : end + 1])
        try:
            confidence = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None
        if confidence < _ACTION_PARAMS_MIN_CONFIDENCE:
            return None

        action_type = str(parsed.get("action_type") or "custom").lower()
        if action_type not in {"assignment", "removal", "notification", "custom"}:
            action_type = "custom"

        allowed = _ALLOWED_ACTION_FIELDS
        if available_fields:
            available = set(available_fields)
            allowed = allowed & available

        haystack = text.casefold()
        if doc_context:
            haystack += " " + " ".join(str(v) for v in doc_context.values()).casefold()

        def _ground_field_items(items, cap: int):
            out: list[dict] = []
            seen: set[tuple[str, str]] = set()
            for item in items or []:
                field = str(item.get("field", "")).strip().lower()
                value = str(item.get("value", "")).strip()
                if not field:
                    continue
                if field not in allowed:
                    continue
                if not value:
                    continue
                if value != "*" and value.casefold() not in haystack:
                    continue
                key = (field, value.casefold())
                if key in seen:
                    continue
                seen.add(key)
                out.append({"field": field, "value": value})
                if len(out) >= cap:
                    break
            return out

        assignments = _ground_field_items(
            parsed.get("assignments"), _ACTION_PARAMS_MAX_ASSIGNMENTS
        )
        removals = _ground_field_items(
            parsed.get("removals"), _ACTION_PARAMS_MAX_REMOVALS
        )

        if not assignments and not removals and action_type not in {"notification", "custom"}:
            return None

        rationale = str(parsed.get("rationale") or "")[:300]
        return {
            "action_type": action_type,
            "assignments": assignments,
            "removals": removals,
            "rationale": rationale,
            "confidence": round(confidence, 4),
            "origin": "ai_generated",
        }
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Near-duplicate title detection
# --------------------------------------------------------------------------- #

def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _detect_near_duplicate_titles(
    new_doc_id: str, title: str, collection_id: str, conn
) -> list[dict]:
    """Find existing titles in the collection that are unusually similar to ``title``.

    Uses token-set Jaccard + IQR outlier detection. Returns a list of candidate
    dicts sorted by descending similarity. DB errors and edge cases degrade to [].
    """
    if not title or not str(title).strip():
        return []
    tokens = set(_tokenize(str(title)))
    if len(tokens) < _NEAR_DUP_MIN_TOKENS:
        return []
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT doc_id, filename, title FROM dic_documents "
            "WHERE collection_id = %s AND doc_id != %s",
            (collection_id, new_doc_id),
        )
        rows = cur.fetchall()
        candidates: list[dict] = []
        scores: list[float] = []
        for row in rows:
            doc_id = row[0] if hasattr(row, "__getitem__") else row["doc_id"]
            filename = row[1] if hasattr(row, "__getitem__") else row["filename"]
            other_title = row[2] if hasattr(row, "__getitem__") else row["title"]
            if not other_title:
                continue
            other_tokens = set(_tokenize(str(other_title)))
            if not other_tokens:
                continue
            union = tokens | other_tokens
            score = len(tokens & other_tokens) / len(union) if union else 0.0
            candidates.append(
                {
                    "doc_id": doc_id,
                    "filename": filename,
                    "title": other_title,
                    "similarity": round(score, 4),
                }
            )
            scores.append(score)
        if len(scores) < 2:
            return []
        lo, hi = _iqr_fences(scores)
        results = [c for c in candidates if c["similarity"] > hi]
        results.sort(key=lambda c: c["similarity"], reverse=True)
        return results
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Lifecycle assignment anomaly detection
# --------------------------------------------------------------------------- #

def detect_lifecycle_assignment_anomaly(
    assignments, rules_evaluated: int = 0
) -> dict | None:
    """Detect suspicious lifecycle auto-assignment patterns."""
    try:
        if not isinstance(assignments, list):
            return None
        signals: list[str] = []

        if not assignments and rules_evaluated > 0:
            signals.append(
                f"no_assignments: {rules_evaluated} rules evaluated but none fired"
            )

        if len(assignments) > _LIFECYCLE_MAX_ASSIGNMENTS:
            signals.append(
                f"over_assignment: {len(assignments)} assignments exceed {_LIFECYCLE_MAX_ASSIGNMENTS}"
            )

        valid_scores: list[float] = []
        for a in assignments:
            if isinstance(a, dict) and "confidence" in a:
                try:
                    valid_scores.append(float(a["confidence"]))
                except (TypeError, ValueError):
                    pass
        if len(valid_scores) >= 2:
            if all(
                abs(s - _LIFECYCLE_MIN_CONFIDENCE) <= _LIFECYCLE_CONFIDENCE_EPSILON
                for s in valid_scores
            ):
                signals.append(
                    "confidence_floor_hit: all assignments near minimum confidence"
                )

        if not signals:
            return None
        return {"source": "lifecycle_assignment_anomaly", "signals": signals}
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Date plausibility anomaly
# --------------------------------------------------------------------------- #

def _detect_date_anomaly(date_str: str | None) -> str | None:
    """Signal implausible/invalid document dates."""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        d = datetime.strptime(date_str.strip(), "%Y-%m-%d")
    except ValueError:
        return "date_invalid_format"
    if d.year < _DATE_MIN_YEAR:
        return f"date_too_old:{d.year}"
    max_year = datetime.now(timezone.utc).year + _DATE_MAX_YEAR_OFFSET
    if d.year > max_year:
        return f"date_too_future:{d.year}"
    return None


# --------------------------------------------------------------------------- #
# Metadata anomaly detection (duplex-scan artifacts)
# --------------------------------------------------------------------------- #

_ANOMALY_MIN_CHARS_PER_PAGE = 10
_ANOMALY_DUPLEX_CPP_RATIO = 3.0
_ANOMALY_INPUT_CHARS = 2000

_ANOMALY_SYSTEM_PROMPT = (
    "You score document metadata anomalies for a document-management index. "
    "Given one or more pre-computed anomaly signals, return a strict JSON object "
    '{"score": 0..1, "verdict": "anomalous|suspicious|normal", "reason": "..."}. '
    "Score high when the signals strongly indicate a real problem."
)


def _ai_metadata_anomaly_detection(extraction: Extraction, filename: str) -> dict | None:
    """Flag scan/conversion artifacts such as duplex-scan blank pages."""
    text = (extraction.text or "").strip()
    page_count = extraction.page_count or 1
    signals: list[str] = []
    if page_count >= 4 and page_count % 2 == 0:
        cpp = len(text) / page_count
        floor = _ANOMALY_MIN_CHARS_PER_PAGE * _ANOMALY_DUPLEX_CPP_RATIO
        if cpp < floor:
            signals.append(
                f"possible_duplex_artifact: pages={page_count}(even) cpp={cpp:.1f}<{floor:.1f}"
            )
    if not signals:
        return None

    llm_result: dict | None = None
    if text:
        try:
            from tools.llm.provider import LLMRequest
            from tools.llm.router import LLMRouter

            snippet = text[:_ANOMALY_INPUT_CHARS]
            req = LLMRequest(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Filename: {filename}\nDocument excerpt:\n{snippet}\n\n"
                            f"Signals: {signals}\n\nScore the anomaly."
                        ),
                    }
                ],
                system_prompt=_ANOMALY_SYSTEM_PROMPT,
                max_tokens=128,
                temperature=0.0,
                skip_injection_scan=True,
                classification="CUI",
            )
            resp = LLMRouter().invoke("anomaly_detection", req)
            if resp and resp.content:
                raw = resp.content.strip()
                if raw.startswith("```"):
                    raw = raw.strip("`")
                    if raw.lower().startswith("json"):
                        raw = raw[4:]
                start, end = raw.find("{"), raw.rfind("}")
                if start != -1 and end != -1 and end > start:
                    llm_result = json.loads(raw[start : end + 1])
        except Exception:
            pass

    score = 0.5
    verdict = "suspicious"
    reason = "llm_unavailable"
    if isinstance(llm_result, dict):
        try:
            score = float(llm_result.get("score", score))
        except (TypeError, ValueError):
            pass
        v = str(llm_result.get("verdict") or "").lower()
        verdict = v if v in {"anomalous", "suspicious", "normal"} else "suspicious"
        reason = str(llm_result.get("reason") or reason)
    return {"score": score, "verdict": verdict, "reason": reason, "signals": signals}


# --------------------------------------------------------------------------- #
# Email ingestion anomaly detection
# --------------------------------------------------------------------------- #

_EMAIL_ANOMALY_MAX_ATTACHMENT_COUNT = 10
_EMAIL_ANOMALY_MAX_ATTACHMENT_SIZE_MB = 25.0
_EMAIL_ANOMALY_MAX_AGE_DAYS = 365
_EMAIL_ANOMALY_MAX_SUBJECT_LEN = 500

_EMAIL_ANOMALY_SYSTEM_PROMPT = (
    "You score email ingestion anomalies. Given one or more pre-computed "
    "signals, return a strict JSON object "
    '{"score": 0..1, "verdict": "anomalous|suspicious|normal", "reason": "..."}. '
    "Score high when the signals strongly indicate a real problem."
)


def _ai_email_ingestion_anomaly_detection(
    correspondence: dict | None = None,
    attachment_count: int = 0,
    attachment_size_bytes: int = 0,
    email_age_days: float | None = None,
) -> dict | None:
    """Detect suspicious email ingestion patterns before/after extraction."""
    signals: list[str] = []
    if attachment_count > _EMAIL_ANOMALY_MAX_ATTACHMENT_COUNT:
        signals.append(
            f"high_attachment_count:{attachment_count}>{_EMAIL_ANOMALY_MAX_ATTACHMENT_COUNT}"
        )
    max_size_bytes = int(_EMAIL_ANOMALY_MAX_ATTACHMENT_SIZE_MB * 1024 * 1024)
    if attachment_size_bytes > max_size_bytes:
        signals.append("large_attachments")
    if email_age_days is not None and email_age_days > _EMAIL_ANOMALY_MAX_AGE_DAYS:
        signals.append(f"stale_email:{email_age_days}>{_EMAIL_ANOMALY_MAX_AGE_DAYS}")
    if correspondence:
        subject = correspondence.get("subject") or ""
        if len(subject) > _EMAIL_ANOMALY_MAX_SUBJECT_LEN:
            signals.append(
                f"long_subject:{len(subject)}>{_EMAIL_ANOMALY_MAX_SUBJECT_LEN}"
            )
        from_name = str(correspondence.get("from_name") or "").strip()
        from_email = str(correspondence.get("from_email") or "").strip()
        if not from_name and not from_email:
            signals.append("missing_sender")
    if not signals:
        return None

    llm_result: dict | None = None
    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        req = LLMRequest(
            messages=[{"role": "user", "content": f"Email signals: {signals}"}],
            system_prompt=_EMAIL_ANOMALY_SYSTEM_PROMPT,
            max_tokens=128,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("anomaly_detection", req)
        if resp and resp.content:
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.lower().startswith("json"):
                    raw = raw[4:]
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                llm_result = json.loads(raw[start : end + 1])
    except Exception:
        pass

    score = 0.5
    verdict = "suspicious"
    reason = "llm_unavailable"
    if isinstance(llm_result, dict):
        try:
            score = float(llm_result.get("score", score))
        except (TypeError, ValueError):
            pass
        v = str(llm_result.get("verdict") or "").lower()
        verdict = v if v in {"anomalous", "suspicious", "normal"} else "suspicious"
        reason = str(llm_result.get("reason") or reason)
    return {"score": score, "verdict": verdict, "reason": reason, "signals": signals}


# --------------------------------------------------------------------------- #
# Routing metadata extraction
# --------------------------------------------------------------------------- #

_ROUTING_INPUT_CHARS = 6000
_ROUTING_MIN_CONFIDENCE = 0.70
_ROUTING_MAX_KEYWORDS = 10
_ROUTING_PRIORITIES = {"routine", "urgent", "immediate", "time_sensitive"}

_ROUTING_SYSTEM_PROMPT = (
    "You extract routing metadata for a single ingested document. "
    "Given the document text, identify the originator (person), originator_org "
    "(organization), routing_keywords, and priority. Respond with a strict JSON "
    "object: {\"originator\": \"...\"|null, \"originator_org\": \"...\"|null, "
    "\"routing_keywords\": [...], \"priority\": \"routine|urgent|immediate|time_sensitive\", "
    "\"confidence\": 0..1}. Ground every value in the text; never invent."
)


def _ai_extract_routing_metadata(text: str, filename: str) -> dict | None:
    """Propose routing metadata grounded in the document text."""
    snippet = (text or "").strip()
    if not snippet:
        return None
    snippet = snippet[:_ROUTING_INPUT_CHARS]
    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        req = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Filename: {filename}\nDocument text:\n{snippet}\n\n"
                        "Produce the routing metadata JSON."
                    ),
                }
            ],
            system_prompt=_ROUTING_SYSTEM_PROMPT,
            max_tokens=256,
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
        parsed = json.loads(raw[start : end + 1])
        try:
            confidence = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None
        if confidence < _ROUTING_MIN_CONFIDENCE:
            return None

        haystack = snippet.casefold()

        def _grounded_token(s):
            return str(s or "").strip().casefold()

        originator = parsed.get("originator")
        if originator and _grounded_token(originator) not in haystack:
            originator = None
        originator_org = parsed.get("originator_org")
        if originator_org and _grounded_token(originator_org) not in haystack:
            originator_org = None

        keywords: list[str] = []
        seen: set[str] = set()
        for kw in parsed.get("routing_keywords") or []:
            k = str(kw).strip()
            if not k or k.lower() in seen:
                continue
            if k.lower() not in haystack:
                continue
            seen.add(k.lower())
            keywords.append(k)
            if len(keywords) >= _ROUTING_MAX_KEYWORDS:
                break

        priority = str(parsed.get("priority") or "routine").lower()
        if priority not in _ROUTING_PRIORITIES:
            priority = "routine"

        return {
            "originator": originator,
            "originator_org": originator_org,
            "routing_keywords": keywords,
            "priority": priority,
            "confidence": round(confidence, 4),
            "origin": "ai_generated",
        }
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Workflow mutation proposals
# --------------------------------------------------------------------------- #

_MUTATION_MAX_FIELDS = 20
_MUTATION_MIN_CONFIDENCE = 0.70
_MUTATION_FIELD_MIN_CONFIDENCE = 0.65
_MUTATION_STORAGE_PATH_MAX_LEN = 260
_MUTATION_STRING_MAX_LEN = 256
_MUTATION_INT_MIN = 0
_MUTATION_INT_MAX = 1_000_000_000

_MUTATION_SYSTEM_PROMPT = (
    "You propose workflow field mutations for a single ingested document. "
    "Given the document text and a JSON schema of custom fields, return a strict "
    "JSON object: {\"mutations\": [{\"field\": \"...\", \"value\": ..., \"confidence\": 0..1}], "
    "\"storage_path\": \"...\"|null, \"confidence\": 0..1}. "
    "Only propose values that appear in the text; never invent."
)


def _validate_mutation_value(value, field_def: dict, haystack: str):
    """Validate and normalize a single proposed mutation value."""
    ftype = field_def["type"]
    if ftype == "string":
        s = str(value).strip()
        if len(s) > _MUTATION_STRING_MAX_LEN:
            return None
        norm = " ".join(s.split()).lower()
        if norm and norm not in haystack:
            return None
        return s
    if ftype == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"true", "yes", "1"}:
                return True
            if v in {"false", "no", "0"}:
                return False
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        return None
    if ftype == "integer":
        try:
            if isinstance(value, bool):
                raise ValueError
            v = int(value)
        except Exception:
            return None
        if v < _MUTATION_INT_MIN or v > _MUTATION_INT_MAX:
            return None
        return v
    if ftype == "monetary":
        try:
            v = float(value)
        except Exception:
            return None
        if v < 0:
            return None
        return v
    if ftype == "date":
        s = str(value).strip()
        try:
            datetime.strptime(s, "%Y-%m-%d")
        except Exception:
            return None
        return s
    if ftype == "select":
        s = str(value).strip()
        opts = {o.lower(): o for o in field_def.get("options", [])}
        if s.lower() not in opts:
            return None
        return opts[s.lower()]
    if ftype == "url":
        s = str(value).strip()
        if "://" not in s:
            return None
        alnum_haystack = re.sub(r"[^a-z0-9]", "", haystack)
        core = re.sub(r"[^a-z0-9]", "", s.lower())
        if core and core not in alnum_haystack:
            return None
        return s
    return None


def _ai_propose_workflow_mutations(text: str, fields: list[dict]) -> dict | None:
    """Use an LLM to propose grounded mutations for caller-defined workflow fields."""
    snippet = (text or "").strip()
    if not snippet:
        return None
    valid_fields: list[dict] = []
    seen_names: set[str] = set()
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or "").strip().lower()
        ftype = str(f.get("type") or "").strip().lower()
        if not name or ftype not in {
            "string", "boolean", "integer", "monetary", "date", "select", "url",
        }:
            continue
        if name in seen_names:
            continue
        seen_names.add(name)
        options = [str(o).strip() for o in (f.get("options") or []) if str(o).strip()]
        if ftype == "select" and not options:
            continue
        valid_fields.append({"name": name, "type": ftype, "options": options})
    if not valid_fields:
        return None
    valid_fields = valid_fields[:_MUTATION_MAX_FIELDS]
    haystack = snippet.casefold()
    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        schema = json.dumps(valid_fields, ensure_ascii=False)
        req = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Document text:\n{snippet[:4000]}\n\n"
                        f"Fields schema:\n{schema}\n\n"
                        "Propose the mutations JSON."
                    ),
                }
            ],
            system_prompt=_MUTATION_SYSTEM_PROMPT,
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
        parsed = json.loads(raw[start : end + 1])
        try:
            overall = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None
        if overall < _MUTATION_MIN_CONFIDENCE:
            return None

        storage_path = str(parsed.get("storage_path") or "").strip()
        if storage_path and len(storage_path) > _MUTATION_STORAGE_PATH_MAX_LEN:
            storage_path = ""

        field_map = {f["name"]: f for f in valid_fields}
        mutations: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for m in parsed.get("mutations") or []:
            if not isinstance(m, dict):
                continue
            fname = str(m.get("field") or "").strip().lower()
            if fname not in field_map:
                continue
            try:
                fconf = float(m.get("confidence", overall))
            except (TypeError, ValueError):
                fconf = overall
            if fconf < _MUTATION_FIELD_MIN_CONFIDENCE:
                continue
            validated = _validate_mutation_value(m.get("value"), field_map[fname], haystack)
            if validated is None:
                continue
            key = (fname, str(validated).lower())
            if key in seen:
                continue
            seen.add(key)
            mutations.append(
                {"field": fname, "value": validated, "confidence": round(fconf, 4)}
            )

        if not mutations and not storage_path:
            return None
        return {
            "mutations": mutations,
            "storage_path": storage_path or None,
            "confidence": round(overall, 4),
            "origin": "ai_generated",
        }
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Identifier anomaly detection
# --------------------------------------------------------------------------- #

def _detect_identifier_anomaly(items: list[dict]) -> str | None:
    """Surface quality signals from a list of extracted identifiers."""
    if not items:
        return None
    if len(items) >= _IDENTIFIER_MAX_ITEMS:
        return "cap_hit"
    if len(items) < 2:
        return None
    confs = [float(i.get("confidence", 0.0)) for i in items]
    first = round(confs[0], 3)
    if all(round(c, 3) == first for c in confs):
        return "uniform_confidence"
    if any(c >= 1.0 for c in confs):
        return "over_confidence"
    return None


# --------------------------------------------------------------------------- #
# Re-enrichment (post-update metadata refresh)
# --------------------------------------------------------------------------- #

def re_enrich_metadata(
    doc_id: str,
    *,
    extract_identifiers: bool = True,
    extract_correspondence: bool = True,
) -> dict | None:
    """Refresh AI metadata proposals for an already-ingested document."""
    conn = get_connection()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT doc_id, filename FROM dic_documents WHERE doc_id = %s",
            (doc_id,),
        ).fetchone()
        if not row:
            return None
        filename = row[1] if hasattr(row, "__getitem__") else row.get("filename", "")
        chunk_rows = conn.execute(
            "SELECT rc.content FROM rag_chunks rc "
            "JOIN dic_chunk_links dcl ON dcl.rag_chunk_id = rc.id "
            "WHERE dcl.doc_id = %s ORDER BY dcl.chunk_index",
            (doc_id,),
        ).fetchall()
        chunks: list[str] = []
        for r in chunk_rows:
            content = r[0] if hasattr(r, "__getitem__") else r.get("content", "")
            chunks.append(content)
        text = "\n".join(chunks)
        proposals: dict = {}
        if text.strip():
            md = _ai_metadata_extraction(text, filename or "")
            if md:
                proposals.update(md)
            if extract_identifiers:
                ids = _ai_extract_identifiers(text)
                if ids:
                    proposals["identifiers"] = ids
            if extract_correspondence:
                corr = _ai_extract_correspondence(text)
                if corr:
                    proposals["correspondence"] = corr
        return {"doc_id": doc_id, "filename": filename, "proposals": proposals}
    finally:
        conn.close()
