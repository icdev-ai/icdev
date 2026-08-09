#!/usr/bin/env python3
# CUI // SP-CTI
"""RAPTOR summary hierarchy above the flat rag_chunks leaves (RCE, D-RAG-RCE-1).

RCE (RAG Capability Evolution) grows a RAPTOR-style tree of *summaries* on top
of the existing flat ``rag_chunks`` retrieval leaves:

    level 0  →  leaf chunks (live in ``rag_chunks`` — NOT stored here)
    level 1  →  parent summaries (each summarizes a group of sibling leaves)
    level 2  →  root / document summary (summarizes the level-1 summaries)

Design constraints (see CLAUDE.md guardrails):
    * Pure Python, air-gap safe — brute-force cosine over the (small) summary
      tier; no pgvector / numpy hard dependency.
    * Summary generation uses the cheap-LLM router pattern (mirrors
      ``tools/rag/evaluator.py::_llm_judge``) and NO-OPs gracefully (returns
      "" → nothing written) when the LLM is unavailable.
    * Embeddings via the provider abstraction (``tools.llm.get_embedding_provider``),
      never a direct Ollama call.
    * New behavior is default-OFF (``rag.raptor.enabled: false``) so default
      retrieval and existing tests are unchanged.
    * TRUST: summaries are LLM-generated. They carry provenance and MUST NOT be
      surfaced as a citation source — citations resolve to leaf chunks. The
      retriever tags summary results ``metadata.is_summary = True``.

The table ``rag_chunk_summaries`` is co-located with ``rag_chunks`` in the
vector-store DB (``data/rag/rag_vectors.db`` for SQLite; the main DB for PG)
and created via ``CREATE TABLE IF NOT EXISTS`` — mirroring how ``rag_chunks``
is created by ``SQLiteVectorStore._init_schema()``.

Usage:
    python tools/rag/raptor.py --build --json
    python tools/rag/raptor.py --build --source <source_id> --dry-run --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import sys

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from tools.rag.vector_store_provider import SearchResult

try:
    from tools.db.storage import get_backend
except ImportError:  # pragma: no cover - storage always ships get_backend
    def get_backend() -> str:
        return "sqlite"


BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "rag" / "rag_vectors.db"

# ---------------------------------------------------------------------------
# Module-level fallbacks — overridable from args/rag_config.yaml under
# rag.raptor. Change config, not code.
# ---------------------------------------------------------------------------
_MAX_LEVELS_DEFAULT = 2       # 1 = parent summaries only; 2 = + root summary
_GROUP_SIZE_DEFAULT = 5       # sibling leaves summarized into one parent
_SUMMARY_MAX_TOKENS = 512     # LLM summary token budget
_LLM_FUNCTION_DEFAULT = "rag_evaluate"  # router function (known-good routing)
_SUMMARY_TOP_K_DEFAULT = 20   # default summary results returned by search()


# ---------------------------------------------------------------------------
# float32 BLOB packing (same wire format as sqlite_vector_store)
# ---------------------------------------------------------------------------


def _embedding_to_blob(embedding: List[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


def _blob_to_embedding(blob: bytes) -> List[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity with numpy fast path + pure-python fallback."""
    try:
        import numpy as np

        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        dot = float(np.dot(va, vb))
        norm = float(np.linalg.norm(va) * np.linalg.norm(vb))
        return dot / norm if norm > 0 else 0.0
    except ImportError:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        return dot / (norm_a * norm_b) if norm_a > 0 and norm_b > 0 else 0.0


def load_raptor_config(config: Optional[dict] = None) -> dict:
    """Load the ``rag.raptor`` config block from args/rag_config.yaml.

    A caller may pass the full rag_config dict or the raptor block directly.
    Returns an empty dict (→ module defaults) when nothing is configured.
    """
    cfg = config or {}
    if "raptor" in cfg:
        return cfg.get("raptor") or {}
    if "rag" in cfg:
        return (cfg.get("rag") or {}).get("raptor", {}) or {}
    config_path = BASE_DIR / "args" / "rag_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return (raw.get("rag", {}) or {}).get("raptor", {}) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# SummaryStore — persistence for the rag_chunk_summaries tier
