# CUI // SP-CTI
"""A green draft must not be able to jam the autonomous pipeline.

GitHub refuses ``gh pr merge`` on a draft ("Pull Request is still a draft"), and
pr_watcher did not fetch ``isDraft`` — so it re-attempted the same refused merge
every cycle, forever, and finished work waited on a human clicking a button. Five
of ten open PRs were in that state on 2026-08-09, and the same jam had happened
twice before.

The fix must not become a way around a deliberate hold, so the interesting tests
here are the REFUSALS: a manual gate row, and a task whose dependency is
unsatisfied — which is exactly how a MANUAL-ONLY card expresses "a human decides
when this ships".
"""
from __future__ import annotations

import tools.ci.pr_watcher as pw


class _Row(dict):
    pass


class _Conn:
    """Answers the two queries the guard makes: gate title, and dependency."""

    def __init__(self, *, title="", dep_status=None, has_dep=False, raises=False):
        self.title = title
        self.dep_status = dep_status
        self.has_dep = has_dep
        self.raises = raises
        self.closed = False

    def execute(self, sql, params=None):
        if self.raises:
            raise RuntimeError("db down")
        self._last = sql
        return self

    def fetchone(self):
        if "depends_on_task_id IS NOT NULL" in self._last:
            return _Row(dep_status=self.dep_status) if self.has_dep else None
        return _Row(title=self.title)

    def close(self):
        self.closed = True


class _Runner:
    """Stands in for subprocess.run; records the argv it was handed."""

    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return type("P", (), {"returncode": self.returncode,
                              "stdout": "", "stderr": "refused"})()


def _watcher(conn, runner, **config):
    cfg = {"auto_merge_enabled": True, "auto_ready_draft_prs": True}
    cfg.update(config)
    w = pw.PRWatcher(config=cfg, get_connection=lambda: conn)
    w._auto_merge_runner = runner
    return w


# ── the fix ────────────────────────────────────────────────────────────────
def test_isdraft_is_actually_requested_from_gh():
    """The guard is unreachable if the field never arrives."""
    assert "isDraft" in pw._GH_JSON_FIELDS


def test_a_green_draft_on_a_free_task_is_marked_ready():
    conn, runner = _Conn(title="Add a thing"), _Runner()
    w = _watcher(conn, runner)
    assert w._mark_ready("https://x/pull/1", "hgx-cx-01", lambda: conn) is True
    assert runner.calls == [["gh", "pr", "ready", "https://x/pull/1"]]


# ── the refusals, which are the point ──────────────────────────────────────
def test_a_manual_gate_is_never_un_drafted():
    conn = _Conn(title="MANUAL-MODE GATE — do not dispatch")
    runner = _Runner()
    w = _watcher(conn, runner)
    assert w._mark_ready("https://x/pull/1", "hgx-gate-02", lambda: conn) is False
    assert runner.calls == [], "no gh call may be made for a gate"


def test_a_task_held_by_an_unsatisfied_dependency_keeps_its_draft():
    """For a MANUAL-ONLY card the draft IS the brake."""
    conn = _Conn(title="AGOV work", has_dep=True, dep_status="in_progress")
    runner = _Runner()
    w = _watcher(conn, runner)
    assert w._mark_ready("https://x/pull/1", "agov-det-01", lambda: conn) is False
    assert runner.calls == []


def test_a_satisfied_dependency_does_not_hold_it():
    conn = _Conn(title="work", has_dep=True, dep_status="done")
    runner = _Runner()
    w = _watcher(conn, runner)
    assert w._mark_ready("https://x/pull/1", "hgx-cx-02", lambda: conn) is True


def test_a_dependency_lookup_failure_errs_toward_HELD():
    """False 'held' costs one human click; false 'free' ships gated work."""
    conn = _Conn(raises=True)
    assert pw._held_by_a_gate(conn, "any") is True


def test_the_toggle_off_leaves_drafts_alone():
    conn, runner = _Conn(title="work"), _Runner()
    w = _watcher(conn, runner, auto_ready_draft_prs=False)
    assert w._mark_ready("https://x/pull/1", "hgx-cx-01", lambda: conn) is False
    assert runner.calls == []


def test_a_refusal_from_gh_is_not_reported_as_success():
    conn, runner = _Conn(title="work"), _Runner(returncode=1)
    w = _watcher(conn, runner)
    assert w._mark_ready("https://x/pull/1", "hgx-cx-01", lambda: conn) is False


def test_dry_run_never_touches_the_forge():
    conn, runner = _Conn(title="work"), _Runner()
    w = _watcher(conn, runner)
    w.dry_run = True
    assert w._mark_ready("https://x/pull/1", "hgx-cx-01", lambda: conn) is True
    assert runner.calls == []
