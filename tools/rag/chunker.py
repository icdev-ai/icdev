# [TEMPLATE: CUI // SP-CTI]
"""Adaptive content chunker for RAG ingestion (D-RAG-4).

Strategy:
  - Short content (<500 tokens): store whole as single chunk
  - Long content (>2000 tokens): sliding window with 10% overlap at sentence boundaries
  - Medium content: store whole (below chunk threshold)

Token estimation: ~4 chars per token (conservative for English text).
Deterministic — no LLM needed, air-gap safe.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import List, Optional

from tools.rag.vector_store_provider import VectorChunk

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Approximate chars per token (conservative for English)
CHARS_PER_TOKEN = 4


def _load_chunk_config() -> dict:
    """Load chunking config from args/rag_config.yaml."""
    config_path = BASE_DIR / "args" / "rag_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("rag", {}).get("chunking", {})
    except Exception:
        return {}


def _estimate_tokens(text: str) -> int:
    """Estimate token count from character length."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def _find_sentence_boundary(text: str, target_pos: int) -> int:
    """Find the nearest sentence boundary to target_pos.

    Looks for sentence-ending punctuation (.!?) followed by whitespace.
    Searches within +/- 200 chars of target position.
    """
    search_start = max(0, target_pos - 200)
    search_end = min(len(text), target_pos + 200)
    window = text[search_start:search_end]

    # Find all sentence boundaries in window
    boundaries = []
    for m in re.finditer(r"[.!?]\s+", window):
        abs_pos = search_start + m.end()
        boundaries.append(abs_pos)

    if not boundaries:
        # Fall back to nearest whitespace
        for i in range(target_pos, min(target_pos + 100, len(text))):
            if text[i : i + 1].isspace():
                return i + 1
        return target_pos

    # Return boundary closest to target
    return min(boundaries, key=lambda b: abs(b - target_pos))


def chunk_content(
    content: str,
    source_type: str = "",
    source_id: str = "",
    source_table: str = "",
    metadata: Optional[dict] = None,
    tenant_id: str = "",
    project_id: str = "",
    classification: str = "CUI",
    chunk_config: Optional[dict] = None,
) -> List[VectorChunk]:
    """Chunk content adaptively based on length.

    Args:
        content: Raw text to chunk.
        source_type: Source type identifier.
        source_id: Row ID in source table.
        source_table: DB table name.
        metadata: Optional metadata dict.
        tenant_id: Tenant ID for multi-tenant.
        project_id: Project ID.
        classification: CUI marking.
        chunk_config: Override chunking config.

    Returns:
        List of VectorChunk instances (without embeddings — caller must embed).
    """
    if not content or not content.strip():
        return []

    cfg = chunk_config or _load_chunk_config()
    cfg.get("short_threshold_tokens", 500)
    chunk_size = cfg.get("chunk_size_tokens", 2000)
    overlap_pct = cfg.get("overlap_pct", 0.10)

    content = content.strip()
    est_tokens = _estimate_tokens(content)

    # Short content: store as single chunk
    if est_tokens <= chunk_size:
        chunk = VectorChunk(
            chunk_id=f"chunk-{uuid.uuid4().hex[:12]}",
            content=content,
            source_type=source_type,
            source_id=str(source_id),
            source_table=source_table,
            chunk_index=0,
            total_chunks=1,
            metadata=metadata or {},
            tenant_id=tenant_id,
            project_id=project_id,
            classification=classification,
        )
        chunk.compute_content_hash()
        return [chunk]

    # Long content: sliding window with overlap
    chunk_chars = chunk_size * CHARS_PER_TOKEN
    overlap_chars = int(chunk_chars * overlap_pct)
    chunk_chars - overlap_chars

    chunks: list[VectorChunk] = []
    pos = 0
    idx = 0

    while pos < len(content):
        end_pos = pos + chunk_chars
        if end_pos >= len(content):
            # Last chunk: take everything remaining
            chunk_text = content[pos:].strip()
        else:
            # Find sentence boundary near end_pos
            boundary = _find_sentence_boundary(content, end_pos)
            chunk_text = content[pos:boundary].strip()
            # Advance by stride from actual boundary
            end_pos = boundary

        if chunk_text:
            chunk = VectorChunk(
                chunk_id=f"chunk-{uuid.uuid4().hex[:12]}",
                content=chunk_text,
                source_type=source_type,
                source_id=str(source_id),
                source_table=source_table,
                chunk_index=idx,
                total_chunks=0,  # Set after loop
                metadata=metadata or {},
                tenant_id=tenant_id,
                project_id=project_id,
                classification=classification,
            )
            chunk.compute_content_hash()
            chunks.append(chunk)
            idx += 1

        # Advance position
        if end_pos >= len(content):
            break
        pos = end_pos - overlap_chars
        if pos <= chunks[-1].chunk_index if chunks else 0:
            pos = end_pos  # Prevent infinite loop

    # Set total_chunks on all
    for c in chunks:
        c.total_chunks = len(chunks)

    return chunks


def chunk_fields(
    fields: dict,
    field_names: List[str],
    source_type: str = "",
    source_id: str = "",
    source_table: str = "",
    metadata: Optional[dict] = None,
    tenant_id: str = "",
    project_id: str = "",
    classification: str = "CUI",
) -> List[VectorChunk]:
    """Chunk multiple fields from a DB row, concatenated with field labels.

    Args:
        fields: Dict of field_name → value from DB row.
        field_names: Which fields to include (in order).
        source_type: Source type identifier.
        source_id: Row ID.
        source_table: DB table name.
        metadata: Optional metadata.
        tenant_id: Tenant ID.
        project_id: Project ID.
        classification: CUI marking.

    Returns:
        List of VectorChunk instances.
    """
    parts = []
    for name in field_names:
        val = fields.get(name, "")
        if val and str(val).strip():
            parts.append(f"{name}: {str(val).strip()}")

    combined = "\n".join(parts)
    return chunk_content(
        content=combined,
        source_type=source_type,
        source_id=source_id,
        source_table=source_table,
        metadata=metadata,
        tenant_id=tenant_id,
        project_id=project_id,
        classification=classification,
    )
