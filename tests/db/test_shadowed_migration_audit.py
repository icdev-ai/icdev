# CUI // SP-CTI
"""Guards for the shadowed-migration audit (mvs-audit-03).

The audit answers "is this grandfathered collision actually harmless?" by
rebuilding both backends from empty. These tests pin the parts that can be
checked without a database — the DDL extraction, the runner's real shadowing
view, and the rule that every allowlist entry carries a reason.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.db import migration_versions as mv
from tools.db import shadowed_migration_audit as audit

# Paths are passed EXPLICITLY rather than relying on the module defaults.
# ``tools.db.*`` resolves through the backward-compat shim to
# ``icdev.tools.db.*``, whose ``_MIGRATIONS_DIR`` and ``_ALLOWLIST_PATH`` are
# computed from its own ``__file__`` and therefore point at the icdev/ mirror —
# a genuinely different set of migration files (17 colliding versions differ).
# Left implicit, these tests would silently assert against the mirror instead of
# the tree the allowlist describes.
REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_MIGRATIONS = REPO_ROOT / "tools" / "db" / "migrations"
CANONICAL_ALLOWLIST = REPO_ROOT / "args" / "migration_duplicate_versions.yaml"


def _load_up(path: Path):
    """Import an ``up.py`` by PATH, not by package name.

    Two copies of this migration exist (``tools/`` and ``icdev/tools/``) under
    the same module name, so a normal import would return whichever landed in
    ``sys.modules`` first and the mirror test would compare a file with itself.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"_up_{abs(hash(str(path)))}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestAllowlistReasons:
    """Criterion 3: the allowlist must say WHY each entry is safe."""

    def test_every_grandfathered_entry_has_a_reason(self):
        # An unexplained entry is the same defect the allowlist exists to stop —
        # an unexamined collision that reads as approved — merely written down.
        assert mv.unexplained_entries(CANONICAL_ALLOWLIST) == []

    def test_gate_fails_on_an_unexplained_entry(self, tmp_path: Path):
        p = tmp_path / "allow.yaml"
        p.write_text(
            'grandfathered:\n  "10":\n    - 010_a\n    - 010_b: "why b is fine"\n',
            encoding="utf-8",
        )
        assert mv.unexplained_entries(p) == ["10/010_a"]

    def test_both_entry_shapes_parse(self, tmp_path: Path):
        p = tmp_path / "allow.yaml"
        p.write_text(
            'grandfathered:\n  "7":\n    - 007_bare\n    - 007_with: "a reason"\n',
            encoding="utf-8",
        )
        # load_allowlist keeps returning plain names, so check() is unaffected.
        assert mv.load_allowlist(p) == {"7": ["007_bare", "007_with"]}
        assert mv.load_allowlist_reasons(p)["7"]["007_with"] == "a reason"

    def test_allowlist_covers_every_real_collision(self):
        # A version on disk that the file does not describe is a new violation.
        result = mv.check(CANONICAL_MIGRATIONS, CANONICAL_ALLOWLIST)
        assert result["new_violations"] == {}
        assert result["passed"] is True


class TestDdlExtraction:
    def test_prose_is_not_mistaken_for_a_table(self):
        # "CREATE TABLE IF NOT EXISTS and CREATE INDEX..." in a docstring used to
        # yield a table named `and`.
        objs = audit.extract_objects(
            audit._python_sql_text(
                '"""Idempotent: uses CREATE TABLE IF NOT EXISTS and CREATE INDEX."""\n'
                'SQL = "CREATE TABLE IF NOT EXISTS real_one (id TEXT)"\n'
            )
        )
        assert objs["tables"] == ["real_one"]

    def test_adjacent_literals_do_not_bridge_into_one_alter(self):
        # conn.execute("ALTER TABLE t ADD COLUMN x") followed by
        # log.append("added_t.x") must not read as one ALTER.
        text = audit._python_sql_text(
            'a = "ALTER TABLE kanban_tasks ADD COLUMN "\n'
            'b = "added_kanban_tasks_marker"\n'
        )
        objs = audit.extract_objects(text)
        assert ("kanban_tasks", "added_kanban_tasks_marker") not in objs["columns"]

    def test_sqlite_rebuild_scratch_table_is_not_a_declared_table(self):
        objs = audit.extract_objects(
            "CREATE TABLE t_new (id TEXT); "
            "ALTER TABLE t_new RENAME TO t;"
        )
        assert "t_new" not in objs["tables"]
        assert objs["transient_tables"] == ["t_new"]

    def test_widening_check_is_attributed_to_the_renamed_target(self):
        checks = audit._widening_checks(
            "CREATE TABLE v_new (kind TEXT CHECK(kind IN ('a','b'))); "
            "ALTER TABLE v_new RENAME TO vendors;"
        )
        assert checks["vendors"]["kind"] == ["a", "b"]


