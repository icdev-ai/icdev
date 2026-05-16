# CUI // SP-CTI
"""Cache savings statistics — aggregates prompt caching metrics from the LLM response cache.

Queries the llm_response_cache table to compute:
  - hit_count / miss_count / hit_rate_pct
  - tokens_saved (cache_read_input_tokens accumulated across all hits)
  - cost_usd_saved (estimated based on per-model pricing)

NIST 800-53: AU-12 (Audit Record Generation), SC-28 (Protection at Rest),
SA-11 (Developer Testing).

Usage::

    python tools/cache_savings/savings.py --json
    python tools/cache_savings/savings.py --since-hours 24 --json
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

logger = logging.getLogger("icdev.cache_savings.savings")

# Approximate blended per-token read cost for savings estimate (USD / 1k tokens).
# Conservative baseline: uses cheapest plausible provider (GPT-4o-mini cache read).
_DEFAULT_COST_PER_1K_CACHED_TOKENS_READ = 0.0000375  # OpenAI cached input pricing


def get_savings_stats(since_hours: int = 168) -> Dict[str, Any]:
    """Return cache savings statistics for the given look-back window.

    Args:
        since_hours: How many hours back to aggregate (default 168 = 7 days).

    Returns:
        Dict with keys:
          hit_count (int): Responses served from cache.
          miss_count (int): Responses computed fresh.
          hit_rate_pct (float): Percentage of requests served from cache [0, 100].
          tokens_saved (int): Sum of cache_read_input_tokens from all cached responses.
          cost_usd_saved (float): Estimated USD saved by reading from cache.
          window_hours (int): Look-back window used for this computation.
    """
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        since_ts = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        # Hit count = rows whose hit_count > 1 (served from cache at least once) OR
        # rows with cache_read_input_tokens > 0
        hit_row = conn.execute(
            """
            SELECT
                COUNT(*) AS hit_count,
                COALESCE(SUM(cache_read_input_tokens), 0) AS tokens_saved
            FROM llm_response_cache
            WHERE created_at >= ?
            AND (hit_count > 1 OR cache_read_input_tokens > 0)
            """,
            (since_ts,),
        ).fetchone()

        total_row = conn.execute(
            "SELECT COUNT(*) AS total FROM llm_response_cache WHERE created_at >= ?",
            (since_ts,),
        ).fetchone()

        conn.close()

        hit_count = int(hit_row["hit_count"] or 0) if hit_row else 0
        tokens_saved = int(hit_row["tokens_saved"] or 0) if hit_row else 0
        total = int(total_row["total"] or 0) if total_row else 0
        miss_count = max(0, total - hit_count)
        hit_rate_pct = round((hit_count / total) * 100, 2) if total > 0 else 0.0
        cost_usd_saved = round(tokens_saved / 1000.0 * _DEFAULT_COST_PER_1K_CACHED_TOKENS_READ, 6)

    except Exception as exc:
        logger.debug("get_savings_stats: DB query failed, returning zeros: %s", exc)
        hit_count = 0
        miss_count = 0
        tokens_saved = 0
        hit_rate_pct = 0.0
        cost_usd_saved = 0.0

    return {
        "hit_count": hit_count,
        "miss_count": miss_count,
        "hit_rate_pct": hit_rate_pct,
        "tokens_saved": tokens_saved,
        "cost_usd_saved": cost_usd_saved,
        "window_hours": since_hours,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main():
    parser = argparse.ArgumentParser(description="ICDEV™ cache savings statistics")
    parser.add_argument("--since-hours", type=int, default=168, help="Look-back window in hours (default 168 = 7 days)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    stats = get_savings_stats(since_hours=args.since_hours)
    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        print(f"Cache hit rate:   {stats['hit_rate_pct']:.1f}%")
        print(f"Hits:             {stats['hit_count']}")
        print(f"Misses:           {stats['miss_count']}")
        print(f"Tokens saved:     {stats['tokens_saved']:,}")
        print(f"Cost saved (est): ${stats['cost_usd_saved']:.4f} USD")
        print(f"Window:           {stats['window_hours']} hours")


if __name__ == "__main__":
    _main()
