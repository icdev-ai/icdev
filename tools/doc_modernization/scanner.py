# CUI // SP-CTI
"""Document Modernization Engine — scan pipeline.

For each DIC document's latest approved version: pull text (rag chunks via
dic_chunk_links, falling back to dic_sections for docs ingested without
chunking), run every enabled domain pack (extract → evaluate → recommend),
and persist findings.

Append-only discipline: findings never mutate. A state change or resolution
is a NEW row whose ``supersedes_id`` points at the superseded row for the
same ``dedupe_key``. ``get_findings()`` in the package __init__ resolves the
latest state per chain.

Incremental sweeps: a document is skipped when neither its approved version
nor the combined pack ``evidence_snapshot()`` hash changed since the last
scan (docmod_doc_scan_state).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

from .base_pack import ChunkRef, DomainPack
from .pack_loader import load_config, load_packs

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    from tools.db.storage import get_connection
    return get_connection()


def dedupe_key(doc_id: str, pack_id: str, entity_label: str, finding_type: str) -> str:
    raw = f"{doc_id}|{pack_id}|{entity_label.lower().strip()}|{finding_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def combined_evidence_hash(packs: dict[str, DomainPack], conn) -> str:
    parts = []
    for pid in sorted(packs):
        try:
            parts.append(f"{pid}:{packs[pid].evidence_snapshot(conn)}")
        except Exception as exc:
            logger.warning("docmod: evidence_snapshot failed for %s: %s", pid, exc)
            parts.append(f"{pid}:error")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _latest_approved_version(conn, doc_id: str) -> str | None:
    row = conn.execute(
        "SELECT version_id FROM dic_versions WHERE doc_id=%s AND status='approved' "
        "ORDER BY version_no DESC LIMIT 1",
        (doc_id,),
    ).fetchone()
    return dict(row)["version_id"] if row else None


def _doc_chunks(conn, doc_id: str, version_id: str) -> list[tuple[str, ChunkRef]]:
    """(text, ChunkRef) pairs — rag chunks first, dic_sections fallback."""
    chunks: list[tuple[str, ChunkRef]] = []
    try:
        rows = conn.execute(
            "SELECT dcl.id AS link_id, dcl.page, dcl.section, rc.content "
            "FROM dic_chunk_links dcl JOIN rag_chunks rc ON dcl.rag_chunk_id = rc.id "
            "WHERE dcl.version_id = %s",
            (version_id,),
        ).fetchall()
    except Exception:
        rows = []
    for r in rows:
        d = dict(r)
        if d.get("content"):
            chunks.append((
                d["content"],
                ChunkRef(doc_id=doc_id, version_id=version_id,
                         chunk_link_id=str(d.get("link_id") or ""),
                         page=d.get("page"), section=d.get("section")),
            ))
    if chunks:
        return chunks
    # Fallback: section content (e.g. docs imported via the docgen bridge).
    try:
        rows = conn.execute(
            "SELECT section_id, heading, content FROM dic_sections "
            "WHERE version_id = %s ORDER BY section_id",
            (version_id,),
        ).fetchall()
    except Exception:
        rows = []
    for r in rows:
        d = dict(r)
        if d.get("content"):
            chunks.append((
                d["content"],
                ChunkRef(doc_id=doc_id, version_id=version_id,
                         chunk_link_id=None, section=d.get("heading")),
            ))
    return chunks


def _open_findings(conn, doc_id: str) -> dict[str, dict]:
    """Latest-state open findings for a doc, keyed by dedupe_key."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM docmod_findings WHERE doc_id = %s ORDER BY created_at",
        (doc_id,),
    ).fetchall()]
    latest: dict[str, dict] = {}
    for r in rows:  # created_at ascending — later rows supersede earlier ones
        if r.get("dedupe_key"):
            latest[r["dedupe_key"]] = r
    return {k: v for k, v in latest.items() if v.get("state") == "open"}


