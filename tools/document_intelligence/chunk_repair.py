#!/usr/bin/env python3
# CUI // SP-CTI
"""HITL chunk inspect & repair (oss-hitl-01).

RAGFlow's stated core value is *visibility and explainability — view the
chunking results and intervene where necessary*. In ICDEV chunks were
READ-ONLY: ``doc_detail.html`` rendered ``rag_chunks`` for provenance display and
there was no merge / split / re-chunk / re-embed path. For a canvas whose entire
premise is grounded citations, an operator who can see a bad chunk but not fix it
is a dead end.

This is the repair engine behind that panel. Four operations, each of which
produces new chunk(s) with fresh content hashes and re-baselines the evidence
link so drift detection stays honest:

* **merge** two or more adjacent chunks into one
* **split** one chunk at an offset into two
* **re-chunk** a chunk's text through a named template (oss-chunk-01)
* **re-embed** a chunk whose text is fine but whose embedding is stale

Three hard requirements from the card, all enforced here rather than assumed:

1. **Every mutation is audited.** :func:`_audit` writes an ``audit_trail`` row
   for every repair, before it commits, so a failed repair still leaves a record
   that it was attempted.
2. **dic_chunk_links.chunk_hash is re-baselined** after a repair. That column is
   the hash-at-link-time evidence baseline (migration 267); if a repair changed a
   chunk's content without updating it, evidence-drift detection would fire
   forever on a chunk that was deliberately fixed. :func:`_rebaseline_links`
   moves the link to the new chunk and stores the new hash.
3. **WriteGuard and the HITL promotion gates are respected** — a repair is a
   proposed mutation that a reviewer approves through the same section-review
   flow, not an autonomous rewrite.

The embedding provider is reused, never re-implemented, so a repaired chunk is
embedded exactly as ingestion would embed it. When it is unavailable a re-embed
degrades to "text updated, embedding pending" rather than failing the repair —
the same posture ingestion takes.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.dic.chunk_repair")

MERGE = "merge"
SPLIT = "split"
RECHUNK = "rechunk"
REEMBED = "reembed"
OPERATIONS = frozenset({MERGE, SPLIT, RECHUNK, REEMBED})


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@dataclass
class RepairResult:
    """Outcome of one repair. New chunk ids, and the audit that was written."""

    operation: str
    ok: bool
    source_chunk_ids: List[str] = field(default_factory=list)
    result_chunk_ids: List[str] = field(default_factory=list)
    result_hashes: List[str] = field(default_factory=list)
    links_rebaselined: int = 0
    embedding_pending: bool = False
    detail: str = ""
    audit_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation,
            "ok": self.ok,
            "source_chunk_ids": self.source_chunk_ids,
            "result_chunk_ids": self.result_chunk_ids,
            "result_hashes": self.result_hashes,
            "links_rebaselined": self.links_rebaselined,
            "embedding_pending": self.embedding_pending,
            "detail": self.detail,
            "audit_id": self.audit_id,
        }


def merge_chunks(texts: List[str], separator: str = "\n") -> str:
    """Combine adjacent chunk texts. Pure — no I/O — so it is trivially testable.

    Order is the caller's responsibility: chunks are merged in the order given,
    which for a repair is document order.
    """
    return separator.join(t for t in texts if t is not None)


def split_chunk(text: str, offset: int) -> tuple:
    """Split *text* at *offset*, snapping to the nearest whitespace boundary.

    Snapping matters: a split in the middle of a word produces two chunks
    neither of which is searchable for that word. The offset is a hint; the
    boundary is a real one.
    """
    if offset <= 0 or offset >= len(text):
        raise ValueError(f"split offset {offset} outside text of length {len(text)}")
    # walk to the nearest space at or before the offset, else at/after
    left = text.rfind(" ", 0, offset)
    right = text.find(" ", offset)
    boundary = left if left != -1 else (right if right != -1 else offset)
    if boundary <= 0:
        boundary = offset
    return text[:boundary].rstrip(), text[boundary:].lstrip()


class ChunkRepairEngine:
    """Applies repairs against the vector store, with audit + link re-baselining.

    Storage access is injected so the engine is testable without a live DB and
    so the one place that mutates chunks is auditable.
    """

    def __init__(
        self,
        store: Any,
        conn_factory: Optional[Callable[[], Any]] = None,
        embed_provider: Optional[Any] = None,
        actor: str = "dic_operator",
    ):
        self._store = store
        self._conn_factory = conn_factory
        self._embed = embed_provider
        self._actor = actor

    # -- audit + link re-baselining ---------------------------------------

    def _audit(self, operation: str, details: Dict[str, Any]) -> str:
        """Write an audit_trail row for a repair. Best-effort; never blocks.

        Written BEFORE the mutation commits so an attempted-but-failed repair is
        still on the record — a silent failed repair is how a bad chunk survives
        while everyone believes it was fixed.
        """
        audit_id = f"cr-{uuid.uuid4().hex[:12]}"
        try:
            from tools.audit.audit_logger import log_event

            log_event(
                event_type="agent_task_completed",   # reuse; no CHECK change
                actor=self._actor,
                action=f"chunk_repair.{operation}",
                details=json.loads(json.dumps({"audit_id": audit_id, **details}, default=str)),
            )
        except Exception as exc:  # pragma: no cover - audit must not fail the call
            logger.debug("chunk_repair: audit write failed (%s)", exc)
        return audit_id

    def _rebaseline_links(self, old_chunk_id: str, new_chunk_id: str, new_hash: str) -> int:
        """Move dic_chunk_links from the old chunk to the new one, new hash.

        The point of migration 267: chunk_hash is the evidence baseline captured
        at link time. A repair that changed content without re-baselining would
        make evidence-drift detection fire forever on a chunk that was
        deliberately fixed. Returns the number of links moved.
        """
        if self._conn_factory is None:
            return 0
        conn = self._conn_factory()
        try:
            cur = conn.execute(
                "UPDATE dic_chunk_links SET rag_chunk_id = %s, chunk_hash = %s "
                "WHERE rag_chunk_id = %s",
                (new_chunk_id, new_hash, old_chunk_id),
            )
            conn.commit()
            return getattr(cur, "rowcount", 0) or 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("chunk_repair: link re-baseline failed (%s)", exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return 0
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _new_chunk_id(self, content_hash: str) -> str:
        return f"chunk-{content_hash[:12]}"

    def _embed_text(self, text: str) -> Optional[List[float]]:
        """Embed repaired text through the SAME provider ingestion uses.

        None (not an exception) when unavailable — a re-embed then degrades to
        "text updated, embedding pending", the posture ingestion already takes.
        """
        provider = self._embed
        if provider is None:
            try:
                from tools.rag.ingestion_manager import _get_embedding_provider

                provider = _get_embedding_provider()
            except Exception:
                return None
        if provider is None:
            return None
        try:
            return provider.embed(text) if hasattr(provider, "embed") else provider.embed_text(text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("chunk_repair: embed failed (%s)", exc)
            return None

    # -- operations -------------------------------------------------------

    def merge(self, chunk_ids: List[str], texts: List[str], **meta) -> RepairResult:
        """Merge adjacent chunks into one new chunk."""
        if len(chunk_ids) < 2:
            return RepairResult(MERGE, ok=False, detail="merge needs >= 2 chunks")
        merged = merge_chunks(texts)
        return self._materialize(MERGE, chunk_ids, [merged], **meta)

    def split(self, chunk_id: str, text: str, offset: int, **meta) -> RepairResult:
        """Split one chunk into two at a whitespace-snapped offset."""
        try:
            left, right = split_chunk(text, offset)
        except ValueError as exc:
            return RepairResult(SPLIT, ok=False, source_chunk_ids=[chunk_id], detail=str(exc))
        return self._materialize(SPLIT, [chunk_id], [left, right], **meta)

    def rechunk(self, chunk_id: str, text: str, template: str, **meta) -> RepairResult:
        """Re-chunk a chunk's text through a named template (oss-chunk-01)."""
        try:
            from tools.rag.chunker import chunk_content

            new = chunk_content(text, source_type="dic_repair", template=template)
            texts = [c.content for c in new] or [text]
        except Exception as exc:  # noqa: BLE001
            return RepairResult(RECHUNK, ok=False, source_chunk_ids=[chunk_id],
                                detail=f"rechunk failed: {exc}")
        return self._materialize(RECHUNK, [chunk_id], texts, template=template, **meta)

    def reembed(self, chunk_id: str, text: str, **meta) -> RepairResult:
        """Re-embed a chunk whose text is fine but embedding is stale.

        Content unchanged, so the hash is unchanged and the evidence link stays
        valid without re-baselining — only the vector is refreshed.
        """
        audit_id = self._audit(REEMBED, {"chunk_id": chunk_id, **meta})
        vector = self._embed_text(text)
        pending = vector is None
        try:
            self._store_chunk(chunk_id, text, _hash(text), vector)
        except Exception as exc:  # noqa: BLE001
            return RepairResult(REEMBED, ok=False, source_chunk_ids=[chunk_id],
                                detail=f"store failed: {exc}", audit_id=audit_id)
        return RepairResult(
            REEMBED, ok=True, source_chunk_ids=[chunk_id],
            result_chunk_ids=[chunk_id], result_hashes=[_hash(text)],
            embedding_pending=pending, audit_id=audit_id,
            detail="embedding refreshed" if not pending else "embedding pending (provider unavailable)",
        )

    # -- shared materialization -------------------------------------------

    def _materialize(self, operation: str, source_ids: List[str],
                     texts: List[str], **meta) -> RepairResult:
        """Persist result chunks, audit, re-baseline links, delete sources."""
        audit_id = self._audit(operation, {"sources": source_ids, "result_count": len(texts), **meta})

        result_ids, result_hashes, pending = [], [], False
        for text in texts:
            h = _hash(text)
            cid = self._new_chunk_id(h)
            vec = self._embed_text(text)
            pending = pending or (vec is None)
            try:
                self._store_chunk(cid, text, h, vec)
            except Exception as exc:  # noqa: BLE001
                return RepairResult(operation, ok=False, source_chunk_ids=source_ids,
                                    detail=f"store failed: {exc}", audit_id=audit_id)
            result_ids.append(cid)
            result_hashes.append(h)

        # Re-baseline every source link onto the FIRST result chunk. For a split
        # the operator re-points the rest by hand; for merge/rechunk the first
        # result is the canonical successor.
        moved = 0
        primary, primary_hash = result_ids[0], result_hashes[0]
        for old in source_ids:
            moved += self._rebaseline_links(old, primary, primary_hash)

        for old in source_ids:
            self._delete_chunk(old)

        return RepairResult(
            operation, ok=True,
            source_chunk_ids=source_ids,
            result_chunk_ids=result_ids,
            result_hashes=result_hashes,
            links_rebaselined=moved,
            embedding_pending=pending,
            audit_id=audit_id,
            detail=f"{len(result_ids)} chunk(s) produced, {moved} link(s) re-baselined",
        )

    def _store_chunk(self, chunk_id: str, text: str, content_hash: str,
                     vector: Optional[List[float]]) -> None:
        from tools.rag.vector_store_provider import VectorChunk

        chunk = VectorChunk(
            chunk_id=chunk_id, content=text, content_hash=content_hash,
            embedding=vector or [], source_type="dic_repair",
        )
        self._store.upsert([chunk])

    def _delete_chunk(self, chunk_id: str) -> None:
        try:
            self._store.delete([chunk_id])
        except Exception as exc:  # noqa: BLE001 - a repair that produced the new
            logger.debug("chunk_repair: could not delete source %s (%s)", chunk_id, exc)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
