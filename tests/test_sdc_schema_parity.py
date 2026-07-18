# CUI // SP-CTI
"""Schema-parity guard for the Security Design Canvas (SDC) core schema.

The SDC core tables are declared in FOUR places that must never drift apart:

  (a) tools/security_canvas/db/init_db.py::SCHEMA      — runtime creator+seeder
  (b) tools/db/migrations/272_security_canvas_core.sql — main-chain migration
  (c) tools/db/schema/pg_consolidated.sql              — PG bootstrap baseline
                                                         (SDC additive section)
  (d) tools/security_canvas/db/migrations/001_security_canvas_core.sql
                                                         — canvas-local reference

This test extracts the CREATE TABLE names from each source and asserts:

  * migration (b) == pg_consolidated SDC section (c) == canvas-local (d)
    — exact set equality; these three are byte-for-byte parity mirrors.
  * init_db (a) is a SUBSET of the migration (b) set — the runtime initializer
    may create FEWER tables, but must never create a table the migration lacks.
  * The tables present in the migration but NOT in init_db exactly equal the
    documented EXPECTED_MIGRATION_ONLY constant (currently just ``zig_targets``).

If someone adds a new table to init_db.py without also adding it to migration
272 (and its two mirrors), the subset check FAILS. If someone adds a table to
one mirror but not the others, the equality check FAILS. Either way the drift
is caught in CI before it reaches production.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

_MIGRATION_SQL = _REPO_ROOT / "tools" / "db" / "migrations" / "272_security_canvas_core.sql"
_PG_CONSOLIDATED_SQL = _REPO_ROOT / "tools" / "db" / "schema" / "pg_consolidated.sql"
_CANVAS_LOCAL_SQL = (
    _REPO_ROOT / "tools" / "security_canvas" / "db" / "migrations" / "001_security_canvas_core.sql"
)

# Markers that delimit the SDC additive section inside pg_consolidated.sql, so
# unrelated additive tables (e.g. the Second-Brain user_* block that precedes it)
# do not leak into the comparison.
_PG_SECTION_BEGIN = "Security Design Canvas (SDC) core schema"
_PG_SECTION_END = "END ICDEV ADDITIVE SECTION (Security Design Canvas core schema)"

# Tables intentionally present in migration 272 (and its mirrors) but NOT created
# by the runtime initializer tools/security_canvas/db/init_db.py::SCHEMA.
#
#   zig_targets — external ZIG assessment targets. Written by
#     tools/security_canvas/blueprint.py + zig_portfolio.py and read by the ZIG
#     portfolio / zig.targets IQE collection, but historically its DDL lived only
#     in tests/test_zig_external_targets.py. Migration 272 provisions it so a
#     PG-bootstrapped /security works without runtime init; init_db.py has not
#     (yet) been extended to create it. See PR #384.
#
# NOTE: fedramp_ato_packages / fedramp_controls ARE created by init_db.py, so
# they are NOT migration-only and must not appear here.
EXPECTED_MIGRATION_ONLY = frozenset({"zig_targets"})

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?[\"'`]?(\w+)",
    re.IGNORECASE,
)


def _tables_from_sql(text: str) -> set[str]:
    """Return the set of CREATE TABLE names in a block of SQL text."""
    return {m.group(1).lower() for m in _CREATE_TABLE_RE.finditer(text)}


def _pg_consolidated_section() -> str:
    """Return only the SDC additive section of pg_consolidated.sql."""
    full = _PG_CONSOLIDATED_SQL.read_text(encoding="utf-8")
    begin = full.find(_PG_SECTION_BEGIN)
    assert begin != -1, f"SDC begin marker not found in {_PG_CONSOLIDATED_SQL}"
    end = full.find(_PG_SECTION_END, begin)
    assert end != -1, f"SDC end marker not found in {_PG_CONSOLIDATED_SQL}"
    return full[begin:end]


def _init_db_schema() -> str:
    """Return the SCHEMA DDL string from the runtime initializer."""
    from tools.security_canvas.db import init_db

    return init_db.SCHEMA


# ── Fixtures: extract each source's table set once ────────────────────────────


@pytest.fixture(scope="module")
def migration_tables() -> set[str]:
    return _tables_from_sql(_MIGRATION_SQL.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pg_consolidated_tables() -> set[str]:
    return _tables_from_sql(_pg_consolidated_section())


@pytest.fixture(scope="module")
def canvas_local_tables() -> set[str]:
    return _tables_from_sql(_CANVAS_LOCAL_SQL.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def init_db_tables() -> set[str]:
    return _tables_from_sql(_init_db_schema())


# ── Sanity: every source parsed a non-trivial set ─────────────────────────────


def test_sources_are_non_empty(
    migration_tables, pg_consolidated_tables, canvas_local_tables, init_db_tables
):
    assert "security_designs" in migration_tables
    assert "zig_pillars" in migration_tables
    for name, tables in (
        ("migration 272", migration_tables),
        ("pg_consolidated SDC section", pg_consolidated_tables),
        ("canvas-local 001", canvas_local_tables),
        ("init_db.py::SCHEMA", init_db_tables),
    ):
        assert len(tables) >= 15, f"{name} parsed only {len(tables)} tables — parser broke?"


# ── Three mirrors must be byte-for-byte parity (exact set equality) ───────────


def test_migration_equals_pg_consolidated(migration_tables, pg_consolidated_tables):
    assert migration_tables == pg_consolidated_tables, (
        "Drift between migration 272 and the pg_consolidated.sql SDC section.\n"
        f"  only in migration:      {sorted(migration_tables - pg_consolidated_tables)}\n"
        f"  only in pg_consolidated: {sorted(pg_consolidated_tables - migration_tables)}"
    )


def test_migration_equals_canvas_local(migration_tables, canvas_local_tables):
    assert migration_tables == canvas_local_tables, (
        "Drift between migration 272 and the canvas-local reference copy "
        "(tools/security_canvas/db/migrations/001_security_canvas_core.sql).\n"
        f"  only in migration:    {sorted(migration_tables - canvas_local_tables)}\n"
        f"  only in canvas-local: {sorted(canvas_local_tables - migration_tables)}"
    )


def test_all_three_mirrors_identical(
    migration_tables, pg_consolidated_tables, canvas_local_tables
):
    assert migration_tables == pg_consolidated_tables == canvas_local_tables


# ── init_db is a subset; the delta is exactly the documented allowlist ────────


def test_init_db_is_subset_of_migration(init_db_tables, migration_tables):
    """Any init_db table missing from the migration is a hard FAILURE."""
    extra = init_db_tables - migration_tables
    assert not extra, (
        "init_db.py::SCHEMA creates table(s) absent from migration 272 "
        f"(and its mirrors): {sorted(extra)}. Add them to "
        "tools/db/migrations/272_security_canvas_core.sql (and both mirrors)."
    )


def test_migration_only_tables_match_allowlist(migration_tables, init_db_tables):
    """Tables in the migration but not init_db must equal EXPECTED_MIGRATION_ONLY."""
    migration_only = migration_tables - init_db_tables
    assert migration_only == EXPECTED_MIGRATION_ONLY, (
        "The set of migration-only SDC tables changed.\n"
        f"  actual migration-only:   {sorted(migration_only)}\n"
        f"  expected (allowlist):    {sorted(EXPECTED_MIGRATION_ONLY)}\n"
        "If this is intentional, update EXPECTED_MIGRATION_ONLY in this test "
        "with a comment explaining why the table is migration-only."
    )


def test_allowlist_is_migration_only(migration_tables, init_db_tables):
    """The allowlist must be present in the migration and absent from init_db."""
    assert EXPECTED_MIGRATION_ONLY <= migration_tables, (
        f"EXPECTED_MIGRATION_ONLY not all in migration: "
        f"{sorted(EXPECTED_MIGRATION_ONLY - migration_tables)}"
    )
    assert not (EXPECTED_MIGRATION_ONLY & init_db_tables), (
        f"EXPECTED_MIGRATION_ONLY table(s) unexpectedly created by init_db: "
        f"{sorted(EXPECTED_MIGRATION_ONLY & init_db_tables)}"
    )
