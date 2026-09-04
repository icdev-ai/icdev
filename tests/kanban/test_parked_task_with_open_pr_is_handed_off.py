# CUI // SP-CTI
"""A task parked token_exhausted with an OPEN PR is handed to pr_watcher (mfx-own-01).

MEASURED 2026-09-03: rmf-rfp-01 and rmf-wp-02 parked `token exhaustion: parked
for retry 2/60` at 19:38 / 20:31 with PRs #2040 / #2042 OPEN and red on CI.
Their resume_at then slid forward every cycle for SIX HOURS (22:50 -> 23:31 ->
01:35 ...) and no worker ever started. Two guards, each correct on its own,
composed into a task nobody owned:

  * the dispatcher's respawn guard skips a task whose branch has an open PR
    (right -- it is what stops duplicate PRs), so every token-retry dispatch was
    refused and `_token_retry_backoff` pushed resume_at out again;
  * pr_watcher resumes only tasks it polls (`pr_opened`, `ci_failed`, ...), and
    `token_exhausted` is not in that set.

A human fixed both CI failures by hand and landed them through the door.

THE FIX IS A HAND-OFF, NOT A THIRD DISPATCHER. When the scheduler parks a task
token_exhausted, on every retry evaluation while it is parked, and at startup
recovery, an OPEN PR on `kanban/<id>` moves the task to `pr_opened` -- actor
scheduler (or startup-recovery), reason naming the PR number and the new owner
-- so the watcher's existing resume-on-CI-failure path owns it. The scheduler
never spawns a worker for a task in that state.

Everything here is injected: no gh, no database, no network.
"""

from __future__ import annotations

import inspect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import kanban as k  # noqa: E402
from tools.kanban import startup_recovery as sr  # noqa: E402

TASK = "rmf-rfp-01"
BRANCH = "kanban/rmf-rfp-01"


class _Conn:
    """A connection stub that answers SELECTs from *rows* and records writes."""

    def __init__(self, rows):
        self._rows = rows
        self.writes = []
        self.committed = False

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("SELECT"):
            self._last = [dict(r) for r in self._rows]
        else:
            self.writes.append((sql, params))
            self._last = []
        return self

    def fetchall(self):
        return getattr(self, "_last", [])

    def fetchone(self):
        rows = getattr(self, "_last", [])
        return rows[0] if rows else None

    def commit(self):
        self.committed = True

    def rollback(self):
        return None

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _prime_listing(monkeypatch, numbers_by_branch):
    """Make the per-cycle open-PR listing answer from *numbers_by_branch*."""
    monkeypatch.setattr(k, "_open_pr_head_branches",
                        lambda _root: set(numbers_by_branch))
    monkeypatch.setattr(k, "_open_pr_numbers_cache",
                        {str(k.BASE_DIR): (0.0, dict(numbers_by_branch))})
    monkeypatch.setattr(k, "_task_repo_root", lambda _tid: k.BASE_DIR)


# --------------------------------------------------------------------------- #
# 1. The forge lookup returns the PR NUMBER, or None on no evidence
# --------------------------------------------------------------------------- #
def test_open_pr_for_branch_returns_the_number(monkeypatch):
    _prime_listing(monkeypatch, {BRANCH: 2040, "kanban/rmf-wp-02": 2042})
    assert k._open_pr_for_branch(str(k.BASE_DIR), BRANCH) == 2040
    assert k._open_pr_for_branch(str(k.BASE_DIR), "kanban/rmf-wp-02") == 2042


def test_open_pr_for_branch_is_none_without_positive_evidence(monkeypatch):
    """An empty listing means no open PRs OR gh was unavailable. Both are None:
    the hand-off only ever moves a task on positive evidence its PR exists."""
    _prime_listing(monkeypatch, {})
    assert k._open_pr_for_branch(str(k.BASE_DIR), BRANCH) is None
    _prime_listing(monkeypatch, {"kanban/other": 7})
    assert k._open_pr_for_branch(str(k.BASE_DIR), BRANCH) is None


