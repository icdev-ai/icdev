#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Unified Platform Connector CLI — single-entry internet access layer for AI agents.

Consolidates fragmented per-engine platform fetches (GitHub, HackerNews,
StackOverflow, Reddit, YouTube) into a shared adapter registry with
multi-backend routing, zero-config APIs, and health monitoring.

Usage:
    python tools/platform_connectors/connector_cli.py --fetch github "machine learning" --json
    python tools/platform_connectors/connector_cli.py --fetch-all "kubernetes operators" --json
    python tools/platform_connectors/connector_cli.py --doctor --json
    python tools/platform_connectors/connector_cli.py --list-platforms --json
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.db.storage import get_connection
    _HAS_DB = True
except ImportError:
    _HAS_DB = False

try:
    from tools.audit.audit_logger import log_event as _audit_log
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def _audit_log(**kwargs):
        return -1


from tools.platform_connectors.connector_registry import get_registry
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.platform_connectors.connector_cli")


# =========================================================================
# HELPERS
# =========================================================================

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit(action: str, details: dict | None = None) -> None:
    if _HAS_AUDIT:
        try:
            _audit_log(
                event_type="platform_connector",
                actor="connector_cli",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="platform-connectors",
            )
        except Exception:
            pass


def _get_db():
    if not _HAS_DB:
        return None
    try:
        db_path = BASE_DIR / "data" / "icdev.db"
        conn = get_connection(db_path=str(db_path))
        return conn
    except Exception:
        return None


