# CUI // SP-CTI
"""Full-document regeneration — rebuild a stale document from current evidence
as a NEW pending_review version of the SAME document.

Uses doc_generator.generate_document(target_doc_id=...) so the whole
grounding/verification/citation machinery (and the existing per-version diff
endpoint GET /api/versions/<a>/diff/<b>) applies unchanged. The old approved
version is never touched (dic_versions is append-only); HITL approves or
rejects the new version through the existing review queue.
"""
from __future__ import annotations

import json

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def _connect():
    from tools.db.storage import get_connection
    return get_connection()


def _approved_text(conn, doc_id: str) -> tuple[str | None, str]:
    """(version_id, reassembled markdown) of the latest approved version."""
    row = conn.execute(
        "SELECT version_id FROM dic_versions WHERE doc_id = %s AND status='approved' "
        "ORDER BY version_no DESC LIMIT 1",
        (doc_id,),
    ).fetchone()
    if not row:
        return None, ""
    version_id = dict(row)["version_id"]
    sections = [dict(r) for r in conn.execute(
        "SELECT heading, content FROM dic_sections WHERE version_id = %s ORDER BY section_id",
        (version_id,),
    ).fetchall()]
    text = "\n\n".join(f"## {s['heading']}\n{s.get('content') or ''}" for s in sections)
    return version_id, text


def regenerate_document(doc_id: str, created_by: str = "docmod_regen") -> dict:
    """Rebuild `doc_id` from current evidence + open findings.

    Returns {doc_id, old_version_id, new_version_id, findings_used, error?}.
    Diff is rendered at request time via the existing
    /document-intelligence/api/versions/<old>/diff/<new> endpoint.
    """
    from tools.doc_modernization import get_findings

    conn = _connect()
    try:
        doc_row = conn.execute(
            "SELECT doc_id, collection_id, title, tenant_id, classification "
            "FROM dic_documents WHERE doc_id = %s",
            (doc_id,),
        ).fetchone()
        if not doc_row:
            return {"doc_id": doc_id, "error": "document not found"}
        doc = dict(doc_row)

        old_version_id, old_text = _approved_text(conn, doc_id)
        if not old_version_id:
            return {"doc_id": doc_id, "error": "no approved version to regenerate from"}

        findings = get_findings(doc_id=doc_id, state="open", conn=conn)
        change_lines = []
        for f in findings:
            try:
                evidence = json.loads(f.get("evidence_json") or "[]")
            except Exception:
                evidence = []
            sources = ", ".join(e.get("source", "") for e in evidence if e.get("source"))
            line = (
                f"- '{f['entity_label']}' is {f['currency_verdict']} "
                f"({f.get('rationale','')})"
            )
            if f.get("recommended_replacement"):
                line += f" -> replace with '{f['recommended_replacement']}'"
            if sources:
                line += f" [evidence: {sources}]"
            change_lines.append(line)

        change_context = (
            "MANDATORY MODERNIZATION CHANGES (deterministic findings — apply "
            "each; cite the given evidence ids):\n" + "\n".join(change_lines)
            if change_lines else ""
        )
        prior_context = (
            "CURRENT APPROVED DOCUMENT (authoritative structure/scope — "
            "modernize its content, do not change its purpose):\n" + old_text
        )

        from tools.document_intelligence.doc_generator import generate_document
        result = generate_document(
            query=(
                f"Regenerate the document '{doc.get('title') or doc_id}' with "
                f"current, supported technology. Preserve the section structure. "
                f"Cite all facts."
            ),
            collection_id=doc.get("collection_id"),
            tenant_id=doc.get("tenant_id") or "default",
            classification=doc.get("classification") or "CUI",
            created_by=created_by,
            supplemental_text=f"{prior_context}\n\n{change_context}".strip(),
            target_doc_id=doc_id,
        )
        if getattr(result, "error", None):
            return {"doc_id": doc_id, "old_version_id": old_version_id,
                    "error": result.error}
        return {
            "doc_id": doc_id,
            "old_version_id": old_version_id,
            "new_version_id": result.version_id,
            "findings_used": len(findings),
            "diff_url": (
                f"/document-intelligence/api/versions/{old_version_id}/diff/{result.version_id}"
            ),
        }
    finally:
        conn.close()