def test_the_listing_asks_the_forge_for_the_number_too():
    """The cached listing used to fetch headRefName only; the reason string
    names `#N`, so the same single gh call must carry the number."""
    src = inspect.getsource(k._open_pr_head_branches)
    assert "number" in src and "headRefName" in src


# --------------------------------------------------------------------------- #
# 2. The hand-off itself
# --------------------------------------------------------------------------- #
def test_a_parked_task_with_an_open_pr_moves_to_pr_opened(monkeypatch):
    _prime_listing(monkeypatch, {BRANCH: 2040})
    moves = []
    monkeypatch.setattr(k, "_move_task",
                        lambda tid, status, actor="scheduler", reason=None, **kw:
                        moves.append((tid, status, actor, reason)))
    cleared = []
    monkeypatch.setattr(k, "_clear_resume_at", lambda tid: cleared.append(tid))

    number = k._hand_parked_task_to_pr_watcher(TASK, context="at park")

    assert number == 2040
    assert moves == [(TASK, "pr_opened", "scheduler",
                      "open PR #2040 found while parked (at park); handed to pr_watcher")]
    assert cleared == [TASK], "a resume_at for a task the watcher owns is a lie"


def test_a_parked_task_without_a_pr_is_left_parked(monkeypatch):
    _prime_listing(monkeypatch, {"kanban/somebody-else": 9})
    moves = []
    monkeypatch.setattr(k, "_move_task",
                        lambda *a, **kw: moves.append((a, kw)))
    assert k._hand_parked_task_to_pr_watcher(TASK, context="retry evaluation") is None
    assert moves == []


def test_an_unavailable_forge_hands_nothing_off(monkeypatch):
    """gh down is 'could not ask', never 'no PR' -- and never 'PR' either."""
    _prime_listing(monkeypatch, {})
    moves = []
    monkeypatch.setattr(k, "_move_task", lambda *a, **kw: moves.append(a))
    assert k._hand_parked_task_to_pr_watcher(TASK, context="at park") is None
    assert moves == []


def test_the_hand_off_never_spawns_a_worker():
    """The scheduler's job here ends at the status write. Dispatching from
    this state is exactly the duplicate-PR the respawn guard exists to stop."""
    src = inspect.getsource(k._hand_parked_task_to_pr_watcher)
    assert "_dispatch_to_claude" not in src
    assert "_write_prompt_file" not in src


# --------------------------------------------------------------------------- #
# 3. Every retry evaluation asks, and a handed-off task is never 'ready'
# --------------------------------------------------------------------------- #
def test_retry_evaluation_hands_off_before_waiting_on_resume_at(monkeypatch):
    """The measured shape: resume_at in the past, PR open, dispatch refused,
    resume_at pushed out, repeat for six hours. The evaluation must hand the
    task off and NOT return it as a retry candidate."""
    rows = [{"id": TASK, "title": "t", "priority": "high",
             "updated_at": "2026-09-03T19:38:00+00:00",
             "max_retries": 5, "failure_count": 0}]
    monkeypatch.setattr(k, "get_connection", lambda *a, **kw: _Conn(rows))
    monkeypatch.setattr(k, "_load_resume_at",
                        lambda _tid: datetime.now(timezone.utc) - timedelta(hours=1))
    monkeypatch.setattr(k, "_get_retry_count", lambda _tid: 2)
    handed = []
    monkeypatch.setattr(k, "_hand_parked_task_to_pr_watcher",
                        lambda tid, *, context: handed.append((tid, context)) or 2040)
    dispatched = []
    monkeypatch.setattr(k, "_dispatch_to_claude",
                        lambda *a, **kw: dispatched.append(a))

    ready = k._check_token_exhausted_tasks()

    assert ready == [], "a task the watcher now owns must not be retried by the scheduler"
    assert handed and handed[0][0] == TASK
    assert dispatched == []


