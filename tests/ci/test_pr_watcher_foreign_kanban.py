# CUI // SP-CTI
"""Two installs, one repo: a watcher never acts on ANOTHER board's kanban PR (omx-host-03).

The Omarchy laptop runs a separate ICDEV install -- its own database, its own
board, its own copy of ``tools/ci/pr_watcher.py`` -- against the same GitHub
repo. To THIS board a ``kanban/<id>`` PR opened by that install has no task
row, so ``_sweep_unlinked_prs`` read it as a human's PR and merged it the
moment it went green: past the owning board's done-gate, sibling-conflict
guard and done-verification, with its card left un-transitioned.

Pinned here, against a real SQLite board (MINIMAL_ICDEV_SCHEMA via
``icdev_db``) and a fake ``gh``:

  * a green ``kanban/<id>`` PR with no task row is never merged, readied,
    commented on or closed, and is audited ONCE across two cycles;
  * the same PR whose task IS on this board takes today's path unchanged;
  * a ``feat/*`` PR with no task is still merged;
  * the superseded rung does not hold-and-audit a foreign PR either;
  * ``ICDEV_PR_WATCHER_MERGE_UNLINKED`` 0/1 beats the YAML, unset -> YAML;
  * ``merge_readiness`` (and ``merge_stall``) label it ``foreign_kanban``.
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

import tools.ci.merge_readiness as mr
import tools.ci.merge_stall as ms
import tools.ci.pr_watcher as pw
from tools.db.storage import StorageConnection

_FOREIGN_URL = "https://github.com/o/r/pull/501"
_FOREIGN_BRANCH = "kanban/laptop-only-07"
_OURS_URL = "https://github.com/o/r/pull/502"
_FEAT_URL = "https://github.com/o/r/pull/503"


def _pr(url, branch, *, head="a" * 40):
    return {
        "url": url, "number": int(url.rsplit("/", 1)[-1]),
        "headRefName": branch, "headRefOid": head, "baseRefName": "main",
        "isDraft": False, "labels": [], "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [{"name": "tests", "conclusion": "SUCCESS"}],
        "reviews": [], "state": "OPEN",
    }


def _factory(db_path):
    def _conn():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return StorageConnection(c, "sqlite")
    return _conn


def _seed_task(db_path, task_id, status="done", executor_url=""):
    c = sqlite3.connect(str(db_path))
    c.execute("INSERT INTO kanban_tasks (id, title, status, executor_url) "
              "VALUES (?, ?, ?, ?)", (task_id, "T", status, executor_url))
    c.commit()
    c.close()


def _audit_rows(db_path, action="pr_watcher.foreign_kanban_pr"):
    c = sqlite3.connect(str(db_path))
    try:
        return [json.loads(r[0]) for r in c.execute(
            "SELECT details FROM audit_trail WHERE action = ?", (action,))]
    finally:
        c.close()


class _Sweep:
    """A real watcher over a real SQLite board; only the forge is faked."""

    def __init__(self, db_path, open_prs, *, merged=(), config_over=None):
        self.calls = []
        self.merged_urls = []

        def runner(cmd, **kw):
            self.calls.append(list(cmd))
            if "--state" in cmd and "merged" in cmd:
                return SimpleNamespace(returncode=0, stdout=json.dumps(list(merged)),
                                       stderr="")
            if "--state" in cmd and "open" in cmd:
                return SimpleNamespace(returncode=0, stdout=json.dumps(open_prs),
                                       stderr="")
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")

        def fetch_state(url, **kw):
            pr = next(p for p in open_prs if p["url"] == url)
            return dict(pr, commits=[{"oid": pr["headRefOid"]}])

        config = {
            "auto_merge_enabled": True,
            "auto_merge_require_approval": False,
            "merge_unlinked_prs": True,
            "sibling_conflict_check": False,
            "landed_check_on_poll": False,
            "link_prs_on_poll": False,
            "refuse_merge_when_behind": False,
            "required_checks_only": False,
            "protected_paths": [],
            "superseded_check": True,
            "superseded_close": True,
            "superseded_revert_leg": False,
        }
        config.update(config_over or {})
        self.w = pw.PRWatcher(
            config=config,
            get_connection=_factory(db_path),
            queue_message=lambda *a, **kw: {"queued": True},
            fetch_state=fetch_state,
            fetch_logs=lambda url, **kw: "",
            auto_merge_runner=runner,
            pr_list_runner=runner,
            gh_runner=runner,
            gh_close_runner=runner,
            default_branch_resolver=lambda: "main",
        )
        self.w._auto_merge = lambda url, **_kw: (
            self.merged_urls.append(url) or True)
        self.w._hitl_alert = lambda *a, **kw: None
        self.w._open_pr_index = lambda *a, **kw: {}

    def run(self):
        self.w._sweep_unlinked_prs(
            pw.WatcherReport(started_at="", finished_at="", tasks_checked=0))

    def acts(self):
        """Every gh call that would change a PR."""
        return [c for c in self.calls
                if any(v in c for v in ("merge", "ready", "comment", "close",
                                        "edit", "review"))]


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    monkeypatch.delenv(pw.MERGE_UNLINKED_ENV, raising=False)


# ---------------------------------------------------------------------------
def test_a_foreign_kanban_pr_is_never_touched_and_audited_once(icdev_db):
    s = _Sweep(icdev_db, [_pr(_FOREIGN_URL, _FOREIGN_BRANCH)])
    s.run()
    s.run()
    assert s.merged_urls == [], "another board's PR must not merge here"
    assert s.acts() == [], "no merge, ready, comment or close"
    rows = _audit_rows(icdev_db)
    assert len(rows) == 1, "audited ONCE across two cycles, not every poll"
    assert rows[0]["pr_url"] == _FOREIGN_URL
    assert "not on this board" in rows[0]["reason"]
    assert _FOREIGN_BRANCH in rows[0]["reason"]


def test_the_same_pr_whose_task_is_on_this_board_is_unchanged(icdev_db):
    """Task terminal (so not in `linked`): today's path merges it."""
    _seed_task(icdev_db, "laptop-only-07", status="done")
    s = _Sweep(icdev_db, [_pr(_FOREIGN_URL, _FOREIGN_BRANCH)])
    s.run()
    assert s.merged_urls == [_FOREIGN_URL]
    assert _audit_rows(icdev_db) == []


