# CUI // SP-CTI
"""kax-merge-01: `--set-status <id> done --merge` must LAND the PR, and must be
strictly stricter than the refusal (and the --force-done bypass) it replaces.

The CLI's done-gate only ever refused. A worker session that genuinely finished
could be refused or bypass the verification with --force-done; neither lands the
work. --merge is the third option, and the failure mode designed against here is
a session that cannot merge legitimately reaching for --merge and getting an
unverified landing. Every test below exists to keep that impossible.
"""
from __future__ import annotations

import importlib

import pytest

cli = importlib.import_module("tools.kanban.cli")
land = importlib.import_module("tools.kanban.land")
prw = importlib.import_module("tools.ci.pr_watcher")
kb = importlib.import_module("tools.genesis.reflexes.kanban")

PR = "https://github.com/icdev-ai/ICDev/pull/1234"

GREEN = {
    "state": "OPEN",
    "baseRefName": "main",
    "mergeable": "MERGEABLE",
    "reviews": [],
    "statusCheckRollup": [
        {"name": "Lint", "conclusion": "SUCCESS"},
        {"name": "Test", "conclusion": "SUCCESS"},
    ],
}


def _state(**over):
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in GREEN.items()}
    out.update(over)
    return out


class _FakeWatcher:
    """Stands in for PRWatcher, exposing only what land.py reuses."""

    def __init__(self, state, *, config=None, merge_ok=True, merged_after=True,
                 file_map=None, default_branch="main"):
        self.config = {"auto_merge_enabled": True,
                       "auto_merge_require_approval": False}
        self.config.update(config or {})
        self._state = state
        self._merge_ok = merge_ok
        self._merged_after = merged_after
        self._file_map = file_map if file_map is not None else {PR: {"a.py"}}
        self._branch = default_branch
        self.merge_calls: list = []

    def _fetch_state(self, pr_url):
        if isinstance(self._state, Exception):
            raise self._state
        if self.merge_calls and self._merged_after:
            return {**self._state, "state": "MERGED"}
        return self._state

    def _default_branch(self):
        return self._branch

    def _auto_merge(self, pr_url, state=None):
        # `state` is kpr-watch-04: the shared chokepoint takes the PR record
        # so it can refuse a hold label for both callers.
        self.merge_calls.append(pr_url)
        return self._merge_ok

    def _open_pr_files(self):
        return self._file_map

    def _sibling_conflicts(self, url, file_map):
        return prw.PRWatcher._sibling_conflicts(self, url, file_map)


@pytest.fixture()
def one_pr_task(monkeypatch):
    """`list_pr_tasks` resolves exactly one task carrying PR."""
    monkeypatch.setattr(
        prw, "list_pr_tasks",
        lambda _conn, task_id=None: [{"id": task_id, "pr_url": PR}])


@pytest.fixture()
def gate_passes(monkeypatch):
    monkeypatch.setattr(prw, "_enforced_done_ok",
                        lambda _c, _t: (True, "enforcement off"))


def _land(state, **kw):
    w = _FakeWatcher(state, **kw)
    return land.land("t-1", get_conn=lambda: None, watcher=w, sleeper=lambda _s: None), w


# ── refusals ────────────────────────────────────────────────────────────────

def test_refuses_when_ci_is_red(one_pr_task, gate_passes):
    verdict, w = _land(_state(statusCheckRollup=[
        {"name": "Test", "conclusion": "FAILURE"}]))
    assert verdict["ok"] is False and verdict["merged"] is False
    assert "CI is red" in verdict["reason"]
    assert w.merge_calls == [], "asked gh to merge a red PR"


def test_refuses_while_ci_is_still_running(one_pr_task, gate_passes):
    verdict, w = _land(_state(statusCheckRollup=[
        {"name": "Test", "state": "PENDING"}]))
    assert verdict["ok"] is False
    assert "still running" in verdict["reason"]
    assert w.merge_calls == []


def test_refuses_when_no_check_has_reported(one_pr_task, gate_passes):
    """An empty rollup is unknown, not green."""
    verdict, w = _land(_state(statusCheckRollup=[]))
    assert verdict["ok"] is False and w.merge_calls == []


def test_refuses_when_the_pr_is_closed(one_pr_task, gate_passes):
    verdict, w = _land(_state(state="CLOSED"))
    assert verdict["ok"] is False and verdict["merged"] is False
    assert "not OPEN" in verdict["reason"]
    assert w.merge_calls == []


