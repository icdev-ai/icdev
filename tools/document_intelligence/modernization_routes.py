# CUI // SP-CTI
"""DIC Modernization routes — the document-centric HITL surface.

Registered on the existing ``dic_bp`` (blueprint.py imports this module at the
bottom — one line, keeping the 176KB blueprint from growing). Every route
degrades gracefully when the docmod engine tables are absent.

Routes:
    GET  /api/modernization/summary            corpus triage (counts, worst-first)
    GET  /api/modernization/doc/<doc_id>/findings   section-level badges feed
    POST /api/modernization/bulk               queue_redlines | queue_regen | rescan
    POST /api/modernization/findings/<id>/resolve   accepted|rejected + card reconcile
    POST /api/ingest/batch                     bulk legacy-doc onboarding (docmod-ux-02)
"""
from __future__ import annotations

import json
import queue as _queue
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import jsonify, request

from tools.document_intelligence.blueprint import (
    _JOB_LOCK,
    _JOB_QUEUES,
    _conn,
    _security_context,
    dic_bp,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# 'Awaiting review' = open AND redline_drafted: once the sweep drafts a
# redline the finding still needs a human — it must never drop off the board.
_AWAITING_STATES = ("open", "redline_drafted")


def _open_findings(doc_id: str | None = None) -> list[dict]:
    try:
        from tools.doc_modernization import get_findings
        return [f for f in get_findings(doc_id=doc_id)
                if f.get("state") in _AWAITING_STATES]
    except Exception as exc:
        logger.debug("dic modernization: engine unavailable: %s", exc)
        return []


@dic_bp.route("/api/modernization/findings", methods=["GET"])
def api_modernization_findings():
    """Corpus-wide findings list — the tiles show counts; this shows the
    findings themselves (user feedback: '11 open findings, I can't see them').

    Query params: state (default 'open'), finding_type, limit (default 200).
    """
    import json as _json

    state = (request.args.get("state") or "open").strip() or None
    finding_type = (request.args.get("finding_type") or "").strip() or None
    limit = min(int(request.args.get("limit", 200) or 200), 1000)
    try:
        from tools.doc_modernization import get_findings
        if state == "open":
            # default view = awaiting review (open + redline_drafted)
            findings = [f for f in get_findings(finding_type=finding_type)
                        if f.get("state") in _AWAITING_STATES][:limit]
        else:
            findings = get_findings(state=state, finding_type=finding_type)[:limit]
    except Exception as exc:
        logger.debug("dic modernization: engine unavailable: %s", exc)
        return jsonify([])

    titles: dict[str, str] = {}
    try:
        conn = _conn()
        try:
            for doc_id in {f["doc_id"] for f in findings}:
                row = conn.execute(
                    "SELECT title FROM dic_documents WHERE doc_id = %s", (doc_id,)
                ).fetchone()
                titles[doc_id] = (dict(row).get("title") if row else None) or doc_id
        finally:
            conn.close()
    except Exception:
        pass

    out = []
    for f in findings:
        try:
            evidence = _json.loads(f.get("evidence_json") or "[]")
        except Exception:
            evidence = []
        out.append({
            "finding_id": f["finding_id"],
            "doc_id": f["doc_id"],
            "doc_title": titles.get(f["doc_id"], f["doc_id"]),
            "section_heading": f.get("section_heading"),
            "finding_type": f["finding_type"],
            "severity": f.get("severity") or "medium",
            "entity_label": f["entity_label"],
            "currency_verdict": f["currency_verdict"],
            "rationale": f.get("rationale"),
            "recommended_replacement": f.get("recommended_replacement"),
            "redline_suggestion_id": f.get("redline_suggestion_id"),
            "state": f.get("state"),
            "evidence": evidence,
        })
    return jsonify(out)


@dic_bp.route("/api/modernization/summary", methods=["GET"])
def api_modernization_summary():
    """Corpus triage: finding counts by type + worst-first document list."""
    findings = _open_findings()
    by_type: dict[str, int] = {}
    by_doc: dict[str, dict] = {}
    for f in findings:
        by_type[f["finding_type"]] = by_type.get(f["finding_type"], 0) + 1
        d = by_doc.setdefault(f["doc_id"], {"doc_id": f["doc_id"], "open_findings": 0,
                                            "types": {}, "max_severity": "low"})
        d["open_findings"] += 1
        d["types"][f["finding_type"]] = d["types"].get(f["finding_type"], 0) + 1
        rank = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        if rank.get(f.get("severity") or "medium", 2) > rank.get(d["max_severity"], 1):
            d["max_severity"] = f.get("severity") or "medium"

    # enrich with titles (best-effort)
    docs = sorted(by_doc.values(), key=lambda d: -d["open_findings"])[:100]
    if docs:
        try:
            conn = _conn()
            try:
                for d in docs:
                    row = conn.execute(
                        "SELECT title FROM dic_documents WHERE doc_id = %s",
                        (d["doc_id"],),
                    ).fetchone()
                    d["title"] = (dict(row).get("title") if row else None) or d["doc_id"]
            finally:
                conn.close()
        except Exception:
            for d in docs:
                d.setdefault("title", d["doc_id"])
    return jsonify({
        "total_open": len(findings),
        "by_type": by_type,
        "documents": docs,
    })


@dic_bp.route("/api/modernization/doc/<doc_id>/findings", methods=["GET"])
def api_modernization_doc_findings(doc_id: str):
    """Section-level findings for inline badges + the redline panel."""
    out = []
    for f in _open_findings(doc_id=doc_id) + [
        f for f in _all_findings_states(doc_id) if f.get("state") == "redline_drafted"
    ]:
        try:
            evidence = json.loads(f.get("evidence_json") or "[]")
        except Exception:
            evidence = []
        out.append({
            "finding_id": f["finding_id"],
            "section_heading": f.get("section_heading"),
            "entity_label": f["entity_label"],
            "finding_type": f["finding_type"],
            "currency_verdict": f["currency_verdict"],
            "severity": f.get("severity") or "medium",
            "rationale": f.get("rationale"),
            "recommended_replacement": f.get("recommended_replacement"),
            "redline_suggestion_id": f.get("redline_suggestion_id"),
            "state": f.get("state"),
            "evidence": evidence,
        })
    return jsonify(out)


def _all_findings_states(doc_id: str) -> list[dict]:
    try:
        from tools.doc_modernization import get_findings
        return get_findings(doc_id=doc_id)
    except Exception:
        return []


@dic_bp.route("/api/modernization/scan", methods=["POST"])
def api_modernization_scan():
    """One-click modernization scan — the missing link after upload.
    Body: {collection_id?} — omitted scans the whole corpus."""
    body = request.get_json(silent=True) or {}
    collection_id = (body.get("collection_id") or "").strip() or None
    try:
        from tools.doc_modernization.scanner import scan_collection
        result = scan_collection(collection_id=collection_id, trigger="api",
                                 force=bool(body.get("force", False)))
        return jsonify(result)
    except Exception as exc:
        logger.warning("dic modernization scan failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@dic_bp.route("/api/collections/<collection_id>", methods=["DELETE"])
def api_delete_collection(collection_id: str):
    """Delete a collection and its documents (admin only).

    Removes documents, sections, chunk links, freshness rows, suggestions, and
    linked rag chunks. dic_versions rows are retained (append-only, NIST AU) as
    orphaned history; docmod findings likewise keep their append-only trail.
    """
    from tools.document_intelligence.blueprint import _require_role

    if not _require_role(collection_id, "admin"):
        return jsonify({"error": "admin role required"}), 403
    conn = _conn()
    try:
        doc_rows = [dict(r)["doc_id"] for r in conn.execute(
            "SELECT doc_id FROM dic_documents WHERE collection_id = %s", (collection_id,)
        ).fetchall()]
        chunk_ids = [dict(r)["rag_chunk_id"] for r in conn.execute(
            "SELECT rag_chunk_id FROM dic_chunk_links WHERE collection_id = %s", (collection_id,)
        ).fetchall()]
        for stmt, params in [
            ("DELETE FROM dic_sections WHERE doc_id IN (SELECT doc_id FROM dic_documents WHERE collection_id = %s)", (collection_id,)),
            ("DELETE FROM dic_chunk_links WHERE collection_id = %s", (collection_id,)),
            ("DELETE FROM dic_doc_freshness WHERE collection_id = %s", (collection_id,)),
            ("DELETE FROM dic_suggestions WHERE collection_id = %s", (collection_id,)),
            ("DELETE FROM dic_documents WHERE collection_id = %s", (collection_id,)),
            ("DELETE FROM dic_collections WHERE collection_id = %s", (collection_id,)),
        ]:
            try:
                conn.execute(stmt, params)
            except Exception as exc:
                logger.warning("dic delete-collection step failed (%s): %s", stmt[:40], exc)
                try:
                    conn.rollback()
                except Exception:
                    pass
        for cid in chunk_ids:
            try:
                conn.execute("DELETE FROM rag_chunks WHERE id = %s", (cid,))
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                break
        conn.commit()
        logger.info("dic: collection %s deleted (%d docs, %d chunks)",
                    collection_id, len(doc_rows), len(chunk_ids))
        return jsonify({"deleted": collection_id, "documents": len(doc_rows),
                        "chunks": len(chunk_ids)})
    finally:
        conn.close()


@dic_bp.route("/api/modernization/bulk", methods=["POST"])
def api_modernization_bulk():
    """Bulk actions from the triage board. All HITL-gated downstream."""
    body = request.get_json(silent=True) or {}
    action = (body.get("action") or "").strip()
    doc_ids = [d for d in (body.get("doc_ids") or []) if d]
    if action not in ("queue_redlines", "queue_regen", "rescan"):
        return jsonify({"error": f"unknown action: {action}"}), 400
    if not doc_ids:
        return jsonify({"error": "doc_ids required"}), 400

    results = []
    try:
        if action == "rescan":
            from tools.doc_modernization.scanner import scan_document
            for d in doc_ids:
                results.append({**scan_document(d, force=True), "doc_id": d})
        elif action == "queue_redlines":
            from tools.doc_modernization.redline_drafter import draft_open_redlines
            for d in doc_ids:
                results.append({"doc_id": d, **draft_open_redlines(doc_id=d)})
        else:  # queue_regen
            from tools.doc_modernization.regen_orchestrator import regenerate_document
            # force lets an authorized reviewer promote a quality-gate-blocked
            # regeneration to pending_review anyway (audited as a review note).
            force = bool(body.get("force"))
            reviewer = (body.get("reviewer") or "").strip()
            for d in doc_ids:
                results.append(regenerate_document(d, force=force, reviewer=reviewer))
        return jsonify({"action": action, "results": results})
    except Exception as exc:
        logger.warning("dic modernization bulk %s failed: %s", action, exc)
        return jsonify({"error": str(exc), "results": results}), 500


@dic_bp.route("/api/modernization/findings/<finding_id>/resolve", methods=["POST"])
def api_modernization_resolve(finding_id: str):
    """HITL disposition: appends the accepted/rejected state row (append-only)
    and reconciles the suggested kanban card immediately."""
    body = request.get_json(silent=True) or {}
    disposition = (body.get("disposition") or "").strip()
    if disposition not in ("accepted", "rejected"):
        return jsonify({"error": "disposition must be 'accepted' or 'rejected'"}), 400
    try:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT * FROM docmod_findings WHERE finding_id = %s", (finding_id,)
            ).fetchone()
            if not row:
                return jsonify({"error": "finding not found"}), 404
            f = dict(row)
            conn.execute(
                """INSERT INTO docmod_findings
                   (finding_id, run_id, doc_id, version_id, pack_id, entity_label,
                    entity_type, finding_type, currency_verdict, severity, rationale,
                    evidence_json, recommended_replacement, confidence, state,
                    supersedes_id, redline_suggestion_id, dedupe_key, created_at,
                    tenant_id, classification)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    f"fnd-{uuid.uuid4().hex[:12]}", f["run_id"], f["doc_id"],
                    f["version_id"], f["pack_id"], f["entity_label"],
                    f.get("entity_type"), f["finding_type"], f["currency_verdict"],
                    f.get("severity") or "medium", f.get("rationale"),
                    f.get("evidence_json"), f.get("recommended_replacement"),
                    f.get("confidence"), disposition, f["finding_id"],
                    f.get("redline_suggestion_id"), f.get("dedupe_key"), _now(),
                    f.get("tenant_id"), f.get("classification"),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        reconciled = {}
        try:
            from tools.doc_modernization.card_bridge import reconcile
            reconciled = reconcile()
        except Exception as exc:
            logger.debug("dic modernization: card reconcile skipped: %s", exc)
        return jsonify({"finding_id": finding_id, "state": disposition,
                        "cards_reconciled": reconciled.get("closed", 0)})
    except Exception as exc:
        logger.warning("dic modernization resolve failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── docmod-ux-02: bulk legacy-document onboarding ────────────────────────────

def _form_bool(name: str, default: bool) -> bool:
    """Read an optional multipart boolean, defaulting when absent or blank."""
    raw = (request.form.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dic_bp.route("/api/ingest/batch", methods=["POST"])
def api_ingest_batch():
    """Multi-file ingest for hundreds of legacy docs — wraps the existing
    (previously unexposed) ingest_orchestrator.ingest_batch with the same
    _JOB_QUEUES/SSE progress pattern as single-file ingest.

    ``embed`` and ``bridge_kg`` default ON here, unlike ``ingest_batch`` itself.
    That function defaults every enrichment off, which is right for a low-level
    primitive a caller drives deliberately — but this route is the front door for
    "ingest my documents", and it passed neither, so bulk uploads silently
    produced documents with no embeddings (invisible to vector search) and no KG
    entities (invisible to /explorer and /analytics). Nothing reported a failure;
    the documents simply did not work. That is how the live corpus ended up with
    559 chunks but only ~26 document-derived KG nodes.

    The cheap defaults stay available — pass ``embed=false`` / ``bridge_kg=false``
    to opt out — but opting out of a working document is now an explicit choice
    the caller makes, not a silent one the route makes for them. The remaining
    per-file LLM enrichments (summarize, metadata, correspondence) stay off by
    default: those are genuinely expensive per document and, unlike these two,
    their absence does not stop the document from being found.
    """
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files provided (multipart field 'files')"}), 400
    collection_id = (request.form.get("collection_id") or "default").strip()
    embed = _form_bool("embed", True)
    bridge_kg = _form_bool("bridge_kg", True)
    tenant_id, classification = _security_context()

    staging = Path(tempfile.gettempdir()) / f"dic-batch-{uuid.uuid4().hex[:8]}"
    staging.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for f in files:
        name = Path(f.filename or f"upload-{uuid.uuid4().hex[:6]}").name
        dest = staging / name
        f.save(str(dest))
        saved.append(dest)

    job_id = f"batch-{uuid.uuid4().hex[:10]}"
    q: _queue.Queue = _queue.Queue()
    with _JOB_LOCK:
        _JOB_QUEUES[job_id] = q

    def _run():
        try:
            from tools.document_intelligence.ingest_orchestrator import ingest_batch

            # ingest_batch progress contract: (done_count, total, anomalous_paths)
            def progress_cb(done, total, anomalous_paths):
                q.put(json.dumps({"file": done, "of": total,
                                  "anomalous": len(anomalous_paths or [])}))

            result = ingest_batch(
                [str(p) for p in saved], collection_id,
                tenant_id=tenant_id, classification=classification,
                embed=embed, bridge_kg=bridge_kg,
                progress_cb=progress_cb,
            )
            q.put(json.dumps({"done": True,
                              "succeeded": getattr(result, "succeeded", None),
                              "failed": getattr(result, "failed", None)}))
        except Exception as exc:
            logger.warning("dic batch ingest failed: %s", exc)
            q.put(json.dumps({"done": True, "error": str(exc)}))

    threading.Thread(target=_run, name=f"dic-batch-{job_id}", daemon=True).start()
    return jsonify({"job_id": job_id, "files": len(saved),
                    "progress_url": f"/document-intelligence/api/ingest/{job_id}/stream"}), 202
