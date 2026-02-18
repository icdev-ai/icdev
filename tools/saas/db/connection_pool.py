#!/usr/bin/env python3
"""ICDEV SaaS - PostgreSQL Connection Pool.
CUI // SP-CTI

Manages per-tenant PostgreSQL connection pools. Each tenant gets its own pool
to ensure connection isolation and efficient reuse.
"""

import logging
import os
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("saas.db.pool")

PLATFORM_DB_PATH = Path(
    os.environ.get("PLATFORM_DB_PATH", str(BASE_DIR / "data" / "platform.db"))
)


class TenantConnectionPool:
    """Manages connection pools for multiple tenants."""

    def __init__(self, min_conn: int = 1, max_conn: int = 10):
        self._pools: Dict[str, Any] = {}
        self._tenant_configs: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._min_conn = min_conn
        self._max_conn = max_conn

    def _load_tenant_config(self, tenant_id: str) -> Optional[dict]:
        """Load tenant DB config from platform database."""
        if tenant_id in self._tenant_configs:
            return self._tenant_configs[tenant_id]

        try:
            conn = sqlite3.connect(str(PLATFORM_DB_PATH))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT db_host, db_name, db_port, slug, impact_level "
                "FROM tenants WHERE id = ?",
                (tenant_id,),
            ).fetchone()
            conn.close()

            if not row:
                return None

            config = dict(row)
            self._tenant_configs[tenant_id] = config
            return config
        except Exception as e:
            logger.error("Failed to load tenant config for %s: %s", tenant_id, e)
            return None

    def get_connection(self, tenant_id: str) -> Any:
        """Get a database connection for a specific tenant."""
        from tools.saas.db.db_compat import get_sqlite_connection

        config = self._load_tenant_config(tenant_id)
        if not config:
            raise ValueError(f"Tenant {tenant_id} not found or not configured")

        if config.get("db_host") and config["db_host"] not in (
            "localhost-sqlite", "", None,
        ):
            return self._get_pg_connection(tenant_id, config)

        slug = config.get("slug", tenant_id)
        db_path = BASE_DIR / "data" / "tenants" / f"{slug}.db"
        return get_sqlite_connection(str(db_path))

    def _get_pg_connection(self, tenant_id: str, config: dict) -> Any:
        """Get a PostgreSQL connection from the tenant pool."""
        from tools.saas.db.db_compat import DBAdapter

        with self._lock:
            if tenant_id not in self._pools:
                self._create_pool(tenant_id, config)

        try:
            import psycopg2
            import psycopg2.extras

            pool = self._pools.get(tenant_id)
            if pool:
                conn = pool.getconn()
                conn.cursor_factory = psycopg2.extras.RealDictCursor
                return DBAdapter(conn, engine="postgresql")
        except ImportError:
            logger.warning("psycopg2 not installed, falling back to SQLite")
            slug = config.get("slug", tenant_id)
            from tools.saas.db.db_compat import get_sqlite_connection
            return get_sqlite_connection(
                str(BASE_DIR / "data" / "tenants" / f"{slug}.db")
            )
        except Exception as e:
            logger.error("PG pool error for tenant %s: %s", tenant_id, e)
            raise

    def _create_pool(self, tenant_id: str, config: dict) -> None:
        """Create a new connection pool for a tenant."""
        try:
            from psycopg2.pool import ThreadedConnectionPool

            db_url = (
                f"postgresql://{config['db_host']}"
                f":{config.get('db_port', 5432)}"
                f"/{config['db_name']}"
            )
            pool = ThreadedConnectionPool(self._min_conn, self._max_conn, db_url)
            self._pools[tenant_id] = pool
            logger.info(
                "Created PG pool for tenant %s: %s", tenant_id, config["db_name"]
            )
        except ImportError:
            logger.warning(
                "psycopg2 not available - no PG pool for tenant %s", tenant_id
            )
        except Exception as e:
            logger.error("Failed to create PG pool for %s: %s", tenant_id, e)

    def return_connection(self, tenant_id: str, conn: Any) -> None:
        """Return a connection to the pool."""
        pool = self._pools.get(tenant_id)
        if pool and hasattr(conn, "_conn"):
            try:
                pool.putconn(conn._conn)
            except Exception:
                pass

    def close_all(self) -> None:
        """Close all connection pools."""
        with self._lock:
            for tid, pool in self._pools.items():
                try:
                    pool.closeall()
                except Exception:
                    pass
            self._pools.clear()
            self._tenant_configs.clear()


# Global singleton
_pool: Optional[TenantConnectionPool] = None


def get_pool() -> TenantConnectionPool:
    """Get the global tenant connection pool."""
    global _pool
    if _pool is None:
        _pool = TenantConnectionPool()
    return _pool


def get_tenant_connection(tenant_id: str) -> Any:
    """Convenience: get a connection for a tenant from the global pool."""
    return get_pool().get_connection(tenant_id)
