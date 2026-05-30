#!/usr/bin/env python3
"""DIC ingest orchestrator — route file -> provider -> RAG ingest + KG bridge + dic rows.

[TEMPLATE: CUI // SP-CTI]

Given a file path and a collection id this module:

1. Picks an extractor (provider) by file extension. If the dic-ingest-02
   provider package (``tools.document_intelligence.providers``) is installed it
   is preferred; otherwise a small built-in text/markup extractor is used.
2. REUSES the RAG layer: ``icdev.tools.rag.chunker.chunk_content`` to chunk and
   ``icdev.tools.rag.ingestion_manager.IngestionManager.ingest_source`` to
   embed + upsert chunks into the vector store.
3. Bridges each chunk into the Knowledge Graph via
   ``icdev.tools.rag.rag_to_kg_ingester.ingest_chunk_to_kg``.
4. Writes DIC bookkeeping rows: ``dic_documents`` + an initial
   ``dic_versions(origin='human_authored', status='approved')`` row, plus
   ``dic_chunk_links`` mapping each rag chunk back to the document and its
   page/section.

Every row is stamped with ``tenant_id``/``classification`` taken from the
caller's security context (``get_security_context()``) or explicit overrides,
so writes participate in RBAC+ABAC+RLS access control (dic-authz-01).

Embedding and KG bridging are best-effort: if the vector store or LLM router is
unavailable (e.g. air-gapped/headless without credentials) the DIC rows are
still written and the failure is reported in the result, never raised.
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

from icdev.tools.rag.chunker import ChunkingConfig, chunk_content
from icdev.tools.rag.ingestion_manager import IngestionManager

try:  # storage may live under either namespace depending on install mode
    from icdev.tools.db.storage import get_connection, get_security_context
except Exception:  # pragma: no cover - fallback to shim
    from tools.db.storage import get_connection, get_security_context


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
    # Optional per-chunk-ish hints; aligned by chunk index when available.
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

    # 1) Prefer the dic-ingest-02 provider package if available.
    provider = _try_provider_package(path, ext)
    if provider is not None:
        return provider

    # 2) Built-in extractor for text/markup files.
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

    # 3) Last-resort: read bytes as best-effort utf-8 (covers unknown text-ish
    #    files). Binary blobs degrade to whatever decodes; callers see the
    #    provider name and can re-ingest once a real provider lands.
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
        # Providers expose .extract(path) -> object with .text/.pages/etc.
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
        # Provider exists but failed; fall back to built-in extraction.
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


def _rag_source_id(text: str) -> str:
    """Mirror IngestionManager._make_source_id for source_type='text'."""
    h = hashlib.sha256(f"text:{text}".encode()).hexdigest()[:16]
    return f"src_{h}"


def _doc_id(collection_id: str, filepath: str) -> str:
    h = hashlib.sha256(f"{collection_id}:{filepath}".encode()).hexdigest()[:16]
    return f"dic_doc_{h}"


def _resolve_context(tenant_id: str | None, classification: str | None) -> tuple[str, str]:
    ctx = {}
    try:
        ctx = get_security_context() or {}
    except Exception:
        ctx = {}
    tid = tenant_id or ctx.get("tenant_id") or "default"
    cls = classification or ctx.get("classification") or "UNCLASSIFIED"
    return tid, cls


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
    chunk_config: ChunkingConfig | None = None,
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
        chunk_config: chunking knobs (defaults to RAG defaults).
        conn: optional DB connection (else an RLS-aware one is opened).
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"not a file: {path}")

    tid, cls = _resolve_context(tenant_id, classification)
    cfg = chunk_config or ChunkingConfig()
    errors: list[str] = []

    # 1) Extract.
    extraction = _select_extractor(p)
    text = extraction.text or ""

    # 2) Chunk (reuse chunker) — gives us deterministic chunk indices + texts
    #    for KG bridging and chunk links.
    chunks = chunk_content(text, cfg, metadata={"filename": p.name})
    source_id = _rag_source_id(text)

    # 3) Embed + upsert via the RAG ingestion manager (reuse ingest_source).
    chunks_embedded = 0
    if embed and text:
        try:
            mgr = IngestionManager(collection=collection_id, config=cfg)
            res = mgr.ingest_source(
                text,
                source_type="text",
                metadata={
                    "filename": p.name,
                    "filepath": str(p),
                    "provider": extraction.provider,
                    "content_type": extraction.content_type,
                    "tenant_id": tid,
                    "classification": cls,
                    "dic_collection": collection_id,
                },
                collection=collection_id,
            )
            source_id = res.source_id or source_id
            chunks_embedded = res.chunks_embedded
            errors.extend(res.errors or [])
        except Exception as e:  # vector store / LLM unavailable
            errors.append(f"embed failed: {e}")

    # 4) DIC bookkeeping rows.
    own_conn = conn is None
    if own_conn:
        conn = get_connection(tenant_id=tid, classification=cls)
    try:
        _ensure_schema(conn)
        now = _now()
        doc_id = _doc_id(collection_id, str(p))
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
            rag_chunk_id = f"{source_id}_chunk_{i}"
            md = chunk.metadata or {}
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
                    i, page, section, now, tid, cls,
                ),
            )
        conn.commit()
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass

    # 5) KG bridge (best-effort, after rows are durable).
    kg_entities = 0
    kg_rels = 0
    if bridge_kg and chunks:
        try:
            from icdev.tools.rag.rag_to_kg_ingester import ingest_chunk_to_kg

            for i, chunk in enumerate(chunks):
                rag_chunk_id = f"{source_id}_chunk_{i}"
                summary = ingest_chunk_to_kg(
                    rag_chunk_id,
                    chunk.text,
                    source_id=source_id,
                    collection=collection_id,
                )
                kg_entities += int(summary.get("entities", 0) or 0)
                kg_rels += int(summary.get("relationships", 0) or 0)
        except Exception as e:
            errors.append(f"kg bridge failed: {e}")

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
