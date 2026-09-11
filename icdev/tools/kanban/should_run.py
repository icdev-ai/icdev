# CUI // SP-CTI
"""ONE pre-dispatch verdict, asked before a token is spent (xrv-run-01).

THE DEFECT. ``tools/genesis/reflexes/kanban.py::_dispatch_to_claude`` runs six
sequential early-return guards -- manual build, admission, external-repo park,
recent success, open PR, circuit breaker -- each with its own log line and no
shared record, and ``_get_due_tasks`` runs five more before it (pause, capacity,
backpressure, respawn, sibling overlap). None of the eleven is a budget. The
four USD guards the platform declares (``token_tracker.check_budget``,
``module_budget_tracker.check_module_budget``, ``cost_budget.apply_to_chain``
and the proxy budgets) ALL sit inside ``tools/llm/router.py::invoke`` -- so a
``claude_cli`` dispatch, which never passes through the router, bypasses every
cap, and the budget refuses the task's calls MID-RUN instead. kpr-dup-09 (95
done<->backlog flips in 5.5 hours) was this shape: a run that ran out of budget,
every transition legitimate.

WHAT THIS IS. ``assess(task)`` asks every predicate the dispatcher already
consults, PLUS the budget rung nothing consulted, and folds the answers through
ONE pure ``classify`` into a single recorded verdict:

    proceed       every check answered and none objects
    wait          try later -- a lease, a pause, an open PR, an exhausted budget
                  (``resets_at`` names when)
    ask           dispatch, but a human was (or should be) asked -- a soft
                  cost threshold crossed
    refuse        do not dispatch this task -- a merged PR already carries it, a
                  circuit breaker tripped, a manual-gate sentinel
    unmeasurable  a check could not run. NEVER blocks (fail-open, the
                  dispatch_admission rule): a board that stops moving because a
                  ledger was unreadable at 3am is worse than one overspend.

IT RE-IMPLEMENTS NOTHING. Every check CALLS the module that owns the rule --
``scheduler_control.should_pause``, ``build_mode`` via ``kanban._manual_build``,
``gates.is_manual_gate``, ``lease_liveness.task_lease_verdict``,
``dispatch_admission.assess``, ``kanban._had_recent_success``,
``kanban._has_open_pr``, ``kanban._circuit_breaker_tripped``,
``backpressure.status``, ``module_budget_tracker.check_module_budget``,
``cost_budget.evaluate``, ``token_tracker.check_budget`` -- and maps each
module's OWN answer onto the five verdicts above. An AST test refuses a SQL
statement naming ``kanban_tasks`` here and refuses a local spelling of the
admission rule: the survey that ships beside a gate must measure the gate that
ships, never a copy (``deps.py`` names six sites that each grew their own).

THE BUDGET RUNG is three seams folded by ``budget_check``:
  * module   ``check_module_budget(module_for_function("code_generation"))``
             -- the pool a kanban worker's calls are charged to. ``block`` is
             ``wait`` until the first of next month; ``warn`` is ``proceed``
             with the reason recorded.
  * cost     ``cost_budget.evaluate("code_generation")`` -- ``block`` is
             ``wait`` until the period rolls; a DOWNGRADE tier is ``proceed``
             with the reason recorded (the router will route on the cheaper
             chain, that is not a reason to withhold the task); a raised-and-
             approved soft ASK is ``ask``. NOTE: ``evaluate`` raises the soft
             ASK itself, once per threshold per period, deduped through
             ``agent_approval_log`` -- the SAME ask the router would raise on
             the task's first call, so calling it here moves the ask earlier
             and adds no second one.
  * token    ``token_tracker.check_budget("kanban-scheduler")`` -- the agent
             id the scheduler's own calls carry. Same mapping as module.
An unreadable ledger, a corrupt budget config (``BudgetConfigError``) or an
``unmeasurable`` cost status is ``unmeasurable``, never ``proceed`` -- the
router treats those as allow, and it is right to (a budget gate that can take
routing down is worse than the overspend); THIS surface records that the
answer is unknown, which is a different thing from recording that it was fine.

WHY THE DEFAULT IS ``report`` AND MUST STAY SO UNTIL RE-SURVEYED. Measured
2026-08-18 on this board (CLAUDE.md, cef-ui-01): ``generative_intelligence``
read 420,375 of 400,000 tokens -- a cap that is a COST PROXY and fired while
the USD cap beside it sat at $0.00 of $150.00, because every call routed to a
$0/1k provider. Arming ``enforce`` against that ledger would park EVERY task on
the board until the month rolled. ``--survey`` replays the budget rung over
every recorded scheduler dispatch with the ledgers AS THEY WERE and reports
the would-have-been verdict distribution; the number lives in
docs/audits/xrv-run-01-should-run-survey.md. ``KANBAN_SHOULD_RUN=enforce`` arms
it, ``=off`` disables it. Never raise a cap to quieten it.

NOT folded here, and named: capacity and sibling overlap are decisions over
the CANDIDATE SET (how many slots, which of several tasks share a file), not
verdicts about one task, and stay in ``_get_due_tasks``; the external-repo
park is a fact about where a task builds, not whether it should.

Usage:
    python -m tools.kanban.should_run --task xrv-run-01 --json
    python -m tools.kanban.should_run --survey [--window-days 30] [--json]
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from icdev.core.paths import repo_root  # noqa: E402

_BASE = str(repo_root(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

# The admission rule is IMPORTED, never respelled -- pinned by an AST test.
from tools.kanban import dispatch_admission  # noqa: E402

# ── Verdicts ────────────────────────────────────────────────────────────────
PROCEED = "proceed"
WAIT = "wait"
ASK = "ask"
REFUSE = "refuse"
UNMEASURABLE = "unmeasurable"
VERDICTS = (PROCEED, WAIT, ASK, REFUSE, UNMEASURABLE)
#: The two verdicts that stop a dispatch when the mode is `enforce`.
BLOCKING = frozenset({WAIT, REFUSE})

#: report (default) | enforce | off
MODE_ENV = "KANBAN_SHOULD_RUN"
DEFAULT_MODE = "report"
_MODES = ("report", "enforce", "off")

#: The checks `assess` runs, in the order the dispatcher used to ask them.
CHECKS = (
    "pause", "manual_build", "manual_gate", "lease", "admission",
    "recent_success", "open_pr", "circuit_breaker", "backpressure", "budget",
)

#: The LLM function a kanban worker's calls are charged as, and the agent id
#: the scheduler's own calls carry (router.invoke reads `request.agent_id`).
BUDGET_FUNCTION = "code_generation"
SCHEDULER_AGENT_ID = "kanban-scheduler"

#: Reason vocabulary. Where a state already has a name in
#: tools/kanban/idle_advisor.py it is reused, never respelled.
REASON_PAUSED = "paused"            # idle_advisor.PAUSED
REASON_REVIEW_BOUND = "review_bound"  # idle_advisor.REVIEW_BOUND
REASON_STUCK = "stuck"              # idle_advisor.STUCK
REASON_DECISION_BOUND = "decision_bound"  # idle_advisor.DECISION_BOUND


@dataclass
class ShouldRun:
    task_id: str
    verdict: str
    reasons: List[str] = field(default_factory=list)
    checks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: When the earliest `wait` clears, if a check could say (ISO 8601).
    resets_at: Optional[str] = None

    @property
    def blocks(self) -> bool:
        """Whether this verdict stops a dispatch, given the current mode."""
        return self.verdict in BLOCKING and mode() == "enforce"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id, "verdict": self.verdict,
            "reasons": list(self.reasons), "checks": dict(self.checks),
            "resets_at": self.resets_at, "mode": mode(), "blocks": self.blocks,
        }


def mode() -> str:
    raw = (os.environ.get(MODE_ENV) or DEFAULT_MODE).strip().lower()
    return raw if raw in _MODES else DEFAULT_MODE


def _check(verdict: str, reason: str, **extra: Any) -> Dict[str, Any]:
    out = {"verdict": verdict, "reason": reason}
    out.update(extra)
    return out


# ────────────────────────────────────────────────────────────────────────────
# The fold. Everything else feeds it.
# ────────────────────────────────────────────────────────────────────────────
def classify(task_id: str, checks: Dict[str, Dict[str, Any]]) -> ShouldRun:
    """Fold named check results into ONE verdict, as a pure function.

    Precedence: any refuse -> refuse; else any wait -> wait; else any ask ->
    ask; else proceed. A check whose verdict is `unmeasurable` (or unknown) is
    RECORDED as such and NEVER moves the fold towards blocking -- and when
    every check is unmeasurable the whole verdict is, because "nothing
    objected" over a set of questions none of which was answered is the
    fabricated clean bill this repo keeps refusing.
    """
    if not task_id:
        return ShouldRun("", UNMEASURABLE, ["no task id"], dict(checks or {}))
    checks = dict(checks or {})
    normalised: Dict[str, Dict[str, Any]] = {}
    for name, result in checks.items():
        result = dict(result or {})
        if result.get("verdict") not in VERDICTS:
            result["verdict"] = UNMEASURABLE
            result.setdefault("reason", "check returned no verdict")
        normalised[name] = result

    def _with(verdict: str) -> List[str]:
        return [n for n, r in normalised.items() if r["verdict"] == verdict]

    def _reasons(names: Sequence[str]) -> List[str]:
        return [f"{n}: {normalised[n].get('reason', '')}".rstrip(": ") for n in names]

    if _with(REFUSE):
        return ShouldRun(task_id, REFUSE, _reasons(_with(REFUSE)), normalised)
    waits = _with(WAIT)
    if waits:
        resets = [normalised[n].get("resets_at") for n in waits
                  if normalised[n].get("resets_at")]
        return ShouldRun(task_id, WAIT, _reasons(waits), normalised,
                         resets_at=max(resets) if resets else None)
    if _with(ASK):
        return ShouldRun(task_id, ASK, _reasons(_with(ASK)), normalised)
    unmeasured = _with(UNMEASURABLE)
    if normalised and len(unmeasured) == len(normalised):
        return ShouldRun(task_id, UNMEASURABLE,
                         ["every check was unmeasurable"] + _reasons(unmeasured),
                         normalised)
    # Proceed -- carrying the reasons a `warn`-shaped check recorded, and naming
    # the checks that could not answer, so a proceed never reads as "all clear"
    # over a rung nobody could see.
    noted = [f"{n}: {r['reason']}" for n, r in normalised.items()
             if r["verdict"] == PROCEED and r.get("noted")]
    if unmeasured:
        noted.append("unmeasurable: " + ", ".join(unmeasured))
    return ShouldRun(task_id, PROCEED, noted, normalised)


# ────────────────────────────────────────────────────────────────────────────
# The budget rung -- three seams, one fold
# ────────────────────────────────────────────────────────────────────────────
def _month_after(month: str) -> str:
    """ISO instant at which the monthly ledger for ``YYYY-MM`` rolls over."""
    year, mon = int(month[:4]), int(month[5:7])
    if mon == 12:
        year, mon = year + 1, 1
    else:
        mon += 1
    return datetime(year, mon, 1, tzinfo=timezone.utc).isoformat()


def _period_end(period_start: str, period: str) -> Optional[str]:
    """When a cost_budget period that started at ``period_start`` rolls over."""
    try:
        start = datetime.fromisoformat(period_start[:10]).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    if str(period).lower() == "daily":
        return (start + timedelta(days=1)).isoformat()
    return _month_after(start.strftime("%Y-%m"))


def _fold_cap(status: Optional[Dict[str, Any]], *, resets_at: Optional[str],
              label: str) -> Dict[str, Any]:
    """Map a token_tracker / module_budget_tracker status dict onto a check."""
    if status is None:
        return _check(UNMEASURABLE, f"{label} ledger unreadable")
    action = str(status.get("action") or "")
    message = str(status.get("message") or "")
    if action == "block":
        return _check(WAIT, f"{label} exhausted: {message}", resets_at=resets_at)
    if action == "warn":
        return _check(PROCEED, f"{label} approaching cap: {message}", noted=True)
    if action == "allow":
        return _check(PROCEED, f"{label}: {message}")
    return _check(UNMEASURABLE, f"{label} returned no action: {message}")


def _fold_cost(verdict: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Map a ``cost_budget.BudgetVerdict.to_dict()`` onto a check."""
    if verdict is None:
        return _check(UNMEASURABLE, "cost_budget could not be evaluated")
    status = str(verdict.get("status") or "")
    action = str(verdict.get("action") or "")
    reason = str(verdict.get("reason") or "")
    if status == "unmeasurable" or verdict.get("telemetry_available") is False:
        return _check(UNMEASURABLE, f"cost_budget: {reason}")
    if action == "block":
        return _check(WAIT, f"cost_budget blocked: {reason}",
                      resets_at=_period_end(str(verdict.get("period_start") or ""),
                                            str(verdict.get("period") or "monthly")))
    if action == "downgrade" or verdict.get("downgraded"):
        return _check(PROCEED, f"cost_budget downgrade tier: {reason}", noted=True)
    if action == "ask":
        return _check(ASK, f"cost_budget soft threshold: {reason}")
    if status == "soft":
        return _check(PROCEED, f"cost_budget soft threshold noted: {reason}", noted=True)
    return _check(PROCEED, f"cost_budget: {reason}" if reason else "cost_budget: ok")