def test_refuses_when_the_enforced_done_gate_holds(one_pr_task, monkeypatch):
    monkeypatch.setattr(
        prw, "_enforced_done_ok",
        lambda _c, _t: (False, "enforced gate: conformance review_passed=false"))
    verdict, w = _land(_state())
    assert verdict["ok"] is False
    assert "review_passed=false" in verdict["reason"]
    assert w.merge_calls == [], "merged a PR whose conformance review failed"


def test_uses_the_watchers_enforced_gate_not_a_copy(one_pr_task, monkeypatch):
    """The contract must be read from pr_watcher, never re-derived here."""
    seen = []

    def _spy(get_conn, task_id):
        seen.append(task_id)
        return True, "enforcement off"

    monkeypatch.setattr(prw, "_enforced_done_ok", _spy)
    _land(_state())
    assert seen == ["t-1"]


def test_refuses_when_no_pr_is_recorded(monkeypatch, gate_passes):
    monkeypatch.setattr(prw, "list_pr_tasks", lambda _c, task_id=None: [])
    verdict, w = _land(_state())
    assert verdict["ok"] is False
    assert "no PR is recorded" in verdict["reason"]
    assert w.merge_calls == []


def test_refuses_when_pr_state_is_unreadable(one_pr_task, gate_passes):
    """Fail CLOSED: the plain done-gate fails open, landing a PR must not."""
    verdict, w = _land(RuntimeError("gh CLI not on PATH"))
    assert verdict["ok"] is False
    assert "unreadable" in verdict["reason"]
    assert w.merge_calls == []


def test_refuses_a_pr_based_on_a_feature_branch(one_pr_task, gate_passes):
    verdict, w = _land(_state(baseRefName="feat/other"))
    assert verdict["ok"] is False
    assert "not the default branch" in verdict["reason"]
    assert w.merge_calls == []


def test_refuses_a_conflicting_pr(one_pr_task, gate_passes):
    verdict, w = _land(_state(mergeable="CONFLICTING"))
    assert verdict["ok"] is False and w.merge_calls == []


def test_refuses_when_changes_were_requested(one_pr_task, gate_passes):
    verdict, w = _land(_state(reviews=[{"state": "CHANGES_REQUESTED"}]))
    assert verdict["ok"] is False and w.merge_calls == []


def test_requires_approval_when_configured(one_pr_task, gate_passes):
    verdict, w = _land(_state(),
                       config={"auto_merge_require_approval": True})
    assert verdict["ok"] is False
    assert "approving review" in verdict["reason"]
    assert w.merge_calls == []


def test_holds_on_sibling_file_conflict_when_configured(one_pr_task, gate_passes):
    other = "https://github.com/icdev-ai/ICDev/pull/9999"
    verdict, w = _land(
        _state(),
        config={"hold_on_sibling_conflict": True},
        file_map={PR: {"tools/kanban/cli.py"}, other: {"tools/kanban/cli.py"}})
    assert verdict["ok"] is False
    assert "sibling" in verdict["reason"] or "shares source file" in verdict["reason"]
    assert w.merge_calls == []


def test_sibling_hold_fails_closed_when_the_listing_is_unavailable(
        one_pr_task, gate_passes):
    """`_open_pr_files` returns {} on any gh failure. The candidate PR is OPEN,
    so its own absence means the listing failed — refuse rather than race."""
    verdict, w = _land(_state(), config={"hold_on_sibling_conflict": True},
                       file_map={})
    assert verdict["ok"] is False and w.merge_calls == []


def test_sibling_check_is_skipped_when_not_configured(one_pr_task, gate_passes):
    other = "https://github.com/icdev-ai/ICDev/pull/9999"
    verdict, _w = _land(
        _state(),
        config={"hold_on_sibling_conflict": False},
        file_map={PR: {"tools/kanban/cli.py"}, other: {"tools/kanban/cli.py"}})
    assert verdict["merged"] is True


# ── the happy path, and the confirmation that makes it honest ───────────────

def test_merges_and_confirms_when_every_gate_passes(one_pr_task, gate_passes):
    verdict, w = _land(_state())
    assert verdict["ok"] is True and verdict["merged"] is True
    assert w.merge_calls == [PR]
    assert "confirmed" in verdict["reason"]


