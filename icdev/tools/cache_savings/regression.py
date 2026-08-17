#!/usr/bin/env python3
# CUI // SP-CTI
"""Prompt-cache REGRESSION detection over the per-call telemetry ledger (cch-obs-02).

Caching that worked and stops looks exactly like caching that was never enabled:
both render as zero. Azure served cached tokens and discarded the count for its
entire life and nothing went red; the savings ledger sat in an UNLOGGED table and
a restart zeroed it with no record it had ever been anything else.

cch-tel-01 made the number exist -- ``ai_telemetry.cache_read_input_tokens`` and
``.cache_creation_input_tokens``, one row per LLM call. This module decides when a
change in it is worth a human's attention. It reads; it never writes. The genesis
reflex ``cache_regression_reflex`` is what turns a finding into a kanban card.

THREE RUNGS
-----------
``stopped``      a provider reported cache reads across the baseline window and
                 reports exactly ZERO across the recent one, with real traffic in
                 both. The headline case: it was working, and it is not now.
``collapsed``    the same provider's cache-read share fell by at least
                 ``collapse_drop_ratio`` relative, from a baseline that was
                 meaningfully non-zero. Degradation short of a stop.
``never_cached`` a provider whose declared mechanism BILLS cached tokens
                 (explicit/automatic) has made enough instrumented calls to have
                 had a real chance, and has never once reported a cache read.
                 Configured for caching and never caching.

WHAT IT REFUSES TO SAY
----------------------
Every non-finding is NAMED rather than left as a bare zero, because the whole
defect being fixed is that a zero has four different meanings:

* rows predating the instrumentation hold 0 because they were BACKFILLED. The
  ``never_cached`` rung counts only rows at or after ``instrumented_since`` and
  reports ``pre_instrumentation_unknown`` when that instant cannot be
  established. On the live board (2026-08-16) every one of 13,073 rows predates
  it -- counting them would fabricate a finding against all 8 providers.
* the comparative rungs need no such floor: both require a NON-ZERO baseline,
  which a backfilled window cannot produce.
* a provider whose mechanism is ``local`` (Ollama server-side KV reuse) has no
  billed cache read to miss. A permanent zero there is correct, not broken.
* a provider absent from the mechanism map is ``unknown`` and never a finding.
* a database with no telemetry at all reports ``unmeasurable``, never a clean
  bill and never a wall of findings -- the fresh-worktree rule that
  ``check_capability_liveness`` and ``capability_consumption`` already apply.

THE SHARE'S DENOMINATOR
-----------------------
``cache_read_share = cache_read / (cache_read + input)``. Providers disagree on
whether ``input_tokens`` already includes cached reads (Anthropic excludes them,
OpenAI includes them), so this is a provider-comparable proxy rather than an
exact hit rate. That does not weaken a regression claim: both windows of a
comparison are the SAME provider under the SAME formula, so a collapse is a
collapse either way. Per-provider effectiveness reporting is cch-obs-01's job.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

log = get_logger("icdev.cache_savings.regression")

TABLE = "ai_telemetry"
CONFIG_PATH = BASE_DIR / "args" / "cache_regression.yaml"

#: Rung verdicts that are findings. Everything else is a named non-finding.
FINDING_VERDICTS = ("stopped", "collapsed", "never_cached")

#: Named non-findings. A zero has four meanings and this is where they are kept
#: apart; never collapse two of these into one.
VERDICT_HEALTHY = "healthy"                      # caching, and not meaningfully worse
VERDICT_NO_TRAFFIC = "no_traffic"                # no calls at all -- nothing was asked
VERDICT_INSUFFICIENT = "insufficient_calls"      # traffic, but below min_calls_per_window
VERDICT_NO_BILLING = "mechanism_no_billing"      # local/none -- latency, never dollars
VERDICT_UNKNOWN_MECH = "mechanism_unknown"       # not in the map; guessing is not allowed
VERDICT_PRE_INSTR = "pre_instrumentation_unknown"  # the floor could not be established
VERDICT_TOO_YOUNG = "insufficient_instrumented_calls"  # not enough post-floor calls yet

STATUS_OK = "ok"
STATUS_UNMEASURABLE = "unmeasurable"

_DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "windows": {"recent_days": 7, "baseline_days": 21},
    "thresholds": {
        "min_calls_per_window": 20,
        "collapse_drop_ratio": 0.7,
        "min_baseline_share": 0.05,
        "never_cached_min_calls": 50,
    },
    "instrumented_since": None,
    "instrumented_migration": "20260816135136",
    "mechanisms": {},
    "never_cached_mechanisms": ["explicit", "automatic"],
    "card": {
        "id_prefix": "cache-regr-",
        "task_type": "fix",
        "priority": "high",
        "status": "backlog",
    },
}


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read args/cache_regression.yaml, falling back to the defaults above.

    An unreadable config yields the defaults rather than an exception: the
    thresholds are the tuning surface, not a precondition for reporting.
    """
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in _DEFAULTS.items()}
    p = Path(path) if path else CONFIG_PATH
    try:
        import yaml

        loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 -- a missing config is not a failure
        log.debug("cache_regression: using defaults (%s: %s)", p, exc)
        return cfg

    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


