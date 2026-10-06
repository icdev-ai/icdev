# CUI // SP-CTI
"""AADC ops-config generator: idempotent seeding, unshared defaults, real CLIs.

Found 2026-10-06 by an Academy walkthrough:

* ``create_kanban_tasks`` raw-INSERTed a fresh ``uuid4`` row per task, and the
  page defaults to creating tasks — so every click put another duplicate set on
  the live board. It now seeds through ``task_factory.create_tasks`` with a
  deterministic id and idempotency key, so the second run creates nothing.
* ``_default_tool_config`` wrote ``tool_path``/``docs_link`` into the shared
  module-level ``*_DEFAULTS`` dicts.
* ``args/aadc_node_tool_map.yaml`` carried ``cli_command`` values naming flags
  and functions that do not exist, and they were pasted into task descriptions.
"""
from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path

import pytest
import yaml

import tools.agentic_ai_canvas.ops_config_generator as gen

REPO_ROOT = Path(__file__).resolve().parent.parent
NODE_MAP = REPO_ROOT / "args" / "aadc_node_tool_map.yaml"


def _node_map() -> dict:
    return yaml.safe_load(NODE_MAP.read_text(encoding="utf-8"))["node_tool_map"]


# ---------------------------------------------------------------------------
# (1) Seeding is idempotent and goes through the canonical seeder
# ---------------------------------------------------------------------------


@pytest.fixture
def board(tmp_path, monkeypatch):
    """An empty kanban board on a throwaway SQLite file."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "kanban.db"))
    monkeypatch.setenv("ICDEV_KANBAN_ALLOW_LOCAL_BOARD", "1")
    monkeypatch.setenv("KANBAN_LANDED_CHECK", "off")

    from tools.db.storage import get_connection
    from tools.kanban.init_db import init_kanban_tables

    init_kanban_tables()
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _count(conn) -> int:
    return int(dict(conn.execute("SELECT COUNT(*) AS n FROM kanban_tasks").fetchone())["n"])


def _tasks(design_id: str = "aadc-demo") -> list[dict]:
    node_map = _node_map()
    return [
        gen._make_kanban_task(design_id, "Demo design", ntype, node_map[ntype])
        for ntype in ("drift-detector", "token-budget", "guardrail")
    ]


def test_create_twice_second_run_creates_nothing(board):
    first = gen.create_kanban_tasks(_tasks())
    assert len(first) == 3
    assert _count(board) == 3

    second = gen.create_kanban_tasks(_tasks())
    assert second == []
    assert _count(board) == 3


def test_seeded_rows_carry_source_and_criteria(board):
    created = gen.create_kanban_tasks(_tasks())
    rows = [
        dict(r)
        for r in board.execute(
            "SELECT id, dispatch_source, status, acceptance_criteria FROM kanban_tasks"
        ).fetchall()
    ]
    assert sorted(r["id"] for r in rows) == sorted(created)
    for r in rows:
        assert r["dispatch_source"] == "ops_config_generator"
        assert r["status"] == "backlog"
        assert r["acceptance_criteria"]


def test_task_ids_are_deterministic_opaque_and_design_scoped():
    from tools.kanban.task_identity import SHAPE_OPAQUE, classify_shape

    a = [t["id"] for t in _tasks("design-a")]
    assert a == [t["id"] for t in _tasks("design-a")]
    assert set(a).isdisjoint(t["id"] for t in _tasks("design-b"))
    assert len(set(a)) == len(a)
    for tid in a:
        assert re.fullmatch(r"task-[0-9a-f]{10}", tid)
        assert classify_shape(tid) == SHAPE_OPAQUE


# ---------------------------------------------------------------------------
# (2) Module-level defaults are never mutated
# ---------------------------------------------------------------------------


def test_default_tool_config_does_not_mutate_module_defaults():
    before = {
        name: dict(getattr(gen, name))
        for name in (
            "_DRIFT_DEFAULTS",
            "_TOKEN_BUDGET_DEFAULTS",
            "_GUARDRAIL_DEFAULTS",
            "_CIRCUIT_BREAKER_DEFAULTS",
            "_AUDIT_DEFAULTS",
        )
    }
    entry = {"tool_path": "tools/x.py", "docs_link": "m-x"}
    for ntype in ("drift-detector", "token-budget", "guardrail", "circuit-breaker", "audit-logger"):
        cfg = gen._default_tool_config(ntype, entry)
        assert cfg["tool_path"] == "tools/x.py"
        cfg["enabled"] = False

    for name, snapshot in before.items():
        assert getattr(gen, name) == snapshot, f"{name} was mutated"


def test_default_tool_config_returns_independent_copies():
    a = gen._default_tool_config("drift-detector", {"tool_path": "a.py"})
    b = gen._default_tool_config("drift-detector", {"tool_path": "b.py"})
    assert a is not b
    assert a["tool_path"] == "a.py"
    assert b["tool_path"] == "b.py"


# ---------------------------------------------------------------------------
# (3) Every cli_command in the node map names something real
# ---------------------------------------------------------------------------

_FROM_IMPORT = re.compile(r"from\s+([\w.]+)\s+import\s+(\w+)")


def _module_file(dotted: str) -> Path:
    return REPO_ROOT / (dotted.replace(".", "/") + ".py")


def _top_level_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _declared_flags(path: Path) -> set[str]:
    """Every ``--flag`` string literal passed to an ``add_argument`` call."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    flags: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and str(arg.value).startswith("--"):
                    flags.add(arg.value)
    return flags


def _cli_entries() -> list[tuple[str, str]]:
    return [
        (ntype, entry["cli_command"])
        for ntype, entry in _node_map().items()
        if entry.get("cli_command")
    ]


def test_node_map_has_cli_commands_to_check():
    assert len(_cli_entries()) >= 5


@pytest.mark.parametrize("ntype", sorted(_node_map()))
def test_tool_path_exists(ntype):
    tool_path = _node_map()[ntype]["tool_path"]
    assert (REPO_ROOT / tool_path).is_file(), f"{ntype}: {tool_path} does not exist"


@pytest.mark.parametrize("ntype,command", _cli_entries())
def test_cli_command_resolves(ntype, command):
    argv = shlex.split(command)
    assert argv[0] == "python", f"{ntype}: unexpected launcher {argv[0]!r}"

    if argv[1] == "-c":
        imports = _FROM_IMPORT.findall(argv[2])
        assert imports, f"{ntype}: python -c snippet imports nothing checkable"
        for module, name in imports:
            path = _module_file(module)
            assert path.is_file(), f"{ntype}: module {module} has no file"
            assert name in _top_level_names(path), f"{ntype}: {module} defines no {name}"
        assert "..." not in argv[2], f"{ntype}: snippet is a placeholder, not a command"
        return

    script = REPO_ROOT / argv[1]
    assert script.is_file(), f"{ntype}: {argv[1]} does not exist"
    used = {tok.split("=", 1)[0] for tok in argv[2:] if tok.startswith("--")}
    missing = used - _declared_flags(script)
    assert not missing, f"{ntype}: {argv[1]} declares no {sorted(missing)}"


def test_task_description_omits_cli_block_when_no_cli_command():
    task = gen._make_kanban_task("d1", "Demo", "span-recorder", {"tool_path": "t.py"})
    assert "CLI reference" not in task["description"]
    assert "```bash\n\n```" not in task["description"]
