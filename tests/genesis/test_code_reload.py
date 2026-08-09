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
