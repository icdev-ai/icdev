# CUI // SP-CTI
"""OPT-70: tests for tools/ci/pr_watcher.py."""
from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _sql_compat  # noqa: E402  (tests/ is on sys.path via tests/conftest.py)
from tools.ci import pr_watcher as pw  # noqa: E402
from tools.kanban.state_machine import KanbanState  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Helpers: a fake in-memory DB that mimics tools.db.storage.get_connection
# ────────────────────────────────────────────────────────────────────────────


class _FakeRow(dict):
    def __getitem__(self, k):
        return dict.__getitem__(self, k)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConnection:
    """SQL-sniffing stand-in for the task reads, with a *real* DB behind
    ``cursor()``.

    ``_enforced_done_ok`` reads ``kanban_verifications`` through
    ``conn.cursor()``, which this class did not implement. Under
    ``KANBAN_PIPELINE_ENFORCE`` the resulting AttributeError was swallowed by
    the gate's fail-closed ``except`` and every auto-merge decision became
    "hold" — so the base-branch guard tests below passed or failed depending on
    whether the launcher exported that variable. A developer's shell leaves it
    unset (gate skipped, tests green); a kanban-scheduler session inherits
    ``KANBAN_PIPELINE_ENFORCE=1`` from .env and the same three tests failed.

    Backing ``cursor()`` with a temporary in-memory database via
    ``tests/_sql_compat`` makes the gate run for real in both environments, so
    what these tests assert no longer depends on who started pytest.
    """

    def __init__(self, tasks, verifications=()):
        self._tasks = tasks
        self._audit = []
        self._db = _sql_compat.connect(":memory:")
        self._db.execute(
            """
            CREATE TABLE kanban_verifications (
                id           TEXT PRIMARY KEY,
                task_id      TEXT NOT NULL,
                verified_at  TEXT NOT NULL,
                result       TEXT NOT NULL,
                review_passed INTEGER
            )
            """
        )
        for i, v in enumerate(verifications):
            self._db.execute(
                "INSERT INTO kanban_verifications "
                "(id, task_id, verified_at, result, review_passed) "
                "VALUES (%s, %s, %s, %s, %s)",
                (f"v{i}", v["task_id"], v.get("verified_at", "2026-08-01T00:00:00Z"),
                 v["result"], v.get("review_passed")),
            )
        self._db.commit()

    def cursor(self):
        return self._db.cursor()

    def execute(self, sql, params=()):
        sql_upper = sql.upper()
        if sql_upper.startswith("SELECT") and "KANBAN_TASKS" in sql_upper:
            if "WHERE ID" in sql_upper:
                task_id = params[0]
                filtered = [t for t in self._tasks if t["id"] == task_id]
            else:
                filtered = self._tasks
            return _FakeCursor(filtered)
        if "AUDIT_TRAIL" in sql_upper and sql_upper.startswith("SELECT"):
            return _FakeCursor([])
        if sql_upper.startswith("INSERT INTO AUDIT_TRAIL"):
            self._audit.append(params)
            return _FakeCursor([])
        return _FakeCursor([])

    def commit(self):
        pass

    def close(self):
        # The in-memory verification DB dies with its connection and the
        # watcher closes what it is given, so keep it alive for the assertions.
        pass


def _fake_connection_factory(tasks, verifications=()):
    def _make():
        return _FakeConnection(tasks, verifications)
    return _make


# ────────────────────────────────────────────────────────────────────────────
# parse + list_pr_tasks
# ────────────────────────────────────────────────────────────────────────────


def test_parse_pr_url_extracts_valid_url():
    url = "https://github.com/icdev-ai/icdev-ai/pull/123"
    assert pw._parse_pr_url(f"text {url} more") == url


def test_parse_pr_url_none_when_absent():
    assert pw._parse_pr_url("no url here") is None
    assert pw._parse_pr_url(None) is None


