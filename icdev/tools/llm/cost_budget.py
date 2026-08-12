# CUI // SP-CTI
"""Cost budget as a DOWNGRADE gate on the LLMRouter chain (exa-policy-04).

ICDEV already has four budget layers and every one of them BLOCKS:

  ``tools/agent/token_tracker.py``      per-agent monthly, raised in ``router.invoke``
  ``tools/budget/module_budget_tracker`` per-module, ``ModuleBudgetExceededError``
  ``tools/llm/chain_orchestrator.py``    per chain run
  ``tools/llm/proxy_budgets.py``         per virtual key

A hard stop is the wrong shape for a long autonomous run. Hitting a ceiling at
02:00 kills the run; what an operator actually wants is for the work to keep
going on something cheaper. This gate does the two things the existing four do
not:

  soft threshold  ASK — once per threshold per period, never once per call
  hard limit      DOWNGRADE — reorder the function's DECLARED chain so the
                  cheap tier is tried first, instead of refusing the call

Nothing here invents a model. The downgrade operates purely on
``routing.<function>.chain`` and the ``pricing:`` already declared for each
model in ``args/llm_config.yaml`` — so there is no model id in this file, and an
air-gapped deployment downgrades onto its local Ollama tier by construction:
local models declare ``input_per_1k: 0.0`` and sort first under
``downgrade.prefer_local``.

## Why reorder rather than truncate

Truncating the chain to the affordable tier converts a budget event into an
outage the moment that tier is down. Reordering preserves every fallback the
operator declared while making the expensive model unreachable in practice — it
is tried only after the cheap tier has actually failed, which is the fallback
semantics the chain already means. ``downgrade.max_blended_per_1k`` is the
ceiling that decides which models count as affordable; models above it are
demoted to the tail, never dropped.

## Measurement, and refusing to fabricate a zero

Spend is read from ``ai_telemetry`` (cost_usd, function, created_at) — existing
telemetry, no new table. When that table is absent the verdict is
``unmeasurable`` and the action is ``allow``: a fresh worktree or an ephemeral
CI database must not read as "you have spent nothing, spend freely", and it must
not read as "you are over budget" either. Same principle as
``tools/awareness/capability_consumption.py``.

## The ASK is deduped through its own audit trail

An ASK that fires on every call is noise an operator learns to ignore, so
crossing 0.8 asks once for that period. The dedupe key is
``(tool_name, rule, period)`` read back from ``agent_approval_log`` — the
append-only table the approval gate already writes — plus a process-local cache
so the steady state costs no query. Spending money is irreversible in the
literal sense that table exists to record, so the tier is ``irreversible`` and
the vocabulary is not bent.

CLI::

    python tools/llm/cost_budget.py --status --json
    python tools/llm/cost_budget.py --function code_generation --json
    python tools/llm/cost_budget.py --explain code_generation --json
    python tools/llm/cost_budget.py --gate
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.llm.cost_budget")

CONFIG_SECTION = "cost_budget"
TELEMETRY_TABLE = "ai_telemetry"
AUDIT_TABLE = "agent_approval_log"

# Statuses — what the budget says.
STATUS_DISABLED = "disabled"
STATUS_UNMEASURABLE = "unmeasurable"
STATUS_OK = "ok"
STATUS_SOFT = "soft"
STATUS_HARD = "hard"

# Actions — what the router should do about it.
ACTION_ALLOW = "allow"
ACTION_ASK = "ask"
ACTION_DOWNGRADE = "downgrade"
ACTION_BLOCK = "block"

# Config defaults. Every one of these is overridable in args/llm_config.yaml;
# they exist so an installation that has not declared a cost_budget block yet
# behaves as it always did (disabled => allow) rather than guessing a limit.
_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "scope": "global",              # global | function
    "period": "monthly",            # monthly | daily
    "limit_usd": 0.0,
    "soft_thresholds": [],
    "hard_action": ACTION_DOWNGRADE,  # downgrade | block
    "downgrade": {
        "max_blended_per_1k": 0.0,
        "prefer_local": True,
    },
    "ask": {
        "approver": "record",       # record | console | deny
        "on_denied": ACTION_DOWNGRADE,
    },
    "per_function": {},
}

# Process-local "already asked" set, keyed (tool_name, rule, period_key). Purely
# an optimisation over the agent_approval_log read — correctness of the
# once-per-threshold guarantee does not depend on it, because a fresh process
# re-reads the log.
_ASKED: set[tuple[str, str, str]] = set()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config(config: Optional[dict] = None) -> dict:
    """Return the full llm_config dict, or ``config`` when one is supplied."""
    if config is not None:
        return config
    try:
        import yaml

        from tools.llm.config_path import resolve_llm_config_path

        return yaml.safe_load(resolve_llm_config_path().read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — a missing config disables the gate
        logger.debug("cost_budget: config unavailable (%s); gate disabled", exc)
        return {}


def settings_for(function: str, config: Optional[dict] = None) -> dict:
    """Merge ``cost_budget`` with its ``per_function`` override for *function*.

    One level of merge on purpose: ``downgrade`` and ``ask`` are merged key by
    key so an override can change ``max_blended_per_1k`` without having to
    restate ``prefer_local``.
    """
    cfg = load_config(config)
    section = cfg.get(CONFIG_SECTION) or {}
    merged: dict[str, Any] = {**_DEFAULTS, **{k: v for k, v in section.items() if k != "per_function"}}
    for nested in ("downgrade", "ask"):
        merged[nested] = {**_DEFAULTS[nested], **(section.get(nested) or {})}

    override = (section.get("per_function") or {}).get(function) or {}
    for key, value in override.items():
        if key in ("downgrade", "ask") and isinstance(value, dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def is_declared_function(function: str, config: Optional[dict] = None) -> bool:
    """True when *function* has its own ``routing:`` entry.

    An undeclared llm_function silently falls back to ``routing.default``, so a
    budget decision made "for code_generation" would in fact be reordering some
    other function's chain. Callers surface this rather than hiding it.
    """
    routing = (load_config(config).get("routing") or {})
    return function in routing


# ---------------------------------------------------------------------------
# Period + spend
# ---------------------------------------------------------------------------
def period_start(period: str = "monthly", *, now: Optional[datetime] = None) -> str:
    """Return the inclusive start of the current period as a DATE prefix.

    A date-only string (``2026-08-01``) rather than a full ISO timestamp, and
    that is load-bearing. ``ai_telemetry.created_at`` is TEXT written by two
    different writers: SQLite's ``datetime('now')`` emits ``2026-08-01 10:00:00``
    (space separator) while Python's ``isoformat()`` emits
    ``2026-08-01T10:00:00+00:00`` (``T``). Comparing against a full ISO boundary
    lexicographically drops every space-separated row on the first day of the
    period, because ``' '`` sorts below ``'T'``. A 10-character date prefix is
    correct under both spellings and casts cleanly if the column is a real
    PostgreSQL timestamp.
    """
    moment = now or datetime.now(timezone.utc)
    if str(period).lower() == "daily":
        return moment.strftime("%Y-%m-%d")
    return moment.strftime("%Y-%m-01")


def read_spend(
    *, since: str, function: Optional[str] = None
) -> tuple[float, bool]:
    """Return ``(spend_usd, telemetry_available)`` from ``ai_telemetry``.

    ``telemetry_available`` is False when the table does not exist or cannot be
    read. The caller must NOT treat that as 0.0 spend — see module docstring.
    """
    try:
        from tools.db.storage import get_connection, table_exists

        conn = get_connection()
        try:
            if not table_exists(conn, TELEMETRY_TABLE):
                return 0.0, False
            sql = (
                f"SELECT COALESCE(SUM(cost_usd), 0) FROM {TELEMETRY_TABLE} "
                "WHERE created_at >= %s"
            )
            params: list[Any] = [since]
            if function:
                sql += " AND function = %s"
                params.append(function)
            cur = conn.execute(sql, tuple(params))
            row = cur.fetchone()
            return (float(row[0]) if row and row[0] is not None else 0.0), True
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001 — an unreadable budget must not break routing
        logger.debug("cost_budget: spend read failed (%s); reporting unmeasurable", exc)
        return 0.0, False


# ---------------------------------------------------------------------------
# Pricing + the downgrade itself
# ---------------------------------------------------------------------------
def blended_price(model_name: str, models: dict) -> Optional[float]:
    """Declared input+output price per 1k tokens for *model_name*.

    ``None`` for a model that declares no pricing — unknown price is not free.
    Summing the two rates is a deliberate simplification: the gate only needs a
    stable ORDER over the chain, not a cost estimate, and no realistic chain has
    two models whose ordering flips under a different input/output mix.
    """
    spec = models.get(model_name)
    if not isinstance(spec, dict):
        return None
    pricing = spec.get("pricing")
    if not isinstance(pricing, dict):
        return None
    try:
        return float(pricing.get("input_per_1k", 0.0)) + float(pricing.get("output_per_1k", 0.0))
    except (TypeError, ValueError):
        return None


def _is_local(model_name: str, models: dict, providers: dict) -> bool:
    """Locality via the ONE definition of local (cli_bridge.activate).

    Never a second inline ``provider == 'ollama'`` test: two definitions of
    "local" is how CUI leaks, and an unresolvable model must not read as local.
    """
    try:
        from tools.llm.cli_bridge.activate import is_local_only_model

        return bool(is_local_only_model(model_name, models, providers))
    except Exception:  # noqa: BLE001 — cannot prove local => not local
        return False


def downgrade_chain(
    chain: list, models: dict, providers: dict, *, settings: Optional[dict] = None
) -> list:
    """Reorder *chain* so the affordable tier is tried first.

    Sort key, in order:

      1. over the ``max_blended_per_1k`` ceiling last (this is the demotion; an
         unpriced model counts as over the ceiling — unknown price is not free)
      2. cheaper first
      3. local first among equals when ``prefer_local`` — this is what makes the
         downgrade land on Ollama in an air-gapped deployment rather than on
         whichever zero-priced cloud model happened to be listed first
      4. original chain position, so an otherwise-tied pair keeps the operator's
         declared preference

    Every model survives. The chain is a fallback list, so demoting the
    expensive model to the tail makes it unreachable while the cheap tier works
    and still available if that tier is down.
    """
    conf = {**_DEFAULTS["downgrade"], **((settings or {}).get("downgrade") or {})}
    try:
        ceiling = float(conf.get("max_blended_per_1k", 0.0))
    except (TypeError, ValueError):
        ceiling = 0.0
    prefer_local = bool(conf.get("prefer_local", True))

    def sort_key(item: tuple[int, Any]) -> tuple:
        index, name = item
        price = blended_price(name, models)
        over = 1 if (price is None or price > ceiling) else 0
        # An unpriced model sorts after every priced one within the "over" group.
        effective = float("inf") if price is None else price
        local_rank = 0 if (prefer_local and _is_local(name, models, providers)) else 1
        return (over, effective, local_rank, index)

    return [name for _index, name in sorted(enumerate(chain), key=sort_key)]


# ---------------------------------------------------------------------------
# The ASK
# ---------------------------------------------------------------------------
def _already_asked(tool_name: str, rule: str, since: str) -> bool:
    """True when this exact threshold was already asked in this period."""
    if (tool_name, rule, since) in _ASKED:
        return True
    try:
        from tools.db.storage import get_connection, table_exists

        conn = get_connection()
        try:
            if not table_exists(conn, AUDIT_TABLE):
                return False
            cur = conn.execute(
                f"SELECT 1 FROM {AUDIT_TABLE} "
                "WHERE tool_name = %s AND rule = %s AND decided_at >= %s LIMIT 1",
                (tool_name, rule, since),
            )
            return cur.fetchone() is not None
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        # Cannot read the log => cannot prove we already asked. Asking twice is
        # noisy; never asking is the failure that matters.
        logger.debug("cost_budget: ask-dedupe read failed (%s); asking", exc)
        return False


def _resolve_approver(name: str) -> Callable:
    """Map the config's ``ask.approver`` to an approver callable."""
    from tools.agent_runtime.approval_gate import (
        ApprovalDecision,
        console_approver,
        deny_all_approver,
    )

    if name == "console":
        return console_approver
    if name == "deny":
        return deny_all_approver

    def record_approver(request) -> ApprovalDecision:
        """Autonomous default: surface the ASK, record it, keep working.

        A long unattended run has no console. Denying there would turn a soft
        threshold into the hard stop this whole module exists to avoid, so the
        soft ASK is a recorded, logged notification and the enforcement stays
        where it belongs — at the hard limit.
        """
        logger.warning("cost budget ASK: %s", request.summary())
        return ApprovalDecision(True, "recorded (autonomous run, no console)", request.actor)

    return record_approver


