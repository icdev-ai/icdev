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
import shutil
import subprocess
import warnings

import pytest

from tools.db.migration_versions import (
    check,
    discover_versions,
    find_duplicates,
    load_allowlist,
    shadowed_migrations,
    stale_directories,
    stale_directory_message,
)


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def test_no_new_duplicate_migration_versions():
    """The one that matters. A new collision silently discards a migration."""
    result = check()
    if result["stale_directories"]:
        # Not a failure — they are excluded from the scan. Surfaced here because
        # this is where someone chasing a migration problem is already looking.
        warnings.warn(stale_directory_message(result["stale_directories"]), stacklevel=2)
    assert result["passed"], (
        "New duplicate migration version(s) detected: "
        f"{result['new_violations']}\n\n"
        "MigrationRunner keeps only the FIRST migration per version, so the "
        "others will never run and their tables will never exist. Renumber to "
        "the next unused version. Do NOT add to "
        "args/migration_duplicate_versions.yaml — that allowlist freezes "
        "historical damage, it is not a place to park new collisions."
        + ("\n\n" + stale_directory_message(result["stale_directories"])
           if result["stale_directories"] else "")
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
# Stale local directories are not migrations (mvs-guard-02)
# --------------------------------------------------------------------------- #
#
# The scan reads the FILESYSTEM. When a migration is renamed — PR #1199 renamed
# ten — the old directory survives in every pre-existing checkout holding
# nothing but __pycache__. Git reports the tree clean, so nothing signals that
# it is stale, and the scan counts the corpse as a second migration claiming
# that version. Observed 2026-08-02: 027_pipeline_snapshots/ and
# 028_odc_mitre_coverage/ produced two phantom duplicate-version failures
# locally while main was green in CI, and the failure text pointed at the
# migrations rather than at the working tree.


def _git_migrations_repo(root: pathlib.Path) -> pathlib.Path:
    """A real git repo with a migrations/ dir. Returns the migrations dir."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    d = root / "migrations"
    d.mkdir()
    return d


def _add(root: pathlib.Path, *paths: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", "--", *paths],
                   check=True, capture_output=True)


def _make_stale_pycache_dir(migrations_dir: pathlib.Path, name: str) -> pathlib.Path:
    """Exactly what a renamed migration leaves behind: only __pycache__."""
    d = migrations_dir / name / "__pycache__"
    d.mkdir(parents=True)
    (d / "up.cpython-311.pyc").write_bytes(b"\x00\x00\x00\x00")
    return migrations_dir / name


def test_pycache_only_directory_is_not_a_migration(tmp_path: pathlib.Path):
    """The reported failure, reproduced: a rename leaves a phantom duplicate."""
    d = _git_migrations_repo(tmp_path)
    (d / "027_pipeline_snapshot").mkdir()
    (d / "027_pipeline_snapshot" / "up.sql").write_text("-- x", encoding="utf-8")
    _add(tmp_path, "migrations/027_pipeline_snapshot/up.sql")

    # the pre-rename directory, surviving in this checkout only
    _make_stale_pycache_dir(d, "027_pipeline_snapshots")

    assert find_duplicates(d) == {}, "a stale local directory is not a collision"
    assert discover_versions(d)["27"] == ["027_pipeline_snapshot"]
    assert shadowed_migrations(d) == []


def test_stale_directory_is_named_and_its_removal_explained(tmp_path: pathlib.Path):
    d = _git_migrations_repo(tmp_path)
    (d / "028_odc_mitre_coverage_v2").mkdir()
    (d / "028_odc_mitre_coverage_v2" / "up.sql").write_text("-- x", encoding="utf-8")
    _add(tmp_path, "migrations/028_odc_mitre_coverage_v2/up.sql")
    _make_stale_pycache_dir(d, "028_odc_mitre_coverage")

    rows = stale_directories(d)
    assert [r["name"] for r in rows] == ["028_odc_mitre_coverage"]
    assert rows[0]["version"] == "28"

    msg = stale_directory_message(rows)
    assert "028_odc_mitre_coverage" in msg
    assert "stale" in msg.lower(), "must say the working tree is stale"
    assert f"rm -rf {rows[0]['path']}" in msg, "must give the exact path to remove"
    assert "git clean -xdf" not in msg.replace("NOT reach for `git clean -xdf`", ""), (
        "must not hand out a blanket clean — it would also delete a migration "
        "the author has not `git add`ed yet, which is listed here too"
    )
    assert "git add" in msg, "must say what to do if the directory is yours"
    # The whole point: it must not read as a version collision. The original
    # failure text blamed the migrations; this has to blame the working tree.
    assert "do NOT collide" in msg
    assert "duplicate" not in msg.lower()


def test_check_reports_a_stale_directory_rather_than_failing(tmp_path: pathlib.Path):
    """Green gate, plus the artifact surfaced — not a red gate blaming main."""
    d = _git_migrations_repo(tmp_path)
    (d / "027_real").mkdir()
    (d / "027_real" / "up.sql").write_text("-- x", encoding="utf-8")
    _add(tmp_path, "migrations/027_real/up.sql")
    _make_stale_pycache_dir(d, "027_renamed_away")

    empty_allowlist = tmp_path / "none.yaml"
    empty_allowlist.write_text("grandfathered: {}\n", encoding="utf-8")

    result = check(migrations_dir=d, allowlist_path=empty_allowlist)
    assert result["passed"] is True
    assert result["new_violations"] == {}
    assert [r["name"] for r in result["stale_directories"]] == ["027_renamed_away"]


def test_an_uncommitted_migration_file_still_collides(tmp_path: pathlib.Path):
    """Only DIRECTORIES are exempted. A new .sql file is a real migration.

    Otherwise the fix would blind the gate to exactly the case it exists for:
    the collision you are about to commit.
    """
    d = _git_migrations_repo(tmp_path)
    (d / "030_first.sql").write_text("-- x", encoding="utf-8")
    _add(tmp_path, "migrations/030_first.sql")
    (d / "030_second.sql").write_text("-- x", encoding="utf-8")  # not added

    assert sorted(find_duplicates(d)["30"]) == ["030_first.sql", "030_second.sql"]
    assert stale_directories(d) == []


def test_scan_fails_open_when_git_cannot_answer(tmp_path: pathlib.Path):
    """No repo, no git binary, no tracked files -> every entry counts.

    Silently dropping migrations because git was unavailable would be a worse
    failure than the phantom duplicate this guards against.
    """
    d = tmp_path / "migrations"  # deliberately NOT a git work tree
    d.mkdir()
    for name in ("031_alpha", "031_beta"):
        (d / name).mkdir()
        (d / name / "up.sql").write_text("-- x", encoding="utf-8")

    assert stale_directories(d) == []
    assert sorted(find_duplicates(d)["31"]) == ["031_alpha", "031_beta"]


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
#: like migrations and will never run. Frozen 2026-08-02, triaged 2026-08-03
#: (mvs-invisible-04). Shrink this list; never grow it.
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
#: THE TRIAGE (2026-08-03). Every one of the original 17 was checked against the
#: live PostgreSQL database, because "never ran" and "left a schema gap" are not
#: the same claim — the question is whether the schema arrived by another route.
#: For most of these it did: ``tools/db/schema/pg_consolidated.sql`` is a
#: pg_dump snapshot taken from a database where these had been applied BY HAND
#: (each bare .py has an ``if __name__ == "__main__"`` block), so every fresh
#: bootstrap inherits their objects and only long-lived incrementally-migrated
#: databases can be missing them.
#:
#: Removed because they were REAL gaps, absent from the live database and now
#: converted to ``NNN_name/up.sql`` directories:
#:
#:   200 teams_inbox, 201 mattermost_inbox, 202 github_inbox,
#:   203 gitlab_inbox, 204 skype_inbox
#:
#: Those five are the ones that mattered: nothing outside the invisible
#: migration creates them (``init_icdev_db.py`` has no route at all), the
#: gateway adapters and listeners INSERT into them, and every such write is
#: wrapped in a broad ``except`` that logs a warning and drops the message. So
#: the symptom was silent message loss, not a crash. ``telegram_inbox`` is the
#: control case: same shape, same era, but it exists — which is what made the
#: other five visible as an anomaly rather than a design.
#:
#: KEPT, and why each is benign (verified present on the live database):
#:
#:   149  source_citation_registry + govchain_pending_operations — both present;
#:        also in pg_consolidated.sql and tools/integrity/provenance.py.
#:   150  wf_citations.source_hash — column present.
#:   151  canvas_ai_decisions.decision_hash/previous_decision_hash/signature.
#:   152  memory_entries.decay_weight/classification/compartment.
#:   153  kg_nodes.ontology_id + canvas_kg_nodes.ontology_id.
#:   168  RESOLVED 2026-08-15 — promoted to
#:        20260815191145_seed_canvas_grants_for_existing_tenants and the bare
#:        file deleted, so this version no longer exists in either discoverer.
#:        The entry's original reasoning was "making the runner execute it would
#:        point a data seed at the wrong database", and that turned out not to
#:        hold: canvas_access.grant_access writes through get_connection() and
#:        canvas_access_grants is created by init_icdev_db.py, so the grants land
#:        in the ICDEV database the runner already targets. Only the TENANT LIST
#:        is read from platform.db, read-only. The seed is an upsert
#:        (ON CONFLICT DO UPDATE) and a no-op with no tenants, so running it on
#:        every deployment is safe — and a backfill nobody is told to run is a
#:        backfill that does not happen, which is what this file was.
#:   172  declares NO schema in this database. It copies aac_* rows into the
#:        AI-ify CANVAS db and says so: "any conn passed by a runner is
#:        ignored". Runner-visibility would be actively misleading.
#:   173  TWO files, and the only split verdict. 173_cpmp_obligation_periods.py
#:        is benign (cpmp_contract_periods + three cpmp_contracts columns all
#:        present). 173_white_team_review_type.py was a REAL gap — the live
#:        CHECK constraint rejected 'white_team' while the dashboard API offered
#:        it — fixed by 20260803201015_proposal_reviews_white_team_review_type.
#:        173 stays here because promoting either file to a directory would make
#:        version 173 a DUPLICATE, which is the collision the tests above exist
#:        to prevent, and because the legacy 3-digit range is closed.
#:   177  cpmp_contract_mods — present.
#:   178  cpmp_budget_allocations/_obligations/_tier_history — all present.
#:   179  integrity_{assessments,capabilities,findings,verdicts,authorizations}
#:        and kanban_task_revivals — all present.
#:   205  kanban_tasks.loop_type + adversarial_enabled — both present.
RUNNER_INVISIBLE_VERSIONS = frozenset({
    "149", "150", "151", "152", "153",          # dirs with migration.py
    # 168 removed 2026-08-15 — promoted to a real migration, see the note above.
    # This set may only SHRINK: an entry leaves when the gap is fixed, and a new
    # one is a regression, not a line to add.
    "172", "173", "177", "178", "179",          # bare .py files
    "205",                                      # bare .py file
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