def test_retry_evaluation_hands_off_even_before_resume_at(monkeypatch):
    """Ownership does not wait for the reset window: the watcher can act on a
    red PR now, and there is nothing for the scheduler to do at resume_at."""
    rows = [{"id": TASK, "title": "t", "priority": "high",
             "updated_at": "2026-09-03T19:38:00+00:00",
             "max_retries": 5, "failure_count": 0}]
    monkeypatch.setattr(k, "get_connection", lambda *a, **kw: _Conn(rows))
    monkeypatch.setattr(k, "_load_resume_at",
                        lambda _tid: datetime.now(timezone.utc) + timedelta(hours=1))
    handed = []
    monkeypatch.setattr(k, "_hand_parked_task_to_pr_watcher",
                        lambda tid, *, context: handed.append(tid) or 2040)
    assert k._check_token_exhausted_tasks() == []
    assert handed == [TASK]


def test_a_parked_task_without_a_pr_still_follows_the_retry_path(monkeypatch):
    rows = [{"id": TASK, "title": "t", "priority": "high",
             "updated_at": "2026-09-03T19:38:00+00:00",
             "max_retries": 5, "failure_count": 0}]
    monkeypatch.setattr(k, "get_connection", lambda *a, **kw: _Conn(rows))
    monkeypatch.setattr(k, "_load_resume_at",
                        lambda _tid: datetime.now(timezone.utc) - timedelta(hours=1))
    monkeypatch.setattr(k, "_get_retry_count", lambda _tid: 2)
    monkeypatch.setattr(k, "_hand_parked_task_to_pr_watcher",
                        lambda tid, *, context: None)
    ready = k._check_token_exhausted_tasks()
    assert [t["id"] for t in ready] == [TASK]


def test_the_park_site_hands_off_at_park_time():
    """The moment of parking is the first chance to notice the PR; waiting for
    the next evaluation costs one cycle for no reason."""
    src = inspect.getsource(k._check_completed)
    park = src.index('_move_task(task_id, "token_exhausted"')
    window = src[park:park + 4000]
    assert "_hand_parked_task_to_pr_watcher(" in window
    assert 'context="at park"' in window


# --------------------------------------------------------------------------- #
# 4. Startup recovery makes the same hand-off
# --------------------------------------------------------------------------- #
def test_startup_recovery_hands_a_parked_task_with_an_open_pr_to_the_watcher():
    conn = _Conn([{"id": TASK, "title": "t"}, {"id": "rmf-wp-02", "title": "u"}])
    out = sr.hand_off_parked_tasks_with_open_pr(
        conn_factory=lambda: conn,
        list_open_prs=lambda _root: {BRANCH: 2040, "kanban/rmf-wp-02": 2042},
    )
    assert sorted(e["id"] for e in out["handed"]) == ["rmf-rfp-01", "rmf-wp-02"]
    assert {e["pr_number"] for e in out["handed"]} == {2040, 2042}
    assert conn.committed
    status_writes = [(s, p) for s, p in conn.writes if "SET status = 'pr_opened'" in s]
    assert len(status_writes) == 2
    for sql, params in status_writes:
        assert "AND status = 'token_exhausted'" in sql, "guarded on the status it read"
    ledger = [(s, p) for s, p in conn.writes if "kanban_status_transitions" in s]
    assert len(ledger) == 2
    for _sql, params in ledger:
        assert "token_exhausted" in params and "pr_opened" in params
        assert "startup-recovery" in params
        assert any(isinstance(p, str) and "handed to pr_watcher" in p and "#20" in p
                   for p in params)


def test_startup_recovery_leaves_a_parked_task_without_a_pr_alone():
    conn = _Conn([{"id": TASK, "title": "t"}])
    out = sr.hand_off_parked_tasks_with_open_pr(
        conn_factory=lambda: conn, list_open_prs=lambda _root: {"kanban/other": 3},
    )
    assert out["handed"] == []
    assert conn.writes == []


def test_startup_recovery_with_an_unavailable_forge_is_unmeasured_not_clean():
    conn = _Conn([{"id": TASK, "title": "t"}])
    out = sr.hand_off_parked_tasks_with_open_pr(
        conn_factory=lambda: conn, list_open_prs=lambda _root: None,
    )
    assert out["handed"] == []
    assert out["forge_unavailable"] is True
    assert conn.writes == []


