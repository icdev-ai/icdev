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
windows.

WHEN THE PANEL SHOWS IT. ``ready`` and ``tierLabel`` are NOT enough (omx-panel-04,
measured on Omarchy 4): ``shell/plugins/agents/Main.qml`` admits a record only
when ``providerHasData`` holds -- ``totalPrompts``, ``totalSessions``,
``activeDays``, ``todayPrompts`` or ``todaySessions`` above zero, or a non-empty
``limits``, or a ``balance``. ICDEV has no limits and no balance and invents
neither, so its tab appears once this machine has DISPATCHED at least once. A
machine that never has stays hidden: that is Omarchy's design, not a bug.

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
* the contract's local-stats block -- ``tools.cost.task_attribution.attributed_rows``,
  the same ledger, all time. A SESSION is one kanban dispatch (one attributed
  row, written at reap); ``todaySessions``/``totalSessions``/``activeDays``/
  ``activeDates``/``recentDays``/``todayTotalTokens``/``todayTokensByModel``/
  ``modelUsage`` are shaped like the upstream ``claude``/``codex`` collectors'
  (``recentDays`` is seven ``{date, messageCount}`` entries, oldest first, and
  ``messageCount`` is a TOKEN total there too). The ledger holds one row per
  dispatch, not one per LLM call, so prompts stay 0 with ``hasPromptStats:
  false``; its ``input_tokens`` already includes cache reads and writes (no
  split), so the cache fields of a ``modelUsage`` bucket are 0.

UNREACHABLE IS NOT ZERO. When the board or the ledger cannot be read the
record says ``stale: true``, its counts are ``null`` and ``usageStatusText``
names the outage. A ``0 in progress`` the database never said would read as an
idle board. An unreadable ledger likewise nulls the local-stats block with
``hasLocalStats: false``; an EMPTY ledger is real zeros with ``hasLocalStats:
true``.

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
from datetime import datetime, timedelta, timezone
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


def _row_local_date(created_at: Any, tz) -> Optional[str]:
    """``YYYY-MM-DD`` in *tz* for a ledger timestamp; naive means UTC (both spellings)."""
    if isinstance(created_at, datetime):
        stamp = created_at
    else:
        try:
            stamp = datetime.fromisoformat(str(created_at or "").strip().replace(" ", "T", 1))
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(tz).strftime("%Y-%m-%d")


