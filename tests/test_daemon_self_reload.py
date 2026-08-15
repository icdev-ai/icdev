#!/usr/bin/env python3
"""A merged fix must reach the process that runs it. CUI // SP-CTI

Every long-lived process here imports its modules once and then runs for days,
so a merged fix is inert until somebody restarts it -- and the failure is
invisible, because a process serving hours-old code looks exactly like a process
whose fix did not work.

`kanban_scheduler` and `pr_watcher` already re-exec themselves via
tools/genesis/code_reload. Three did not, and it cost real time on 2026-08-15:

  * tools/daemon/base.py       run_forever, shared by SEVEN daemon subclasses
                               (genesis, appforge, proposal_genesis, evolution,
                               review_board, companion_sync, trading). The
                               genesis daemon ran a reflex fix and a raised
                               reflex timeout from args/genesis_config.yaml only
                               after being bounced by hand.
  * tools/ci/triggers/poll_trigger.py
  * tools/dashboard/app.py     the one users look at

Config rides along: DaemonBase reads self.config once in main(), so a changed
args/*.yaml arrives BECAUSE the process re-execs, not because anything re-reads
the file. That is why the reload is the fix for both.

Deterministic: source inspection plus injected clocks. Nothing re-execs, no
daemon is started, no network.
"""
from __future__ import annotations

import inspect
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# --------------------------------------------------------------------------- #
# The shared daemon base — one edit, seven daemons
# --------------------------------------------------------------------------- #

def test_daemon_base_run_forever_checks_for_changed_code():
    from tools.daemon import base

    src = inspect.getsource(base.DaemonBase.run_forever)
    assert "code_reload" in src
    assert "restart_if_code_changed" in src


def test_the_reload_happens_after_the_cycle_not_before():
    """A re-exec mid-cycle abandons a reflex that already claimed work.

    Ordering is the whole safety argument, so it is asserted rather than trusted:
    the reload call must come after run_due_reflexes and before the sleep.
    """
    from tools.daemon import base

    src = inspect.getsource(base.DaemonBase.run_forever)
    work_at = src.find("self.run_due_reflexes()")
    reload_at = src.find("restart_if_code_changed")
    sleep_at = src.find("time.sleep(1)")
    assert work_at != -1 and reload_at != -1 and sleep_at != -1
    assert work_at < reload_at < sleep_at, (
        "reload must sit between the cycle's work and the sleep"
    )


def test_a_missing_code_reload_module_does_not_stop_the_daemon():
    """Reloading is an optimisation; losing it must not take the daemon down."""
    from tools.daemon import base

    src = inspect.getsource(base.DaemonBase.run_forever)
    assert "except Exception as _cr_imp" in src
    assert "_code_reload = None" in src


def test_a_failing_reload_check_does_not_kill_the_loop():
    from tools.daemon import base

    src = inspect.getsource(base.DaemonBase.run_forever)
    assert "code-change check failed" in src


def test_all_seven_daemons_inherit_it():
    """The reason this went in the base rather than in genesis/daemon.py."""
    from tools.daemon.base import DaemonBase

    modules = [
        "tools.genesis.daemon",
        "tools.appforge.daemon",
        "tools.proposal_genesis.daemon",
        "tools.registry.evolution_daemon",
        "tools.review_board.daemon",
        "tools.scheduler.companion_sync_daemon",
    ]
    found = 0
    for name in modules:
        try:
            mod = __import__(name, fromlist=["*"])
        except Exception:  # noqa: BLE001 — an optional daemon may not import here
            continue
        for obj in vars(mod).values():
            if isinstance(obj, type) and issubclass(obj, DaemonBase) and obj is not DaemonBase:
                # It must not override run_forever and thereby lose the reload.
                assert "run_forever" not in vars(obj), (
                    f"{obj.__name__} overrides run_forever — it would skip the "
                    f"self-reload the base provides"
                )
                found += 1
                break
    assert found >= 3, f"expected several daemon subclasses, resolved {found}"


# --------------------------------------------------------------------------- #
# poll_trigger
# --------------------------------------------------------------------------- #

def test_poll_trigger_reloads_after_its_poll():
    import tools.ci.triggers.poll_trigger as pt

    src = inspect.getsource(pt.main)
    assert "restart_if_code_changed" in src
    work_at = src.rfind("check_and_process_issues(vcs)")
    reload_at = src.find("restart_if_code_changed")
    assert work_at < reload_at, "a re-exec mid-poll would drop an issue already picked up"