def test_startup_recovery_dry_run_writes_nothing():
    conn = _Conn([{"id": TASK, "title": "t"}])
    out = sr.hand_off_parked_tasks_with_open_pr(
        conn_factory=lambda: conn, list_open_prs=lambda _root: {BRANCH: 2040},
        dry_run=True,
    )
    assert [e["id"] for e in out["handed"]] == [TASK]
    assert conn.writes == []
    assert not conn.committed


def test_recover_interrupted_tasks_runs_the_hand_off(monkeypatch):
    """Both restart entrypoints call recover_interrupted_tasks, so wiring the
    hand-off there is what makes it run on EVERY restart."""
    monkeypatch.setattr(sr, "foreign_scheduler_pid", lambda: 0)
    calls = []

    def _fake(**kw):
        calls.append(kw)
        return {"swept": 0, "handed": [], "forge_unavailable": False, "dry_run": False}

    monkeypatch.setattr(sr, "hand_off_parked_tasks_with_open_pr", _fake)
    conn = _Conn([])
    out = sr.recover_interrupted_tasks(conn_factory=lambda: conn, notify=False,
                                       scan_processes=False)
    assert calls, "recover_interrupted_tasks did not run the parked-PR hand-off"
    assert out["handed_to_pr_watcher"]["swept"] == 0


# --------------------------------------------------------------------------- #
# 5. The standing claim
# --------------------------------------------------------------------------- #
def test_the_claim_is_registered_and_cites_its_incident():
    from tools.awareness import claims as C

    claim = next(c for c in C.REGISTRY if c.claim_id == "parked_task_with_open_pr_is_owned")
    assert claim.incident is not None
    assert "mfx-own-01" in claim.incident.task_ids
    assert claim.incident.observed_on == "2026-09-03"
    assert claim.incident.fixed_by
    assert claim.tier == "propose"
    # The two cards that lost six hours each are named, so the claim's own
    # description says which incident it was learned from.
    assert "rmf-rfp-01" in claim.description and "rmf-wp-02" in claim.description
    assert claim.reported is not claim.derived
    assert claim.reported.__code__ is not claim.derived.__code__


def test_the_claim_refuses_a_parked_task_the_forge_says_has_a_pr():
    from tools.awareness import claims as C

    claim = next(c for c in C.REGISTRY if c.claim_id == "parked_task_with_open_pr_is_owned")
    # reported: the board's parked-and-older-than-a-cycle ids
    # derived:  the forge's open kanban/<id> PRs
    assert claim.agree(["rmf-rfp-01", "rmf-wp-02"], ["rmf-rfp-01"]) is False
    assert claim.agree(["rmf-rfp-01"], ["other-01"]) is True
    assert claim.agree([], ["other-01"]) is True
    assert claim.agree(["rmf-rfp-01"], []) is True


def test_the_claim_derived_side_reads_the_forge_not_the_scheduler():
    """The board says 'parked'; the FORGE says 'has a PR'. If the derived side
    imported the scheduler's cached listing it would re-run the reported side's
    own blind spot."""
    from tools.awareness import claims as C

    src = inspect.getsource(C._derived_forge_open_kanban_task_ids)
    assert "tools.genesis" not in src
    assert "_forge_open_head_branches" in src
    forge = inspect.getsource(C._forge_open_head_branches)
    assert '"gh"' in forge and "tools.genesis" not in forge
    src_reported = inspect.getsource(C._reported_parked_tasks_older_than_a_cycle)
    assert "token_exhausted" in src_reported
    assert "gh" not in src_reported


# --------------------------------------------------------------------------- #
# 6. The scheduler ENTRYPOINT reports the hand-off it just made
# --------------------------------------------------------------------------- #
def test_the_scheduler_entrypoint_reports_what_startup_recovery_handed_off():
    """`tools.genesis.kanban_scheduler` runs recover_interrupted_tasks at
    process start and logs its counts; a hand-off it made silently would be
    the same defect one log line over. Read as text: importing the entrypoint
    module starts a scheduler."""
    src = (ROOT / "tools" / "genesis" / "kanban_scheduler.py").read_text(encoding="utf-8")
    assert "handed_to_pr_watcher" in src
    assert "forge_unavailable" in src, "an unasked forge must not read as 'nothing parked'"
