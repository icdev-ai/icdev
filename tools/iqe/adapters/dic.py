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


register_collection("dic.documents", documents_adapter)
register_collection("dic.collections", collections_adapter)
register_collection("dic.drift_events", drift_events_adapter)
register_collection("dic.regen_queue", regen_queue_adapter)
register_collection("dic.ssp_fragments", ssp_fragments_adapter)
