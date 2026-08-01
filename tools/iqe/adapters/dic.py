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


def _dic_graph_ids_for_iqe(c) -> list[str]:
    """Graph IDs scoped to DIC-ingested documents only."""
    try:
        cur = c.execute(
            "SELECT g.id FROM kg_graphs g "
            "INNER JOIN dic_documents d ON d.doc_id = g.source_doc_id "
            "WHERE g.source_doc_id IS NOT NULL"
        )
        return [row[0] for row in cur.fetchall()]
    except Exception:
        return []


def kg_entities_adapter(conn: Any) -> list[dict]:
    """KG nodes (entities) extracted from ingested DIC documents only."""
    c = _conn(conn)
    try:
        gids = _dic_graph_ids_for_iqe(c)
        if not gids:
            return []
        ph = ",".join(["?" for _ in gids])
        cur = c.execute(
            f"SELECT n.id, n.label, n.entity_type, n.centrality, "
            f"n.source_chunk_id, n.created_at, g.source_doc_id "
            f"FROM kg_nodes n LEFT JOIN kg_graphs g ON g.id = n.graph_id "
            f"WHERE n.graph_id IN ({ph}) "
            f"ORDER BY n.centrality DESC LIMIT 1000",
            tuple(gids),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def kg_relationships_adapter(conn: Any) -> list[dict]:
    """KG edges (relationships) between entities from DIC documents only."""
    c = _conn(conn)
    try:
        gids = _dic_graph_ids_for_iqe(c)
        if not gids:
            return []
        ph = ",".join(["?" for _ in gids])
        cur = c.execute(
            f"SELECT e.id, src.label AS source, tgt.label AS target, "
            f"e.relationship, e.weight, e.created_at "
            f"FROM kg_edges e "
            f"JOIN kg_nodes src ON src.id = e.source_id "
            f"JOIN kg_nodes tgt ON tgt.id = e.target_id "
            f"WHERE src.graph_id IN ({ph}) "
            f"ORDER BY e.weight DESC LIMIT 1000",
            tuple(gids),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def doc_freshness_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT doc_id, collection_id, state, reason, score, updated_at, tenant_id "
            "FROM dic_doc_freshness ORDER BY score DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def freshness_scans_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT scan_id, collection_id, stale_count, regen_priority, scanned_at, tenant_id "
            "FROM dic_freshness_scans ORDER BY scanned_at DESC LIMIT 200"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def handoff_sessions_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT session_id, departing_owner_id, successor_owner_id, dest_collection_id, "
            "title, status, agenda_count, answered_count, generated_count, orphan_count, "
            "created_at, tenant_id FROM dic_handoff_sessions ORDER BY created_at DESC LIMIT 200"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def handoff_items_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT item_id, session_id, item_kind, topic, prompt, answer_text, doc_id, "
            "version_id, verified, abstained, status, created_at, tenant_id "
            "FROM dic_handoff_items ORDER BY created_at DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def generated_outputs_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, output_type, collection_id, provider, status, audio_path, "
            "created_at, tenant_id, classification FROM dic_generated_outputs "
            "ORDER BY created_at DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def techwriter_docs_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT doc_id, title, template_type, writeguard_mode, created_at, tenant_id, classification "
            "FROM dic_documents WHERE template_type IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 500"
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
register_collection("dic.doc_freshness", doc_freshness_adapter)
register_collection("dic.freshness_scans", freshness_scans_adapter)
register_collection("dic.handoff_sessions", handoff_sessions_adapter)
register_collection("dic.handoff_items", handoff_items_adapter)
register_collection("dic.generated_outputs", generated_outputs_adapter)
register_collection("dic.techwriter_docs", techwriter_docs_adapter)


def modernization_findings_adapter(conn: Any) -> list[dict]:
    """Latest-state docmod findings (append-only supersede chains resolved)."""
    try:
        from tools.doc_modernization import get_findings
        return get_findings(conn=conn)
    except Exception:
        return []


register_collection("dic.modernization_findings", modernization_findings_adapter)
