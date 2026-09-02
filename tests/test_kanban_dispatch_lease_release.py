# CUI // SP-CTI
"""An abandoned dispatch releases the task lease; a real dispatch keeps it (kpr-stale-03).

THE DEFECT. The dispatch loop acquires ``kanban:task:<id>`` before spending a
token, and its own comment said the lease "is released in _move_task on
terminal/re-queue transitions". _move_task only runs if the task was ACTUALLY
DISPATCHED -- so every path that abandoned dispatch after the acquire leaked the
lease for its full 3600s TTL. There were four such paths (the landed-check
refusal, the pre-dispatch decompose, the `dispatch failed` else-branch, and the
bare `except Exception`) and ZERO ``_leases.release`` calls anywhere in the loop.

The next cycle then asked ``_drop_respawn_guarded`` whether the task was
"claimed by a live session" -- and it was, by the scheduler itself. So the
scheduler refused every task it had leaked a lease on. Measured on the live
board 2026-09-02: 20 tasks across three unrelated projects, nothing dispatched
board-wide for 8+ hours, every gate green and the board reading `scheduled`.

WHY THE RELEASE MUST BE CONDITIONAL, and why this file tests both directions: a
DISPATCHED task must KEEP its lease while the worker runs. Releasing
unconditionally in a `finally` would fix the starvation and reintroduce the
double-build race that rem-hyg-15 and kpr-dup-07 exist to prevent -- the runner
and a human building the same task into divergent branches. A test that only
asserted "the lease is gone" would pass against that regression.
"""

from __future__ import annotations

import ast
import io

from icdev.core.paths import repo_root

_KANBAN = repo_root(__file__) / "tools" / "genesis" / "reflexes" / "kanban.py"


def _dispatch_loop() -> ast.For:
    """The `for task in due_tasks:` loop, located structurally rather than by line."""
    tree = ast.parse(io.open(_KANBAN, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and getattr(node.iter, "id", "") == "due_tasks":
            return node
    raise AssertionError("the `for task in due_tasks:` dispatch loop no longer exists")


class TestLeaseIsReleasedOnEveryAbandonedPath:
    def test_loop_body_is_wrapped_in_try_finally(self):
        """One try/finally, so a new `continue` cannot reintroduce the leak.

        The repair is deliberately structural rather than an explicit release
        before each `continue`: releasing per-exit is exactly what this code
        already failed to do at four separate sites.
        """
        loop = _dispatch_loop()
        tries = [n for n in loop.body if isinstance(n, ast.Try)]
        assert len(tries) == 1, (
            "the dispatch loop body is no longer a single try/finally -- a path that "
            "abandons dispatch can leak its lease again"
        )
        assert tries[0].finalbody, "the try has no finally: nothing releases the lease"

    def test_every_continue_is_inside_the_try(self):
        """A `continue` outside the try would skip the release."""
        loop = _dispatch_loop()
        try_node = next(n for n in loop.body if isinstance(n, ast.Try))
        inside = {n.lineno for n in ast.walk(try_node) if isinstance(n, ast.Continue)}
        all_continues = {
            n.lineno for n in ast.walk(loop) if isinstance(n, ast.Continue)
        }
        assert all_continues, "expected the loop to still have early-exit paths"
        assert all_continues == inside, (
            f"continue(s) outside the try/finally would leak the lease: "
            f"{sorted(all_continues - inside)}"
        )

    def test_finally_releases_only_when_dispatch_did_not_start(self):
        """The release is guarded by `not _dispatch_started`.

        An UNCONDITIONAL release here would hand the task straight back while a
        worker is building it.
        """
        loop = _dispatch_loop()
        try_node = next(n for n in loop.body if isinstance(n, ast.Try))
        src = ast.unparse(ast.Module(body=try_node.finalbody, type_ignores=[]))
        assert "release" in src, "the finally no longer releases the lease"
        assert "_dispatch_started" in src, (
            "the finally releases unconditionally -- a dispatched task would lose its "
            "lease while its worker is still running, reopening the double-build race"
        )
        assert "not _dispatch_started" in src.replace("  ", " "), (
            "the release guard is not `not _dispatch_started`"
        )

    def test_dispatch_started_is_set_on_every_success_branch(self):
        """Each branch that actually dispatched must claim the lease.

        Miss one and that task's lease is released out from under a live worker.
        """
        loop = _dispatch_loop()
        src = ast.unparse(loop)
        assert src.count("_dispatch_started = True") == 3, (
            "expected exactly 3 success branches (Ollama sync, GitHub Actions, agent "
            "subprocess) to set _dispatch_started; found "
            f"{src.count('_dispatch_started = True')}"
        )

    def test_flags_are_reset_per_task(self):
        """Initialised at the top of the body, not before the loop.

        Hoisting either flag out of the loop would let one dispatched task
        suppress the release for every task after it in the same cycle.
        """
        loop = _dispatch_loop()
        leading = [n for n in loop.body if isinstance(n, ast.Assign)][:2]
        targets = {t.id for n in leading for t in n.targets if isinstance(t, ast.Name)}
        assert targets == {"_task_lease", "_dispatch_started"}, (
            f"per-task lease flags are not reset at the top of the loop body: {targets}"
        )


class TestReleaseIsSurvivable:
    def test_release_failure_cannot_wedge_the_loop(self):
        """A release that raises must not abort the cycle.

        The whole point of this repair is to stop one task's failure starving
        the board; a release that propagated would do exactly that.
        """
        loop = _dispatch_loop()
        try_node = next(n for n in loop.body if isinstance(n, ast.Try))
        inner_tries = [n for n in ast.walk(ast.Module(body=try_node.finalbody, type_ignores=[]))
                       if isinstance(n, ast.Try)]
        assert inner_tries, "the release is not itself guarded -- it can wedge the cycle"
        assert any(h.type is not None or h.body for h in inner_tries[0].handlers), (
            "the release guard has no exception handler"
        )