def test_merge_request_alone_is_not_a_landing(one_pr_task, gate_passes):
    """gh pr merge --auto exits 0 while GitHub queues the merge. A requested
    merge that never reaches MERGED must NOT report success."""
    verdict, w = _land(_state(), merged_after=False)
    assert w.merge_calls == [PR], "the merge was never attempted"
    assert verdict["ok"] is False and verdict["merged"] is False
    assert "NOT marked done" in verdict["reason"]


def test_failed_gh_merge_refuses(one_pr_task, gate_passes):
    verdict, _w = _land(_state(), merge_ok=False)
    assert verdict["ok"] is False and verdict["merged"] is False


def test_an_already_merged_pr_needs_no_second_merge(one_pr_task, gate_passes):
    verdict, w = _land(_state(state="MERGED"))
    assert verdict["merged"] is True
    assert w.merge_calls == [], "re-merged an already merged PR"


def test_already_merged_still_obeys_the_enforced_gate(one_pr_task, monkeypatch):
    """A merged PR does not excuse a failed conformance review."""
    monkeypatch.setattr(prw, "_enforced_done_ok",
                        lambda _c, _t: (False, "enforced gate: conformance review_passed=false"))
    verdict, _w = _land(_state(state="MERGED"))
    assert verdict["ok"] is False and verdict["merged"] is False


def test_dry_run_never_merges(one_pr_task, gate_passes):
    w = _FakeWatcher(_state())
    verdict = land.land("t-1", get_conn=lambda: None, watcher=w, dry_run=True)
    assert verdict["ok"] is True
    assert verdict["merged"] is False
    assert w.merge_calls == []


# ── strictly stricter than --force-done ─────────────────────────────────────

_BYPASS_CASES = [
    "env_toggle_off",     # KANBAN_REQUIRE_MERGE_FOR_DONE=0
    "git_unreachable",    # the primitive raises -> gate fails open
    "no_unmerged_branch",  # nothing found -> gate allows
]


@pytest.mark.parametrize("case", _BYPASS_CASES)
def test_merge_cannot_succeed_where_plain_done_would_be_allowed(
        case, monkeypatch, one_pr_task, gate_passes):
    """--merge must be strictly HARDER to satisfy than the refusal it replaces.

    In each of these cases the plain gate lets `--set-status done` through with
    no verification at all — that is the bypass surface. --merge inherits NONE
    of it: with a red PR it still refuses, so it can never be the softer path.
    """
    if case == "env_toggle_off":
        monkeypatch.setenv("KANBAN_REQUIRE_MERGE_FOR_DONE", "0")
        monkeypatch.setattr(kb, "_branch_has_unmerged_commits", lambda _t: True)
    elif case == "git_unreachable":
        monkeypatch.delenv("KANBAN_REQUIRE_MERGE_FOR_DONE", raising=False)

        def _boom(_t):
            raise RuntimeError("git unavailable")

        monkeypatch.setattr(kb, "_branch_has_unmerged_commits", _boom)
    else:
        monkeypatch.delenv("KANBAN_REQUIRE_MERGE_FOR_DONE", raising=False)
        monkeypatch.setattr(kb, "_branch_has_unmerged_commits", lambda _t: False)

    # Plain done: allowed (this is the bypass this test is pinned against).
    assert cli._refuses_done("t-1") == ""

    # --merge, same environment, a PR that is not landable: still refused.
    verdict, w = _land(_state(statusCheckRollup=[
        {"name": "Test", "conclusion": "FAILURE"}]))
    assert verdict["ok"] is False, (
        f"--merge succeeded under the '{case}' bypass — it is not stricter")
    assert w.merge_calls == []


def test_merge_ignores_the_gate_disable_switch_entirely(
        monkeypatch, one_pr_task, gate_passes):
    """KANBAN_REQUIRE_MERGE_FOR_DONE=0 disables the local heuristic. It must not
    disable a single landing check."""
    monkeypatch.setenv("KANBAN_REQUIRE_MERGE_FOR_DONE", "0")
    verdict, _w = _land(_state(state="CLOSED"))
    assert verdict["ok"] is False


def test_merge_and_force_done_are_mutually_exclusive(capsys):
    rc = cli.cmd_set_status(["t-1"], "done", json_out=False, merge=True,
                            force_done=True, reason="because",
                            lander=lambda *a, **k: pytest.fail("lander ran"))
    assert rc == 1
    assert "mutually exclusive" in capsys.readouterr().err