def budget_check(module_status: Optional[Dict[str, Any]],
                 cost_verdict: Optional[Dict[str, Any]],
                 token_status: Optional[Dict[str, Any]],
                 *, month: Optional[str] = None) -> Dict[str, Any]:
    """Fold the three budget seams' OWN answers into the ``budget`` check.

    Pure: takes the dicts the seams return (or ``None`` for a seam that could
    not answer) and never touches a ledger. The live path and the survey both
    feed it -- that is what makes the surveyed rate a rate for the rung that
    ships.
    """
    month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    roll = _month_after(month)
    arms = {
        "module": _fold_cap(module_status, resets_at=roll, label="module budget"),
        "cost": _fold_cost(cost_verdict),
        "token": _fold_cap(token_status, resets_at=roll, label="agent token budget"),
    }
    folded = classify("budget", arms)
    reason = "; ".join(folded.reasons) if folded.reasons else "within every budget"
    if folded.verdict == PROCEED and not any(a.get("noted") for a in arms.values()):
        reason = "within every budget"
    out = _check(folded.verdict, reason, arms=arms,
                 noted=any(a.get("noted") for a in arms.values()))
    if folded.resets_at:
        out["resets_at"] = folded.resets_at
    return out


def _live_budget_check() -> Dict[str, Any]:
    """The budget rung against the LIVE ledgers, each seam called on its own."""
    module_status: Optional[Dict[str, Any]]
    try:
        from tools.budget import module_budget_tracker as mbt
        module_status = mbt.check_module_budget(
            mbt.module_for_function(BUDGET_FUNCTION), function=BUDGET_FUNCTION)
    except Exception as exc:  # noqa: BLE001 -- unreadable is unmeasurable, never proceed
        module_status = None
        module_error = f"{type(exc).__name__}: {exc}"
    else:
        module_error = None

    cost_verdict: Optional[Dict[str, Any]]
    try:
        from tools.llm import cost_budget
        cost_verdict = cost_budget.evaluate(BUDGET_FUNCTION).to_dict()
    except Exception as exc:  # noqa: BLE001
        cost_verdict = None
        cost_error = f"{type(exc).__name__}: {exc}"
    else:
        cost_error = None

    token_status: Optional[Dict[str, Any]]
    try:
        from tools.agent import token_tracker
        token_status = token_tracker.check_budget(SCHEDULER_AGENT_ID)
    except Exception as exc:  # noqa: BLE001 -- includes BudgetConfigError (corrupt config)
        token_status = None
        token_error = f"{type(exc).__name__}: {exc}"
    else:
        token_error = None

    out = budget_check(module_status, cost_verdict, token_status)
    errors = {k: v for k, v in (("module", module_error), ("cost", cost_error),
                                ("token", token_error)) if v}
    if errors:
        out["errors"] = errors
    return out


