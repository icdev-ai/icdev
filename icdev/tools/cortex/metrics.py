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

NIST 800-53: AU-6 (audit review/analysis/reporting), AU-12.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_DEFAULT_WINDOW_HOURS = 24


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


def summarize(window_hours: int = _DEFAULT_WINDOW_HOURS, conn=None) -> dict:
    """Aggregate ``cortex_audit`` over the trailing ``window_hours``.

    Returns a nested dict: ``summary`` totals plus ``by_function`` /
    ``by_outcome`` / ``by_domain`` / ``by_tenant`` breakdowns. Degrades to an
    ``available: False`` skeleton if the table is missing (fresh DB) or the read
    fails — the panel must render, never 500.
    """
    window_hours = max(1, int(window_hours or _DEFAULT_WINDOW_HOURS))
    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection
        conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT function, tenant_id, classification, outcome, blocked, "
            "gates_json FROM cortex_audit WHERE created_at >= %s",
            (_cutoff(window_hours),),
        )
        rows = cursor.fetchall()
    except Exception as exc:  # noqa: BLE001 — table may not exist yet
        logger.debug("cortex metrics: cortex_audit read failed: %s", exc)
        if own_conn:
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110
                pass
        return _empty(window_hours, status="unavailable")

    # An empty window is not the same as a broken table. Report when the last
    # governed call actually happened so "no calls in 24h" can be read as
    # "quiet since <date>" rather than "no data exists" — the trail may hold
    # months of history just outside the default window.
    last_call_at = ""
    if not rows:
        try:
            cur = conn.execute("SELECT MAX(created_at) FROM cortex_audit")
            row = cur.fetchone()
            raw = _row_get(row, "max", 0) if row else None
            last_call_at = str(raw) if raw else ""
        except Exception as exc:  # noqa: BLE001
            logger.debug("cortex metrics: last_call_at lookup failed: %s", exc)

    if own_conn:
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110
            pass

    if not rows:
        out = _empty(window_hours, status="idle")
        out["last_call_at"] = last_call_at
        return out

    return _aggregate(rows, window_hours)


def _row_get(row, key: str, idx: int):
    """Read a column from a dict-like (RealDictRow) or tuple row."""
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[idx]
    except (IndexError, KeyError, TypeError):
        return None


def _aggregate(rows, window_hours: int) -> dict:
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
        blocked = _row_get(row, "blocked", 4)
        blocked = bool(blocked) and blocked not in (0, "0", "f", "false", "False")
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

    out["by_function"] = sorted(by_function.values(), key=lambda r: r["calls"], reverse=True)
    out["by_outcome"] = by_outcome
    out["by_domain"] = sorted(by_domain.values(), key=lambda r: r["calls"], reverse=True)
    out["by_tenant"] = sorted(by_tenant.values(), key=lambda r: r["calls"], reverse=True)
    out["by_model"] = sorted(by_model.values(), key=lambda r: r["total_tokens"], reverse=True)
    return out
