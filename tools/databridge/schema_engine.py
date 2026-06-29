# CUI // SP-CTI
"""DataBridge Schema Engine — schema inference storage and lookup.

Stores inferred table schemas in icdev.db so that downstream
consumers (dashboards, mapping, data quality) can reference column
metadata without re-connecting to the source.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import sqlite3
import uuid
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = get_logger("databridge.schema_engine")

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "icdev.db"


def register_schema(
    connection_id: str,
    table_name: str,
    schema: Any,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Store or update an inferred schema for a connection + table.

    ``schema`` may be a ``SchemaDefinition`` dataclass or a plain dict.
    Stores as JSON in the ``db_inferred_schemas`` table (upsert by
    connection_id + table_name).

    Returns a status dict.
    """
    db = db_path or str(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()

    # Normalise schema to a JSON-serialisable dict
    if hasattr(schema, "__dataclass_fields__"):
        from dataclasses import asdict

        schema_dict = asdict(schema)
    elif isinstance(schema, dict):
        schema_dict = schema
    else:
        schema_dict = {"raw": str(schema)}

    schema_json = json.dumps(schema_dict, default=str)

    try:
        conn = _get_conn(db)
        _ensure_table(conn)

        # Upsert: update if exists, insert otherwise
        existing = conn.execute(
            "SELECT id FROM db_inferred_schemas WHERE connection_id = %s AND table_name = %s",
            (connection_id, table_name),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE db_inferred_schemas SET schema_json = %s, updated_at = %s "
                "WHERE connection_id = %s AND table_name = %s",
                (schema_json, now, connection_id, table_name),
            )
        else:
            schema_id = f"schema-{uuid.uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO db_inferred_schemas "
                "(id, connection_id, table_name, schema_json, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (schema_id, connection_id, table_name, schema_json, now, now),
            )

        conn.commit()
        conn.close()
        return {"status": "ok", "connection_id": connection_id, "table_name": table_name}

    except Exception as exc:
        logger.error("register_schema(%s, %s) failed: %s", connection_id, table_name, exc)
        return {"status": "error", "error": str(exc)}


def get_schema(
    connection_id: str,
    table_name: str,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve a stored schema for a connection + table.

    Returns the parsed JSON dict or None if not found.
    """
    db = db_path or str(DB_PATH)
    try:
        conn = _get_conn(db)
        _ensure_table(conn)
        row = conn.execute(
            "SELECT schema_json FROM db_inferred_schemas WHERE connection_id = %s AND table_name = %s",
            (connection_id, table_name),
        ).fetchone()
        conn.close()
        if row:
            return json.loads(row["schema_json"])
        return None
    except Exception as exc:
        logger.error("get_schema(%s, %s) failed: %s", connection_id, table_name, exc)
        return None


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create the inferred schemas table if it does not exist."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS db_inferred_schemas (
            id TEXT PRIMARY KEY,
            connection_id TEXT NOT NULL,
            table_name TEXT NOT NULL,
            schema_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(connection_id, table_name)
        )"""
    )


def _get_conn(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with WAL and row_factory."""
    conn = get_connection(db_path=str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
