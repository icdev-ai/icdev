#!/usr/bin/env python3
"""Migration version uniqueness gate — CUI // SP-CTI.

Two migration files claiming the same version number is not a cosmetic clash.
``MigrationRunner.get_pending_migrations`` dedupes by version and keeps the
first by sort order, so every other entry with that version is **skipped
permanently and silently**: no error, no warning, no ``schema_migrations`` row,
and the tables it declares never exist.

As of 2026-07-26 that has already cost 71 migrations. A large share of the ~40
tables found missing from the live database are shadowed this way —
``283_dic_claims.sql`` behind ``283_soar_playbook_runs.sql``,
``282_docmod_nist_pubs.sql`` behind ``282_insider_risk_uba.sql``, and so on.

These tests freeze the existing damage and stop the count growing.
"""
from __future__ import annotations

import pathlib

import pytest

from tools.db.migration_versions import (
    check,
    discover_versions,
    find_duplicates,
    load_allowlist,
    shadowed_migrations,
)


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def test_no_new_duplicate_migration_versions():
    """The one that matters. A new collision silently discards a migration."""
    result = check()
    assert result["passed"], (
        "New duplicate migration version(s) detected: "
        f"{result['new_violations']}\n\n"
        "MigrationRunner keeps only the FIRST migration per version, so the "
        "others will never run and their tables will never exist. Renumber to "
        "the next unused version. Do NOT add to "
        "args/migration_duplicate_versions.yaml — that allowlist freezes "
        "historical damage, it is not a place to park new collisions."
    )


def test_allowlist_matches_disk_exactly():
    """Grandfathered entries must track reality.

    If a duplicate is resolved by renumbering, its allowlist entry should go
    too — otherwise the allowlist slowly becomes fiction and stops meaning
    anything.
    """
    on_disk = find_duplicates()
    allowed = load_allowlist()

    stale = {v: names for v, names in allowed.items() if v not in on_disk}
    assert not stale, (
        f"allowlist lists version(s) that are no longer duplicated: {stale}. "
        "Remove them — a resolved collision should not stay grandfathered."
    )

    for version, names in allowed.items():
        assert on_disk[version] == names, (
            f"version {version}: allowlist has {names}, disk has {on_disk[version]}. "
            "A changed file set means a different migration is now being shadowed."
        )


def test_allowlist_is_not_growing_silently():
    """Pin the historical count so an increase has to be deliberate."""
    allowed = load_allowlist()
    assert len(allowed) <= 54, (
        f"grandfathered duplicate versions grew to {len(allowed)} (was 54). "
        "New collisions must be renumbered, not absorbed."
    )


# --------------------------------------------------------------------------- #
# The detector itself must be trustworthy
# --------------------------------------------------------------------------- #


def test_zero_padding_is_normalised(tmp_path: pathlib.Path):
    """'007_foo' and '7_bar' are the same version to the runner."""
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "007_foo.sql").write_text("-- x", encoding="utf-8")
    (d / "7_bar.sql").write_text("-- x", encoding="utf-8")
    dups = find_duplicates(d)
    assert "7" in dups, f"zero-padded collision missed: {dups}"
    assert sorted(dups["7"]) == ["007_foo.sql", "7_bar.sql"]


def test_detects_a_new_collision(tmp_path: pathlib.Path):
    """Proof the gate fires — not just that it is currently green."""
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "001_first.sql").write_text("-- x", encoding="utf-8")
    (d / "002_alpha.sql").write_text("-- x", encoding="utf-8")
    (d / "002_beta.sql").write_text("-- x", encoding="utf-8")

    empty_allowlist = tmp_path / "none.yaml"
    empty_allowlist.write_text("grandfathered: {}\n", encoding="utf-8")

    result = check(migrations_dir=d, allowlist_path=empty_allowlist)
    assert result["passed"] is False
    assert "2" in result["new_violations"]
    assert result["shadowed_count"] == 1


def test_a_third_file_on_a_known_collision_is_a_new_violation(tmp_path: pathlib.Path):
    """Grandfathering a pair must not license adding a third to it."""
    d = tmp_path / "migrations"
    d.mkdir()
    for n in ("010_a.sql", "010_b.sql", "010_c.sql"):
        (d / n).write_text("-- x", encoding="utf-8")

    allowlist = tmp_path / "allow.yaml"
    allowlist.write_text('grandfathered:\n  "10":\n    - 010_a.sql\n    - 010_b.sql\n', encoding="utf-8")

    result = check(migrations_dir=d, allowlist_path=allowlist)
    assert result["passed"] is False, "adding a third file shadows one more migration"


def test_shadowed_reports_the_losers_not_the_winner(tmp_path: pathlib.Path):
    d = tmp_path / "migrations"
    d.mkdir()
    for n in ("005_aaa.sql", "005_bbb.sql", "005_ccc.sql"):
        (d / n).write_text("-- x", encoding="utf-8")
    rows = shadowed_migrations(d)
    assert len(rows) == 2, "3 files on one version shadows 2 of them"
    assert {r["shadowed"] for r in rows} == {"005_bbb.sql", "005_ccc.sql"}
    assert {r["applied"] for r in rows} == {"005_aaa.sql"}


def test_discover_handles_missing_directory(tmp_path: pathlib.Path):
    assert discover_versions(tmp_path / "nope") == {}


# --------------------------------------------------------------------------- #
# Regression pins for the known damage
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "version,shadowed_file",
    [
        # 283_soar_playbook_runs.sql and 289_twin_compat_reports.sql used to be
        # pinned here. Both were renumbered (to 340 and 332) once their tables
        # were confirmed missing from the live database and each was verified to
        # apply, so they are no longer shadowed — which is this pin working as
        # designed: it failed, and the fix was to remove the entry rather than
        # to weaken the check.
        ("282", "282_insider_risk_uba.sql"),
        ("18", "018_reflex_observations.py"),
        ("19", "019_kanban_verifications"),
    ],
)
def test_known_shadowed_migrations_are_visible(version: str, shadowed_file: str):
    """These declare tables confirmed absent from the live database.

    Pinned so that if someone renumbers them (the correct fix), this test
    fails and prompts removing the corresponding allowlist entry.
    """
    rows = {(r["version"], r["shadowed"]) for r in shadowed_migrations()}
    assert (version, shadowed_file) in rows, (
        f"{shadowed_file} is no longer shadowed at v{version} — if it was "
        "renumbered, drop its entry from args/migration_duplicate_versions.yaml."
    )
