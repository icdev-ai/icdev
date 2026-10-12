# CUI // SP-CTI
"""kpr-watch-28: a PR repair session orphaned by a scheduler restart is reaped.

kpr-watch-27 tracks an in-flight repair only in the scheduler's in-memory
``_repairing``. A scheduler killed or re-exec'd mid-repair never ran
``_reap_pr_repairs`` for it: the receipt was written and the queue claimed (so
nothing re-dispatched), the ``_push_repair`` backstop never ran (an opencode /
pi session that only EDITED lost its fix), and the worktree lingered. The
dispatch ledger now records the in-flight repair, and
``_reap_orphaned_pr_repairs`` finishes every one whose owner AND session are
dead: backstop push (never forced), worktree removed, and -- when nothing
landed -- the resume re-queued with its receipt reversed, bounded by
``max_resume_cycles_per_task``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools.airgap import hook_compat
from tools.ci import resume_delivery
from tools.genesis.reflexes import kanban
from tools.kanban import repair_dispatch

TASK = "omx-guard-05"
CI_FAIL = "CI failed on PR #2430: Test. tests/test_x.py::test_y FAILED"
DEAD_SCHEDULER = 91001
SESSION = 91002


@pytest.fixture
def orphan(monkeypatch, tmp_path):
    """A repair dispatched by a scheduler that has since died: queue claimed,
    receipt written, ledger row on disk, worktree present, `_repairing` empty."""
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR",
                        tmp_path / ".tmp" / "kanban" / "messages")
    monkeypatch.setattr(kanban, "_repairing", {})
    monkeypatch.delenv("KANBAN_REPAIR_MAX_REQUEUES", raising=False)

    state = {"alive": set(), "pushed": [], "removed": [], "audit": [],
             "outcome": "no_change", "remote": None, "holder": {},
             "on_push": None}
    monkeypatch.setattr(repair_dispatch, "_pid_alive",
                        lambda pid, since=None: pid in state["alive"])

    def push(task_id, path):
        state["pushed"].append((task_id, path))
        if state["on_push"]:
            state["on_push"]()
        return state["outcome"]

    monkeypatch.setattr(kanban, "_push_repair", push)
    monkeypatch.setattr(kanban, "_repair_remote_head",
                        lambda t, p: state["remote"])
    monkeypatch.setattr(kanban, "_remove_repair_worktree",
                        lambda t, p: state["removed"].append((t, p)))
    monkeypatch.setattr(kanban, "_audit_agent_execution",
                        lambda ev, t, **d: state["audit"].append((ev, t, d)))

    # A hard `kanban:task:<id>` lease shared by every "scheduler" in the test.
    from tools.coordination import leases

    def acquire(resource, **kw):
        if resource in state["holder"]:
            return None
        state["holder"][resource] = kw.get("intent")
        return object()

    monkeypatch.setattr(leases, "acquire", acquire)
    monkeypatch.setattr(leases, "release",
                        lambda r: state["holder"].pop(r, None) is not None)
    monkeypatch.setattr(leases, "release_stale", lambda r: False)

    wt = tmp_path / "wt" / f"{TASK}-repair"
    wt.mkdir(parents=True)
    state["worktree"] = str(wt)

    hook_compat.queue_message(TASK, CI_FAIL, sender="pr_watcher")
    claim = repair_dispatch.claim_messages(TASK)
    claim.commit()
    state["row"] = repair_dispatch.record_dispatch(
        TASK, adapter="opencode_cli", worktree=str(wt),
        messages=claim.messages, session_pid=SESSION, base_sha="base0",
    )["entry"]
    # Pretend a DIFFERENT, now-dead scheduler wrote it.
    _rewrite_owner(state["row"]["dispatch_id"], DEAD_SCHEDULER)
    assert resume_delivery.pending_messages(TASK) == []
    return state


def _rewrite_owner(dispatch_id, pid):
    lp = repair_dispatch.ledger_path()
    text = lp.read_text(encoding="utf-8")
    import json
    rows = [json.loads(x) for x in text.splitlines() if x.strip()]
    for r in rows:
        if r.get("dispatch_id") == dispatch_id:
            r["pid"] = pid
    lp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _receipted():
    return resume_delivery.receipted_count(resume_delivery.read_receipts(TASK))


# ------------------------------------------------------------------- reaping

def test_a_dead_repair_that_landed_nothing_is_pushed_cleaned_and_requeued(orphan):
    assert kanban._reap_orphaned_pr_repairs() == [TASK]

    assert orphan["pushed"] == [(TASK, orphan["worktree"])], "backstop ran once"
    assert orphan["removed"] == [(TASK, orphan["worktree"])]
    pending = resume_delivery.pending_messages(TASK)
    assert [m["content"] for m in pending] == [CI_FAIL], "the resume is back"
    assert _receipted() == 0, "the drain receipt is reversed"
    assert repair_dispatch.in_flight() == []
    assert orphan["holder"] == {}, "the task lease is released"
    assert orphan["audit"][-1][2]["mode"] == "pr_repair_orphan"

    # Reaped ONCE: a second sweep finds nothing in flight.
    assert kanban._reap_orphaned_pr_repairs() == []
    assert len(orphan["pushed"]) == 1


def test_a_pushed_fix_is_not_requeued(orphan):
    orphan["outcome"] = "pushed"
    assert kanban._reap_orphaned_pr_repairs() == [TASK]
    assert resume_delivery.pending_messages(TASK) == []
    assert _receipted() == 1


def test_a_session_that_pushed_itself_is_not_requeued(orphan):
    """`no_change` with the remote PAST the dispatch base = the session pushed."""
    orphan["remote"] = "fixed1"
    assert kanban._reap_orphaned_pr_repairs() == [TASK]
    assert resume_delivery.pending_messages(TASK) == []


def test_a_diverged_branch_is_left_unforced_and_not_requeued(orphan):
    orphan["outcome"] = "diverged"
    orphan["remote"] = "someone-else"
    assert kanban._reap_orphaned_pr_repairs() == [TASK]
    assert orphan["removed"], "the worktree is still cleaned"
    assert resume_delivery.pending_messages(TASK) == []
    finished = [r for r in repair_dispatch.read_ledger(TASK)
                if r.get("event") == "finished"]
    assert finished[-1]["outcome"] == "diverged"


@pytest.mark.parametrize("alive", [SESSION, DEAD_SCHEDULER])
def test_a_live_owner_or_live_session_is_left_alone(orphan, alive):
    orphan["alive"].add(alive)
    assert kanban._reap_orphaned_pr_repairs() == []
    assert orphan["pushed"] == [] and orphan["removed"] == []
    assert resume_delivery.pending_messages(TASK) == []
    assert len(repair_dispatch.in_flight()) == 1


def test_a_repair_this_process_still_holds_is_left_alone(orphan):
    kanban._repairing[TASK] = {"dispatch_id": orphan["row"]["dispatch_id"]}
    assert kanban._reap_orphaned_pr_repairs() == []
    assert orphan["pushed"] == []


def test_two_schedulers_starting_together_reap_once(orphan):
    """B sweeps while A holds the task lease, and again after A finished."""
    orphan["on_push"] = lambda: kanban._reap_orphaned_pr_repairs()  # B, mid-reap
    assert kanban._reap_orphaned_pr_repairs() == [TASK]             # A
    orphan["on_push"] = None
    assert kanban._reap_orphaned_pr_repairs() == []                 # B, late
    assert len(orphan["pushed"]) == 1
    assert len(resume_delivery.pending_messages(TASK)) == 1, "re-queued once"


def test_a_task_lease_held_by_a_live_session_blocks_the_reap(orphan):
    orphan["holder"][f"kanban:task:{TASK}"] = "a human --claim"
    assert kanban._reap_orphaned_pr_repairs() == []
    assert orphan["pushed"] == []


def test_a_crash_loop_cannot_requeue_without_bound(orphan, monkeypatch):
    monkeypatch.setenv("KANBAN_REPAIR_MAX_REQUEUES", "1")
    assert kanban._reap_orphaned_pr_repairs() == [TASK]
    assert len(resume_delivery.pending_messages(TASK)) == 1

    # The re-dispatched repair is orphaned again by the next crash.
    claim = repair_dispatch.claim_messages(TASK)
    claim.commit()
    row = repair_dispatch.record_dispatch(
        TASK, adapter="opencode_cli", worktree=orphan["worktree"],
        messages=claim.messages, session_pid=SESSION, base_sha="base0")["entry"]
    _rewrite_owner(row["dispatch_id"], DEAD_SCHEDULER)

    assert kanban._reap_orphaned_pr_repairs() == [TASK]
    assert len(orphan["pushed"]) == 2, "the backstop still runs"
    assert resume_delivery.pending_messages(TASK) == [], "budget spent: not re-queued"


def test_the_requeue_budget_defaults_to_the_watchers(monkeypatch):
    from tools.ci import pr_watcher

    monkeypatch.delenv("KANBAN_REPAIR_MAX_REQUEUES", raising=False)
    assert repair_dispatch.max_requeues() == int(
        pr_watcher.load_config().get("max_resume_cycles_per_task", 5))


def test_a_missing_worktree_is_requeued_without_a_push(orphan):
    import shutil

    shutil.rmtree(orphan["worktree"])
    assert kanban._reap_orphaned_pr_repairs() == [TASK]
    assert orphan["pushed"] == []
    assert len(resume_delivery.pending_messages(TASK)) == 1


def test_a_legacy_ledger_row_is_never_reaped(orphan):
    """kpr-watch-27 rows carry no dispatch_id and no messages: not in flight."""
    kanban._reap_orphaned_pr_repairs()
    repair_dispatch.record_dispatch(TASK, adapter="claude_cli", worktree="/w",
                                    messages=1)
    lp = repair_dispatch.ledger_path()
    import json
    rows = [json.loads(x) for x in lp.read_text(encoding="utf-8").splitlines()]
    rows[-1].pop("dispatch_id")
    rows[-1]["pid"] = DEAD_SCHEDULER
    lp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert repair_dispatch.in_flight() == []


def test_a_normally_reaped_repair_is_not_an_orphan(orphan):
    """`_reap_pr_repairs` closes the ledger row it opened."""

    class _Done:
        pid = SESSION

        def poll(self):
            return 0

    from datetime import datetime, timezone

    kanban._repairing[TASK] = {
        "handle": _Done(), "worktree": orphan["worktree"], "log_fh": None,
        "started": datetime.now(timezone.utc), "adapter": "opencode_cli",
        "timeout": 900, "leased": False,
        "dispatch_id": orphan["row"]["dispatch_id"],
    }
    assert kanban._reap_pr_repairs() == [TASK]
    assert repair_dispatch.in_flight() == []
    assert kanban._reap_orphaned_pr_repairs() == []
    assert len(orphan["pushed"]) == 1


def test_the_cooldown_still_counts_dispatch_rows_only(orphan):
    kanban._reap_orphaned_pr_repairs()
    assert repair_dispatch.in_cooldown(TASK), "re-queued, but not re-dispatchable yet"
    assert repair_dispatch.survey()["repairs"] == 1


def test_the_cycle_reaps_orphans_before_the_live_reap():
    src = Path(kanban.__file__).read_text(encoding="utf-8")
    run_src = src[src.index("\ndef _run_cycle(config"):]
    orphan_reap = run_src.index("_reap_orphaned_pr_repairs()")
    assert orphan_reap < run_src.index("_reap_pr_repairs()")
    assert orphan_reap < run_src.index("_dispatch_pr_repairs()")


def test_dispatch_records_what_an_orphan_reap_needs():
    src = Path(kanban.__file__).read_text(encoding="utf-8")
    body = src[src.index("\ndef _dispatch_pr_repair(task"):]
    body = body[:body.index("\ndef ", 1)]
    call = body[body.index("repair_dispatch.record_dispatch("):]
    call = call[:call.index(")\n")]
    for kw in ("messages=claim.messages", "session_pid=", "base_sha="):
        assert re.search(re.escape(kw), call), kw
