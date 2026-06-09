#!/usr/bin/env python3
# CUI // SP-CTI
"""Runtime-backed deletion evidence for dead-code findings (CodeLens CL-4).

The static dead-code lens (tools/code_intelligence/dead_code.py, CL-1+CL-2)
flags candidates by name/graph reachability alone, which is false-positive
prone: a route handler reached only via the web framework, or a function
reached by dynamic dispatch, looks "dead" but is not. This module is the
ICDEV analog of Fallow Runtime's "cold-path deletion evidence" — but built
entirely from runtime data ICDEV already persists, air-gap safe, no new
instrumentation.

It correlates each static finding's file with real runtime traffic:

    usage_events (per-route Flask hits, written by the dashboard's
    before/after_request hooks) ->  the @bp.route patterns a file defines

Evidence verdicts
-----------------
    runtime_hot   - the file's routes were hit within the window
    runtime_cold  - the file defines routes but had ZERO hits in the window
    no_signal     - the file serves no routes (ICDEV has no per-function call
                    log, so plain helpers cannot be confirmed at runtime)

Recommendation per finding fuses the static `confidence` with the runtime
signal. Crucially, it NEVER upgrades a `dead_function`/`dead_class` to "delete"
on the strength of file-level traffic — runtime hits prove the file is live,
not that a specific symbol inside it is dead, so those are flagged "review".

Usage
-----
    python tools/code_intelligence/deletion_evidence.py --json
    python tools/code_intelligence/deletion_evidence.py --project-dir tools/ --window-days 30 --human --explain
    python tools/code_intelligence/deletion_evidence.py --human   # summary + verdicts
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.code_intelligence.dead_code import run_scan  # noqa: E402

DEFAULT_WINDOW_DAYS = 30

# Kinds for which web-route traffic is a meaningful liveness signal.
_ROUTE_RELEVANT_KINDS = {"orphan_file", "dead_function", "dead_class"}

# Matches @bp.route("..."), @app.route('...'), @blueprint.route("..."), etc.
_ROUTE_DECORATOR_RE = re.compile(r"\.route\(\s*['\"]([^'\"]+)['\"]")
# Matches app.add_url_rule("...")
_ADD_URL_RULE_RE = re.compile(r"add_url_rule\(\s*['\"]([^'\"]+)['\"]")


# ---------------------------------------------------------------------------
# Route pattern handling
# ---------------------------------------------------------------------------


def extract_route_patterns(source: str) -> List[str]:
    """Return the Flask route patterns a source file declares (sorted/unique)."""
    found = set(_ROUTE_DECORATOR_RE.findall(source))
    found |= set(_ADD_URL_RULE_RE.findall(source))
    return sorted(found)


def static_prefix(pattern: str) -> str:
    """Literal prefix of a route pattern up to the first `<converter>`."""
    idx = pattern.find("<")
    return pattern if idx == -1 else pattern[:idx]


def pattern_to_regex(pattern: str) -> "re.Pattern[str]":
    """Compile a Flask route pattern into a concrete-path matcher.

    `<path:x>` matches across slashes (.+); any other `<...>` matches a single
    segment ([^/]+). A trailing slash is tolerated either way.
    """
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "<":
            end = pattern.find(">", i)
            if end == -1:
                out.append(re.escape(pattern[i:]))
                break
            token = pattern[i + 1:end]
            out.append(".+" if token.startswith("path:") else "[^/]+")
            i = end + 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("^" + "".join(out) + "/?$")


# ---------------------------------------------------------------------------
# Runtime traffic lookup
# ---------------------------------------------------------------------------


def load_route_traffic(conn: Any, cutoff_iso: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """Aggregate ALL route hits within the window in ONE grouped scan.

    Returns {route: {"n": hit_count, "last": last_iso}} (<= number of distinct
    routes, typically a few thousand), or None if the source is unavailable
    (table missing / RLS / backend error) so callers can degrade to no_signal.

    A single scan is critical: usage_events has millions of rows, so per-file
    or per-prefix queries are prohibitively slow. We pull the small grouped
    result once and match patterns against it in Python.
    """
    if conn is None:
        return None
    try:
        cur = conn.execute(
            "SELECT route, COUNT(*) AS n, MAX(occurred_at) AS last_hit "
            "FROM usage_events WHERE occurred_at >= ? GROUP BY route",
            (cutoff_iso,),
        )
        rows = cur.fetchall()
    except Exception:
        return None

    traffic: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, (tuple, list)):
            route, n, last = row[0], row[1], row[2]
        else:
            route, n, last = row["route"], row["n"], row["last_hit"]
        if route is not None:
            traffic[route] = {"n": n, "last": last}
    return traffic


def match_traffic(
    patterns: List[str],
    traffic: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Match a file's route patterns against the preloaded traffic map (Python).

    Returns {hit_count, last_hit, matched_routes, routes_defined}.
    """
    regexes = [pattern_to_regex(p) for p in patterns]
    matched: Dict[str, Dict[str, Any]] = {}
    for route, info in traffic.items():
        if any(rx.match(route) for rx in regexes):
            matched[route] = info
    total = sum(m["n"] for m in matched.values())
    last_hit = max((m["last"] for m in matched.values() if m["last"]), default=None)
    return {
        "hit_count": total,
        "last_hit": last_hit,
        "matched_routes": sorted(matched)[:20],
        "routes_defined": len(patterns),
    }


