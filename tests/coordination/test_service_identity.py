# CUI // SP-CTI
"""Two scheduler processes must block each other on a task (autonomy-sid-01).

THE DEFECT, observed live 2026-08-20. Two `kanban_scheduler` processes were
dispatching onto the same board — the supervisor's and one from a task worktree
that had its own `.env`. Each did::

    os.environ.setdefault("ICDEV_SESSION_ID", "kanban-scheduler")

so both presented the same id. `leases.acquire` refuses a hard lease held by
ANOTHER live session, and `kanban:task:<id>` IS hard (`RES_KANBAN` is in
`HARD_NAMESPACES`) — so the refusal was armed, correct, and blind. The guard
whose whole job is stopping two workers building one task could not tell them
apart.

WHY THE OLD BEHAVIOUR PASSED EVERY TEST: a test that exercises ONE process never
sees it. The first test below is the one that matters — it drives TWO distinct
sessions at one task and asserts the second is refused. It fails against the old
fixed id.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.coordination import leases  # noqa: E402
from tools.coordination.service_identity import (  # noqa: E402
    claim_service_identity,
    is_service_session,
    service_session_id,
)


def _as_process(session_id: str) -> None:
    """Present as a specific process to the lease layer."""
    os.environ["ICDEV_SESSION_ID"] = session_id
    os.environ.pop("CLAUDE_SESSION_ID", None)
    os.environ["ICDEV_AGENT"] = "kanban"
    try:
        import tools.airgap.hook_compat as hc
        hc._session_id = None
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from tools.coordination.constants import LEASE_DIR

    if LEASE_DIR.exists():
        shutil.rmtree(LEASE_DIR, ignore_errors=True)
    saved = {k: os.environ.get(k) for k in
             ("ICDEV_SESSION_ID", "CLAUDE_SESSION_ID", "ICDEV_AGENT")}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        import tools.airgap.hook_compat as hc
        hc._session_id = None
    except Exception:  # noqa: BLE001
        pass
    if LEASE_DIR.exists():
        shutil.rmtree(LEASE_DIR, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 1. THE test — two processes, one task
# --------------------------------------------------------------------------- #
def test_two_scheduler_processes_block_each_other_on_one_task():
    """The live defect, in one assertion.

    Under the old fixed id both sides are `kanban-scheduler`, the refusal sees
    one session, and BOTH acquire — which is two workers on one task.
    """
    resource = "kanban:task:autonomy-probe-01"

    _as_process(service_session_id("kanban-scheduler", pid=1111))
    first = leases.acquire(resource, intent="process A")
    assert first is not None, "the first process could not take the lease at all"

    _as_process(service_session_id("kanban-scheduler", pid=2222))
    second = leases.acquire(resource, intent="process B")

    assert second is None, (
        "a SECOND scheduler process took a lease the first already held — two "
        "workers would build this task"
    )


def test_the_same_process_may_re_acquire_its_own_lease():
    """Re-entrancy must survive: a process that already holds the task is not
    another claimant, and refusing it would deadlock the holder against itself."""
    resource = "kanban:task:autonomy-probe-02"
    _as_process(service_session_id("kanban-scheduler", pid=3333))

    assert leases.acquire(resource, intent="first") is not None
    assert leases.acquire(resource, intent="again") is not None, (
        "a process was refused its OWN lease"
    )


def test_a_fixed_id_is_what_broke_it():
    """Pins the mechanism rather than the fix: with one shared id, the second
    claimant is NOT refused. This is the behaviour that shipped."""
    resource = "kanban:task:autonomy-probe-03"

    _as_process("kanban-scheduler")           # the old, fixed id
    assert leases.acquire(resource, intent="A") is not None
    _as_process("kanban-scheduler")           # a "different process", same id
    assert leases.acquire(resource, intent="B") is not None, (
        "if this now refuses, the lease layer changed and this test's premise "
        "is stale — re-read leases.acquire before trusting the fix"
    )


# --------------------------------------------------------------------------- #
# 2. The id itself
# --------------------------------------------------------------------------- #
def test_the_id_is_distinct_per_process():
    assert service_session_id("svc", pid=1) != service_session_id("svc", pid=2)


def test_the_id_keeps_the_service_name_readable():
    """It is read by humans in list_active(), by merge_stall's attribution and
    by the coordination hook. An opaque uuid is distinct and unreadable."""
    assert service_session_id("kanban-scheduler", pid=99) == "kanban-scheduler-99"


def test_the_id_is_stable_for_one_process():
    """`os.execv` re-exec keeps the SAME pid, so a self-updating daemon keeps
    the leases it held. A uuid minted at boot would orphan them on every code
    change."""
    assert service_session_id("svc", pid=7) == service_session_id("svc", pid=7)


def test_an_explicit_id_from_an_orchestrator_always_wins(monkeypatch):
    monkeypatch.setenv("ICDEV_SESSION_ID", "set-by-the-launcher")
    assert claim_service_identity("kanban-scheduler", "kanban") == "set-by-the-launcher"


def test_claiming_sets_a_per_process_id_when_none_was_given(monkeypatch):
    monkeypatch.delenv("ICDEV_SESSION_ID", raising=False)
    monkeypatch.delenv("ICDEV_AGENT", raising=False)
    got = claim_service_identity("kanban-scheduler", "kanban")
    assert got.startswith("kanban-scheduler-")
    assert got != "kanban-scheduler"
    assert os.environ["ICDEV_AGENT"] == "kanban"


# --------------------------------------------------------------------------- #
# 3. Recognising a service across the change
# --------------------------------------------------------------------------- #
def test_a_row_written_before_this_change_is_still_recognised():
    """A deployment still running the old code writes the bare name. Treating
    that as an unknown session would make the fleet view worse, not better,
    during the rollout."""
    assert is_service_session("kanban-scheduler", "kanban-scheduler")
    assert is_service_session("kanban-scheduler-4242", "kanban-scheduler")


def test_a_different_service_is_not_matched():
    assert not is_service_session("pr-watcher-1", "kanban-scheduler")
    assert not is_service_session("", "kanban-scheduler")


def test_a_name_that_merely_starts_the_same_is_not_matched():
    """`kanban-scheduler-x` must not match `kanban-schedule`, and a longer
    service name must not swallow a shorter one."""
    assert not is_service_session("kanban-scheduler-99", "kanban-schedul")


# --------------------------------------------------------------------------- #
# 4. All three services use it — none re-invents the scheme
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path,name", [
    ("tools/genesis/kanban_scheduler.py", "kanban-scheduler"),
    ("tools/genesis/daemon.py", "genesis-daemon"),
    ("tools/ci/pr_watcher.py", "pr-watcher"),
])
def test_every_long_running_service_claims_a_per_process_id(path, name):
    """Three services set an id, and a fourth will be written one day. They must
    share one helper: a service that keeps the fixed form is invisible to the
    lease again, and nothing would report it."""
    src = (ROOT / path).read_text(encoding="utf-8", errors="replace")
    assert "claim_service_identity" in src, f"{path} does not claim a per-process id"
    assert f'setdefault("ICDEV_SESSION_ID", "{name}")' not in src, (
        f"{path} still sets the fixed id that made two processes look like one"
    )
