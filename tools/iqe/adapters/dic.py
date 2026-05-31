# CUI // SP-CTI
"""IQE Document Intelligence Canvas collection adapters.

Registers five collections on the module-level Executor:
  dic.documents     — ingested documents (dic_documents)
  dic.collections   — user-created collections (dic_collections)
  dic.drift_events  — canvas drift events (dic_drift_events)
  dic.regen_queue   — regeneration queue items (dic_acoic_regen_queue)
  dic.ssp_fragments — drafted SSP fragments (dic_ssp_fragments)
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    return conn


def documents_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT doc_id, collection_id, title, content_type, created_at, "
            "classification, tenant_id FROM dic_documents ORDER BY created_at DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def collections_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT collection_id, name, description, owner_id, classification, "
            "tenant_id, created_at FROM dic_collections ORDER BY created_at DESC LIMIT 200"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def drift_events_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT event_id, source, entity, severity, detected_at, processed "
            "FROM dic_drift_events ORDER BY detected_at DESC LIMIT 200"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def regen_queue_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT item_id, document_id, event_id, impact_level, state, queued_at, updated_at "
            "FROM dic_acoic_regen_queue ORDER BY queued_at DESC LIMIT 200"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def ssp_fragments_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT fragment_id, control_id, document_id, status, reviewed_by, created_at "
            "FROM dic_ssp_fragments ORDER BY created_at DESC LIMIT 200"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def kg_entities_adapter(conn: Any) -> list[dict]:
    """KG nodes (entities) extracted from ingested DIC documents."""
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT n.id, n.label, n.entity_type, n.centrality, "
            "n.source_chunk_id, n.created_at, g.source_doc_id "
            "FROM kg_nodes n LEFT JOIN kg_graphs g ON g.id = n.graph_id "
            "ORDER BY n.centrality DESC NULLS LAST LIMIT 1000"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def kg_relationships_adapter(conn: Any) -> list[dict]:
    """KG edges (relationships) between entities extracted from DIC documents."""
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT e.id, src.label AS source, tgt.label AS target, "
            "e.relationship, e.weight, e.created_at "
            "FROM kg_edges e "
            "JOIN kg_nodes src ON src.id = e.source_id "
            "JOIN kg_nodes tgt ON tgt.id = e.target_id "
            "ORDER BY e.weight DESC NULLS LAST LIMIT 1000"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


register_collection("dic.documents", documents_adapter)
register_collection("dic.collections", collections_adapter)
register_collection("dic.drift_events", drift_events_adapter)
register_collection("dic.regen_queue", regen_queue_adapter)
register_collection("dic.ssp_fragments", ssp_fragments_adapter)
register_collection("dic.kg_entities", kg_entities_adapter)
register_collection("dic.kg_relationships", kg_relationships_adapter)