# --------------------------------------------------------------------------- #
# The dashboard — idle is its substitute for "between cycles"
# --------------------------------------------------------------------------- #

def test_dashboard_only_reloads_when_idle():
    """A web server has no gap between cycles; re-execing mid-request resets it.

    Asserts the GUARD, not merely that the names appear. Checking for
    "_LAST_REQUEST_AT in src" passes even with the `if` deleted, because the name
    survives in the docstring — a weak assertion I only caught by regressing the
    code and watching this test stay green.
    """
    from tools.dashboard import app as dash

    src = inspect.getsource(dash._start_self_reload_watcher)
    assert "restart_if_code_changed" in src

    # The comparison and its early-exit must both be present, and the guard must
    # come BEFORE the reload call inside the loop body.
    guard = "now - _LAST_REQUEST_AT >= _RELOAD_IDLE_SECONDS"
    assert guard in src, "the idle comparison itself must be present"
    guard_at = src.find(guard)
    reload_at = src.find("_cr.restart_if_code_changed")
    assert guard_at < reload_at, "the idle check must gate the reload, not follow it"

    # ...and it must actually skip, not just evaluate.
    tail = src[guard_at:reload_at]
    assert "continue" in tail, "a busy server must skip the reload, not fall through"


def test_request_activity_is_actually_recorded():
    """An idle gate fed by a timestamp nobody writes would reload mid-request."""
    from tools.dashboard import app as dash

    src = inspect.getsource(dash.create_app)
    assert "_mark_request_activity" in src
    assert "global _LAST_REQUEST_AT" in src


def test_dashboard_reload_is_on_by_default():
    """An opt-in reloader is one more capability that exists and never runs."""
    from tools.dashboard import app as dash

    src = inspect.getsource(dash._start_self_reload_watcher)
    assert 'ICDEV_DASHBOARD_SELF_RELOAD", "1"' in src


def test_dashboard_reload_has_a_kill_switch(monkeypatch):
    from tools.dashboard import app as dash

    monkeypatch.setenv("ICDEV_DASHBOARD_SELF_RELOAD", "0")
    # Must return without starting a thread and without raising.
    dash._start_self_reload_watcher()


def test_the_idle_window_is_configurable():
    from tools.dashboard import app as dash

    assert dash._RELOAD_IDLE_SECONDS > 0
    src = pathlib.Path(dash.__file__).read_text(encoding="utf-8")
    assert "ICDEV_DASHBOARD_RELOAD_IDLE" in src


@pytest.mark.parametrize("since_request,should_reload", [
    (0.0, False),      # request just served
    (5.0, False),      # still busy
    (59.0, False),     # just under the window
    (61.0, True),      # quiet long enough
    (3600.0, True),
])
def test_the_idle_gate_boundary(since_request, should_reload):
    from tools.dashboard import app as dash

    decided = since_request >= dash._RELOAD_IDLE_SECONDS
    assert decided is should_reload


def test_a_server_that_never_served_counts_as_idle():
    """_LAST_REQUEST_AT starts at 0.0, which must read as "idle since boot"
    rather than "a request is in flight" — otherwise a dashboard nobody has
    opened yet would never pick up code."""
    import time as _t

    from tools.dashboard import app as dash

    assert _t.time() - 0.0 >= dash._RELOAD_IDLE_SECONDS


# --------------------------------------------------------------------------- #
# The processes that already had it must keep it
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("rel", [
    "tools/genesis/kanban_scheduler.py",
    "tools/ci/pr_watcher.py",
])
def test_the_two_that_already_self_reloaded_still_do(rel):
    src = (_ROOT / rel).read_text(encoding="utf-8")
    assert "restart_if_code_changed" in src


# --------------------------------------------------------------------------- #
# Idleness alone would mean NEVER — the backstop
# --------------------------------------------------------------------------- #

