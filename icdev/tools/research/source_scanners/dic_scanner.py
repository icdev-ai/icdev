# CUI // SP-CTI
"""DIC source scanner for the Research engine.

Queries rag_chunks from DIC collections and maps them to research_signals rows.
Scanner key: "dic_collection"

Usage (via research engine):
    from tools.research.source_scanner import run_scan
    run_scan(session_id, source="dic_collection", session_config={
        "dic_collection_id": "my-collection",
        "tenant_id": "default",
    })

Or standalone:
    from tools.research.source_scanners.dic_scanner import scan_dic_collection
    signals = scan_dic_collection(config={}, session_config={
        "dic_collection_id": "compliance-docs",
    })
"""
from __future__ import annotations

import hashlib
import json
from tools.logging.icdev_logger import get_logger
from datetime import datetime, timezone

logger = get_logger(__name__)


def scan_dic_collection(config: dict, session_config: dict | None = None) -> list[dict]:
    """Scan a DIC collection and surface RAG chunks as research signals.

    Args:
        config: Full research_config.yaml dict (unused but required by scanner protocol).
        session_config: Must contain 'dic_collection_id'; optionally 'tenant_id', 'limit'.

    Returns:
        List of normalized signal dicts with source='dic_collection'.
    """
    sc = session_config or {}
    collection_id = (sc.get("dic_collection_id") or "default").strip()
    tenant_id = (sc.get("tenant_id") or "default").strip()
    limit = int(sc.get("limit") or 50)

    signals: list[dict] = []
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT rc.chunk_id, rc.content, rc.source_id, rc.metadata_json,
                   dd.title AS doc_title
            FROM   rag_chunks rc
            LEFT JOIN dic_documents dd ON dd.id = rc.source_id
            WHERE  rc.collection_id = %s
              AND  rc.tenant_id     = %s
            ORDER  BY rc.created_at DESC
            LIMIT  %s
            """,
            (collection_id, tenant_id, limit),
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("dic_scanner: DB query failed: %s", exc)
        return [{"source_type": "scan_error", "error": str(exc)}]

    now_iso = datetime.now(timezone.utc).isoformat()

    for row in rows:
        try:
            body = (row["content"] if hasattr(row, "__getitem__") else row[1]) or ""
            if not body.strip():
                continue
            doc_title = (
                row["doc_title"] if hasattr(row, "__getitem__") else row[4]
            ) or collection_id
            chunk_id = row["chunk_id"] if hasattr(row, "__getitem__") else row[0]

            # Extract keywords from metadata if available
            raw_meta = row["metadata_json"] if hasattr(row, "__getitem__") else row[3]
            meta: dict = {}
            if raw_meta:
                try:
                    meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
                except Exception:
                    pass
            keywords = meta.get("keywords") or meta.get("tags") or []

            content_hash = hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()

            signals.append({
                "id": f"dic-{chunk_id or content_hash[:12]}",
                "source": "dic_collection",
                "source_type": "dic_rag_chunk",
                "title": f"[DIC:{collection_id}] {doc_title}"[:255],
                "body": body[:2000],
                "url": f"/document-intelligence/notebook?collection_id={collection_id}",
                "author": f"dic:{collection_id}",
                "upvotes": 0,
                "citations": 0,
                "sentiment": "neutral",
                "content_hash": content_hash,
                "keywords": json.dumps(keywords[:10] if isinstance(keywords, list) else []),
                "metadata": json.dumps({"collection_id": collection_id, "chunk_id": chunk_id}),
                "discovered_at": now_iso,
            })
        except Exception as exc:
            logger.debug("dic_scanner: skipping chunk: %s", exc)

    logger.info(
        "dic_scanner: collection=%s — %d signals from %d chunks",
        collection_id, len(signals), len(rows),
    )
    return signals
