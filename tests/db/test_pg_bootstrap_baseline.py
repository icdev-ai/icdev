# CUI // SP-CTI
"""A migration the snapshot predates must never be marked applied without running.

``bootstrap_pg.py`` builds a fresh PostgreSQL database by loading a point-in-time
pg_dump instead of replaying the (unreplayable) historical migration chain. It
then writes ``schema_migrations`` rows so ``migrate.py --up`` reports no pending
work — and it used to write one for *every* migration on disk, including those
merged after the dump was taken.

The result was a database missing all post-snapshot DDL while asserting it was
fully migrated. Nothing surfaced it: no pending migrations, no error, no failing
test. It reached CI as migrations 302-310 being absent from the E2E database,
which made ``studio_workflow_runs.inputs_json`` not exist, every run POST return
500, and the DWO V&V specs fail in CI while passing locally — where the developer
database *had* been migrated.

These tests are database-free on purpose: the bug lives in which versions get
marked, which is decidable from the migration files and the baseline marker
alone. Requiring a live PostgreSQL is why nothing caught it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools.db import bootstrap_pg

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "tools" / "db" / "migrations"


def _all_versions() -> list[str]:
    """Every migration version on disk, as zero-padded strings."""
    found = set()
    for entry in MIGRATIONS_DIR.iterdir():
        match = re.match(r"^(\d{3})_", entry.name)
        if match and (entry.is_dir() or entry.suffix == ".sql"):
            found.add(match.group(1))
    return sorted(found)


# ── The marker itself ─────────────────────────────────────────────────────────


def test_baseline_marker_exists_and_is_a_version():
    assert bootstrap_pg.META_FILE.exists(), (
        f"{bootstrap_pg.META_FILE.name} records which migrations the snapshot "
        f"already contains; bootstrap cannot be correct without it"
    )
    data = json.loads(bootstrap_pg.META_FILE.read_text(encoding="utf-8"))
    assert str(data["through_version"]).isdigit()
    assert bootstrap_pg.snapshot_through_version() == str(data["through_version"]).zfill(3)


def test_baseline_does_not_claim_migrations_that_do_not_exist():
    """A through_version above every migration on disk means it was guessed."""
    assert bootstrap_pg.snapshot_through_version() <= max(_all_versions())


# ── The defect, in one assertion ──────────────────────────────────────────────


def test_post_snapshot_migrations_are_never_marked_applied():
    through = bootstrap_pg.snapshot_through_version()
    marked = bootstrap_pg.baseline_versions(_all_versions(), through)

    wrongly_marked = [v for v in marked if v > through]
    assert not wrongly_marked, (
        f"versions {wrongly_marked} would be recorded as applied without ever "
        f"running — the exact failure that left CI's database unmigrated"
    )


def test_migrations_after_the_baseline_are_left_pending_to_run():
    """The other half: the tail must NOT be marked, or it will never execute.

    An empty tail is the *correct* state right after the snapshot is regenerated
    and ``through_version`` is bumped to the head migration — there is genuinely
    nothing newer to run. An earlier version of this test asserted the tail was
    non-empty, which made regenerating the snapshot fail the suite: it pinned a
    passing moment rather than the invariant. The invariant is only that nothing
    above the baseline is ever marked, which is vacuously true of an empty tail
    and is what the assertion below states.
    """
    through = bootstrap_pg.snapshot_through_version()
    versions = _all_versions()
    marked = set(bootstrap_pg.baseline_versions(versions, through))

    tail = {v for v in versions if v > through}
    assert not (marked & tail), "post-snapshot migrations must stay pending"


@pytest.mark.parametrize("version", ["302", "999"])
def test_baseline_split_is_a_pure_boundary(version):
    """Guard the guard: a helper that marked nothing would pass the test above."""
    marked = bootstrap_pg.baseline_versions(["300", "301", version], "301")
    assert "301" in marked and "300" in marked, "the baseline itself must be marked"
    assert version not in marked


# ── The concrete regression ───────────────────────────────────────────────────


#: Declared by init_db/migrations but NOT present in the canonical database, so
#: pg_dump does not emit them. They survive only because the snapshot carries
#: them explicitly — a straight re-dump drops them from every fresh install.
_CARRIED_FORWARD = (
    "rag_queries", "rag_citations", "teams_inbox", "mattermost_inbox",
    "github_inbox", "gitlab_inbox", "skype_inbox", "pipeline_snapshots",
    "dm_policy_audit_log", "dd_mapping_sessions", "dd_field_mappings",
    "dd_mapping_transforms",
    # audit_chain_genesis (migration 20260812041301) postdates the snapshot, so
    # it currently reaches a fresh install by *running* rather than from the
    # dump. That path ends the moment through_version is bumped past it — and
    # the canonical database has never run the migration, so a straight re-dump
    # would not emit it either. Carried forward for the same reason as the
    # twelve above. (task trust-anchor-03)
    "audit_chain_genesis",
)


@pytest.mark.parametrize("table", _CARRIED_FORWARD)
def test_regenerating_the_snapshot_does_not_drop_carried_forward_tables(table):
    """Regenerating the snapshot silently deleted these once. Never again.

    The snapshot is *mostly* a pg_dump of the canonical database, but not only
    that: a dozen tables are declared by init_db/migrations and absent from the
    canonical database, so a straight re-dump does not contain them. The first
    regeneration dropped all twelve — no error, no failing query, just twelve
    tables missing from every future fresh install.
    """
    snapshot = bootstrap_pg.SCHEMA_FILE.read_text(encoding="utf-8-sig", errors="replace")
    assert re.search(
        rf"CREATE TABLE (?:IF NOT EXISTS )?(?:public\.)?\"?{table}\"?\s*\(",
        snapshot,
    ), (
        f"{table} is not in the snapshot. If you regenerated it with pg_dump, "
        f"re-append the carried-forward section — pg_dump cannot emit a table "
        f"the canonical database does not have."
    )


def test_carried_forward_tables_are_ordered_for_their_foreign_keys():
    """dd_field_mappings references dd_mapping_sessions; order is not cosmetic.

    Appending these alphabetically produced a snapshot that failed to load with
    UndefinedTable partway through, leaving a half-built database.

    The rule is about an INLINE ``REFERENCES``: a CREATE TABLE that names its
    parent in the column list needs the parent to exist first. pg_dump never
    inlines a foreign key -- it emits every FK as a trailing
    ``ALTER TABLE ... ADD CONSTRAINT``, after all tables -- so once the
    canonical database carries these tables, the dump region legitimately lists
    them alphabetically (child first) and the FK still arrives. Either the
    inline form is correctly ordered, or the FK is declared by a later ALTER;
    a snapshot with NEITHER has lost the constraint.
    """
    snapshot = bootstrap_pg.SCHEMA_FILE.read_text(encoding="utf-8-sig", errors="replace")

    parent = re.search(
        r"CREATE TABLE (?:IF NOT EXISTS )?(?:public\.)?\"?dd_mapping_sessions\"?\s*\(", snapshot
    )
    assert parent, "dd_mapping_sessions missing from snapshot"

    child_blocks = list(re.finditer(
        r"CREATE TABLE (?:IF NOT EXISTS )?(?:public\.)?\"?dd_field_mappings\"?\s*\((.*?)\n\);",
        snapshot, re.S,
    ))
    assert child_blocks, "dd_field_mappings missing from snapshot"

    inline = [m for m in child_blocks if re.search(r"REFERENCES\s+(?:public\.)?dd_mapping_sessions", m.group(1))]
    for m in inline:
        assert m.start() > parent.start(), (
            "dd_mapping_sessions must be created before a dd_field_mappings that "
            "inlines a REFERENCES to it"
        )
    deferred = re.search(
        r"ALTER TABLE (?:ONLY )?public\.dd_field_mappings\s+ADD CONSTRAINT \S+ FOREIGN KEY \(session_id\) "
        r"REFERENCES public\.dd_mapping_sessions",
        snapshot,
    )
    assert inline or deferred, (
        "no CREATE TABLE of dd_field_mappings inlines its session_id foreign key and "
        "no trailing ALTER declares it -- the constraint has been lost"
    )


# -- The 173-column hole (2026-08-21) ------------------------------------------
#
# The snapshot was a pg_dump taken on 2026-07-26 (through_version 301) and was
# then hand-extended for four weeks while the canonical database kept moving.
# Measured against the canonical database on 2026-08-21, a database bootstrapped
# from it was short 173 columns across 102 tables -- among them
# dic_chunk_links.chunk_hash (migration 267, which the runner reported as
# applied because bootstrap had STAMPED it) and twelve kanban_tasks columns
# (last_heartbeat_at, max_runtime_seconds, idempotency_key, ...). The first
# consumer of a genuinely fresh database, ICDEV[FT]'s icdev_ft, failed on the
# first INSERT the document-intelligence ingest made. Nothing in CI saw it:
# the CI database is built by init_db first and only MARKED by bootstrap, so
# the snapshot's own contents had not been exercised by anything since July.
#
# These pin two columns that were measured absent, one per failure mode, so a
# regenerated snapshot that drops them -- or a through_version bumped past a
# migration whose DDL is not actually inside -- goes red here instead of on the
# next fresh deployment.

def _snapshot_table_body(table: str) -> str:
    snapshot = bootstrap_pg.SCHEMA_FILE.read_text(encoding="utf-8-sig", errors="replace")
    m = re.search(
        rf"CREATE TABLE (?:IF NOT EXISTS )?public\.{table} \((.*?)\n\);", snapshot, re.S
    )
    assert m, f"{table} not found in the consolidated snapshot"
    body = m.group(1)
    # columns a later hand-maintained section adds to the same table
    body += "\n".join(re.findall(
        rf"ALTER TABLE public\.{table} ADD COLUMN IF NOT EXISTS (\w+)", snapshot
    ))
    return body


def test_snapshot_carries_the_column_migration_267_adds():
    """dic_chunk_links.chunk_hash: a legacy migration the baseline marks applied.

    bootstrap marks every version <= through_version as applied WITHOUT running
    it, so a column a marked migration adds exists on a fresh database only if
    the snapshot carries it. 267 is below the baseline; its column must be in.
    """
    assert bootstrap_pg.snapshot_through_version() >= "267"
    assert re.search(r"\bchunk_hash\b", _snapshot_table_body("dic_chunk_links")), (
        "dic_chunk_links.chunk_hash (migration 267) is missing from the snapshot "
        "while 267 is marked applied -- ingest_orchestrator's INSERT fails on a fresh database"
    )


@pytest.mark.parametrize("column", ["last_heartbeat_at", "max_runtime_seconds", "idempotency_key"])
def test_snapshot_carries_the_kanban_runtime_columns(column):
    """kanban_tasks columns declared by init_db and added to the canonical database
    at runtime -- no migration anywhere in the tree ALTERs them in, so a fresh
    database gets them from the snapshot or not at all."""
    assert re.search(rf"\b{column}\b", _snapshot_table_body("kanban_tasks")), (
        f"kanban_tasks.{column} is missing from the snapshot; the scheduler and "
        f"pr_watcher read it on every poll"
    )


def test_every_column_migration_308_adds_is_reachable_by_some_path():
    """The gap the regenerated snapshot exposed, and why it was invisible.

    `schema_migrations` recorded 308 as applied on the canonical database via a
    `squashed-308` row — the baseline marker bootstrap writes — so its DDL never
    ran. Five of its eight columns existed anyway because migration 310 adds them
    conditionally; the other three did not.

    Nothing failed, because the readers default instead of raising:

        event_dispatch.py:230   workflow_il = trigger.get("workflow_il") or "IL6"

    IL6 is the top of IL_ORDER, so classification_allows(any, "IL6") is true —
    a missing column makes the classification gate fail OPEN rather than refuse.

    A fresh install gets these from init_db.py; a snapshot-bootstrapped one needs
    migration 312. This asserts both paths declare them, since either alone
    leaves half the installations silently permissive.
    """
    import sqlite3
    from pathlib import Path

    from tools.studio.init_db import STUDIO_TABLES

    expected = {
        "studio_event_sources": {"max_il"},
        "studio_workflow_triggers": {"workflow_il", "project_id"},
    }

    for table, cols in expected.items():
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(STUDIO_TABLES[table])
            declared = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        finally:
            conn.close()
        missing = cols - declared
        assert not missing, f"init_db does not declare {table}.{sorted(missing)}"

    up = (Path(__file__).resolve().parents[2]
          / "tools/db/migrations/312_studio_trigger_dispatch_backfill/up.py")
    assert up.exists(), "migration 312 is missing — snapshot-built databases stay permissive"
    text = up.read_text(encoding="utf-8")
    for table, cols in expected.items():
        for col in cols:
            assert col in text, f"312 must backfill {table}.{col}"


def test_the_column_whose_absence_broke_ci_agrees_with_the_baseline():
    """inputs_json (migration 306) and through_version must tell the same story.

    Below 306 the snapshot must NOT contain the column: if it does, the marker
    is stale-low rather than correct. At or above 306 it MUST: if it does not,
    the marker was bumped past DDL the dump never contained, which is exactly
    the "too HIGH" failure that cost CI every migration 302-310 on 2026-07-29.
    Two sides, no skip -- a skipped gated test satisfies the coverage claim
    while asserting nothing.
    """
    snapshot = bootstrap_pg.SCHEMA_FILE.read_text(encoding="utf-8-sig", errors="replace")
    match = re.search(
        r"CREATE TABLE public\.studio_workflow_runs \((.*?)\n\);", snapshot, re.S
    )
    assert match, "studio_workflow_runs not found in the consolidated snapshot"
    present = "inputs_json" in match.group(1)
    if bootstrap_pg.snapshot_through_version() >= "306":
        assert present, (
            "through_version is at or past 306 but the snapshot lacks "
            "studio_workflow_runs.inputs_json -- the marker claims DDL the dump does not contain"
        )
    else:
        assert not present, (
            "the snapshot already has inputs_json, so through_version is stale-low "
            "rather than correct -- bump it to match what the dump contains"
        )