def _insert_finding(conn, run_id: str, doc_id: str, version_id: str, entity, verdict,
                    replacement, key: str, tenant_id: str | None,
                    classification: str | None, state: str = "open",
                    supersedes_id: str | None = None) -> str:
    fid = f"fnd-{uuid.uuid4().hex[:12]}"
    conn.execute(
        """INSERT INTO docmod_findings
           (finding_id, run_id, doc_id, version_id, chunk_link_id, section_heading,
            page, pack_id, entity_label, entity_type, finding_type, currency_verdict,
            severity, rationale, evidence_json, recommended_replacement,
            replacement_evidence_json, confidence, state, supersedes_id,
            dedupe_key, created_at, tenant_id, classification)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            fid, run_id, doc_id, version_id,
            entity.chunk_ref.chunk_link_id, entity.chunk_ref.section,
            entity.chunk_ref.page, entity.pack_id, entity.label, entity.entity_type,
            verdict.finding_type, verdict.currency_verdict, verdict.severity,
            verdict.rationale, json.dumps(verdict.evidence, default=str),
            replacement.label if replacement else None,
            json.dumps(replacement.evidence, default=str) if replacement else None,
            verdict.confidence, state, supersedes_id, key, _now(),
            tenant_id, classification,
        ),
    )
    return fid


def scan_document(doc_id: str, conn=None, packs: dict[str, DomainPack] | None = None,
                  run_id: str | None = None, force: bool = False) -> dict:
    """Scan one document. Returns {doc_id, scanned, findings_new, findings_resolved}."""
    own_conn = conn is None
    if own_conn:
        conn = _connect()
    try:
        packs = packs or load_packs()
        version_id = _latest_approved_version(conn, doc_id)
        if not version_id:
            return {"doc_id": doc_id, "scanned": False, "reason": "no approved version"}

        evidence_hash = combined_evidence_hash(packs, conn)
        state_row = conn.execute(
            "SELECT last_version_id, last_evidence_hash FROM docmod_doc_scan_state WHERE doc_id=%s",
            (doc_id,),
        ).fetchone()
        if state_row and not force:
            s = dict(state_row)
            if s.get("last_version_id") == version_id and s.get("last_evidence_hash") == evidence_hash:
                return {"doc_id": doc_id, "scanned": False, "reason": "unchanged"}

        doc_row = conn.execute(
            "SELECT tenant_id, classification FROM dic_documents WHERE doc_id=%s", (doc_id,)
        ).fetchone()
        tenant_id = dict(doc_row).get("tenant_id") if doc_row else None
        classification = dict(doc_row).get("classification") if doc_row else None

        own_run = run_id is None
        started_at = _now()
        if own_run:
            run_id = f"run-{uuid.uuid4().hex[:12]}"

        threshold = float(load_config().get("confidence_threshold", 0.0) or 0.0)
        existing_open = _open_findings(conn, doc_id)
        seen_keys: set[str] = set()
        findings_new = 0

        for text, chunk_ref in _doc_chunks(conn, doc_id, version_id):
            for pack in packs.values():
                try:
                    entities = pack.extract(text, chunk_ref)
                except Exception as exc:
                    logger.warning("docmod: %s.extract failed: %s", pack.pack_id, exc)
                    continue
                for entity in entities:
                    try:
                        verdict = pack.evaluate(entity, conn)
                    except Exception as exc:
                        logger.warning("docmod: %s.evaluate failed: %s", pack.pack_id, exc)
                        continue
                    if not verdict.is_finding or verdict.confidence < threshold:
                        continue
                    key = dedupe_key(doc_id, pack.pack_id, entity.label, verdict.finding_type)
                    if key in seen_keys or key in existing_open:
                        seen_keys.add(key)
                        continue
                    seen_keys.add(key)
                    replacement = None
                    try:
                        replacement = pack.recommend(entity, verdict, conn)
                    except Exception as exc:
                        logger.warning("docmod: %s.recommend failed: %s", pack.pack_id, exc)
                    _insert_finding(conn, run_id, doc_id, version_id, entity, verdict,
                                    replacement, key, tenant_id, classification)
                    findings_new += 1

        # Resolve open findings whose entity no longer matches (append supersede row).
        findings_resolved = 0
        for key, row in existing_open.items():
            if key in seen_keys:
                continue
            conn.execute(
                """INSERT INTO docmod_findings
                   (finding_id, run_id, doc_id, version_id, pack_id, entity_label,
                    entity_type, finding_type, currency_verdict, severity, rationale,
                    confidence, state, supersedes_id, dedupe_key, created_at,
                    tenant_id, classification)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'superseded',%s,%s,%s,%s,%s)""",
                (
                    f"fnd-{uuid.uuid4().hex[:12]}", run_id, doc_id, version_id,
                    row["pack_id"], row["entity_label"], row.get("entity_type"),
                    row["finding_type"], "current", row.get("severity") or "medium",
                    "entity no longer present or now current", row.get("confidence"),
                    row["finding_id"], key, _now(), tenant_id, classification,
                ),
            )
            findings_resolved += 1

        open_count = len(_open_findings(conn, doc_id))
        # scan-state is a mutable bookkeeping table (NOT append-only)
        conn.execute("DELETE FROM docmod_doc_scan_state WHERE doc_id=%s", (doc_id,))
        conn.execute(
            """INSERT INTO docmod_doc_scan_state
               (doc_id, last_version_id, last_evidence_hash, last_scanned_at,
                open_findings, tenant_id, classification)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (doc_id, version_id, evidence_hash, _now(), open_count, tenant_id, classification),
        )
        if own_run:
            # scan_runs is append-only: one row written at completion, no UPDATEs.
            conn.execute(
                """INSERT INTO docmod_scan_runs
                   (run_id, scope_type, scope_id, pack_ids, evidence_hash, trigger,
                    docs_scanned, findings_new, findings_resolved,
                    started_at, finished_at, tenant_id, classification)
                   VALUES (%s,'document',%s,%s,%s,'manual',1,%s,%s,%s,%s,%s,%s)""",
                (run_id, doc_id, json.dumps(sorted(packs)), evidence_hash,
                 findings_new, findings_resolved, started_at, _now(),
                 tenant_id, classification),
            )
        conn.commit()
        return {"doc_id": doc_id, "scanned": True, "findings_new": findings_new,
                "findings_resolved": findings_resolved, "open_findings": open_count}
    finally:
        if own_conn:
            conn.close()


