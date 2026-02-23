#!/usr/bin/env python3
# CUI // SP-CTI
"""Centralized database path resolution and connection helpers for ICDEV.

Provides functions to resolve database paths and create connections with a
consistent fallback chain: env var > explicit argument > default.

Usage:
    from tools.compat.db_utils import get_icdev_db_path, get_db_connection

    db_path = get_icdev_db_path()                    # env var or default
    db_path = get_icdev_db_path("/custom/path.db")   # explicit override

    conn = get_db_connection()                       # default icdev.db
    conn = get_db_connection(validate=True)           # raise if DB missing
    conn = get_db_connection(db_path="/other.db")    # explicit DB

Fallback chain:
    1. Explicit path argument (if provided)
    2. ICDEV_DB_PATH environment variable
    3. Default: <project_root>/data/icdev.db

This module uses only Python stdlib (air-gap safe).
"""

import os
import sqlite3
from pathlib import Path
from typing import Optional, Union

# Project root: 3 levels up from tools/compat/db_utils.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB = _PROJECT_ROOT / "data" / "icdev.db"


def get_project_root() -> Path:
    """Return the ICDEV project root directory."""
    return _PROJECT_ROOT


def get_icdev_db_path(explicit: Optional[Union[str, Path]] = None) -> Path:
    """Resolve the ICDEV database path.

    Args:
        explicit: Optional explicit path override (highest priority).

    Returns:
        Resolved Path to the ICDEV database.

    Fallback chain:
        1. explicit argument
        2. ICDEV_DB_PATH env var
        3. <project_root>/data/icdev.db
    """
    if explicit:
        return Path(explicit)

    env_path = os.environ.get("ICDEV_DB_PATH")
    if env_path:
        return Path(env_path)

    return _DEFAULT_DB


def get_memory_db_path(explicit: Optional[Union[str, Path]] = None) -> Path:
    """Resolve the memory database path.

    Fallback: ICDEV_MEMORY_DB_PATH env var > <project_root>/data/memory.db
    """
    if explicit:
        return Path(explicit)

    env_path = os.environ.get("ICDEV_MEMORY_DB_PATH")
    if env_path:
        return Path(env_path)

    return _PROJECT_ROOT / "data" / "memory.db"


def get_platform_db_path(explicit: Optional[Union[str, Path]] = None) -> Path:
    """Resolve the platform (SaaS) database path.

    Fallback: ICDEV_PLATFORM_DB_PATH env var > <project_root>/data/platform.db
    """
    if explicit:
        return Path(explicit)

    env_path = os.environ.get("ICDEV_PLATFORM_DB_PATH")
    if env_path:
        return Path(env_path)

    return _PROJECT_ROOT / "data" / "platform.db"


# ---------------------------------------------------------------------------
# Connection helpers (D152 pattern — centralized, replaces per-file duplication)
# ---------------------------------------------------------------------------


def get_db_connection(
    db_path: Optional[Union[str, Path]] = None,
    validate: bool = False,
    row_factory: bool = True,
) -> sqlite3.Connection:
    """Get a SQLite connection to the ICDEV database.

    Centralizes the ``_get_connection()`` pattern duplicated across 87+ files.

    Args:
        db_path: Explicit path override.  Falls through the standard
                 ``get_icdev_db_path`` chain when *None*.
        validate: When *True*, raise ``FileNotFoundError`` if the DB file
                  does not exist yet.
        row_factory: When *True* (default), set ``sqlite3.Row`` so columns
                     are accessible by name.

    Returns:
        An open ``sqlite3.Connection``.  Caller is responsible for closing it.
    """
    path = get_icdev_db_path(db_path)
    if validate and not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn


def get_memory_connection(
    db_path: Optional[Union[str, Path]] = None,
    validate: bool = False,
    row_factory: bool = True,
) -> sqlite3.Connection:
    """Get a SQLite connection to the memory database."""
    path = get_memory_db_path(db_path)
    if validate and not path.exists():
        raise FileNotFoundError(f"Memory database not found: {path}")
    conn = sqlite3.connect(str(path))
    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn


def get_platform_connection(
    db_path: Optional[Union[str, Path]] = None,
    validate: bool = False,
    row_factory: bool = True,
) -> sqlite3.Connection:
    """Get a SQLite connection to the platform (SaaS) database."""
    path = get_platform_db_path(db_path)
    if validate and not path.exists():
        raise FileNotFoundError(f"Platform database not found: {path}")
    conn = sqlite3.connect(str(path))
    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn
