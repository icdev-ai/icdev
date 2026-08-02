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


# --------------------------------------------------------------------------- #
# The sequential scheme is CLOSED (mvs-alloc-01)
# --------------------------------------------------------------------------- #
#
# Deduping duplicates is a cure; this is the prevention. A 3-digit version has
# to be allocated as "highest on main + 1", which is a read-modify-write across
# every concurrent session with no lock between them. Measured 2026-08-02: one
# branch collided three times in a single session (329, 330, 333), and one of
# those collisions broke main for every other PR until it was renumbered.
#
# New migrations use a 14-digit UTC timestamp (see
# tools/db/migration_runner.py::new_timestamp_version), which two sessions
# cannot pick simultaneously. The legacy range stays exactly as it is — the 317
# existing versions are frozen history, not a debt to repay.

#: Highest legacy 3-digit version in existence. Frozen 2026-08-02.
#: Do NOT raise this to land a migration — use `python tools/db/migrate.py
#: --create "<name>"`, which allocates a timestamp.
LEGACY_VERSION_CEILING = 341


def _legacy_versions() -> list[int]:
    return sorted(
        int(v) for v in discover_versions() if v.isdigit() and len(v) <= 3
    )


def test_no_new_sequential_migration_versions():
    """A new 3-digit migration means someone re-opened the colliding scheme."""
    above = [v for v in _legacy_versions() if v > LEGACY_VERSION_CEILING]
    assert not above, (
        f"New sequential migration version(s): {above}\n\n"
        "The 3-digit scheme is closed — it cannot be allocated safely by "
        "concurrent sessions (three collisions in one session on 2026-08-02, "
        "one of which broke main). Create migrations with:\n"
        "    python tools/db/migrate.py --create \"add my table\"\n"
        "which allocates a YYYYMMDDHHMMSS version instead."
    )


def test_timestamp_versions_cannot_alias_a_legacy_version():
    """A 14-digit id must never normalise onto a 3-digit one.

    The gate strips leading zeros to treat '007' and '7' as the same version.
    That is right for the legacy range, and it is why timestamps are safe: no
    amount of zero-stripping turns a 14-digit id into a 3-digit one.
    """
    versions = discover_versions()
    legacy = {v for v in versions if v.isdigit() and len(v) <= 3}
    stamped = {v for v in versions if v.isdigit() and len(v) == 14}
    assert not (legacy & stamped), "a timestamp id collided with a legacy id"


#: Versions the GATE sees but MigrationRunner does not — i.e. files that look
#: like migrations and will never run. Frozen 2026-08-02 (mvs-invisible-04).
#:
#: Two distinct causes, both silent:
#:
#:   * 149-153 are DIRECTORIES holding ``migration.py``. discover_migrations
#:     only records a directory when ``up.sql`` or ``up.py`` exists
#:     (``if up_file: migrations.append(...)``), so these are dropped without a
#:     word.
#:   * the rest are bare ``NNN_name.py`` FILES. The file branch matches only
#:     ``.sql``; a top-level ``.py`` migration is not a shape the runner has
#:     ever understood.
#:
#: They are frozen rather than fixed here because making 17 never-executed
#: migrations suddenly apply to live databases is a triage job of its own — the
#: same one PR #1199 did for the shadowed set, where 10 of the examined entries
#: turned out to have left real schema gaps. Shrink this list; never grow it.
RUNNER_INVISIBLE_VERSIONS = frozenset({
    "149", "150", "151", "152", "153",          # dirs with migration.py
    "168", "172", "173", "177", "178", "179",   # bare .py files
    "200", "201", "202", "203", "204", "205",   # bare .py files
})


def test_runner_and_gate_agree_on_what_a_migration_is():
    """The two discoverers must not disagree about which entries count.

    They are separate implementations — MigrationRunner walks the directory to
    RUN migrations, migration_versions walks it to POLICE them. A disagreement
    is invisible in both directions: an id shape the runner accepts and the gate
    ignores lets a colliding migration ship unseen; one the gate accepts and the
    runner ignores is a migration that silently never runs.

    The known-invisible set is frozen above. This test exists to stop it growing.
    """
    from tools.db.migration_runner import MigrationRunner

    runner_versions = {m["version"].lstrip("0") or "0"
                       for m in MigrationRunner().discover_migrations()}
    gate_versions = set(discover_versions())

    runner_only = runner_versions - gate_versions
    gate_only = (gate_versions - runner_versions) - RUNNER_INVISIBLE_VERSIONS

    assert not runner_only, (
        f"the runner sees versions the gate does not police: {sorted(runner_only)}\n"
        "A colliding migration in that shape would ship unnoticed."
    )
    assert not gate_only, (
        f"NEW runner-invisible migration(s): {sorted(gate_only)}\n\n"
        "These look like migrations but discover_migrations skips them, so they "
        "will never run and their tables will never exist. Either give the "
        "directory an up.sql/up.py, or convert the bare .py file into a "
        "directory migration. Do NOT add to RUNNER_INVISIBLE_VERSIONS."
    )


def test_known_invisible_migrations_are_still_invisible():
    """Shrinking the frozen set must require deleting the entry, not weakening it.

    If someone fixes one of these (the correct outcome), this fails and points
    at the list — mirroring how the shadowed-migration pins work above.
    """
    from tools.db.migration_runner import MigrationRunner

    runner_versions = {m["version"].lstrip("0") or "0"
                       for m in MigrationRunner().discover_migrations()}
    now_visible = RUNNER_INVISIBLE_VERSIONS & runner_versions
    assert not now_visible, (
        f"version(s) {sorted(now_visible)} now RUN — remove them from "
        "RUNNER_INVISIBLE_VERSIONS."
    )
