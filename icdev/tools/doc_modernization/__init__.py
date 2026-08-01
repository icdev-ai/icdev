# CUI // SP-CTI
"""Document Modernization Engine (docmod).

Generic, domain-agnostic staleness intelligence for DIC documents: pluggable
domain packs detect EOL hardware/software, deprecated technology, and
superseded standards; verdicts are deterministic (TRUST); redlines and
regeneration are HITL-gated elsewhere (redline_drafter / regen_orchestrator).

Public API:
    scan_document(doc_id)            — scan one document
    scan_collection(collection_id)   — scan a collection (None = whole corpus)
    get_findings(doc_id=..., ...)    — latest-state findings (supersede chains resolved)
"""
from __future__ import annotations

from .scanner import scan_collection, scan_document  # noqa: F401


def get_findings(doc_id: str | None = None, state: str | None = None,
                 finding_type: str | None = None, conn=None) -> list[dict]:
    """Latest-state findings. The docmod_findings table is append-only —
    per dedupe_key, the newest row is the current state; older rows are history.
    Always read findings through this helper."""
    from tools.db.storage import get_connection

    own = conn is None
    if own:
        conn = get_connection()
    try:
        sql = "SELECT * FROM docmod_findings WHERE 1=1"
        params: list = []
        if doc_id:
            sql += " AND doc_id = %s"
            params.append(doc_id)
        if finding_type:
            sql += " AND finding_type = %s"
            params.append(finding_type)
        sql += " ORDER BY created_at"
        rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    finally:
        if own:
            conn.close()

    latest: dict[str, dict] = {}
    for r in rows:  # ascending — later rows supersede earlier ones per chain
        key = r.get("dedupe_key") or r["finding_id"]
        latest[key] = r
    out = list(latest.values())
    if state:
        out = [r for r in out if r.get("state") == state]
    return out
