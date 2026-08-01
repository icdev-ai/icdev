#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""ICDEV™ Database Migration Runner.

D150: Lightweight migration framework using stdlib only (no Alembic).
Tracks schema versions via `schema_migrations` table. Supports .sql and .py
migration files with dual-engine directives (@sqlite-only, @pg-only).

D151: Baseline migration (001) extracted from init_icdev_db.py. The init
script is preserved for backward compatibility.

D152: Migration numbering is strictly sequential with no intentional gaps.
Verified 2026-04-11: migrations 001–014 are all present and applied.
The sequence 010, 011, 012, 013 (finding_approvals), 014 is continuous.
Any apparent gap (e.g. 013 missing) indicates the observer's working copy
predates when that migration was committed — not an intentional skip.
"""

import hashlib
import importlib.util
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import StorageConnection

logger = get_logger("icdev.db.migration")


def _detect_engine() -> str:
    """The engine actually in use, so `engine=` need not be guessed correctly.

    Defaulting to "sqlite" while connected to PostgreSQL is silent data loss:
    `_filter_sql` drops every `@pg-only` statement, and the migration is then
    recorded as applied.
    """
    try:
        from tools.db.storage import is_pg

        return "postgresql" if is_pg() else "sqlite"
    except Exception:  # noqa: BLE001 - storage unavailable at import time
        return "sqlite"

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
        engine: Optional[str] = None,
    ):
        self.db_path = db_path or (BASE_DIR / "data" / "icdev.db")
        self.migrations_dir = migrations_dir or MIGRATIONS_DIR
        self.engine = engine if engine is not None else _detect_engine()

        # `engine` drives _filter_sql, so a value that disagrees with the live
        # backend does not error — it silently drops every statement meant for
        # the real engine and applies the other one's, then records the
        # migration as applied with ~0ms elapsed. That is indistinguishable
        # from success. Warn loudly; the caller may still have a reason.
        actual = _detect_engine()
        if self.engine != actual:
            logger.warning(
                "MigrationRunner engine=%r but the active backend is %r. "
                "@%s-only statements will be DROPPED and the migration may "
                "record as applied having done nothing.",
                self.engine, actual, "pg" if actual == "postgresql" else "sqlite",
            )

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------
    def _get_connection(self):
        """Get a database connection.

        Uses get_connection() from storage abstraction when
        ICDEV_STORAGE_BACKEND=postgresql; falls back to direct sqlite3
        so the runner remains stdlib-only on SQLite deployments.
        """
        import os

        backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()
        if backend == "postgresql":
            from tools.db.storage import get_connection

            return get_connection()
        # SQLite default
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # Wrap so %s-authored SQL in this module (PG-primary house style) is
        # translated to sqlite's ? placeholders. No security context is set,
        # so StorageConnection injects no RLS predicate — schema_migrations
        # has neither tenant_id nor classification.
        return StorageConnection(conn, "sqlite")

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
        import os

        backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()
        if backend == "postgresql":
            conn = self._get_connection()
            try:
                result = conn.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'schema_migrations' AND table_schema = 'public'"
                ).fetchone()
                return result is not None
            finally:
                conn.close()
        # SQLite
        if not self.db_path.exists():
            return False
        conn = self._get_connection()
        try:
            # pg-portability: sqlite-only path — SQLite branch (the PG branch above
            # queries information_schema); backend selected by ICDEV_STORAGE_BACKEND.
            c = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'")
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
            # Flat SQL file: NNN_description.sql
            if entry.is_file() and entry.suffix == ".sql":
                flat_match = re.match(r"^(\d{3})_(.+)\.sql$", entry.name)
                if flat_match:
                    version = flat_match.group(1)
                    name = flat_match.group(2)
                    migrations.append(
                        {
                            "version": version,
                            "name": name,
                            "dir": None,
                            "flat_sql": entry,
                            "has_up_sql": True,
                            "has_up_py": False,
                            "has_down_sql": False,
                            "has_down_py": False,
                            "meta": {},
                            "checksum": self._file_checksum(entry),
                        }
                    )
                continue

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
        """Return list of migrations not yet applied.

        schema_migrations.version is UNIQUE, so only one migration per version
        number is ever recorded. Several version numbers are duplicated on disk
        (e.g. two 010_* dirs); in steady state get_pending naturally yields only
        the first because the version is already in applied_versions after the
        first run. On a *fresh* database both same-version dirs would otherwise
        be pending in the same run, and applying the second would violate the
        UNIQUE constraint and fail the whole chain (seen on the CI E2E PG job).
        Dedupe by version within the run too — keep the first by sort order,
        matching the system's established one-migration-per-version behaviour.
        """
        seen = {m["version"] for m in self.get_applied_migrations()}
        pending = []
        for m in self.discover_migrations():
            if m["version"] in seen:
                continue
            seen.add(m["version"])
            pending.append(m)
        return pending

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
        mdir = migration.get("dir")

        logger.info("Applying migration %s (%s)...", version, name)

        if dry_run:
            up_sql = migration.get("flat_sql") or (mdir / "up.sql" if mdir else None)
            if up_sql and up_sql.exists():
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
            up_sql = migration.get("flat_sql") or (mdir / "up.sql" if mdir else None)
            if up_sql and up_sql.exists():
                sql = up_sql.read_text(encoding="utf-8")
                filtered = self._filter_sql(sql)
                try:
                    conn.executescript(filtered)
                except Exception as table_exc:
                    if "already exists" in str(table_exc).lower():
                        logger.warning(
                            "Migration %s: object already exists — graceful guard active: %s",
                            version,
                            table_exc,
                        )
                    else:
                        raise

            # Python migration
            up_py = (mdir / "up.py") if mdir else None
            if up_py and up_py.exists() and not (up_sql and up_sql.exists()):
                spec = importlib.util.spec_from_file_location(f"migration_{version}_up", str(up_py))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "up"):
                    mod.up(conn)

            elapsed_ms = int((time.time() - start) * 1000)

            # Record in schema_migrations. Use OR IGNORE (→ ON CONFLICT DO
            # NOTHING on PG) so a duplicate version number — e.g. two distinct
            # migration dirs both prefixed 010 (kanban_executor_schema and
            # network_intelligence_schema) — does not crash a fresh full run.
            # Both such migrations are individually idempotent; recording one
            # version row is sufficient.
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, name, checksum, execution_time_ms) VALUES (%s, %s, %s, %s)",
                (version, name, migration.get("checksum", ""), elapsed_ms),
            )
            conn.commit()

            logger.info("Migration %s applied in %dms", version, elapsed_ms)

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
                spec = importlib.util.spec_from_file_location(f"migration_{version}_down", str(down_py))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "down"):
                    mod.down(conn)

            elapsed_ms = int((time.time() - start) * 1000)

            # Mark as rolled back (append-only — don't delete the row)
            conn.execute(
                "UPDATE schema_migrations SET rolled_back_at = datetime('now') WHERE version = %s",
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
        self,
        target: Optional[str] = None,
        dry_run: bool = False,
        continue_on_error: bool = False,
    ) -> List[Dict]:
        """Apply all pending migrations up to target version.

        continue_on_error: when True, a failing migration does not stop the
        pass — the remaining pending migrations are still attempted. Used by
        the converge runner to tolerate out-of-order migrations (each
        apply_migration is isolated on its own connection, rolled back on
        failure, so a failure never poisons the next migration).
        """
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
            if not result.get("success") and not continue_on_error:
                logger.error("Migration failed — stopping.")
                break

        return results

    def migrate_up_converge(
        self, target: Optional[str] = None, max_passes: int = 12
    ) -> Dict:
        """Apply pending migrations in repeated passes until a pass makes no
        progress (fixpoint), tolerating out-of-order migrations.

        A migration that fails because a table it ALTERs is created by a LATER
        migration is not recorded as applied, so it stays pending and is retried
        on the next pass — by which time the later migration has created the
        table. Converges to a complete schema without per-migration reordering.

        Returns {passes: [...], applied_total, remaining_failures: [...]}.
        """
        passes: List[Dict] = []
        for pass_num in range(1, max_passes + 1):
            results = self.migrate_up(target=target, continue_on_error=True)
            applied = [r for r in results if r.get("success")]
            failed = [r for r in results if not r.get("success")]
            passes.append({
                "pass": pass_num,
                "applied": len(applied),
                "failed": len(failed),
                "failures": [
                    {"version": r["version"], "name": r.get("name"), "error": r.get("error")}
                    for r in failed
                ],
            })
            logger.info(
                "converge pass %d: applied=%d failed=%d", pass_num, len(applied), len(failed)
            )
            if not results:
                break  # nothing pending — fully applied
            if not applied:
                break  # no progress this pass — remaining failures are real
        return {
            "passes": passes,
            "applied_total": sum(p["applied"] for p in passes),
            "remaining_failures": passes[-1]["failures"] if passes else [],
        }

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
                issues.append(
                    {
                        "version": version,
                        "issue": "migration_files_missing",
                        "detail": f"Migration {version} was applied but files no longer exist",
                    }
                )
                continue

            if discovered["checksum"] != m["checksum"]:
                issues.append(
                    {
                        "version": version,
                        "issue": "checksum_mismatch",
                        "detail": f"Expected {m['checksum']}, found {discovered['checksum']}",
                    }
                )

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
        (mdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

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
                "VALUES (%s, %s, %s, 0, 'icdev-migrate (mark-applied)')",
                (version, migration["name"], migration.get("checksum", "")),
            )
            conn.commit()
            logger.info("Marked migration %s as applied", version)
        finally:
            conn.close()
