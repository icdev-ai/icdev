# CUI // SP-CTI
"""Two claims authored from confirmed incidents (autonomy-cov-01).

autonomy-lrn-01 made the gap measurable and deliberately closed none of it:
115 done `fix` cards in 14 days, 5 guarded, 110 UNGUARDED — 4.3% coverage.
Nothing seeds a claim automatically, because the two derivations must not share
code and only whoever understands the defect can write the second one.

A THIRD CLAIM WAS AUTHORED AND WITHDRAWN, which is the part worth recording.
`done_is_not_reversed` was written for kpr-dup-09 (a task flipping done<->backlog
95 times in 5.5 hours). Run against the live board it DISAGREED on 276 tasks —
far too many for that defect. Inspecting two of them showed the real cause: the
board's final move to `done` was never RECORDED. `aca-hyg-06`'s last logged
transition is `in_progress -> needs_decomposition`, six minutes before the board
says done; `ace-chat-04`'s is `done -> needs_decomposition`, ten minutes before.

That is an INCOMPLETE TRANSITION LOG — a writer bypassing `_move_task` — not two
writers fighting. Shipping the claim with kpr-dup-09's description over that
evidence would have been the exact over-claiming this registry exists to catch,
and it would have fired 276 times on day one until somebody switched it off.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.awareness import claims as C  # noqa: E402
from tools.awareness.claim_verifier import UNMEASURABLE, Claim, verify  # noqa: E402


def _claim(claim_id: str) -> Claim:
    return next(c for c in C.REGISTRY if c.claim_id == claim_id)


# --------------------------------------------------------------------------- #
# 1. Both claims are registered, cited, and independently derived
# --------------------------------------------------------------------------- #
def test_both_claims_cite_an_incident_that_actually_happened():
    """Seeding only from PROVEN defects is what stops this becoming another
    capability that reports clean because it does nothing."""
    for claim_id, task_id in (
        ("held_task_lease_has_a_live_holder", "rem-hyg-15"),
        ("dispatchable_task_has_no_open_pr", "rem-hyg-18"),
    ):
        claim = _claim(claim_id)
        assert claim.incident is not None, f"{claim_id} cites no incident"
        assert task_id in claim.incident.task_ids
        assert claim.incident.observed_on
        assert claim.incident.fixed_by


def test_the_two_sides_share_no_implementation():
    """If the verifier calls what the surface calls, it proves the function is
    deterministic — which was never in question."""
    for claim_id in ("held_task_lease_has_a_live_holder",
                     "dispatchable_task_has_no_open_pr"):
        claim = _claim(claim_id)
        assert claim.reported is not claim.derived
        assert claim.reported.__code__ is not claim.derived.__code__


# --------------------------------------------------------------------------- #
# 2. The lease claim — cannot-tell counts as ALIVE
# --------------------------------------------------------------------------- #
def test_a_holder_that_cannot_be_tested_counts_as_alive(monkeypatch):
    """`holder_is_alive` returns None when it cannot tell, and treating that as
    dead is precisely how a live worker loses its lease — the error rem-hyg-15
    made on its first probe, reaping a lease whose task had heartbeat four
    seconds earlier."""
    from tools.coordination import leases

    monkeypatch.setattr(C, "_reported_task_leases", lambda: ["kanban:task:t-1"])
    monkeypatch.setattr(leases, "holder_is_alive", lambda _r: None)

    assert C._derived_leases_with_a_live_holder() == ["kanban:task:t-1"]


def test_a_dead_pid_that_is_still_heartbeating_counts_as_alive(monkeypatch):
    """A dead pid is not dead work: the lease records the DISPATCHING pid, which
    exits after handoff. adm-03 requires the heartbeat as a second signal."""
    from tools.coordination import leases

    monkeypatch.setattr(C, "_reported_task_leases", lambda: ["kanban:task:t-2"])
    monkeypatch.setattr(leases, "holder_is_alive", lambda _r: False)
    monkeypatch.setattr(C, "_task_is_heartbeating", lambda _t: True)

    assert C._derived_leases_with_a_live_holder() == ["kanban:task:t-2"]


def test_a_dead_pid_with_no_heartbeat_is_the_finding(monkeypatch):
    """Both signals gone. This is the lease that pinned three tasks while the
    board reported idle with free capacity."""
    from tools.coordination import leases

    monkeypatch.setattr(C, "_reported_task_leases", lambda: ["kanban:task:t-3"])
    monkeypatch.setattr(leases, "holder_is_alive", lambda _r: False)
    monkeypatch.setattr(C, "_task_is_heartbeating", lambda _t: False)

    assert C._derived_leases_with_a_live_holder() == []


def test_an_unreadable_heartbeat_assumes_alive(monkeypatch):
    def _boom(_t):
        raise RuntimeError("board unreachable")

    monkeypatch.setattr("tools.kanban.lease_liveness.task_is_heartbeating", _boom)
    assert C._task_is_heartbeating("t-4") is True


def test_no_leases_held_is_unmeasurable_not_agreement(monkeypatch):
    """`[] == []` compares nothing. A board with no leases held has not been
    checked — it has nothing to check."""
    monkeypatch.setattr(C, "_reported_task_leases", lambda: [])
    monkeypatch.setattr(C, "_derived_leases_with_a_live_holder", lambda: [])

    claim = _claim("held_task_lease_has_a_live_holder")
    result = verify(Claim(
        claim_id=claim.claim_id, description=claim.description,
        reported=C._reported_task_leases,
        derived=C._derived_leases_with_a_live_holder,
        agree=claim.agree, tier=claim.tier,
    ))
    assert result.verdict == UNMEASURABLE


# --------------------------------------------------------------------------- #
# 3. The dispatch claim — an unreachable forge is not "no open PRs"
# --------------------------------------------------------------------------- #
def test_an_unreachable_forge_is_none_never_an_empty_answer(monkeypatch):
    """Returning [] would say "no dispatchable task has a PR", which reports
    every board clean whenever `gh` is unavailable."""
    monkeypatch.setattr(C, "_reported_dispatchable_tasks", lambda: ["t-1"])

    class _Fail:
        returncode = 1
        stdout = ""

    monkeypatch.setattr("subprocess.run", lambda *_a, **_k: _Fail())
    assert C._derived_tasks_without_an_open_pr() is None


def test_a_task_with_an_open_pr_is_dropped_from_the_derived_set(monkeypatch):
    monkeypatch.setattr(C, "_reported_dispatchable_tasks", lambda: ["t-1", "t-2"])

    class _Ok:
        returncode = 0
        stdout = '[{"headRefName": "kanban/t-2"}]'

    monkeypatch.setattr("subprocess.run", lambda *_a, **_k: _Ok())
    assert C._derived_tasks_without_an_open_pr() == ["t-1"]


def test_a_pr_on_an_unrelated_branch_does_not_match(monkeypatch):
    """The branch must be this task's own `kanban/<id>`, not merely contain it."""
    monkeypatch.setattr(C, "_reported_dispatchable_tasks", lambda: ["t-1"])

    class _Ok:
        returncode = 0
        stdout = '[{"headRefName": "feat/t-1-something-else"}]'

    monkeypatch.setattr("subprocess.run", lambda *_a, **_k: _Ok())
    assert C._derived_tasks_without_an_open_pr() == ["t-1"]


