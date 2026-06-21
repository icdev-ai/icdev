"""DIC Provenance Adapter — bridge DIC search results to provenance_engine metadata.

Exposes get_chunk_provenance() for use by the DIC search blueprint to annotate
each search result with provenance metadata before rendering the footnote popover
(irad-aidp-09).

Attribution score is a deterministic token-overlap recall ratio — the fraction of
the chunk's tokens that appear in the LLM output paragraph — serving as a proxy
for attention-weight attribution (GUI-001).  No LLM call is made.
"""

from __future__ import annotations

import re
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.dic.provenance_adapter")

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_chunk_provenance(
    chunk_uuid: str,
    chunk_text: str = "",
    llm_output: str = "",
) -> dict[str, Any]:
    """Return provenance metadata for a RAG chunk.

    Queries rag_provenance_ledger via provenance_engine.get_lineage().
    Falls back to a direct DB query when provenance_engine is unavailable
    (e.g. before irad-aidp-02 ships).

    Args:
        chunk_uuid:  UUID of the RAG chunk.
        chunk_text:  Raw text of the chunk (from the search result).  Used
                     only for attribution scoring; safe to omit.
        llm_output:  LLM-generated paragraph being annotated.  Used only
                     for attribution scoring; safe to omit.

    Returns:
        {sha256, classification, source_doc_uuid, version_tree_ref,
         ingest_timestamp, attribution_score}
    """
    row = _fetch_lineage(chunk_uuid)

    resolved_chunk_text = row.get("chunk_text", "") or chunk_text
    attribution = _compute_attribution_score(resolved_chunk_text, llm_output)

    return {
        "sha256": row.get("sha256_hash", ""),
        "classification": row.get("classification_label", "CUI"),
        "source_doc_uuid": row.get("parent_doc_uuid", ""),
        "version_tree_ref": row.get("version_tree_ref", ""),
        "ingest_timestamp": row.get("ingest_timestamp", ""),
        "attribution_score": attribution,
    }


# ---------------------------------------------------------------------------
# Attribution score
# ---------------------------------------------------------------------------


def _compute_attribution_score(chunk_text: str, llm_output: str) -> float:
    """Token-overlap recall: |chunk_tokens ∩ output_tokens| / |chunk_tokens|.

    Returns 0.0 when either input is empty or the chunk has no tokens.
    Result is rounded to four decimal places.
    """
    if not chunk_text or not llm_output:
        return 0.0

    chunk_tokens = _tokenize(chunk_text)
    if not chunk_tokens:
        return 0.0

    output_tokens = _tokenize(llm_output)
    overlap = chunk_tokens & output_tokens
    return round(len(overlap) / len(chunk_tokens), 4)


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokens (strips punctuation)."""
    return set(re.findall(r"\b\w+\b", text.lower()))


# ---------------------------------------------------------------------------
# Lineage resolution (provenance_engine → direct DB fallback)
# ---------------------------------------------------------------------------


def _fetch_lineage(chunk_uuid: str) -> dict[str, Any]:
    """Try provenance_engine.get_lineage(); fall back to direct DB query."""
    try:
        from tools.rag.provenance_engine import get_lineage  # noqa: PLC0415

        result = get_lineage(chunk_uuid)
        if isinstance(result, dict) and result:
            return result
    except ImportError:
        # provenance_engine ships with irad-aidp-02; degrade gracefully until then
        logger.debug("provenance_engine not yet available; using direct DB query")
    except Exception as exc:
        logger.warning("get_lineage(%s) raised %s; falling back to direct DB", chunk_uuid, exc)

    return _query_ledger_direct(chunk_uuid)


def _query_ledger_direct(chunk_uuid: str) -> dict[str, Any]:
    """Direct fallback: read rag_provenance_ledger for the latest ingest record."""
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415

        with get_connection() as conn:
            cur = conn.execute(
                """
                SELECT sha256_hash,
                       classification_label,
                       parent_doc_uuid,
                       version_tree_ref,
                       ingest_timestamp
                FROM   rag_provenance_ledger
                WHERE  chunk_uuid = ?
                  AND  event_type = 'ingest'
                ORDER  BY created_at DESC
                LIMIT  1
                """,
                (chunk_uuid,),
            )
            row = cur.fetchone()
            if row:
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
    except Exception as exc:
        logger.debug("ledger direct query failed for %s: %s", chunk_uuid, exc)

    return {}
