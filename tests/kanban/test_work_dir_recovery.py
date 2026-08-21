# CUI // SP-CTI
"""A scheduler restart must not turn correct work into a phantom completion.

`_worktrees` is an in-memory dict, and the scheduler RE-EXECS ITSELF routinely:
`code_reload.restart_if_code_changed` runs inside its poll loop, so every merge
touching its import closure replaces the process and empties that map while
tasks are still in flight.

The old expression at five call sites was::

    work_dir = _worktrees.get(task_id) or str(BASE_DIR)

and the fallback is not harmless. `_verify_claimed_files_exist` asks whether the
files the agent SAID it wrote exist under that directory. An agent works in its
own worktree on its own branch, so under BASE_DIR none of its new files exist,
`existing == 0`, and correct work is rejected as a PHANTOM COMPLETION — a
confidently wrong verdict whose actual cause, a restart minutes earlier, appears
nowhere in it.

This is the failure mode that reads as a real finding, which is why it is worth
a test rather than a comment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import kanban as k  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_map():
    saved = dict(k._worktrees)
    k._worktrees.clear()
    yield
    k._worktrees.clear()
    k._worktrees.update(saved)


def test_the_in_memory_map_is_used_when_present(monkeypatch):
    k._worktrees["t-1"] = "/somewhere/wt"
    assert k._work_dir_for("t-1") == "/somewhere/wt"


def test_a_lost_map_is_rebuilt_from_the_deterministic_path(monkeypatch, tmp_path):
    """THE restart case. The worktree is named from the task id, so it can be
    recovered with no in-memory state at all."""
    wt = tmp_path / "t-2"
    wt.mkdir()
    monkeypatch.setattr(k, "_task_worktree_path", lambda _tid: wt)

    assert k._work_dir_for("t-2") == str(wt), (
        "after a restart the work dir fell back to BASE_DIR, where the agent's "
        "files do not exist — its correct work would be called phantom"
    )


def test_the_recovery_repopulates_so_later_checks_agree(monkeypatch, tmp_path):
    """Several checks in one run ask this question. If only the first recovered,
    the others would disagree with it about the same task."""
    wt = tmp_path / "t-3"
    wt.mkdir()
    monkeypatch.setattr(k, "_task_worktree_path", lambda _tid: wt)

    k._work_dir_for("t-3")
    assert k._worktrees.get("t-3") == str(wt)


def test_base_dir_remains_the_fallback_when_there_is_no_worktree(monkeypatch, tmp_path):
    """A task that genuinely has no worktree directory still needs an answer —
    the fix narrows the fallback, it does not remove it."""
    monkeypatch.setattr(k, "_task_worktree_path", lambda _tid: tmp_path / "absent")
    assert k._work_dir_for("t-4") == str(k.BASE_DIR)


def test_an_unresolvable_path_never_raises(monkeypatch):
    """Recovery is best-effort. A task id the registry cannot place must not
    take down verification."""
    def _boom(_tid):
        raise RuntimeError("no repo target")

    monkeypatch.setattr(k, "_task_worktree_path", _boom)
    assert k._work_dir_for("t-5") == str(k.BASE_DIR)


def test_no_call_site_still_reads_the_map_directly():
    """Five sites shared the old expression. One left behind would keep the bug
    at whichever check it guards, and that check would be the only one to
    disagree — the hardest shape to diagnose."""
    import io
    import tokenize

    # CODE ONLY. `_work_dir_for`'s own docstring quotes the old expression to
    # explain what it replaced, so grepping raw source would fail on the very
    # sentence documenting the fix. Strip string literals and comments first.
    path = Path(k.__file__)
    code = []
    with io.open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                continue
            code.append(tok.string)
    joined = " ".join(code)

    assert "_worktrees . get ( task_id ) or str ( BASE_DIR )" not in joined, (
        "a call site still falls back to BASE_DIR without attempting recovery"
    )
