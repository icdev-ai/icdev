"""Manual Build is ONE state, wherever you ask from (kpr-rvfy-10).

`_ROOT` was `Path(__file__).resolve().parents[2]` -- a self-root (xit-decl-03).
It answers "which copy of this file am I", and in a git WORKTREE that is the
worktree, not the repository the scheduler runs in.

MEASURED 2026-08-30: `--build-mode status` reported MANUAL from C:/AI/ICDev and
AUTOMATIC from a worktree of it, in the same minute, while dispatch was
genuinely paused. Two failures follow, and the second is the dangerous one:

  * an operator in a worktree is TOLD the runner is live when it is not;
  * `--build-mode manual` run there writes a flag the scheduler never reads, so
    the pause silently does nothing -- and "I paused it" is exactly the belief
    under which someone then edits a branch the runner is about to dispatch onto.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from tools.kanban import build_mode as bm


def test_the_flag_lives_in_the_main_checkout_not_the_calling_copy():
    """The whole point: one state, one file."""
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=str(bm.repo_root(bm.__file__)),
        capture_output=True, text=True, timeout=10,
    )
    if common.returncode != 0 or not common.stdout.strip():
        return  # not a git checkout (tarball / container): the fallback is the self-root
    main_repo = Path(common.stdout.strip()).resolve().parent
    assert bm._FLAG.parent.parent == main_repo, (
        f"the flag resolved to {bm._FLAG}, which is not under the main checkout "
        f"{main_repo}. In a worktree that reads a different file, so the same "
        f"command reports a different mode -- and a pause set there is invisible "
        f"to the scheduler."
    )


def test_the_env_override_still_wins():
    """A deployment that keeps state elsewhere must not be overridden by the
    git lookup. Read at import, so this asserts the precedence in the source."""
    src = Path(bm.__file__).read_text(encoding="utf-8")
    i_env = src.index("KANBAN_BUILD_MODE_FLAG")
    i_root = src.index("_ROOT / \"data\"")
    assert i_env < i_root, "the env override must be consulted before the derived path"


def test_no_git_falls_back_rather_than_raising(monkeypatch):
    """A source tarball or a container with no git binary must still boot: the
    module's posture throughout is never to raise and never to wedge dispatch."""
    def _boom(*_a, **_kw):
        raise FileNotFoundError("git not installed")

    monkeypatch.setattr(bm.subprocess, "run", _boom, raising=False)
    assert bm._main_checkout() == bm.repo_root(bm.__file__)


def test_a_git_failure_falls_back_rather_than_raising(monkeypatch):
    """Not a repository at all -- rc != 0 rather than an exception."""
    def _fail(*_a, **_kw):
        return subprocess.CompletedProcess([], 128, stdout="", stderr="not a git repository")

    monkeypatch.setattr(bm.subprocess, "run", _fail, raising=False)
    assert bm._main_checkout() == bm.repo_root(bm.__file__)


def test_is_manual_never_raises_whatever_the_flag_holds(tmp_path, monkeypatch):
    """Unchanged contract: an unreadable flag reports AUTOMATIC, because the
    failure that is hard to notice is the one where nothing happens."""
    bad = tmp_path / "corrupt.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(bm, "_FLAG", bad)
    assert bm.is_manual() is True          # present-but-corrupt is still a flag
    monkeypatch.setattr(bm, "_FLAG", tmp_path / "absent.json")
    assert bm.is_manual() is False


def test_the_fallback_is_repo_root_and_never_a_self_root():
    """The first version of this fix fell back to `parents[2]` -- reintroducing,
    as the safety net, the exact defect being repaired. The self-root census
    caught it on CI; this pins it so the next edit cannot quietly restore it.

    Read from the AST with the DOCSTRING REMOVED. A plain substring scan fails
    on its own explanation: the function's docstring names `parents[2]` while
    describing why it must not appear, so the naive check reports the defect it
    is documenting. The code is what has to be clean, not the prose about it.
    """
    import ast

    tree = ast.parse(Path(bm.__file__).read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_main_checkout"
    )
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]  # drop the docstring
    code = ast.unparse(ast.Module(body=body, type_ignores=[]))
    assert "repo_root(__file__)" in code, "_main_checkout no longer uses the one resolver"
    assert "parents[2]" not in code, (
        "_main_checkout computes the root from this file's location again. That is the "
        "defect this module was changed to remove; use icdev.core.paths.repo_root."
    )