def _days_since(iso_ts: Optional[str], now: datetime) -> Optional[int]:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (now - dt).days)
    except ValueError:
        return None


def runtime_evidence_for_file(
    file_rel: str,
    base: Path,
    traffic: Optional[Dict[str, Dict[str, Any]]],
    now: datetime,
) -> Dict[str, Any]:
    """Compute the runtime signal for a single source file.

    `traffic` is the preloaded route->hits map from load_route_traffic(), or
    None when runtime data is unavailable (degrade to no_signal).
    """
    abs_path = (base / file_rel)
    try:
        source = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"signal": "no_signal", "reason": "file unreadable"}

    patterns = extract_route_patterns(source)
    if not patterns:
        return {"signal": "no_signal", "reason": "not a route handler",
                "routes_defined": 0}
    if traffic is None:
        return {"signal": "no_signal", "reason": "runtime data unavailable",
                "routes_defined": len(patterns)}

    t = match_traffic(patterns, traffic)
    if t["hit_count"] > 0:
        return {
            "signal": "runtime_hot",
            "routes_defined": t["routes_defined"],
            "hit_count": t["hit_count"],
            "last_hit": t["last_hit"],
            "days_since_last_hit": _days_since(t["last_hit"], now),
            "matched_routes": t["matched_routes"],
        }
    return {
        "signal": "runtime_cold",
        "routes_defined": t["routes_defined"],
        "hit_count": 0,
        "last_hit": None,
        "matched_routes": [],
    }


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def verdict_for(finding: Dict[str, Any], ev: Dict[str, Any], window_days: int) -> Dict[str, Any]:
    """Fuse static confidence with runtime signal into a recommendation.

    recommendation in {keep, delete, review}. Never escalates a dead-symbol
    finding to "delete" on file-level traffic alone.
    """
    kind = finding["kind"]
    signal = ev.get("signal", "no_signal")
    base_conf = finding.get("confidence", "medium")

    if kind == "orphan_file":
        if signal == "runtime_hot":
            return {
                "recommendation": "keep",
                "confidence": "low",
                "rationale": (
                    f"File serves {ev.get('routes_defined', 0)} route(s) hit "
                    f"{ev.get('hit_count')} time(s) in the last {window_days}d "
                    f"(last {ev.get('days_since_last_hit')}d ago). It is reached "
                    f"via the web framework, not the import graph — static "
                    f"orphan flag is a false positive."
                ),
            }
        if signal == "runtime_cold":
            return {
                "recommendation": "delete",
                "confidence": "high",
                "rationale": (
                    f"File defines {ev.get('routes_defined', 0)} route(s) but had "
                    f"ZERO hits in the last {window_days}d, and nothing imports it. "
                    f"Static + runtime agree: safe to delete."
                ),
            }
        return {
            "recommendation": "review",
            "confidence": base_conf,
            "rationale": (
                "No inbound import and no web routes; ICDEV has no per-module "
                "invocation log, so runtime cannot confirm. Manual check (CLI "
                "entry point? dynamic import?) before deletion."
            ),
        }

    # dead_function / dead_class
    if signal == "runtime_hot":
        return {
            "recommendation": "review",
            "confidence": "low",
            "rationale": (
                f"Containing file is runtime-hot ({ev.get('hit_count')} route "
                f"hit(s) in {window_days}d). The symbol may be reached via those "
                f"served routes (dynamic dispatch); do NOT auto-delete — verify "
                f"manually."
            ),
        }
    return {
        "recommendation": "review",
        "confidence": base_conf,
        "rationale": (
            "Static finding stands; no runtime contradiction found, but ICDEV "
            "has no per-function call log to positively confirm the symbol is "
            "dead. Verify before deletion."
        ),
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _default_conn_factory() -> Any:
    from tools.db.storage import get_connection
    return get_connection()


def enrich(
    findings: List[Dict[str, Any]],
    base: Path,
    window_days: int = DEFAULT_WINDOW_DAYS,
    conn: Any = None,
    conn_factory: Optional[Callable[[], Any]] = None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Annotate each finding with a `runtime` block and a `verdict` block.

    A connection may be injected (`conn`); otherwise one is opened lazily via
    `conn_factory` (default: tools.db.storage.get_connection) and closed at the
    end. If no connection can be obtained, every finding degrades to a
    no-runtime-signal verdict rather than failing.
    """
    now = now or datetime.now(timezone.utc)
    cutoff_iso = (now - timedelta(days=window_days)).isoformat()

    owns_conn = False
    if conn is None:
        try:
            conn = (conn_factory or _default_conn_factory)()
            owns_conn = True
        except Exception:
            conn = None

    # ONE grouped scan of usage_events for the whole window, reused for every
    # file (None if the source is unavailable -> all route findings degrade).
    try:
        traffic = load_route_traffic(conn, cutoff_iso)
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # Cache evidence per file (many findings share a file).
    file_ev: Dict[str, Dict[str, Any]] = {}
    enriched: List[Dict[str, Any]] = []
    for f in findings:
        out = dict(f)
        if f["kind"] in _ROUTE_RELEVANT_KINDS and f.get("file"):
            rel = f["file"]
            if rel not in file_ev:
                file_ev[rel] = runtime_evidence_for_file(rel, base, traffic, now)
            ev = file_ev[rel]
        else:
            ev = {"signal": "no_signal", "reason": "runtime traffic not "
                  "applicable to this finding kind"}
        out["runtime"] = ev
        out["verdict"] = verdict_for(f, ev, window_days)
        enriched.append(out)
    return enriched


def run(
    project_dir: Optional[str] = None,
    checks: Optional[List[str]] = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    base: Optional[Path] = None,
    conn: Any = None,
    conn_factory: Optional[Callable[[], Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Run the static lens then enrich with runtime deletion evidence."""
    base = base or BASE_DIR
    scan = run_scan(project_dir=project_dir, checks=checks, base=base)
    enriched = enrich(scan["findings"], base, window_days,
                      conn=conn, conn_factory=conn_factory, now=now)

    by_signal: Dict[str, int] = {}
    by_reco: Dict[str, int] = {}
    for f in enriched:
        by_signal[f["runtime"]["signal"]] = by_signal.get(f["runtime"]["signal"], 0) + 1
        rec = f["verdict"]["recommendation"]
        by_reco[rec] = by_reco.get(rec, 0) + 1

    confirmed = [
        f for f in enriched
        if f["verdict"]["recommendation"] == "delete"
        and f["verdict"]["confidence"] == "high"
    ]
    false_positives = [
        f for f in enriched if f["verdict"]["recommendation"] == "keep"
    ]

    return {
        "tool": "deletion_evidence",
        "target": scan["target"],
        "window_days": window_days,
        "summary": {
            "findings": len(enriched),
            "by_runtime_signal": by_signal,
            "by_recommendation": by_reco,
            "confirmed_deletions": len(confirmed),
            "runtime_false_positives": len(false_positives),
            "static_summary": scan["summary"],
        },
        "findings": enriched,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_human(report: Dict[str, Any], explain: bool) -> None:
    s = report["summary"]
    print()
    print(f"  Deletion evidence: {report['target']}  (window {report['window_days']}d)")
    print(f"  Findings: {s['findings']}   signal={s['by_runtime_signal']}   "
          f"reco={s['by_recommendation']}")
    print(f"  Confirmed deletions (static+runtime): {s['confirmed_deletions']}   "
          f"Runtime false-positives rescued: {s['runtime_false_positives']}")
    print()
    # Surface the most actionable first: confirmed deletes, then false-positives.
    order = {"delete": 0, "keep": 1, "review": 2}
    rows = sorted(report["findings"],
                  key=lambda f: (order.get(f["verdict"]["recommendation"], 3),
                                 f["kind"], f["file"] or ""))
    for f in rows:
        v, ev = f["verdict"], f["runtime"]
        loc = f"{f['file']}:{f['line']}" if f.get("line") else (f["file"] or "-")
        print(f"  [{v['recommendation'].upper():6} {v['confidence']:6}] "
              f"{f['kind']:18} {f['name']}")
        print(f"           {loc}   signal={ev['signal']}")
        if explain:
            print(f"           why: {v['rationale']}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Runtime-backed deletion evidence for dead-code (CL-4)")
    parser.add_argument("--project-dir", default=None, help="Directory to scan (default: tools/)")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
                        help=f"Runtime traffic window (default: {DEFAULT_WINDOW_DAYS})")
    parser.add_argument("--check", default="all",
                        choices=("all", "dead-code", "orphans", "deps", "circular"),
                        help="Limit the underlying static check (default: all)")
    parser.add_argument("--explain", action="store_true", help="Include rationale in human output")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Formatted terminal output")
    args = parser.parse_args()

    checks = None if args.check == "all" else [args.check]
    report = run(project_dir=args.project_dir, checks=checks,
                 window_days=args.window_days)

    if args.human:
        _print_human(report, args.explain)
    else:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