def scan_collection(collection_id: str | None = None, trigger: str = "manual",
                    force: bool = False) -> dict:
    """Scan every document in a collection (or the whole corpus when None)."""
    conn = _connect()
    try:
        packs = load_packs()
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        started_at = _now()
        evidence_hash = combined_evidence_hash(packs, conn)
        if collection_id:
            rows = conn.execute(
                "SELECT doc_id FROM dic_documents WHERE collection_id=%s", (collection_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT doc_id FROM dic_documents").fetchall()
        doc_ids = [dict(r)["doc_id"] for r in rows]

        scanned = new = resolved = 0
        for doc_id in doc_ids:
            result = scan_document(doc_id, conn=conn, packs=packs, run_id=run_id, force=force)
            if result.get("scanned"):
                scanned += 1
                new += result.get("findings_new", 0)
                resolved += result.get("findings_resolved", 0)
        # scan_runs is append-only: one summary row at completion, no UPDATEs.
        conn.execute(
            """INSERT INTO docmod_scan_runs
               (run_id, scope_type, scope_id, pack_ids, evidence_hash, trigger,
                docs_scanned, findings_new, findings_resolved, started_at, finished_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (run_id, "collection" if collection_id else "sweep", collection_id,
             json.dumps(sorted(packs)), evidence_hash, trigger,
             scanned, new, resolved, started_at, _now()),
        )
        conn.commit()
        return {"run_id": run_id, "docs_total": len(doc_ids), "docs_scanned": scanned,
                "findings_new": new, "findings_resolved": resolved}
    finally:
        conn.close()
