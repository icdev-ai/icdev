#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-fnd-02 — storage for the 2026 SBOM Minimum Elements.

Migration under test: ``tools/db/migrations/20260808030213_sbom_2026_minimum_elements``.

The point of this module is that nothing here reads the migration's SQL as text.
Every assertion runs against a database the migration was actually APPLIED to —
the SQLite one is built by handing the real ``MigrationRunner`` a temp file whose
``sbom_records`` / ``sbom_components`` carry their PRE-migration shape, exactly as
``tools/db/init_icdev_db.py`` declares them. A test that pasted the new DDL in and
then asserted the columns existed would pass for a migration that never runs.

Three things are checked:

1. The migration applies to a pre-migration SQLite database and every new column
   round-trips a real value — write, read back, compare. That is what proves the
   column is usable, not merely present.
2. ``MINIMAL_ICDEV_SCHEMA`` in ``tests/conftest.py`` declares the SAME column set
   the migration produces. The conftest schema is what most of the suite tests
   against, so drift between it and the migration is how a test starts passing
   against a table shape production does not have.
3. The same round-trip on PostgreSQL, skipped unless a live PG is configured and
   reachable. PG is the primary backend and ``ADD COLUMN IF NOT EXISTS`` is a
   PG-only clause, so the SQLite pass alone does not cover the branch that
   actually ships.
"""

import os
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

MIGRATION_VERSION = "20260808030213"

#: The shape of the two tables BEFORE the migration, copied from
#: tools/db/init_icdev_db.py. This is the starting point the migration has to
#: cope with on a live database — not a convenience fixture.
PRE_MIGRATION_DDL = """
CREATE TABLE IF NOT EXISTS sbom_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    version TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'cyclonedx',
    file_path TEXT NOT NULL,
    component_count INTEGER,
    vulnerability_count INTEGER,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sbom_components (
    id              TEXT    PRIMARY KEY,
    component_name  TEXT    NOT NULL,
    version         TEXT,
    vendor          TEXT,
    component_type  TEXT,
    purl            TEXT,
    license         TEXT,
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);
"""

#: Every column the migration adds to sbom_records, with the value the
#: round-trip writes. Keyed by column so a missing column fails by name.
NEW_RECORD_COLUMNS = {
    "sbom_author": "Innovative Computer Development, Incorporated",
    "author_signature": "MEUCIQD0f4k3S1gnatur3Byt3s==",
    "signature_algorithm": "ecdsa-with-SHA384",
    "data_format_name": "CycloneDX",
    "data_format_version": "1.6",
    "generation_context": "before build",
    "tool_name": "icdev-sbom-generator",
    "tool_version": "2.4.1",
    "sbom_version": "1.2.0",
    "serial_number": "urn:uuid:1b2c3d4e-5f60-4718-8293-a4b5c6d7e8f9",
    "classification": "CUI // SP-CTI",
    "tenant_id": "acme",
}

#: Every column the migration adds to sbom_components.
NEW_COMPONENT_COLUMNS = {
    "producer": "Python Software Foundation",
    "hash_value": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "hash_algorithm": "sha-256",
    "identifiers_json": '{"purl": "pkg:pypi/requests@2.32.3", '
                        '"cpe": "cpe:2.3:a:python:requests:2.32.3:*:*:*:*:*:*:*"}',
    "unknown_fields_json": '{"producer": "unknown-to-author"}',
    "withheld_fields_json": '{"hash_value": "withheld"}',
    "tenant_id": "acme",
}

#: sbom_dependencies is created whole by the migration, so every column counts.
DEPENDENCY_COLUMNS = {
    "relationship_type": "depends_on",
    "scope": "runtime",
    "classification": "CUI",
    "tenant_id": "acme",
}


def _sqlite_columns(conn, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


@pytest.fixture
def migrated_sqlite_db(tmp_path, monkeypatch):
    """A pre-migration SQLite database with the real migration applied to it."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    from tools.db.migration_runner import MigrationRunner

    db_path = tmp_path / "sbom_schema.db"
    seed = sqlite3.connect(str(db_path))
    seed.executescript(PRE_MIGRATION_DDL)
    seed.commit()
    seed.close()

    runner = MigrationRunner(db_path=db_path, engine="sqlite")
    runner.ensure_migrations_table()
    migration = next(
        (m for m in runner.discover_migrations() if m["version"] == MIGRATION_VERSION),
        None,
    )
    assert migration is not None, (
        f"migration {MIGRATION_VERSION} is not discoverable — a directory with "
        "neither up.sql nor up.py is skipped silently by discover_migrations"
    )
    result = runner.apply_migration(migration)
    assert result["success"], f"migration failed on SQLite: {result.get('error')}"
    return db_path