def test_list_pr_tasks_filters_tasks_without_pr_url():
    tasks = [
        _FakeRow(
            id="task-a", title="A", description="",
            status="in_progress",
            executor_url="https://github.com/o/r/pull/1",
        ),
        _FakeRow(
            id="task-b", title="B", description="no url",
            status="in_progress", executor_url=None,
        ),
        _FakeRow(
            id="task-c", title="C",
            description="see https://github.com/o/r/pull/2",
            status="in_progress", executor_url=None,
        ),
    ]
    factory = _fake_connection_factory(tasks)
    out = pw.list_pr_tasks(factory)
    assert len(out) == 2
    ids = {t["id"] for t in out}
    assert ids == {"task-a", "task-c"}


# ────────────────────────────────────────────────────────────────────────────
# prepare_resume_context
# ────────────────────────────────────────────────────────────────────────────


def test_prepare_resume_context_ci_failed_includes_checks():
    state = {
        "number": 42,
        "headRefName": "fix/x",
        "statusCheckRollup": [
            {"name": "tests", "conclusion": "FAILURE"},
            {"name": "lint", "conclusion": "SUCCESS"},
        ],
    }
    ctx = pw.prepare_resume_context(
        "task-1", KanbanState.CI_FAILED, state, "FAILED tests/test_x.py",
    )
    assert "ci_failed" in ctx.lower() or "CI_FAILED" in ctx
    assert "tests" in ctx
    assert "PR#42" in ctx


def test_prepare_resume_context_merge_conflict_gives_instructions():
    state = {"number": 7, "headRefName": "wip/merge"}
    ctx = pw.prepare_resume_context(
        "task-7", KanbanState.MERGE_CONFLICT, state, "",
    )
    assert "merge conflict" in ctx.lower()
    assert "rebase" in ctx.lower() or "resolve" in ctx.lower()


def test_prepare_resume_context_changes_requested_includes_review_body():
    state = {
        "number": 12,
        "headRefName": "feat/z",
        "reviews": [
            {
                "state": "CHANGES_REQUESTED",
                "body": "Please add a docstring",
                "author": {"login": "alice"},
            }
        ],
    }
    ctx = pw.prepare_resume_context(
        "task-12", KanbanState.CHANGES_REQUESTED, state, "",
    )
    assert "docstring" in ctx.lower()
    assert "alice" in ctx.lower()


def test_prepare_resume_context_truncates_large_logs():
    state = {"number": 1, "headRefName": "x"}
    long_log = "x" * 50000
    ctx = pw.prepare_resume_context(
        "task-1", KanbanState.CI_FAILED, state, long_log, max_chars=500,
    )
    # The ctx itself is bounded by the explanatory text + 500 char tail
    assert len(ctx) < 2000


# ────────────────────────────────────────────────────────────────────────────
# Watcher.poll_once end-to-end (with fakes for every external dep)
# ────────────────────────────────────────────────────────────────────────────


def _build_watcher(tasks, pr_states, queue_log, config=None, logs_by_url=None):
    factory = _fake_connection_factory(tasks)
    state_map = dict(pr_states)
    logs_map = dict(logs_by_url or {})

    def fake_state(pr_url, **kwargs):
        return state_map[pr_url]

    def fake_logs(pr_url, **kwargs):
        return logs_map.get(pr_url, "")

    def fake_queue(task_id, text, sender="user"):
        queue_log.append({"task_id": task_id, "text": text, "sender": sender})
        return {"queued": True}

    return pw.PRWatcher(
        config=config or {"max_resume_cycles_per_task": 5},
        get_connection=factory,
        queue_message=fake_queue,
        fetch_state=fake_state,
        fetch_logs=fake_logs,
        dry_run=True,
    )


def test_watcher_passes_through_on_ci_still_running():
    tasks = [_FakeRow(
        id="task-pending", title="T", description="",
        status="in_progress",
        executor_url="https://github.com/o/r/pull/1",
    )]
    pr_states = {
        "https://github.com/o/r/pull/1": {
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "statusCheckRollup": [{"state": "IN_PROGRESS", "name": "tests"}],
        }
    }
    queue_log = []
    w = _build_watcher(tasks, pr_states, queue_log)
    report = w.poll_once()
    assert report.tasks_checked == 1
    assert report.actions[0].action == "wait"
    assert report.actions[0].classification == "pr_opened"
    assert queue_log == []


