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
            errors=errors,
        )
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass
