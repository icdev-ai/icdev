# CUI // SP-CTI
"""A daemon must pick up its own code changes without a human.

pr_watcher was restarted BY HAND four times on 2026-08-09, and between each
restart it ran fixes that had already merged. Twice the board looked broken when
the only fault was a daemon serving code from hours earlier. Fixing the
underlying bug does not help if the fix cannot reach the process that needs it.
"""
from __future__ import annotations

import time
from pathlib import Path

from tools.genesis import code_reload as cr


def test_the_watch_set_is_what_was_actually_imported():
    """Not a hand-maintained list: a change to error_classifier changes
    pr_watcher's behaviour as much as a change to pr_watcher.py, and a list would
    have missed most of a day's merges."""
    snap = cr.snapshot()
    assert snap, "expected some repo modules to be imported"
    root = str(Path(__file__).resolve().parents[2])
    assert all(p.startswith(root) for p in snap), "only repo files may be watched"
    assert any(p.endswith("code_reload.py") for p in snap)


def test_a_changed_mtime_is_detected():
    before = {"/repo/a.py": 100.0, "/repo/b.py": 200.0}
    after = {"/repo/a.py": 100.0, "/repo/b.py": 201.0}
    assert cr.changed_files(before, after) == ["/repo/b.py"]


def test_a_new_import_counts_as_a_change():
    """Lazy imports mean new code can arrive after the baseline was taken."""
    assert cr.changed_files({}, {"/repo/new.py": 1.0}) == ["/repo/new.py"]


def test_a_vanished_file_is_NOT_a_change():
    """An import that disappeared is not the code this process is running, and a
    file deleted mid-write would trigger a restart that fixes nothing."""
    assert cr.changed_files({"/repo/gone.py": 1.0}, {}) == []


# ── the restart decision ────────────────────────────────────────────────────
class _Exec:
    def __init__(self):
        self.calls = []

    def __call__(self, exe, argv):
        self.calls.append((exe, argv))


def test_it_reexecs_when_code_changed():
    ex = _Exec()
    cr.restart_if_code_changed(
        {}, started_at=time.time() - 10_000, execv=ex,
        root=Path(__file__).resolve().parents[2])
    assert ex.calls, "expected a re-exec"
    exe, argv = ex.calls[0]
    assert argv[0] == exe, "must re-exec through the interpreter, not argv[0]"


def test_it_refuses_to_restart_before_the_minimum_uptime():
    """A restart loop is worse than stale code: it never finishes a poll, and
    every cycle looks like a fresh start so the loop itself is hard to see."""
    ex = _Exec()
    changed = cr.restart_if_code_changed(
        {}, started_at=time.time(), execv=ex,
        root=Path(__file__).resolve().parents[2])
    assert ex.calls == []
    assert changed, "it should still report what changed"


def test_nothing_changed_means_nothing_happens():
    root = Path(__file__).resolve().parents[2]
    base = cr.snapshot(root)
    ex = _Exec()
    assert cr.restart_if_code_changed(
        base, started_at=time.time() - 10_000, execv=ex, root=root) == []
    assert ex.calls == []


def test_disabled_never_restarts():
    ex = _Exec()
    assert cr.restart_if_code_changed(
        {}, started_at=time.time() - 10_000, enabled=False, execv=ex) == []
    assert ex.calls == []


def test_a_failed_reexec_does_not_kill_the_daemon():
    """Running stale is bad; dying is worse."""
    def boom(exe, argv):
        raise OSError("execv refused")
    cr.restart_if_code_changed(
        {}, started_at=time.time() - 10_000, execv=boom,
        root=Path(__file__).resolve().parents[2])


def test_both_daemons_check_after_the_work_not_during():
    """A restart mid-dispatch abandons a task the scheduler had just claimed;
    mid-poll it could abandon a merge in flight."""
    root = Path(__file__).resolve().parents[2]
    for rel, marker in (
        ("tools/ci/pr_watcher.py", "report = self.poll_once()"),
        ("tools/genesis/kanban_scheduler.py", "time.sleep(args.interval)"),
    ):
        text = (root / rel).read_text(encoding="utf-8")
        call = text.index("restart_if_code_changed(")
        assert text.index(marker) < call or marker == "time.sleep(args.interval)", rel
        if marker == "time.sleep(args.interval)":
            # the check must come BEFORE the sleep that ends the cycle
            assert call < text.rindex(marker), rel


