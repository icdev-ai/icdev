#!/usr/bin/env python3
"""Publish-gate values stay in sync across Python and SQL — CUI // SP-CTI.

`idr_publish_audit` records HITL force-overrides of a publish gate. Its `gate`
CHECK constraint listed only `citation_guard` and `placeholder_guard`, hardcoded
in two places (migration 276 and pg_consolidated.sql) with no Python constant
behind them — contrary to the CLAUDE.md rule that CHECK constraints derive from
a Python constant.

The failure mode that makes this worth a test: adding a gate in code without
widening the constraint does not break at import, or at startup, or in review.
It breaks on the INSERT — at exactly the moment a reviewer force-overrides that
gate, which is the single event that must never go unrecorded (NIST AU).

So: one constant, and a test that the SQL agrees with it.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from tools.quality.citation_grounding import PUBLISH_GATES, publish_gate_check_sql

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CONSOLIDATED = _ROOT / "tools" / "db" / "schema" / "pg_consolidated.sql"
_MIGRATION_300 = _ROOT / "tools" / "db" / "migrations" / "300_idr_publish_audit_cove_guard.sql"
# The CURRENT constraint. Each widening supersedes the last, so only the newest
# migration is expected to render the full PUBLISH_GATES set — older ones are
# historical snapshots and are asserted to be widening-only, not exhaustive.
_MIGRATION = (
    _ROOT / "tools" / "db" / "migrations"
    / "20260814181227_idr_publish_audit_trust_v2_gates" / "up.sql"
)


def _gates_in(sql: str) -> set[str]:
    """Extract the gate values from the CHECK body, in either spelling.

    The migration file is authored as ``gate IN ('a', 'b')``. PostgreSQL stores
    that normalised, so ``pg_dump`` renders the identical constraint as
    ``gate = ANY (ARRAY['a'::text, 'b'::text])`` — which means a regenerated
    consolidated snapshot never contains the ``IN`` form and an ``IN``-only
    reader fails on every regeneration. Both spellings denote the same set, so
    both are parsed here rather than constraining how the snapshot is produced.
    """
    m = re.search(r"gate\s+IN\s*\(([^)]*)\)", sql, re.IGNORECASE)
    if not m:
        m = re.search(r"gate\s*=\s*ANY\s*\(\s*ARRAY\s*\[([^\]]*)\]", sql, re.IGNORECASE)
    assert m, "no `gate IN (...)` or `gate = ANY (ARRAY[...])` CHECK found"
    return set(re.findall(r"'([^']+)'", m.group(1)))


# --------------------------------------------------------------------------- #
# The constant
# --------------------------------------------------------------------------- #


def test_cove_guard_is_a_recognised_gate():
    """The reason this migration exists."""
    assert "cove_guard" in PUBLISH_GATES


def test_the_pre_existing_gates_are_still_there():
    """Widening only — a value previously accepted must stay accepted."""
    assert {"citation_guard", "placeholder_guard"} <= set(PUBLISH_GATES)


def test_gates_are_unique():
    assert len(PUBLISH_GATES) == len(set(PUBLISH_GATES))


def test_trust_v2_guards_are_recognised_gates():
    """TRUST v2 wired the claim tier and the constitution rule set.

    Both had shipped with zero production callers. A guard that can block but
    whose override cannot be recorded is worse than no guard.
    """
    assert {"claim_guard", "constitution_guard"} <= set(PUBLISH_GATES)


def test_unshipped_guards_are_not_declared_early():
    """kg_guard (phase 2) and structure_guard (phase 3) must not appear yet.

    A gate value nothing can emit is the declared-but-unconsumed defect this
    framework exists to catch. Each ships with its own widening migration.
    """
    assert "kg_guard" not in PUBLISH_GATES
    assert "structure_guard" not in PUBLISH_GATES


def test_rendered_sql_is_deterministic():
    """Sorted, so the constant and the SQL can be compared literally."""
    assert publish_gate_check_sql() == (
        "gate IN ('citation_guard','claim_guard','constitution_guard',"
        "'cove_guard','placeholder_guard')"
    )
    assert publish_gate_check_sql("g").startswith("g IN (")


# --------------------------------------------------------------------------- #
# SQL agrees with the constant
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", [_CONSOLIDATED, _MIGRATION], ids=["consolidated", "migration_300"])
def test_sql_matches_the_python_constant(path: pathlib.Path):
    """The actual guard.

    A gate added to PUBLISH_GATES without widening the constraint fails here,
    in CI, instead of failing on a reviewer's override at runtime.
    """
    assert path.is_file(), f"{path.name} missing"
    text = path.read_text(encoding="utf-8", errors="replace")
    if path is _CONSOLIDATED:
        # Scope to the idr_publish_audit table — the file has many CHECKs.
        #
        # Both DDL spellings are accepted because the snapshot contains both:
        # pg_dump emits a plain `CREATE TABLE public.x (`, while the tables
        # hand-carried into the snapshot (declared by init_db/migrations but
        # absent from the canonical database) use `IF NOT EXISTS`. Matching only
        # the latter made this test fail the moment the snapshot was regenerated,
        # reporting "not found" for a table that was present the whole time. The
        # gate list is what this asserts; the CREATE spelling is incidental.
        block = re.search(
            r"CREATE TABLE (?:IF NOT EXISTS )?public\.idr_publish_audit \((.*?)\n\);",
            text, re.S,
        )
        assert block, "idr_publish_audit not found in pg_consolidated.sql"
        text = block.group(1)
    assert _gates_in(text) == set(PUBLISH_GATES)


@pytest.mark.parametrize(
    "path", [_MIGRATION_300, _MIGRATION], ids=["migration_300", "trust_v2"]
)
def test_migration_widens_rather_than_replaces(path: pathlib.Path):
    """An override already recorded must not be invalidated by a widening.

    Every migration in the chain must accept everything its predecessor did.
    """
    sql = path.read_text(encoding="utf-8", errors="replace")
    assert "DROP CONSTRAINT IF EXISTS" in sql, "must be idempotent"
    assert _gates_in(sql) >= {"citation_guard", "cove_guard", "placeholder_guard"}


def test_migration_300_avoided_the_reserved_range():
    """298/299 were offered to the two open PRs still holding 295.

    Resolving one collision must not manufacture another — picking a migration
    number is a repo-wide claim, so open PRs count, not just main.
    """
    assert _MIGRATION_300.name.startswith("300_")


def test_trust_v2_migration_is_timestamp_versioned():
    """The 001-341 sequence is CLOSED (CLAUDE.md).

    Hand-numbering is a read-modify-write across concurrent branches with no
    lock between them; the loser is silently never applied because
    MigrationRunner keeps only the FIRST entry per version.
    """
    version = _MIGRATION.parent.name.split("_", 1)[0]
    assert len(version) == 14 and version.isdigit(), (
        f"{_MIGRATION.parent.name} must use a 14-digit UTC timestamp version"
    )


def test_migrations_are_not_duplicate_versions():
    """Belt and braces against the failure this repo has 70 instances of."""
    from tools.db.migration_versions import find_duplicates

    duplicates = find_duplicates()
    assert "300" not in duplicates
    assert _MIGRATION.parent.name.split("_", 1)[0] not in duplicates