def ask(
    *, function: str, threshold: float, detail: dict, settings: dict, since: str
) -> tuple[bool, bool]:
    """Raise the soft-threshold ASK once. Returns ``(asked, approved)``.

    ``asked`` is False when this threshold was already raised this period.
    """
    from tools.agent_runtime.approval_gate import (
        ApprovalRequest,
        Classification,
        IRREVERSIBLE,
        record_decision,
        resolve_actor,
        resolve_mode,
    )

    tool_name = f"llm_invoke:{function}"
    rule = f"{CONFIG_SECTION}:soft:{threshold:g}"
    if _already_asked(tool_name, rule, since):
        return False, True

    classification = Classification(
        tool_name=tool_name,
        tier=IRREVERSIBLE,
        rule=rule,
        detail=json.dumps(detail, sort_keys=True),
        requires_approval=True,
    )
    tool_input = {
        "function": function,
        "scope": settings.get("scope", "global"),
        "period": settings.get("period", "monthly"),
        "threshold": threshold,
    }
    actor = resolve_actor()
    approver = _resolve_approver(str((settings.get("ask") or {}).get("approver", "record")))
    try:
        decision = approver(
            ApprovalRequest(
                tool_name=tool_name,
                tool_input=tool_input,
                classification=classification,
                actor=actor,
            )
        )
    except Exception as exc:  # noqa: BLE001 — a broken approver must not break routing
        from tools.agent_runtime.approval_gate import ApprovalDecision

        logger.warning("cost_budget: approver raised (%s); recording as unanswered", exc)
        decision = ApprovalDecision(True, f"approver error: {exc}", actor)

    if not isinstance(getattr(decision, "approved", None), bool):
        from tools.agent_runtime.approval_gate import ApprovalDecision

        decision = ApprovalDecision(bool(decision), "approver returned bool", actor)

    record_decision(
        tool_name=tool_name,
        tool_input=tool_input,
        classification=classification,
        decision=decision,
        mode=resolve_mode(),
    )
    _ASKED.add((tool_name, rule, since))
    return True, bool(decision.approved)


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
@dataclass
class BudgetVerdict:
    """What the budget says, and what the router should do about it."""

    function: str
    status: str = STATUS_DISABLED
    action: str = ACTION_ALLOW
    spend_usd: float = 0.0
    limit_usd: float = 0.0
    fraction: float = 0.0
    period: str = "monthly"
    period_start: str = ""
    scope: str = "global"
    telemetry_available: bool = True
    threshold_crossed: Optional[float] = None
    asked: bool = False
    ask_approved: Optional[bool] = None
    downgraded: bool = False
    routing_declared: bool = True
    chain_before: list = field(default_factory=list)
    chain_after: list = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _crossed_threshold(fraction: float, thresholds) -> Optional[float]:
    """Highest declared soft threshold at or below *fraction*, if any."""
    crossed = [float(t) for t in (thresholds or []) if fraction >= float(t)]
    return max(crossed) if crossed else None


