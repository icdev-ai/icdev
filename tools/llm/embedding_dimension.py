# [TEMPLATE: CUI // SP-CTI]
"""Embedding DIMENSION safety -- one check every vector writer shares (omx-vllm-03).

Switching embedder silently corrupts a vector store when the dimensions differ:
a 1024-d vLLM vector written beside 768-d nomic vectors raises nothing on a BLOB
column, and every later cosine against the mixed rows is garbage (the SQLite
store's cosine returns 0.0 on a length mismatch, so the search simply goes
quiet). The embedding chain makes that switch easy -- a provider failing its
probe falls through to the next entry -- so the store, not the embedder, has to
be the one that refuses.

A store's RECORDED dimension is the length of a vector it already holds. An empty
store records nothing and adopts the first vector written to it.
"""

from __future__ import annotations

import struct
from typing import Iterable, List, Optional, Sequence

_RAG_BLOB_MAGIC = b"RVQ1"


class EmbeddingDimensionMismatch(ValueError):
    """A vector's dimension differs from the dimension its store already records."""

    def __init__(self, store: str, recorded: int, incoming: int):
        self.store = store
        self.recorded = recorded
        self.incoming = incoming
        super().__init__(
            f"refusing to write a {incoming}-dimension embedding into {store}, which "
            f"records {recorded}-dimension vectors. The embedding chain "
            "(args/llm_config.yaml embeddings.default_chain) selected a different "
            "embedder than the one that built this store. Pin the original embedder, "
            "or re-embed the store from scratch -- never mix the two."
        )


def blob_dimension(blob) -> Optional[int]:
    """Number of floats in a stored embedding BLOB, or None when it holds none.

    Reads both encodings in use: the RAG store's self-describing ``RVQ1`` header
    (float16/float32, rce-quant-01) and raw headerless float32 (memory, KG, the
    PG BYTEA column, every legacy RAG row).
    """
    if blob is None:
        return None
    raw = bytes(blob)
    if len(raw) >= 5 and raw[:4] == _RAG_BLOB_MAGIC:
        char = chr(raw[4])
        if char in ("e", "f"):
            return (len(raw) - 5) // struct.calcsize(char) or None
    return len(raw) // 4 or None


def recorded_dimension(conn, table: str, where: str = "", params: Sequence = ()) -> Optional[int]:
    """Dimension of one vector already in ``table.embedding`` (filtered by ``where``).

    ``table`` and ``where`` are code-supplied identifiers, never user input.
    Returns None when the store holds no vector yet -- or cannot be read, since a
    store that cannot be read holds nothing this write could be mixed with.
    """
    sql = f"SELECT embedding FROM {table} WHERE embedding IS NOT NULL"  # nosec B608 -- identifiers are code constants
    if where:
        sql += f" AND {where}"
    sql += " LIMIT 1"
    try:
        row = conn.execute(sql, tuple(params)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        value = row["embedding"]
    except (KeyError, IndexError, TypeError):
        value = row[0]
    return blob_dimension(value)


class DimensionGuard:
    """Holds one store's dimension for the length of a write.

    ``recorded`` is what the store already holds (None for an empty store); the
    first vector checked against an empty store fixes the dimension for the rest
    of the write, so one batch cannot seed a fresh store with mixed vectors either.
    """

    def __init__(self, store: str, recorded: Optional[int]):
        self.store = store
        self.recorded = recorded

    def check(self, vector: Sequence[float]) -> None:
        """Raise :class:`EmbeddingDimensionMismatch` BEFORE ``vector`` is written."""
        incoming = len(vector)
        if self.recorded is None:
            self.recorded = incoming
            return
        if incoming != self.recorded:
            raise EmbeddingDimensionMismatch(self.store, self.recorded, incoming)

    def check_all(self, vectors: Iterable[Sequence[float]]) -> List[Sequence[float]]:
        """Check every vector, then return them -- nothing is written on a mismatch."""
        out = list(vectors)
        for vec in out:
            self.check(vec)
        return out
