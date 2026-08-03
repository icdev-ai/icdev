# CUI // SP-CTI
"""gdx-aud-01 — AI GameDay League append-only declaration vs enforcement.

Migration 136 used to declare all 8 ``gd_ai_*`` tables an "append-only audit
trail (AU)" while none of them were registered in ``APPEND_ONLY_TABLES`` in
``.claude/hooks/pre_tool_use.py``. The declaration was also wrong: five of the
eight are mutated at runtime, so blanket-registering them would have broken the
league.

These tests pin the audited split in three places at once — the migration
docstring, the hook list, and the actual SQL in ``tools/gameday/`` — so the
three can never drift apart again.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py"
MIGRATION_PATH = (
    REPO_ROOT / "tools" / "db" / "migrations" / "136_gameday_ai_league" / "up.py"
)
ICDEV_MIGRATION_PATH = (
    REPO_ROOT
    / "icdev"
    / "tools"
    / "db"
    / "migrations"
    / "136_gameday_ai_league"
    / "up.py"
)
GAMEDAY_DIR = REPO_ROOT / "tools" / "gameday"

# The audited split. Append-only == every write site is a bare INSERT.
APPEND_ONLY = {
    "gd_ai_artifacts",
    "gd_ai_llmops_events",
    "gd_ai_training_pairs",
}
MUTABLE = {
    "gd_ai_tournaments",
    "gd_ai_teams",
    "gd_ai_rounds",
    "gd_ai_judge_evals",
    "gd_ai_leaderboard",
}
ALL_TABLES = APPEND_ONLY | MUTABLE


def _hook_append_only_tables() -> set[str]:
    """Extract APPEND_ONLY_TABLES from the hook via ast (no import side effects)."""
    tree = ast.parse(HOOK_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "APPEND_ONLY_TABLES":
                assert isinstance(node.value, ast.List)
                return {
                    elt.value
                    for elt in node.value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                }
    pytest.fail("APPEND_ONLY_TABLES not found in pre_tool_use.py")


def _migration_declared_append_only() -> set[str]:
    """Parse the gd_ai_* names under the migration docstring's APPEND-ONLY heading."""
    doc = ast.get_docstring(ast.parse(MIGRATION_PATH.read_text(encoding="utf-8")))
    assert doc, "migration 136 must keep its docstring"
    match = re.search(r"^APPEND-ONLY\b.*?$(.*?)^MUTABLE BY DESIGN\b", doc,
                      re.MULTILINE | re.DOTALL)
    assert match, "migration 136 docstring must declare APPEND-ONLY and MUTABLE sections"
    return set(re.findall(r"\bgd_ai_\w+", match.group(1)))


def _gameday_sql() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(GAMEDAY_DIR.glob("*.py"))
    )


def test_migration_declares_the_audited_append_only_set():
    assert _migration_declared_append_only() == APPEND_ONLY


def test_hook_protects_every_append_only_gd_ai_table():
    protected = _hook_append_only_tables()
    missing = APPEND_ONLY - protected
    assert not missing, (
        f"append-only gd_ai_* tables unprotected in pre_tool_use.py: {sorted(missing)}"
    )


def test_hook_does_not_protect_mutable_gd_ai_tables():
    """Registering a mutable table would block the league's own UPDATEs."""
    protected = _hook_append_only_tables()
    wrongly_protected = MUTABLE & protected
    assert not wrongly_protected, (
        "these gd_ai_* tables are UPDATEd at runtime and must not be append-only: "
        f"{sorted(wrongly_protected)}"
    )


def test_declaration_and_enforcement_agree():
    """Every one of the 8 tables is classified exactly once, both sides matching."""
    protected = _hook_append_only_tables()
    assert (ALL_TABLES & protected) == _migration_declared_append_only() == APPEND_ONLY


def test_icdev_migration_mirror_matches():
    assert ICDEV_MIGRATION_PATH.read_text(encoding="utf-8") == MIGRATION_PATH.read_text(
        encoding="utf-8"
    ), "icdev/ mirror of migration 136 is stale — copy tools/ over it"


@pytest.mark.parametrize("table", sorted(APPEND_ONLY))
def test_append_only_tables_have_no_mutating_sql(table):
    """Guard the classification itself: no UPDATE/DELETE/upsert may appear."""
    sql = _gameday_sql()
    assert not re.search(rf"\bUPDATE\s+{table}\b", sql, re.IGNORECASE), (
        f"{table} is registered append-only but tools/gameday/ UPDATEs it"
    )
    assert not re.search(rf"\bDELETE\s+FROM\s+{table}\b", sql, re.IGNORECASE), (
        f"{table} is registered append-only but tools/gameday/ DELETEs from it"
    )
    # ON CONFLICT ... DO UPDATE is an UPDATE in all but name.
    for match in re.finditer(rf"INSERT\s+INTO\s+{table}\b(.*?)(?:\"\"\"|$)", sql,
                             re.IGNORECASE | re.DOTALL):
        assert not re.search(r"ON\s+CONFLICT.*?DO\s+UPDATE", match.group(1),
                             re.IGNORECASE | re.DOTALL), (
            f"{table} is registered append-only but has an upsert (ON CONFLICT DO UPDATE)"
        )


@pytest.mark.parametrize("table", sorted(ALL_TABLES))
def test_every_gd_ai_table_is_in_the_test_schema(table):
    """MINIMAL_ICDEV_SCHEMA must create all 8 tables so league tests run cold."""
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    assert re.search(
        rf"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+{table}\b",
        MINIMAL_ICDEV_SCHEMA,
        re.IGNORECASE,
    ), f"{table} missing from MINIMAL_ICDEV_SCHEMA in tests/conftest.py"
