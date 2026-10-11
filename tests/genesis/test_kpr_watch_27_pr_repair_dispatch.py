# CUI // SP-CTI
"""kpr-watch-27: a red kanban PR whose worker exited gets ONE repair session.

MEASURED 2026-10-11 on PR #2430 (omx-guard-05): pr_watcher enqueued one resume,
saw it undelivered, escalated once, then held for 4+ hours -- nothing STARTS the
session that reads a post-run resume. The scheduler now does:
`_dispatch_pr_repairs` dispatches one repair worker per enqueued resume onto the
EXISTING branch, with the messages in its prompt (any adapter), writes the
drain receipt at dispatch, and leaves the task `pr_opened`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools.agents.adapter_base import AgentResult, AgentSession
from tools.airgap import hook_compat
from tools.ci import resume_delivery
from tools.genesis.reflexes import kanban
from tools.kanban import repair_dispatch

ROOT = Path(__file__).resolve().parents[2]
TASK = "omx-guard-05"
CI_FAIL = "CI failed on PR #2430: Test, Test Gates. tests/test_x.py::test_y FAILED"


# ----------------------------------------------------------------- fixtures

class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        assert "status = 'pr_opened'" in sql
        return _Rows(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Proc:
    """Popen-shaped handle."""

    def __init__(self, rc=None):
        self.rc = rc
        self.pid = 4242
        self.killed = False

    def poll(self):
        return self.rc

    def kill(self):
        self.killed = True


class _ClaudeLike:
    name = "claude_cli"

    def __init__(self, fail=False):
        self.sessions = []
        self.fail = fail

    def spawn(self, session, stdout=None, stderr=None):
        if self.fail:
            raise OSError("spawn failed")
        self.sessions.append(session)
        return _Proc()


class _OpencodeLike:
    """No spawn: the blocking invoke runs on a thread (_LLMTaskHandle)."""

    name = "opencode_cli"

    def __init__(self):
        self.sessions = []

    def invoke(self, session):
        self.sessions.append(session)
        return AgentResult(task_id=session.task_id, adapter_name=self.name,
                           completed=True, output="fixed")


@pytest.fixture
def board(monkeypatch, tmp_path):
    """A pr_opened task with one undelivered pr_watcher resume; every outside
    effect (git, gh, leases, DB, audit) replaced and recorded."""
    qdir = tmp_path / ".tmp" / "kanban" / "messages"
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", qdir)
    monkeypatch.setattr(kanban, "PROMPT_DIR", tmp_path / ".tmp" / "kanban")
    monkeypatch.setattr(kanban, "_running", {})
    monkeypatch.setattr(kanban, "_repairing", {})
    monkeypatch.setattr(kanban, "MAX_IN_PROGRESS", 3)
    monkeypatch.setattr(kanban, "_manual_build", lambda: False)
    monkeypatch.delenv("KANBAN_REPAIR_COOLDOWN_SECONDS", raising=False)

    from tools.kanban import scheduler_control
    state = {
        "paused": False, "lease_blocks": False, "pr": kanban.PR_OPEN,
        "acquire": True, "acquired": [], "released": [], "worktrees": [],
        "removed": [], "pushed": [], "moved": [], "audit": [],
        "adapter": _ClaudeLike(),
    }
    monkeypatch.setattr(scheduler_control, "is_paused", lambda: state["paused"])
    monkeypatch.setattr(kanban, "_lease_blocks_dispatch", lambda t: state["lease_blocks"])
    monkeypatch.setattr(kanban, "_pr_open_state", lambda t: state["pr"])
    monkeypatch.setattr(kanban, "get_connection", lambda: _Conn([{
        "id": TASK, "title": "guard parity", "task_type": "fix",
        "project_id": "omx", "executor_url": "https://github.com/x/y/pull/2430"}]))

    from tools.coordination import leases

    def acquire(resource, **kw):
        state["acquired"].append((resource, kw.get("intent")))
        return object() if state["acquire"] else None

    monkeypatch.setattr(leases, "acquire", acquire)
    monkeypatch.setattr(leases, "release", lambda r: state["released"].append(r))

    monkeypatch.setattr(kanban, "_configured_executor_chain", lambda: ["claude_cli"])
    monkeypatch.setattr(kanban, "_build_effective_executor_chain", lambda c: c)
    monkeypatch.setattr(kanban, "_pick_chain_adapter", lambda c, t=None: state["adapter"])
    monkeypatch.setattr(kanban, "_guard_parity_screen", lambda a, c, t, o: a)

    def make_wt(task_id):
        path = tmp_path / "wt" / f"{task_id}-repair"
        path.mkdir(parents=True, exist_ok=True)
        state["worktrees"].append(task_id)
        return str(path)

    monkeypatch.setattr(kanban, "_create_repair_worktree", make_wt)
    monkeypatch.setattr(kanban, "_remove_repair_worktree",
                        lambda t, p: state["removed"].append((t, p)))
    monkeypatch.setattr(kanban, "_push_repair",
                        lambda t, p: state["pushed"].append(t) or "pushed")
    monkeypatch.setattr(kanban, "_agent_session",
                        lambda task, instr, wd, dispatch_source="": AgentSession(
                            task_id=task["id"], prompt=instr, working_dir=wd,
                            timeout_seconds=900,
                            metadata={"dispatch_source": dispatch_source}))

    def no_move(*a, **kw):
        state["moved"].append(a)
        raise AssertionError("a PR repair must never move the task (kpr-watch-11)")

    monkeypatch.setattr(kanban, "_move_task", no_move)
    monkeypatch.setattr(kanban, "_audit_agent_execution",
                        lambda ev, t, **d: state["audit"].append((ev, t, d)))

    hook_compat.queue_message(TASK, CI_FAIL, sender="pr_watcher")
    return state


def _prompt(adapter):
    assert len(adapter.sessions) == 1
    return adapter.sessions[0].prompt


# ------------------------------------------------------------------ dispatch

def test_a_red_pr_with_an_undelivered_resume_gets_one_repair_session(board):
    assert kanban._dispatch_pr_repairs() == [TASK]

    prompt = _prompt(board["adapter"])
    assert CI_FAIL in prompt, "the CI-failure resume must be IN the prompt"
    assert "git push origin HEAD:refs/heads/kanban/omx-guard-05" in prompt
    assert "never" in prompt.lower() and "new PR" in prompt
    assert board["adapter"].sessions[0].metadata["dispatch_source"] == "repair_dispatch"
    assert board["worktrees"] == [TASK]
    assert board["acquired"] == [(f"kanban:task:{TASK}", "kanban-pr-repair")]
    assert TASK in kanban._repairing
    assert board["moved"] == []

    # Delivered: queue drained, receipt written by THIS reader, ledger row.
    assert resume_delivery.pending_messages(TASK) == []
    receipts = resume_delivery.read_receipts(TASK)
    assert len(receipts) == 1
    assert receipts[0]["reader"] == "repair_dispatch"
    assert receipts[0]["senders"] == {"pr_watcher": 1}
    assert len(repair_dispatch.read_ledger(TASK)) == 1
    assert board["audit"][0][0] == "agent_execution_started"
    assert board["audit"][0][2]["mode"] == "pr_repair"


def test_an_opencode_task_gets_the_same_prompt_delivery(board):
    board["adapter"] = _OpencodeLike()
    assert kanban._dispatch_pr_repairs() == [TASK]
    handle = kanban._repairing[TASK]["handle"]
    handle.wait(timeout=5)
    assert handle.returncode == 0, "the invoke-only adapter ran to completion"

    assert CI_FAIL in _prompt(board["adapter"])
    assert resume_delivery.read_receipts(TASK)[0]["reader"] == "repair_dispatch"
    assert resume_delivery.pending_messages(TASK) == []


@pytest.mark.parametrize("knob, value", [
    ("lease_blocks", True),        # a --claim'd task: a human is on it
    ("paused", True),              # runner paused
    ("pr", kanban.PR_NONE),        # PR closed / merged
    ("pr", kanban.PR_UNKNOWN),     # cannot tell -> do not spend a session
])
def test_claimed_paused_or_closed_pr_is_not_repaired(board, knob, value):
    board[knob] = value
    assert kanban._dispatch_pr_repairs() == []
    assert board["adapter"].sessions == []
    assert board["worktrees"] == []
    assert len(resume_delivery.pending_messages(TASK)) == 1, "the resume stays queued"
    assert resume_delivery.read_receipts(TASK) == []


def test_a_lease_lost_at_acquire_is_not_repaired(board):
    board["acquire"] = False
    assert kanban._dispatch_pr_repairs() == []
    assert board["adapter"].sessions == []
    assert len(resume_delivery.pending_messages(TASK)) == 1


def test_a_task_without_a_pending_resume_is_not_repaired(board):
    hook_compat.check_message_queue(TASK)  # drained by someone else
    assert kanban._dispatch_pr_repairs() == []
    assert board["adapter"].sessions == []


def test_a_second_sweep_inside_the_cooldown_does_not_redispatch(board):
    assert kanban._dispatch_pr_repairs() == [TASK]
    kanban._repairing[TASK]["handle"].rc = 0
    assert kanban._reap_pr_repairs() == [TASK]

    hook_compat.queue_message(TASK, "CI failed again", sender="pr_watcher")
    assert kanban._dispatch_pr_repairs() == []
    assert len(board["adapter"].sessions) == 1
    assert len(resume_delivery.pending_messages(TASK)) == 1


def test_after_the_cooldown_the_next_resume_gets_the_next_repair(board, monkeypatch):
    monkeypatch.setenv("KANBAN_REPAIR_COOLDOWN_SECONDS", "0")
    assert kanban._dispatch_pr_repairs() == [TASK]
    kanban._repairing[TASK]["handle"].rc = 0
    kanban._reap_pr_repairs()
    hook_compat.queue_message(TASK, "CI failed again", sender="pr_watcher")
    assert kanban._dispatch_pr_repairs() == [TASK]
    assert len(board["adapter"].sessions) == 2


def test_a_live_repair_is_not_doubled(board, monkeypatch):
    monkeypatch.setenv("KANBAN_REPAIR_COOLDOWN_SECONDS", "0")
    assert kanban._dispatch_pr_repairs() == [TASK]
    hook_compat.queue_message(TASK, "more", sender="pr_watcher")
    assert kanban._dispatch_pr_repairs() == []
    assert len(board["adapter"].sessions) == 1


def test_a_repair_counts_against_capacity(board, monkeypatch):
    monkeypatch.setattr(kanban, "_running", {"a": 1, "b": 2, "c": 3})
    assert kanban._dispatch_pr_repairs() == []
    monkeypatch.setattr(kanban, "_running", {"a": 1, "b": 2})
    assert kanban._dispatch_pr_repairs() == [TASK]
    assert kanban._busy_slots() == 3


def test_a_failed_spawn_puts_the_resume_back_and_writes_no_receipt(board):
    board["adapter"] = _ClaudeLike(fail=True)
    assert kanban._dispatch_pr_repairs() == []
    pending = resume_delivery.pending_messages(TASK)
    assert [m["content"] for m in pending] == [CI_FAIL]
    assert resume_delivery.read_receipts(TASK) == []
    assert repair_dispatch.read_ledger(TASK) == []
    assert board["removed"] and board["released"] == [f"kanban:task:{TASK}"]
    assert TASK not in kanban._repairing


def test_reaping_pushes_cleans_up_and_leaves_the_task_pr_opened(board):
    kanban._dispatch_pr_repairs()
    assert kanban._reap_pr_repairs() == [], "a live session is not reaped"
    kanban._repairing[TASK]["handle"].rc = 1
    assert kanban._reap_pr_repairs() == [TASK]
    assert board["pushed"] == [TASK]
    assert board["removed"] and board["released"] == [f"kanban:task:{TASK}"]
    assert board["moved"] == []
    assert board["audit"][-1][0] == "agent_execution_completed"


def test_a_repair_past_its_timeout_is_killed_and_reaped(board):
    from datetime import datetime, timedelta, timezone

    kanban._dispatch_pr_repairs()
    entry = kanban._repairing[TASK]
    entry["started"] = datetime.now(timezone.utc) - timedelta(seconds=entry["timeout"] + 5)
    assert kanban._reap_pr_repairs() == [TASK]
    assert entry["handle"].killed


# ---------------------------------------------------------- git + structure

def test_the_repair_worktree_is_detached_on_the_existing_branch(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(kanban, "_task_repo_root", lambda t: tmp_path)
    monkeypatch.setattr(kanban, "_task_worktree_path", lambda t: tmp_path / "wts" / t)
    monkeypatch.setattr(kanban, "_quiet_git", lambda args, cwd: calls.append(args) or (0, ""))
    path = kanban._create_repair_worktree(TASK)
    assert path == str(tmp_path / "wts" / f"{TASK}-repair")
    add = [c for c in calls if c[:2] == ["worktree", "add"]][0]
    assert add == ["worktree", "add", "--detach", path, f"origin/kanban/{TASK}"]
    assert not any("-b" in c or "branch" in c for c in calls), "never a new branch"


def test_push_repair_never_forces_and_refuses_a_diverged_branch(monkeypatch):
    calls = []
    answers = {"status": (0, ""), "rev-parse HEAD": (0, "aaa\n"),
               "rev-parse origin": (0, "bbb\n"), "merge-base": (1, "")}

    def git(args, cwd):
        calls.append(args)
        key = "rev-parse HEAD" if args == ["rev-parse", "HEAD"] else (
            "rev-parse origin" if args[0] == "rev-parse" else args[0])
        return answers.get(key, (0, ""))

    monkeypatch.setattr(kanban, "_quiet_git", git)
    assert kanban._push_repair(TASK, "/wt") == "diverged"
    assert not any(c[0] == "push" for c in calls)

    answers["merge-base"] = (0, "")
    calls.clear()
    assert kanban._push_repair(TASK, "/wt") == "pushed"
    push = [c for c in calls if c[0] == "push"][0]
    assert push == ["push", "origin", f"HEAD:refs/heads/kanban/{TASK}"]
    assert not any("--force" in a or a.startswith("+") for a in push)


def test_the_cycle_runs_the_sweep_after_the_drain_and_before_dispatch():
    src = Path(kanban.__file__).read_text(encoding="utf-8")
    run_src = src[src.index("\ndef _run_cycle(config"):]
    drain = run_src.index("_code_reload.is_draining()")
    sweep = run_src.index("_dispatch_pr_repairs()")
    reap = run_src.index("_reap_pr_repairs()")
    first_dispatch = min(m.start() for m in re.finditer(r"_dispatch_to_claude\(", run_src))
    assert reap < run_src.index("if _busy_slots() >= MAX_IN_PROGRESS")
    assert drain < sweep < first_dispatch


def test_the_undelivered_hold_names_the_sweep():
    src = (ROOT / "tools" / "ci" / "pr_watcher.py").read_text(encoding="utf-8")
    hold = src[src.index('f"undelivered hold: '):]
    hold = hold[:hold.index(")[:500]")]
    assert "_dispatch_pr_repairs" in hold and "kpr-watch-27" in hold


# ------------------------------------------------------------- the module

def test_a_claim_is_atomic_against_a_concurrent_append(monkeypatch, tmp_path):
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", tmp_path / "messages")
    hook_compat.queue_message(TASK, "first", sender="pr_watcher")
    claim = repair_dispatch.claim_messages(TASK)
    hook_compat.queue_message(TASK, "arrived mid-dispatch", sender="pr_watcher")
    assert [m["content"] for m in claim.messages] == ["first"]

    assert claim.release()
    assert [m["content"] for m in resume_delivery.pending_messages(TASK)] == [
        "first", "arrived mid-dispatch"]
    assert not list((tmp_path / "messages").glob("*.repair_dispatch-*"))


def test_commit_receipts_only_what_was_claimed(monkeypatch, tmp_path):
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", tmp_path / "messages")
    hook_compat.queue_message(TASK, "first", sender="pr_watcher")
    claim = repair_dispatch.claim_messages(TASK)
    hook_compat.queue_message(TASK, "later", sender="pr_watcher")
    claim.commit()
    assert resume_delivery.read_receipts(TASK)[0]["count"] == 1
    assert [m["content"] for m in resume_delivery.pending_messages(TASK)] == ["later"]


def test_the_cooldown_defaults_to_the_watchers(monkeypatch):
    from tools.ci.pr_watcher import RESUME_COOLDOWN_SECONDS

    monkeypatch.delenv("KANBAN_REPAIR_COOLDOWN_SECONDS", raising=False)
    assert repair_dispatch.cooldown_seconds() == RESUME_COOLDOWN_SECONDS


def test_the_survey_counts_ledger_rows(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", tmp_path / "messages")
    repair_dispatch.record_dispatch(TASK, adapter="claude_cli", worktree="/w", messages=1)
    assert repair_dispatch.main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["repairs"] == 1 and data["per_task"] == {TASK: 1}