def test_migration_applies_to_a_pre_migration_sqlite_database(migrated_sqlite_db):
    """Every declared column exists on the live table after the migration runs."""
    conn = sqlite3.connect(str(migrated_sqlite_db))
    try:
        record_cols = _sqlite_columns(conn, "sbom_records")
        assert set(NEW_RECORD_COLUMNS) <= record_cols
        assert "supersedes_sbom_id" in record_cols
        # The original columns survive — ADD COLUMN must not have rebuilt the table.
        assert {"project_id", "version", "format", "file_path"} <= record_cols

        assert set(NEW_COMPONENT_COLUMNS) <= _sqlite_columns(conn, "sbom_components")
        # The dead-but-reusable columns are reused, not duplicated.
        component_cols = _sqlite_columns(conn, "sbom_components")
        assert {"license", "vendor"} <= component_cols
        assert "component_license" not in component_cols
        assert "component_vendor" not in component_cols

        dep_cols = _sqlite_columns(conn, "sbom_dependencies")
        assert {
            "id",
            "sbom_record_id",
            "parent_component_id",
            "child_component_id",
        } | set(DEPENDENCY_COLUMNS) <= dep_cols
    finally:
        conn.close()


def test_every_new_column_round_trips_on_sqlite(migrated_sqlite_db):
    """Write one row per table populating every new column, then read it back."""
    conn = sqlite3.connect(str(migrated_sqlite_db))
    conn.row_factory = sqlite3.Row
    try:
        # --- sbom_records: base row, then a successor that supersedes it -----
        base_cols = ["project_id", "version", "format", "file_path",
                     "component_count", "vulnerability_count"]
        base_vals = ["proj-sbx", "1.0", "cyclonedx", "/tmp/sbom-v1.cdx.json", 3, 0]
        cols = base_cols + list(NEW_RECORD_COLUMNS)
        vals = base_vals + list(NEW_RECORD_COLUMNS.values())
        placeholders = ", ".join("?" * len(cols))
        cur = conn.execute(
            f"INSERT INTO sbom_records ({', '.join(cols)}) VALUES ({placeholders})",
            vals,
        )
        first_id = cur.lastrowid

        cur = conn.execute(
            "INSERT INTO sbom_records "
            "(project_id, version, format, file_path, sbom_version, supersedes_sbom_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("proj-sbx", "2.0", "cyclonedx", "/tmp/sbom-v2.cdx.json", "1.3.0", first_id),
        )
        second_id = cur.lastrowid

        row = conn.execute(
            "SELECT * FROM sbom_records WHERE id = ?", (first_id,)
        ).fetchone()
        for column, expected in NEW_RECORD_COLUMNS.items():
            assert row[column] == expected, f"sbom_records.{column} did not round-trip"

        successor = conn.execute(
            "SELECT supersedes_sbom_id FROM sbom_records WHERE id = ?", (second_id,)
        ).fetchone()
        assert successor["supersedes_sbom_id"] == first_id

        # --- sbom_components -------------------------------------------------
        parent_id, child_id = str(uuid.uuid4()), str(uuid.uuid4())
        for comp_id, name in ((parent_id, "icdev"), (child_id, "requests")):
            cols = ["id", "component_name", "version", "vendor", "component_type",
                    "purl", "license"] + list(NEW_COMPONENT_COLUMNS)
            vals = [comp_id, name, "2.32.3", "PSF", "library",
                    f"pkg:pypi/{name}@2.32.3", "Apache-2.0"] + list(
                NEW_COMPONENT_COLUMNS.values()
            )
            conn.execute(
                f"INSERT INTO sbom_components ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})",
                vals,
            )

        row = conn.execute(
            "SELECT * FROM sbom_components WHERE id = ?", (child_id,)
        ).fetchone()
        for column, expected in NEW_COMPONENT_COLUMNS.items():
            assert row[column] == expected, f"sbom_components.{column} did not round-trip"
        # The reused columns carry the 2026 Component License / vendor data.
        assert row["license"] == "Apache-2.0"
        assert row["vendor"] == "PSF"

        # --- sbom_dependencies: a real edge, scoped to the document ----------
        edge_id = str(uuid.uuid4())
        cols = ["id", "sbom_record_id", "parent_component_id",
                "child_component_id"] + list(DEPENDENCY_COLUMNS)
        vals = [edge_id, first_id, parent_id, child_id] + list(
            DEPENDENCY_COLUMNS.values()
        )
        conn.execute(
            f"INSERT INTO sbom_dependencies ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})",
            vals,
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM sbom_dependencies WHERE id = ?", (edge_id,)
        ).fetchone()
        assert row["sbom_record_id"] == first_id
        assert row["parent_component_id"] == parent_id
        assert row["child_component_id"] == child_id
        for column, expected in DEPENDENCY_COLUMNS.items():
            assert row[column] == expected, f"sbom_dependencies.{column} did not round-trip"
        assert row["created_at"] is not None
    finally:
        conn.close()


