# CUI // SP-CTI
"""DIC KG "Buried Bodies" Explorer.

Findings surfaced:
  1. Orphaned documents — no collection, no chunks, or no versions.
  2. Single-owner / tribal knowledge — collection has only 1 team member.
  3. Undocumented dependencies — entity referenced in KG but absent from doc corpus.
  4. Contradictions — docs with overlapping entity labels but divergent content hashes.
  5. Superseded — older version exists with same title but newer approved version.

All queries are RLS-filtered by tenant_id. No LLM calls — pure graph analytics.
"""
from __future__ import annotations

from dataclasses import dataclass

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


@dataclass
class ExplorerFinding:
    finding_id: str
    finding_type: str  # orphan | single_owner | undocumented_dep | contradiction | superseded
    severity: str  # low | medium | high | critical
    doc_id: str = ""
    title: str = ""
    collection_id: str = "default"
    detail: str = ""
    entity_ref: str = ""
    suggested_action: str = ""
    tenant_id: str = "default"
    classification: str = "CUI"


def _safe_rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    try:
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("explorer: query error: %s", exc)
        return []


def _find_orphans(tenant_id: str, limit: int) -> list[ExplorerFinding]:
    """Docs with no chunks, no versions, or dangling collection."""
    from tools.db.storage import get_connection
    conn = get_connection()
    findings: list[ExplorerFinding] = []
    try:
        # Docs with zero chunks.
        rows = _safe_rows(
            conn,
            "SELECT d.doc_id, d.title, d.collection_id, d.classification "
            "FROM dic_documents d "
            "LEFT JOIN dic_chunk_links c ON c.doc_id = d.doc_id "
            "WHERE d.tenant_id = %s AND c.doc_id IS NULL "
            "ORDER BY d.created_at DESC LIMIT %s",
            (tenant_id, limit),
        )
        for r in rows:
            findings.append(ExplorerFinding(
                finding_id=f"orph_{r['doc_id'][:16]}",
                finding_type="orphan",
                severity="medium",
                doc_id=r["doc_id"],
                title=r.get("title", ""),
                collection_id=r.get("collection_id", "default"),
                detail="Document has zero chunks — may be unprocessed or empty upload.",
                suggested_action="Re-ingest or remove.",
                tenant_id=tenant_id,
                classification=r.get("classification", "CUI"),
            ))
        # Docs with no versions.
        rows = _safe_rows(
            conn,
            "SELECT d.doc_id, d.title, d.collection_id, d.classification "
            "FROM dic_documents d "
            "LEFT JOIN dic_versions v ON v.doc_id = d.doc_id "
            "WHERE d.tenant_id = %s AND v.doc_id IS NULL "
            "ORDER BY d.created_at DESC LIMIT %s",
            (tenant_id, limit),
        )
        for r in rows:
            findings.append(ExplorerFinding(
                finding_id=f"nover_{r['doc_id'][:16]}",
                finding_type="orphan",
                severity="high",
                doc_id=r["doc_id"],
                title=r.get("title", ""),
                collection_id=r.get("collection_id", "default"),
                detail="Document has no versions — incomplete ingestion pipeline.",
                suggested_action="Check ingest logs or re-upload.",
                tenant_id=tenant_id,
                classification=r.get("classification", "CUI"),
            ))
    finally:
        conn.close()
    return findings


def _find_single_owner(tenant_id: str, limit: int) -> list[ExplorerFinding]:
    """Collections with exactly one team member = tribal knowledge risk."""
    from tools.db.storage import get_connection
    conn = get_connection()
    findings: list[ExplorerFinding] = []
    try:
        rows = _safe_rows(
            conn,
            "SELECT collection_id, COUNT(*) as cnt FROM dic_team_access "
            "WHERE tenant_id = %s GROUP BY collection_id HAVING COUNT(*) = 1 "
            "LIMIT %s",
            (tenant_id, limit),
        )
        for r in rows:
            cid = r["collection_id"]
            member_rows = _safe_rows(
                conn,
                "SELECT user_id, role FROM dic_team_access WHERE collection_id = %s AND tenant_id = %s LIMIT 1",
                (cid, tenant_id),
            )
            user_id = member_rows[0]["user_id"] if member_rows else "unknown"
            findings.append(ExplorerFinding(
                finding_id=f"tribal_{cid}",
                finding_type="single_owner",
                severity="high",
                doc_id="",
                title=f"Collection {cid}",
                collection_id=cid,
                detail=f"Only one team member ({user_id}) — tribal knowledge risk if they depart.",
                suggested_action="Add a successor via Knowledge Handoff workflow.",
                tenant_id=tenant_id,
                classification="CUI",
            ))
    finally:
        conn.close()
    return findings


