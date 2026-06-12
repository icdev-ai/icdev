# CUI // SP-CTI
"""IQE ontology collection adapters.

Registers three collections:
  ontology.classes   — All ontology classes with domain and hierarchy
  ontology.closure   — Transitive subclass closure pairs
  ontology.alignments — Cross-domain equivalence/subclass alignments
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def classes_adapter(conn: Any) -> list[dict]:
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, domain, label, superclasses, canonical_id FROM ontology_classes ORDER BY domain, label"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def closure_adapter(conn: Any) -> list[dict]:
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT subclass, superclass, distance FROM ontology_subclass_closure ORDER BY distance, subclass"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def alignments_adapter(conn: Any) -> list[dict]:
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT source, target, assertion FROM ontology_alignments"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


register_collection("ontology.classes", classes_adapter)
register_collection("ontology.closure", closure_adapter)
register_collection("ontology.alignments", alignments_adapter)
