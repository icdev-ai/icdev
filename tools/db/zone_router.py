# CUI // SP-CTI
"""Per-tenant PostgreSQL connection router by data residency zone (ECR-DRES-02).

Routes connections to zone-specific PG instances based on the tenant's assignment
in tenant_zone_assignments.  Falls back to the default connection when no
assignment exists, the zone DSN env var is unset, or the zone PG is unreachable.

Connections are cached in Flask g for the duration of the request (one
connection per zone per request) to avoid per-query reconnects.  A teardown
handler closes all zone connections at request end; the handler is registered
once per Flask app instance to avoid accumulation.
"""
from __future__ import annotations

import os
from typing import Optional

_teardown_registered_on_apps: set[int] = set()


def get_zone_connection(tenant_id: str):
    """Return a StorageConnection routed to the tenant's data residency zone.

    Resolution order:
      1. Flask g cache hit on ``g._zone_connections[tenant_id]`` → return cached.
      2. Look up tenant_zone_assignments JOIN data_residency_zones for tenant_id.
      3. Read the zone DSN from the env var named by ``pg_dsn_env``.
      4. Open a direct connection to the zone PG instance.
      5. On any failure, fall back to the default connection.

    The result is cached in ``g._zone_connections`` and a close-on-teardown
    handler is registered on the Flask app (once per app instance).

    Args:
        tenant_id: Tenant identifier for zone lookup.

    Returns:
        A StorageConnection (zone-specific or default fallback).
    """
    _g = _get_flask_g()

    if _g is not None:
        cache = getattr(_g, "_zone_connections", None)
        if cache is not None and tenant_id in cache:
            return cache[tenant_id]

    conn = _build_zone_or_default_connection(tenant_id)

    if _g is not None:
        if not hasattr(_g, "_zone_connections"):
            _g._zone_connections = {}
            _ensure_teardown_registered()
        _g._zone_connections[tenant_id] = conn

    return conn


def _get_flask_g():
    """Return Flask g if inside an active request context, else None."""
    try:
        from flask import g, has_request_context
        if has_request_context():
            return g
    except (ImportError, RuntimeError):
        pass
    return None


def _resolve_tenant_zone_dsn(tenant_id: str) -> Optional[str]:
    """Return the zone DSN URL for a tenant, or None.

    Queries tenant_zone_assignments JOIN data_residency_zones using a direct
    psycopg2 connection (bypasses zone routing to avoid recursion).  Returns
    the *value* of the pg_dsn_env env var (i.e. the actual DSN string), not
    the variable name.  Returns None for non-PG backends or any error.
    """
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower()
    if backend != "postgresql":
        return None

    try:
        import psycopg2
        import psycopg2.extras
        from tools.db.storage import _pg_ssl_kwargs, _pg_session_options

        db_url = os.environ.get("ICDEV_DATABASE_URL")
        ssl_kwargs = _pg_ssl_kwargs()
        pg_timeout = int(os.environ.get("ICDEV_PG_CONNECT_TIMEOUT", "10"))
        pg_options = _pg_session_options()

        if db_url:
            raw = psycopg2.connect(
                db_url,
                connect_timeout=pg_timeout,
                options=pg_options,
                cursor_factory=psycopg2.extras.RealDictCursor,
                **ssl_kwargs,
            )
        else:
            raw = psycopg2.connect(
                host=os.environ.get("ICDEV_PG_HOST", "localhost"),
                port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
                user=os.environ.get("ICDEV_PG_USER", "icdev"),
                password=os.environ.get("ICDEV_PG_PASSWORD", "icdev_dev_2026"),
                dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
                connect_timeout=pg_timeout,
                options=pg_options,
                cursor_factory=psycopg2.extras.RealDictCursor,
            )
        try:
            with raw.cursor() as cur:
                cur.execute(
                    "SELECT z.pg_dsn_env"
                    " FROM tenant_zone_assignments a"
                    " JOIN data_residency_zones z ON z.id = a.zone_id"
                    " WHERE a.tenant_id = %s",
                    (tenant_id,),
                )
                row = cur.fetchone()
        finally:
            raw.close()

        if row:
            pg_dsn_env = row["pg_dsn_env"]
            return os.environ.get(pg_dsn_env)

    except Exception as exc:
        from tools.logging.icdev_logger import get_logger
        get_logger(__name__).debug(
            "zone_router: could not resolve zone for tenant %r: %s", tenant_id, exc
        )
    return None


def _build_zone_or_default_connection(tenant_id: str):
    """Build a zone-specific or default StorageConnection for a tenant."""
    from tools.db.storage import (
        StorageConnection,
        _get_sqlite_connection,
        _attach_flask_security_context,
    )
    from tools.logging.icdev_logger import get_logger

    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower()

    if backend == "postgresql":
        zone_db_url = _resolve_tenant_zone_dsn(tenant_id)
        if zone_db_url:
            try:
                raw = _open_zone_pg(zone_db_url)
                conn = StorageConnection(raw, "postgresql")
                _attach_flask_security_context(conn)
                return conn
            except Exception as exc:
                get_logger(__name__).warning(
                    "zone_router: zone PG unreachable for tenant %r (%s), using default",
                    tenant_id,
                    exc,
                )
        # Default PG path
        try:
            from tools.db.storage import _get_pg_connection
            raw = _get_pg_connection()
            conn = StorageConnection(raw, "postgresql")
            _attach_flask_security_context(conn)
            return conn
        except Exception as exc:
            get_logger(__name__).warning(
                "zone_router: default PG unavailable (%s), falling back to SQLite", exc
            )

    # SQLite fallback (used in tests and air-gap mode)
    raw = _get_sqlite_connection()
    conn = StorageConnection(raw, "sqlite")
    _attach_flask_security_context(conn)
    return conn


def _open_zone_pg(zone_db_url: str):
    """Open a direct psycopg2 connection to a zone-specific PG instance.

    Uses a direct connection (not the shared pool) because the pool is bound
    to the default DSN; zone DSNs require their own connections.
    """
    import psycopg2
    import psycopg2.extras
    from tools.db.storage import _pg_ssl_kwargs, _pg_session_options

    ssl_kwargs = _pg_ssl_kwargs()
    pg_timeout = int(os.environ.get("ICDEV_PG_CONNECT_TIMEOUT", "10"))
    pg_options = _pg_session_options()

    conn = psycopg2.connect(
        zone_db_url,
        connect_timeout=pg_timeout,
        options=pg_options,
        cursor_factory=psycopg2.extras.RealDictCursor,
        **ssl_kwargs,
    )
    conn.autocommit = False
    return conn


def _ensure_teardown_registered() -> None:
    """Register a close-on-teardown handler on the current Flask app.

    Guards against registering the same handler multiple times by keying on
    the app's object id.  The handler iterates ``g._zone_connections`` and
    closes each cached zone connection so the PG server-side sessions are
    released promptly rather than waiting for GC.
    """
    try:
        from flask import current_app

        app = current_app._get_current_object()
        app_id = id(app)
        if app_id in _teardown_registered_on_apps:
            return
        _teardown_registered_on_apps.add(app_id)

        @app.teardown_appcontext
        def _close_zone_connections(exc=None):
            try:
                from flask import g as _g
                conns = getattr(_g, "_zone_connections", {})
                for conn in conns.values():
                    try:
                        conn.close()
                    except Exception:
                        pass
                if conns:
                    _g._zone_connections = {}
            except Exception:
                pass

    except Exception:
        pass
