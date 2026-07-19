# CUI // SP-CTI
"""Regression (nav-misc-04) — tools/kanban/cli.py must resolve the REPO-LOCAL
database, never a globally-installed ``icdev`` shadow package's database.

Two stacked bugs, empirically proven 2026-07-18:
  1. ``_repo_root = Path(__file__).resolve().parents[3]`` was correct only for
     the ``icdev/tools/kanban/`` mirror; from ``tools/kanban/`` it resolved one
     level ABOVE the repo (e.g. ``C:\\ai``), so ``load_dotenv`` loaded nothing.
  2. ``from icdev.tools.db.storage import get_connection`` then bound to a
     globally-installed editable ``icdev`` package from a DIFFERENT repo, so the
     CLI read/wrote THAT repo's database — ``--show``/``--set-status`` printed
     "NOT FOUND" for real tasks and could mutate a foreign board.

Fixes under test:
  * ``_find_repo_root`` marker-walk resolves the true repo root from either the
    ``tools/`` or ``icdev/tools/`` copy and from any cwd.
  * The CLI imports the repo-local ``tools.db.storage`` shim and a fail-loud
    shadow guard (``_storage_shadow_error``) exits 1 if storage resolves outside
    the repo.
  * End-to-end: ``python tools/kanban/cli.py --show <task>`` as a subprocess from
    the repo root AND from an unrelated cwd both resolve the repo-local DB.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import tools.kanban.cli as cli
from tools.kanban.task_factory import create_tasks

REPO_ROOT = Path(cli.__file__).resolve().parents[2]
CLI_PATH = REPO_ROOT / "tools" / "kanban" / "cli.py"


# ---------------------------------------------------------------------------
# Unit — marker-walk repo-root finder
# ---------------------------------------------------------------------------
def _make_repo(tmp_path: Path) -> Path:
    """Build a fake repo tree with the two markers at the root."""
    root = tmp_path / "fakerepo"
    (root / "args").mkdir(parents=True)
    (root / "goals").mkdir(parents=True)
    (root / "args" / "projects.yaml").write_text("x", encoding="utf-8")
    (root / "goals" / "manifest.md").write_text("x", encoding="utf-8")
    return root


def test_find_repo_root_from_canonical_tools_kanban(tmp_path):
    root = _make_repo(tmp_path)
    start = root / "tools" / "kanban"
    start.mkdir(parents=True)
    assert cli._find_repo_root(start) == root


def test_find_repo_root_from_icdev_mirror(tmp_path):
    """The mirror lives at ``icdev/tools/kanban`` with NO markers under icdev/;
    the walk must ascend past icdev/ to the true repo root."""
    root = _make_repo(tmp_path)
    start = root / "icdev" / "tools" / "kanban"
    start.mkdir(parents=True)
    assert cli._find_repo_root(start) == root


def test_find_repo_root_stops_at_nearest_marker_not_above(tmp_path):
    root = _make_repo(tmp_path)
    start = root / "tools" / "kanban"
    start.mkdir(parents=True)
    # Parent of the fake repo must NOT be returned even though it exists.
    assert cli._find_repo_root(start) != root.parent


def test_find_repo_root_fallback_when_no_marker(tmp_path):
    """No markers anywhere -> canonical fallback of two levels up."""
    start = tmp_path / "a" / "b" / "c"
    start.mkdir(parents=True)
    assert cli._find_repo_root(start) == start.parents[1]


def test_real_repo_root_resolves_to_this_checkout():
    """The live module resolved a repo root that actually contains the markers
    and is the checkout this test file lives in."""
    assert (cli._repo_root / "args" / "projects.yaml").exists()
    assert (cli._repo_root / "goals" / "manifest.md").exists()
    assert Path(cli._repo_root).resolve() == REPO_ROOT


# ---------------------------------------------------------------------------
# Unit — fail-loud shadow guard
# ---------------------------------------------------------------------------
def test_shadow_guard_flags_foreign_storage_path(tmp_path):
    foreign = tmp_path / "OtherRepo" / "icdev" / "tools" / "db" / "storage.py"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("# not our storage", encoding="utf-8")
    msg = cli._storage_shadow_error(foreign, REPO_ROOT)
    assert msg is not None
    assert "refusing to run" in msg
    assert str(foreign.resolve()) in msg
    assert str(REPO_ROOT.resolve()) in msg


def test_shadow_guard_allows_repo_local_storage():
    local = REPO_ROOT / "tools" / "db" / "storage.py"
    assert cli._storage_shadow_error(local, REPO_ROOT) is None


def test_live_storage_module_is_repo_local():
    """The storage module the CLI actually imported (``from tools.db import
    storage``) must be inside this repo — the shadow guard must pass for it."""
    assert cli._storage_shadow_error(cli.storage.__file__, cli._repo_root) is None
    assert Path(cli.storage.__file__).resolve().is_relative_to(REPO_ROOT)


# ---------------------------------------------------------------------------
# End-to-end — subprocess resolves the repo-local DB from any cwd
# ---------------------------------------------------------------------------
@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    """Seed one task into a temp SQLite DB via the repo-local task_factory."""
    db = tmp_path / "nav_misc_04.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    created = create_tasks([{
        "id": "navmisc04-seed-01",
        "title": "nav-misc-04 subprocess resolution probe",
        "status": "backlog",
    }])
    assert created == ["navmisc04-seed-01"], f"seed failed: {created}"
    return db


def _run_cli(db: Path, cwd: Path):
    env = os.environ.copy()
    env["ICDEV_STORAGE_BACKEND"] = "sqlite"
    env["ICDEV_DB_PATH"] = str(db)
    # Remove PYTHONPATH so the subprocess relies solely on the marker-walk to put
    # the repo on sys.path — this is exactly the real-world invocation the bug hit.
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), "--show", "navmisc04-seed-01", "--json"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


def test_cli_subprocess_from_repo_root_resolves_local_db(seeded_db):
    proc = _run_cli(seeded_db, cwd=REPO_ROOT)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "navmisc04-seed-01" in proc.stdout
    assert "NOT FOUND" not in proc.stdout + proc.stderr


def test_cli_subprocess_from_unrelated_cwd_resolves_local_db(seeded_db, tmp_path):
    # A cwd that is NOT the repo root and NOT under it — the historic parents[3]
    # + icdev-import bug produced "NOT FOUND" here against a foreign DB.
    other = tmp_path / "some" / "unrelated" / "cwd"
    other.mkdir(parents=True)
    proc = _run_cli(seeded_db, cwd=other)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "navmisc04-seed-01" in proc.stdout
    assert "NOT FOUND" not in proc.stdout + proc.stderr