def test_watcher_injects_resume_on_ci_failed():
    tasks = [_FakeRow(
        id="task-fail", title="T", description="",
        status="in_progress",
        executor_url="https://github.com/o/r/pull/9",
    )]
    pr_states = {
        "https://github.com/o/r/pull/9": {
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "statusCheckRollup": [
                {"conclusion": "FAILURE", "name": "tests"}
            ],
            "number": 9,
            "headRefName": "fix/x",
        }
    }
    queue_log = []
    logs = {"https://github.com/o/r/pull/9": "FAILED tests/test_y.py"}
    w = _build_watcher(tasks, pr_states, queue_log, logs_by_url=logs)
    # Disable dry_run so _send_resume actually calls the queue_message stub
    w.dry_run = False
    report = w.poll_once()
    assert report.actions[0].action == "resume"
    assert report.actions[0].classification == "ci_failed"
    assert len(queue_log) == 1
    assert "ci_failed" in queue_log[0]["text"].lower()


def test_watcher_escalates_on_max_resume_cycles():
    tasks = [_FakeRow(
        id="task-loop", title="T", description="",
        status="in_progress",
        executor_url="https://github.com/o/r/pull/5",
    )]
    pr_states = {
        "https://github.com/o/r/pull/5": {
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "statusCheckRollup": [{"conclusion": "FAILURE", "name": "tests"}],
            "number": 5, "headRefName": "x",
        }
    }
    queue_log = []
    w = _build_watcher(
        tasks, pr_states, queue_log,
        config={"max_resume_cycles_per_task": 5},
    )
    # Patch resume_cycle to simulate we've already hit the cap
    w._resume_cycle = lambda task_id, pr_url=None: 5  # noqa: SLF001
    report = w.poll_once()
    assert report.actions[0].action == "escalate"
    assert "cap reached" in report.actions[0].reason
    assert queue_log == []  # no injection on escalate


def test_watcher_reports_merge_on_already_merged_pr():
    tasks = [_FakeRow(
        id="task-done", title="T", description="",
        status="in_progress",
        executor_url="https://github.com/o/r/pull/2",
    )]
    pr_states = {
        "https://github.com/o/r/pull/2": {
            "state": "MERGED",
            "mergeable": "MERGEABLE",
        }
    }
    queue_log = []
    w = _build_watcher(tasks, pr_states, queue_log)
    report = w.poll_once()
    assert report.actions[0].action == "merge"
    assert report.actions[0].classification == "done"


def test_watcher_auto_merge_disabled_by_default():
    tasks = [_FakeRow(
        id="task-green", title="T", description="",
        status="in_progress",
        executor_url="https://github.com/o/r/pull/3",
    )]
    pr_states = {
        "https://github.com/o/r/pull/3": {
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "reviews": [{"state": "APPROVED", "author": "b"}],
            "statusCheckRollup": [{"conclusion": "SUCCESS", "name": "tests"}],
        }
    }
    queue_log = []
    w = _build_watcher(
        tasks, pr_states, queue_log,
        config={"auto_merge_enabled": False, "max_resume_cycles_per_task": 5},
    )
    report = w.poll_once()
    # Classification says done (approved + passing) but auto_merge is off
    assert report.actions[0].classification == "done"
    assert report.actions[0].action == "wait"


def test_watcher_fails_gracefully_on_fetch_exception():
    tasks = [_FakeRow(
        id="task-err", title="T", description="",
        status="in_progress",
        executor_url="https://github.com/o/r/pull/99",
    )]

    def raising_fetch(url, **kwargs):
        raise RuntimeError("gh down")

    factory = _fake_connection_factory(tasks)
    w = pw.PRWatcher(
        config={"max_resume_cycles_per_task": 5},
        get_connection=factory,
        fetch_state=raising_fetch,
        queue_message=lambda *a, **kw: {"queued": True},
        dry_run=True,
    )
    report = w.poll_once()
    assert report.actions[0].action == "wait"
    assert "gh down" in report.actions[0].reason


