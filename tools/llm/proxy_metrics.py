# CUI // SP-CTI
"""ICDEV LLM proxy spend + rate metrics collector (lpx-obs-01).

Surfaces the shared LLM proxy's operational metrics — spend per key, requests
per model, rate-limit pressure — into ICDEV's existing observability (the Ops Hub
Canvas ``/ops/llm`` LLMOps page). This adds NO new dashboard page; it feeds an
existing surface, so the 8-component page gate does not apply.

Two independent sources are aggregated, and the report says which is which:

* **Ledger (always available, ICDEV's own record).** ICDEV already records
  per-key spend in ``llm_proxy_spend`` (lpx-keys-02) and per-team minute-bucket
  RPM/TPM in ``llm_proxy_team_usage`` (lpx-teams-01). These are the source of
  truth ICDEV controls and are present whether or not the proxy container is
  running. Aggregation is plain ``SUM``/``GROUP BY`` (PG-portable; no
  SQLite-dialect JSON SQL).

* **Proxy Prometheus scrape (best-effort, opt-in).** When the proxy is enabled
  (``ICDEV_LLM_PROXY_ENABLED``) and its ``/metrics`` endpoint is reachable, this
  module scrapes the LiteLLM Prometheus exposition (spend, tokens, requests,
  rate-limit/failure counters) using the ``prometheus_client`` text parser that
  ICDEV already depends on. It NEVER raises and NEVER blocks: an unreachable or
  absent proxy simply yields ``{"available": false, ...}``. This keeps the
  air-gap guarantee (lpx-egress-01) intact — nothing here requires the proxy.

The two records are deliberately NOT joined: the proxy counts per virtual
key/model at the gateway, while the ledger counts per ICDEV scope. Reconciling
the two (divergence detection) is the concern of ``tools/llm/proxy_reconcile.py``
(lpx-obs-02); this module only *collects and surfaces* them.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from tools.llm import proxy_gateway

# Metric-family name prefixes emitted by LiteLLM's Prometheus exporter that we
# care about. Matched by ``startswith`` so version suffixes (``_total``, ``_bucket``)
# are captured. Kept as data, not hardcoded call-site literals.
_SPEND_FAMILIES = ("litellm_spend_metric",)
_TOKEN_FAMILIES = ("litellm_total_tokens", "litellm_input_tokens", "litellm_output_tokens")
_REQUEST_FAMILIES = ("litellm_requests_metric", "litellm_request_total_latency")
_RATE_LIMIT_FAMILIES = (
    "litellm_rate_limit",
    "litellm_deployment_failure_responses",
    "litellm_failed_requests_metric",
    "litellm_deployment_state",
)

_DEFAULT_WINDOW_HOURS = 24


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _window_start_iso(window_hours: int, now: Optional[datetime] = None) -> str:
    now = now or _now()
    return (now - timedelta(hours=max(0, int(window_hours)))).isoformat()


def _table_exists(conn, table: str) -> bool:
    try:
        from tools.db.storage import table_exists as _te

        return bool(_te(conn, table))
    except Exception:
        # Fallback: probe with a bounded query; swallow errors as "absent".
        try:
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1")  # nosec B608 — fixed identifier
            return True
        except Exception:
            return False


# ── Ledger aggregation (ICDEV's own record) ─────────────────────────────────

def collect_ledger_spend(window_hours: int = _DEFAULT_WINDOW_HOURS, *, top: int = 10, conn=None) -> Dict[str, Any]:
    """Aggregate per-key / per-scope spend from ``llm_proxy_spend`` over a window.

    Returns totals plus the top spenders by key and by scope. Never raises —
    a missing table (fresh DB, proxy never used) yields zeroed totals.
    """
    own = conn is None
    c = conn or get_connection()
    try:
        if not _table_exists(c, "llm_proxy_spend"):
            return {
                "available": False,
                "reason": "llm_proxy_spend table absent (proxy budgets never used)",
                "total_spend_usd": 0.0, "event_count": 0,
                "total_input_tokens": 0, "total_output_tokens": 0,
                "by_key": [], "by_scope": [],
            }
        start = _window_start_iso(window_hours)
        totals = c.execute(
            "SELECT COALESCE(SUM(cost_usd),0.0) AS spend, COUNT(*) AS n, "
            "COALESCE(SUM(input_tokens),0) AS itok, COALESCE(SUM(output_tokens),0) AS otok "
            "FROM llm_proxy_spend WHERE recorded_at >= %s",
            (start,),
        ).fetchone()
        by_key_rows = c.execute(
            "SELECT key_id, scope_type, COALESCE(SUM(cost_usd),0.0) AS spend, "
            "COUNT(*) AS n, COALESCE(SUM(input_tokens),0) AS itok, "
            "COALESCE(SUM(output_tokens),0) AS otok "
            "FROM llm_proxy_spend WHERE recorded_at >= %s "
            "GROUP BY key_id, scope_type ORDER BY spend DESC LIMIT %s",
            (start, int(top)),
        ).fetchall()
        by_scope_rows = c.execute(
            "SELECT scope_type, COALESCE(SUM(cost_usd),0.0) AS spend, COUNT(*) AS n "
            "FROM llm_proxy_spend WHERE recorded_at >= %s "
            "GROUP BY scope_type ORDER BY spend DESC",
            (start,),
        ).fetchall()
        td = dict(totals)
        return {
            "available": True,
            "window_hours": int(window_hours),
            "total_spend_usd": round(float(td["spend"]), 6),
            "event_count": int(td["n"]),
            "total_input_tokens": int(td["itok"]),
            "total_output_tokens": int(td["otok"]),
            "by_key": [
                {
                    "key_id": r["key_id"], "scope_type": r["scope_type"],
                    "spend_usd": round(float(r["spend"]), 6), "requests": int(r["n"]),
                    "input_tokens": int(r["itok"]), "output_tokens": int(r["otok"]),
                }
                for r in by_key_rows
            ],
            "by_scope": [
                {"scope_type": r["scope_type"], "spend_usd": round(float(r["spend"]), 6),
                 "requests": int(r["n"])}
                for r in by_scope_rows
            ],
        }
    except Exception as exc:  # never let observability collection break a page
        return {"available": False, "reason": str(exc), "total_spend_usd": 0.0,
                "event_count": 0, "by_key": [], "by_scope": []}
    finally:
        if own:
            _safe_close(c)


def collect_ledger_rate(window_hours: int = _DEFAULT_WINDOW_HOURS, *, top: int = 10, conn=None) -> Dict[str, Any]:
    """Aggregate per-team RPM/TPM pressure from ``llm_proxy_team_usage``.

    ``window_minute`` is an epoch-minute integer bucket; we compare against the
    epoch minute of the window start. Returns busiest minute-buckets and rollups.
    """
    own = conn is None
    c = conn or get_connection()
    try:
        if not _table_exists(c, "llm_proxy_team_usage"):
            return {"available": False,
                    "reason": "llm_proxy_team_usage table absent (per-team limits never used)",
                    "total_requests": 0, "total_tokens": 0, "busiest": []}
        start_minute = int((_now() - timedelta(hours=max(0, int(window_hours)))).timestamp() // 60)
        totals = c.execute(
            "SELECT COALESCE(SUM(request_count),0) AS req, COALESCE(SUM(token_count),0) AS tok, "
            "COUNT(*) AS buckets FROM llm_proxy_team_usage WHERE window_minute >= %s",
            (start_minute,),
        ).fetchone()
        busiest = c.execute(
            "SELECT session_id, team_id, window_minute, request_count, token_count "
            "FROM llm_proxy_team_usage WHERE window_minute >= %s "
            "ORDER BY request_count DESC, token_count DESC LIMIT %s",
            (start_minute, int(top)),
        ).fetchall()
        td = dict(totals)
        return {
            "available": True,
            "window_hours": int(window_hours),
            "total_requests": int(td["req"]),
            "total_tokens": int(td["tok"]),
            "bucket_count": int(td["buckets"]),
            "busiest": [
                {"session_id": r["session_id"], "team_id": r["team_id"],
                 "window_minute": r["window_minute"], "requests": int(r["request_count"]),
                 "tokens": int(r["token_count"])}
                for r in busiest
            ],
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc), "total_requests": 0,
                "total_tokens": 0, "busiest": []}
    finally:
        if own:
            _safe_close(c)


# ── Prometheus scrape (best-effort, opt-in) ─────────────────────────────────

def scrape_proxy_prometheus(base_url: Optional[str] = None, *, timeout: float = 3.0) -> Dict[str, Any]:
    """Scrape and parse the proxy's Prometheus ``/metrics`` endpoint.

    Best-effort and non-blocking: returns ``{"available": false, "reason": ...}``
    when the proxy is disabled, unreachable, or exposes no metrics. Never raises,
    so a dashboard render or CLI call is safe with no proxy running.

    Only reads the metrics endpoint — no provider URL/key literals live here; the
    base URL comes from ``proxy_gateway.proxy_base_url()`` (inside ``tools/llm/``).
    """
    if not proxy_gateway.is_proxy_enabled() and base_url is None:
        return {"available": False, "reason": "proxy disabled (ICDEV_LLM_PROXY_ENABLED not set)"}
    url = (base_url or proxy_gateway.proxy_base_url()).rstrip("/") + "/metrics"
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec B310 — operator-configured loopback gateway
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return {"available": False, "reason": f"metrics endpoint unreachable: {exc}", "url": url}

    try:
        from prometheus_client.parser import text_string_to_metric_families
    except Exception:
        return {"available": False, "reason": "prometheus_client parser unavailable", "url": url}

    spend_by_key: Dict[str, float] = {}
    requests_by_model: Dict[str, float] = {}
    tokens_total = 0.0
    rate_limit_errors = 0.0
    families_seen: List[str] = []
    try:
        for fam in text_string_to_metric_families(raw):
            name = fam.name
            if name.startswith(_SPEND_FAMILIES):
                families_seen.append(name)
                for s in fam.samples:
                    key = s.labels.get("hashed_api_key") or s.labels.get("api_key") \
                        or s.labels.get("key") or s.labels.get("team") or "unlabelled"
                    spend_by_key[key] = spend_by_key.get(key, 0.0) + float(s.value)
            elif name.startswith(_REQUEST_FAMILIES):
                families_seen.append(name)
                for s in fam.samples:
                    model = s.labels.get("model") or s.labels.get("litellm_model_name") \
                        or s.labels.get("deployment") or "unlabelled"
                    requests_by_model[model] = requests_by_model.get(model, 0.0) + float(s.value)
            elif name.startswith(_TOKEN_FAMILIES):
                families_seen.append(name)
                for s in fam.samples:
                    tokens_total += float(s.value)
            elif name.startswith(_RATE_LIMIT_FAMILIES):
                families_seen.append(name)
                for s in fam.samples:
                    rate_limit_errors += float(s.value)
    except Exception as exc:
        return {"available": False, "reason": f"metrics parse error: {exc}", "url": url}

    return {
        "available": True,
        "url": url,
        "families_seen": sorted(set(families_seen)),
        "spend_by_key": [{"key": k, "spend_usd": round(v, 6)} for k, v in
                         sorted(spend_by_key.items(), key=lambda kv: kv[1], reverse=True)],
        "requests_by_model": [{"model": k, "requests": v} for k, v in
                              sorted(requests_by_model.items(), key=lambda kv: kv[1], reverse=True)],
        "total_tokens": tokens_total,
        "rate_limit_errors": rate_limit_errors,
    }


# ── Unified collection ──────────────────────────────────────────────────────

def collect_proxy_metrics(window_hours: int = _DEFAULT_WINDOW_HOURS, *, top: int = 10,
                          scrape: bool = True, conn=None) -> Dict[str, Any]:
    """Unified proxy metrics for the LLMOps observability surface.

    Combines the ICDEV ledger aggregation (always available) with a best-effort
    Prometheus scrape (only when the proxy is enabled+reachable). The returned
    dict is safe to render directly and always includes ``proxy_enabled`` so the
    UI can explain an off/unconfigured proxy rather than showing empty cards.
    """
    own = conn is None
    c = conn or get_connection()
    try:
        ledger_spend = collect_ledger_spend(window_hours, top=top, conn=c)
        ledger_rate = collect_ledger_rate(window_hours, top=top, conn=c)
        prom = scrape_proxy_prometheus() if scrape else {"available": False, "reason": "scrape disabled"}
        return {
            "proxy_enabled": proxy_gateway.is_proxy_enabled(),
            "local_copy_mode": proxy_gateway.is_local_copy_mode(),
            "base_url": proxy_gateway.proxy_base_url(),
            "window_hours": int(window_hours),
            "generated_at": _now().isoformat(),
            "ledger_spend": ledger_spend,
            "ledger_rate": ledger_rate,
            "prometheus": prom,
            "source_note": (
                "ledger_* is ICDEV's own record (always present); prometheus is a "
                "best-effort scrape of the proxy /metrics endpoint (only when enabled "
                "and reachable). They are NOT joined — reconciliation lives in "
                "tools/llm/proxy_reconcile.py (lpx-obs-02)."
            ),
        }
    finally:
        if own:
            _safe_close(c)


def _safe_close(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ICDEV LLM proxy spend + rate metrics (lpx-obs-01)")
    parser.add_argument("--window-hours", type=int, default=_DEFAULT_WINDOW_HOURS)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--no-scrape", action="store_true", help="Skip the Prometheus scrape (ledger only)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = collect_proxy_metrics(args.window_hours, top=args.top, scrape=not args.no_scrape)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        led = result["ledger_spend"]
        print(f"Proxy enabled: {result['proxy_enabled']}  base_url: {result['base_url']}")
        print(f"Ledger spend ({result['window_hours']}h): ${led.get('total_spend_usd', 0):.4f} "
              f"over {led.get('event_count', 0)} events")
        print(f"Prometheus scrape available: {result['prometheus'].get('available')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
