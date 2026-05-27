# CUI // SP-CTI
"""Citation / sourcing for AI-generated outputs — traceability and trust."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_citation(
    instance_id: str,
    stage: str,
    source_doc: str,
    cited_by: str,
    cited_in_type: str,
    cited_in_id: str,
    *,
    section: str | None = None,
    page_number: int | None = None,
    excerpt: str | None = None,
    source_type: str | None = None,
    doc_version: str | None = None,
) -> str:
    """Record a citation link between an AI/human output and its source document."""
    cite_id = f"wfc-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO wf_citations
               (id, instance_id, stage, source_doc, source_type, doc_version,
                section, page_number, excerpt, cited_by, cited_in_type, cited_in_id, created_at)
               VALUES (%s,?,?,?,?,?,?,?,?,?,?,?,%s)""",
            (cite_id, instance_id, stage, source_doc, source_type, doc_version,
             section, page_number, excerpt, cited_by, cited_in_type, cited_in_id, _now()),
        )
        try:
            conn.commit()
        except Exception:
            pass
        return cite_id
    finally:
        conn.close()


def get_by_instance(instance_id: str) -> list:
    """Return all citations for an instance, ordered by stage + created_at."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM wf_citations WHERE instance_id=%s ORDER BY stage, created_at",
            (instance_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_by_record(cited_in_type: str, cited_in_id: str) -> list:
    """Return citations attached to a specific feedback/approval/step record."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM wf_citations WHERE cited_in_type=%s AND cited_in_id=%s ORDER BY created_at",
            (cited_in_type, cited_in_id),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def format_citation_ref(citation: dict) -> str:
    """Format a citation as a short MLA-style inline reference.

    Example: 'NIST SP 800-53 Rev 5, AC-2(a), p.47'
    """
    parts = [citation["source_doc"]]
    if citation.get("doc_version"):
        parts[0] += f" {citation['doc_version']}"
    if citation.get("section"):
        parts.append(citation["section"])
    if citation.get("page_number") is not None:
        parts.append(f"p.{citation['page_number']}")
    return ", ".join(parts)


def format_citation_block(citations: list) -> str:
    """Format a list of citations as a readable block for embedding in AI output.

    Example:
        Sources:
        [1] NIST SP 800-53 Rev 5, AC-2(a), p.47
            "The organization manages information system accounts..."
    """
    if not citations:
        return ""
    lines = ["Sources:"]
    for i, c in enumerate(citations, start=1):
        ref = format_citation_ref(c)
        lines.append(f"  [{i}] {ref}")
        if c.get("excerpt"):
            lines.append(f'      "{c["excerpt"]}"')
    return "\n".join(lines)


def add_citations_from_standards(
    instance_id: str,
    stage: str,
    standards: list,
    cited_by: str,
    cited_in_type: str,
    cited_in_id: str,
) -> list[str]:
    """Bulk-add citations from a list of AI standard templates.

    Called by the engine after automated steps use standards from document_manager.
    """
    import json
    ids = []
    for std in standards:
        schema = std.get("schema") or {}
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except Exception:
                schema = {}
        cite_id = add_citation(
            instance_id=instance_id,
            stage=stage,
            source_doc=std["name"],
            cited_by=cited_by,
            cited_in_type=cited_in_type,
            cited_in_id=cited_in_id,
            source_type="standard",
            doc_version=std.get("version"),
        )
        ids.append(cite_id)
    return ids
