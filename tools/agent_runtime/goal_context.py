# CUI // SP-CTI
"""Budgeted standing-goal injection for the SAG runtime (hgx-goal-02).

WHY THIS EXISTS

:mod:`tools.agent_runtime.standing_goals` (hgx-goal-01) persists durable
objectives and can render a compact list of them, but nothing ever put that list
in front of the model. A goal the agent never sees is a row in a table, not a
standing goal. This module is the seam between the two: it turns the active
goals for one operator/session into the block
:meth:`AgentRuntime._effective_system_prompt` prepends.

It is deliberately separate from ``standing_goals``. That module is pure CRUD +
lifecycle with no notion of a model or a context window; the budgeting here reads
``args/llm_config.yaml`` through :mod:`tools.llm.context_budget`. Keeping the two
apart is what lets the goal store stay usable from the CLI, the dashboard or a
test with no LLM config present at all.

TWO CAPS, NOT ONE

A *count* cap (``limit``, default 5) and a *token* cap are both required, and
neither subsumes the other:

* The count cap is the product decision — beyond a handful of simultaneous
  objectives the agent is not pursuing goals, it is reading a backlog. It is
  what the operator reasons about, so it is the one that is configurable
  (``ICDEV_SAG_GOAL_LIMIT``).
* The token cap is the safety net. Five goals is five *short* goals only if
  nobody pasted a paragraph into ``detail``. Sized from
  :func:`tools.llm.context_budget.available_input_tokens` — the real window for
  the routing function, floored across the routed chain — so a 32k local model
  and a 200k cloud model get proportionate blocks rather than the same one.

Nothing is dropped silently: the block always says how many goals it is not
showing and how to see them (``/goal list``).

LLM-agnostic: no model IDs; the window is resolved by routing function through
the same config the router reads, and every failure path returns ``""`` so a
checkout with no LLM config still runs. OS-agnostic: no filesystem access beyond
what ``context_budget`` already does, and no shell.

CLI::

    python tools/agent_runtime/goal_context.py --json
    python tools/agent_runtime/goal_context.py --user op --function code_generation
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Optional

from tools.llm.context_budget import available_input_tokens, estimate_tokens
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.goal_context")

#: Default number of goals injected. Five is the point past which a "standing
#: goal" list stops reading as intent and starts reading as a backlog.
DEFAULT_LIMIT = 5

#: Hard ceiling on the configurable limit — an operator who exports 500 has
#: misunderstood the feature, and honouring it would defeat the token cap's
#: purpose by handing the packer 500 candidates to shave.
MAX_LIMIT = 25

#: Share of the *available* input window the goal block may occupy. Small on
#: purpose: goals are one line each, and the window belongs to the conversation.
WINDOW_SHARE = 0.05

#: Reserved out of the budget for the two "this was cut" footers, so announcing
#: a truncation can never itself overrun the budget. Sized from the footers'
#: measured cost, not guessed.
_FOOTER_RESERVE = 56

#: Fewest tokens one goal is worth rendering in.
_MIN_GOAL_TOKENS = 12

#: Below this the block is dropped entirely: the fixed header, instruction and
#: footer reserve plus one goal do not fit, and a goal list clipped to a few
#: tokens is a fragment of an objective — worse than no objective at all.
MIN_BLOCK_TOKENS = 128

#: Longest ``detail`` rendered for one goal, before the token budget applies.
_MAX_DETAIL_CHARS = 240

ENV_DISABLE = "ICDEV_SAG_GOALS"
ENV_LIMIT = "ICDEV_SAG_GOAL_LIMIT"

_HEADER = "## Standing goals"
#: Kept short deliberately — it is charged to the same budget as the goals, and
#: a preamble longer than the list it introduces is the block eating itself.
_INSTRUCTION = (
    "Durable objectives that outlive this conversation. Pursue them alongside "
    "the immediate request; say so when one conflicts. Managed with `/goal`."
)


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def goal_limit(default: int = DEFAULT_LIMIT) -> int:
    """How many goals to inject: ``ICDEV_SAG_GOAL_LIMIT`` clamped to sane bounds.

    An unparseable or negative value falls back to ``default`` rather than
    disabling injection — a typo in an env var should not silently strip the
    agent's objectives. Set ``ICDEV_SAG_GOALS=0`` to turn the block off.
    """
    raw = os.environ.get(ENV_LIMIT)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.debug("goal_context: bad %s=%r, using %d", ENV_LIMIT, raw, default)
        return default
    if value <= 0:
        return default
    return min(value, MAX_LIMIT)


def budget_tokens(
    llm_function: str,
    *,
    system_prompt: str = "",
    share: float = WINDOW_SHARE,
    reserved_output: int = 1024,
) -> int:
    """Tokens the goal block may occupy for ``llm_function``.

    A share of the real available window, never a constant — see
    :func:`tools.llm.context_budget.available_input_tokens`, which floors across
    the routed chain because the model that actually serves the call may be the
    smallest member of it.
    """
    usable = available_input_tokens(
        llm_function, system_prompt=system_prompt, reserved_output=reserved_output
    )
    return max(int(usable * share), 0)


def _fit_text(text: str, budget: int) -> str:
    """Trim ``text`` to ``budget`` tokens at a word boundary, marking the cut."""
    if budget <= 0:
        return ""
    if estimate_tokens(text) <= budget:
        return text
    words = text.split()
    kept: list[str] = []
    for word in words:
        candidate = " ".join([*kept, word])
        if estimate_tokens(candidate + " …") > budget:
            break
        kept.append(word)
    return (" ".join(kept) + " …") if kept else ""


def _render_one(
    goal: Any, *, title_budget: int = 0, detail_budget: int = 0
) -> "tuple[str, bool]":
    """Render one goal, reporting whether anything was cut to make it fit.

    The flag is why this is separate from :func:`render_goal`: a block that
    shaved a title or dropped a detail line must say so, and only the renderer
    knows it happened. An agent that cannot tell a whole objective from half of
    one will confidently act on half of it.
    """
    title = str(getattr(goal, "title", "") or "").strip()
    if not title:
        return "", False
    shaved = False
    if title_budget > 0:
        fitted_title = _fit_text(title, title_budget)
        if not fitted_title:
            return "", True
        shaved = shaved or fitted_title != title
        title = fitted_title
    progress = getattr(goal, "progress", 0) or 0
    suffix = f" [{int(progress)}% done]" if progress else ""
    lines = [f"- {title}{suffix}"]
    detail = " ".join(str(getattr(goal, "detail", "") or "").split())
    if detail:
        clipped = detail[:_MAX_DETAIL_CHARS]
        fitted = _fit_text(clipped, detail_budget) if detail_budget > 0 else ""
        if fitted:
            lines.append(f"    {fitted}")
            shaved = shaved or fitted != detail
        else:
            shaved = True  # the whole detail line did not fit
    return "\n".join(lines), shaved


def render_goal(
    goal: Any, *, title_budget: int = 0, detail_budget: int = 0
) -> str:
    """Render one goal as the one or two lines the model sees.

    Both budgets are in tokens; 0 means "no limit" for the title and "omit" for
    the detail. Capping the *title* too is what makes the block's total bound
    hold — a goal store row allows 300 characters, so five of them can overrun a
    small window's share on their own. Returns ``""`` when nothing legible fits,
    and the caller counts that goal as withheld rather than printing a stub.

    The percentage is shown only when non-zero: "[0% done]" on every goal is
    noise that reads like a signal.
    """
    return _render_one(
        goal, title_budget=title_budget, detail_budget=detail_budget
    )[0]


def render_block(goals: list, *, budget: int, limit: int = DEFAULT_LIMIT) -> dict:
    """Render the goal block and report exactly what it left out.

    Applies the count cap first, then the token cap, then reports the total
    withheld by either. Returns ``{text, tokens, shown, withheld, truncated,
    shortened}``.

    The two caps fail differently and both are announced. The token cap first
    *shortens* goal text within each goal's share (``shortened``); only when
    even the shortened form does not fit are later goals dropped
    (``truncated``). Preferring to shave over to drop keeps every objective at
    least named — a goal the agent has never heard of is worse than one it has
    heard summarised.
    """
    empty = {
        "text": "", "tokens": 0, "shown": 0, "withheld": len(goals),
        "truncated": False, "shortened": False,
    }
    if not goals or budget < MIN_BLOCK_TOKENS:
        return empty

    capped = list(goals)[: max(int(limit), 0)]
    if not capped:
        return empty

    overhead = estimate_tokens(f"{_HEADER}\n{_INSTRUCTION}\n") + _FOOTER_RESERVE
    body_budget = budget - overhead
    if body_budget < _MIN_GOAL_TOKENS:
        return empty

    # Per-goal share, split title-first, so one pasted paragraph cannot consume
    # the whole block. Because every goal is rendered inside its own share, the
    # block total is bounded by ``overhead + body_budget`` even when the first
    # goal is force-included below.
    per_goal = max(body_budget // len(capped), 1)
    title_share = max(int(per_goal * 0.6), min(8, per_goal))
    detail_share = max(per_goal - title_share - 2, 0)
    rendered: list[str] = []
    used = 0
    shown = 0
    truncated = False
    shortened = False
    for goal in capped:
        text, shaved = _render_one(
            goal, title_budget=title_share, detail_budget=detail_share
        )
        shortened = shortened or shaved
        if not text:
            truncated = True  # nothing legible fit in this goal's share
            continue
        cost = estimate_tokens(text)
        if rendered and used + cost > body_budget:
            truncated = True
            break
        rendered.append(text)
        used += cost
        shown += 1

    if not rendered:
        return empty

    withheld = len(goals) - shown
    parts = [_HEADER, _INSTRUCTION, "", *rendered]
    if shortened:
        parts.append(
            "\n[Goal text shortened to fit the context budget — "
            "run /goal status <N> for the full wording]"
        )
    if withheld > 0:
        parts.append(
            f"\n[{withheld} further active goal(s) not shown "
            f"({'context budget' if truncated else 'display limit'}) — "
            f"run /goal list to see them]"
        )
    text = "\n".join(parts)
    return {
        "text": text,
        "tokens": estimate_tokens(text),
        "shown": shown,
        "withheld": withheld,
        "truncated": truncated,
        "shortened": shortened,
    }


def active_goals(
    *,
    user_id: str = "default",
    tenant_id: str = "",
    context_id: str = "",
    manager: Any = None,
) -> list:
    """Active goals in play for one operator and session context.

    Includes goals with no ``context_id`` (global to the operator) alongside the
    ones pinned to this conversation — that is what makes a goal *standing*.
    Returns ``[]`` on any failure: the goal store is optional infrastructure.
    """
    try:
        from tools.agent_runtime.standing_goals import GoalManager, GoalStatus

        mgr = manager if manager is not None else GoalManager(
            user_id=user_id, tenant_id=tenant_id
        )
        return mgr.list_for_context(context_id or "", statuses=(GoalStatus.ACTIVE,))
    except Exception as exc:  # noqa: BLE001 — goals are best-effort context
        logger.debug("goal_context: cannot list goals: %s", exc)
        return []


def describe(
    *,
    llm_function: str,
    system_prompt: str = "",
    user_id: str = "default",
    tenant_id: str = "",
    context_id: str = "",
    limit: Optional[int] = None,
    budget: Optional[int] = None,
    manager: Any = None,
) -> dict:
    """Build the block and report how the budget was spent.

    Returns ``{text, budget, limit, total_active, shown, withheld, truncated,
    tokens}``. The accounting is the contract — ``--json`` and the tests both
    read it, so a cap that silently stops applying is visible.
    """
    goals = active_goals(
        user_id=user_id, tenant_id=tenant_id, context_id=context_id, manager=manager
    )
    cap = goal_limit() if limit is None else max(int(limit), 0)
    allowance = (
        budget_tokens(llm_function, system_prompt=system_prompt)
        if budget is None
        else max(int(budget), 0)
    )
    block = render_block(goals, budget=allowance, limit=cap)
    return {
        "text": block["text"],
        "budget": allowance,
        "limit": cap,
        "total_active": len(goals),
        "shown": block["shown"],
        "withheld": block["withheld"],
        "truncated": block["truncated"],
        "shortened": block["shortened"],
        "tokens": block["tokens"],
    }


def build_for_runtime(
    llm_function: str,
    system_prompt: str = "",
    *,
    user_id: str = "default",
    tenant_id: str = "",
    context_id: str = "",
) -> str:
    """Entry point the runtime calls each time its goal cache is cold.

    Never raises. ``ICDEV_SAG_GOALS=0`` disables the block entirely.
    """
    if not _env_flag(ENV_DISABLE):
        return ""
    try:
        return describe(
            llm_function=llm_function,
            system_prompt=system_prompt,
            user_id=user_id,
            tenant_id=tenant_id,
            context_id=context_id,
        )["text"]
    except Exception as exc:  # noqa: BLE001 — injection is best-effort
        logger.debug("goal_context: injection skipped: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        description="Show the standing-goal block the SAG runtime injects."
    )
    ap.add_argument("--function", default="code_generation",
                    help="Routing function to budget against.")
    ap.add_argument("--user", default="default", help="Operator user id.")
    ap.add_argument("--tenant", default="", help="Tenant id.")
    ap.add_argument("--context", default="", help="Session context id.")
    ap.add_argument("--limit", type=int, default=None, help="Override the count cap.")
    ap.add_argument("--json", action="store_true", help="Emit the budget accounting.")
    args = ap.parse_args(argv)

    report = describe(
        llm_function=args.function,
        user_id=args.user,
        tenant_id=args.tenant,
        context_id=args.context,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps({k: v for k, v in report.items() if k != "text"}, indent=2))
    else:
        print(report["text"] or "(no active goals)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