def evaluate(
    function: str,
    chain: Optional[list] = None,
    *,
    config: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> BudgetVerdict:
    """Evaluate the cost budget for *function* and decide the chain to route on.

    Never raises. Every failure path resolves to ``allow`` with the chain
    unchanged: a budget gate that can take routing down is worse than the
    overspend it prevents.
    """
    cfg = load_config(config)
    settings = settings_for(function, cfg)
    verdict = BudgetVerdict(function=function, chain_before=list(chain or []))
    verdict.chain_after = list(verdict.chain_before)

    if not settings.get("enabled"):
        verdict.reason = "cost_budget disabled in args/llm_config.yaml"
        return verdict

    try:
        limit = float(settings.get("limit_usd") or 0.0)
    except (TypeError, ValueError):
        limit = 0.0
    if limit <= 0:
        verdict.reason = "no positive limit_usd declared"
        return verdict

    verdict.limit_usd = limit
    verdict.scope = str(settings.get("scope", "global"))
    verdict.period = str(settings.get("period", "monthly"))
    verdict.period_start = period_start(verdict.period, now=now)
    verdict.routing_declared = is_declared_function(function, cfg)
    if not verdict.routing_declared:
        # Not fatal, but it means the chain being reordered belongs to
        # routing.default. Say so rather than letting it pass as this
        # function's own budget decision.
        logger.warning(
            "cost_budget: function %r is not declared under routing: in "
            "args/llm_config.yaml — it falls back to routing.default, so the "
            "downgrade acts on the default chain",
            function,
        )

    spend, available = read_spend(
        since=verdict.period_start,
        function=function if verdict.scope == "function" else None,
    )
    verdict.telemetry_available = available
    if not available:
        verdict.status = STATUS_UNMEASURABLE
        verdict.reason = (
            f"{TELEMETRY_TABLE} unreadable — spend is unknown, not zero; "
            "routing unchanged"
        )
        return verdict

    verdict.spend_usd = spend
    verdict.fraction = spend / limit

    models = cfg.get("models") or {}
    providers = cfg.get("providers") or {}
    detail = {
        "function": function,
        "spend_usd": round(spend, 6),
        "limit_usd": limit,
        "fraction": round(verdict.fraction, 4),
        "period": verdict.period,
        "period_start": verdict.period_start,
        "scope": verdict.scope,
    }

    if verdict.fraction >= 1.0:
        verdict.status = STATUS_HARD
        hard_action = str(settings.get("hard_action", ACTION_DOWNGRADE))
        if hard_action == ACTION_BLOCK:
            verdict.action = ACTION_BLOCK
            verdict.reason = (
                f"hard limit reached (${spend:.4f} of ${limit:.2f}) and "
                "hard_action is 'block'"
            )
            return verdict
        verdict.action = ACTION_DOWNGRADE
        verdict.chain_after = downgrade_chain(
            verdict.chain_before, models, providers, settings=settings
        )
        verdict.downgraded = verdict.chain_after != verdict.chain_before
        verdict.reason = (
            f"hard limit reached (${spend:.4f} of ${limit:.2f}) — downgraded to "
            "the affordable tier instead of failing the call"
        )
        return verdict

    threshold = _crossed_threshold(verdict.fraction, settings.get("soft_thresholds"))
    if threshold is None:
        verdict.status = STATUS_OK
        verdict.reason = f"${spend:.4f} of ${limit:.2f} ({verdict.fraction:.0%})"
        return verdict

    verdict.status = STATUS_SOFT
    verdict.threshold_crossed = threshold
    detail["threshold"] = threshold
    try:
        verdict.asked, approved = ask(
            function=function,
            threshold=threshold,
            detail=detail,
            settings=settings,
            since=verdict.period_start,
        )
        verdict.ask_approved = approved
    except Exception as exc:  # noqa: BLE001 — the ASK must not break routing
        logger.warning("cost_budget: ASK failed (%s); allowing", exc)
        verdict.reason = f"soft threshold {threshold:g} crossed; ASK failed: {exc}"
        return verdict

    if verdict.ask_approved:
        verdict.action = ACTION_ASK if verdict.asked else ACTION_ALLOW
        verdict.reason = (
            f"soft threshold {threshold:g} crossed at ${spend:.4f} of ${limit:.2f}"
            + ("" if verdict.asked else " (already asked this period)")
        )
        return verdict

    # A denied soft ASK is an operator saying "stop spending on the expensive
    # model" before the hard limit arrives. Downgrade early rather than fail.
    on_denied = str((settings.get("ask") or {}).get("on_denied", ACTION_DOWNGRADE))
    if on_denied == ACTION_BLOCK:
        verdict.action = ACTION_BLOCK
        verdict.reason = f"soft threshold {threshold:g} ASK denied; ask.on_denied is 'block'"
        return verdict
    verdict.action = ACTION_DOWNGRADE
    verdict.chain_after = downgrade_chain(
        verdict.chain_before, models, providers, settings=settings
    )
    verdict.downgraded = verdict.chain_after != verdict.chain_before
    verdict.reason = f"soft threshold {threshold:g} ASK denied — downgraded early"
    return verdict


def apply_to_chain(
    function: str, chain: list, *, config: Optional[dict] = None
) -> tuple[list, BudgetVerdict]:
    """Router entry point. Returns ``(chain_to_route_on, verdict)``.

    Never raises — an unusable budget gate returns the chain it was handed.
    """
    try:
        verdict = evaluate(function, chain, config=config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cost_budget: evaluation failed (%s); routing unchanged", exc)
        return list(chain), BudgetVerdict(
            function=function,
            chain_before=list(chain),
            chain_after=list(chain),
            reason=f"evaluation error: {exc}",
        )
    if verdict.status in (STATUS_HARD, STATUS_SOFT) or verdict.downgraded:
        logger.info("cost_budget[%s]: %s -> %s", function, verdict.action, verdict.reason)
    return list(verdict.chain_after), verdict


class CostBudgetExceededError(RuntimeError):
    """Raised only when ``hard_action`` (or ``ask.on_denied``) is ``block``.

    The default is ``downgrade``; this exists so an operator who genuinely wants
    a hard stop can still declare one, and so the four existing blocking layers
    have a shape to converge on.
    """

    def __init__(self, verdict: BudgetVerdict):
        self.verdict = verdict
        super().__init__(
            f"cost budget blocked '{verdict.function}': {verdict.reason}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cost budget downgrade gate for the LLMRouter chain."
    )
    parser.add_argument("--status", action="store_true", help="Show budget status")
    parser.add_argument("--function", default="default", help="Function to evaluate")
    parser.add_argument(
        "--explain",
        metavar="FUNCTION",
        help="Show the declared chain and what it downgrades to",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Exit 1 when the budget is exhausted and hard_action is 'block'",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    cfg = load_config()
    function = args.explain or args.function
    chain = ((cfg.get("routing") or {}).get(function) or {}).get("chain") or []
    if not chain:
        chain = ((cfg.get("routing") or {}).get("default") or {}).get("chain") or []
    verdict = evaluate(function, chain, config=cfg)

    payload = verdict.to_dict()
    if args.explain:
        models = cfg.get("models") or {}
        payload["prices"] = {
            name: blended_price(name, models) for name in verdict.chain_before
        }

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(f"function      : {verdict.function}")
        print(f"status/action : {verdict.status} / {verdict.action}")
        print(f"spend         : ${verdict.spend_usd:.4f} of ${verdict.limit_usd:.2f}")
        print(f"period        : {verdict.period} since {verdict.period_start}")
        print(f"telemetry     : {'available' if verdict.telemetry_available else 'UNAVAILABLE'}")
        print(f"chain         : {verdict.chain_before}")
        if verdict.downgraded:
            print(f"downgraded to : {verdict.chain_after}")
        print(f"reason        : {verdict.reason}")

    if args.gate and verdict.action == ACTION_BLOCK:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
