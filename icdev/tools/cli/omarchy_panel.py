#!/usr/bin/env python3
# CUI // SP-CTI
"""The ICDEV record for Omarchy's agents panel (omx-panel-01).

Omarchy's agents panel draws one JSON record per agent from
``~/.local/state/omarchy/agents/usage/<id>.json`` and "picks up any record that
lands in the directory regardless of who wrote it" (omarchy
``shell/plugins/agents/README.md``). The contract is the one its own
``omarchy-agent-usage-<agent>`` collectors print -- ``schemaVersion``, ``id``,
``name``, ``updatedAt``, ``ready``, ``tierLabel``, ``usageStatusText``,
``authHelpText``, ``limits`` and the local-stats block -- copied here from the
upstream ``fireworks`` collector, the one that, like ICDEV, has no rate-limit
windows. The panel shows a record once ``ready`` is true and ``tierLabel`` is
non-empty.

WHAT IT REPORTS, AND WHERE EACH FIGURE COMES FROM -- nothing is recomputed:

* kanban in-progress / backlog counts -- ``tools.mcp.kanban_server``'s board
  summary, the query the ``kanban_board_summary`` MCP tool answers.
* today's LLM spend -- ``tools.cost.task_attribution.attributed_totals`` over
  the window since local midnight: the same ``agent_token_usage`` seam the
  ``/cache-savings`` spend panel and ``session_cost --by-verdict`` read, with
  its unpriced-is-not-zero rule (a task with no priced row has NO cost).
* the active adapter -- ``tools.agents.registry.pick_default()`` (which follows
  ``omarchy default agent``), and whether its guard is wired --
  ``tools.agents.guard_parity.verdict_for()``, MEASURED, not declared.

UNREACHABLE IS NOT ZERO. When the board or the ledger cannot be read the
record says ``stale: true``, its counts are ``null`` and ``usageStatusText``
names the outage. A ``0 in progress`` the database never said would read as an
idle board. (The contract's local-stats block -- prompts/sessions/tokens -- is
carried with ``hasLocalStats: false``, the upstream way of saying "this agent
has no such stats", exactly as the fireworks collector does without a key.)

Omarchy's ``omarchy-agent-usage-update`` only runs collectors from
``$OMARCHY_PATH/bin``, so ``icdev omarchy setup`` installs this one into
``~/.local/bin`` plus a ``systemd --user`` timer that runs it with ``--write``.

Usage:
    omarchy-agent-usage-icdev                 # print the record (the contract)
    omarchy-agent-usage-icdev --write         # also write it where the panel reads
    python -m tools.cli.omarchy_panel --json  # same, from a checkout
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

AGENT_ID = "icdev"
AGENT_NAME = "ICDEV"
SCHEMA_VERSION = 1
STALE_TEXT = "ICDEV database unreachable"
AUTH_HELP_STALE = ("The kanban board or the LLM spend ledger could not be read. "
                   "Check that PostgreSQL is running and ICDEV_DATABASE_URL in .env.")

#: The keys every upstream record carries. Pinned by the tests.
CONTRACT_KEYS = (
    "schemaVersion", "id", "name", "updatedAt", "ready", "hasLocalStats", "scope",
    "hasPromptStats", "tierLabel", "usageStatusText", "authHelpText", "limits",
    "todayPrompts", "todaySessions", "todayTotalTokens", "todayTokensByModel",
    "recentDays", "totalPrompts", "totalSessions", "activeDays", "activeDates", "modelUsage",
)


def usage_dir(environ: Optional[Mapping[str, str]] = None, home: Optional[Path] = None) -> Path:
    """``$XDG_STATE_HOME/omarchy/agents/usage`` -- where omarchy-agent-usage-update writes."""
    env = os.environ if environ is None else environ
    state = env.get("XDG_STATE_HOME") or str((home or Path.home()) / ".local" / "state")
    return Path(state) / "omarchy" / "agents" / "usage"


# --------------------------------------------------------------------------- #
# The three sources -- each returns None when it could not be read.
# --------------------------------------------------------------------------- #

def kanban_counts(summary_fn: Optional[Callable[[dict], dict]] = None) -> Optional[Dict[str, int]]:
    """``{in_progress, backlog, total}`` from the board summary; None when unreadable."""
    if summary_fn is None:
        from icdev.tools.mcp.kanban_server import handle_kanban_board_summary as summary_fn
    try:
        summary = summary_fn({})
    except Exception:  # noqa: BLE001 -- unreadable is None, never zeros
        return None
    if not isinstance(summary, dict) or summary.get("error"):
        return None
    lanes = summary.get("lanes") or {}
    return {"in_progress": int(lanes.get("in_progress", 0)),
            "backlog": int(lanes.get("backlog", 0)),
            "total": int(summary.get("total", 0))}


def _since_local_midnight(now: datetime) -> float:
    local = now.astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return max((local - midnight).total_seconds(), 1.0) / 86400.0


def today_spend(now: datetime,
                totals_fn: Optional[Callable[..., Optional[Dict[str, Dict[str, Any]]]]] = None
                ) -> Optional[Dict[str, Any]]:
    """Attributed spend since local midnight; None when the ledger is unreadable.

    ``cost_usd`` is None when no dispatch today carried a price -- whether there
    were none or all were unpriced (``dispatches`` tells the two apart).
    """
    if totals_fn is None:
        from icdev.tools.cost.task_attribution import attributed_totals as totals_fn
    try:
        totals = totals_fn(window_days=_since_local_midnight(now))
    except Exception:  # noqa: BLE001
        return None
    if totals is None:
        return None
    buckets = list(totals.values())
    priced = [b["cost_usd"] for b in buckets if b.get("cost_usd") is not None]
    return {
        "cost_usd": round(sum(priced), 4) if priced else None,
        "tasks": len(buckets),
        "dispatches": sum(int(b.get("rows") or 0) for b in buckets),
        "unpriced_dispatches": sum(int(b.get("unpriced_rows") or 0) for b in buckets),
        "tokens": sum(int(b.get("input_tokens") or 0) + int(b.get("output_tokens") or 0)
                      for b in buckets),
    }


def adapter_status(pick_fn: Optional[Callable[[], Any]] = None,
                   verdict_fn: Optional[Callable[[str], Any]] = None) -> Dict[str, Any]:
    """The adapter autonomous dispatch would pick, and its MEASURED guard verdict."""
    if pick_fn is None:
        from icdev.tools.agents.registry import pick_default as pick_fn
    if verdict_fn is None:
        from icdev.tools.agents.guard_parity import verdict_for as verdict_fn
    try:
        adapter = pick_fn()
        name = str(getattr(adapter, "name", "") or adapter)
    except Exception as exc:  # noqa: BLE001 -- NotInstalledError and friends
        return {"name": None, "guard_wired": None, "reason": f"{type(exc).__name__}: {exc}"}
    try:
        verdict = verdict_fn(name)
        return {"name": name, "guard_wired": bool(verdict.guarded), "reason": str(verdict.reason)}
    except Exception as exc:  # noqa: BLE001 -- an unprobed guard is not a wired one
        return {"name": name, "guard_wired": None, "reason": f"guard probe failed: {exc}"}


# --------------------------------------------------------------------------- #
# The record -- pure.
# --------------------------------------------------------------------------- #

def _tier_label(adapter: Dict[str, Any]) -> str:
    if not adapter.get("name"):
        return "no harness"
    guard = {True: "guard wired", False: "guard NOT wired"}.get(adapter.get("guard_wired"),
                                                                "guard unverified")
    return f"{adapter['name']} · {guard}"


def _status_text(kanban: Optional[Dict[str, int]], spend: Optional[Dict[str, Any]]) -> str:
    if kanban is None or spend is None:
        return STALE_TEXT
    if spend["cost_usd"] is not None:
        money = f"${spend['cost_usd']:.2f} today"
        if spend["unpriced_dispatches"]:
            money += f" (+{spend['unpriced_dispatches']} unpriced)"
    elif spend["dispatches"]:
        money = f"{spend['dispatches']} unpriced dispatch(es) today"
    else:
        money = "no attributed spend today"
    return f"{kanban['in_progress']} in progress · {kanban['backlog']} backlog · {money}"


def build_record(*, kanban: Optional[Dict[str, int]], spend: Optional[Dict[str, Any]],
                 adapter: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """The contract-shaped record. ``stale`` is true when any DB figure is missing."""
    stale = kanban is None or spend is None
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": AGENT_ID,
        "name": AGENT_NAME,
        "updatedAt": now.astimezone(timezone.utc).isoformat(),
        # ICDEV is installed and ran: the panel shows the record, stale or not,
        # so an outage has somewhere to say so.
        "ready": True,
        "hasLocalStats": False,
        "scope": "machine",
        "hasPromptStats": False,
        "tierLabel": _tier_label(adapter),
        "usageStatusText": _status_text(kanban, spend),
        "authHelpText": AUTH_HELP_STALE if stale else "",
        "limits": [],
        "stale": stale,
        "limitsStale": stale,
        # The upstream local-stats block, empty: hasLocalStats=false says so.
        "todayPrompts": 0, "todaySessions": 0, "todayTotalTokens": 0,
        "todayTokensByModel": {}, "recentDays": [], "totalPrompts": 0,
        "totalSessions": 0, "activeDays": 0, "activeDates": [], "modelUsage": {},
        # ICDEV's own figures. null = could not be read, never 0.
        "icdev": {
            "kanban": kanban or {"in_progress": None, "backlog": None, "total": None},
            "spendToday": spend or {"cost_usd": None, "tasks": None, "dispatches": None,
                                    "unpriced_dispatches": None, "tokens": None},
            "adapter": adapter,
        },
    }


def collect(now: Optional[datetime] = None, *, summary_fn=None, totals_fn=None,
            pick_fn=None, verdict_fn=None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    return build_record(kanban=kanban_counts(summary_fn),
                        spend=today_spend(now, totals_fn),
                        adapter=adapter_status(pick_fn, verdict_fn), now=now)


def write_record(record: Dict[str, Any], directory: Path) -> Path:
    """Atomic write of ``<id>.json`` -- the panel watches the file, so never half-written."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{record['id']}.json"
    fd, tmp = tempfile.mkstemp(prefix=f".{record['id']}.", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="omarchy-agent-usage-icdev",
                                 description="Print the ICDEV agents-panel record as JSON")
    # Accepted so every collector takes the same invocation (upstream contract);
    # there is no cache to force past and no faster limits-only path.
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limits-only", action="store_true")
    ap.add_argument("--write", action="store_true",
                    help="also write the record into the Omarchy usage directory")
    ap.add_argument("--usage-dir", type=Path, default=None)
    ap.add_argument("--json", action="store_true", help="(default) print the record")
    args = ap.parse_args(argv)

    record = collect()
    print(json.dumps(record, separators=(",", ":")))
    if args.write:
        write_record(record, args.usage_dir or usage_dir())
    return 0


if __name__ == "__main__":
    sys.exit(main())
