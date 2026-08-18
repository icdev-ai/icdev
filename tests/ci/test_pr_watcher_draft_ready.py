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
    """A three-row board: the task, its scalar parent, and its junction parent.

    The dependency half of the guard is now ``tools.kanban.deps``, which asks
    which dependency actually GATES rather than reading the scalar column
    (kpr-fix-02) — so the stub has to model both mechanisms. ``dep_status=None``
    with ``has_dep`` set is a dangling parent, which blocks.
    """

    _PARENT = "parent-01"
    _JUNCTION_PARENT = "junction-01"

    def __init__(self, *, title="", dep_status=None, has_dep=False, raises=False,
                 parent_title="", junction_status=None):
        self.title = title
        self.dep_status = dep_status
        self.has_dep = has_dep
        self.raises = raises
        self.parent_title = parent_title
        self.junction_status = junction_status
        self.closed = False

    def execute(self, sql, params=None):
        if self.raises:
            raise RuntimeError("db down")
        self._last = sql
        self._params = params or ()
        return self

    def fetchone(self):
        sql, params = self._last, self._params
        if "depends_on_task_id AS dep_id" in sql:
            if not self.has_dep:
                return _Row(dep_id=None, dep_title=None)
            return _Row(dep_id=self._PARENT, dep_title=self.parent_title)
        if "SELECT status FROM kanban_tasks" in sql:
            wanted = params[0] if params else None
            if wanted == self._PARENT:
                return _Row(status=self.dep_status) if self.dep_status else None
            if wanted == self._JUNCTION_PARENT:
                return (_Row(status=self.junction_status)
                        if self.junction_status else None)
            return None
        return _Row(title=self.title)

    def fetchall(self):
        if "kanban_task_deps" in self._last and self.junction_status is not None:
            return [_Row(depends_on_id=self._JUNCTION_PARENT)]
        return []

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


# ── ordering: auto-merge must not depend on who opened the PR ───────────────
def test_undraft_happens_before_the_sibling_hold_can_return():
    """A green PR held by a sibling was never taken out of draft.

    The sibling-conflict guard `continue`s, so the un-draft that used to sit
    below it never ran — and when the sibling finally merged, the PR sat there
    STILL a draft with nothing left to trigger it. Three AGOV PRs were in that
    state at once: CLEAN, green, invisible to auto-merge.

    Asserted on source order rather than behaviour because the failure IS the
    order: both calls exist either way, and a behavioural test that happened to
    take the non-holding path would pass while the bug remained.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "tools" / "ci" / "pr_watcher.py"
    text = src.read_text(encoding="utf-8")
    undraft = text.index('if state.get("isDraft"):')
    # Matches the config key rather than the whole line: the hold gained a
    # tie-breaker clause, and a test that pins exact source text breaks on every
    # refactor of the thing it guards.
    sibling_hold = text.index('hold_on_sibling_conflict", False)')
    assert undraft < sibling_hold, (
        "the un-draft must precede the sibling-conflict hold; below it, a held "
        "PR is never taken out of draft and auto-merge can never reach it"
    )


def test_a_scalar_the_junction_graph_superseded_does_not_hold_the_draft():
    """Seeding order must not strand a released task's PR in draft (kpr-fix-02).

    ``cef-di-04`` is dispatched while ``cef-di-03`` — the predecessor a seeder
    wrote as it walked the batch — is still open. If the draft guard still read
    the scalar column, the PR would sit in draft until that unrelated task
    finished, which is the same stall the dispatch fix removed, one step later.
    """
    conn = _Conn(title="freed work", has_dep=True, dep_status="in_progress",
                 junction_status="done")
    runner = _Runner()
    w = _watcher(conn, runner)
    assert w._mark_ready("https://x/pull/1", "cef-di-04", lambda: conn) is True
    assert runner.calls == [["gh", "pr", "ready", "https://x/pull/1"]]


def test_a_manual_gate_scalar_holds_the_draft_even_with_junction_rows():
    """A gate is a HOLD. The junction graph must not release what a human held."""
    conn = _Conn(title="AGOV work", has_dep=True, dep_status="in_progress",
                 parent_title="MANUAL-MODE GATE — a human decides",
                 junction_status="done")
    runner = _Runner()
    w = _watcher(conn, runner)
    assert w._mark_ready("https://x/pull/1", "agov-det-02", lambda: conn) is False
    assert runner.calls == []