def test_a_linked_task_pr_is_still_left_to_the_poll(icdev_db):
    _seed_task(icdev_db, "laptop-only-07", status="pr_opened",
               executor_url=_FOREIGN_URL)
    s = _Sweep(icdev_db, [_pr(_FOREIGN_URL, _FOREIGN_BRANCH)])
    s.run()
    assert s.merged_urls == []
    assert _audit_rows(icdev_db) == []


def test_retry_and_repark_branches_bind_through_the_existing_matcher(icdev_db):
    _seed_task(icdev_db, "mfx-ci-04", status="done")
    _seed_task(icdev_db, "kph-repark-mfx-ci-05", status="done")
    s = _Sweep(icdev_db, [
        _pr("https://github.com/o/r/pull/601", "kanban/mfx-ci-04-r2"),
        _pr("https://github.com/o/r/pull/602", "kanban/kph-repark-mfx-ci-05"),
    ])
    s.run()
    assert s.merged_urls == ["https://github.com/o/r/pull/601",
                             "https://github.com/o/r/pull/602"]
    assert _audit_rows(icdev_db) == []


def test_a_human_feat_pr_with_no_task_is_still_merged(icdev_db):
    s = _Sweep(icdev_db, [_pr(_FEAT_URL, "feat/some-human-work"),
                          _pr(_FOREIGN_URL, _FOREIGN_BRANCH)])
    s.run()
    assert s.merged_urls == [_FEAT_URL]


def test_the_superseded_rung_does_not_hold_a_foreign_pr(icdev_db):
    """Its no-task branch would hold AND audit `superseded_hold` -- an act on
    a PR this board does not own. The foreign rule runs first."""
    head = "b" * 40
    merged = [{"number": 400, "url": "https://github.com/o/r/pull/400",
               "title": "landed", "body": "", "headRefName": _FOREIGN_BRANCH,
               "headRefOid": head, "mergedAt": "2026-10-10T00:00:00Z",
               "commits": [{"oid": head}]}]
    s = _Sweep(icdev_db, [_pr(_FOREIGN_URL, _FOREIGN_BRANCH, head=head)],
               merged=merged)
    s.run()
    assert s.merged_urls == [] and s.acts() == []
    assert _audit_rows(icdev_db, "pr_watcher.superseded_hold") == []
    assert len(_audit_rows(icdev_db)) == 1


