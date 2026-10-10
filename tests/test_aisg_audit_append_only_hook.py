# CUI // SP-CTI
"""aisg_audit is registered in the PreToolUse hook's APPEND_ONLY_TABLES.

CLAUDE.md: "When adding an append-only/immutable DB table, ALWAYS add it to
APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py". aisg_audit carries
DB-level immutability triggers on SQLite (tools/aisg/db/init_db.py) and on
PostgreSQL (migration 20261008003850_aisg_audit_pg_immutability), but the hook
never listed it, so an agent's raw UPDATE/DELETE against it went unrefused.

The hook sits under a dotted directory, so it is loaded by file path and
exercised through its public predicate.
"""

import importlib.util
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "pre_tool_use.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("aisg_pre_tool_use", _HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("verb", ["DELETE FROM aisg_audit WHERE id=1", "UPDATE aisg_audit SET action='x'"])
def test_raw_mutation_of_aisg_audit_is_refused(verb):
    mod = _load_hook()
    assert mod.is_append_only_table_modification(
        "Bash", {"command": f"sqlite3 data/aisg_canvas.db \"{verb}\""}
    ) is True


def test_a_mutable_aisg_table_is_not_refused():
    # Negative control: the canvas's working tables stay writable.
    mod = _load_hook()
    assert mod.is_append_only_table_modification(
        "Bash", {"command": "sqlite3 data/aisg_canvas.db \"UPDATE aisg_roadmaps SET classification='CUI'\""}
    ) is False