# ── pulling: the guard is the point, not the pull ───────────────────────────
class _Git:
    """Scripted git. Keyed on the first argument of each call."""

    def __init__(self, **replies):
        self.replies = replies
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        rc, out = self.replies.get(args[0], (0, ""))
        return type("P", (), {"returncode": rc, "stdout": out, "stderr": ""})()

    @property
    def merged(self):
        return any(a[:2] == ["merge", "--ff-only"] for a in self.calls)


def _reset_throttle():
    cr._last_pull = 0.0


def test_it_pulls_when_nothing_local_is_at_risk():
    _reset_throttle()
    g = _Git(**{"rev-parse": (0, "main\n"), "fetch": (0, ""),
                "diff": (0, "tools/ci/pr_watcher.py\n"), "status": (0, "")})
    out = cr.pull_if_safe(runner=g)
    assert out["pulled"] is True and g.merged


def test_it_REFUSES_when_an_incoming_file_is_locally_modified():
    """The whole reason this is guarded: a blind pull in a daemon either fails
    every cycle or clobbers work nobody asked it to touch."""
    _reset_throttle()
    g = _Git(**{"rev-parse": (0, "main\n"), "fetch": (0, ""),
                "diff": (0, "args/projects.yaml\n"),
                "status": (0, " M args/projects.yaml\n")})
    out = cr.pull_if_safe(runner=g)
    assert out["pulled"] is False
    assert out["conflicts"] == ["args/projects.yaml"]
    assert not g.merged


def test_unrelated_local_edits_do_not_block_it():
    """Refusing on ANY dirt would mean never pulling on a working checkout."""
    _reset_throttle()
    g = _Git(**{"rev-parse": (0, "main\n"), "fetch": (0, ""),
                "diff": (0, "tools/ci/pr_watcher.py\n"),
                "status": (0, " M docs/notes.md\n")})
    assert cr.pull_if_safe(runner=g)["pulled"] is True


def test_it_never_moves_a_checkout_that_is_not_on_main():
    """Someone may be working there; a daemon must not move it under them."""
    for head in ("feat/something\n", "HEAD\n", ""):
        _reset_throttle()          # the throttle is module-global; reset per case
        g = _Git(**{"rev-parse": (0, head)})
        out = cr.pull_if_safe(runner=g)
        assert out["pulled"] is False and "not on main" in out["reason"]
        assert not g.merged


def test_a_merge_in_progress_stops_it():
    _reset_throttle()
    g = _Git(**{"rev-parse": (0, "main\n"), "fetch": (0, ""),
                "diff": (0, "a.py\n"), "status": (0, "UU a.py\n")})
    out = cr.pull_if_safe(runner=g)
    assert out["pulled"] is False and out["reason"] == "merge in progress"


def test_a_non_fast_forward_is_never_forced():
    _reset_throttle()
    g = _Git(**{"rev-parse": (0, "main\n"), "fetch": (0, ""),
                "diff": (0, "a.py\n"), "status": (0, ""), "merge": (1, "")})
    assert cr.pull_if_safe(runner=g)["pulled"] is False


def test_a_rename_is_read_from_its_NEW_path():
    """`R  old -> new` would otherwise register the wrong file as modified."""
    _reset_throttle()
    g = _Git(**{"rev-parse": (0, "main\n"), "fetch": (0, ""),
                "diff": (0, "new.py\n"), "status": (0, "R  old.py -> new.py\n")})
    assert cr.pull_if_safe(runner=g)["pulled"] is False


def test_it_is_throttled():
    """A 30s poll does not need to ask the forge every cycle."""
    _reset_throttle()
    g = _Git(**{"rev-parse": (0, "main\n"), "fetch": (0, ""),
                "diff": (0, "a.py\n"), "status": (0, "")})
    assert cr.pull_if_safe(runner=g)["pulled"] is True
    assert cr.pull_if_safe(runner=g)["reason"] == "throttled"


def test_git_unavailable_is_a_reason_not_an_exception():
    """This runs inside someone else's poll loop."""
    _reset_throttle()
    def boom(args, **kw):
        raise OSError("no git")
    assert cr.pull_if_safe(runner=boom)["pulled"] is False
