# CUI // SP-CTI
"""Every studio_* table must carry the columns the RLS predicate assumes.

`get_connection()` attaches a tenant/classification predicate to every statement
issued inside a request context. A table without those columns therefore cannot
satisfy SQL the storage layer generates for it, and the read raises

    no such column: classification      (SQLite)
    UndefinedColumn: column "classification" does not exist   (PostgreSQL)

**Why a schema test rather than a behavioural one.** The identical read SUCCEEDS
outside a request context, because RLS is only injected under one. So a
pytest-layer test of the calling function passes while the HTTP route 500s — a
durability suite can be fully green and the UI still broken. That trap has now
produced the same defect four times (migrations 305, 309, 311, 326), each time
found in a browser rather than in CI. Asserting the *schema* invariant catches
the next one at the point a table is added, which is the only place it is cheap.

Measured before migration 326 landed, driving a real SecurityContext against the
live database:

    single-tenant ctx        tenant-scoped ctx
    studio_run_memory  ERR   studio_run_memory  ERR   <- neither column
    studio_cases       OK    studio_cases       ERR   <- no tenant_id
    studio_forms       OK    studio_forms       ERR

`studio_run_memory` was the one flagged as out of scope by the tsr-canv-01-d2
seed verification (PR #1140); probing for it found the other nine.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INIT_DB = ROOT / "tools" / "studio" / "init_db.py"
MIGRATION = (
    ROOT / "tools" / "db" / "migrations"
    / "326_studio_rls_column_backfill" / "up.py"
)

RLS_COLUMNS = ("classification", "tenant_id")

#: Tables whose DDL is a plain string entry in init_db.py's schema dict.
_DDL_RE = re.compile(r'"(studio_\w+)":\s*"""(.*?)"""', re.S)


def _ddl_blocks() -> dict:
    return {m.group(1): m.group(2) for m in _DDL_RE.finditer(
        INIT_DB.read_text(encoding="utf-8"))}


def test_the_schema_dict_is_actually_parsed():
    """Guard the guard: a regex that matches nothing would pass everything."""
    blocks = _ddl_blocks()
    assert len(blocks) >= 15, f"only parsed {len(blocks)} studio DDL blocks"
    assert "studio_run_memory" in blocks


@pytest.mark.parametrize("column", RLS_COLUMNS)
def test_every_studio_ddl_declares_the_rls_column(column):
    """A fresh install must not need a migration to be queryable."""
    missing = [t for t, ddl in _ddl_blocks().items() if column not in ddl]
    assert not missing, (
        f"{len(missing)} studio table(s) declare no {column!r} in "
        f"tools/studio/init_db.py: {sorted(missing)}. "
        "get_connection() injects a predicate on that column inside a request "
        "context, so these are unreadable from any authenticated route while "
        "the same read from a script succeeds."
    )


def test_run_memory_specifically_declares_both():
    """The table this migration was written for — pinned by name."""
    ddl = _ddl_blocks()["studio_run_memory"]
    for column in RLS_COLUMNS:
        assert column in ddl, f"studio_run_memory lost {column}"


#: Tables whose `classification` declaration predates the RLS work and differs
#: from the studio_workflows standard. Both are recorded rather than "fixed"
#: because the column is theirs, not the RLS layer's, and changing a DDL only
#: affects fresh installs anyway — so a silent edit here would make the two
#: paths disagree instead of converging.
#:
#:   studio_trigger_events     classification TEXT      nullable, no default
#:   studio_mcp_dispatch_audit classification TEXT NOT NULL   no default
#:
#: The nullable one is the notable one: under the read-down predicate
#: (`classification IN (<dominated set>)`) a NULL never matches, so any
#: trigger event written without a classification is invisible to every caller
#: rather than merely restricted. Not changed here — that is a data-semantics
#: decision for whoever owns the trigger pipeline, and it needs a backfill, not
#: a DDL edit. Flagged so it is not mistaken for coverage.
_KNOWN_CLASSIFICATION_VARIANCES = {
    "studio_trigger_events",
    "studio_mcp_dispatch_audit",
}


def test_classification_declarations_are_consistent():
    """A mismatched default makes pre-existing rows invisible under read-down.

    Asserts the standard for every table except two pre-existing, documented
    variances — so a NEW variance still fails here.
    """
    standard = re.compile(r"classification\s+TEXT\s+NOT NULL\s+DEFAULT\s+'CUI'")
    offenders = [
        table for table, ddl in _ddl_blocks().items()
        if "classification" in ddl
        and table not in _KNOWN_CLASSIFICATION_VARIANCES
        and not standard.search(ddl)
    ]
    assert not offenders, (
        f"{offenders} declare classification non-standardly. Mirror "
        "studio_workflows (TEXT NOT NULL DEFAULT 'CUI') or existing rows stop "
        "matching the read-down predicate."
    )


def test_the_known_variances_are_still_exactly_those_two():
    """Stops the allowlist silently growing, and catches one being fixed."""
    standard = re.compile(r"classification\s+TEXT\s+NOT NULL\s+DEFAULT\s+'CUI'")
    actual = {
        table for table, ddl in _ddl_blocks().items()
        if "classification" in ddl and not standard.search(ddl)
    }
    assert actual == _KNOWN_CLASSIFICATION_VARIANCES, (
        f"classification-declaration variances changed: {sorted(actual)}. "
        "If one was fixed, drop it from _KNOWN_CLASSIFICATION_VARIANCES."
    )


def test_tenant_id_is_nullable():
    """Single-tenant installs carry no tenant, so NOT NULL would break them."""
    for table, ddl in _ddl_blocks().items():
        m = re.search(r"tenant_id\s+TEXT([^,\n]*)", ddl)
        if m:
            assert "NOT NULL" not in m.group(1).upper(), (
                f"{table} declares tenant_id NOT NULL"
            )


# --------------------------------------------------------------------------
# The migration must cover what the DDL cannot: databases that already exist
# --------------------------------------------------------------------------

def test_migration_exists_and_names_both_columns():
    src = MIGRATION.read_text(encoding="utf-8")
    for column in RLS_COLUMNS:
        assert column in src, f"migration 326 does not add {column}"


def test_migration_covers_every_table_the_ddl_does():
    """CREATE TABLE IF NOT EXISTS never alters an existing table.

    So the DDL fix only helps fresh installs; every table it covers needs a
    migration entry too, or existing databases stay broken.
    """
    src = MIGRATION.read_text(encoding="utf-8")
    listed = set(re.findall(r'"(studio_\w+)"', src))
    # Tables that already had their columns from 305/309/311 are legitimately
    # absent from 326 — assert only that nothing is left uncovered by BOTH.
    from_ddl = set(_ddl_blocks())
    earlier = {
        "studio_workflows", "studio_workflow_runs", "studio_workflow_run_steps",
        "studio_event_sources", "studio_workflow_triggers", "studio_trigger_events",
        "studio_mcp_dispatch_audit",
    }
    uncovered = from_ddl - listed - earlier
    assert not uncovered, (
        f"{sorted(uncovered)} are in the studio schema but covered by neither "
        "migration 326 nor an earlier RLS migration"
    )


def test_migration_is_idempotent_in_shape():
    """It must survive a re-run and a hand-patched database."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "IF NOT EXISTS" in src, "PostgreSQL path must tolerate an existing column"
    assert "duplicate column" in src, "SQLite path must tolerate an existing column"


def test_migration_skips_absent_tables():
    """A database that never initialised studio must not fail the chain."""
    assert "_table_exists" in MIGRATION.read_text(encoding="utf-8")
