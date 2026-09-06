# CUI // SP-CTI
"""Retry a service's coordination registration across its first cycles (mfx-boot-01).

THE DEFECT, measured on two consecutive boots (2026-09-03 05:02, 2026-09-04
07:20). The logon task started the supervisor while PostgreSQL still answered
"the database system is starting up", and PG took ~8 minutes to accept a
connection. The dashboard and the two daemons crash at `assert_identity` and
are restarted by the supervisor's 30s loop until PG accepts -- noisy, but
self-correcting. The kanban scheduler and pr_watcher do NOT crash: each
registers with `session_registry` ONCE at start-up, inside a `try` whose
`except` gave up for the life of the process (`_coord_reg = None`), and then
ran the whole day with no `agent_sessions` row. supervisor_status read
`not recorded` for both, code_staleness could not see what they were running,
and the `scheduler_heartbeat_is_fresh` claim was blind. A human restarted the
scheduler by verified pid both mornings.

THE RULE THIS KEEPS. The "never restart" rule (pid 29880's five silent hours,
2026-09-02) is about restarting the LOOP -- a scheduler that exits on a
registration failure is one the board loses. Registration is retried; the loop
is never touched. Every attempt is LOGGED with its number, the reason and the
cycle the next one is due, so a silent process is no longer possible: it is
either registered, retrying, or has said out loud that it gave up.

BACKOFF IS IN CYCLES, NOT SECONDS. A scheduler cycle sleeps 60s between cycles
but a cycle itself can run 9-37 minutes on the live board, so a wall-clock
schedule would fire a burst of attempts after one long cycle. Attempts are due
at cycle 0 (start-up), then after gaps of 1, 2, 4, 8, 16, 32, 32... cycles until
`max_attempts` is spent -- 12 attempts span ~223 cycles, which is ~3.7h at the
scheduler's 60s interval and ~1.9h at the watcher's 30s. Both cover the
8-minute recovery measured above many times over.

A registration that RETURNS a refusal (`{"ok": False, "reason": ...}`, the
registry's own contract) counts as a failure exactly like one that raises: the
registry catches its own SQL errors and reports them in the dict, and only the
connect step raises. Treating one and not the other is how a failure goes
quiet.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

from tools.logging.icdev_logger import get_logger

DEFAULT_MAX_ATTEMPTS = 12
DEFAULT_GAP_CAP = 32  # cycles between attempts, once the doubling reaches it

MAX_ATTEMPTS_ENV = "ICDEV_REGISTRATION_MAX_ATTEMPTS"
GAP_CAP_ENV = "ICDEV_REGISTRATION_GAP_CAP"

_logger = get_logger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def read_result(result: Any) -> tuple[bool, Optional[str], Optional[str]]:
    """Normalise what a `register` callable handed back to (ok, reason, session_id).

    The registry returns a dict; a fake or a wrapper may return None (no
    complaint) or a bare bool. A dict with `ok` False is a REFUSAL and its
    reason is carried through verbatim.
    """
    if isinstance(result, dict):
        ok = bool(result.get("ok", True))
        reason = result.get("reason") if not ok else None
        return ok, (str(reason)[:200] if reason else None), result.get("session_id")
    if result is None:
        return True, None, None
    return bool(result), None if result else "register returned a falsy value", None


class RegistrationRetry:
    """Per-process state of one service's registration attempts.

    `attempt(cycle)` is called at the top of every loop cycle; it does nothing
    unless an attempt is DUE, so it is cheap to call unconditionally. Outcomes:
    `registered` | `failed` | `not_due` | `exhausted`. Once `registered` is
    True the caller heartbeats instead (and `session_registry.heartbeat`
    re-registers on its own if the row is later reaped).
    """

    def __init__(
        self,
        service: str,
        register: Callable[..., Any],
        *,
        intent: Optional[str] = None,
        max_attempts: Optional[int] = None,
        gap_cap: Optional[int] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.service = service
        self._register = register
        self.intent = intent
        self.max_attempts = max_attempts or _env_int(MAX_ATTEMPTS_ENV, DEFAULT_MAX_ATTEMPTS)
        self.gap_cap = gap_cap or _env_int(GAP_CAP_ENV, DEFAULT_GAP_CAP)
        self.log = log or _logger
        self.attempts = 0
        self.registered = False
        self.exhausted = False
        self.next_due_cycle = 0  # the first attempt is immediate
        self.session_id: Optional[str] = None
        self.last_reason: Optional[str] = None
        self.history: list = []  # (cycle, outcome, reason) per real attempt
        self._gap = 1

    # ------------------------------------------------------------------ #
    def due(self, cycle: int) -> bool:
        return not self.registered and not self.exhausted and cycle >= self.next_due_cycle

    def attempt(self, cycle: int) -> str:
        if self.registered:
            return "registered"
        if self.exhausted:
            return "exhausted"
        if cycle < self.next_due_cycle:
            return "not_due"

        self.attempts += 1
        try:
            result = self._register(intent=self.intent)
            ok, reason, sid = read_result(result)
        except Exception as exc:  # noqa: BLE001 -- the connect step raises; that IS the failure
            ok, reason, sid = False, f"{type(exc).__name__}: {exc}"[:200], None

        if ok:
            self.registered = True
            self.session_id = sid
            self.history.append((cycle, "registered", None))
            self.log.info(
                "%s: coordination registration succeeded on attempt %d/%d (cycle %d)%s",
                self.service, self.attempts, self.max_attempts, cycle,
                f" as {sid}" if sid else "",
            )
            return "registered"

        self.last_reason = reason
        if self.attempts >= self.max_attempts:
            self.exhausted = True
            self.history.append((cycle, "exhausted", reason))
            # LOUD, not `pass`. This process will not heartbeat in
            # agent_sessions: supervisor_status reads it `not recorded`,
            # code_staleness cannot see it, and the scheduler_heartbeat_is_fresh
            # claim will flag it. The loop keeps running -- that is the rule.
            self.log.warning(
                "%s: coordination registration FAILED on all %d attempts (last: %s) -- "
                "giving up; this process will not heartbeat in agent_sessions and "
                "will read as a silent service until restarted",
                self.service, self.attempts, reason,
            )
            return "exhausted"

        self.next_due_cycle = cycle + self._gap
        self.history.append((cycle, "failed", reason))
        self.log.warning(
            "%s: coordination registration attempt %d/%d FAILED (%s); retrying at cycle %d",
            self.service, self.attempts, self.max_attempts, reason, self.next_due_cycle,
        )
        self._gap = min(self._gap * 2, self.gap_cap)
        return "failed"

    def describe(self) -> dict:
        return {
            "service": self.service,
            "registered": self.registered,
            "exhausted": self.exhausted,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "next_due_cycle": None if (self.registered or self.exhausted) else self.next_due_cycle,
            "session_id": self.session_id,
            "last_reason": self.last_reason,
        }
