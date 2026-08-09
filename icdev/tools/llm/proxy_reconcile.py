# CUI // SP-CTI
"""Reconcile proxy spend against token_tracker and llm_gateway_audit (lpx-obs-02).

Once the shared LLM proxy is enabled, two independent cost records coexist:

* **Proxy accounting** — ``llm_proxy_spend`` (lpx-keys-02), per virtual key/scope.
* **ICDEV's own accounting** — ``agent_token_usage`` (tools/agent/token_tracker),
  per agent/project, with ``cost_estimate_usd``. ``llm_gateway_audit``
  (tools/llm/gateway) is a third signal — it records request VOLUME and latency
  (no cost column), so it corroborates call counts, not dollars.

Silent divergence between the two dollar records means the budget guardrails are
not actually enforcing anything. This module produces a reconciliation report
(CLI with ``--json``) over a window and can gate (``--gate``) when the two
diverge past a configurable threshold.

**Why aggregation, not a join.** The two ledgers are keyed DIFFERENTLY — the
proxy by virtual key/scope (a cohort: team/guild/user), the tracker by agent —
and ``agent_token_usage`` has no ``key_id`` and ``llm_proxy_spend`` has no
``agent_id``. A direct row join would be empty or fragile (the same keying
mismatch that shaped lpx-teams-03). So we aggregate each side to totals over the
SAME time window and compare the aggregates. Aggregation is plain ``SUM``/
``GROUP BY`` (PG-portable; no SQLite-dialect JSON SQL).

**Structural gaps are explained, not papered over** (see ``STRUCTURAL_NOTES``):
some divergence is expected and is NOT an anomaly — e.g. the tracker is a
superset (it records local + direct-cloud calls the proxy never sees), cached
proxy responses cost ~nothing upstream yet are still real usage the tracker
counts, and the two use different keyings/attribution. The gate therefore only
fires when the proxy is actually active AND both sides have data AND they still
diverge beyond the threshold.
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

from tools.db.storage import get_connection, table_exists
from tools.llm import proxy_gateway

_DEFAULT_WINDOW_HOURS = 24
_DEFAULT_THRESHOLD_PCT = 10.0

STRUCTURAL_NOTES = [
    "token_tracker (agent_token_usage) is a SUPERSET: it records local and "
    "direct-to-cloud calls that never traverse the proxy, so proxy_spend <= "
    "tracker_spend is normal, especially when the proxy is off or partially adopted.",
    "Cached proxy responses (LiteLLM cache hits) cost ~$0 upstream but are still "
    "real usage the tracker may count — a legitimate source of divergence.",
    "The two ledgers use different attribution keys (proxy: virtual key/scope; "
    "tracker: agent/project), so per-row joining is impossible; only windowed "
    "aggregates are comparable.",
    "llm_gateway_audit has no cost column; it corroborates request VOLUME only.",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _window_start_iso(window_hours: int, now: Optional[datetime] = None) -> str:
    now = now or _now()
    return (now - timedelta(hours=max(0, int(window_hours)))).isoformat()


def _proxy_totals(conn, start_iso: str) -> Dict[str, Any]:
    if not table_exists(conn, "llm_proxy_spend"):
        return {"available": False, "spend_usd": 0.0, "requests": 0,
                "input_tokens": 0, "output_tokens": 0, "by_tenant": []}
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd),0.0) AS spend, COUNT(*) AS n, "
        "COALESCE(SUM(input_tokens),0) AS itok, COALESCE(SUM(output_tokens),0) AS otok "
        "FROM llm_proxy_spend WHERE recorded_at >= %s",
        (start_iso,),
    ).fetchone()
    by_tenant = conn.execute(
        "SELECT COALESCE(tenant_id, '(none)') AS tenant, COALESCE(SUM(cost_usd),0.0) AS spend, "
        "COUNT(*) AS n FROM llm_proxy_spend WHERE recorded_at >= %s "
        "GROUP BY tenant_id ORDER BY spend DESC",
        (start_iso,),
    ).fetchall()
    d = dict(row)
    return {
        "available": True,
        "spend_usd": round(float(d["spend"]), 6),
        "requests": int(d["n"]),
        "input_tokens": int(d["itok"]),
        "output_tokens": int(d["otok"]),
        "by_tenant": [
            {"tenant_id": r["tenant"], "spend_usd": round(float(r["spend"]), 6),
             "requests": int(r["n"])}
            for r in by_tenant
        ],
    }


def _tracker_totals(conn, start_iso: str) -> Dict[str, Any]:
    if not table_exists(conn, "agent_token_usage"):
        return {"available": False, "spend_usd": 0.0, "requests": 0,
                "input_tokens": 0, "output_tokens": 0}
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_estimate_usd),0.0) AS spend, COUNT(*) AS n, "
        "COALESCE(SUM(input_tokens),0) AS itok, COALESCE(SUM(output_tokens),0) AS otok "
        "FROM agent_token_usage WHERE created_at >= %s",
        (start_iso,),
    ).fetchone()
    d = dict(row)
    return {
        "available": True,
        "spend_usd": round(float(d["spend"]), 6),
        "requests": int(d["n"]),
        "input_tokens": int(d["itok"]),
        "output_tokens": int(d["otok"]),
    }


def _gateway_audit_count(conn, start_iso: str) -> Dict[str, Any]:
    if not table_exists(conn, "llm_gateway_audit"):
        return {"available": False, "requests": 0}
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM llm_gateway_audit WHERE created_at >= %s",
        (start_iso,),
    ).fetchone()
    return {"available": True, "requests": int(dict(row)["n"])}


def _divergence_pct(a: float, b: float) -> float:
    """Symmetric relative divergence between two magnitudes, in percent.

    Uses the larger magnitude as the denominator so it is bounded [0, 100] and
    never divides by zero. Two zeros are perfectly reconciled (0%).
    """
    hi = max(abs(a), abs(b))
    if hi == 0.0:
        return 0.0
    return round(abs(a - b) / hi * 100.0, 4)


def reconcile(
    window_hours: int = _DEFAULT_WINDOW_HOURS,
    *,
    threshold_pct: float = _DEFAULT_THRESHOLD_PCT,
    conn=None,
) -> Dict[str, Any]:
    """Reconcile proxy spend vs token_tracker (and audit volume) over a window.

    Returns a report dict with a ``status`` of:
      * ``reconciled`` — within threshold (or nothing to compare);
      * ``divergent`` — proxy active, both sides have spend, divergence > threshold;
      * ``proxy_inactive`` — proxy has no spend in the window (expected when off).
    """
    own = conn is None
    c = conn or get_connection()
    try:
        start = _window_start_iso(window_hours)
        proxy = _proxy_totals(c, start)
        tracker = _tracker_totals(c, start)
        audit = _gateway_audit_count(c, start)

        spend_div = _divergence_pct(proxy["spend_usd"], tracker["spend_usd"])
        token_div = _divergence_pct(
            proxy["input_tokens"] + proxy["output_tokens"],
            tracker["input_tokens"] + tracker["output_tokens"],
        )

        proxy_active = proxy["available"] and proxy["requests"] > 0
        both_have_spend = proxy["spend_usd"] > 0 and tracker["spend_usd"] > 0

        if not proxy_active:
            status = "proxy_inactive"
            gate_fail = False
            message = ("No proxy spend recorded in the window — nothing to reconcile "
                       "(the proxy is opt-in and off by default).")
        elif both_have_spend and spend_div > threshold_pct:
            status = "divergent"
            gate_fail = True
            message = (f"Proxy vs token_tracker spend diverge by {spend_div}% "
                       f"(> {threshold_pct}% threshold): "
                       f"proxy ${proxy['spend_usd']:.4f} vs tracker ${tracker['spend_usd']:.4f}. "
                       "Investigate before trusting budget guardrails.")
        else:
            status = "reconciled"
            gate_fail = False
            message = (f"Within threshold: spend divergence {spend_div}% "
                       f"(<= {threshold_pct}%).")

        return {
            "status": status,
            "gate_fail": gate_fail,
            "window_hours": int(window_hours),
            "threshold_pct": float(threshold_pct),
            "generated_at": _now().isoformat(),
            "proxy_enabled": proxy_gateway.is_proxy_enabled(),
            "proxy": proxy,
            "token_tracker": tracker,
            "gateway_audit": audit,
            "divergence": {
                "spend_pct": spend_div,
                "token_pct": token_div,
                "spend_delta_usd": round(proxy["spend_usd"] - tracker["spend_usd"], 6),
            },
            "structural_notes": STRUCTURAL_NOTES,
            "message": message,
        }
    finally:
        if own:
            try:
                c.close()
            except Exception:
                pass


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile LLM proxy spend vs token_tracker/gateway audit (lpx-obs-02)")
    parser.add_argument("--window-hours", type=int, default=_DEFAULT_WINDOW_HOURS)
    parser.add_argument("--threshold-pct", type=float, default=_DEFAULT_THRESHOLD_PCT)
    parser.add_argument("--gate", action="store_true",
                        help="Exit 1 when status is 'divergent' (for CI/monitoring)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = reconcile(args.window_hours, threshold_pct=args.threshold_pct)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"status: {report['status']}")
        print(f"proxy spend: ${report['proxy']['spend_usd']:.4f}  "
              f"tracker spend: ${report['token_tracker']['spend_usd']:.4f}  "
              f"divergence: {report['divergence']['spend_pct']}%")
        print(report["message"])
    if args.gate and report["gate_fail"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