def _empty_bucket() -> Dict[str, int]:
    return {"inputTokens": 0, "outputTokens": 0,
            "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}


def dispatch_history(now: datetime,
                     rows_fn: Optional[Callable[[], Optional[list]]] = None,
                     tz=None) -> Optional[Dict[str, Any]]:
    """The upstream local-stats block from every reaped dispatch; None when unreadable.

    One attributed ``agent_token_usage`` row is one dispatch -- one session.
    *tz* is the zone "today" is counted in: the machine's local zone by default,
    as the upstream collectors count it.
    """
    if rows_fn is None:
        from icdev.tools.cost.task_attribution import attributed_rows as rows_fn
    try:
        rows = rows_fn()
    except Exception:  # noqa: BLE001 -- unreadable is None, never zeros
        return None
    if rows is None:
        return None
    local_now = now.astimezone(tz) if tz is not None else now.astimezone()
    tz = local_now.tzinfo
    today = local_now.strftime("%Y-%m-%d")
    recent_dates = [(local_now - timedelta(days=offset)).strftime("%Y-%m-%d")
                    for offset in range(6, -1, -1)]
    recent = {day: {"date": day, "messageCount": 0} for day in recent_dates}
    model_usage: Dict[str, Dict[str, int]] = {}
    today_tokens: Dict[str, int] = {}
    active_days = set()
    sessions = today_sessions = 0
    for row in rows:
        day = _row_local_date(row.get("created_at"), tz)
        if day is None:
            continue
        inp, out = int(row.get("input_tokens") or 0), int(row.get("output_tokens") or 0)
        model = str(row.get("model_id") or "").strip() or "unknown"
        sessions += 1
        active_days.add(day)
        bucket = model_usage.setdefault(model, _empty_bucket())
        bucket["inputTokens"] += inp
        bucket["outputTokens"] += out
        if day in recent:
            # A token total despite the name -- upstream's legacy key, kept.
            recent[day]["messageCount"] += inp + out
        if day == today:
            today_sessions += 1
            today_tokens[model] = today_tokens.get(model, 0) + inp + out
    return {
        "todayPrompts": 0, "todaySessions": today_sessions,
        "todayTotalTokens": sum(today_tokens.values()),
        "todayTokensByModel": today_tokens,
        "recentDays": [recent[day] for day in recent_dates],
        "totalPrompts": 0, "totalSessions": sessions,
        "activeDays": len(active_days), "activeDates": sorted(active_days),
        "modelUsage": model_usage,
    }


#: The local-stats block when the ledger could not be read: null, never 0.
UNREADABLE_LOCAL_STATS = {
    "todayPrompts": None, "todaySessions": None, "todayTotalTokens": None,
    "todayTokensByModel": None, "recentDays": None, "totalPrompts": None,
    "totalSessions": None, "activeDays": None, "activeDates": None, "modelUsage": None,
}


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
                 adapter: Dict[str, Any], now: datetime,
                 history: Optional[Dict[str, Any]] = None,
                 exhaustion: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The contract-shaped record. ``stale`` is true when any DB figure is missing."""
    stale = kanban is None or spend is None or history is None
    record = {
        "schemaVersion": SCHEMA_VERSION,
        "id": AGENT_ID,
        "name": AGENT_NAME,
        "updatedAt": now.astimezone(timezone.utc).isoformat(),
        # ICDEV is installed and ran: the panel shows the record, stale or not,
        # so an outage has somewhere to say so.
        "ready": True,
        "hasLocalStats": history is not None,
        "scope": "machine",
        "hasPromptStats": False,
        "tierLabel": _tier_label(adapter),
        "usageStatusText": STALE_TEXT if stale else _status_text(kanban, spend),
        "authHelpText": AUTH_HELP_STALE if stale else "",
        "limits": [],
        "stale": stale,
        "limitsStale": stale,
        # ICDEV's own figures. null = could not be read, never 0.
        "icdev": {
            "kanban": kanban or {"in_progress": None, "backlog": None, "total": None},
            "spendToday": spend or {"cost_usd": None, "tasks": None, "dispatches": None,
                                    "unpriced_dispatches": None, "tokens": None},
            "adapter": adapter,
            # omx-adapt-06: provider families exhausted now, and their resume
            # times. null = could not be read.
            "providerExhaustion": exhaustion,
        },
    }
    # The upstream local-stats block -- what the panel's providerHasData reads.
    record.update(history if history is not None else UNREADABLE_LOCAL_STATS)
    return record


def provider_exhaustion(snapshot_fn: Optional[Callable[[], Dict[str, Any]]] = None
                        ) -> Optional[Dict[str, Any]]:
    """omx-adapt-06 state per provider family; None when it cannot be read."""
    if snapshot_fn is None:
        from icdev.tools.llm.provider_exhaustion import snapshot as snapshot_fn
    try:
        return snapshot_fn()
    except Exception:  # noqa: BLE001 -- null, never a guess
        return None


def collect(now: Optional[datetime] = None, *, summary_fn=None, totals_fn=None,
            pick_fn=None, verdict_fn=None, rows_fn=None, tz=None,
            exhaustion_fn=None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    return build_record(kanban=kanban_counts(summary_fn),
                        spend=today_spend(now, totals_fn),
                        adapter=adapter_status(pick_fn, verdict_fn), now=now,
                        history=dispatch_history(now, rows_fn, tz),
                        exhaustion=provider_exhaustion(exhaustion_fn))


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
