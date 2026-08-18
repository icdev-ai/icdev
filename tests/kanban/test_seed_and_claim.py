# CUI // SP-CTI
"""Seeding a task you intend to build should claim it in the same act.

THE RACE. A session seeds a task and begins implementing it; the runner sees an
eligible row and builds the same task in parallel. Two implementations exist and
a human closes the loser. Four times in two days — PRs #1784, #1792, #1806 and
#1807 — and #1807 also sat open on `kanban/kpr-fix-02`, which made the respawn
guard withhold that task from dispatch while the board reported `review_bound`
with capacity free. A duplicate does not merely waste a build; it blocks the
queue behind it.

THE MECHANISM WAS NEVER THE GAP. `cli.py --claim` already takes the per-task
coordination lease, and the runner already refuses a task another live session
holds — `kanban` is in HARD_NAMESPACES, so the refusal is real. What was missing
is that seeding and claiming were two separate acts with a window between them,
and nothing pointed a seeder at the second one. The reliable-looking
alternative, `--pause-runner`, got reached for instead: it halts the entire
board to protect one task and lapses silently after 4h with no renewal, which is
exactly how #1806 and #1807 were built hours after a pause was taken.

So this closes the window; it does not add a mechanism.
"""
from __future__ import annotations

import inspect

import tools.coordination.leases as leases
import tools.kanban.task_factory as tf


# ── opt-in, so no existing caller changes behaviour ────────────────────────
def test_claim_is_keyword_only_and_defaults_to_off():
    """A seeder that silently started claiming would strand tasks for every
    caller that never learned to release."""
    sig = inspect.signature(tf.create_tasks)
    assert sig.parameters["claim"].default is False
    assert sig.parameters["claim"].kind is inspect.Parameter.KEYWORD_ONLY


def test_create_tasks_calls_the_real_helper():
    """Pins the wiring. An earlier draft of this file tested a COPY of the claim
    block, which would have passed while the shipped code did nothing."""
    assert "claim_seeded_tasks(created)" in inspect.getsource(tf.create_tasks)


# ── the helper itself, exercised directly ──────────────────────────────────
def test_a_claim_is_taken_for_every_inserted_task(monkeypatch):
    taken = []
    monkeypatch.setattr(leases, "acquire",
                        lambda res, **k: taken.append(res) or object())
    out = tf.claim_seeded_tasks(["a-01", "a-02"])
    assert taken == ["kanban:task:a-01", "kanban:task:a-02"]
    assert out["claimed"] == ["a-01", "a-02"]


def test_nothing_is_claimed_when_nothing_was_inserted(monkeypatch):
    """A fully-deduped batch must not take leases on rows it did not create."""
    taken = []
    monkeypatch.setattr(leases, "acquire",
                        lambda res, **k: taken.append(res) or object())
    assert tf.claim_seeded_tasks([]) == {"claimed": [], "refused": [], "failed": []}
    assert taken == []


def test_a_refused_claim_does_not_undo_the_insert(monkeypatch, caplog):
    """The case that decides whether this is safe to turn on. The rows already
    exist and the board is correct; failing here would turn a coordination
    nicety into a seeding outage."""
    monkeypatch.setattr(leases, "acquire", lambda *a, **k: None)
    tf.logger.propagate = True   # icdev_logger detaches from root by default
    with caplog.at_level("WARNING", logger=tf.logger.name):
        out = tf.claim_seeded_tasks(["a-01"])
    assert out["refused"] == ["a-01"]
    assert any("could NOT be claimed" in r.getMessage() for r in caplog.records), (
        "a refused claim must be said out loud — silently seeding an unclaimed "
        "task hands the caller the OLD behaviour while looking like the new one")


def test_a_raising_lease_backend_does_not_break_seeding(monkeypatch, caplog):
    def _boom(*a, **k):
        raise OSError("lease dir unreadable")

    monkeypatch.setattr(leases, "acquire", _boom)
    tf.logger.propagate = True
    with caplog.at_level("WARNING", logger=tf.logger.name):
        out = tf.claim_seeded_tasks(["a-01"])
    assert out["failed"] == ["a-01"]
    assert any("claim failed" in r.getMessage() for r in caplog.records)


def test_one_refusal_does_not_stop_the_rest(monkeypatch):
    """A batch is not all-or-nothing: the second task must still be protected."""
    def _acquire(res, **k):
        return None if res.endswith("a-01") else object()

    monkeypatch.setattr(leases, "acquire", _acquire)
    out = tf.claim_seeded_tasks(["a-01", "a-02"])
    assert out["refused"] == ["a-01"]
    assert out["claimed"] == ["a-02"]


# ── one resource name across seeder, CLI and runner ────────────────────────
def test_the_seeder_and_the_cli_claim_the_same_resource():
    """Two names would mean the CLI claims one thing, the seeder another, and
    the runner honours neither reliably."""
    from tools.kanban.cli import _task_lease_resource

    taken = []
    import tools.coordination.leases as ls

    orig = ls.acquire
    try:
        ls.acquire = lambda res, **k: taken.append(res) or object()
        tf.claim_seeded_tasks(["a-01"])
    finally:
        ls.acquire = orig
    assert taken == [_task_lease_resource("a-01")]


def test_the_runner_consumes_that_same_claim():
    """The consumer side, asserted so this cannot become a declared-but-unused
    capability — the failure mode this repo ships most. The runner acquires the
    same resource before spending tokens and skips when it is refused."""
    import tools.genesis.reflexes.kanban as km

    src = inspect.getsource(km)
    # The ACQUIRE site, not the first mention — the first is a release inside
    # _move_task, and anchoring there proved nothing about the guard.
    i = src.index('_leases.acquire(')
    window = src[i:i + 700]
    assert "kanban:task:" in window, "the runner must claim the per-task resource"
    assert "skipping" in window.lower(), (
        "the runner acquires the per-task lease but does not skip on refusal")


def test_the_claim_ttl_is_bounded():
    """A claim that never expires turns a crashed session into a permanently
    stranded task — a worse failure than the duplicate build it prevents."""
    assert 0 < tf.SEED_CLAIM_TTL_SECONDS <= 24 * 60 * 60
