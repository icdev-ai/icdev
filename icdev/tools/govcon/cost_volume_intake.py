#!/usr/bin/env python3
# CUI // SP-CTI
"""Accept a cost volume priced SOMEWHERE ELSE.

prem-bid-04. The architecture question this settles: who owns the price?

ICDEV can compute a cost volume (rate_benchmarker.generate_cost_volume). But it prices
from ``pg_lcat_allocations``, whose hourly_rate is frequently NULL — because ICDEV does
not hold the supplier rate cards. compass does: it merges ~80 supplier files
(tools/dataops/rate_card_merger) and knows what an LCAT actually costs from a given
vendor on a given date.

So **compass is the pricing authority**, and ICDEV computing its own number would give
us two prices for one bid — which is worse than having none, because now somebody has to
decide which one is real, and they will decide it late.

This accepts compass's price as authoritative and records it. ICDEV's own pricing path
stays for opportunities compass never touched.

## An accepted price is still checked

Accepting does not mean believing. A pushed volume is REFUSED when:

  * it carries no total — there is nothing to accept;
  * its line items do not add up to the direct labour it claims (tolerance: a cent per
    line, for float noise). A total that does not reconcile with its own lines is not a
    price, it is a number;
  * it declares itself ``partial`` or ``unpriced``. compass refuses to emit those, but
    the server does not take that on trust: the whole point of prem-bid-01 is that a
    volume with holes in it must never be mistaken for a complete price, and the last
    place to catch that is the place it gets stored.

## The provenance is stored, not assumed

``pricing_strategy`` records WHERE the price came from. Six months on, "why is this
$4.2M" has a different answer depending on whether ICDEV guessed it or compass priced it
off a real rate card, and the row should say which.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

# Per-line float tolerance when reconciling line items against the claimed total.
_CENT = 0.01

# Where a stored price came from. Recorded, never assumed.
PRICING_SOURCES = ("compass", "icdev_internal", "manual")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def accept_cost_volume(
    *,
    opportunity_id: str,
    priced: Dict[str, Any],
    source: str = "compass",
    tenant_id: str = "default",
    classification: str = "CUI",
    conn=None,
) -> Dict[str, Any]:
    """Record an externally-priced cost volume. Refuses anything it cannot reconcile."""
    opportunity_id = (opportunity_id or "").strip()
    if not opportunity_id:
        return {"status": "refused", "reason": "opportunity_id is required"}

    if source not in PRICING_SOURCES:
        return {"status": "refused",
                "reason": f"source must be one of {list(PRICING_SOURCES)}"}

    if not isinstance(priced, dict):
        return {"status": "refused", "reason": "priced must be an object"}

    # A volume that says it is incomplete IS incomplete. compass refuses to emit one —
    # but the server does not take that on trust. A price with a hole in it must never be
    # mistaken for a complete one, and this is the last place to catch it.
    declared = str(priced.get("status") or "").lower()
    if declared in ("partial", "unpriced", "incomplete"):
        return {
            "status": "refused",
            "reason": (
                f"the pushed volume declares itself '{declared}'. A volume with unpriced "
                f"lines is not a price — storing it would let a hole in a bid reach the "
                f"customer wearing the shape of a total."
            ),
            "unrated": priced.get("unrated") or [],
        }

    total = priced.get("total_price", priced.get("total_evaluated_price"))
    if total is None:
        return {"status": "refused", "reason": "no total_price — there is nothing to accept"}
    total = _num(total)

    build_up = priced.get("build_up") or {}
    direct_labor = _num(build_up.get("direct_labor"))
    sub_cost = _num(build_up.get("subcontractor_cost"))
    odc_cost = _num(build_up.get("odc_cost"))

    # The lines must add up to what the volume claims. A total that does not reconcile
    # with its own line items is not a price, it is a number.
    line_items: List[Dict[str, Any]] = priced.get("line_items") or []
    if line_items:
        summed = sum(_num(li.get("annual_cost", li.get("amount"))) for li in line_items)
        claimed = direct_labor + sub_cost
        if abs(summed - claimed) > max(_CENT * len(line_items), _CENT):
            return {
                "status": "refused",
                "reason": (
                    f"line items sum to {summed:.2f} but the volume claims "
                    f"{claimed:.2f} of labour. A total that does not reconcile with its "
                    f"own lines cannot be audited, and an unauditable price on a bid is "
                    f"one nobody can defend."
                ),
                "line_sum": round(summed, 2),
                "claimed": round(claimed, 2),
            }

    ptw = priced.get("ptw") or {}
    low = (ptw.get("low") or {}).get("total_price") if isinstance(ptw.get("low"), dict) else None
    high = (ptw.get("high") or {}).get("total_price") if isinstance(ptw.get("high"), dict) else None

    close_after = conn is None
    conn = conn or get_connection()
    try:
        cv_id = f"cv-{uuid.uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO pg_cost_volumes (id, opportunity_id, contract_type, "
            "pricing_strategy, total_evaluated_price, direct_labor_cost, fringe_rate, "
            "overhead_rate, g_and_a_rate, fee_rate, subcontractor_cost, odc_cost, "
            "ptw_estimate_low, ptw_estimate_high, status, created_at, updated_at, "
            "tenant_id, classification) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                cv_id, opportunity_id,
                str(priced.get("contract_type") or "ffp").lower(),
                # Provenance, stored rather than assumed. "Why is this $4.2M" has a
                # different answer depending on who priced it, and the row should say.
                f"priced_by:{source}",
                total, direct_labor,
                _num((priced.get("wrap_rates") or {}).get("fringe_pct")),
                _num((priced.get("wrap_rates") or {}).get("overhead_pct")),
                _num((priced.get("wrap_rates") or {}).get("ga_pct")),
                _num((priced.get("wrap_rates") or {}).get("fee_pct")),
                sub_cost, odc_cost,
                _num(low) if low is not None else None,
                _num(high) if high is not None else None,
                "draft", _now(), _now(), tenant_id, classification,
            ),
        )
        conn.commit()
        return {
            "status": "accepted",
            "cost_volume_id": cv_id,
            "opportunity_id": opportunity_id,
            "total_evaluated_price": round(total, 2),
            "priced_by": source,
            "line_item_count": len(line_items),
        }
    finally:
        if close_after:
            try:
                conn.close()
            except Exception:
                pass
