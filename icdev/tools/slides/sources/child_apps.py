# CUI // SP-CTI
"""Source connector: Child apps and showcase apps.

Reads from the showcase_apps table (or static catalog fallback)
to produce a summary of ICDEV-generated child applications.
"""
from __future__ import annotations

from typing import Any

_STATIC_FALLBACK: list[dict] = [
    {"name": "ThreatScope", "category": "cybersecurity", "description": "AI-driven CVE triage and blast radius analysis"},
    {"name": "ATO Express", "category": "compliance", "description": "Rapid ATO artifact generation with eMASS sync"},
    {"name": "PropGen", "category": "govcon", "description": "AI-powered proposal generation from SAM.gov solicitations"},
    {"name": "NetGuard", "category": "network", "description": "Network topology drift detection and Ansible auto-remediation"},
    {"name": "DocIntel", "category": "document_intelligence", "description": "Enterprise RAG+KG document search with citation grounding"},
]


def gather(max_apps: int = 10) -> dict[str, Any]:
    """Return child apps summary for slide generation."""
    apps: list[dict] = []
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT name, category, description, status FROM showcase_apps "
                "WHERE status = 'published' ORDER BY created_at DESC LIMIT %s",
                (max_apps,),
            ).fetchall()
            for row in rows:
                apps.append({
                    "name": row["name"] if hasattr(row, "__getitem__") else row[0],
                    "category": row["category"] if hasattr(row, "__getitem__") else row[1],
                    "description": row["description"] if hasattr(row, "__getitem__") else row[2],
                })
        finally:
            conn.close()
    except Exception:
        pass

    if not apps:
        apps = _STATIC_FALLBACK[:max_apps]

    categories = list({a["category"] for a in apps})

    return {
        "source": "child_apps",
        "total_apps": len(apps),
        "categories": categories,
        "apps": apps,
        "summary": (
            f"ICDEV™ has generated {len(apps)} showcase applications across "
            f"{len(categories)} categories: {', '.join(categories)}."
        ),
    }
