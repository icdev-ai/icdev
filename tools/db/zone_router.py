# CUI // SP-CTI
"""Per-tenant PostgreSQL connection router by data residency zone (ecr-dres-02).

Usage:
    from tools.db.zone_router import get_zone_connection, teardown_zone_connections

    conn = get_zone_connection(tenant_id)   # correct zone or default fallback
    # ... use conn ...
    conn.close()

Register teardown in your Flask app factory:
    from tools.db.zone_router import teardown_zone_connections
    app.teardown_request(lambda _: teardown_zone_connections())
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

_CACHE_ATTR = "_icdev_zone_conn_cache"


# ---------------------------------------------------------------------------
# Zone lookup helpers
# ---------------------------------------------------------------------------

def _lookup_tenant_zone_sqlite(tenant_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Direct SQLite lookup — avoids importing storage.py to prevent circular import."""
    try:
        import sqlite3 as _sq
        db_path = os.environ.get("ICDEV_DB_PATH", "")
        if not db_path:
            return None, None
        conn = _sq.connect(db_path, timeout=5)
        row = conn.execute(
            "SELECT z.zone_id, z.pg_dsn_env "
            "FROM tenant_zone_assignments a "
            "JOIN data_residency_zones z ON z.zone_id = a.zone_id "
            "WHERE a.tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        conn.close()
        if row:
            return row[0], row[1]
    except Exception:
        pass
    return None, None


def _lookup_tenant_zone_pg(tenant_id: str) -> Tuple[Optional[str], Optional[str]]:
    """PostgreSQL lookup — lazy-imports storage helpers to avoid module-level circularity."""
    try:
        import psycopg2
        import psycopg2.extras
        from tools.db.storage import _pg_ssl_kwargs, _pg_session_options  # lazy, inside fn

        db_url = os.environ.get("ICDEV_DATABASE_URL")
        ssl_kwargs = _pg_ssl_kwargs()
        options = _pg_session_options()
        if db_url:
            raw = psycopg2.connect(
                db_url, connect_timeout=5, options=options,
                cursor_factory=psycopg2.extras.RealDictCursor, **ssl_kwargs,
            )
        else:
            raw = psycopg2.connect(
                host=os.environ.get("ICDEV_PG_HOST", "localhost"),
                port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
                user=os.environ.get("ICDEV_PG_USER", "icdev"),
                password=os.environ.get("ICDEV_PG_PASSWORD", "icdev_dev_2026"),
                dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
                connect_timeout=5, options=options,
                cursor_factory=psycopg2.extras.RealDictCursor,
                **ssl_kwargs,
            )
        cur = raw.cursor()
        cur.execute(
            "SELECT z.zone_id, z.pg_dsn_env "
            "FROM tenant_zone_assignments a "
            "JOIN data_residency_zones z ON z.zone_id = a.zone_id "
            "WHERE a.tenant_id = %s",
            (tenant_id,),
        )
        row = cur.fetchone()
        raw.close()
        if row:
            return row["zone_id"], row["pg_dsn_env"]
    except Exception:
        pass
    return None, None


def _lookup_tenant_zone(tenant_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (zone_id, pg_dsn_env) for the tenant, or (None, None) if unassigned."""
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower()
    if backend == "sqlite":
        return _lookup_tenant_zone_sqlite(tenant_id)
    return _lookup_tenant_zone_pg(tenant_id)


# ---------------------------------------------------------------------------
# Connection opener
# ---------------------------------------------------------------------------

def _open_zone_connection(zone_id: Optional[str], pg_dsn_env: Optional[str]):
    """Open a StorageConnection for the zone, falling back to default on any error.

    Priority:
      1. DSN from os.environ[pg_dsn_env] — the zone's dedicated PG instance.
      2. Default PG connection (_get_pg_connection) if zone DSN is unset/unreachable.
      3. SQLite fallback if both PG attempts fail.
    """
    # Lazy imports to avoid module-level circular dependency with storage.py
    from tools.db.storage import (  # noqa: PLC0415
        StorageConnection,
        _get_pg_connection,
        _get_sqlite_connection,
        get_logger,
    )
    log = get_logger(__name__)
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower()

    if backend == "postgresql" and pg_dsn_env:
        dsn = os.environ.get(pg_dsn_env)
        if dsn:
            try:
                import psycopg2
                import psycopg2.extras

                raw = psycopg2.connect(
                    dsn,
                    connect_timeout=5,
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
                raw.autocommit = False
                return StorageConnection(raw, "postgresql")
            except Exception as exc:
                log.warning(
                    "Zone %s DSN unreachable (%s), falling back to default PG",
                    zone_id,
                    exc,
                )

    # Default PG or SQLite fallback
    if backend == "postgresql":
        try:
            raw = _get_pg_connection()
            return StorageConnection(raw, "postgresql")
        except Exception as exc:
            log.warning(
                "Default PG unavailable (%s), falling back to SQLite",
                exc,
            )

    raw = _get_sqlite_connection()
    return StorageConnection(raw, "sqlite")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_zone_connection(tenant_id: str):
    """Return a StorageConnection for the tenant's data residency zone.

    Steps:
      1. Check Flask g for a cached zone connection (avoids per-query reconnects).
      2. Look up the tenant's zone via tenant_zone_assignments.
      3. Open a connection to the zone's PG instance (or fallback).
      4. Cache the connection in Flask g keyed by zone_id.

    Falls back to the default PG/SQLite connection if:
      - The tenant has no zone assignment.
      - The zone's pg_dsn_env is unset.
      - The zone's PG instance is unreachable.

    Args:
        tenant_id: The tenant identifier from g.tenant_id.

    Returns:
        A StorageConnection instance.
    """
    # Request-scoped cache via Flask g
    in_request = False
    cache: dict = {}
    try:
        from flask import g, has_request_context  # noqa: PLC0415

        if has_request_context():
            in_request = True
            existing = getattr(g, _CACHE_ATTR, None)
            if existing is None:
                existing = {}
                setattr(g, _CACHE_ATTR, existing)
            cache = existing
    except ImportError:
        pass

    zone_id, pg_dsn_env = _lookup_tenant_zone(tenant_id)
    cache_key = zone_id or "_default"

    if in_request and cache_key in cache:
        return cache[cache_key]

    conn = _open_zone_connection(zone_id, pg_dsn_env)

    if in_request:
        cache[cache_key] = conn

    return conn


def teardown_zone_connections() -> None:
    """Close and clear all zone connections cached in Flask g.

    Register this as a Flask teardown_request handler to prevent connection leaks:

        app.teardown_request(lambda _: teardown_zone_connections())
    """
    try:
        from flask import g, has_request_context  # noqa: PLC0415

        if not has_request_context():
            return
        cache = getattr(g, _CACHE_ATTR, None)
        if not cache:
            return
        for conn in list(cache.values()):
            try:
                conn.close()
            except Exception:
                pass
        cache.clear()
    except ImportError:
        pass
