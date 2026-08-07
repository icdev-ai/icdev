# CUI // SP-CTI
"""A worktree must land somewhere two sessions cannot both pick.

Measured on this repo 2026-08-07: 150 registered worktrees across 22 parent
directories, 118 of them stray. Five basenames collided across parents, and two
simultaneous ``wt-wake2`` checkouts on different branches is how one session's
edits turned up in another session's working tree.

These tests cover the two halves that make the convention hold: the path
resolver (``tools/git/worktree_paths.py``) and the hook that refuses a
``git worktree add`` outside it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py"

sys.path.insert(0, str(REPO_ROOT))
from tools.git.worktree_paths import (  # noqa: E402
    ACTORS,
    actor_root,
    is_sanctioned,
    worktree_path,
    worktree_root,
)


# ---------------------------------------------------------------------------
# Disjointness — the property the whole design rests on

def test_actor_roots_are_disjoint():
    roots = {a: actor_root(a).resolve() for a in ACTORS}
    for a, ra in roots.items():
        for b, rb in roots.items():
            if a >= b:
                continue
            assert not str(ra).startswith(str(rb) + os.sep), f"{a} nested under {b}"
            assert not str(rb).startswith(str(ra) + os.sep), f"{b} nested under {a}"


def test_kanban_and_cli_cannot_collide_on_the_same_slug():
    """The exact 2026-08-07 incident: two 'wt-wake2' worktrees at once."""
    assert worktree_path("kanban", "wt-wake2") != worktree_path("cli", "wt-wake2")


def test_two_cli_sessions_cannot_collide_on_the_same_slug():
    """Interactive sessions reliably pick the same handful of slugs."""
    a = worktree_path("cli", "wt-wake2", session="session-aaa")
    b = worktree_path("cli", "wt-wake2", session="session-bbb")
    assert a != b, "two sessions picking one slug must get separate directories"


def test_unknown_actor_is_refused():
    with pytest.raises(ValueError):
        worktree_path("whoever", "wt-x")


def test_slug_is_path_sanitised():
    p = worktree_path("kanban", "../../escape")
    assert ".." not in p.parts, f"slug escaped its root: {p}"
    assert str(p).startswith(str(actor_root("kanban")))


def test_default_root_is_outside_the_repository():
    """A worktree under the repo is walked by pytest, coherence, and git grep."""
    assert not str(worktree_root().resolve()).startswith(str(REPO_ROOT.resolve()) + os.sep)


def test_root_is_configurable(monkeypatch, tmp_path):
    monkeypatch.setenv("ICDEV_WORKTREE_ROOT", str(tmp_path / "elsewhere"))
    assert str(worktree_root()).startswith(str(tmp_path))


# ---------------------------------------------------------------------------
# Classification

def test_real_stray_locations_are_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("ICDEV_WORKTREE_ROOT", str(tmp_path / "sanctioned"))
    # Every one of these is a real parent directory observed on 2026-08-07.
    for stray in (
        r"C:\Users\schuo\AppData\Local\Temp\claude\wt-wake2",
        r"C:\AI\.worktrees\aca-trn-03",
        r"C:\AI\.wt\tsrdoc-d5-r2",
        r"C:\AI\_wt\idp-gap-01",
        r"C:\Users\schuo\AppData\Local\Temp\icdev-wt\kax-stall-01",
        "/var/tmp/somewhere/wt-x",
    ):
        assert is_sanctioned(stray, repo_root=REPO_ROOT) is False, f"{stray} must be refused"


def test_sanctioned_root_is_accepted(monkeypatch, tmp_path):
    monkeypatch.setenv("ICDEV_WORKTREE_ROOT", str(tmp_path / "sanctioned"))
    assert is_sanctioned(worktree_path("cli", "wt-x"), repo_root=REPO_ROOT) is True
    assert is_sanctioned(worktree_path("kanban", "task-1"), repo_root=REPO_ROOT) is True


def test_runner_legacy_base_stays_sanctioned(monkeypatch, tmp_path):
    """Grandfathered: repointing the live runner mid-flight would orphan its worktrees."""
    monkeypatch.setenv("ICDEV_WORKTREE_ROOT", str(tmp_path / "sanctioned"))
    assert is_sanctioned(REPO_ROOT / ".tmp" / "worktrees" / "obs-cov-02-d5",
                         repo_root=REPO_ROOT) is True


def test_session_scratchpad_is_sanctioned(monkeypatch, tmp_path):
    """Already namespaced by session id, and CLAUDE.md directs sessions there."""
    monkeypatch.setenv("ICDEV_WORKTREE_ROOT", str(tmp_path / "sanctioned"))
    p = Path(r"C:\Users\u\AppData\Local\Temp\claude\c--AI-ICDev\some-uuid\scratchpad\wt-x")
    assert is_sanctioned(p, repo_root=REPO_ROOT) is True


# ---------------------------------------------------------------------------
# Command parsing — wrong answers here block real work, so it fails open

def _target(cmd):
    sys.path.insert(0, str(HOOK.parent))
    import importlib.util
    spec = importlib.util.spec_from_file_location("_ptu", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._worktree_add_target(cmd)


@pytest.mark.parametrize("cmd,expected", [
    ("git worktree add /tmp/wt main", "/tmp/wt"),
    ("git worktree add -b feat/x /tmp/wt origin/main", "/tmp/wt"),
    ("git worktree add --detach /tmp/wt", "/tmp/wt"),
    ("git worktree add --no-checkout /tmp/wt -b feat/x", "/tmp/wt"),
    ("cd /repo && git worktree add -b feat/x /tmp/wt origin/main", "/tmp/wt"),
    ('git worktree add -b feat/x "/tmp/path with space" origin/main', "/tmp/path with space"),
    ("git worktree list", None),
    ("git worktree remove /tmp/wt", None),
    ("echo add worktree", None),
])
def test_worktree_add_target_parsing(cmd, expected):
    assert _target(cmd) == expected


def test_b_flag_value_is_never_mistaken_for_the_path():
    """-b takes a branch name; treating it as the path would block every add."""
    assert _target("git worktree add -b /slashy/branch-name /tmp/wt") == "/tmp/wt"


def test_unparseable_command_fails_open():
    assert _target('git worktree add "unbalanced') is None


def test_windows_backslash_path_survives_parsing():
    """POSIX-mode shlex silently eats backslashes.

    ``C:\\Users\\u\\wt`` parsed to ``C:Usersuwt`` — no longer absolute, so it
    resolved against cwd. A session sitting inside its own scratchpad then saw
    the mangled path as sanctioned and the guard passed every stray through.
    """
    got = _target(r"git worktree add -b feat/x C:\Users\u\AppData\Local\Temp\claude\wt-dup origin/main")
    assert got == r"C:\Users\u\AppData\Local\Temp\claude\wt-dup", got


def test_relative_paths_are_refused_not_resolved_against_cwd():
    """Where the caller stands must not change the verdict.

    Resolving a relative path against cwd meant a session running inside a
    scratchpad worktree sanctioned every relative path, because the resolved
    result inherited '.../claude/.../scratchpad/' from the cwd it was joined to.
    """
    assert is_sanctioned("wt-dup", repo_root=REPO_ROOT) is False
    assert is_sanctioned("../wt-dup", repo_root=REPO_ROOT) is False
    assert is_sanctioned("C:Usersuwt-dup", repo_root=REPO_ROOT) is False


# ---------------------------------------------------------------------------
# End-to-end through the real hook process

def _run_hook(command: str, env_extra=None):
    payload = {"session_id": "t", "tool_name": "Bash", "tool_input": {"command": command}}
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    env.pop("ICDEV_WORKTREE_ROOT", None)
    env.update(env_extra or {})
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=120, env=env,
                       cwd=str(REPO_ROOT))
    return p.returncode, (p.stderr or "")


def test_hook_blocks_a_stray_worktree():
    rc, err = _run_hook(r"git worktree add -b feat/x C:\Users\u\AppData\Local\Temp\claude\wt-dup origin/main")
    assert rc == 2, f"stray worktree was allowed (rc={rc}) {err[:400]}"
    assert "not sanctioned" in err


def test_hook_names_the_path_to_use_instead():
    """A block that does not say what to do instead just gets worked around."""
    _, err = _run_hook(r"git worktree add C:\AI\.worktrees\wt-dup")
    assert "use:" in err and "icdev-worktrees" in err


def test_hook_allows_a_sanctioned_worktree():
    ok_path = worktree_path("cli", "wt-ok", session="sess-1")
    rc, err = _run_hook(f'git worktree add -b feat/x "{ok_path}" origin/main')
    assert rc == 0, f"sanctioned worktree was blocked: {err[:400]}"


def test_hook_allows_the_runners_legacy_base():
    """In-flight kanban tasks must not start failing the moment this ships."""
    legacy = REPO_ROOT / ".tmp" / "worktrees" / "some-task"
    rc, err = _run_hook(f'git worktree add -b kanban/some-task "{legacy}" origin/main')
    assert rc == 0, f"runner's own base was blocked: {err[:400]}"


def test_hook_ignores_unrelated_bash():
    rc, _ = _run_hook("git worktree list --porcelain")
    assert rc == 0


def test_guard_can_be_disabled():
    rc, _ = _run_hook(r"git worktree add C:\AI\.worktrees\wt-dup",
                      env_extra={"ICDEV_WORKTREE_GUARD": "0"})
    assert rc == 0, "ICDEV_WORKTREE_GUARD=0 must disable the guard"
