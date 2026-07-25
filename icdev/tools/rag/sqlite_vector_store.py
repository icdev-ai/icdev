# [TEMPLATE: CUI // SP-CTI]
"""SQLite BLOB vector store — default backend (D-RAG-1).

Stores embeddings as packed BLOBs in SQLite using a self-describing,
versioned header (rce-quant-01): magic + dtype byte + payload, so float16
(default, ~50% smaller) and float32 coexist and legacy headerless float32
rows keep reading. Computes cosine similarity in Python with numpy fast path
+ pure-python fallback. Reuses struct.pack/unpack from tools/memory/embed_memory.py.

Always available — no optional dependencies. Air-gap safe.
"""

from __future__ import annotations

import json
import math
import sqlite3
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.db.storage import get_connection
from tools.rag.vector_store_provider import (
    SearchResult,
    VectorChunk,
    VectorStoreProvider,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "rag" / "rag_vectors.db"


# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from args/rag_config.yaml
# under sqlite_vector_store.anomaly_detection.  Change config, not code.
# ---------------------------------------------------------------------------
_DEFAULT_TOP_K = 50      # default number of nearest-neighbour results returned
_BUSY_TIMEOUT_MS = 5000  # SQLite busy_timeout (ms) for WAL contention

# Anomaly-detection relevance floor — a vector search whose best (top-1) cosine
# similarity falls below this is flagged as a low-confidence retrieval (nothing
# in the corpus is strongly similar to the query).  When enough history exists
# the floor is recomputed adaptively as (mean − k·stddev) of past vector-search
# top scores; otherwise this module-level floor is used.
_VECTOR_SCORE_FLOOR = 0.30  # top similarity score below this is flagged
_ANOMALY_STDDEV_K = 2.0     # flag searches below mean − k·stddev of history


def _load_vector_anomaly_config(config: "dict | None" = None) -> dict:
    """Load sqlite_vector_store.anomaly_detection settings from rag_config.yaml.

    A caller-supplied ``config`` may carry the block directly under an
    ``anomaly_detection`` key; otherwise fall back to the top-level
    ``sqlite_vector_store.anomaly_detection`` block in args/rag_config.yaml.
    Returns an empty dict when nothing is configured.
    """
    cfg = config or {}
    if "anomaly_detection" in cfg:
        return cfg["anomaly_detection"] or {}
    config_path = BASE_DIR / "args" / "rag_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return raw.get("sqlite_vector_store", {}).get("anomaly_detection", {})
    except Exception:
        return {}


def _compute_vector_anomaly_thresholds(anomaly_cfg: "dict | None" = None) -> dict:
    """Compute an adaptive relevance floor from historical vector searches.

    Reads ``top_score`` from ``rag_retrieval_log`` for runs whose
    ``retrieval_mode = 'vector'`` and derives a statistical lower bound
    (mean − k·stddev), so the definition of a "low-confidence" search tracks the
    corpus instead of a frozen magic number.

    Falls back to the module-level floor when fewer than ``min_samples`` rows
    exist or anomaly detection is disabled.
    """
    cfg = anomaly_cfg or {}
    defaults = {
        "score_floor": cfg.get("fallback_score_floor", _VECTOR_SCORE_FLOOR),
        "computed": False,
    }
    if not cfg.get("enabled", True):
        return defaults

    min_samples = cfg.get("min_samples", 30)
    stddev_k = float(cfg.get("stddev_k", _ANOMALY_STDDEV_K))
    bounds = cfg.get("adaptive_bounds", {})
    floor_min = float(bounds.get("floor_min", 0.05))
    floor_max = float(bounds.get("floor_max", 0.80))

    conn = None
    try:
        conn = get_connection()
        # Population mean / stddev via E[x²] − E[x]² (SQLite has no STDDEV()).
        row = conn.execute(
            "SELECT "
            "AVG(top_score) AS mean, AVG(top_score * top_score) AS sq, "
            "COUNT(*) AS n "
            "FROM rag_retrieval_log "
            "WHERE retrieval_mode = 'vector' AND top_score IS NOT NULL"
        ).fetchone()
        if row:
            n = row["n"] if isinstance(row, dict) else row[2]
            if n and n >= min_samples:
                mean = float((row["mean"] if isinstance(row, dict) else row[0]) or 0.0)
                sq = float((row["sq"] if isinstance(row, dict) else row[1]) or 0.0)
                std = math.sqrt(max(0.0, sq - mean * mean))
                score_floor = max(floor_min, min(floor_max, mean - stddev_k * std))
                return {
                    "score_floor": round(score_floor, 3),
                    "computed": True,
                    "n_samples": n,
                    "mean": round(mean, 3),
                }
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return defaults


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


# ---------------------------------------------------------------------------
# Quantized blob format (rce-quant-01)
# ---------------------------------------------------------------------------
# Self-describing header: magic b'RVQ1' + 1 dtype byte ('f'=float32,
# 'e'=float16) + packed payload.  Blobs WITHOUT the magic are treated as
# LEGACY raw float32 (len//4 values) so every previously stored embedding keeps
# reading correctly — no forced re-index.  Half-precision ('e') is native to
# struct since Python 3.6, so float16 packing needs no numpy (air-gap safe).
_BLOB_MAGIC = b"RVQ1"
_DTYPE_TO_CHAR = {"float32": "f", "float16": "e"}
_CHAR_TO_DTYPE = {"f": "float32", "e": "float16"}
_DEFAULT_SQLITE_DTYPE = "float16"  # ~50% storage/IO win; ~2e-3 element error


def _load_quantization_config(config: "dict | None" = None) -> dict:
    """Load the ``rag.quantization`` block from config or args/rag_config.yaml.

    A caller-supplied ``config`` may carry the block directly under a
    ``quantization`` key or nested under ``rag.quantization``; otherwise fall
    back to ``rag.quantization`` in args/rag_config.yaml.  Empty dict when
    nothing is configured.
    """
    cfg = config or {}
    if isinstance(cfg, dict):
        if "quantization" in cfg:
            return cfg["quantization"] or {}
        rag = cfg.get("rag")
        if isinstance(rag, dict) and "quantization" in rag:
            return rag["quantization"] or {}
    config_path = BASE_DIR / "args" / "rag_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return raw.get("rag", {}).get("quantization", {}) or {}
    except Exception:
        return {}


def _resolve_sqlite_dtype(config: "dict | None" = None) -> str:
    """Resolve the configured write dtype ('float16' | 'float32')."""
    q = _load_quantization_config(config)
    dtype = str(q.get("sqlite_dtype", _DEFAULT_SQLITE_DTYPE)).lower()
    return dtype if dtype in _DTYPE_TO_CHAR else _DEFAULT_SQLITE_DTYPE


def _resolve_binary_prefilter(config: "dict | None" = None) -> dict:
    """Resolve the ``rag.quantization.binary_prefilter`` settings (rce-quant-02).

    Returns ``{enabled, candidate_multiplier, min_corpus_size}`` with safe
    defaults.  Default is DISABLED, so search behaviour is unchanged unless
    explicitly turned on.
    """
    q = _load_quantization_config(config)
    bp = q.get("binary_prefilter", {}) or {}
    try:
        mult = int(bp.get("candidate_multiplier", _DEFAULT_BINARY_CANDIDATE_MULTIPLIER))
    except (TypeError, ValueError):
        mult = _DEFAULT_BINARY_CANDIDATE_MULTIPLIER
    try:
        min_corpus = int(bp.get("min_corpus_size", _DEFAULT_BINARY_MIN_CORPUS))
    except (TypeError, ValueError):
        min_corpus = _DEFAULT_BINARY_MIN_CORPUS
    return {
        "enabled": bool(bp.get("enabled", False)),
        "candidate_multiplier": max(1, mult),
        "min_corpus_size": max(1, min_corpus),
    }


def _embedding_to_blob(embedding: List[float], dtype: str = "float16") -> bytes:
    """Pack a float list to a self-describing BLOB (rce-quant-01).

    Writes ``magic + dtype-byte + payload``.  ``dtype`` selects storage
    precision: ``float16`` (default) halves storage/IO at ~2e-3 per-element
    error; ``float32`` is exact.  Reads via :func:`_blob_to_embedding` are
    back-compat for both dtypes and for legacy headerless float32 blobs.
    """
    char = _DTYPE_TO_CHAR.get(str(dtype).lower(), _DTYPE_TO_CHAR[_DEFAULT_SQLITE_DTYPE])
    payload = struct.pack(f"{len(embedding)}{char}", *embedding)
    return _BLOB_MAGIC + char.encode("ascii") + payload


def _blob_to_embedding(blob: bytes) -> List[float]:
    """Unpack a BLOB to a float list (rce-quant-01).

    If ``blob`` carries the ``RVQ1`` magic header, its dtype byte selects the
    unpack format (float16/float32).  Otherwise the whole blob is read as
    LEGACY raw float32 (``len // 4`` values), preserving back-compat with every
    row written before this format existed.

    Magic-collision risk is negligible: a legacy float32 embedding would only
    be misread if its first four little-endian bytes spelled ``b'RVQ1'``, i.e.
    element[0] equalled a value on the order of 1e-9 — normalized embeddings
    never produce it.
    """
    if len(blob) >= 5 and blob[:4] == _BLOB_MAGIC:
        char = chr(blob[4])
        if char in _CHAR_TO_DTYPE:
            payload = blob[5:]
            itemsize = struct.calcsize(char)
            n = len(payload) // itemsize
            return list(struct.unpack(f"{n}{char}", payload))
    # Legacy headerless float32
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


# ---------------------------------------------------------------------------
# Binary quantization + Hamming pre-filter (rce-quant-02)
# ---------------------------------------------------------------------------
# Optional second tier for large air-gap corpora: store 1 sign bit per
# dimension (packed MSB-first into bytes) alongside the float embedding.  At
# search time — when enabled and the corpus is large enough — a cheap Hamming
# distance on the packed sign bits pre-selects top-(k * multiplier) candidates,
# which are then re-ranked with full-precision cosine.  This shrinks the
# expensive float-cosine set while keeping recall.  Pure Python, zero deps.
_DEFAULT_BINARY_CANDIDATE_MULTIPLIER = 4
_DEFAULT_BINARY_MIN_CORPUS = 512  # below this, brute-force cosine wins


def _embedding_to_sign_bits(embedding: List[float]) -> bytes:
    """Pack per-dimension sign bits (>=0 -> 1) MSB-first into bytes."""
    n = len(embedding)
    out = bytearray((n + 7) // 8)
    for i, v in enumerate(embedding):
        if v >= 0.0:
            out[i >> 3] |= 0x80 >> (i & 7)
    return bytes(out)


def _hamming_distance(a: bytes, b: bytes) -> int:
    """Hamming distance between two equal-length packed sign-bit vectors."""
    x = int.from_bytes(a, "big") ^ int.from_bytes(b, "big")
    try:
        return x.bit_count()  # Python 3.10+
    except AttributeError:  # pragma: no cover - portability fallback
        return bin(x).count("1")


class SQLiteVectorStore(VectorStoreProvider):
    """SQLite BLOB vector store implementation.

    Schema uses the rag_chunks table in a dedicated SQLite DB.
    Thread-safe via per-call connection (SQLite handles locking).
    """

    # Adaptive anomaly-detection state (lazily configured; see
    # configure_anomaly_detection / flag_anomaly).
    _anomaly_cfg: Optional[dict] = None
    _anomaly_thresholds: Optional[dict] = None

    def __init__(
        self,
        db_path: str | Path | None = None,
        tenant_id: str = "",
        config: Optional[dict] = None,
    ):
        cfg = config or {}
        self._db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._tenant_id = tenant_id
        self._busy_timeout = int(cfg.get("busy_timeout_ms", _BUSY_TIMEOUT_MS))
        # Write-time storage precision (rce-quant-01); reads stay back-compat.
        self._sqlite_dtype = _resolve_sqlite_dtype(cfg)
        # Binary-quantization Hamming pre-filter (rce-quant-02); default OFF.
        self._binary_prefilter = _resolve_binary_prefilter(cfg)
        if tenant_id:
            tenant_dir = BASE_DIR / "data" / "tenants"
            tenant_dir.mkdir(parents=True, exist_ok=True)
            self._db_path = tenant_dir / f"{tenant_id}_rag.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = get_connection(db_path=str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={self._busy_timeout}")
        return conn

    def configure_anomaly_detection(self, config: Optional[dict] = None) -> None:
        """Load anomaly config and compute the adaptive relevance floor."""
        self._anomaly_cfg = _load_vector_anomaly_config(config)
        self._anomaly_thresholds = _compute_vector_anomaly_thresholds(self._anomaly_cfg)

    def flag_anomaly(self, results: List[SearchResult]) -> dict:
        """Flag a vector search as a low-confidence (anomalous) retrieval.

        ``results`` is the output of :meth:`search` — ``SearchResult`` items
        sorted by similarity descending.  The search is flagged when the best
        score falls below the (adaptive) relevance floor, i.e. nothing in the
        corpus is strongly similar to the query.

        Returns ``{"anomalous": bool, "reasons": [...], "score_floor": float}``;
        clean (and reason-free) for an empty result set.
        """
        if self._anomaly_thresholds is None:
            self.configure_anomaly_detection(None)
        floor = self._anomaly_thresholds["score_floor"]
        reasons: List[str] = []
        if results:
            top_score = results[0].score
            if top_score < floor:
                reasons.append(f"top similarity {round(top_score, 3)} < floor {floor}")
        return {"anomalous": bool(reasons), "reasons": reasons, "score_floor": floor}

    def _init_schema(self):
        """Create rag_chunks table if not exists."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rag_chunks (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                embedding BLOB,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL DEFAULT '',
                source_table TEXT NOT NULL DEFAULT '',
                chunk_index INTEGER NOT NULL DEFAULT 0,
                total_chunks INTEGER NOT NULL DEFAULT 1,
                metadata TEXT DEFAULT '{}',
                tier TEXT NOT NULL DEFAULT 'hot'
                    CHECK(tier IN ('hot', 'warm', 'cold')),
                tenant_id TEXT DEFAULT '',
                project_id TEXT DEFAULT '',
                classification TEXT NOT NULL DEFAULT 'CUI',
                sign_bits BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Back-compat: add sign_bits column to pre-existing tables (rce-quant-02).
        # Nullable; legacy rows keep NULL and fall back to on-the-fly sign bits.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(rag_chunks)").fetchall()}
        if "sign_bits" not in cols:
            conn.execute("ALTER TABLE rag_chunks ADD COLUMN sign_bits BLOB")
        # Indexes for common queries
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rag_chunks_content_hash
            ON rag_chunks(content_hash)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rag_chunks_source
            ON rag_chunks(source_type, source_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rag_chunks_tier
            ON rag_chunks(tier)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rag_chunks_tenant
            ON rag_chunks(tenant_id)
        """)
        conn.commit()
        conn.close()

    @property
    def provider_name(self) -> str:
        return "sqlite"

    def upsert(self, chunks: List[VectorChunk]) -> int:
        """Insert chunks, skipping duplicates by content_hash (D-RAG-5)."""
        if not chunks:
            return 0
        conn = self._get_conn()
        inserted = 0
        for chunk in chunks:
            if not chunk.content_hash:
                chunk.compute_content_hash()
            # Check for existing content_hash (dedup)
            row = conn.execute(
                "SELECT id FROM rag_chunks WHERE content_hash = %s",
                (chunk.content_hash,),
            ).fetchone()
            if row:
                continue  # Skip duplicate
            if chunk.embedding is None:
                continue  # No embedding, skip
            blob = _embedding_to_blob(chunk.embedding, dtype=self._sqlite_dtype)
            sign_bits = _embedding_to_sign_bits(chunk.embedding)  # rce-quant-02
            meta_json = json.dumps(chunk.metadata) if chunk.metadata else "{}"
            conn.execute(
                """INSERT INTO rag_chunks
                   (id, content, content_hash, embedding, source_type, source_id,
                    source_table, chunk_index, total_chunks, metadata, tier,
                    tenant_id, project_id, classification, sign_bits)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    chunk.chunk_id,
                    chunk.content,
                    chunk.content_hash,
                    blob,
                    chunk.source_type,
                    chunk.source_id,
                    chunk.source_table,
                    chunk.chunk_index,
                    chunk.total_chunks,
                    meta_json,
                    chunk.tier,
                    chunk.tenant_id or self._tenant_id,
                    chunk.project_id,
                    chunk.classification,
                    sign_bits,
                ),
            )
            inserted += 1
        conn.commit()
        conn.close()
        return inserted

    def search(
        self,
        query_embedding: List[float],
        top_k: int = _DEFAULT_TOP_K,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        """Cosine similarity search.

        Brute-force by default. When ``rag.quantization.binary_prefilter`` is
        enabled and the (post-filter) corpus is large enough, a cheap Hamming
        distance on packed sign bits pre-selects top-(k * multiplier)
        candidates which are then re-ranked with full-precision cosine
        (rce-quant-02). Legacy rows lacking stored sign bits derive them
        on the fly, so the path degrades gracefully.
        """
        conn = self._get_conn()
        sql = """SELECT id, content, source_type, source_id, source_table,
                        chunk_index, embedding, metadata, tier, classification,
                        sign_bits
                 FROM rag_chunks WHERE embedding IS NOT NULL"""
        params: list = []
        if filters:
            if "source_type" in filters:
                sql += " AND source_type = ?"
                params.append(filters["source_type"])
            if "tier" in filters:
                sql += " AND tier = ?"
                params.append(filters["tier"])
            if "project_id" in filters:
                sql += " AND (project_id = ? OR project_id = '')"
                params.append(filters["project_id"])
            if "tenant_id" in filters:
                sql += " AND (tenant_id = ? OR tenant_id = '')"
                params.append(filters["tenant_id"])
        elif self._tenant_id:
            sql += " AND (tenant_id = ? OR tenant_id = '')"
            params.append(self._tenant_id)

        rows = conn.execute(sql, params).fetchall()
        conn.close()

        candidate_rows = self._binary_prefilter_rows(query_embedding, rows, top_k)

        results: list[SearchResult] = []
        for row in candidate_rows:
            (
                cid,
                content,
                src_type,
                src_id,
                src_table,
                cidx,
                emb_blob,
                meta_str,
                tier,
                cls,
                _sign,
            ) = row
            stored_emb = _blob_to_embedding(emb_blob)
            score = _cosine_similarity(query_embedding, stored_emb)
            meta = {}
            if meta_str:
                try:
                    meta = json.loads(meta_str)
                except (json.JSONDecodeError, ValueError):
                    pass
            results.append(
                SearchResult(
                    chunk_id=cid,
                    content=content,
                    source_type=src_type,
                    source_id=src_id,
                    source_table=src_table,
                    chunk_index=cidx,
                    score=score,
                    final_score=score,
                    metadata=meta,
                    tier=tier,
                    classification=cls,
                )
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def _binary_prefilter_rows(
        self, query_embedding: List[float], rows: list, top_k: int
    ) -> list:
        """Return the row subset to re-rank with cosine (rce-quant-02).

        When the binary pre-filter is disabled, or the corpus is smaller than
        the configured threshold, returns ``rows`` unchanged (full cosine
        scan). Otherwise ranks rows by Hamming distance on packed sign bits
        (column index 10; derived on the fly for legacy NULL rows) and keeps
        the ``top_k * candidate_multiplier`` nearest. Ties and unusable rows
        are retained so recall is never silently reduced below the candidate
        budget.
        """
        cfg = self._binary_prefilter
        if not cfg.get("enabled"):
            return rows
        min_corpus = cfg.get("min_corpus_size", _DEFAULT_BINARY_MIN_CORPUS)
        if len(rows) < min_corpus:
            return rows
        n_candidates = max(1, top_k) * cfg.get("candidate_multiplier", 1)
        if n_candidates >= len(rows):
            return rows

        query_sign = _embedding_to_sign_bits(query_embedding)
        scored: list = []
        for row in rows:
            emb_blob = row[6]
            row_sign = row[10]
            if not row_sign:  # legacy row — derive on the fly
                row_sign = _embedding_to_sign_bits(_blob_to_embedding(emb_blob))
            if len(row_sign) == len(query_sign):
                dist = _hamming_distance(query_sign, row_sign)
            else:
                dist = -1  # dim mismatch — force-keep as candidate
            scored.append((dist, row))
        scored.sort(key=lambda t: t[0])
        return [row for _dist, row in scored[:n_candidates]]

    def delete(self, chunk_ids: List[str]) -> int:
        if not chunk_ids:
            return 0
        conn = self._get_conn()
        placeholders = ",".join("?" for _ in chunk_ids)
        cur = conn.execute(
            f"DELETE FROM rag_chunks WHERE id IN ({placeholders})",
            chunk_ids,  # nosec B608 -- table/column names are internal constants, not user input
        )
        deleted = cur.rowcount
        conn.commit()
        conn.close()
        return deleted

    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        conn = self._get_conn()
        sql = "SELECT COUNT(*) FROM rag_chunks"
        params: list = []
        conditions = []
        if filters:
            if "source_type" in filters:
                conditions.append("source_type = ?")
                params.append(filters["source_type"])
            if "tier" in filters:
                conditions.append("tier = ?")
                params.append(filters["tier"])
            if "tenant_id" in filters:
                conditions.append("(tenant_id = ? OR tenant_id = '')")
                params.append(filters["tenant_id"])
        elif self._tenant_id:
            conditions.append("(tenant_id = ? OR tenant_id = '')")
            params.append(self._tenant_id)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        result = conn.execute(sql, params).fetchone()[0]
        conn.close()
        return result

    def check_availability(self) -> bool:
        try:
            conn = self._get_conn()
            conn.execute("SELECT 1 FROM rag_chunks LIMIT 1")
            conn.close()
            return True
        except Exception:
            return False

    def get_by_content_hash(self, content_hash: str) -> Optional[VectorChunk]:
        conn = self._get_conn()
        row = conn.execute(
            """SELECT id, content, content_hash, source_type, source_id,
                      source_table, chunk_index, total_chunks, metadata,
                      tier, tenant_id, project_id, classification
               FROM rag_chunks WHERE content_hash = %s""",
            (content_hash,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        meta = {}
        if row[8]:
            try:
                meta = json.loads(row[8])
            except (json.JSONDecodeError, ValueError):
                pass
        return VectorChunk(
            chunk_id=row[0],
            content=row[1],
            content_hash=row[2],
            source_type=row[3],
            source_id=row[4],
            source_table=row[5],
            chunk_index=row[6],
            total_chunks=row[7],
            metadata=meta,
            tier=row[9],
            tenant_id=row[10],
            project_id=row[11],
            classification=row[12],
        )

    def migrate_tier(self, chunk_ids: List[str], target_tier: str) -> int:
        """Migrate chunks to target tier (D-RAG-6).

        For warm tier: compress embedding to float16.
        For cold tier: remove embedding (metadata only).
        """
        if not chunk_ids or target_tier not in ("hot", "warm", "cold"):
            return 0
        conn = self._get_conn()
        migrated = 0
        for cid in chunk_ids:
            if target_tier == "warm":
                # Compress to float16 via the self-describing header format so
                # the blob round-trips correctly on read (rce-quant-01). The
                # former raw np.float16.tobytes() wrote a headerless payload
                # that _blob_to_embedding then MIS-read as float32.
                row = conn.execute("SELECT embedding FROM rag_chunks WHERE id = %s", (cid,)).fetchone()
                if row and row[0]:
                    emb = _blob_to_embedding(row[0])
                    compressed = _embedding_to_blob(emb, dtype="float16")
                    conn.execute(
                        """UPDATE rag_chunks
                           SET tier = %s, embedding = %s, updated_at = CURRENT_TIMESTAMP
                           WHERE id = %s""",
                        (target_tier, compressed, cid),
                    )
                    migrated += 1
            elif target_tier == "cold":
                # Remove embedding, keep metadata
                conn.execute(
                    """UPDATE rag_chunks
                       SET tier = %s, embedding = NULL, updated_at = CURRENT_TIMESTAMP
                       WHERE id = %s""",
                    (target_tier, cid),
                )
                migrated += 1
            else:
                conn.execute(
                    """UPDATE rag_chunks
                       SET tier = %s, updated_at = CURRENT_TIMESTAMP
                       WHERE id = %s""",
                    (target_tier, cid),
                )
                migrated += 1
        conn.commit()
        conn.close()
        return migrated