# ────────────────────────────────────────────────────────────────────────────
# The other nine checks -- each CALLS the module that owns the rule
# ────────────────────────────────────────────────────────────────────────────
def _kanban():
    """The scheduler reflex, resolved at CALL time (it imports this module)."""
    return importlib.import_module("tools.genesis.reflexes.kanban")


def _guarded(fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 -- a check that cannot run never blocks
        return _check(UNMEASURABLE, f"{type(exc).__name__}: {exc}")


def _check_pause() -> Dict[str, Any]:
    from tools.kanban import scheduler_control
    state = scheduler_control.should_pause()
    if state.get("paused"):
        return _check(WAIT, f"{REASON_PAUSED} ({state.get('mode')})", state=state)
    return _check(PROCEED, "not paused")


def _check_manual_build() -> Dict[str, Any]:
    if _kanban()._manual_build():
        return _check(WAIT, "manual build is on; the runner does not build")
    return _check(PROCEED, "automatic build")


def _check_manual_gate(task: Dict[str, Any]) -> Dict[str, Any]:
    from tools.kanban import gates
    if gates.is_manual_gate(task.get("id"), task.get("title")):
        return _check(REFUSE, f"{REASON_DECISION_BOUND}: a manual-gate sentinel is never dispatched")
    return _check(PROCEED, "not a gate sentinel")


def _check_lease(task_id: str) -> Dict[str, Any]:
    from tools.kanban import lease_liveness
    verdict = lease_liveness.task_lease_verdict(task_id)
    detail = {"state": verdict.state, "holder_session": verdict.holder_session,
              "holder_pid": verdict.holder_pid}
    if verdict.blocks_dispatch:
        return _check(WAIT, f"lease {verdict.state}: {lease_liveness.describe(verdict)}",
                      **detail)
    if verdict.state == lease_liveness.STATE_LITTER:
        return _check(PROCEED, "lease is litter (the dispatch window reaps it)",
                      noted=True, **detail)
    return _check(PROCEED, "lease free", **detail)


def _check_admission(task_id: str) -> Dict[str, Any]:
    verdict = dispatch_admission.assess(task_id)
    mapped = {dispatch_admission.ALLOW: PROCEED, dispatch_admission.REFUSE: REFUSE,
              dispatch_admission.UNMEASURABLE: UNMEASURABLE}.get(verdict.verdict, UNMEASURABLE)
    return _check(mapped, verdict.reason, prior_merged=verdict.prior_merged,
                  open_prs=verdict.open_prs)


def _check_recent_success(task_id: str) -> Dict[str, Any]:
    if _kanban()._had_recent_success(task_id, within_minutes=30):
        return _check(REFUSE, "completed successfully within the last 30 minutes")
    return _check(PROCEED, "no recent completion")


def _check_open_pr(task_id: str) -> Dict[str, Any]:
    if _kanban()._has_open_pr(task_id):
        return _check(WAIT, f"{REASON_REVIEW_BOUND}: an open PR already carries this task")
    return _check(PROCEED, "no open PR")


def _check_circuit_breaker(task: Dict[str, Any]) -> Dict[str, Any]:
    tripped, failures, max_retries = _kanban()._circuit_breaker_tripped(task)
    detail = {"failure_count": failures, "max_retries": max_retries}
    if tripped:
        return _check(REFUSE, f"{REASON_STUCK}: failure_count {failures} >= max_retries "
                              f"{max_retries}", **detail)
    return _check(PROCEED, f"failure_count {failures} < max_retries {max_retries}", **detail)


def _check_backpressure() -> Dict[str, Any]:
    from tools.kanban import backpressure
    state = backpressure.status()
    if not state.get("enabled"):
        return _check(PROCEED, "backpressure disabled", **state)
    if state.get("holding"):
        return _check(WAIT, f"{REASON_REVIEW_BOUND}: {state.get('unreviewed')} unreviewed "
                            f"output(s) at the ceiling of {state.get('ceiling')}", **state)
    return _check(PROCEED, f"{state.get('headroom')} slot(s) of headroom", **state)


def assess(task: Dict[str, Any]) -> ShouldRun:
    """The gate's entry point. Never raises; every check that cannot run is
    recorded `unmeasurable`, which never blocks."""
    task = dict(task or {})
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        return classify("", {})
    checks = {
        "pause": _guarded(_check_pause),
        "manual_build": _guarded(_check_manual_build),
        "manual_gate": _guarded(lambda: _check_manual_gate(task)),
        "lease": _guarded(lambda: _check_lease(task_id)),
        "admission": _guarded(lambda: _check_admission(task_id)),
        "recent_success": _guarded(lambda: _check_recent_success(task_id)),
        "open_pr": _guarded(lambda: _check_open_pr(task_id)),
        "circuit_breaker": _guarded(lambda: _check_circuit_breaker(task)),
        "backpressure": _guarded(_check_backpressure),
        "budget": _guarded(_live_budget_check),
    }
    return classify(task_id, checks)


# ────────────────────────────────────────────────────────────────────────────
# The survey -- the SAME budget fold, replayed over recorded dispatches
# ────────────────────────────────────────────────────────────────────────────
def _cap_status(spent: float, cap: float, *, warning_threshold: float,
                hard_stop: bool, label: str, spent_tokens: int = 0,
                cap_tokens: int = 0) -> Dict[str, Any]:
    """Shape a ledger reading into the ``{action, message}`` dict the trackers
    return, for a dispatch INSTANT the trackers cannot be asked about.

    ``check_module_budget`` and ``check_budget`` read "now" and take no as-of
    parameter, so the survey re-derives the cap comparison over the ledger rows
    as they stood at each dispatch. It is labelled ``replayed_ledger`` on every
    row it produces, and the THRESHOLD SEMANTICS are the trackers' own: a cap of
    0 is unlimited; ``block`` only under ``hard_stop``; ``warn`` at or above
    ``warning_threshold`` of the primary resource.
    """
    over = []
    if cap > 0 and spent > cap:
        over.append(f"USD {spent:.4f} over {cap:.2f}")
    if cap_tokens > 0 and spent_tokens > cap_tokens:
        over.append(f"tokens {spent_tokens} over {cap_tokens}")
    base = {"basis": "replayed_ledger", "spent_usd": round(spent, 6), "budget_usd": cap,
            "spent_tokens": spent_tokens, "budget_tokens": cap_tokens}
    if over and hard_stop:
        return {"action": "block", "message": f"{label}: " + "; ".join(over), **base}
    primary = (spent / cap) if cap > 0 else ((spent_tokens / cap_tokens) if cap_tokens > 0 else None)
    if primary is not None and primary >= warning_threshold:
        return {"action": "warn", "message": f"{label}: {primary:.0%} of cap", **base}
    return {"action": "allow", "message": f"{label}: within cap", **base}


def _replay_module(conn, module: str, at: datetime) -> Optional[Dict[str, Any]]:
    month = at.strftime("%Y-%m")
    try:
        period = conn.execute(
            "SELECT budget_usd, budget_tokens, warning_threshold, hard_stop "
            "FROM module_budget_periods WHERE module_name = %s AND month = %s",
            (module, month)).fetchone()
        if period is None:
            return None
        period = dict(period)
        spent = conn.execute(
            "SELECT COALESCE(SUM(amount), 0.0) AS usd, COALESCE(SUM(tokens), 0) AS tokens "
            "FROM module_budget_usage WHERE module_name = %s "
            "AND created_at >= %s AND created_at < %s",
            (module, f"{month}-01", at.isoformat())).fetchone()
        spent = dict(spent) if spent is not None else {"usd": 0.0, "tokens": 0}
    except Exception:  # noqa: BLE001
        return None
    return _cap_status(float(spent.get("usd") or 0.0), float(period.get("budget_usd") or 0.0),
                       warning_threshold=float(period.get("warning_threshold") or 0.8),
                       hard_stop=bool(period.get("hard_stop", 1)),
                       label=f"module {module} {month}",
                       spent_tokens=int(spent.get("tokens") or 0),
                       cap_tokens=int(period.get("budget_tokens") or 0))


def _replay_token(conn, agent_id: str, at: datetime, cfg: Dict[str, Any]
                  ) -> Optional[Dict[str, Any]]:
    if not cfg.get("enabled"):
        return {"action": "allow", "message": "token budget disabled", "basis": "config"}
    cap = float(cfg.get("budget_usd") or 0.0)
    if cap <= 0:
        return {"action": "allow", "message": "no cap (unlimited)", "basis": "config"}
    month = at.strftime("%Y-%m")
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_estimate_usd), 0.0) AS usd FROM agent_token_usage "
            "WHERE agent_id = %s AND created_at >= %s AND created_at < %s",
            (agent_id, f"{month}-01", at.isoformat())).fetchone()
    except Exception:  # noqa: BLE001
        return None
    spent = float(dict(row).get("usd") or 0.0) if row is not None else 0.0
    return _cap_status(spent, cap, warning_threshold=float(cfg.get("warning_threshold") or 0.8),
                       hard_stop=bool(cfg.get("hard_stop", True)),
                       label=f"agent {agent_id} {month}")


