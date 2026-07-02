# CUI // SP-CTI
"""IQE adapter for the IDR Doc Regeneration canvas.

Collections exposed:
    docgen.sessions     — idr_sessions
    docgen.uploads      — idr_uploads
    docgen.conflicts    — idr_conflicts (all — resolved and pending)
    docgen.analyses     — idr_analyses
    docgen.artifacts    — idr_artifacts
"""
from __future__ import annotations

from typing import Any

from tools.db.storage import get_connection


def get_collections() -> list[str]:
    return [
        "docgen.sessions",
        "docgen.uploads",
        "docgen.conflicts",
        "docgen.analyses",
        "docgen.artifacts",
    ]


def search(collection: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Simple keyword search for IQE queries."""
    q = f"%{query.lower()}%"
    with get_connection() as conn:
        if collection == "docgen.sessions":
            rows = conn.execute(
                "SELECT * FROM idr_sessions WHERE lower(title) LIKE %s OR lower(status) LIKE %s OR lower(domain) LIKE %s LIMIT %s",
                (q, q, q, limit),
            ).fetchall()
        elif collection == "docgen.uploads":
            rows = conn.execute(
                "SELECT * FROM idr_uploads WHERE lower(filename) LIKE %s OR lower(upload_type) LIKE %s LIMIT %s",
                (q, q, limit),
            ).fetchall()
        elif collection == "docgen.conflicts":
            rows = conn.execute(
                "SELECT * FROM idr_conflicts WHERE lower(node_label) LIKE %s OR lower(conflict_type) LIKE %s LIMIT %s",
                (q, q, limit),
            ).fetchall()
        elif collection == "docgen.analyses":
            rows = conn.execute(
                "SELECT * FROM idr_analyses WHERE lower(analysis_type) LIKE %s LIMIT %s",
                (q, limit),
            ).fetchall()
        elif collection == "docgen.artifacts":
            rows = conn.execute(
                "SELECT * FROM idr_artifacts WHERE lower(format) LIKE %s LIMIT %s",
                (q, limit),
            ).fetchall()
        else:
            return []
    return [dict(r) for r in rows]


def handle_iqe_query(query: str) -> dict[str, Any]:
    """Main IQE entry point — searches across all collections."""
    results: list[dict[str, Any]] = []
    for col in get_collections():
        try:
            hits = search(col, query, limit=10)
            for h in hits:
                results.append({"collection": col, "record": h})
        except Exception:
            pass
    return {
        "query": query,
        "total": len(results),
        "results": results[:30],
        "collections_searched": get_collections(),
    }
