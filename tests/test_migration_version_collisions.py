# CUI // SP-CTI
"""A duplicate migration version silently loses a migration.

``schema_migrations.version`` is UNIQUE, and
``MigrationRunner.get_pending_migrations`` dedupes by version keeping the FIRST
by sort order. So when two migrations share a number, only one is ever applied —
the other never runs, on any database, forever, with no error.

Measured 2026-08-02: three migrations simultaneously claimed 329
(``329_cortex_asset_token_citation_types.sql``,
``329_insert_column_schema_parity``, ``329_runtime_invocations``). Sort order
would have applied the first and silently dropped the other two.

MEASURED SCALE: 53 version numbers are already duplicated on disk, shadowing
**70 migrations that can never have been applied**. That backlog is
grandfathered below rather than renumbered — these have been in place for a
long time, some of their tables exist by other routes, and renumbering a
migration that DID apply somewhere would re-run it. The gate freezes that debt
and stops NEW collisions; it deliberately does not try to repair the old ones,
which needs per-migration verification against a live schema.
"""
import re
from collections import defaultdict
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[1] / "tools" / "db" / "migrations"

#: Version numbers with a pre-existing duplicate. Do NOT add to this list to get
#: a commit through — renumber the new migration instead. Every entry here is a
#: migration that has already run somewhere and cannot be safely renumbered.
GRANDFATHERED: set = {
    "010", "018", "019", "020", "021", "022", "023", "024", "027", "028", "031", "043",
    "050", "052", "055", "056", "057", "064", "078", "082", "083", "084", "085", "086",
    "107", "108", "113", "120", "135", "136", "139", "158", "161", "163", "173", "179",
    "184", "188", "189", "193", "207", "210", "211", "212", "215", "223", "236", "247",
    "257", "269", "282", "283", "289"
}


def _versions() -> dict:
    by_version = defaultdict(list)
    for entry in sorted(MIGRATIONS.iterdir()):
        if entry.name.startswith("."):
            continue
        m = re.match(r"^(\d+)[_-]", entry.name)
        if not m:
            continue
        by_version[m.group(1)].append(entry.name)
    return by_version


def test_no_new_duplicate_migration_versions():
    """Two migrations sharing a number means one never runs."""
    dupes = {
        v: names for v, names in _versions().items()
        if len(names) > 1 and v not in GRANDFATHERED
    }
    assert not dupes, (
        "duplicate migration version(s) — schema_migrations.version is UNIQUE "
        "and the runner keeps only the FIRST by sort order, so the rest are "
        f"silently never applied: {dupes}. Renumber the newer migration."
    )


def test_the_grandfather_list_only_holds_real_duplicates():
    """A stale exemption makes the gate weaker than it looks."""
    by_version = _versions()
    stale = sorted(v for v in GRANDFATHERED if len(by_version.get(v, [])) <= 1)
    assert not stale, (
        f"grandfathered versions that are no longer duplicated: {stale}. "
        "Remove them so the exemption list keeps meaning something."
    )


def test_the_guard_can_actually_see_a_violation(tmp_path, monkeypatch):
    """A check that cannot fail is not a check."""
    fake = tmp_path / "migrations"
    fake.mkdir()
    (fake / "900_alpha.sql").write_text("-- x", encoding="utf-8")
    (fake / "900_beta.sql").write_text("-- x", encoding="utf-8")

    monkeypatch.setattr(
        __import__(__name__.rsplit(".", 1)[-1] if "." in __name__ else __name__, fromlist=["MIGRATIONS"]),
        "MIGRATIONS", fake, raising=False,
    )
    dupes = {v: n for v, n in _versions().items() if len(n) > 1}
    assert "900" in dupes, "the collision detector does not detect collisions"


@pytest.mark.parametrize("version", ["330"])
def test_the_cortex_citation_migration_has_its_own_version(version):
    """cxo-trust-01's migration must not share a number with another."""
    names = _versions().get(version, [])
    assert len(names) == 1, f"version {version} is claimed by {names}"
    assert "cortex_asset_token" in names[0]
