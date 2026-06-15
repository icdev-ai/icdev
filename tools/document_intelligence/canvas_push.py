# CUI // SP-CTI
"""canvas_push — shared utility for any canvas to push artifacts into DIC.

Usage:
    from tools.document_intelligence.canvas_push import push_artifact

    result = push_artifact(
        canvas_name="compliance",
        title="FedRAMP SSP — Acme Corp",
        content_text="System security plan text ...",
        collection_id="compliance-ssp",
        classification="CUI",
        tenant_id="default",
    )
    # returns {doc_id, chunks, warnings, canvas_name, title}
"""
from __future__ import annotations

import logging
import os
import tempfile

logger = logging.getLogger(__name__)


def push_artifact(
    canvas_name: str,
    title: str,
    content_text: str,
    collection_id: str = "default",
    classification: str = "CUI",
    tenant_id: str = "default",
) -> dict:
    """Ingest a canvas-generated text artifact into the DIC RAG/KG store.

    Args:
        canvas_name: Source canvas key (e.g. 'compliance', 'zig', 'foundry').
        title: Human-readable document title.
        content_text: Plain text content to ingest.
        collection_id: Target DIC collection (default: 'default').
        classification: Security classification label.
        tenant_id: Tenant scope.

    Returns:
        dict with doc_id, chunks, warnings, canvas_name, title.
    """
    if not content_text or not content_text.strip():
        return {"error": "content_text is empty", "canvas_name": canvas_name, "title": title}

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".txt", mode="w", encoding="utf-8",
            prefix=f"dic_canvas_{canvas_name}_",
        ) as f:
            f.write(f"Title: {title}\nCanvas: {canvas_name}\n\n{content_text}")
            tmp_path = f.name

        from tools.document_intelligence.ingest_orchestrator import ingest_file
        outcome = ingest_file(
            tmp_path,
            collection_id,
            tenant_id=tenant_id,
            classification=classification,
            created_by=f"canvas_{canvas_name}",
        )
        return {
            "doc_id": outcome.doc_id,
            "chunks": outcome.chunks,
            "warnings": outcome.errors,
            "canvas_name": canvas_name,
            "title": title,
            "collection_id": collection_id,
        }
    except Exception as exc:
        logger.warning("canvas_push.push_artifact [%s]: %s", canvas_name, exc)
        return {"error": str(exc), "canvas_name": canvas_name, "title": title}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