def test_a_component_can_have_many_parents(migrated_sqlite_db):
    """The edge table, not a parent-ref column — that is why it is a table.

    A parent ref on sbom_components would cap a component at one parent, which
    is wrong for any real dependency tree: one package is pulled in by several
    others inside a single SBOM.
    """
    conn = sqlite3.connect(str(migrated_sqlite_db))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "INSERT INTO sbom_records (project_id, version, format, file_path) "
            "VALUES (?, ?, ?, ?)",
            ("proj-multi", "1.0", "cyclonedx", "/tmp/multi.cdx.json"),
        )
        record_id = cur.lastrowid

        shared = str(uuid.uuid4())
        parents = [str(uuid.uuid4()) for _ in range(3)]
        for comp_id, name in [(shared, "urllib3")] + [
            (p, f"parent-{i}") for i, p in enumerate(parents)
        ]:
            conn.execute(
                "INSERT INTO sbom_components (id, component_name) VALUES (?, ?)",
                (comp_id, name),
            )
        for parent in parents:
            conn.execute(
                "INSERT INTO sbom_dependencies "
                "(id, sbom_record_id, parent_component_id, child_component_id) "
                "VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), record_id, parent, shared),
            )
        conn.commit()

        count = conn.execute(
            "SELECT COUNT(*) AS n FROM sbom_dependencies WHERE child_component_id = ?",
            (shared,),
        ).fetchone()["n"]
        assert count == 3

        # The UNIQUE constraint still stops the same edge being recorded twice.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sbom_dependencies "
                "(id, sbom_record_id, parent_component_id, child_component_id) "
                "VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), record_id, parents[0], shared),
            )
    finally:
        conn.close()


def test_conftest_schema_matches_migrated_schema(migrated_sqlite_db, tmp_path):
    """MINIMAL_ICDEV_SCHEMA must declare exactly what the migration produces.

    Most of the suite runs against the conftest schema. If it drifts from the
    migration, tests keep passing against a table shape no real database has —
    which is the failure this repo has hit often enough to have a rule about it.
    """
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    conftest_db = tmp_path / "conftest_shape.db"
    conn = sqlite3.connect(str(conftest_db))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()

    migrated = sqlite3.connect(str(migrated_sqlite_db))
    try:
        for table in ("sbom_records", "sbom_components", "sbom_dependencies"):
            from_conftest = _sqlite_columns(conn, table)
            from_migration = _sqlite_columns(migrated, table)
            assert from_conftest == from_migration, (
                f"{table}: conftest MINIMAL_ICDEV_SCHEMA and migration "
                f"{MIGRATION_VERSION} disagree. "
                f"only in conftest={sorted(from_conftest - from_migration)}, "
                f"only in migration={sorted(from_migration - from_conftest)}"
            )
    finally:
        conn.close()
        migrated.close()


