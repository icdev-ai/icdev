#!/usr/bin/env python3
# CUI // SP-CTI
"""Would ``enforce`` refuse routine work for this MCP client? (owui-mcp-02)

``MCPToolAuthorizer`` on the SaaS HTTP surface defaults to ``monitor``: it
records what it WOULD deny and lets the call through. Arming it for a chat
client (Open WebUI) without measuring that first is the mistake the PreToolUse
fire-rate rule exists to prevent -- a check armed blind either refuses routine
work or gets switched off.

This reads the per-call ``mcp.tool.call`` rows ``tools/saas/mcp_http.py`` writes
to ``audit_platform`` (tagged by the ``X-ICDEV-Client`` header) and reports, for
one caller over a window: calls, allowed, would-deny, denied, the would-deny
rate and which tools drove it. JSON is parsed in Python, not in SQL, so the
query is the same on PostgreSQL and SQLite.

Exit codes: 0 measured; 2 UNMEASURABLE (no calls in the window, or the table
could not be read) -- an empty history is not evidence that enforce is safe.

Usage:
  python tools/saas/mcp_authz_survey.py --caller openwebui --days 14 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# kax-conflict-04: run by path, sys.path[0] is this file's own directory -- bootstrap
# the import root before the first first-party import.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.saas.mcp_authz_survey")

CALL_ACTION = "mcp.tool.call"


def fetch_rows(since_iso: str, get_conn=None) -> List[Dict[str, Any]]:
    if get_conn is None:
        from tools.db.storage import get_connection as get_conn
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT details, recorded_at FROM audit_platform "
            "WHERE event_type = %s AND action = %s AND recorded_at >= %s",
            ("mcp.authz", CALL_ACTION, since_iso),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def summarize(rows: Iterable[Dict[str, Any]], caller: str) -> Dict[str, Any]:
    calls = allowed = would_deny = denied = 0
    by_tool: Counter = Counter()
    for row in rows:
        raw = row.get("details")
        try:
            d = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except ValueError:
            continue
        if d.get("caller_source", "unknown") != caller:
            continue
        calls += 1
        if d.get("allowed"):
            allowed += 1
        elif d.get("enforced"):
            denied += 1
        else:
            would_deny += 1
            by_tool[d.get("tool") or "?"] += 1
    return {
        "caller": caller,
        "calls": calls,
        "allowed": allowed,
        "would_deny": would_deny,
        "denied": denied,
        # None, never 0.0, over an empty denominator (rem-hyg-13).
        "would_deny_rate": round(would_deny / calls, 4) if calls else None,
        "would_deny_by_tool": dict(by_tool.most_common()),
        "measurable": calls > 0,
    }


def main(argv: Optional[List[str]] = None, get_conn=None) -> int:
    ap = argparse.ArgumentParser(description="Measure MCP authorizer would-deny rate for one client.")
    ap.add_argument("--caller", default="openwebui", help="X-ICDEV-Client value to survey")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
    try:
        report = summarize(fetch_rows(since, get_conn), args.caller)
    except Exception as exc:  # noqa: BLE001 -- unreadable is its own verdict
        report = {"caller": args.caller, "measurable": False, "error": str(exc)}
    report["window_days"] = args.days

    if args.json:
        print(json.dumps(report, indent=2))
    elif report.get("measurable"):
        print(f"{args.caller}: {report['calls']} calls in {args.days}d -- would-deny "
              f"{report['would_deny']} ({report['would_deny_rate']:.2%}), denied {report['denied']}")
        for tool, n in report["would_deny_by_tool"].items():
            print(f"  {tool}: {n}")
    else:
        print(f"{args.caller}: UNMEASURABLE -- {report.get('error') or 'no calls in the window'}")
    return 0 if report.get("measurable") else 2


if __name__ == "__main__":
    sys.exit(main())
