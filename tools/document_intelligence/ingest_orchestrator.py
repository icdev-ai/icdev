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
from tools.rag.chunker import chunk_content


# --------------------------------------------------------------------------- #
# Provider routing (extension -> extractor)
# --------------------------------------------------------------------------- #

# Extensions handled directly by the built-in text/markup extractor. Binary
# formats (.pdf/.docx/.pptx/.xlsx/images) are delegated to the dic-ingest-02
# provider package when present.
_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv",
    ".json", ".yaml", ".yml", ".xml", ".html", ".htm", ".py",
    ".sql", ".ini", ".cfg", ".toml",
}


@dataclass
class Extraction:
    """Normalized output of an extractor."""

    text: str
    provider: str
    content_type: str
    page_count: int = 1
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _strip_html(raw: str) -> str:
    import re

    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = re.sub(r"&nbsp;", " ", raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _select_extractor(path: Path) -> Extraction:
    """Pick a provider by extension and extract text.

    Prefers the dic-ingest-02 provider registry when importable; otherwise
    falls back to a built-in text/markup reader.
    """
    ext = path.suffix.lower()

    provider = _try_provider_package(path, ext)
    if provider is not None:
        return provider

    if ext in _TEXT_EXTS:
        raw = path.read_text(encoding="utf-8", errors="replace")
        if ext in (".html", ".htm"):
            text = _strip_html(raw)
            ctype = "text/html"
        else:
            text = raw
            ctype = "text/plain"
        return Extraction(
            text=text,
            provider="builtin-text",
            content_type=ctype,
            page_count=1,
            title=path.stem,
        )

    # Last-resort: best-effort utf-8 decode. Binary blobs degrade to whatever
    # decodes; callers can re-ingest once a real provider lands.
    raw = path.read_text(encoding="utf-8", errors="replace")
    return Extraction(
        text=raw,
        provider="builtin-fallback",
        content_type="application/octet-stream",
        page_count=1,
        title=path.stem,
    )


def _try_provider_package(path: Path, ext: str) -> Extraction | None:
    """Best-effort bridge to the dic-ingest-02 provider registry.

    The provider package is optional. We probe a couple of conventional entry
    points so this orchestrator works both before and after dic-ingest-02
    lands, without a hard import dependency.
    """
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
]


def _ensure_schema(conn) -> None:
    cur = conn.cursor()
    for ddl in _SCHEMA:
        cur.execute(ddl)
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
            "errors": self.errors,
        }


# --------------------------------------------------------------------------- #
# Embedding + vector-store upsert (mirrors ingestion_manager.ingest_source path)
# --------------------------------------------------------------------------- #

def _embed_and_store(chunks: list, tenant_id: str, errors: list[str]) -> int:
    """Embed new chunks and upsert into the vector store. Returns count embedded."""
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
    conn=None,
) -> IngestOutcome:
    """Route a file through provider -> RAG ingest -> KG bridge -> DIC rows.

    Args:
        path: file to ingest.
        collection_id: target RAG/DIC collection.
        tenant_id/classification: security stamp; default from security context.
        created_by: user id recorded on the initial version row.
        embed: when True, embed + upsert chunks into the vector store.
        bridge_kg: when True, extract entities/relationships into the KG.
        conn: optional DB connection (else an RLS-aware one is opened).
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"not a file: {path}")

    tid, cls = _resolve_context(tenant_id, classification)
    errors: list[str] = []

    # 1) Extract.
    extraction = _select_extractor(p)
    text = extraction.text or ""

    doc_id = _doc_id(collection_id, str(p))
    source_id = doc_id  # rag source id for these chunks

    # 2) Chunk (reuse chunker). chunk_content returns VectorChunk objects whose
    #    .chunk_id is the canonical rag_chunks id used by the vector store + KG.
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

    # 3) Embed + upsert into the vector store (same path ingest_source uses).
    chunks_embedded = 0
    if embed and chunks:
        chunks_embedded = _embed_and_store(chunks, tid, errors)

    # 4) DIC bookkeeping rows.
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        _ensure_schema(conn)
        now = _now()
        version_id = f"{doc_id}_v1"
        content_hash = _sha256(text)
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
                extraction.title or p.stem, p.stat().st_size, content_hash,
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
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass

    # 5) KG bridge (best-effort). ingest_chunk reads rag_chunks by id, so this
    #    only finds content when embedding upserted the chunk above.
    kg_entities = 0
    kg_rels = 0
    if bridge_kg and chunks and chunks_embedded:
        try:
            from tools.rag.rag_to_kg_ingester import ingest_chunk

            kg_conn = get_connection()
            try:
                for chunk in chunks:
                    cid = getattr(chunk, "chunk_id", None)
                    if not cid:
                        continue
                    try:
                        summary = ingest_chunk(kg_conn, cid)
                    except Exception as e:
                        errors.append(f"kg chunk failed: {e}")
                        continue
                    kg_entities += int(summary.get("nodes_written", 0) or 0)
                    kg_rels += int(summary.get("edges_written", 0) or 0)
            finally:
                try:
                    kg_conn.close()
                except Exception:
                    pass
        except Exception as e:
            errors.append(f"kg bridge unavailable: {e}")

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
        errors=errors,
    )