def test_an_unreadable_board_is_not_a_foreign_finding():
    def boom():
        raise RuntimeError("db down")
    assert pw.foreign_kanban_branch(boom, _FOREIGN_BRANCH) is False
    assert pw.foreign_kanban_branch(boom, "feat/x") is False


# ── the per-install override ───────────────────────────────────────────────
@pytest.mark.parametrize("env,yaml_on,merges", [
    (None, True, True), (None, False, False),
    ("0", True, False), ("1", False, True),
    ("false", True, False), ("on", False, True),
    ("garbage", True, True), ("garbage", False, False),
])
def test_env_override_beats_the_yaml(icdev_db, monkeypatch, env, yaml_on, merges):
    if env is not None:
        monkeypatch.setenv(pw.MERGE_UNLINKED_ENV, env)
    assert pw.merge_unlinked_enabled({"merge_unlinked_prs": yaml_on}) is merges
    s = _Sweep(icdev_db, [_pr(_FEAT_URL, "feat/x")],
               config_over={"merge_unlinked_prs": yaml_on})
    s.run()
    assert s.merged_urls == ([_FEAT_URL] if merges else [])


def test_merge_stall_reports_the_door_through_the_same_rule(monkeypatch):
    monkeypatch.setenv(pw.MERGE_UNLINKED_ENV, "0")
    assert ms.merge_unlinked_enabled({"merge_unlinked_prs": True}) is False


# ── the report: same ladder, its own reason ────────────────────────────────
def test_merge_readiness_names_a_foreign_kanban_pr(icdev_db):
    _seed_task(icdev_db, "ours-01", status="done")
    prs = [_pr(_FOREIGN_URL, _FOREIGN_BRANCH),
           _pr(_OURS_URL, "kanban/ours-01"),
           _pr(_FEAT_URL, "feat/x")]
    foreign = mr.foreign_pr_urls(prs, _factory(icdev_db))
    assert foreign == frozenset({_FOREIGN_URL})
    report = mr.build_report(prs, default_branch="main", foreign_urls=foreign)
    by_url = {r["url"]: r for r in report["prs"]}
    assert by_url[_FOREIGN_URL]["state"] == mr.FOREIGN_KANBAN
    assert "kanban PR not on this board" in by_url[_FOREIGN_URL]["reason"]
    assert by_url[_FOREIGN_URL]["ready"] is False
    assert by_url[_FOREIGN_URL]["foreign"] is True
    assert by_url[_OURS_URL]["state"] == mr.READY
    assert by_url[_FEAT_URL]["state"] == mr.READY
    assert mr.FOREIGN_KANBAN in mr.MERGE_STATES
    assert mr.FOREIGN_KANBAN in mr.ATTENTION_ORDER


def test_collect_report_wires_the_board_lookup(icdev_db, tmp_path, monkeypatch):
    import tools.db.storage as storage

    def _board():
        c = sqlite3.connect(str(icdev_db))
        c.row_factory = sqlite3.Row
        return StorageConnection(c, "sqlite")

    monkeypatch.setattr(storage, "get_connection", _board)
    dump = tmp_path / "prs.json"
    dump.write_text(json.dumps([_pr(_FOREIGN_URL, _FOREIGN_BRANCH),
                                _pr(_FEAT_URL, "feat/x")]), encoding="utf-8")
    report = mr.collect_report(from_json=str(dump), default_branch="main",
                               measure_behind=False, max_behind_commits=10)
    states = {r["url"]: r["state"] for r in report["prs"]}
    assert states == {_FOREIGN_URL: mr.FOREIGN_KANBAN, _FEAT_URL: mr.READY}


def test_merge_stall_does_not_count_a_foreign_pr_as_eligible():
    rows = ms.eligibility_rows([_pr(_FOREIGN_URL, _FOREIGN_BRANCH)],
                               default_branch="main",
                               foreign_urls={_FOREIGN_URL})
    assert rows[0]["state"] == mr.FOREIGN_KANBAN
    assert rows[0]["eligible"] is False
