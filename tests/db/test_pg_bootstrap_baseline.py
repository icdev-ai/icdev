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


def test_the_column_whose_absence_broke_ci_is_not_in_the_snapshot():
    """inputs_json (migration 306) postdates the snapshot.

    If a regenerated snapshot ever *does* contain it, through_version was bumped
    with it and this test's premise moves — hence the guard is conditional on the
    baseline rather than absolute.
    """
    if bootstrap_pg.snapshot_through_version() >= "306":
        pytest.skip("snapshot regenerated past 306; the column is legitimately present")

    snapshot = bootstrap_pg.SCHEMA_FILE.read_text(encoding="utf-8-sig", errors="replace")
    match = re.search(
        r"CREATE TABLE public\.studio_workflow_runs \((.*?)\n\);", snapshot, re.S
    )
    assert match, "studio_workflow_runs not found in the consolidated snapshot"
    assert "inputs_json" not in match.group(1), (
        "the snapshot already has inputs_json, so through_version is stale-low "
        "rather than correct — bump it to match what the dump contains"
    )
