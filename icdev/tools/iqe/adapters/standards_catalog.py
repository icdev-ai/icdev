# CUI // SP-CTI
"""IQE Standards Catalog collection adapter.

Registers 2 collections on the module-level Executor:
  standards_catalog.entries — merged curated entries (generic store; adapter
                              entries from the network canvas carry their own
                              provenance in the docmod catalog layer)
  standards_catalog.audit   — append-only curation audit trail
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _catalog_conn(conn: Any) -> tuple[Any, bool]:
    if conn is not None:
        return conn, False
    from tools.db.storage import get_connection  # noqa: PLC0415
    return get_connection(), True


def entries_adapter(conn: Any) -> list[dict]:
    """Central-store catalog entries, newest first."""
    c, owned = _catalog_conn(conn)
    try:
        cur = c.execute(
            "SELECT entry_id, domain, category, vendor, product, model_family, "
            "version, status, eol_date, eos_date, replacement_entry_id, source, "
            "is_builtin, created_by, created_at, updated_at "
            "FROM docmod_catalog_entries ORDER BY updated_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def audit_adapter(conn: Any) -> list[dict]:
    """Curation audit rows, newest first."""
    c, owned = _catalog_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, entry_id, event_type, actor, details, recorded_at "
            "FROM docmod_catalog_audit ORDER BY recorded_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


register_collection("standards_catalog.entries", entries_adapter)
register_collection("standards_catalog.audit", audit_adapter)