def _ensure_schema(conn) -> None:
    """Create platform_connector_fetches table if not present."""
    if conn is None:
        return
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS platform_connector_fetches (
                id          TEXT PRIMARY KEY,
                platform    TEXT NOT NULL,
                query       TEXT NOT NULL,
                adapter     TEXT,
                item_count  INTEGER DEFAULT 0,
                error       TEXT,
                fetched_at  TEXT NOT NULL,
                metadata    TEXT
            )
        """)
        conn.commit()
    except Exception:
        pass


def _persist_result(conn, result) -> None:
    """Append fetch result to platform_connector_fetches (audit trail)."""
    if conn is None:
        return
    try:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO platform_connector_fetches
                (id, platform, query, adapter, item_count, error, fetched_at, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                f"pcf-{uuid.uuid4().hex[:12]}",
                result.platform,
                result.query,
                result.adapter_name,
                result.total,
                result.error,
                result.fetched_at,
                json.dumps(result.metadata) if result.metadata else None,
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_persist_result: best-effort INSERT into platform_connector_fetches failed (non-blocking): %s",
            exc,
        )


# =========================================================================
# COMMANDS
# =========================================================================

def cmd_fetch(args: argparse.Namespace) -> dict:
    """Fetch from a single platform."""
    registry = get_registry()
    kwargs: dict = {}
    if args.max_results:
        kwargs["max_results"] = args.max_results
    if args.since_days:
        kwargs["since_days"] = args.since_days

    result = registry.fetch(args.platform, args.query, **kwargs)

    conn = _get_db()
    _persist_result(conn, result)
    if conn:
        conn.close()

    _audit("fetch", {"platform": args.platform, "query": args.query,
                     "item_count": result.total, "ok": result.ok})
    return result.to_dict()


def cmd_fetch_all(args: argparse.Namespace) -> dict:
    """Fetch from all platforms."""
    registry = get_registry()
    kwargs: dict = {}
    if args.max_results:
        kwargs["max_results"] = args.max_results
    if args.since_days:
        kwargs["since_days"] = args.since_days

    results = registry.fetch_all(args.query, **kwargs)

    conn = _get_db()
    for r in results.values():
        _persist_result(conn, r)
    if conn:
        conn.close()

    total_items = sum(r.total for r in results.values())
    _audit("fetch_all", {"query": args.query, "platforms": len(results),
                         "total_items": total_items})
    return {
        "query": args.query,
        "fetched_at": _now(),
        "platforms": {p: r.to_dict() for p, r in results.items()},
        "summary": {
            "total_platforms": len(results),
            "ok_platforms": sum(1 for r in results.values() if r.ok),
            "total_items": total_items,
        },
    }


def cmd_doctor(args: argparse.Namespace) -> dict:
    """Run health probes on all adapters."""
    registry = get_registry()
    report = registry.doctor()

    _audit("doctor", {"platforms": list(report.keys())})

    platforms_out = {}
    for platform, health_results in report.items():
        platforms_out[platform] = [h.to_dict() for h in health_results]

    all_statuses = [h.status for results in report.values() for h in results]
    return {
        "checked_at": _now(),
        "platforms": platforms_out,
        "summary": {
            "total_adapters": len(all_statuses),
            "ok": sum(1 for s in all_statuses if s == "ok"),
            "degraded": sum(1 for s in all_statuses if s == "degraded"),
            "unreachable": sum(1 for s in all_statuses if s == "unreachable"),
            "auth_error": sum(1 for s in all_statuses if s == "auth_error"),
        },
    }


def cmd_list_platforms(args: argparse.Namespace) -> dict:
    """List all registered platforms and their adapters."""
    registry = get_registry()
    platforms = registry.platforms()
    result = {
        "platforms": [],
        "total": len(platforms),
        "listed_at": _now(),
    }
    for p in platforms:
        adapters = registry._adapters.get(p, [])
        result["platforms"].append({
            "platform": p,
            "adapters": [
                {"name": a.name, "priority": a.priority} for a in adapters
            ],
        })
    return result


# =========================================================================
# MAIN
# =========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified Platform Connector — single-entry internet access for AI agents"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    sub = parser.add_subparsers(dest="command")

    fetch_p = sub.add_parser("fetch", help="Fetch from a single platform")
    fetch_p.add_argument("platform", help="Platform name (github, hackernews, stackoverflow, reddit, youtube)")
    fetch_p.add_argument("query", help="Search query")
    fetch_p.add_argument("--max-results", type=int, dest="max_results")
    fetch_p.add_argument("--since-days", type=int, dest="since_days")

    fetch_all_p = sub.add_parser("fetch-all", help="Fetch from all platforms")
    fetch_all_p.add_argument("query", help="Search query")
    fetch_all_p.add_argument("--max-results", type=int, dest="max_results")
    fetch_all_p.add_argument("--since-days", type=int, dest="since_days")

    sub.add_parser("doctor", help="Health check all adapters")
    sub.add_parser("list-platforms", help="List registered platforms")

    # Legacy --flag style for backward compat
    parser.add_argument("--fetch", nargs=2, metavar=("PLATFORM", "QUERY"))
    parser.add_argument("--fetch-all", dest="fetch_all_query", metavar="QUERY")
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--list-platforms", dest="list_platforms", action="store_true")
    parser.add_argument("--max-results", type=int, dest="max_results")
    parser.add_argument("--since-days", type=int, dest="since_days")

    args = parser.parse_args()

    # Resolve legacy flags to subcommand-style args
    if args.fetch:
        args.command = "fetch"
        args.platform = args.fetch[0]
        args.query = args.fetch[1]
    elif args.fetch_all_query:
        args.command = "fetch-all"
        args.query = args.fetch_all_query
    elif args.doctor:
        args.command = "doctor"
    elif args.list_platforms:
        args.command = "list-platforms"

    if not args.command:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "fetch": cmd_fetch,
        "fetch-all": cmd_fetch_all,
        "doctor": cmd_doctor,
        "list-platforms": cmd_list_platforms,
    }

    fn = dispatch.get(args.command)
    if fn is None:
        parser.print_help()
        sys.exit(1)

    try:
        result = fn(args)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if "items" in result:
                for item in result["items"]:
                    print(f"  [{item.get('platform','?')}] {item.get('title','')}")
                    if item.get("url"):
                        print(f"    {item['url']}")
            elif "platforms" in result and "summary" in result:
                summary = result.get("summary", {})
                print(f"Fetched from {summary.get('total_platforms',0)} platforms, "
                      f"{summary.get('total_items',0)} total items")
            else:
                print(json.dumps(result, indent=2, default=str))
    except Exception as exc:
        out = {"error": str(exc), "command": args.command}
        if args.json:
            print(json.dumps(out))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
