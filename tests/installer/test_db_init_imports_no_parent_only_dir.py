# CUI // SP-CTI
"""`icdev-init-db` must not import from a directory the PyPI wheel excludes.

sync_package_tree.PARENT_ONLY_DIRS (govcon, saas, ...) are never packaged. When
init_icdev_db.py started importing tools.govcon.compliance_matrix_schema at
module load (rmf-rfp-01), every `pip install icdev` user's first
`icdev-init-db` raised ModuleNotFoundError -- caught only by the 1.2.43 release
smoke test, a month after the import landed. This pins it at commit time.

Scope: the DB init module and the migrations tree, which both run on a pip
install. The AST walk sees function-local imports too -- a migration runs its body.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tools.installer.sync_package_tree import PARENT_ONLY_DIRS

REPO_ROOT = Path(__file__).resolve().parents[2]


_IMPORT_ERRORS = {"ImportError", "ModuleNotFoundError"}


def _guarded_imports(tree: ast.AST) -> set:
    """Import nodes inside a `try` that handles ImportError: they degrade, not crash."""
    guarded = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        caught = set()
        for h in node.handlers:
            types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
            caught |= {t.id for t in types if isinstance(t, ast.Name)}
        if caught & _IMPORT_ERRORS:
            guarded |= {id(n) for stmt in node.body for n in ast.walk(stmt)}
    return guarded


def _parent_only_imports(path: Path, root: Path = REPO_ROOT) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    guarded = _guarded_imports(tree)
    hits = []
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        else:
            continue
        for m in mods:
            parts = m.split(".")
            if parts[0] == "icdev":
                parts = parts[1:]
            if len(parts) >= 2 and parts[0] == "tools" and parts[1] in PARENT_ONLY_DIRS:
                hits.append(f"{path.relative_to(root).as_posix()}:{node.lineno}: {m}")
    return hits


def test_init_icdev_db_imports_nothing_the_wheel_excludes():
    assert _parent_only_imports(REPO_ROOT / "tools" / "db" / "init_icdev_db.py") == []


# Pre-existing, found by this test on adoption (2026-10-08). Both import inside
# `up()`, so they fail only when that migration RUNS on a pip install, not at
# discovery. Enumerated by name so the set can only shrink: fix one by moving its
# constants out of the parent-only dir (as compliance_matrix_schema was) and
# delete its line here.
# 20260912122759 left this set in omx-setup-02: its import is now guarded and the
# migration reports a stated skip on a package install.
_KNOWN_MIGRATION_DEBT = {
    "tools/db/migrations/266_proposal_key_personnel/up.py:66: tools.govcon.key_personnel",
}


def test_migrations_import_nothing_the_wheel_excludes():
    hits = []
    for p in sorted((REPO_ROOT / "tools" / "db" / "migrations").rglob("*.py")):
        hits += _parent_only_imports(p)
    assert sorted(set(hits) - _KNOWN_MIGRATION_DEBT) == []


def test_detector_sees_the_original_defect(tmp_path):
    f = tmp_path / "x.py"
    f.write_text(
        "from tools.govcon.compliance_matrix_schema import sql_in_list\n"
        "from icdev.tools.saas import x\n"
        "from tools.db.compliance_matrix_schema import sql_in_list\n",
        encoding="utf-8",
    )
    assert _parent_only_imports(f, root=tmp_path) == [
        "x.py:1: tools.govcon.compliance_matrix_schema",
        "x.py:2: icdev.tools.saas",
    ]


def test_an_import_guarded_by_import_error_degrades_and_is_not_a_hit(tmp_path):
    f = tmp_path / "y.py"
    f.write_text(
        "def up(conn):\n"
        "    try:\n"
        "        from tools.autoresearch.real_mutation import LANES\n"
        "    except ImportError:\n"
        "        return {}\n"
        "    try:\n"
        "        from tools.govcon.x import y\n"
        "    except ValueError:\n"
        "        pass\n",
        encoding="utf-8",
    )
    assert _parent_only_imports(f, root=tmp_path) == ["y.py:7: tools.govcon.x"]
