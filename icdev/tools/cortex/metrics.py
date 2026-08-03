# CUI // SP-CTI
"""Cortex observability — read-only aggregation over the ``cortex_audit`` trail.

The append-only ``cortex_audit`` table records one row per governed Cortex call
(``GovernancePipeline._audit``). This module rolls those rows up into usage /
governance / spend metrics for the ``/cortex/metrics`` panel — calls, outcome
and block breakdown, redactions, cache hits, and cost/latency.

Design notes:
- ``cortex_audit`` has no cost/tokens/latency COLUMNS; those live in the
  free-form ``gates_json`` blob (written by ``record_audit``), so all
  JSON-derived aggregation is done in Python after ``json.loads`` — never via
  SQLite-dialect ``json_extract`` in runtime SQL (PG-portability rule).
- Rows written before the accounting enrichment simply carry cost/latency 0,
  so the numbers fill in going forward rather than back-populating.
- Purely read-only: no writes, no schema changes, no hot-path cost.

Cost control (cxo-perf-04). A 30-day window (the panel's widest preset) used to
``fetchall()`` every row in it and ``json.loads`` each one in Python, and the
three routes that render it — ``/cortex/metrics``, ``/cortex/api/metrics``,
``/cortex/api/metrics/tile`` — each opened their own connection and re-ran it,
so loading the page while the home tile polled did the whole scan two or three
times over. Three things bound that now:

1. **Counts come from SQL.** Calls / blocked, and the by-function, by-outcome
   and by-tenant breakdowns, come from a single ``GROUP BY`` whose result set
   is bounded by cardinality (functions x tenants x outcomes), not by row
   count. Those numbers stay EXACT over the full window.
2. **The gates_json detail read is bounded.** Only the newest
   ``_DETAIL_ROW_LIMIT`` rows in the window are parsed for the fields that have
   no column of their own (cost, tokens, latency, redactions, domain, model,
   cache_hit). When that cap bites, ``detail.truncated`` is set so the panel can
   say the spend figures cover a sample rather than quietly under-reporting.
3. **The three routes share one computation.** A short-TTL in-process memo keyed
   by window + detail cap + the RLS boundary the read runs under (tenant +
   classification) + the DB pointer. The security context is part of the key
   because ``get_connection()`` auto-attaches ``flask.g.security_context``, so
   the same window legitimately returns different rows per caller — a memo that
   ignored it would serve one tenant's governance numbers to another.

The index on ``cortex_audit(created_at)`` (migration 262) covers the window
filter for both reads; no composite index is needed until a per-tenant windowed
rollup is introduced.

NIST 800-53: AU-6 (audit review/analysis/reporting), AU-12.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_DEFAULT_WINDOW_HOURS = 24

# Newest-first cap on the rows whose gates_json is parsed in Python. Counts are
# unaffected (they come from SQL); only the JSON-derived spend/latency detail is
# sampled when a window holds more rows than this.
_DETAIL_ROW_LIMIT = 5000

# Shared-computation memo. Short enough that the panel still reads as live,
# long enough that a page render plus a polling tile collapse into one scan.
_MEMO_TTL_SECONDS = 15.0
_MEMO_MAX_ENTRIES = 64
_MEMO_TTL_ENV = "CORTEX_METRICS_MEMO_TTL_SECONDS"


def _empty(window_hours: int, *, status: str = "unavailable") -> dict:
    """Skeleton result.

    ``status`` distinguishes two states the panel used to render identically:

    ``unavailable``  the table is missing or the read raised — Cortex metrics
                     are BROKEN and the operator should investigate.
    ``idle``         the table read fine and simply held no rows in the window —
                     Cortex is HEALTHY but has had no governed traffic.

    Collapsing these was actively misleading: a governance tile reading "no
    calls" looks the same whether governance is switched off or the audit table
    has vanished.
    """
    return {
        "available": status != "unavailable",
        "status": status,
        "last_call_at": "",
        "window_hours": window_hours,
        "summary": {
            "calls": 0, "blocked": 0, "block_rate_pct": 0.0,
            "redactions": 0, "cache_hits": 0,
            "cost_usd": 0.0, "avg_latency_ms": 0.0,
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        },
        # How much of the window the gates_json-derived figures actually cover.
        # calls/blocked are always exact; cost, tokens, latency, redactions,
        # cache hits and the domain/model breakdowns come from these rows only.
        "detail": {"rows_scanned": 0, "limit": None, "truncated": False},
        "by_function": [],
        "by_outcome": {},
        "by_domain": [],
        "by_tenant": [],
        "by_model": [],
    }


def _cutoff(window_hours: int) -> str:
    """UTC cutoff string in a format both PG (timestamp) and SQLite (TEXT from
    CURRENT_TIMESTAMP, 'YYYY-MM-DD HH:MM:SS') compare correctly against."""
    dt = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── Shared-computation memo ──────────────────────────────────────────────────

_memo: dict = {}
_memo_lock = threading.Lock()


def _memo_ttl() -> float:
    """Memo lifetime in seconds; ``<= 0`` disables the memo entirely."""
    raw = os.environ.get(_MEMO_TTL_ENV)
    if raw is None:
        return _MEMO_TTL_SECONDS
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return _MEMO_TTL_SECONDS


def _reader_context() -> tuple[str, str]:
    """The RLS boundary this read will run under (tenant_id, classification).

    ``get_connection()`` auto-attaches ``flask.g.security_context``, so two
    callers asking for the same window can legitimately see different rows.
    Folding both into the memo key is what keeps a cached result from crossing
    a tenant or classification boundary.
    """
    try:
        from flask import g, has_request_context
        if not has_request_context():
            return "", ""
        ctx = getattr(g, "security_context", None) or {}
        if isinstance(ctx, dict):
            return str(ctx.get("tenant_id") or ""), str(ctx.get("classification") or "")
        return (str(getattr(ctx, "tenant_id", "") or ""),
                str(getattr(ctx, "classification", "") or ""))
    except Exception:  # noqa: BLE001 — no Flask / no request context
        return "", ""


def _memo_key(window_hours: int, limit: int) -> tuple:
    tenant, classification = _reader_context()
    # The DB pointer is part of the key so a process that repoints at another
    # database (tests, tenant DB switch) never reads a stale rollup.
    return (
        window_hours, limit, tenant, classification,
        os.environ.get("ICDEV_STORAGE_BACKEND", ""),
        os.environ.get("ICDEV_DB_PATH", ""),
    )


def _memo_get(key: tuple):
    with _memo_lock:
        item = _memo.get(key)
        if item is None:
            return None
        expiry, value = item
        if expiry < time.monotonic():
            _memo.pop(key, None)
            return None
        return value


def _memo_put(key: tuple, value: dict, ttl: float) -> None:
    with _memo_lock:
        if len(_memo) >= _MEMO_MAX_ENTRIES:
            _memo.clear()
        _memo[key] = (time.monotonic() + ttl, value)


def reset_memo() -> None:
    """Drop the shared-computation memo (tests, or after a DB repoint)."""
    with _memo_lock:
        _memo.clear()


# ── Reads ────────────────────────────────────────────────────────────────────

def _scan(conn, window_hours: int, limit: int):
    """Two bounded reads over ``cortex_audit`` for the trailing window.

    Returns ``(counts, rows)``:

    ``counts``  SQL ``GROUP BY`` rollup — exact call/block counts per
                function x tenant x outcome over the FULL window. Bounded by
                cardinality, so a 30-day window costs the same as a 1-hour one
                on the Python side.
    ``rows``    the newest ``limit`` rows in the window, for the gates_json
                fields that have no column (cost/tokens/latency/redactions/
                domain/model/cache_hit).
    """
    cutoff = _cutoff(window_hours)
    counts = conn.execute(
        "SELECT function, tenant_id, outcome, blocked, COUNT(*) AS n "
        "FROM cortex_audit WHERE created_at >= %s "
        "GROUP BY function, tenant_id, outcome, blocked",
        (cutoff,),
    ).fetchall()
    rows = conn.execute(
        "SELECT function, tenant_id, classification, outcome, blocked, gates_json "
        "FROM cortex_audit WHERE created_at >= %s "
        "ORDER BY created_at DESC LIMIT %s",
        (cutoff, int(limit)),
    ).fetchall()
    return counts, rows


def _close(conn, own_conn: bool) -> None:
    if not own_conn:
        return
    try:
        conn.close()
    except Exception:  # noqa: BLE001, S110
        pass


def summarize(
    window_hours: int = _DEFAULT_WINDOW_HOURS,
    conn=None,
    *,
    use_memo: bool = True,
    detail_limit: int | None = None,
) -> dict:
    """Aggregate ``cortex_audit`` over the trailing ``window_hours``.

    Returns a nested dict: ``summary`` totals plus ``by_function`` /
    ``by_outcome`` / ``by_domain`` / ``by_tenant`` breakdowns. Degrades to an
    ``available: False`` skeleton if the table is missing (fresh DB) or the read
    fails — the panel must render, never 500.

    Successful results are memoized for a few seconds so the metrics page, the
    JSON endpoint and the home tile share one scan. Pass ``use_memo=False`` (or
    set ``CORTEX_METRICS_MEMO_TTL_SECONDS=0``) to force a fresh read; a caller
    supplying its own ``conn`` always bypasses the memo, since that connection
    carries its own security context.
    """
    window_hours = max(1, int(window_hours or _DEFAULT_WINDOW_HOURS))
    limit = max(1, int(detail_limit or _DETAIL_ROW_LIMIT))
    own_conn = conn is None
    ttl = _memo_ttl()

    memo_key = None
    if own_conn and use_memo and ttl > 0:
        memo_key = _memo_key(window_hours, limit)
        cached = _memo_get(memo_key)
        if cached is not None:
            return copy.deepcopy(cached)

    if own_conn:
        from tools.db.storage import get_connection
        conn = get_connection()

    try:
        counts, rows = _scan(conn, window_hours, limit)
    except Exception as exc:  # noqa: BLE001 — table may not exist yet
        logger.debug("cortex metrics: cortex_audit read failed: %s", exc)
        _close(conn, own_conn)
        # Deliberately NOT memoized: a broken trail must be re-probed on the
        # next hit so recovery shows up immediately rather than one TTL later.
        return _empty(window_hours, status="unavailable")

    # An empty window is not the same as a broken table. Report when the last
    # governed call actually happened so "no calls in 24h" can be read as
    # "quiet since <date>" rather than "no data exists" — the trail may hold
    # months of history just outside the default window.
    last_call_at = ""
    if not counts:
        try:
            cur = conn.execute("SELECT MAX(created_at) FROM cortex_audit")
            row = cur.fetchone()
            raw = _row_get(row, "max", 0) if row else None
            last_call_at = str(raw) if raw else ""
        except Exception as exc:  # noqa: BLE001
            logger.debug("cortex metrics: last_call_at lookup failed: %s", exc)

    _close(conn, own_conn)

    if not counts:
        out = _empty(window_hours, status="idle")
        out["last_call_at"] = last_call_at
    else:
        out = _aggregate(rows, window_hours, counts=counts, detail_limit=limit)

    if memo_key is not None:
        # Store a copy so a caller mutating the returned dict cannot poison it.
        _memo_put(memo_key, copy.deepcopy(out), ttl)
    return out


def _row_get(row, key: str, idx: int):
    """Read a column from a dict-like (RealDictRow) or tuple row."""
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[idx]
    except (IndexError, KeyError, TypeError):
        return None


def _truthy(value) -> bool:
    """Coerce a ``blocked`` column across PG (bool) and SQLite (0/1/text)."""
    return bool(value) and value not in (0, "0", "f", "false", "False")


def _aggregate(rows, window_hours: int, counts=None, detail_limit: int | None = None) -> dict:
    """Roll ``rows`` (gates_json detail) up, then overlay exact SQL ``counts``.

    ``counts`` is optional: without it the detail rows ARE the whole window and
    every figure is derived from them (the pure-aggregation path used by tests
    and by any caller holding its own row set).
    """
    out = _empty(window_hours, status="ok")
    out["available"] = True
    summ = out["summary"]
    by_function: dict = {}
    by_outcome: dict = {}
    by_domain: dict = {}
    by_tenant: dict = {}
    by_model: dict = {}
    total_latency = 0.0
    latency_n = 0

    for row in rows:
        function = _row_get(row, "function", 0) or "cortex"
        tenant = _row_get(row, "tenant_id", 1) or "default"
        outcome = _row_get(row, "outcome", 3) or "pass"
        blocked = _truthy(_row_get(row, "blocked", 4))
        gates_raw = _row_get(row, "gates_json", 5)
        gj = {}
        if gates_raw:
            try:
                gj = json.loads(gates_raw) if isinstance(gates_raw, str) else dict(gates_raw)
            except (ValueError, TypeError):
                gj = {}
        cost = float(gj.get("cost_usd") or 0.0)
        latency = float(gj.get("latency_ms") or 0.0)
        redactions = int(gj.get("redactions_applied") or 0)
        domain = gj.get("domain") or "(none)"
        cache_hit = bool(gj.get("cache_hit"))
        in_tok = int(gj.get("input_tokens") or 0)
        out_tok = int(gj.get("output_tokens") or 0)
        model = gj.get("model") or "(unknown)"

        summ["calls"] += 1
        if blocked:
            summ["blocked"] += 1
        summ["redactions"] += redactions
        summ["cost_usd"] += cost
        summ["input_tokens"] += in_tok
        summ["output_tokens"] += out_tok
        if cache_hit:
            summ["cache_hits"] += 1
        if latency > 0:
            total_latency += latency
            latency_n += 1

        fn = by_function.setdefault(
            function, {"function": function, "calls": 0, "blocked": 0,
                       "cost_usd": 0.0, "total_tokens": 0}
        )
        fn["calls"] += 1
        fn["blocked"] += 1 if blocked else 0
        fn["cost_usd"] += cost
        fn["total_tokens"] += in_tok + out_tok

        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        d = by_domain.setdefault(domain, {"domain": domain, "calls": 0})
        d["calls"] += 1
        t = by_tenant.setdefault(tenant, {"tenant_id": tenant, "calls": 0, "blocked": 0})
        t["calls"] += 1
        t["blocked"] += 1 if blocked else 0
        # Per-model rollup (only rows that actually recorded a model — i.e. real
        # LLM calls; cache hits / blocked-early rows carry "(unknown)").
        m = by_model.setdefault(
            model, {"model": model, "calls": 0, "cost_usd": 0.0, "total_tokens": 0}
        )
        m["calls"] += 1
        m["cost_usd"] += cost
        m["total_tokens"] += in_tok + out_tok

    summ["cost_usd"] = round(summ["cost_usd"], 6)
    summ["total_tokens"] = summ["input_tokens"] + summ["output_tokens"]
    summ["avg_latency_ms"] = round(total_latency / latency_n, 1) if latency_n else 0.0
    summ["block_rate_pct"] = (
        round(100.0 * summ["blocked"] / summ["calls"], 1) if summ["calls"] else 0.0
    )
    for fn in by_function.values():
        fn["cost_usd"] = round(fn["cost_usd"], 6)
    for m in by_model.values():
        m["cost_usd"] = round(m["cost_usd"], 6)

    out["detail"] = {
        "rows_scanned": len(rows),
        "limit": detail_limit,
        "truncated": bool(detail_limit) and len(rows) >= detail_limit,
    }
    out["by_function"] = sorted(by_function.values(), key=lambda r: r["calls"], reverse=True)
    out["by_outcome"] = by_outcome
    out["by_domain"] = sorted(by_domain.values(), key=lambda r: r["calls"], reverse=True)
    out["by_tenant"] = sorted(by_tenant.values(), key=lambda r: r["calls"], reverse=True)
    out["by_model"] = sorted(by_model.values(), key=lambda r: r["total_tokens"], reverse=True)

    if counts is not None:
        _apply_counts(out, counts)
    return out


def _apply_counts(out: dict, counts) -> None:
    """Replace the detail-derived call/block counts with the exact SQL rollup.

    The detail read is capped, so its counts only describe a sample. These
    columns (function / tenant_id / outcome / blocked) exist on the table, so
    the true totals come straight from ``GROUP BY`` and are always exact — even
    on a 30-day window that truncated the gates_json detail. Cost, tokens,
    latency and the domain/model breakdowns stay detail-derived; ``detail`` says
    how much of the window they cover.
    """
    by_function = {f["function"]: f for f in out["by_function"]}
    for fn in by_function.values():
        fn["calls"] = 0
        fn["blocked"] = 0
    by_outcome: dict = {}
    by_tenant: dict = {}
    calls = 0
    blocked_total = 0

    for row in counts:
        function = _row_get(row, "function", 0) or "cortex"
        tenant = _row_get(row, "tenant_id", 1) or "default"
        outcome = _row_get(row, "outcome", 2) or "pass"
        blocked = _truthy(_row_get(row, "blocked", 3))
        n = int(_row_get(row, "n", 4) or 0)

        calls += n
        if blocked:
            blocked_total += n

        fn = by_function.setdefault(
            function, {"function": function, "calls": 0, "blocked": 0,
                       "cost_usd": 0.0, "total_tokens": 0}
        )
        fn["calls"] += n
        fn["blocked"] += n if blocked else 0

        by_outcome[outcome] = by_outcome.get(outcome, 0) + n
        t = by_tenant.setdefault(tenant, {"tenant_id": tenant, "calls": 0, "blocked": 0})
        t["calls"] += n
        t["blocked"] += n if blocked else 0

    summ = out["summary"]
    summ["calls"] = calls
    summ["blocked"] = blocked_total
    summ["block_rate_pct"] = round(100.0 * blocked_total / calls, 1) if calls else 0.0

    out["by_function"] = sorted(by_function.values(), key=lambda r: r["calls"], reverse=True)
    out["by_outcome"] = by_outcome
    out["by_tenant"] = sorted(by_tenant.values(), key=lambda r: r["calls"], reverse=True)
