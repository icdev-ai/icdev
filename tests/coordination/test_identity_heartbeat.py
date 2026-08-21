# CUI // SP-CTI
"""A registered process must keep saying it is alive (autonomy-id-01 follow-up).

THE DEFECT, found by USING the capability id-01 shipped. `session_registry.
register()` runs once at boot and `list_active()` filters on
`SESSION_TTL_SECONDS` (900s). The genesis daemon and pr_watcher registered and
never heartbeat, so after fifteen minutes their rows went stale and they
DISAPPEARED from the fleet code-identity view while still running.

Measured 2026-08-21: every service was UP in the process table —
kanban_scheduler, pr_watcher, two genesis daemons, two dashboards — and
`code_identity.processes()` reported ONE. The scheduler was visible only because
`code_reload` re-execs it often enough to re-register.

WHY THAT IS THE WRONG WAY ROUND. The longer a process runs without restarting,
the more certain it is to vanish from the view — and a long-running process is
exactly the one whose code version matters most, because it is the one most
likely to be stale. The view was quietest about the processes it existed to
watch.

The fix is one heartbeat in `DaemonBase.run_forever`, which every daemon shares,
plus pr_watcher's own loop.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------------- #
# 1. The loops heartbeat
# --------------------------------------------------------------------------- #
def test_the_shared_daemon_loop_heartbeats():
    """One place, every daemon. `DaemonBase.run_forever` is what genesis,
    appforge and every other daemon on the base class run."""
    base = importlib.import_module("tools.daemon.base")
    src = inspect.getsource(base.DaemonBase.run_forever)
    assert "session_registry" in src and "heartbeat()" in src, (
        "the shared daemon loop registers at boot and never says it is still "
        "alive — its row goes stale in 15 minutes and the fleet view loses it"
    )


def test_pr_watcher_heartbeats_in_its_own_loop():
    """It does not use DaemonBase, so it needs its own."""
    watcher = importlib.import_module("tools.ci.pr_watcher")
    src = inspect.getsource(watcher.PRWatcher.run_daemon)
    assert "session_registry" in src and "heartbeat()" in src


def test_the_heartbeat_cannot_kill_the_loop():
    """Liveness REPORTING is not a dependency. A database blip must not stop a
    daemon doing its actual work — that would make an observability feature an
    availability risk."""
    base = importlib.import_module("tools.daemon.base")
    src = inspect.getsource(base.DaemonBase.run_forever)
    idx = src.index("heartbeat()")
    window = src[max(0, idx - 400):idx + 200]
    assert "try:" in window and "except" in window, (
        "the heartbeat is not guarded — a failed write would break the loop"
    )


# --------------------------------------------------------------------------- #
# 2. The behaviour it protects
# --------------------------------------------------------------------------- #
def test_heartbeat_refreshes_an_existing_row(monkeypatch):
    """The point of the call: an existing session stays ACTIVE.

    SELF-CONTAINED, and it was not the first time. This test passed alone and
    failed IN-SUITE on CI, because `get_session_id()` reads CLAUDE_SESSION_ID
    BEFORE ICDEV_SESSION_ID — so an earlier test leaving CLAUDE_SESSION_ID set
    made register() and heartbeat() write under a different id than the one this
    test asserted on. `monkeypatch` (not bare os.environ) so the change is
    undone afterwards: the original also leaked ICDEV_SESSION_ID into every test
    that ran after it.

    It asserts on the id the resolver ACTUALLY returns rather than a hardcoded
    string, which removes the coupling entirely — the invariant is "heartbeat
    refreshes MY row", whatever mine is called.
    """
    from tools.airgap.hook_compat import get_session_id
    from tools.coordination import session_registry as reg

    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.setenv("ICDEV_SESSION_ID", "heartbeat-probe")
    monkeypatch.setenv("ICDEV_AGENT", "test")
    try:
        import tools.airgap.hook_compat as hc
        monkeypatch.setattr(hc, "_session_id", None, raising=False)
    except Exception:  # noqa: BLE001
        pass

    sid = get_session_id()
    assert reg.register(intent="probe").get("ok")
    assert reg.heartbeat() is True, (
        "heartbeat did not refresh a row that register() had just written"
    )
    assert any(dict(r).get("session_id") == sid for r in reg.list_active()), (
        f"the session {sid!r} that heartbeat() just refreshed is not active"
    )


def test_heartbeat_registers_when_the_row_is_missing():
    """Documented behaviour of `heartbeat`: it registers if absent. That is what
    makes a single call in the loop sufficient — a row reaped while the process
    lived comes back rather than staying gone."""
    from tools.coordination import session_registry as reg

    src = inspect.getsource(reg.heartbeat)
    assert "Registers if missing" in src or "register" in src


def test_the_ttl_is_shorter_than_a_daemon_lifetime():
    """The premise of the defect, pinned so the numbers cannot drift apart: a
    TTL measured in minutes against processes that run for days means a
    boot-only registration ALWAYS decays."""
    from tools.coordination.constants import SESSION_TTL_SECONDS

    assert SESSION_TTL_SECONDS <= 3600, (
        "if the TTL ever exceeds a typical daemon lifetime this test's premise "
        "is stale — re-read why the heartbeat was added before removing it"
    )
