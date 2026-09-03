# CUI // SP-CTI
"""The scheduler heartbeats WHILE a cycle works (claim-verif-33c9f4cd11).

THE DEFECT, observed live 2026-09-03. The scheduler wrote its agent_sessions
heartbeat once per cycle, BEFORE the work. On the live board a cycle with a
dozen rmf-ui-* tasks in flight ran 9-37 minutes (dispatches at 09:58-09:59, the
next scheduler act at 10:25; 11:03 to 11:40), so the claim
`scheduler_heartbeat_is_fresh` -- ten minutes, sized for the 60s interval --
caught a normally looping scheduler mid-cycle and filed a card, and any process
that registered meanwhile reaped the scheduler's row at fifteen minutes. The
data was right; the reduction "one beat per cycle boundary" was wrong.

The pump beats every minute while a cycle works and WITHHOLDS the beat past a
cycle ceiling, where "busy" becomes the alive-but-not-looping state the claim
exists for. The decision is a pure function so it is tested without a thread.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis import kanban_scheduler as ks  # noqa: E402


class _Reg:
    def __init__(self):
        self.beats = []

    def heartbeat(self, intent=None):
        self.beats.append(intent)
        return True


# --------------------------------------------------------------------------- #
# 1. The decision
# --------------------------------------------------------------------------- #
def test_between_cycles_the_pump_is_idle():
    reg = _Reg()
    assert ks._pump_tick({"cycle": 3, "cycle_started": 100.0, "working": False}, reg, 160.0) == "idle"
    assert ks._pump_tick({"cycle": 3, "cycle_started": None, "working": True}, reg, 160.0) == "idle"
    assert reg.beats == []


def test_a_working_cycle_is_heartbeat_with_its_number_and_age():
    reg = _Reg()
    state = {"cycle": 93, "cycle_started": 1000.0, "working": True}
    assert ks._pump_tick(state, reg, 1000.0 + 1560, ceiling=3600) == "beat"
    assert reg.beats == ["kanban scheduler — cycle 93, working 1560s"]


def test_a_cycle_past_the_ceiling_is_not_vouched_for():
    """The beat is WITHHELD, not written: a heartbeat for a cycle that long is
    the pump asserting what it cannot know, and the claim is meant to fire."""
    reg = _Reg()
    state = {"cycle": 93, "cycle_started": 1000.0, "working": True}
    assert ks._pump_tick(state, reg, 1000.0 + 3601, ceiling=3600) == "withheld"
    assert reg.beats == []


def test_the_ceiling_clears_every_cycle_measured_on_the_live_board():
    """9-37 minute cycles, 2026-09-03. An hour is above all of them."""
    assert ks._CYCLE_CEILING_SECONDS >= 37 * 60
    assert ks._PUMP_PERIOD_SECONDS * 10 <= 600, (
        "ten missed pump beats must fit inside the claim's ten-minute window")


def test_a_failing_beat_is_reported_not_raised():
    class _Boom:
        def heartbeat(self, intent=None):
            raise RuntimeError("registry down")

    state = {"cycle": 1, "cycle_started": 0.0, "working": True}
    try:
        ks._pump_tick(state, _Boom(), 10.0, ceiling=100)
    except RuntimeError:
        pass  # the tick may raise; the THREAD must swallow it -- asserted below
    src = inspect.getsource(ks._start_heartbeat_pump)
    assert "except Exception" in src and "continue" in src


# --------------------------------------------------------------------------- #
# 2. The wiring -- read from main() itself, because a pump nobody starts is
#    the declared-but-unconsumed defect this repo ships most.
# --------------------------------------------------------------------------- #
def test_main_starts_the_pump_before_the_loop_and_brackets_the_work():
    src = inspect.getsource(ks.main)
    start = src.index("_start_heartbeat_pump(")
    loop = src.index("while True:")
    working_on = src.index("working=True")
    dispatch = src.index("# [DISPATCH POINT - main loop]")
    working_off = src.index('_pump_state["working"] = False')
    assert start < loop, "the pump must be running before the first cycle"
    assert working_on < dispatch < working_off, (
        "the working window must bracket the cycle's work")
    assert src.index("finally:") < working_off, (
        "the window must close on a failed cycle too, or a raised cycle pumps forever")


def test_the_pump_is_a_daemon_thread():
    src = inspect.getsource(ks._start_heartbeat_pump)
    assert "daemon=True" in src, "a pump that outlives the loop keeps a dead scheduler alive"


def test_a_long_cycle_is_logged_with_its_duration():
    """The interval is the SLEEP; a reader of the log must be able to see the
    cycle length the claim's window is read against."""
    src = inspect.getsource(ks.main)
    assert "Cycle %d took %.0fs" in src