# ---------------------------------------------------------------------------


class SummaryStore:
    """Co-located store for RAPTOR summaries (``rag_chunk_summaries``).

    Uses ``%s`` placeholders throughout so the storage layer translates for
    SQLite while PostgreSQL runs them natively (both-backend safe). Embeddings
    are packed float32 bytes (BLOB / BYTEA); cosine similarity is computed in
    Python over the small summary tier — no pgvector dependency required.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        tenant_id: str = "",
        backend: Optional[str] = None,
    ):
        self._tenant_id = tenant_id
        try:
            self._backend = backend or get_backend()
        except Exception:
            self._backend = "sqlite"
        self._is_pg = self._backend == "postgresql"
        if self._is_pg:
            self._db_path = None
        else:
            self._db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
            if tenant_id and not db_path:
                tenant_dir = BASE_DIR / "data" / "tenants"
                tenant_dir.mkdir(parents=True, exist_ok=True)
                self._db_path = tenant_dir / f"{tenant_id}_rag.db"
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _get_conn(self):
        if self._is_pg:
            return get_connection()
        conn = get_connection(db_path=str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        """Create rag_chunk_summaries if it does not exist (both backends)."""
        blob_type = "BYTEA" if self._is_pg else "BLOB"
        conn = self._get_conn()
        try:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS rag_chunk_summaries (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL DEFAULT '',
                    embedding {blob_type},
                    level INTEGER NOT NULL DEFAULT 1,
                    parent_chunk_id TEXT,
                    child_ids TEXT NOT NULL DEFAULT '[]',
                    source_type TEXT NOT NULL DEFAULT '',
                    source_id TEXT NOT NULL DEFAULT '',
                    source_table TEXT NOT NULL DEFAULT '',
                    metadata TEXT DEFAULT '{{}}',
                    tenant_id TEXT DEFAULT '',
                    project_id TEXT DEFAULT '',
                    classification TEXT NOT NULL DEFAULT 'CUI',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rag_summaries_source "
                "ON rag_chunk_summaries(source_id, level)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rag_summaries_parent "
                "ON rag_chunk_summaries(parent_chunk_id)"
            )
            conn.commit()
        finally:
            conn.close()

    # -- writes -----------------------------------------------------------

    def upsert_summary(
        self,
        summary_id: str,
        content: str,
        embedding: Optional[List[float]],
        level: int,
        parent_chunk_id: Optional[str],
        child_ids: List[str],
        source_type: str = "",
        source_id: str = "",
        source_table: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        tenant_id: str = "",
        project_id: str = "",
        classification: str = "CUI",
    ) -> None:
        blob = _embedding_to_blob(embedding) if embedding else None
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO rag_chunk_summaries
                   (id, content, content_hash, embedding, level, parent_chunk_id,
                    child_ids, source_type, source_id, source_table, metadata,
                    tenant_id, project_id, classification)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    summary_id,
                    content,
                    content_hash,
                    blob,
                    level,
                    parent_chunk_id,
                    json.dumps(child_ids),
                    source_type,
                    source_id,
                    source_table,
                    json.dumps(metadata or {}),
                    tenant_id or self._tenant_id,
                    project_id,
                    classification,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_by_source(self, source_id: str) -> int:
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "DELETE FROM rag_chunk_summaries WHERE source_id = %s",
                (source_id,),
            )
            deleted = cur.rowcount if cur.rowcount is not None else 0
            conn.commit()
            return deleted
        finally:
            conn.close()

    def count(self, source_id: str = "", level: Optional[int] = None) -> int:
        conn = self._get_conn()
        try:
            sql = "SELECT COUNT(*) FROM rag_chunk_summaries"
            conds: List[str] = []
            params: List[Any] = []
            if source_id:
                conds.append("source_id = %s")
                params.append(source_id)
            if level is not None:
                conds.append("level = %s")
                params.append(level)
            if conds:
                sql += " WHERE " + " AND ".join(conds)
            row = conn.execute(sql, params).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    # -- reads ------------------------------------------------------------

    def search(
        self,
        query_embedding: List[float],
        top_k: int = _SUMMARY_TOP_K_DEFAULT,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        """Brute-force cosine search over the summary tier.

        Returns SearchResult items tagged (via ``metadata``) with
        ``is_summary=True``, ``level`` and ``child_ids`` so the retriever can
        merge/dedup by lineage and exclude summaries from citations.
        """
        conn = self._get_conn()
        try:
            sql = (
                "SELECT id, content, level, parent_chunk_id, child_ids, "
                "source_type, source_id, source_table, embedding, metadata, "
                "classification FROM rag_chunk_summaries WHERE embedding IS NOT NULL"
            )
            params: List[Any] = []
            filters = filters or {}
            if filters.get("source_type"):
                sql += " AND source_type = %s"
                params.append(filters["source_type"])
            if filters.get("source_id"):
                sql += " AND source_id = %s"
                params.append(filters["source_id"])
            if filters.get("project_id"):
                sql += " AND (project_id = %s OR project_id = '')"
                params.append(filters["project_id"])
            tenant = filters.get("tenant_id") or self._tenant_id
            if tenant:
                sql += " AND (tenant_id = %s OR tenant_id = '')"
                params.append(tenant)
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        results: List[SearchResult] = []
        for row in rows:
            (
                sid,
                content,
                level,
                parent_id,
                child_ids_json,
                src_type,
                src_id,
                src_table,
                emb_blob,
                meta_json,
                cls,
            ) = row
            if not emb_blob:
                continue
            score = _cosine_similarity(query_embedding, _blob_to_embedding(emb_blob))
            try:
                child_ids = json.loads(child_ids_json) if child_ids_json else []
            except (json.JSONDecodeError, ValueError):
                child_ids = []
            meta: Dict[str, Any] = {}
            if meta_json:
                try:
                    meta = json.loads(meta_json)
                except (json.JSONDecodeError, ValueError):
                    meta = {}
            meta.update({"is_summary": True, "level": level, "child_ids": child_ids,
                         "parent_chunk_id": parent_id})
            results.append(
                SearchResult(
                    chunk_id=sid,
                    content=content,
                    source_type=src_type or "rag_summary",
                    source_id=src_id,
                    source_table=src_table,
                    chunk_index=0,
                    score=score,
                    final_score=score,
                    metadata=meta,
                    classification=cls or "CUI",
                )
            )
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]


# ---------------------------------------------------------------------------
# Summarizer / embedder factories (injectable + graceful no-op)
# ---------------------------------------------------------------------------


def default_summarizer(config: Optional[dict] = None) -> Callable[[str], str]:
    """Cheap-LLM summarizer via LLMRouter (mirrors evaluator._llm_judge).

    Returns "" on any failure so the builder can NO-OP gracefully when the LLM
    is unavailable (air-gap / no provider).
    """
    cfg = config or {}
    fn = cfg.get("llm_function", _LLM_FUNCTION_DEFAULT)
    max_tokens = int(cfg.get("summary_max_tokens", _SUMMARY_MAX_TOKENS))

    def summarize(text: str) -> str:
        if not text.strip():
            return ""
        try:
            from tools.llm.router import LLMRouter
            from tools.llm.provider import LLMRequest

            router = LLMRouter()
            request = LLMRequest(
                messages=[{"role": "user", "content": f"Summarize the following text into a concise, factual summary that preserves key entities and claims:\n\n{text}"}],
                system_prompt="You are a precise technical summarizer. Produce a dense, factual summary. Do not add information not present in the source.",
                max_tokens=max_tokens,
                temperature=0.0,
                classification="CUI",
            )
            response = router.invoke(fn, request)
            return (response.content or "").strip()
        except Exception:
            return ""

    return summarize


def _default_embedder() -> Callable[[str], Optional[List[float]]]:
    """Embedding via the provider abstraction; returns None when unavailable."""

    def embed(text: str) -> Optional[List[float]]:
        try:
            from tools.llm import get_embedding_provider

            provider = get_embedding_provider()
            if provider is None:
                return None
            if hasattr(provider, "embed"):
                return provider.embed(text)
            resp = provider.embeddings.create(input=text, model="nomic-embed-text")
            return resp.data[0].embedding
        except Exception:
            return None

    return embed


# ---------------------------------------------------------------------------
# RaptorBuilder — build the summary tree above rag_chunks
# ---------------------------------------------------------------------------


class RaptorBuilder:
    """Build a RAPTOR summary hierarchy above the flat ``rag_chunks`` leaves.

    Deterministic, testable structure: leaves are grouped by source document and
    partitioned into fixed-size sibling groups. Each group → a level-1 parent
    summary; the level-1 summaries → a level-2 root summary. The LLM summarizer
    and the embedder are both injectable so tests can drive the tree structure
    without a live model, and so builds NO-OP gracefully when the LLM returns "".
    """

    def __init__(
        self,
        store: Optional[SummaryStore] = None,
        leaf_db_path: str | Path | None = None,
        tenant_id: str = "",
        summarizer: Optional[Callable[[str], str]] = None,
        embedder: Optional[Callable[[str], Optional[List[float]]]] = None,
        config: Optional[dict] = None,
    ):
        self._config = load_raptor_config(config)
        self._tenant_id = tenant_id
        self._group_size = int(self._config.get("group_size", _GROUP_SIZE_DEFAULT))
        self._max_levels = int(self._config.get("max_levels", _MAX_LEVELS_DEFAULT))
        # Leaf chunks live in the SQLite vector-store DB (or PG main DB).
        self._leaf_db_path = Path(leaf_db_path) if leaf_db_path else DEFAULT_DB_PATH
        self._store = store or SummaryStore(
            db_path=self._leaf_db_path, tenant_id=tenant_id
        )
        self._is_pg = self._store._is_pg
        self._summarizer = summarizer or default_summarizer(self._config)
        self._embedder = embedder or _default_embedder()

    # -- leaf access ------------------------------------------------------

    def _leaf_conn(self):
        if self._is_pg:
            return get_connection()
        return get_connection(db_path=str(self._leaf_db_path))

    def _fetch_leaf_source_ids(self) -> List[str]:
        conn = self._leaf_conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT source_id FROM rag_chunks "
                "WHERE source_id IS NOT NULL AND source_id <> '' ORDER BY source_id"
            ).fetchall()
            return [r[0] for r in rows]
        except Exception:
            # No rag_chunks table yet (fresh/empty DB) → nothing to build.
            return []
        finally:
            conn.close()

    def _fetch_leaves(self, source_id: str) -> List[Dict[str, Any]]:
        conn = self._leaf_conn()
        try:
            rows = conn.execute(
                "SELECT id, content, source_type, source_table, chunk_index, "
                "tenant_id, project_id, classification FROM rag_chunks "
                "WHERE source_id = %s ORDER BY chunk_index",
                (source_id,),
            ).fetchall()
        except Exception:
            return []
        finally:
            conn.close()
        leaves = []
        for r in rows:
            leaves.append(
                {
                    "id": r[0],
                    "content": r[1] or "",
                    "source_type": r[2] or "",
                    "source_table": r[3] or "",
                    "chunk_index": r[4] or 0,
                    "tenant_id": r[5] or "",
                    "project_id": r[6] or "",
                    "classification": r[7] or "CUI",
                }
            )
        return leaves

    # -- build ------------------------------------------------------------

    def build_hierarchy(
        self,
        source_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Build (or plan) the summary tree.

        Args:
            source_id: Limit to one source document; else build all.
            dry_run: Plan only — write nothing.

        Returns:
            Summary dict with per-run counts.
        """
        source_ids = [source_id] if source_id else self._fetch_leaf_source_ids()
        total_l1 = 0
        total_l2 = 0
        total_skipped = 0
        docs_built = 0

        for sid in source_ids:
            leaves = self._fetch_leaves(sid)
            if not leaves:
                continue
            res = self._build_for_source(sid, leaves, dry_run)
            if res["level1"] or res["level2"]:
                docs_built += 1
            total_l1 += res["level1"]
            total_l2 += res["level2"]
            total_skipped += res["skipped"]

        return {
            "classification": "CUI // SP-CTI",
            "dry_run": dry_run,
            "sources_processed": len(source_ids),
            "documents_built": docs_built,
            "level1_created": total_l1,
            "level2_created": total_l2,
            "skipped": total_skipped,
        }

    def _build_for_source(
        self, source_id: str, leaves: List[Dict[str, Any]], dry_run: bool
    ) -> Dict[str, int]:
        # Idempotent per source: clear existing summaries before rebuild.
        if not dry_run:
            self._store.delete_by_source(source_id)

        source_type = leaves[0]["source_type"]
        source_table = leaves[0]["source_table"]
        tenant_id = leaves[0]["tenant_id"]
        project_id = leaves[0]["project_id"]
        classification = leaves[0]["classification"]

        # Reserve a root id up-front so level-1 rows can point at it.
        root_id = f"sum_{uuid.uuid4().hex[:16]}"

        level1: List[Dict[str, Any]] = []
        skipped = 0

        # Partition sibling leaves into fixed-size groups.
        for start in range(0, len(leaves), self._group_size):
            group = leaves[start : start + self._group_size]
            group_text = "\n\n".join(leaf["content"] for leaf in group)
            summary_text = self._summarizer(group_text)
            if not summary_text:
                skipped += 1
                continue  # graceful no-op when the LLM is unavailable
            embedding = self._embedder(summary_text)
            sid = f"sum_{uuid.uuid4().hex[:16]}"
            level1.append(
                {
                    "id": sid,
                    "content": summary_text,
                    "embedding": embedding,
                    "child_ids": [leaf["id"] for leaf in group],
                }
            )

        # No level-1 summaries → full no-op for this document.
        if not level1:
            return {"level1": 0, "level2": 0, "skipped": skipped}

        # Level-2 root summary over the level-1 summaries (if enabled).
        build_root = self._max_levels >= 2 and len(level1) >= 1
        root_created = 0
        root_parent = None
        if build_root:
            root_text = self._summarizer(
                "\n\n".join(item["content"] for item in level1)
            )
            if root_text:
                root_emb = self._embedder(root_text)
                if not dry_run:
                    self._store.upsert_summary(
                        summary_id=root_id,
                        content=root_text,
                        embedding=root_emb,
                        level=2,
                        parent_chunk_id=None,
                        child_ids=[item["id"] for item in level1],
                        source_type=source_type,
                        source_id=source_id,
                        source_table=source_table,
                        metadata={"raptor_level": 2, "provenance": "llm_summary"},
                        tenant_id=tenant_id,
                        project_id=project_id,
                        classification=classification,
                    )
                root_created = 1
                root_parent = root_id
            else:
                skipped += 1

        # Persist level-1 rows (parent points at the root when one was built).
        for item in level1:
            if not dry_run:
                self._store.upsert_summary(
                    summary_id=item["id"],
                    content=item["content"],
                    embedding=item["embedding"],
                    level=1,
                    parent_chunk_id=root_parent,
                    child_ids=item["child_ids"],
                    source_type=source_type,
                    source_id=source_id,
                    source_table=source_table,
                    metadata={"raptor_level": 1, "provenance": "llm_summary"},
                    tenant_id=tenant_id,
                    project_id=project_id,
                    classification=classification,
                )

        return {"level1": len(level1), "level2": root_created, "skipped": skipped}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="RAPTOR summary-tree builder (RCE)")
    parser.add_argument("--build", action="store_true", help="Build the summary hierarchy")
    parser.add_argument("--source", default="", help="Limit to a single source_id")
    parser.add_argument("--dry-run", action="store_true", help="Plan only; write nothing")
    parser.add_argument("--tenant-id", default="", help="Tenant ID")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if not args.build:
        parser.print_help()
        return

    builder = RaptorBuilder(tenant_id=args.tenant_id)
    result = builder.build_hierarchy(
        source_id=args.source or None, dry_run=args.dry_run
    )
    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"[RAPTOR] sources={result['sources_processed']} "
            f"docs_built={result['documents_built']} "
            f"L1={result['level1_created']} L2={result['level2_created']} "
            f"skipped={result['skipped']} dry_run={result['dry_run']}"
        )


if __name__ == "__main__":
    main()