# ---------------------------------------------------------------------------
# PostgreSQL — the primary backend, and the only place the @pg-only branch runs
# ---------------------------------------------------------------------------
def _pg_available() -> bool:
    if os.environ.get("ICDEV_STORAGE_BACKEND", "").lower() != "postgresql":
        return False
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _pg_available(),
    reason="live PostgreSQL not configured (set ICDEV_STORAGE_BACKEND=postgresql)",
)
def test_every_new_column_round_trips_on_postgresql():
    """Same round-trip against PostgreSQL, reading the live information_schema."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        def live_columns(table: str) -> set:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s",
                (table,),
            ).fetchall()
            return {r[0] for r in rows}

        assert set(NEW_RECORD_COLUMNS) | {"supersedes_sbom_id"} <= live_columns(
            "sbom_records"
        )
        assert set(NEW_COMPONENT_COLUMNS) <= live_columns("sbom_components")
        assert {"sbom_record_id", "parent_component_id", "child_component_id"} <= (
            live_columns("sbom_dependencies")
        )

        marker = f"pgtest-{uuid.uuid4()}"
        cols = ["project_id", "version", "format", "file_path"] + list(
            NEW_RECORD_COLUMNS
        )
        vals = [marker, "1.0", "cyclonedx", f"/tmp/{marker}.json"] + list(
            NEW_RECORD_COLUMNS.values()
        )
        row = conn.execute(
            f"INSERT INTO sbom_records ({', '.join(cols)}) "
            f"VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
            vals,
        ).fetchone()
        record_id = row[0]

        comp_id = str(uuid.uuid4())
        ccols = ["id", "component_name", "vendor", "license"] + list(
            NEW_COMPONENT_COLUMNS
        )
        cvals = [comp_id, "requests", "PSF", "Apache-2.0"] + list(
            NEW_COMPONENT_COLUMNS.values()
        )
        conn.execute(
            f"INSERT INTO sbom_components ({', '.join(ccols)}) "
            f"VALUES ({', '.join(['%s'] * len(ccols))})",
            cvals,
        )

        edge_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO sbom_dependencies "
            "(id, sbom_record_id, parent_component_id, child_component_id, "
            " relationship_type, scope, classification, tenant_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (edge_id, record_id, comp_id, comp_id, "depends_on", "runtime",
             "CUI", "acme"),
        )
        conn.commit()

        got = conn.execute(
            f"SELECT {', '.join(NEW_RECORD_COLUMNS)} FROM sbom_records WHERE id = %s",
            (record_id,),
        ).fetchone()
        for i, (column, expected) in enumerate(NEW_RECORD_COLUMNS.items()):
            assert got[i] == expected, f"sbom_records.{column} did not round-trip on PG"

        got = conn.execute(
            f"SELECT {', '.join(NEW_COMPONENT_COLUMNS)} FROM sbom_components "
            "WHERE id = %s",
            (comp_id,),
        ).fetchone()
        for i, (column, expected) in enumerate(NEW_COMPONENT_COLUMNS.items()):
            assert got[i] == expected, (
                f"sbom_components.{column} did not round-trip on PG"
            )

        # Clean up — these are not audit rows, so removing the fixture data is fine.
        conn.execute("DELETE FROM sbom_dependencies WHERE id = %s", (edge_id,))
        conn.execute("DELETE FROM sbom_components WHERE id = %s", (comp_id,))
        conn.execute("DELETE FROM sbom_records WHERE id = %s", (record_id,))
        conn.commit()
    finally:
        conn.close()