# ---------------------------------------------------------------------------
# Substrate probes
# ---------------------------------------------------------------------------
def _table_exists(conn) -> bool:
    from tools.db.storage import get_backend

    try:
        if get_backend() == "postgresql":
            row = conn.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=%s",
                (TABLE,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=%s",
                (TABLE,),
            ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001 -- absence is the answer, not an error
        return False


def _has_cache_columns(conn) -> bool:
    """The cch-tel-01 columns. Absent means this deployment never recorded any."""
    try:
        conn.execute(
            f"SELECT cache_read_input_tokens, cache_creation_input_tokens "
            f"FROM {TABLE} WHERE 1=0"
        ).fetchall()
        return True
    except Exception:  # noqa: BLE001
        return False


def resolve_instrumented_since(conn, cfg: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """When did a cache count first become capable of being real here?

    Returns ``(iso_or_none, source)``. Config wins because the cch-tel-01
    migration's row is ABSENT from ``schema_migrations`` on the live PostgreSQL
    board -- the columns were applied there without a ledger entry -- so
    auto-detection alone would leave the ``never_cached`` rung permanently mute
    on the one deployment that matters.
    """
    explicit = cfg.get("instrumented_since")
    if explicit:
        return str(explicit), "config"

    version = cfg.get("instrumented_migration")
    if version:
        try:
            row = conn.execute(
                "SELECT applied_at FROM schema_migrations WHERE version = %s",
                (str(version),),
            ).fetchone()
            if row:
                applied = dict(row).get("applied_at")
                if applied:
                    return str(applied), "schema_migrations"
        except Exception:  # noqa: BLE001 -- no ledger is an answer, not an error
            pass
    return None, "unknown"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _window_bounds(end: datetime, cfg: Dict[str, Any]) -> Dict[str, str]:
    recent_days = int(cfg["windows"].get("recent_days", 7))
    baseline_days = int(cfg["windows"].get("baseline_days", 21))
    recent_start = end - timedelta(days=recent_days)
    baseline_start = recent_start - timedelta(days=baseline_days)
    # No microseconds: `+` sorts before `.`, so a boundary without them includes
    # rows written in either isoformat variant. logged_at is compared as TEXT
    # because that is how the router writes it (ISO-8601, UTC, one format).
    iso = lambda d: d.astimezone(timezone.utc).replace(microsecond=0).isoformat()  # noqa: E731
    return {
        "baseline_start": iso(baseline_start),
        "recent_start": iso(recent_start),
        "end": iso(end),
    }


def _aggregate(conn, start: str, end: str) -> Dict[str, Dict[str, int]]:
    """Per-provider token totals over ``[start, end)``."""
    rows = conn.execute(
        f"""
        SELECT provider,
               COUNT(*)                            AS calls,
               SUM(input_tokens)                   AS input_tokens,
               SUM(cache_read_input_tokens)        AS cache_read,
               SUM(cache_creation_input_tokens)    AS cache_creation
        FROM {TABLE}
        WHERE logged_at >= %s AND logged_at < %s
        GROUP BY provider
        """,
        (start, end),
    ).fetchall()
    out: Dict[str, Dict[str, int]] = {}
    for row in rows:
        d = dict(row)
        provider = d.get("provider") or "unknown"
        out[provider] = {
            "calls": int(d.get("calls") or 0),
            "input_tokens": int(d.get("input_tokens") or 0),
            "cache_read": int(d.get("cache_read") or 0),
            "cache_creation": int(d.get("cache_creation") or 0),
        }
    return out


def _instrumented_totals(conn, since: str) -> Dict[str, Dict[str, int]]:
    """Per-provider lifetime totals counting only rows the recorder could fill."""
    rows = conn.execute(
        f"""
        SELECT provider,
               COUNT(*)                         AS calls,
               SUM(cache_read_input_tokens)     AS cache_read,
               SUM(cache_creation_input_tokens) AS cache_creation
        FROM {TABLE}
        WHERE logged_at >= %s
        GROUP BY provider
        """,
        (since,),
    ).fetchall()
    out: Dict[str, Dict[str, int]] = {}
    for row in rows:
        d = dict(row)
        out[d.get("provider") or "unknown"] = {
            "calls": int(d.get("calls") or 0),
            "cache_read": int(d.get("cache_read") or 0),
            "cache_creation": int(d.get("cache_creation") or 0),
        }
    return out


def cache_read_share(agg: Optional[Dict[str, int]]) -> Optional[float]:
    """Cached share of countable input tokens, or None when nothing is countable.

    None is not zero: "no tokens to divide" and "no cached tokens" are the two
    claims this whole card exists to keep apart.
    """
    if not agg:
        return None
    denom = (agg.get("cache_read") or 0) + (agg.get("input_tokens") or 0)
    if denom <= 0:
        return None
    return (agg.get("cache_read") or 0) / denom


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def _classify(
    provider: str,
    recent: Optional[Dict[str, int]],
    baseline: Optional[Dict[str, int]],
    instrumented: Optional[Dict[str, int]],
    cfg: Dict[str, Any],
    instrumented_since: Optional[str],
) -> Dict[str, Any]:
    th = cfg["thresholds"]
    min_calls = int(th.get("min_calls_per_window", 20))
    drop_ratio = float(th.get("collapse_drop_ratio", 0.7))
    min_base_share = float(th.get("min_baseline_share", 0.05))
    never_min = int(th.get("never_cached_min_calls", 50))
    mechanism = (cfg.get("mechanisms") or {}).get(provider)
    never_mechs = set(cfg.get("never_cached_mechanisms") or [])

    r_calls = int((recent or {}).get("calls", 0))
    b_calls = int((baseline or {}).get("calls", 0))
    r_share = cache_read_share(recent)
    b_share = cache_read_share(baseline)

    detail: Dict[str, Any] = {
        "provider": provider,
        "mechanism": mechanism or "unknown",
        "recent_calls": r_calls,
        "baseline_calls": b_calls,
        "recent_cache_read": int((recent or {}).get("cache_read", 0)),
        "baseline_cache_read": int((baseline or {}).get("cache_read", 0)),
        "recent_share": r_share,
        "baseline_share": b_share,
    }

    inst_calls = int((instrumented or {}).get("calls", 0))
    inst_read = int((instrumented or {}).get("cache_read", 0))
    inst_write = int((instrumented or {}).get("cache_creation", 0))
    detail["instrumented_calls"] = inst_calls
    detail["instrumented_cache_read"] = inst_read

    # One ladder, first match wins. Every rung below a finding is a NAMED reason
    # the question could not be answered -- never a bare zero standing in for one.
    if r_calls == 0 and b_calls == 0 and inst_calls == 0:
        detail["verdict"] = VERDICT_NO_TRAFFIC
        return detail

    # --- comparative rungs: evidence-based, mechanism-independent -------------
    # A provider that DID report cache reads was caching, whatever the map says,
    # so these rungs never consult the mechanism declaration.
    comparable = (
        r_calls >= min_calls
        and b_calls >= min_calls
        and b_share is not None
        and b_share >= min_base_share
    )
    if comparable:
        if detail["recent_cache_read"] == 0:
            detail["verdict"] = "stopped"
            return detail
        if r_share is not None and (b_share - r_share) / b_share >= drop_ratio:
            detail["verdict"] = "collapsed"
            detail["drop_ratio"] = round((b_share - r_share) / b_share, 4)
            return detail
        detail["verdict"] = VERDICT_HEALTHY
        return detail

    # --- never_cached: a claim about ABSENCE, so it needs the floor ----------
    if mechanism is None:
        detail["verdict"] = VERDICT_UNKNOWN_MECH
    elif mechanism not in never_mechs:
        detail["verdict"] = VERDICT_NO_BILLING
    elif instrumented_since is None:
        detail["verdict"] = VERDICT_PRE_INSTR
    elif inst_read == 0 and inst_write == 0:
        # Configured to bill cached tokens, given a real chance, never once.
        detail["verdict"] = (
            "never_cached" if inst_calls >= never_min else VERDICT_TOO_YOUNG
        )
    else:
        # It has cached at some point, but this window was too thin to judge.
        detail["verdict"] = VERDICT_INSUFFICIENT
    return detail


def detect(
    conn: Any = None,
    config: Optional[Dict[str, Any]] = None,
    window_end: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Evaluate every provider in the ledger. Read-only; files nothing.

    Args:
        conn: an open storage connection. One is opened (and closed) if omitted.
        config: an already-loaded config dict; ``load_config()`` is used if omitted.
        window_end: the instant the recent window ends at. Defaults to now, and
            exists so the thresholds can be replayed over historical windows.

    Returns a dict with ``status`` (``ok`` or ``unmeasurable``), ``findings``
    (the subset worth a card) and ``providers`` (EVERY provider with its named
    verdict, findings and non-findings alike).
    """
    cfg = config or load_config()
    end = window_end or datetime.now(timezone.utc)
    bounds = _window_bounds(end, cfg)

    result: Dict[str, Any] = {
        "status": STATUS_OK,
        "reason": None,
        "windows": bounds,
        "thresholds": dict(cfg.get("thresholds") or {}),
        "instrumented_since": None,
        "instrumented_since_source": "unknown",
        "findings": [],
        "providers": [],
    }

    owns_conn = conn is None
    if owns_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        if not _table_exists(conn):
            result.update(status=STATUS_UNMEASURABLE, reason="telemetry_table_absent")
            return result
        if not _has_cache_columns(conn):
            result.update(status=STATUS_UNMEASURABLE, reason="cache_columns_absent")
            return result

        total = dict(conn.execute(f"SELECT COUNT(*) AS n FROM {TABLE}").fetchone() or {})
        if int(total.get("n") or 0) == 0:
            result.update(status=STATUS_UNMEASURABLE, reason="no_operating_history")
            return result

        since, since_source = resolve_instrumented_since(conn, cfg)
        result["instrumented_since"] = since
        result["instrumented_since_source"] = since_source

        recent = _aggregate(conn, bounds["recent_start"], bounds["end"])
        baseline = _aggregate(conn, bounds["baseline_start"], bounds["recent_start"])
        instrumented = _instrumented_totals(conn, since) if since else {}
    finally:
        if owns_conn:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    for provider in sorted(set(recent) | set(baseline) | set(instrumented)):
        detail = _classify(
            provider,
            recent.get(provider),
            baseline.get(provider),
            instrumented.get(provider),
            cfg,
            since,
        )
        result["providers"].append(detail)
        if detail.get("verdict") in FINDING_VERDICTS:
            result["findings"].append(detail)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[list] = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Prompt-cache regression signal (cch-obs-02)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument(
        "--window-end",
        help="ISO-8601 instant the recent window ends at (replay a historical window)",
    )
    ap.add_argument(
        "--gate",
        action="store_true",
        help="exit 1 when a regression is found (0 clean, 2 unmeasurable)",
    )
    args = ap.parse_args(argv)

    end = datetime.fromisoformat(args.window_end) if args.window_end else None
    if end is not None and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    res = detect(window_end=end)

    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"status: {res['status']}" + (f" ({res['reason']})" if res["reason"] else ""))
        print(
            f"window: {res['windows']['baseline_start']} .. "
            f"{res['windows']['recent_start']} .. {res['windows']['end']}"
        )
        print(
            f"instrumented_since: {res['instrumented_since']} "
            f"({res['instrumented_since_source']})"
        )
        print(f"{'provider':<20} {'mechanism':<15} {'verdict':<32} recent/baseline calls")
        for p in res["providers"]:
            print(
                f"{p['provider']:<20} {p.get('mechanism', ''):<15} "
                f"{p.get('verdict', ''):<32} {p['recent_calls']}/{p['baseline_calls']}"
            )
        print(f"\nfindings: {len(res['findings'])}")

    if args.gate:
        if res["status"] == STATUS_UNMEASURABLE:
            return 2
        return 1 if res["findings"] else 0
    return 0


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv as _load_dotenv

        _load_dotenv(BASE_DIR / ".env", override=True)
    except ImportError:
        pass
    raise SystemExit(main())
