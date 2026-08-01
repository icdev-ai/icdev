# [TEMPLATE: CUI // SP-CTI]
"""Adaptive content chunker for RAG ingestion (D-RAG-4).

Default strategy (template ``general``, unchanged):
  - Short content (<500 tokens): store whole as single chunk
  - Long content (>2000 tokens): sliding window with 10% overlap at sentence boundaries
  - Medium content: store whole (below chunk threshold)

Document-type templates (oss-chunk-01): pass ``template="oscal_catalog"`` (or
any name in ``args/chunking_templates.yaml``) to chunk on structural boundaries
instead — one chunk per control, rule, clause, step or slide. Dispatch is always
explicit: a caller names the template, or a ``tools/rag/source_registry.py``
entry names it via its ``chunking`` key. Content is never inspected to pick a
template silently; :func:`tools.rag.chunking_templates.suggest_template` exists
for operators and is not called from this module.

Every chunk records the template that produced it in
``metadata['chunking_template']`` so the decision is auditable.

Token estimation: ~4 chars per token (conservative for English text).
Deterministic — no LLM needed, air-gap safe.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.rag.chunking_templates import (
    STRATEGY_SLIDING,
    get_template,
    split_content,
)
from tools.rag.vector_store_provider import VectorChunk

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Approximate chars per token (conservative for English)
CHARS_PER_TOKEN = 4

# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from args/rag_config.yaml
# under rag.chunking.  Change config, not code.
# ---------------------------------------------------------------------------
_DEFAULT_SHORT_THRESHOLD  = 500    # tokens — content below this stored as single chunk
_DEFAULT_CHUNK_SIZE       = 2000   # tokens — target chunk size for sliding window
_DEFAULT_OVERLAP_PCT      = 0.10   # fraction of chunk_size used as overlap
_BOUNDARY_SEARCH_RADIUS   = 200    # chars to search around target position for sentence boundary
_BOUNDARY_WHITESPACE_SCAN = 100    # chars to scan forward when no sentence boundary found


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
    search_start = max(0, target_pos - _BOUNDARY_SEARCH_RADIUS)
    search_end = min(len(text), target_pos + _BOUNDARY_SEARCH_RADIUS)
    window = text[search_start:search_end]

    # Find all sentence boundaries in window
    boundaries = []
    for m in re.finditer(r"[.!?]\s+", window):
        abs_pos = search_start + m.end()
        boundaries.append(abs_pos)

    if not boundaries:
        # Fall back to nearest whitespace
        for i in range(target_pos, min(target_pos + _BOUNDARY_WHITESPACE_SCAN, len(text))):
            if text[i : i + 1].isspace():
                return i + 1
        return target_pos

    # Return boundary closest to target
    return min(boundaries, key=lambda b: abs(b - target_pos))


def _resolve_chunk_size(content: str, cfg: dict) -> int:
    """Compute the adaptive sliding-window chunk size for ``content``.

    Adaptive chunk size: always compute a content-length-aware size and use
    min(configured, adaptive) so we never produce fewer than ~30 chunks on
    documents that are actually small/medium.  This avoids the 8-chunk
    constitution problem where each chunk is 8 KB, BM25 scores collapse, and
    amendment text gets buried in noise.

    Formula: target ~70 retrievable chunks per document.
    Clamp to [150, 2000] so we never produce single-sentence slivers
    (bad for semantic coherence) or oversized bricks (bad for retrieval).
    When the doc is large enough that 2000-token chunks still yields >=70
    chunks, the configured value wins — no change for already-large docs.
    """
    configured_chunk_size = cfg.get("chunk_size_tokens", _DEFAULT_CHUNK_SIZE)
    target_chunks = 70
    adaptive = max(150, min(2000, _estimate_tokens(content) // target_chunks))
    return min(configured_chunk_size, adaptive)


def _sliding_window_split(content: str, chunk_size: int, overlap_pct: float) -> List[str]:
    """Split content into overlapping windows snapped to sentence boundaries.

    Returns the chunk texts in order. Content at or below ``chunk_size`` tokens
    comes back as a single element.
    """
    if _estimate_tokens(content) <= chunk_size:
        return [content]

    chunk_chars = chunk_size * CHARS_PER_TOKEN
    overlap_chars = int(chunk_chars * overlap_pct)

    texts: List[str] = []
    pos = 0

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
            texts.append(chunk_text)

        # Advance position
        if end_pos >= len(content):
            break
        prev_pos = pos
        pos = end_pos - overlap_chars
        if pos <= (len(texts) - 1 if texts else 0):
            pos = end_pos  # Prevent infinite loop
        if pos <= prev_pos:
            # A backward-snapping boundary would otherwise stall the window.
            pos = end_pos

    return texts


def _make_chunk(
    text: str,
    idx: int,
    source_type: str,
    source_id: str,
    source_table: str,
    metadata: Optional[dict],
    tenant_id: str,
    project_id: str,
    classification: str,
    chunk_meta: Optional[Dict[str, Any]] = None,
) -> VectorChunk:
    """Build one VectorChunk with per-chunk (non-shared) metadata."""
    meta: Dict[str, Any] = dict(metadata or {})
    if chunk_meta:
        meta.update(chunk_meta)

    chunk = VectorChunk(
        chunk_id=f"chunk-{uuid.uuid4().hex[:12]}",
        content=text,
        source_type=source_type,
        source_id=str(source_id),
        source_table=source_table,
        chunk_index=idx,
        total_chunks=1,
        metadata=meta,
        tenant_id=tenant_id,
        project_id=project_id,
        classification=classification,
    )
    chunk.compute_content_hash()
    return chunk


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
    template: Optional[str] = None,
) -> List[VectorChunk]:
    """Chunk content by document-type template, or adaptively by length.

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
        template: Explicit template name from ``args/chunking_templates.yaml``
            (e.g. ``oscal_catalog``, ``stig_checklist``). ``None`` uses the
            configured default (``general`` — the historical sliding window).
            Never auto-detected; see
            :func:`tools.rag.chunking_templates.suggest_template`.

    Returns:
        List of VectorChunk instances (without embeddings — caller must embed).
    """
    if not content or not content.strip():
        return []

    cfg = chunk_config or _load_chunk_config()
    overlap_pct = cfg.get("overlap_pct", _DEFAULT_OVERLAP_PCT)

    content = content.strip()

    tmpl_name, tmpl, tmpl_fallback = get_template(template)
    strategy = tmpl.get("strategy", STRATEGY_SLIDING)

    # Metadata stamped on every chunk so the chunking decision is auditable.
    base_meta: Dict[str, Any] = {
        "chunking_template": tmpl_name,
        "chunking_strategy": strategy,
    }
    if tmpl_fallback:
        base_meta["chunking_template_requested"] = template
        base_meta["chunking_fallback_reason"] = tmpl_fallback

    seg_result = split_content(content, tmpl)
    if seg_result.pattern_errors:
        base_meta["chunking_pattern_errors"] = seg_result.pattern_errors

    # Structural/row-group template that produced no usable segments falls back
    # to the sliding window rather than emitting one giant chunk.
    if strategy != STRATEGY_SLIDING and not seg_result.segments:
        base_meta["chunking_strategy"] = STRATEGY_SLIDING
        base_meta["chunking_fallback_reason"] = (
            seg_result.fallback_reason or "no_segments"
        )
        seg_result.segments = []

    def _emit(texts_and_meta: List[tuple]) -> List[VectorChunk]:
        out: List[VectorChunk] = []
        for i, (text, extra) in enumerate(texts_and_meta):
            chunk_meta = dict(base_meta)
            chunk_meta.update(extra)
            out.append(
                _make_chunk(
                    text=text,
                    idx=i,
                    source_type=source_type,
                    source_id=source_id,
                    source_table=source_table,
                    metadata=metadata,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    classification=classification,
                    chunk_meta=chunk_meta,
                )
            )
        for c in out:
            c.total_chunks = len(out)
        return out

    # --- Structural / row-group path -------------------------------------
    if seg_result.segments:
        oversize_policy = tmpl.get("oversize_policy", "split")
        max_tokens = int(
            tmpl.get("max_chunk_tokens", cfg.get("chunk_size_tokens", _DEFAULT_CHUNK_SIZE))
        )
        emitted: List[tuple] = []
        for seg in seg_result.segments:
            extra = {"chunking_label": seg.label} if seg.label else {}
            if _estimate_tokens(seg.text) <= max_tokens:
                emitted.append((seg.text, extra))
                continue
            if oversize_policy == "keep":
                # "Never split a control" — emit whole and flag it.
                emitted.append((seg.text, {**extra, "chunking_oversize": True}))
                continue
            parts = _sliding_window_split(seg.text, max_tokens, overlap_pct)
            for p_i, part in enumerate(parts):
                emitted.append(
                    (
                        part,
                        {
                            **extra,
                            "chunking_item_split": True,
                            "chunking_item_part": p_i,
                            "chunking_item_parts": len(parts),
                        },
                    )
                )
        return _emit(emitted)

    # --- Sliding-window path (default / fallback) -------------------------
    chunk_size = _resolve_chunk_size(content, cfg)
    texts = _sliding_window_split(content, chunk_size, overlap_pct)
    return _emit([(t, {}) for t in texts])


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
    template: Optional[str] = None,
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
        template: Explicit chunking template name (see :func:`chunk_content`).

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
        template=template,
    )