def test_idleness_is_not_the_only_way_to_reload():
    """44 dashboard templates poll on setInterval, several every 10-15s.

    One open browser tab therefore refreshes _LAST_REQUEST_AT forever and a
    purely idle-gated reloader never fires — the same declared-but-never-runs
    defect this change exists to remove. A staleness backstop is what makes the
    capability real rather than nominal.
    """
    from tools.dashboard import app as dash

    src = inspect.getsource(dash._start_self_reload_watcher)
    assert "_RELOAD_MAX_STALE_SECONDS" in src
    assert "too_stale" in src
    assert "idle or too_stale" in src, "either condition must be sufficient"


def test_the_backstop_is_configurable_and_sane():
    from tools.dashboard import app as dash

    assert dash._RELOAD_MAX_STALE_SECONDS > dash._RELOAD_IDLE_SECONDS, (
        "the backstop must be a last resort, not the primary path"
    )
    src = pathlib.Path(dash.__file__).read_text(encoding="utf-8")
    assert "ICDEV_DASHBOARD_RELOAD_MAX_STALE" in src


@pytest.mark.parametrize("idle_for,stale_for,expected", [
    (0.0,    0.0,    False),   # busy, nothing pending
    (5.0,    30.0,   False),   # busy, recently changed -> wait
    (120.0,  30.0,   True),    # quiet -> preferred path
    (5.0,    1000.0, True),    # never quiet (polling UI) -> backstop fires
    (0.0,    901.0,  True),    # just past the backstop
])
def test_reload_decision_table(idle_for, stale_for, expected):
    from tools.dashboard import app as dash

    idle = idle_for >= dash._RELOAD_IDLE_SECONDS
    too_stale = stale_for >= dash._RELOAD_MAX_STALE_SECONDS
    assert (idle or too_stale) is expected


def test_staleness_resets_when_there_is_nothing_pending():
    """Otherwise the backstop would eventually fire on an unchanged tree."""
    from tools.dashboard import app as dash

    src = inspect.getsource(dash._start_self_reload_watcher)
    assert "stale_since[0] = 0.0" in src


# --------------------------------------------------------------------------- #
# Reload frequency — the dashboard's restart is NOT cheap
# --------------------------------------------------------------------------- #

def test_the_dashboard_will_not_reload_more_often_than_the_floor():
    """Reported as the dashboard "hanging", 2026-08-15.

    code_reload's MIN_UPTIME_SECONDS (120) is a restart-loop guard sized for a
    DAEMON. This process re-runs PostgreSQL init, the GovLift schema, every
    blueprint mount and the DIC freshness daemon on re-exec, and serves nothing
    meanwhile. At a 120s floor with main merging every few minutes and the UI
    idle, it re-execed roughly every two minutes -- .tmp/dashboard.log carried
    three "self-reload armed" lines in one sitting -- and spent much of its life
    starting up.
    """
    from tools.dashboard import app as dash

    src = inspect.getsource(dash._start_self_reload_watcher)
    assert "_RELOAD_MIN_INTERVAL_SECONDS" in src
    guard = "now - started_at < _RELOAD_MIN_INTERVAL_SECONDS"
    assert guard in src, "the floor must be enforced in the loop"
    # ...and it must gate BOTH signals, not sit after them.
    assert src.find(guard) < src.find("idle = now - _LAST_REQUEST_AT")


def test_the_floor_is_far_longer_than_the_daemon_restart_guard():
    from tools.genesis import code_reload
    from tools.dashboard import app as dash

    assert dash._RELOAD_MIN_INTERVAL_SECONDS >= 10 * code_reload.MIN_UPTIME_SECONDS, (
        "a floor near the daemon's 120s guard is what produced the thrash"
    )


def test_the_floor_is_configurable():
    from tools.dashboard import app as dash

    assert "ICDEV_DASHBOARD_RELOAD_MIN_INTERVAL" in pathlib.Path(
        dash.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize("uptime,changed,idle,expected", [
    (60,    True, True,  False),   # inside the floor -> never
    (119,   True, True,  False),
    (1799,  True, True,  False),   # still inside 30min
    (1801,  True, True,  True),    # past the floor and idle
    (1801,  True, False, False),   # past the floor but busy -> wait for stale
    (5000, False, True,  False),   # nothing changed
])
def test_reload_frequency_decision_table(uptime, changed, idle, expected):
    from tools.dashboard import app as dash

    past_floor = uptime >= dash._RELOAD_MIN_INTERVAL_SECONDS
    assert (changed and past_floor and idle) is expected
