# CUI // SP-CTI
"""DataBridge Connection Manager — CRUD for stored connection configs.

Provides helpers to look up, create, and update connection records in the
``db_connections`` table of icdev.db.  Also resolves secret references for
auth credentials.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("databridge.connection_manager")

# ---------------------------------------------------------------------------
# Secret resolver registry
# ---------------------------------------------------------------------------

try:
    from tools.databridge.resolvers.vault_resolver import (
        SecretResolverError,
        resolve as _vault_resolve,
    )

    SECRET_RESOLVERS: Dict[str, Callable[[str], str]] = {
        "vault": _vault_resolve,
    }
except ImportError:
    # hvac / cachetools not installed; vault backend unavailable
    class SecretResolverError(Exception):  # type: ignore[no-redef]
        """Raised when a secret reference cannot be resolved."""

    SECRET_RESOLVERS: Dict[str, Callable[[str], str]] = {}  # type: ignore[no-redef]

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "icdev.db"


def get_connection(
    connection_id: str,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch a connection record by ID.

    Returns a dict with at least ``id``, ``connector_name``,
    ``config_yaml``, ``auth_secret_ref``, ``status`` keys, or None
    if not found.
    """
    db = db_path or str(DB_PATH)
    try:
        conn = _get_conn(db)
        row = conn.execute("SELECT * FROM db_connections WHERE id = ?", (connection_id,)).fetchone()
        conn.close()
        if row:
            return dict(row)
        return None
    except Exception as exc:
        logger.error("get_connection(%s) failed: %s", connection_id, exc)
        return None


def update_connection(
    connection_id: str,
    updates: Dict[str, Any],
    db_path: Optional[str] = None,
) -> bool:
    """Update fields on an existing connection record.

    ``updates`` is a dict of column_name -> value.  Only known safe
    columns are written.
    """
    db = db_path or str(DB_PATH)
    allowed = {"status", "last_sync", "config_yaml", "auth_secret_ref", "name"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in filtered)
    values = list(filtered.values()) + [connection_id]
    try:
        conn = _get_conn(db)
        conn.execute(
            f"UPDATE db_connections SET {set_clause} WHERE id = ?",  # nosec B608 -- column names validated against allowlist above
            values,
        )
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        logger.error("update_connection(%s) failed: %s", connection_id, exc)
        return False


def resolve_secret(auth_secret_ref: str) -> Optional[str]:
    """Resolve a secret reference to its plaintext value.

    Supports:
      - ``env:VAR_NAME``         -- reads from environment variable
      - ``vault:path#field``     -- HashiCorp Vault KV lookup (secret_backend=vault)
      - plain string             -- returned as-is (for dev/testing only)

    Raises:
        SecretResolverError: when a backend-specific resolver fails hard
                             (e.g. Vault connection error, missing field).
    """
    if not auth_secret_ref:
        return None

    if auth_secret_ref.startswith("env:"):
        var_name = auth_secret_ref[4:]
        value = os.environ.get(var_name)
        if value is None:
            logger.warning("Secret env var '%s' not set", var_name)
        return value

    # Dispatch to registered backend resolvers (e.g. secret_backend=vault)
    for prefix, resolver in SECRET_RESOLVERS.items():
        if auth_secret_ref.startswith(f"{prefix}:"):
            return resolver(auth_secret_ref)

    # Fallback: treat the ref as a literal value (dev only)
    return auth_secret_ref


def _get_conn(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with WAL and row_factory."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
