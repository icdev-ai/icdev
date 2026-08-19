# CUI // SP-CTI
"""kpr-watch-06 — the kanban PR opens as a DRAFT and the watcher promotes it.

The inversion is only safe if BOTH halves hold, so both are asserted here:

* the runner's ``gh pr create`` actually carries ``--draft`` (and stops carrying
  it when ``ICDEV_KANBAN_PR_DRAFT=0``), and
* every path that merges a kanban PR promotes the draft first through
  ``PRWatcher._mark_ready`` — the watcher loop already did, and ``land()``, the
  path a worker session uses to report its own completion, now does too.

The failure mode a half-done inversion produces is every kanban PR stuck in
draft forever, which is strictly worse than the default it replaces. That is why
the promotion is also AUDITED (``pr_watcher.auto_ready``) and why
``tools.ci.draft_promotion_survey`` can count it.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from tools.ci import draft_promotion_survey as dps
from tools.genesis.reflexes import kanban as kb
from tools.kanban import land as land_mod


# ── the switch ──────────────────────────────────────────────────────────────

def test_draft_is_the_default(monkeypatch):
    monkeypatch.delenv("ICDEV_KANBAN_PR_DRAFT", raising=False)
    assert kb._pr_opens_as_draft() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE", " Off "])
def test_kill_switch_restores_the_old_default(monkeypatch, value):
    monkeypatch.setenv("ICDEV_KANBAN_PR_DRAFT", value)
    assert kb._pr_opens_as_draft() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", ""])
def test_anything_else_still_drafts(monkeypatch, value):
    monkeypatch.setenv("ICDEV_KANBAN_PR_DRAFT", value)
    assert kb._pr_opens_as_draft() is True


# ── the gh invocation ───────────────────────────────────────────────────────

def _stub_pr_flow(monkeypatch, tmp_path):
    """Everything around the `gh pr create` call, stubbed. Returns the argv log."""
    calls: list = []

    class _Proc:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(argv, *a, **kw):
        calls.append(list(argv))
        if argv[:2] == ["git", "log"]:
            return _Proc(0, "deadbee did the thing\n")
        if argv[:2] == ["git", "push"]:
            return _Proc(0)
        if argv[:3] == ["gh", "pr", "create"]:
            return _Proc(0, "https://github.com/icdev-ai/ICDev/pull/9999\n")
        return _Proc(0)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr(kb, "_task_repo_root", lambda _t: tmp_path)
    monkeypatch.setattr(kb, "_task_base_branch", lambda _t: "main")
    # The title lookup and the landed preflight are both wrapped in the source;
    # raising proves the PR still opens when they are unavailable.
    monkeypatch.setattr(kb, "get_connection", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db")))
    monkeypatch.setattr(kb, "_landed_preflight", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no board")))
    monkeypatch.setattr(kb, "_open_prs_for_task", lambda *a, **k: [])
    monkeypatch.setattr(kb, "_ensure_pr_base", lambda *a, **k: None)
    monkeypatch.setattr(kb, "_supersede_stale_prs", lambda *a, **k: [])
    return calls


def _create_argv(calls):
    for argv in calls:
        if argv[:3] == ["gh", "pr", "create"]:
            return argv
    raise AssertionError("gh pr create was never invoked: %r" % (calls,))


def test_pr_is_opened_as_a_draft(monkeypatch, tmp_path):
    monkeypatch.delenv("ICDEV_KANBAN_PR_DRAFT", raising=False)
    calls = _stub_pr_flow(monkeypatch, tmp_path)

    url = kb._push_branch_and_open_pr("kpr-watch-06", "did the thing")

    assert url == "https://github.com/icdev-ai/ICDev/pull/9999"
    assert "--draft" in _create_argv(calls)


def test_kill_switch_opens_it_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("ICDEV_KANBAN_PR_DRAFT", "0")
    calls = _stub_pr_flow(monkeypatch, tmp_path)

    kb._push_branch_and_open_pr("kpr-watch-06", "did the thing")

    assert "--draft" not in _create_argv(calls)


# ── the promotion, and the audit row that makes it countable ────────────────

class _FakeConn:
    def __init__(self):
        self.rows: list = []

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("INSERT INTO AUDIT_TRAIL"):
            self.rows.append(params)
        return self

    def fetchone(self):
        return None

    def commit(self):
        pass

    def close(self):
        pass


def _watcher(monkeypatch, *, ready_rc=0):
    from tools.ci import pr_watcher as prw

    w = prw.PRWatcher.__new__(prw.PRWatcher)
    w.dry_run = False
    w.config = {"auto_ready_draft_prs": True}
    conn = _FakeConn()
    w._connection = lambda: (lambda: conn)
    w._auto_merge_runner = lambda *a, **k: type(
        "P", (), {"returncode": ready_rc, "stdout": "", "stderr": ""})()
    monkeypatch.setattr(prw, "_is_gate_task", lambda _c, _t: False)
    monkeypatch.setattr(prw, "_held_by_a_gate", lambda _c, _t: False)
    return w, conn


def test_promotion_writes_one_audit_row(monkeypatch):
    w, conn = _watcher(monkeypatch)

    assert w._mark_ready("https://example/pull/1", "kpr-watch-06", w._connection()) is True

    actions = [p for row in conn.rows for p in row if p == "pr_watcher.auto_ready"]
    assert actions, "the promotion left no countable trace: %r" % (conn.rows,)


def test_a_refused_promotion_writes_nothing(monkeypatch):
    from tools.ci import pr_watcher as prw

    w, conn = _watcher(monkeypatch)
    monkeypatch.setattr(prw, "_held_by_a_gate", lambda _c, _t: True)

    assert w._mark_ready("https://example/pull/1", "kpr-watch-06", w._connection()) is False
    # A hold repeats every poll — auditing it would flood the trail, and the
    # caller's `wait` action already records the hold once per cycle.
    assert not [p for row in conn.rows for p in row if p == "pr_watcher.auto_ready"]


# ── land(): the CLI path must not fail on a draft it can promote ────────────

class _StubWatcher:
    def __init__(self, *, ready=True):
        self.ready = ready
        self.marked: list = []
        self.merged: list = []

    def _mark_ready(self, pr_url, task_id, _get_conn):
        self.marked.append((pr_url, task_id))
        return self.ready

    def _auto_merge(self, pr_url):
        self.merged.append(pr_url)
        return True

    def _fetch_state(self, _pr_url):
        return {"state": "MERGED"}


def _preflight_ok(is_draft):
    return {
        "task_id": "kpr-watch-06", "ok": True, "merged": False,
        "already_merged": False, "pr_url": "https://example/pull/1",
        "is_draft": is_draft, "reason": "landable", "checks": [],
    }


def test_land_promotes_the_draft_before_merging(monkeypatch):
    w = _StubWatcher()
    monkeypatch.setattr(land_mod, "preflight", lambda *a, **k: _preflight_ok(True))
    monkeypatch.setattr(land_mod, "_resolve_conn", lambda c: (c or (lambda: None)))

    verdict = land_mod.land("kpr-watch-06", get_conn=lambda: None, watcher=w)

    assert w.marked == [("https://example/pull/1", "kpr-watch-06")]
    assert w.merged == ["https://example/pull/1"]
    assert verdict["merged"] is True


def test_land_refuses_when_the_draft_is_held(monkeypatch):
    w = _StubWatcher(ready=False)
    monkeypatch.setattr(land_mod, "preflight", lambda *a, **k: _preflight_ok(True))
    monkeypatch.setattr(land_mod, "_resolve_conn", lambda c: (c or (lambda: None)))

    verdict = land_mod.land("kpr-watch-06", get_conn=lambda: None, watcher=w)

    assert verdict["ok"] is False
    assert w.merged == [], "a held draft must never be merged"
    assert "DRAFT" in verdict["reason"]


def test_land_leaves_a_ready_pr_alone(monkeypatch):
    w = _StubWatcher()
    monkeypatch.setattr(land_mod, "preflight", lambda *a, **k: _preflight_ok(False))
    monkeypatch.setattr(land_mod, "_resolve_conn", lambda c: (c or (lambda: None)))

    land_mod.land("kpr-watch-06", get_conn=lambda: None, watcher=w)

    assert w.marked == []


# ── the survey: a zero is not a verdict ─────────────────────────────────────

def _pr(branch, *, hours_ago, state="OPEN", draft=False):
    created = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"headRefName": branch, "createdAt": created.isoformat(),
            "state": state, "isDraft": draft,
            "url": "https://example/pull/%d" % hours_ago}


def test_survey_reports_unmeasurable_when_gh_cannot_answer():
    out = dps.survey(None)
    assert out["unmeasurable"]
    assert "opened" not in out


def test_survey_reports_unmeasurable_on_an_empty_window():
    out = dps.survey([_pr("kanban/x", hours_ago=500)], window_hours=24)
    assert out["unmeasurable"]


def test_survey_counts_the_rate_and_the_stuck_drafts():
    prs = [
        _pr("kanban/a", hours_ago=2, state="MERGED"),
        _pr("kanban/b", hours_ago=20, draft=True),
        _pr("kanban/c", hours_ago=1, draft=True),
        _pr("feat/not-kanban", hours_ago=1, draft=True),
    ]
    out = dps.survey(prs, window_hours=24, stuck_hours=6, promotions=3)

    assert out["opened"] == 3           # the feat/ branch is not a kanban PR
    assert out["merged"] == 1
    assert out["open_drafts_now"] == 2
    assert out["stuck_count"] == 1      # only the 20h-old one is stuck
    assert out["stuck"][0]["branch"] == "kanban/b"
    assert out["promotions"] == 3


def test_survey_never_fabricates_a_promotion_count():
    out = dps.survey([_pr("kanban/a", hours_ago=1)], window_hours=24)
    assert out["promotions"] is None, "unreadable must not print as 0"
