# CUI // SP-CTI
"""Full-document regeneration — rebuild a stale document from current evidence
as a NEW pending_review version of the SAME document.

Uses doc_generator.generate_document(target_doc_id=...) so the whole
grounding/verification/citation machinery (and the existing per-version diff
endpoint GET /api/versions/<a>/diff/<b>) applies unchanged. The old approved
version is never touched (dic_versions is append-only); HITL approves or
rejects the new version through the existing review queue.

Regeneration quality gate (dmx-qa-01): the generator already runs per-section
confidence verification, placeholder detection and confabulation assessment, but
it did NOT re-validate citations against current evidence, check internal
consistency, or diff the new draft against the prior approved assertions before
the version entered the review queue. This module now passes a deterministic
``quality_gate`` (``regen_quality_gate.evaluate_regeneration_quality``) into
``generate_document`` so a defective draft is persisted as ``quality_blocked``
— NOT ``pending_review`` — unless an authorized human forces the override, which
is recorded as an append-only review note (mirrors the approve-time
placeholder/citation publish gate's force_* + audit pattern). The gate READS the
generated draft; it never mutates dic_versions / dic_edit_history.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Status a regenerated version is persisted with when the quality gate blocks it
# and the caller did not force the override. Deliberately NOT in the review-queue
# status set ('pending_review','needs_revision','draft'), so a blocked draft
# never reaches HITL until a reviewer forces it.
BLOCKED_STATUS = "quality_blocked"


def _connect():
    from tools.db.storage import get_connection
    return get_connection()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_gate_note(version_id: str, note_text: str, reviewer: str,
                      tenant_id: str = "", classification: str = "CUI") -> None:
    """Append a review note recording a gate block / forced override.

    Writes to dic_review_notes — the same append-only surface the approve-time
    publish gate uses for force_* audit — never touching dic_versions. Best-effort:
    an audit-write failure must not crash a regeneration.
    """
    try:
        conn = _connect()
        try:
            note_id = f"note_{hashlib.sha256(f'{version_id}:{_now_iso()}'.encode()).hexdigest()[:16]}"
            conn.execute(
                "INSERT INTO dic_review_notes "
                "(note_id, item_id, item_type, note_text, reviewer_id, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (note_id, version_id, "version", note_text, reviewer, _now_iso()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("regen_orchestrator: gate note write failed: %s", exc)


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


def regenerate_document(doc_id: str, created_by: str = "docmod_regen",
                        *, force: bool = False, reviewer: str = "") -> dict:
    """Rebuild `doc_id` from current evidence + open findings.

    A deterministic quality gate (citation re-validation against current
    evidence, internal-consistency check, claim-preservation diff) runs before
    the regenerated version enters the review queue. If the gate finds a blocking
    defect and ``force`` is not set, the version is persisted as
    ``quality_blocked`` (withheld from HITL) with a blocking reason; ``force=True``
    lets an authorized ``reviewer`` promote it to ``pending_review`` anyway, which
    is recorded as an append-only review note.

    Returns {doc_id, old_version_id, new_version_id, findings_used, status,
    blocked, forced, quality_gate, diff_url, error?}.
    """
    from tools.doc_modernization import get_findings
    from tools.doc_modernization.regen_quality_gate import (
        evaluate_regeneration_quality, format_gate_reason,
    )

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

        # Quality gate closure — evaluated by generate_document right before it
        # persists, so a blocking defect can withhold pending_review. Captures the
        # old approved text (for the claim-preservation diff) and the report.
        gate_report: dict = {}

        def _gate(sections, allowed_sources, full_text) -> str:
            report = evaluate_regeneration_quality(
                new_sections=sections,
                old_text=old_text,
                allowed_sources=allowed_sources,
                new_text=full_text,
            )
            gate_report.clear()
            gate_report.update(report)
            if report.get("blocked") and not force:
                return BLOCKED_STATUS
            return "pending_review"

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
            quality_gate=_gate,
        )
        if getattr(result, "error", None):
            return {"doc_id": doc_id, "old_version_id": old_version_id,
                    "error": result.error, "quality_gate": gate_report}

        blocked = bool(gate_report.get("blocked"))
        reviewer_id = reviewer or created_by
        # Audit the gate decision (append-only note; never mutates dic_versions).
        if blocked:
            reason = format_gate_reason(gate_report)
            if force:
                _record_gate_note(
                    result.version_id,
                    f"FORCE-REGENERATED past quality gate ({reason})",
                    reviewer_id,
                    tenant_id=doc.get("tenant_id") or "",
                    classification=doc.get("classification") or "CUI",
                )
            else:
                _record_gate_note(
                    result.version_id,
                    f"REGENERATION BLOCKED by quality gate ({reason}) — "
                    f"version withheld as '{BLOCKED_STATUS}'; resubmit with force to override",
                    reviewer_id,
                    tenant_id=doc.get("tenant_id") or "",
                    classification=doc.get("classification") or "CUI",
                )

        return {
            "doc_id": doc_id,
            "old_version_id": old_version_id,
            "new_version_id": result.version_id,
            "findings_used": len(findings),
            "status": getattr(result, "status", "pending_review"),
            "blocked": blocked and not force,
            "forced": blocked and force,
            "quality_gate": gate_report,
            "diff_url": (
                f"/document-intelligence/api/versions/{old_version_id}/diff/{result.version_id}"
            ),
        }
    finally:
        conn.close()