def test_an_unreadable_board_is_none_on_both_sides(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(C, "_reported_dispatchable_tasks", _boom)
    try:
        got = C._derived_tasks_without_an_open_pr()
    except RuntimeError:
        got = "raised"
    assert got in (None, "raised")


# --------------------------------------------------------------------------- #
# 4. The withdrawn claim must not come back without its evidence
# --------------------------------------------------------------------------- #
def test_the_withdrawn_claim_is_not_in_the_registry():
    """`done_is_not_reversed` disagreed on 276 tasks because the transition log
    is INCOMPLETE, not because tasks left `done`. Re-adding it with kpr-dup-09's
    description would assert something the evidence does not support — and it
    would fire 276 times on day one."""
    assert "done_is_not_reversed" not in {c.claim_id for c in C.REGISTRY}


# --------------------------------------------------------------------------- #
# 4. A live scheduler heartbeats (kpr-stale-03, 2026-09-02)
# --------------------------------------------------------------------------- #
def _fresh(minutes_ago: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def test_scheduler_heartbeat_claim_is_registered_and_cited():
    claim = _claim("scheduler_heartbeat_is_fresh")
    assert claim.tier == "propose"
    assert "kpr-stale-03" in claim.incident.task_ids
    assert claim.incident.observed_on == "2026-09-02"


def test_scheduler_sides_share_no_implementation():
    """One side reads the PROCESS TABLE, the other the REGISTRY TABLE."""
    import inspect

    reported = inspect.getsource(C._reported_scheduler_pids) + inspect.getsource(C._live_scheduler_pids)
    derived = (inspect.getsource(C._derived_scheduler_pids_heartbeating)
               + inspect.getsource(C._kanban_session_rows))
    assert "psutil" in reported and "psutil" not in derived
    assert "agent_sessions" in derived and "agent_sessions" not in reported


def test_a_live_scheduler_that_heartbeats_agrees(monkeypatch):
    monkeypatch.setattr(C, "_live_scheduler_pids", lambda: [36760])
    monkeypatch.setattr(C, "_kanban_session_rows", lambda: [(36760, _fresh(1))])
    assert verify(_claim("scheduler_heartbeat_is_fresh")).verdict == "agrees"


def test_a_live_scheduler_with_no_fresh_heartbeat_is_the_finding(monkeypatch):
    """pid 29880: alive five hours, never heartbeat, never restarted."""
    monkeypatch.setattr(C, "_live_scheduler_pids", lambda: [29880])
    monkeypatch.setattr(C, "_kanban_session_rows", lambda: [(29880, _fresh(300))])
    assert verify(_claim("scheduler_heartbeat_is_fresh")).verdict == "disagrees"


def test_a_dead_registry_row_for_a_gone_process_is_not_a_finding(monkeypatch):
    """A stale row for a pid that no longer exists is registry litter, not a
    silent scheduler -- reaping it is session_registry's job."""
    monkeypatch.setattr(C, "_live_scheduler_pids", lambda: [36760])
    monkeypatch.setattr(C, "_kanban_session_rows", lambda: [(36760, _fresh(1)), (29880, _fresh(600))])
    assert verify(_claim("scheduler_heartbeat_is_fresh")).verdict == "agrees"


def test_no_scheduler_and_no_rows_is_unmeasurable_not_agreement(monkeypatch):
    monkeypatch.setattr(C, "_live_scheduler_pids", lambda: [])
    monkeypatch.setattr(C, "_kanban_session_rows", lambda: [])
    assert verify(_claim("scheduler_heartbeat_is_fresh")).verdict == UNMEASURABLE


def test_an_unreadable_process_table_is_none_never_empty(monkeypatch):
    monkeypatch.setattr(C, "_live_scheduler_pids", lambda: None)
    monkeypatch.setattr(C, "_kanban_session_rows", lambda: [(1, _fresh(1))])
    assert verify(_claim("scheduler_heartbeat_is_fresh")).verdict == UNMEASURABLE


# --------------------------------------------------------------------------- #
# The derived side reads SCHEDULER rows, not every `kanban` row
# (claim-verif-33c9f4cd11, 2026-09-03)
# --------------------------------------------------------------------------- #
def _registry_with(monkeypatch, tmp_path, rows):
    """A scratch agent_sessions holding `rows` of (session_id, agent_type, pid, beat)."""
    from tools.coordination.session_registry import _DDL
    from tools.db.storage import get_connection

    db = tmp_path / "claims.db"
    monkeypatch.setattr(C, "_conn", lambda: get_connection(db_path=str(db)))
    conn = C._conn()
    conn.execute(_DDL)
    for sid, agent, pid, beat in rows:
        conn.execute(
            "INSERT INTO agent_sessions (session_id, agent_type, pid, host, cwd, "
            "started_at, last_heartbeat, current_intent, status) "
            "VALUES (%s, %s, %s, 'h', 'c', %s, %s, 'x', 'active')",
            (sid, agent, pid, beat, beat),
        )
    conn.commit()
    conn.close()


def test_derived_side_reads_scheduler_rows_not_every_kanban_row(monkeypatch, tmp_path):
    """Every process the scheduler spawns inherits ICDEV_AGENT=kanban, so a
    worker's coordination-hook row and a one-shot command's child row are
    `kanban` rows too. pid 31872 on the live card was one of those."""
    _registry_with(monkeypatch, tmp_path, [
        ("kanban-scheduler-22508", "kanban", 22508, _fresh(1)),
        ("d1c00d4c-3ebc-49e0-b638-5be623f0ff4a", "kanban", 31872, _fresh(1)),
        ("kanban-scheduler-22508/child-555", "kanban", 555, _fresh(1)),
    ])
    assert [pid for pid, _ in C._kanban_session_rows()] == [22508]
    assert C._derived_scheduler_pids_heartbeating() == [22508]


def test_no_scheduler_and_only_hook_rows_is_unmeasurable_not_agreement(monkeypatch, tmp_path):
    """A board with NO scheduler used to read `agrees` whenever a worker's hook
    row existed: reported [] is a subset of anything. Two empty sides now."""
    _registry_with(monkeypatch, tmp_path, [
        ("d1c00d4c-3ebc-49e0-b638-5be623f0ff4a", "kanban", 31872, _fresh(1)),
    ])
    monkeypatch.setattr(C, "_live_scheduler_pids", lambda: [])
    assert verify(_claim("scheduler_heartbeat_is_fresh")).verdict == UNMEASURABLE


def test_a_pre_sid_01_bare_name_row_is_still_a_scheduler_row(monkeypatch, tmp_path):
    _registry_with(monkeypatch, tmp_path, [("kanban-scheduler", "kanban", 4242, _fresh(1))])
    assert [pid for pid, _ in C._kanban_session_rows()] == [4242]