def test_merge_refuses_a_batch(capsys):
    """Merging is an irreversible per-task side effect — no partial batches."""
    rc = cli.cmd_set_status(["t-1", "t-2"], "done", json_out=False, merge=True,
                            lander=lambda *a, **k: pytest.fail("lander ran"))
    assert rc == 1
    assert "single task id" in capsys.readouterr().err


def test_merge_only_applies_to_done(capsys):
    rc = cli.cmd_set_status(["t-1"], "backlog", json_out=False, merge=True,
                            lander=lambda *a, **k: pytest.fail("lander ran"))
    assert rc == 1
    assert "only applies to" in capsys.readouterr().err


# ── the CLI wiring: status + audit record ───────────────────────────────────

def _seed_task(task_id="t-merge-1", status="in_progress"):
    from tools.kanban.init_db import init_kanban_tables
    init_kanban_tables()
    with cli.get_connection() as conn:
        conn.execute("DELETE FROM kanban_tasks WHERE id = %s", (task_id,))
        conn.execute(
            "INSERT INTO kanban_tasks (id, title, description, status, priority, "
            "task_type, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (task_id, "landing test", "d", status, "high", "build",
             cli._now(), cli._now()),
        )
    return task_id


def _transitions(task_id):
    with cli.get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT to_status, actor, reason FROM kanban_status_transitions "
            "WHERE task_id = %s ORDER BY recorded_at", (task_id,)).fetchall()]


@pytest.mark.parametrize("mode", ["merge", "force_done"])
def test_merge_writes_the_same_audit_record_force_done_does(monkeypatch, mode):
    """Both routes to 'done' land an actor='manual' transition row carrying a
    reason that names WHY the gate was cleared."""
    task_id = _seed_task(f"t-audit-{mode}")

    if mode == "merge":
        rc = cli.cmd_set_status(
            [task_id], "done", json_out=True, merge=True,
            lander=lambda tid, **kw: {"task_id": tid, "ok": True, "merged": True,
                                      "pr_url": PR, "reason": "merged and confirmed",
                                      "checks": []})
    else:
        monkeypatch.setattr(kb, "_branch_has_unmerged_commits", lambda _t: True)
        rc = cli.cmd_set_status([task_id], "done", json_out=True,
                                force_done=True, reason="operator override")

    assert rc == 0
    with cli.get_connection() as conn:
        row = dict(conn.execute(
            "SELECT status, completed_at FROM kanban_tasks WHERE id = %s",
            (task_id,)).fetchone())
    assert row["status"] == "done" and row["completed_at"]

    rows = _transitions(task_id)
    assert rows, "no audit transition was recorded"
    last = rows[-1]
    assert last["to_status"] == "done"
    assert last["actor"] == "manual"
    assert last["reason"], "the audit reason was blank"
    if mode == "merge":
        assert PR in last["reason"] and "--merge" in last["reason"]
    else:
        assert "FORCED" in last["reason"]


def test_a_refused_merge_leaves_the_task_alone():
    task_id = _seed_task("t-audit-refused")
    rc = cli.cmd_set_status(
        [task_id], "done", json_out=False, merge=True,
        lander=lambda tid, **kw: {"task_id": tid, "ok": False, "merged": False,
                                  "pr_url": PR, "reason": "CI is red",
                                  "checks": [{"name": "ci_green", "ok": False,
                                              "detail": "a check failed"}]})
    assert rc == 1
    with cli.get_connection() as conn:
        row = dict(conn.execute(
            "SELECT status FROM kanban_tasks WHERE id = %s", (task_id,)).fetchone())
    assert row["status"] == "in_progress", "a refused merge still wrote done"


def test_merge_skips_the_local_heuristic_after_a_confirmed_merge(monkeypatch):
    """A PR observed MERGED on GitHub is stronger evidence than `git cherry`
    against a possibly-unfetched local origin/main, which would false-refuse."""
    task_id = _seed_task("t-audit-skip")
    monkeypatch.setattr(kb, "_branch_has_unmerged_commits", lambda _t: True)
    called = []
    monkeypatch.setattr(cli, "_refuses_done",
                        lambda t: called.append(t) or "blocked")

    rc = cli.cmd_set_status(
        [task_id], "done", json_out=True, merge=True,
        lander=lambda tid, **kw: {"task_id": tid, "ok": True, "merged": True,
                                  "pr_url": PR, "reason": "merged and confirmed",
                                  "checks": []})
    assert rc == 0
    assert called == []