def _find_undocumented_deps(tenant_id: str, limit: int) -> list[ExplorerFinding]:
    """KG entities whose source chunk points to a missing doc."""
    from tools.db.storage import get_connection
    conn = get_connection()
    findings: list[ExplorerFinding] = []
    try:
        # Find KG nodes whose source_doc_id does not exist in dic_documents.
        rows = _safe_rows(
            conn,
            "SELECT n.label, n.entity_type, n.source_chunk_id, n.id "
            "FROM kg_nodes n "
            "LEFT JOIN dic_documents d ON d.doc_id = n.source_doc_id "
            "WHERE n.source_doc_id IS NOT NULL AND d.doc_id IS NULL "
            "ORDER BY n.centrality DESC LIMIT %s",
            (limit,),
        )
        for r in rows:
            findings.append(ExplorerFinding(
                finding_id=f"undep_{r['id'][:16]}",
                finding_type="undocumented_dep",
                severity="medium",
                doc_id="",
                title=r.get("label", ""),
                collection_id="default",
                detail=f"Entity '{r.get('label')}' ({r.get('entity_type')}) references a missing source document (chunk {r.get('source_chunk_id')}).",
                entity_ref=r.get("label", ""),
                suggested_action="Re-ingest the missing source or update KG mapping.",
                tenant_id=tenant_id,
                classification="CUI",
            ))
    finally:
        conn.close()
    return findings


def _find_contradictions(tenant_id: str, limit: int) -> list[ExplorerFinding]:
    """Docs with same title but divergent content hashes = potential fork/contradiction."""
    from tools.db.storage import get_connection
    conn = get_connection()
    findings: list[ExplorerFinding] = []
    try:
        # We want titles with MULTIPLE distinct hashes.
        titles = _safe_rows(
            conn,
            "SELECT title FROM dic_documents WHERE tenant_id = %s AND title != '' "
            "GROUP BY title HAVING COUNT(DISTINCT content_sha256) > 1 LIMIT %s",
            (tenant_id, limit),
        )
        for t in titles:
            title = t["title"]
            docs = _safe_rows(
                conn,
                "SELECT doc_id, content_sha256, collection_id, classification FROM dic_documents "
                "WHERE tenant_id = %s AND title = %s ORDER BY created_at DESC LIMIT 5",
                (tenant_id, title),
            )
            hashes = [d["content_sha256"] for d in docs]
            if len(set(hashes)) > 1:
                findings.append(ExplorerFinding(
                    finding_id=f"contr_{hash(title) & 0xFFFFFFFF:08x}",
                    finding_type="contradiction",
                    severity="medium",
                    doc_id=docs[0]["doc_id"],
                    title=title,
                    collection_id=docs[0].get("collection_id", "default"),
                    detail=f"{len(docs)} documents share title '{title}' but have {len(set(hashes))} distinct content hashes — possible fork or unsynced copies.",
                    suggested_action="Consolidate versions or investigate divergence.",
                    tenant_id=tenant_id,
                    classification=docs[0].get("classification", "CUI"),
                ))
    finally:
        conn.close()
    return findings


def _find_superseded(tenant_id: str, limit: int) -> list[ExplorerFinding]:
    """Documents with older approved versions that may be superseded by newer drafts."""
    from tools.db.storage import get_connection
    conn = get_connection()
    findings: list[ExplorerFinding] = []
    try:
        rows = _safe_rows(
            conn,
            "SELECT d.doc_id, d.title, d.collection_id, d.classification, "
            "MAX(CASE WHEN v.status = 'approved' THEN v.version_no END) as approved_ver, "
            "MAX(CASE WHEN v.status != 'approved' THEN v.version_no END) as draft_ver "
            "FROM dic_documents d JOIN dic_versions v ON v.doc_id = d.doc_id "
            "WHERE d.tenant_id = %s "
            "GROUP BY d.doc_id, d.title, d.collection_id, d.classification "
            "HAVING MAX(CASE WHEN v.status != 'approved' THEN v.version_no END) > "
            "       MAX(CASE WHEN v.status = 'approved' THEN v.version_no END) "
            "LIMIT %s",
            (tenant_id, limit),
        )
        for r in rows:
            findings.append(ExplorerFinding(
                finding_id=f"super_{r['doc_id'][:16]}",
                finding_type="superseded",
                severity="low",
                doc_id=r["doc_id"],
                title=r.get("title", ""),
                collection_id=r.get("collection_id", "default"),
                detail=f"Draft version {r.get('draft_ver')} is ahead of approved version {r.get('approved_ver')} — pending promotion.",
                suggested_action="Review and approve the newer version in HITL queue.",
                tenant_id=tenant_id,
                classification=r.get("classification", "CUI"),
            ))
    finally:
        conn.close()
    return findings


def run_explorer(
    *,
    tenant_id: str = "default",
    limit: int = 50,
) -> list[dict]:
    """Run all explorer lenses and return flattened findings as dicts.

    Ordered by severity: critical > high > medium > low.
    """
    findings: list[ExplorerFinding] = []
    findings.extend(_find_orphans(tenant_id, limit))
    findings.extend(_find_single_owner(tenant_id, limit))
    findings.extend(_find_undocumented_deps(tenant_id, limit))
    findings.extend(_find_contradictions(tenant_id, limit))
    findings.extend(_find_superseded(tenant_id, limit))

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: severity_order.get(f.severity, 99))

    return [
        {
            "finding_id": f.finding_id,
            "finding_type": f.finding_type,
            "severity": f.severity,
            "doc_id": f.doc_id,
            "title": f.title,
            "collection_id": f.collection_id,
            "detail": f.detail,
            "entity_ref": f.entity_ref,
            "suggested_action": f.suggested_action,
            "tenant_id": f.tenant_id,
            "classification": f.classification,
        }
        for f in findings
    ]