def _replay_cost(conn, at: datetime, settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """cost_budget's state at ``at``: a BudgetVerdict-shaped dict for ``_fold_cost``."""
    from tools.llm import cost_budget
    if not settings.get("enabled"):
        return {"status": "disabled", "action": "allow", "reason": "cost_budget disabled",
                "telemetry_available": True}
    try:
        limit = float(settings.get("limit_usd") or 0.0)
    except (TypeError, ValueError):
        limit = 0.0
    if limit <= 0:
        return {"status": "disabled", "action": "allow", "reason": "no positive limit_usd",
                "telemetry_available": True}
    period = str(settings.get("period", "monthly"))
    start = cost_budget.period_start(period, now=at)
    try:
        row = conn.execute(
            f"SELECT COALESCE(SUM(cost_usd), 0) FROM {cost_budget.TELEMETRY_TABLE} "
            "WHERE created_at >= %s AND created_at < %s",
            (start, at.isoformat())).fetchone()
    except Exception:  # noqa: BLE001
        return {"status": "unmeasurable", "action": "allow", "telemetry_available": False,
                "reason": "ai_telemetry unreadable at this instant"}
    spend = float(row[0] or 0.0) if row is not None else 0.0
    fraction = spend / limit
    base = {"period": period, "period_start": start, "spend_usd": round(spend, 6),
            "limit_usd": limit, "fraction": round(fraction, 4),
            "telemetry_available": True, "basis": "replayed_ledger"}
    if fraction >= 1.0:
        hard = str(settings.get("hard_action", cost_budget.ACTION_DOWNGRADE))
        return {"status": "hard", "action": hard,
                "downgraded": hard == cost_budget.ACTION_DOWNGRADE,
                "reason": f"hard limit reached (${spend:.4f} of ${limit:.2f})", **base}
    crossed = cost_budget._crossed_threshold(fraction, settings.get("soft_thresholds"))
    if crossed is not None:
        # The ASK is not replayed (it is a side effect); a crossed soft
        # threshold reads as `ask` here, the verdict the router would carry.
        return {"status": "soft", "action": "ask", "threshold_crossed": crossed,
                "reason": f"soft threshold {crossed:g} crossed at ${spend:.4f} of ${limit:.2f}",
                **base}
    return {"status": "ok", "action": "allow", "reason": f"${spend:.4f} of ${limit:.2f}", **base}


def _parse_dt(value: Any) -> Optional[datetime]:
    return dispatch_admission._parse_dt(value)


def survey(window_days: Optional[int] = None, conn=None) -> Dict[str, Any]:
    """What would the BUDGET rung have said at every recorded dispatch?

    The population is the ``-> in_progress`` transition written by the
    scheduler -- the exact moment ``assess`` runs (the dispatch_admission
    idiom). Only the budget rung is replayed: a lease, a pause sentinel or an
    open PR at a past instant is not recorded anywhere, so the other nine checks
    are ``unmeasurable`` in replay and the verdict distribution below is the
    distribution of the budget rung folded through the SAME ``classify``.
    Every ledger arm is labelled ``replayed_ledger``; caps and thresholds are
    read from the period row of the dispatch's month (module) or from today's
    config (token, cost) -- stated, because a cap edited since would move the
    replayed verdict for old rows.
    """
    close = False
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()
        close = True
    try:
        sql = ("SELECT task_id, recorded_at FROM kanban_status_transitions "
               "WHERE to_status = 'in_progress' AND actor = 'scheduler'")
        params: List[Any] = []
        if window_days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
            sql += " AND recorded_at >= %s"
            params.append(cutoff.isoformat())
        sql += " ORDER BY recorded_at"
        rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    except Exception as exc:  # noqa: BLE001
        if close:
            _close(conn)
        return {"state": "unmeasurable", "reason": f"transitions unreadable: {exc}",
                "dispatches": None, "fires": None, "fire_rate_pct": None}

    try:
        from tools.budget import module_budget_tracker as mbt
        module = mbt.module_for_function(BUDGET_FUNCTION)
    except Exception:  # noqa: BLE001
        module = "generative_intelligence"
    try:
        from tools.agent import token_tracker
        token_cfg = token_tracker._get_agent_budget(SCHEDULER_AGENT_ID)
    except Exception:  # noqa: BLE001
        token_cfg = None
    try:
        from tools.llm import cost_budget
        cost_settings = cost_budget.settings_for(BUDGET_FUNCTION)
    except Exception:  # noqa: BLE001
        cost_settings = None

    distribution = {v: 0 for v in VERDICTS}
    arm_counts = {arm: {v: 0 for v in VERDICTS} for arm in ("module", "cost", "token")}
    fires: List[Dict[str, Any]] = []
    dispatches = skipped = 0
    try:
        for row in rows:
            task_id = str(row.get("task_id") or "").strip()
            when = _parse_dt(row.get("recorded_at"))
            if not task_id or not when:
                skipped += 1
                continue
            dispatches += 1
            module_status = _replay_module(conn, module, when)
            token_status = (_replay_token(conn, SCHEDULER_AGENT_ID, when, token_cfg)
                            if token_cfg is not None else None)
            cost_verdict = (_replay_cost(conn, when, cost_settings)
                            if cost_settings is not None else None)
            budget = budget_check(module_status, cost_verdict, token_status,
                                  month=when.strftime("%Y-%m"))
            verdict = classify(task_id, {"budget": budget})
            distribution[verdict.verdict] += 1
            for arm, result in budget.get("arms", {}).items():
                arm_counts[arm][result.get("verdict", UNMEASURABLE)] += 1
            if verdict.verdict in BLOCKING:
                fires.append({"task_id": task_id, "at": when.isoformat(),
                              "verdict": verdict.verdict, "reasons": verdict.reasons})
    finally:
        if close:
            _close(conn)

    if not dispatches:
        return {"state": "unmeasurable",
                "reason": "no recorded scheduler dispatches in this window",
                "dispatches": 0, "fires": None, "fire_rate_pct": None}
    measured = dispatches - distribution[UNMEASURABLE]
    return {
        "state": "measured",
        "window_days": window_days,
        "dispatches": dispatches, "unparseable_rows": skipped,
        "budget_module": module, "budget_function": BUDGET_FUNCTION,
        "scheduler_agent_id": SCHEDULER_AGENT_ID,
        "verdicts": distribution,
        "arms": arm_counts,
        "measured": measured,
        "fires": len(fires),
        # Fire rate over the MEASURED population; None (never 0.0) when the
        # ledgers answered for no dispatch at all.
        "fire_rate_pct": (round(len(fires) / measured * 100, 2) if measured else None),
        "fire_rate_of_all_dispatches_pct": round(len(fires) / dispatches * 100, 2),
        "unmeasurable_pct": round(distribution[UNMEASURABLE] / dispatches * 100, 2),
        "fired": fires[:50],
        "mode": mode(),
        "replay_note": ("budget rung only; the other nine checks are not recorded at a "
                        "past instant and are unmeasurable in replay"),
    }


def _close(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────
def _load_task(task_id: str) -> Dict[str, Any]:
    """The board row for one task, read through the reflex's own loader (this
    module names no board table -- pinned by AST). A task the board does not
    know is assessed on its id alone; every board-backed check then answers for
    itself."""
    try:
        row = _kanban()._task_row(task_id)
        if row:
            return dict(row)
    except Exception:  # noqa: BLE001
        pass
    return {"id": task_id}


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--task", help="assess one task id")
    parser.add_argument("--survey", action="store_true",
                        help="replay recorded dispatches through the same fold")
    parser.add_argument("--window-days", type=int, default=None)
    parser.add_argument("--env-file", default=None,
                        help="load this .env first (a worktree has none and would read "
                             "a throwaway database)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.env_file:
        try:
            from dotenv import load_dotenv
            load_dotenv(args.env_file, override=True)
        except Exception as exc:  # noqa: BLE001
            print(f"warning: could not load {args.env_file}: {exc}", file=sys.stderr)

    if args.task:
        verdict = assess(_load_task(args.task))
        if args.json:
            print(json.dumps(verdict.to_dict(), indent=2, default=str))
        else:
            print(f"{verdict.verdict.upper()}  {verdict.task_id}")
            for reason in verdict.reasons:
                print(f"  - {reason}")
            for name in CHECKS:
                result = verdict.checks.get(name) or {}
                print(f"  {name:<16} {result.get('verdict', '?'):<12} {result.get('reason', '')}")
            if verdict.resets_at:
                print(f"  resets_at={verdict.resets_at}")
            print(f"  mode={mode()} blocks={verdict.blocks}")
        return 0

    if args.survey:
        report = survey(window_days=args.window_days)
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        elif report["state"] != "measured":
            print(f"Survey UNMEASURABLE: {report.get('reason')}")
        else:
            print(f"should_run survey -- {report['dispatches']} dispatch(es), "
                  f"budget rung replayed ({report['budget_module']} / "
                  f"{report['scheduler_agent_id']})")
            for verdict, count in report["verdicts"].items():
                print(f"  {verdict:<13} {count}")
            for arm, counts in report["arms"].items():
                print(f"  arm {arm:<7} " + "  ".join(f"{v}={n}" for v, n in counts.items() if n))
            rate = report["fire_rate_pct"]
            print(f"  would block   : {report['fires']} "
                  f"({'?' if rate is None else rate}% of {report['measured']} measured; "
                  f"{report['fire_rate_of_all_dispatches_pct']}% of all)")
            print(f"  unmeasurable  : {report['unmeasurable_pct']}% of dispatches")
            print(f"  mode          : {report['mode']}")
            print("  (1.63% of calls is the rate this repo already calls refusing "
                  "routine work)")
        return 0 if report["state"] == "measured" else 2

    parser.error("one of --task or --survey is required")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
