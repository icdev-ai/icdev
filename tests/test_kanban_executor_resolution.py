# CUI // SP-CTI
"""The scheduler must find its executor, and must know when it is not the owner.

Two defects that together quarantined 25 tasks on 2026-08-01, each recorded as
``no executor available: internet=False, gitlab=unreachable, ollama=unreachable``:

1. ``_resolve_claude_cli`` fell back to ``~/.local/bin/claude`` with no
   extension. On Windows the installed binary is ``claude.EXE``, so the
   fallback could never fire and resolution depended entirely on PATH.

2. ``_foreign_scheduler_pid`` read the lockfile from ``BASE_DIR``. A dashboard
   started inside a git worktree spawns its own scheduler from that worktree,
   which read its own private lockfile, saw no foreign owner, and dispatched
   against the shared board anyway. Three such schedulers were found running
   (tsh-e2e-01, aca-trn-04, and a nested aca-trn-03).

The combination is what did the damage: a worktree-spawned scheduler with a
thinner PATH could not resolve the CLI, the executor chain fell through gitlab
and ollama, and every task it touched was quarantined.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import kanban as k  # noqa: E402


# --------------------------------------------------------------------------
# 1. CLI resolution must not depend on PATH alone
# --------------------------------------------------------------------------

def test_which_hit_is_used_directly(monkeypatch):
    monkeypatch.setattr(k.shutil, "which", lambda _n: r"C:\somewhere\claude.EXE")
    assert k._resolve_claude_cli() == r"C:\somewhere\claude.EXE"


def test_fallback_finds_a_windows_executable_when_path_misses(monkeypatch, tmp_path):
    """The regression: PATH misses, and the binary has an extension."""
    monkeypatch.setattr(k.shutil, "which", lambda _n: None)
    monkeypatch.setattr(k.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD;.BAT")

    binary = tmp_path / ".local" / "bin" / "claude.EXE"
    binary.parent.mkdir(parents=True)
    binary.write_text("stub", encoding="utf-8")

    resolved = k._resolve_claude_cli()
    assert resolved is not None, "a claude.EXE next to the fallback path must be found"
    assert Path(resolved).name.lower() == "claude.exe"


def test_fallback_still_finds_an_extensionless_binary(monkeypatch, tmp_path):
    """POSIX installs have no suffix — that path must keep working."""
    monkeypatch.setattr(k.shutil, "which", lambda _n: None)
    monkeypatch.setattr(k.Path, "home", staticmethod(lambda: tmp_path))
    binary = tmp_path / ".local" / "bin" / "claude"
    binary.parent.mkdir(parents=True)
    binary.write_text("stub", encoding="utf-8")
    assert k._resolve_claude_cli() == str(binary)


def test_no_binary_anywhere_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(k.shutil, "which", lambda _n: None)
    monkeypatch.setattr(k.Path, "home", staticmethod(lambda: tmp_path))
    assert k._resolve_claude_cli() is None
    assert k._claude_code_available() is False


def test_a_cmd_shim_resolves(monkeypatch, tmp_path):
    """npm-style installs ship a .cmd shim rather than an .exe."""
    monkeypatch.setattr(k.shutil, "which", lambda _n: None)
    monkeypatch.setattr(k.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD;.BAT")
    shim = tmp_path / ".local" / "bin" / "claude.CMD"
    shim.parent.mkdir(parents=True)
    shim.write_text("@echo off", encoding="utf-8")
    assert Path(k._resolve_claude_cli()).suffix.lower() == ".cmd"


# --------------------------------------------------------------------------
# 2. Ownership must be asked of the tree the schedulers share
# --------------------------------------------------------------------------

def test_main_worktree_is_the_first_porcelain_entry(monkeypatch):
    listing = (
        "worktree C:/AI/ICDev\nHEAD abc\nbranch refs/heads/main\n\n"
        "worktree C:/AI/ICDev/.tmp/worktrees/aca-trn-04\nHEAD def\ndetached\n\n"
    )

    class _R:
        returncode = 0
        stdout = listing

    monkeypatch.setattr(k, "_main_worktree_cache", None, raising=False)
    monkeypatch.setattr(k.subprocess, "run", lambda *a, **kw: _R())
    assert str(k._main_worktree_root()).replace("\\", "/") == "C:/AI/ICDev"


def test_main_worktree_falls_back_to_base_dir_when_git_fails(monkeypatch):
    def _boom(*a, **kw):
        raise OSError("git missing")

    monkeypatch.setattr(k, "_main_worktree_cache", None, raising=False)
    monkeypatch.setattr(k.subprocess, "run", _boom)
    assert k._main_worktree_root() == k.BASE_DIR


def test_worktree_scheduler_sees_the_main_lockfile(monkeypatch, tmp_path):
    """The core fix: a worktree-spawned scheduler must find the real owner.

    Before this, it read BASE_DIR/.tmp/kanban_scheduler.pid — its own, private,
    usually absent — concluded it was the owner, and dispatched.
    """
    main_root = tmp_path / "repo"
    worktree = main_root / ".tmp" / "worktrees" / "feature"
    (main_root / ".tmp").mkdir(parents=True)
    worktree.mkdir(parents=True)
    (main_root / ".tmp" / "kanban_scheduler.pid").write_text("999999999", encoding="utf-8")

    # Running FROM the worktree: BASE_DIR is the worktree, not the main tree.
    monkeypatch.setattr(k, "BASE_DIR", worktree)
    monkeypatch.setattr(k, "_main_worktree_cache", main_root, raising=False)

    # A dead pid must not be treated as an owner...
    assert k._foreign_scheduler_pid() == 0

    # ...but a live one must be, and it is read from the MAIN tree.
    import psutil  # noqa: PLC0415

    class _P:
        @staticmethod
        def cmdline():
            return ["python", "-m", "tools.genesis.kanban_scheduler"]

    monkeypatch.setattr(psutil, "pid_exists", lambda _p: True)
    monkeypatch.setattr(psutil, "Process", lambda _p: _P())
    assert k._foreign_scheduler_pid() == 999999999, (
        "the worktree scheduler must see the main tree's owner and stand down"
    )


def test_own_pid_is_not_foreign(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    (root / ".tmp").mkdir(parents=True)
    (root / ".tmp" / "kanban_scheduler.pid").write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(k, "_main_worktree_cache", root, raising=False)
    assert k._foreign_scheduler_pid() == 0


def test_missing_lockfile_means_no_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(k, "_main_worktree_cache", tmp_path, raising=False)
    assert k._foreign_scheduler_pid() == 0