# ────────────────────────────────────────────────────────────────────────────
# Base-branch guard (incident 2026-07-08: PR #114 auto-merged into a
# feature branch instead of main)
# ────────────────────────────────────────────────────────────────────────────


def _green_pr_state(base_ref=None):
    state = {
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "reviews": [{"state": "APPROVED", "author": "b"}],
        "statusCheckRollup": [{"conclusion": "SUCCESS", "name": "tests"}],
    }
    if base_ref is not None:
        state["baseRefName"] = base_ref
    return state


def _build_merge_watcher(base_ref, merge_calls):
    tasks = [_FakeRow(
        id="task-m", title="T", description="",
        status="in_progress",
        executor_url="https://github.com/o/r/pull/114",
    )]
    state_map = {"https://github.com/o/r/pull/114": _green_pr_state(base_ref)}

    def fake_merge_runner(cmd, **kwargs):
        merge_calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return pw.PRWatcher(
        config={
            "auto_merge_enabled": True,
            "auto_merge_require_approval": False,
            "max_resume_cycles_per_task": 5,
        },
        # A PASSED done-verification, so the enforced gate is satisfied and the
        # base-branch guard is what decides — which is what these tests assert.
        # Without it the gate holds every merge under KANBAN_PIPELINE_ENFORCE=1
        # and the guard is never reached.
        get_connection=_fake_connection_factory(
            tasks,
            verifications=[{"task_id": "task-m", "result": "pass", "review_passed": 1}],
        ),
        queue_message=lambda *a, **kw: {"queued": True},
        fetch_state=lambda url, **kw: state_map[url],
        fetch_logs=lambda url, **kw: "",
        auto_merge_runner=fake_merge_runner,
        default_branch_resolver=lambda: "main",
    )


def test_watcher_refuses_auto_merge_into_feature_branch():
    merge_calls = []
    w = _build_merge_watcher("feat/rfi-six-parts", merge_calls)
    report = w.poll_once()
    action = report.actions[0]
    assert action.classification == "done"
    assert action.action == "wait"
    assert "refusing auto-merge" in action.reason
    assert "feat/rfi-six-parts" in action.reason
    assert merge_calls == []


def test_watcher_refuses_auto_merge_when_base_unknown():
    merge_calls = []
    w = _build_merge_watcher(None, merge_calls)
    report = w.poll_once()
    action = report.actions[0]
    assert action.action == "wait"
    assert "refusing auto-merge" in action.reason
    assert merge_calls == []


def test_watcher_auto_merges_on_default_base():
    merge_calls = []
    w = _build_merge_watcher("main", merge_calls)
    report = w.poll_once()
    action = report.actions[0]
    assert action.action == "merge"
    assert action.reason == "auto-merge ok"
    assert len(merge_calls) == 1


def test_repo_default_branch_via_gh():
    def fake_runner(cmd, **kwargs):
        assert cmd[:3] == ["gh", "repo", "view"]
        return SimpleNamespace(
            returncode=0,
            stdout='{"defaultBranchRef": {"name": "main"}}',
            stderr="",
        )

    assert pw.repo_default_branch(runner=fake_runner) == "main"


def test_repo_default_branch_git_fallback():
    def fake_runner(cmd, **kwargs):
        if cmd[0] == "gh":
            return SimpleNamespace(returncode=1, stdout="", stderr="no auth")
        return SimpleNamespace(returncode=0, stdout="origin/develop\n", stderr="")

    assert pw.repo_default_branch(runner=fake_runner) == "develop"


def test_cli_dry_run_json(monkeypatch, capsys):
    # Empty task set — the CLI should still print a report
    def fake_list(*args, **kwargs):
        return []

    monkeypatch.setattr(pw, "list_pr_tasks", fake_list)
    # Stub the DB factory so nothing tries to touch the real DB
    monkeypatch.setattr(
        "icdev.tools.db.storage.get_connection",
        lambda: _FakeConnection([]),
        raising=False,
    )

    rc = pw.main(["--once", "--dry-run", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"actions"' in out
    assert '"tasks_checked": 0' in out
