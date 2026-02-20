#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV Database Migration Runner.

D150: Lightweight migration framework using stdlib only (no Alembic).
Tracks schema versions via `schema_migrations` table. Supports .sql and .py
migration files with dual-engine directives (@sqlite-only, @pg-only).

D151: Baseline migration (001) extracted from init_icdev_db.py. The init
script is preserved for backward compatibility.
"""

import hashlib
import importlib.util
import json
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("icdev.db.migration")

MIGRATIONS_DIR = BASE_DIR / "tools" / "db" / "migrations"

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    applied_at TEXT DEFAULT (datetime('now')),
    checksum TEXT NOT NULL,
    execution_time_ms INTEGER,
    applied_by TEXT DEFAULT 'icdev-migrate',
    rolled_back_at TEXT,
    classification TEXT DEFAULT 'CUI'
);
"""


class MigrationRunner:
    """Lightweight database migration runner.

    Discovers migration directories (NNN_description/), applies them in order,
    tracks versions in schema_migrations table, and validates checksums.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        migrations_dir: Optional[Path] = None,
        engine: str = "sqlite",
    ):
        self.db_path = db_path or (BASE_DIR / "data" / "icdev.db")
        self.migrations_dir = migrations_dir or MIGRATIONS_DIR
        self.engine = engine

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------
    def _get_connection(self) -> sqlite3.Connection:
        """Get a SQLite connection with WAL mode and row factory."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ------------------------------------------------------------------
    # Schema migrations table
    # ------------------------------------------------------------------
    def ensure_migrations_table(self):
        """Create the schema_migrations table if it doesn't exist."""
        conn = self._get_connection()
        try:
            conn.executescript(SCHEMA_MIGRATIONS_DDL)
            conn.commit()
        finally:
            conn.close()

    def has_migrations_table(self) -> bool:
        """Check if the schema_migrations table exists."""
        if not self.db_path.exists():
            return False
        conn = self._get_connection()
        try:
            c = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            )
            return c.fetchone() is not None
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Migration discovery
    # ------------------------------------------------------------------
    def discover_migrations(self) -> List[Dict[str, Any]]:
        """Discover all migration directories, sorted by version."""
        migrations = []
        if not self.migrations_dir.exists():
            return migrations

        for entry in sorted(self.migrations_dir.iterdir()):
            if not entry.is_dir():
                continue
            # Match NNN_description pattern
            match = re.match(r"^(\d{3})_(.+)$", entry.name)
            if not match:
                continue

            version = match.group(1)
            name = match.group(2)

            # Check for up.sql or up.py
            up_sql = entry / "up.sql"
            up_py = entry / "up.py"
            down_sql = entry / "down.sql"
            down_py = entry / "down.py"
            meta_file = entry / "meta.json"

            migration = {
                "version": version,
                "name": name,
                "dir": entry,
                "has_up_sql": up_sql.exists(),
                "has_up_py": up_py.exists(),
                "has_down_sql": down_sql.exists(),
                "has_down_py": down_py.exists(),
                "meta": {},
            }

            # Load metadata
            if meta_file.exists():
                try:
                    with open(meta_file, "r") as f:
                        migration["meta"] = json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass

            # Compute checksum of up file
            up_file = up_sql if up_sql.exists() else (up_py if up_py.exists() else None)
            if up_file:
                migration["checksum"] = self._file_checksum(up_file)
                migrations.append(migration)

        return migrations

    def _file_checksum(self, file_path: Path) -> str:
        """Compute SHA-256 checksum of a file."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]

    # ------------------------------------------------------------------
    # Applied migrations
    # ------------------------------------------------------------------
    def get_applied_migrations(self) -> List[Dict]:
        """Return list of applied (non-rolled-back) migrations."""
        if not self.has_migrations_table():
            return []
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT version, name, applied_at, checksum, execution_time_ms "
                "FROM schema_migrations WHERE rolled_back_at IS NULL "
                "ORDER BY version"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_pending_migrations(self) -> List[Dict]:
        """Return list of migrations not yet applied."""
        applied_versions = {m["version"] for m in self.get_applied_migrations()}
        all_migrations = self.discover_migrations()
        return [m for m in all_migrations if m["version"] not in applied_versions]

    # ------------------------------------------------------------------
    # SQL parsing with engine directives
    # ------------------------------------------------------------------
    def _filter_sql(self, sql: str) -> str:
        """Filter SQL based on engine directives.

        Supports:
            -- @sqlite-only  (next statements until next directive)
            -- @pg-only      (next statements until next directive)
            -- @all           (reset — both engines, default)
        """
        lines = sql.split("\n")
        filtered = []
        include = True

        for line in lines:
            stripped = line.strip().lower()

            if stripped == "-- @sqlite-only":
                include = self.engine == "sqlite"
                continue
            elif stripped == "-- @pg-only":
                include = self.engine == "postgresql"
                continue
            elif stripped == "-- @all":
                include = True
                continue

            if include:
                filtered.append(line)

        return "\n".join(filtered)

    # ------------------------------------------------------------------
    # Apply / Rollback
    # ------------------------------------------------------------------
    def apply_migration(self, migration: Dict, dry_run: bool = False) -> Dict:
        """Apply a single migration (up direction).

        Returns: {version, name, success, execution_time_ms, error}
        """
        version = migration["version"]
        name = migration["name"]
        mdir = migration["dir"]

        logger.info("Applying migration %s (%s)...", version, name)

        if dry_run:
            up_sql = mdir / "up.sql"
            if up_sql.exists():
                sql = up_sql.read_text(encoding="utf-8")
                filtered = self._filter_sql(sql)
                return {
                    "version": version,
                    "name": name,
                    "success": True,
                    "dry_run": True,
                    "sql_preview": filtered[:500],
                }
            return {"version": version, "name": name, "success": True, "dry_run": True}

        start = time.time()
        conn = self._get_connection()

        try:
            # SQL migration
            up_sql = mdir / "up.sql"
            if up_sql.exists():
                sql = up_sql.read_text(encoding="utf-8")
                filtered = self._filter_sql(sql)
                conn.executescript(filtered)

            # Python migration
            up_py = mdir / "up.py"
            if up_py.exists() and not up_sql.exists():
                spec = importlib.util.spec_from_file_location(
                    f"migration_{version}_up", str(up_py)
                )
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "up"):
                    mod.up(conn)

            elapsed_ms = int((time.time() - start) * 1000)

            # Record in schema_migrations
            conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum, execution_time_ms) "
                "VALUES (?, ?, ?, ?)",
                (version, name, migration.get("checksum", ""), elapsed_ms),
            )
            conn.commit()

            logger.info(
                "Migration %s applied in %dms", version, elapsed_ms
            )

            # Audit trail (best-effort)
            try:
                from tools.audit.audit_logger import log_event
                log_event(
                    event_type="config_changed",
                    actor="icdev-migrate",
                    action=f"Applied migration {version} ({name})",
                    details={
                        "version": version,
                        "name": name,
                        "execution_time_ms": elapsed_ms,
                        "direction": "up",
                    },
                    classification="CUI",
                )
            except Exception:
                pass

            return {
                "version": version,
                "name": name,
                "success": True,
                "execution_time_ms": elapsed_ms,
            }

        except Exception as exc:
            conn.rollback()
            logger.error("Migration %s failed: %s", version, exc)
            return {
                "version": version,
                "name": name,
                "success": False,
                "error": str(exc),
            }
        finally:
            conn.close()

    def rollback_migration(self, migration: Dict) -> Dict:
        """Roll back a single migration (down direction)."""
        version = migration["version"]
        name = migration["name"]
        mdir = migration["dir"]

        logger.info("Rolling back migration %s (%s)...", version, name)

        start = time.time()
        conn = self._get_connection()

        try:
            down_sql = mdir / "down.sql"
            if down_sql.exists():
                sql = down_sql.read_text(encoding="utf-8")
                filtered = self._filter_sql(sql)
                conn.executescript(filtered)

            down_py = mdir / "down.py"
            if down_py.exists() and not down_sql.exists():
                spec = importlib.util.spec_from_file_location(
                    f"migration_{version}_down", str(down_py)
                )
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "down"):
                    mod.down(conn)

            elapsed_ms = int((time.time() - start) * 1000)

            # Mark as rolled back (append-only — don't delete the row)
            conn.execute(
                "UPDATE schema_migrations SET rolled_back_at = datetime('now') "
                "WHERE version = ?",
                (version,),
            )
            conn.commit()

            logger.info("Migration %s rolled back in %dms", version, elapsed_ms)
            return {"version": version, "name": name, "success": True, "execution_time_ms": elapsed_ms}

        except Exception as exc:
            conn.rollback()
            logger.error("Rollback of %s failed: %s", version, exc)
            return {"version": version, "name": name, "success": False, "error": str(exc)}
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------
    def migrate_up(
        self, target: Optional[str] = None, dry_run: bool = False
    ) -> List[Dict]:
        """Apply all pending migrations up to target version."""
        self.ensure_migrations_table()
        pending = self.get_pending_migrations()

        if target:
            pending = [m for m in pending if m["version"] <= target]

        if not pending:
            logger.info("No pending migrations.")
            return []

        results = []
        for migration in pending:
            result = self.apply_migration(migration, dry_run=dry_run)
            results.append(result)
            if not result.get("success"):
                logger.error("Migration failed — stopping.")
                break

        return results

    def migrate_down(self, target: Optional[str] = None) -> List[Dict]:
        """Roll back applied migrations down to (but not including) target."""
        applied = self.get_applied_migrations()
        all_discovered = {m["version"]: m for m in self.discover_migrations()}

        # Roll back in reverse order
        to_rollback = list(reversed(applied))
        if target:
            to_rollback = [m for m in to_rollback if m["version"] > target]

        if not to_rollback:
            logger.info("Nothing to roll back.")
            return []

        results = []
        for applied_m in to_rollback:
            version = applied_m["version"]
            discovered = all_discovered.get(version)
            if not discovered:
                logger.warning("Migration %s files not found — skipping", version)
                continue

            result = self.rollback_migration(discovered)
            results.append(result)
            if not result.get("success"):
                break

        return results

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate_checksums(self) -> List[Dict]:
        """Validate that applied migration files haven't been modified."""
        applied = self.get_applied_migrations()
        all_discovered = {m["version"]: m for m in self.discover_migrations()}
        issues = []

        for m in applied:
            version = m["version"]
            discovered = all_discovered.get(version)
            if not discovered:
                issues.append({
                    "version": version,
                    "issue": "migration_files_missing",
                    "detail": f"Migration {version} was applied but files no longer exist",
                })
                continue

            if discovered["checksum"] != m["checksum"]:
                issues.append({
                    "version": version,
                    "issue": "checksum_mismatch",
                    "detail": f"Expected {m['checksum']}, found {discovered['checksum']}",
                })

        return issues

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def get_status(self) -> Dict:
        """Get full migration status."""
        applied = self.get_applied_migrations()
        pending = self.get_pending_migrations()
        issues = self.validate_checksums() if applied else []

        return {
            "db_path": str(self.db_path),
            "migrations_dir": str(self.migrations_dir),
            "engine": self.engine,
            "has_migrations_table": self.has_migrations_table(),
            "applied_count": len(applied),
            "pending_count": len(pending),
            "applied": applied,
            "pending": [{"version": m["version"], "name": m["name"]} for m in pending],
            "issues": issues,
            "current_version": applied[-1]["version"] if applied else None,
        }

    # ------------------------------------------------------------------
    # Scaffold new migration
    # ------------------------------------------------------------------
    def create_migration(self, name: str) -> str:
        """Create a new migration directory scaffold.

        Returns the path to the created migration directory.
        """
        self.migrations_dir.mkdir(parents=True, exist_ok=True)

        # Find next version number
        existing = self.discover_migrations()
        next_version = "001"
        if existing:
            last = int(existing[-1]["version"])
            next_version = f"{last + 1:03d}"

        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        dir_name = f"{next_version}_{slug}"
        mdir = self.migrations_dir / dir_name
        mdir.mkdir(parents=True)

        # Create scaffold files
        (mdir / "up.sql").write_text(
            f"-- Migration: {dir_name}\n-- CUI // SP-CTI\n\n-- Add your schema changes here\n",
            encoding="utf-8",
        )
        (mdir / "down.sql").write_text(
            f"-- Rollback: {dir_name}\n-- CUI // SP-CTI\n\n-- Add rollback statements here\n",
            encoding="utf-8",
        )
        meta = {
            "description": name,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "author": "icdev-builder",
            "database": "icdev",
            "reversible": True,
        }
        (mdir / "meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )

        logger.info("Created migration scaffold: %s", mdir)
        return str(mdir)

    # ------------------------------------------------------------------
    # Mark existing DB as having baseline applied
    # ------------------------------------------------------------------
    def mark_applied(self, version: str):
        """Mark a migration as already applied (for existing databases).

        Used when the baseline migration's schema already exists in the DB
        (e.g., created by init_icdev_db.py before the migration system).
        """
        self.ensure_migrations_table()
        all_discovered = {m["version"]: m for m in self.discover_migrations()}
        migration = all_discovered.get(version)
        if not migration:
            raise ValueError(f"Migration {version} not found in {self.migrations_dir}")

        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations "
                "(version, name, checksum, execution_time_ms, applied_by) "
                "VALUES (?, ?, ?, 0, 'icdev-migrate (mark-applied)')",
                (version, migration["name"], migration.get("checksum", "")),
            )
            conn.commit()
            logger.info("Marked migration %s as applied", version)
        finally:
            conn.close()