class TestRunnerView:
    def test_runner_shadowing_is_a_subset_of_the_gate_view(self):
        # The gate walks every NNN_ name on disk, which is right for POLICING a
        # collision but mispredicts the outcome when the entry that sorts first
        # is one discover_migrations skips.
        gate = {
            (r["version"], r["shadowed"])
            for r in mv.shadowed_migrations(CANONICAL_MIGRATIONS)
        }
        runner = {
            (r["version"], r["shadowed"])
            for r in audit.runner_shadowed(CANONICAL_MIGRATIONS)
        }
        assert runner <= gate

    def test_entries_the_runner_never_discovers_are_reported(self):
        # A bare NNN_name.py has neither up.sql nor up.py in a directory, so the
        # runner never sees it — dead for a different reason than shadowing.
        invisible = audit.runner_invisible(CANONICAL_MIGRATIONS)
        assert all(name.startswith(tuple("0123456789")) for name in invisible)


class TestFoldedGapsStillDeclared:
    """The six real gaps were folded into one migration — keep it present."""

    FOLD = "20260803204235_mvs_audit_03_shadowed_gaps"

    @pytest.mark.parametrize(
        "obj",
        [
            "sso_providers",
            "sso_sessions",
            "rfi_workbench_sessions",
            "rfi_workbench_sections",
            "rfi_workbench_exports",
            "memory_fts",
            "kanban_task_comments",
        ],
    )
    def test_folded_migration_declares(self, obj: str):
        for root in (REPO_ROOT / "tools", REPO_ROOT / "icdev" / "tools"):
            up = root / "db" / "migrations" / self.FOLD / "up.py"
            assert up.is_file(), f"{up} missing — the mirror must carry it too"
            assert obj in up.read_text(encoding="utf-8"), f"{obj} absent from {up}"

    def test_role_vocabulary_matches_the_dashboard_constant(self):
        # Substring-checking four role names would pass while the CHECK and the
        # RBAC matrix drifted in either direction — and drift between the DB copy
        # and the Python copy is the defect this migration exists to repair. So
        # compare the SETS, per CLAUDE.md's "derive CHECK constraints from Python
        # constants" rule. The migration cannot import the constant at runtime (a
        # migration must stay frozen against a moving codebase), so the tuple is
        # literal and this test is what keeps it honest.
        from tools.dashboard.auth import VALID_DASHBOARD_ROLES

        module = _load_up(CANONICAL_MIGRATIONS / self.FOLD / "up.py")
        assert set(module._ROLES) == set(VALID_DASHBOARD_ROLES)
        # The four PostgreSQL rejected before the fold — named explicitly so a
        # regression points at the roles that actually broke create_user().
        assert {"migration_engineer", "component_admin", "auditor", "ciso"} <= set(module._ROLES)

    def test_mirror_carries_the_same_role_vocabulary(self):
        canonical = _load_up(CANONICAL_MIGRATIONS / self.FOLD / "up.py")
        mirror = _load_up(
            REPO_ROOT / "icdev" / "tools" / "db" / "migrations" / self.FOLD / "up.py"
        )
        assert set(canonical._ROLES) == set(mirror._ROLES)


class TestVendorTypeVocabulary:
    """050_theater_supply_chain was the seventh gap — SQLite only."""

    def test_defense_contractor_is_an_accepted_vendor_type(self):
        # The migration that adds it is shadowed by 050_sg_sio_assessments, and
        # widening a CHECK on SQLite means rebuilding the table — so the fix
        # lives in the CREATE TABLE that fresh SQLite databases actually get.
        import tools.db.init_icdev_db  # noqa: F401 — pins the module under test

        for root in (REPO_ROOT / "tools", REPO_ROOT / "icdev" / "tools"):
            src = (root / "db" / "init_icdev_db.py").read_text(encoding="utf-8")
            line = next(
                ln for ln in src.splitlines() if "vendor_type TEXT CHECK" in ln
            )
            assert "'defense_contractor'" in line, f"missing in {root}"


class TestBootstrapBaselineOrdering:
    """A timestamp id must not sort into the pre-snapshot baseline."""

    def test_timestamp_versions_are_not_swept_into_the_baseline(self):
        from tools.db.bootstrap_pg import baseline_versions

        # "20260803204235" <= "301" is True as STRINGS, which silently marked
        # every timestamp migration applied without running it.
        got = baseline_versions(["001", "301", "302", "20260803204235"], "301")
        assert got == ["001", "301"]
