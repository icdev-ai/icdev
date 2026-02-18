#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""ICDEV SaaS -- Tenant Database Adapter.

Routes database operations to the correct tenant's isolated database.
Acts as the bridge between the API gateway and existing ICDEV tools.

In ICDEV SaaS, every tenant gets its own SQLite file (dev) or PostgreSQL
schema (prod).  Existing tools were written for a single-tenant icdev.db.
This adapter transparently redirects their DB access so the same tool code
works unmodified in a multi-tenant context.

Usage:
    from tools.saas.tenant_db_adapter import (
        get_tenant_db_path,
        get_tenant_db_connection,
        call_tool_with_tenant_db,
        verify_project_belongs_to_tenant,
    )
"""

import inspect
import logging
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("saas.tenant_db")

PLATFORM_DB_PATH = Path(
    os.environ.get("PLATFORM_DB_PATH", str(BASE_DIR / "data" / "platform.db"))
)
TENANTS_DATA_DIR = BASE_DIR / "data" / "tenants"

# ---------------------------------------------------------------------------
# In-memory slug cache with TTL
# ---------------------------------------------------------------------------
_slug_cache: Dict[str, Tuple[dict, float]] = {}
_slug_cache_lock = threading.Lock()
_CACHE_TTL_SECONDS = 300  # 5 minutes


def _cache_get(tenant_id: str) -> Optional[dict]:
    """Retrieve a tenant config from the in-memory TTL cache."""
    with _slug_cache_lock:
        entry = _slug_cache.get(tenant_id)
        if entry is None:
            return None
        config, ts = entry
        if time.time() - ts > _CACHE_TTL_SECONDS:
            del _slug_cache[tenant_id]
            return None
        return config


def _cache_set(tenant_id: str, config: dict) -> None:
    """Store a tenant config in the in-memory TTL cache."""
    with _slug_cache_lock:
        _slug_cache[tenant_id] = (config, time.time())


def _cache_invalidate(tenant_id: str) -> None:
    """Remove a specific tenant from the cache."""
    with _slug_cache_lock:
        _slug_cache.pop(tenant_id, None)


def _cache_clear() -> None:
    """Clear the entire slug cache (useful for testing)."""
    with _slug_cache_lock:
        _slug_cache.clear()


# ---------------------------------------------------------------------------
# SQLite vs PostgreSQL detection
# ---------------------------------------------------------------------------
def _is_sqlite_host(db_host: str) -> bool:
    """Return True if db_host indicates a local SQLite setup, not PostgreSQL.

    Recognized SQLite indicators:
    - empty string or None
    - "localhost-sqlite" (set by tenant_manager during provisioning)
    - filesystem paths (contain path separators or drive letters)
    """
    if not db_host:
        return True
    if db_host == "localhost-sqlite":
        return True
    # Detect filesystem paths (Windows drive letters or path separators)
    if os.sep in db_host or "/" in db_host or "\\" in db_host:
        return True
    if len(db_host) >= 2 and db_host[1] == ":":
        return True  # Windows drive letter like C:
    return False


# ---------------------------------------------------------------------------
# Platform DB helpers
# ---------------------------------------------------------------------------
def _get_platform_conn() -> sqlite3.Connection:
    """Open a connection to the platform database."""
    if not PLATFORM_DB_PATH.exists():
        raise FileNotFoundError(
            "Platform database not found at {}. "
            "Run: python tools/saas/platform_db.py --init".format(PLATFORM_DB_PATH)
        )
    conn = sqlite3.connect(str(PLATFORM_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Tenant config lookup (cached)
# ---------------------------------------------------------------------------
def _get_tenant_config(tenant_id: str) -> Optional[dict]:
    """Look up tenant database configuration from the platform DB.

    Returns a dict with keys: id, slug, db_host, db_name, db_port, status,
    impact_level, tier.  Returns None if the tenant is missing or inactive.
    Uses an in-memory TTL cache to avoid repeated DB lookups.
    """
    # Check cache first
    cached = _cache_get(tenant_id)
    if cached is not None:
        return cached

    try:
        conn = _get_platform_conn()
        row = conn.execute(
            """SELECT id, slug, db_host, db_name, db_port, status,
                      impact_level, tier
               FROM tenants
               WHERE id = ? AND status = 'active'""",
            (tenant_id,),
        ).fetchone()
        conn.close()

        if row is None:
            return None

        config = dict(row)
        _cache_set(tenant_id, config)
        return config
    except Exception as exc:
        logger.error("Tenant config lookup failed for %s: %s", tenant_id, exc)
        return None


def get_tenant_slug(tenant_id: str) -> Optional[str]:
    """Return just the slug for a tenant, or None if not found."""
    config = _get_tenant_config(tenant_id)
    return config["slug"] if config else None


# ---------------------------------------------------------------------------
# Path / connection helpers
# ---------------------------------------------------------------------------
def get_tenant_db_path(tenant_id: str) -> Optional[str]:
    """Get the SQLite database path for a tenant (dev mode).

    Returns the absolute path string to data/tenants/{slug}.db, or None
    if the tenant is not found, not active, or the DB file does not exist.
    In production (PostgreSQL), returns None; callers should use
    get_tenant_db_connection() instead.
    """
    config = _get_tenant_config(tenant_id)
    if not config:
        return None

    slug = config["slug"]

    # Check db_name first (set during provisioning)
    db_name = config.get("db_name")
    if db_name:
        db_host = config.get("db_host") or ""
        if not _is_sqlite_host(db_host):
            # PostgreSQL path -- return None for SQLite callers
            return None
        db_path = TENANTS_DATA_DIR / db_name
    else:
        db_path = TENANTS_DATA_DIR / "{}.db".format(slug)

    if db_path.exists():
        return str(db_path)

    # Create tenants directory if it doesn't exist but don't create the DB
    TENANTS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return str(db_path)


def get_tenant_db_connection(tenant_id: str) -> sqlite3.Connection:
    """Get a database connection for a specific tenant.

    Returns a sqlite3.Connection configured with Row factory, WAL mode,
    and foreign keys enabled.

    Raises:
        ValueError: If the tenant is not found or not active.
        FileNotFoundError: If the tenant database file does not exist.
    """
    config = _get_tenant_config(tenant_id)
    if not config:
        raise ValueError("Tenant {} not found or not active".format(tenant_id))

    # Check if PostgreSQL config exists
    db_host = config.get("db_host") or ""
    if not _is_sqlite_host(db_host):
        try:
            import psycopg2
            import psycopg2.extras

            db_port = config.get("db_port") or 5432
            db_name = config.get("db_name") or config["slug"]
            dsn = "host={} port={} dbname={}".format(db_host, db_port, db_name)
            conn = psycopg2.connect(dsn)
            conn.cursor_factory = psycopg2.extras.RealDictCursor
            logger.debug("Connected to PG for tenant %s", tenant_id)
            return conn
        except ImportError:
            logger.warning("psycopg2 not installed; falling back to SQLite")

    # SQLite path
    slug = config["slug"]
    db_name = config.get("db_name")
    if db_name:
        db_path = TENANTS_DATA_DIR / db_name
    else:
        db_path = TENANTS_DATA_DIR / "{}.db".format(slug)

    if not db_path.exists():
        raise FileNotFoundError(
            "Tenant database not found: {}. "
            "Provision the tenant first.".format(db_path)
        )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    logger.debug("Connected to SQLite for tenant %s at %s", tenant_id, db_path)
    return conn


# ---------------------------------------------------------------------------
# Tool adapter
# ---------------------------------------------------------------------------
def call_tool_with_tenant_db(
    tool_func: Callable[..., Any],
    tenant_id: str,
    **kwargs,
) -> Any:
    """Call an existing ICDEV tool function with the tenant's database.

    Most ICDEV tools accept a ``db_path`` parameter.  This adapter:

    1. Resolves the tenant DB path from the platform database.
    2. Inspects the tool function signature.
    3. Injects ``db_path``, ``db``, or ``database`` into kwargs.
    4. Calls the tool and returns its result.

    Args:
        tool_func: The ICDEV tool function to call.
        tenant_id: The authenticated tenant's ID.
        **kwargs: Additional keyword arguments forwarded to tool_func.

    Returns:
        Whatever the tool function returns.

    Raises:
        ValueError: If the tenant is not found or not active.
    """
    config = _get_tenant_config(tenant_id)
    if not config:
        raise ValueError("Tenant {} not found or not active".format(tenant_id))

    slug = config["slug"]
    db_name = config.get("db_name")
    if db_name:
        db_path = str(TENANTS_DATA_DIR / db_name)
    else:
        db_path = str(TENANTS_DATA_DIR / "{}.db".format(slug))

    # Inspect the function signature and inject DB path
    sig = inspect.signature(tool_func)
    params = sig.parameters

    if "db_path" in params:
        kwargs["db_path"] = db_path
    elif "db" in params:
        kwargs["db"] = db_path
    elif "database" in params:
        kwargs["database"] = db_path

    return tool_func(**kwargs)


# ---------------------------------------------------------------------------
# Ownership verification
# ---------------------------------------------------------------------------
def verify_project_belongs_to_tenant(tenant_id: str, project_id: str) -> bool:
    """Verify that a project belongs to the authenticated tenant.

    Each tenant has its own database so any project_id present in that
    database belongs to the tenant.  This is an extra safety check.
    """
    try:
        conn = get_tenant_db_connection(tenant_id)
        row = conn.execute(
            "SELECT id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def get_tenant_impact_level(tenant_id: str) -> Optional[str]:
    """Return the impact level (IL2/IL4/IL5/IL6) for a tenant."""
    config = _get_tenant_config(tenant_id)
    return config["impact_level"] if config else None


def get_tenant_tier(tenant_id: str) -> Optional[str]:
    """Return the subscription tier for a tenant."""
    config = _get_tenant_config(tenant_id)
    return config["tier"] if config else None
