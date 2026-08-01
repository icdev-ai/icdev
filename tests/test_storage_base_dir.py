# CUI // SP-CTI
"""storage._resolve_repo_base must not walk past the project root.

The old implementation was::

    _BASE = Path(__file__).resolve().parent
    while _BASE.name in ("db", "tools", "icdev"):
        _BASE = _BASE.parent

which matches on directory NAME. GitHub Actions checks a repo out to
``/home/runner/work/<repo>/<repo>``, so for this repo every module lives under
two consecutive directories literally named ``icdev``. The loop stripped both
and landed on ``/home/runner/work``; DB_PATH became
``/home/runner/work/data/icdev.db``, which nothing had created, and the health
check reported "Missing 19 tables" with ``tables_found: 0`` while
``init_icdev_db.py`` had just written 525 tables to the real path.

It never reproduced locally: the Windows checkout is ``C:\\AI\\ICDev`` and the
comparison is case-sensitive, so ``"ICDev"`` never matched.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _walk(start: Path) -> Path:
    """The shipped resolution logic, applied to an arbitrary starting dir."""
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").exists() or (candidate / ".git").exists():
            return candidate
        if candidate.name not in ("db", "tools", "icdev"):
            return candidate
    return start


def _make_repo(tmp_path: Path, *segments: str) -> Path:
    """Build <tmp>/<segments...> with a pyproject.toml at the repo root."""
    root = tmp_path
    for seg in segments:
        root = root / seg
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("[project]\nname='icdev'\n", encoding="utf-8")
    return root


def test_ci_double_icdev_checkout_does_not_overwalk(tmp_path):
    """/home/runner/work/icdev/icdev — the shape that actually broke CI."""
    repo = _make_repo(tmp_path, "work", "icdev", "icdev")
    pkg = repo / "tools" / "db"
    pkg.mkdir(parents=True)
    assert _walk(pkg) == repo


def test_ci_double_icdev_checkout_via_the_icdev_mirror(tmp_path):
    """The mirror sits one level deeper: <repo>/icdev/tools/db."""
    repo = _make_repo(tmp_path, "work", "icdev", "icdev")
    pkg = repo / "icdev" / "tools" / "db"
    pkg.mkdir(parents=True)
    assert _walk(pkg) == repo


def test_repo_directory_named_icdev_lowercase(tmp_path):
    repo = _make_repo(tmp_path, "icdev")
    pkg = repo / "tools" / "db"
    pkg.mkdir(parents=True)
    assert _walk(pkg) == repo


def test_mixed_case_checkout_still_resolves(tmp_path):
    """The Windows shape that always worked must keep working."""
    repo = _make_repo(tmp_path, "AI", "ICDev")
    pkg = repo / "tools" / "db"
    pkg.mkdir(parents=True)
    assert _walk(pkg) == repo


def test_git_worktree_marker_is_a_file_not_a_dir(tmp_path):
    """In a git worktree .git is a FILE — .exists() must still anchor on it."""
    repo = tmp_path / "icdev"
    pkg = repo / "tools" / "db"
    pkg.mkdir(parents=True)
    (repo / ".git").write_text("gitdir: ../.git/worktrees/x\n", encoding="utf-8")
    assert _walk(pkg) == repo


def test_both_copies_of_storage_agree_on_base_dir():
    """The tools/ shim and the icdev/ mirror must resolve the same root.

    They disagreeing is the whole defect: init_icdev_db.py wrote one database
    and get_connection read another.
    """
    import importlib

    a = importlib.import_module("tools.db.storage")
    b = importlib.import_module("icdev.tools.db.storage")
    assert str(a.BASE_DIR) == str(b.BASE_DIR)
    assert str(a.DB_PATH) == str(b.DB_PATH)


def test_base_dir_is_the_repo_root_not_its_parent():
    import importlib

    s = importlib.import_module("tools.db.storage")
    assert (Path(s.BASE_DIR) / "pyproject.toml").exists() or (
        Path(s.BASE_DIR) / ".git"
    ).exists(), f"BASE_DIR {s.BASE_DIR} is not a project root"
