#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Cache Warm Reflex — refill the LLM response cache after it resets.

``tools/cache_savings/warmer.py`` exists to "pre-populate the LLM response cache
on deploy so the first real user requests are cache hits", and NOTHING invoked
it: no reflex, no cron, no CI step. A capability with no consumer — the defect
this platform ships most.

It matters here because the cache does not merely expire, it VANISHES.
``llm_response_cache`` is created UNLOGGED on PostgreSQL
(``tools/llm/response_cache.py``), which is a deliberate trade — no WAL, fast
cache writes — with the documented consequence that PostgreSQL empties unlogged
tables on any unclean shutdown or crash recovery. After a restart the cache is
cold, refills only from live router traffic, and the savings tile reads zero.

COST DISCIPLINE. Warming makes real LLM calls, so this reflex is not a periodic
spend: it asks ``get_savings_stats()`` for the cache state first and warms ONLY
when the state is ``cold`` (zero rows at all). A ``populated`` cache is a no-op,
and so is an ``expired`` one — entries aging out through their TTL is the cache
working, and paying to pre-empt that every cycle would burn tokens to beat a
timer. ``disabled`` and ``unreachable`` are no-ops for the obvious reasons.

GREEN tier — reads the cache table, and on a cold cache makes the seeded LLM
calls the warmer already defines in ``context/cache_seeds/default_seeds.yaml``.

Return contract: ALWAYS a dict carrying ``success``. The daemon reads
``result.get("success", False)``, so a missing key is scored a failure forever
and trips the per-reflex circuit breaker — a reflex that did nothing because
there was nothing to do must still report success.
"""
IMPLEMENTATION_STATUS = "full"

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

#: Only this state justifies spending tokens. See the cost note above.
_WARM_WHEN = "cold"


def run(config, state) -> dict:
    """Warm the LLM response cache when, and only when, it is cold.

    Returns ``{success, metric_value, details}``. ``metric_value`` is the number
    of seed queries warmed this cycle — 0 on the common no-op path.
    """
    try:
        from tools.cache_savings.savings import get_savings_stats
    except Exception as exc:  # noqa: BLE001 — a reflex must never crash the daemon
        logger.warning("cache_warm: savings module unavailable: %s", exc)
        return {"success": False, "error": f"{type(exc).__name__}: {exc}",
                "metric_value": 0.0}

    try:
        stats = get_savings_stats()
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache_warm: could not read cache state: %s", exc)
        return {"success": False, "error": f"{type(exc).__name__}: {exc}",
                "metric_value": 0.0}

    cache_state = stats.get("state", "")
    if cache_state != _WARM_WHEN:
        # The overwhelmingly common path. Reported as a success with the reason,
        # so "did nothing" is distinguishable from "failed" in genesis_audit.
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {
                "action": "skipped",
                "cache_state": cache_state,
                "reason": f"cache state is {cache_state!r}; warming only on {_WARM_WHEN!r}",
                "live_entries": (stats.get("summary") or {}).get("total_entries", 0),
                "stored_entries": stats.get("stored_entries", 0),
            },
        }

    try:
        from tools.cache_savings.warmer import CacheWarmer

        results = CacheWarmer().warm_on_deploy() or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache_warm: warming failed: %s", exc)
        return {"success": False, "error": f"{type(exc).__name__}: {exc}",
                "metric_value": 0.0,
                "details": {"action": "warm", "cache_state": cache_state}}

    warmed = sum(1 for r in results if r.get("status") == "warmed")
    errors = [r for r in results if r.get("status") == "error"]
    tokens = sum(int(r.get("tokens_used") or 0) for r in results)
    logger.info("cache_warm: warmed %d seed(s), %d token(s), %d error(s)",
                warmed, tokens, len(errors))

    details = {
        "action": "warmed" if warmed else "warm_failed",
        "cache_state_before": cache_state,
        "seeds_attempted": len(results),
        "seeds_warmed": warmed,
        "tokens_used": tokens,
        "errors": len(errors),
    }
    # Decided the cache was cold, tried to fix it, warmed NOTHING. Reporting
    # success here would be a reflex claiming to have done work it did not do —
    # green in genesis_audit with an empty cache behind it. Observed on the live
    # platform: every seed refused with "Module 'generative_intelligence' budget
    # exceeded", so the honest verdict is failure and the per-reflex circuit
    # breaker SHOULD stop retrying a thing that cannot currently succeed.
    if results and not warmed:
        first = next((str(r.get("error") or "") for r in errors), "")
        details["first_error"] = first[:300]
        return {"success": False, "metric_value": 0.0,
                "error": f"warmed 0 of {len(results)} seed(s): {first[:200]}",
                "details": details}
    return {"success": True, "metric_value": float(warmed), "details": details}


if __name__ == "__main__":  # pragma: no cover - manual invocation
    import json

    print(json.dumps(run({}, None), indent=2, default=str))
