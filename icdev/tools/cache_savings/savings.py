# CUI // SP-CTI
"""Cache Savings analytics — aggregate hit rates, token savings, and cost deltas.

Two-level cache cost model:
  Response cache  — full LLM call avoided (input + output cost saved)
  Context cache   — tokens read at $0.30/MTok vs $3.00/MTok input price
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from typing import Any

log = get_logger("icdev.cache_savings.savings")

# Anthropic claude-sonnet-4-6 pricing (USD / token)
_IN  = 3.00 / 1_000_000      # $3.00/MTok
_OUT = 15.00 / 1_000_000     # $15.00/MTok
_CW  = 3.75 / 1_000_000      # $3.75/MTok cache write (25% premium)
_CR  = 0.30 / 1_000_000      # $0.30/MTok cache read  (90% discount)


def get_savings_stats(conn: Any = None) -> dict:
    """Return aggregated cache savings metrics.

    Returns:
        {
          enabled (bool), backend (str), window_hours (int),
          summary  { total_entries, total_hits, hit_rate_pct, hit_count, miss_count,
                     cache_write_tokens, cache_read_tokens, tokens_saved,
                     resp_cache_usd_saved, context_cache_usd_saved, total_usd_saved,
                     context_cache_write_premium, cost_usd_saved },
          by_function [ { function, total_entries, total_hits, hit_rate_pct,
                          avoided_calls, cache_write_tokens, cache_read_tokens,
                          resp_cache_usd_saved, context_cache_usd_saved, cost_usd_saved } ]
        }
    """
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()

    try:
        rows = conn.execute(
            """
            SELECT
                function,
                COUNT(*)                              AS total_entries,
                SUM(hit_count)                        AS total_hits,
                SUM(input_tokens)                     AS input_tokens,
                SUM(output_tokens)                    AS output_tokens,
                COALESCE(SUM(cache_creation_input_tokens), 0) AS cache_write_tokens,
                COALESCE(SUM(cache_read_input_tokens),     0) AS cache_read_tokens
            FROM llm_response_cache
            WHERE expires_at > datetime('now')
            GROUP BY function
            ORDER BY SUM(hit_count) DESC
            LIMIT 50
            """
        ).fetchall()
    except Exception as exc:
        log.debug("cache savings query failed: %s", exc)
        return _empty()

    by_function = []
    t_entries = t_hits = t_avoided = t_cw = t_cr = 0
    t_resp_saved = t_ctx_saved = t_ctx_premium = 0.0

    for row in rows:
        fn      = row[0]
        entries = row[1] or 0
        hits    = row[2] or 0
        inp     = row[3] or 0
        out     = row[4] or 0
        cw      = row[5] or 0
        cr      = row[6] or 0

        # hit_count DEFAULTS TO 1 at insert and increments per hit, so SUM(hit_count)
        # is total REQUESTS (one store + n hits), not hits. Each repeat hit beyond
        # that first store avoided a full LLM call.
        avoided = max(0, hits - entries)
        resp_saved = avoided * (inp * _IN + out * _OUT)

        ctx_saved   = cr * (_IN - _CR)
        ctx_premium = cw * (_CW - _IN)
        net_ctx     = max(0.0, ctx_saved - ctx_premium)

        # Rate is avoided_calls / requests. It was hits/entries, which is
        # requests-per-entry: one entry served three times reported "300%".
        hit_rate = round(avoided / hits * 100, 1) if hits else 0.0

        by_function.append({
            "function":              fn,
            "total_entries":         entries,
            "total_hits":            hits,
            "hit_rate_pct":          hit_rate,
            "avoided_calls":         avoided,
            "cache_write_tokens":    cw,
            "cache_read_tokens":     cr,
            "resp_cache_usd_saved":  round(resp_saved, 4),
            "context_cache_usd_saved": round(net_ctx, 4),
            "cost_usd_saved":        round(resp_saved + net_ctx, 4),
        })

        t_entries += entries
        t_hits    += hits
        t_avoided += avoided
        t_cw      += cw
        t_cr      += cr
        t_resp_saved  += resp_saved
        t_ctx_saved   += net_ctx
        t_ctx_premium += ctx_premium

    # hit_count + miss_count == t_hits (total requests), so the rate below is the
    # same number the two counts imply — they used to disagree: miss_count was
    # max(0, entries - hits), which is 0 for every possible input.
    platform_hit_rate = round(t_avoided / t_hits * 100, 1) if t_hits else 0.0
    miss_count = t_entries

    try:
        from tools.llm.response_cache import _load_config
        cfg = _load_config()
        enabled = cfg.get("enabled", False)
        backend = cfg.get("backend", "unknown")
    except Exception:
        enabled, backend = False, "unknown"

    total_saved = t_resp_saved + t_ctx_saved
    return {
        "enabled":      enabled,
        "backend":      backend,
        "window_hours": 168,
        "summary": {
            "total_entries":           t_entries,
            "total_hits":              t_hits,
            "hit_count":               t_avoided,
            "miss_count":              miss_count,
            "hit_rate_pct":            platform_hit_rate,
            "cache_write_tokens":      t_cw,
            "cache_read_tokens":       t_cr,
            "tokens_saved":            t_cr,
            "resp_cache_usd_saved":    round(t_resp_saved, 4),
            "context_cache_usd_saved": round(t_ctx_saved, 4),
            "total_usd_saved":         round(total_saved, 4),
            "cost_usd_saved":          round(total_saved, 4),
            "context_cache_write_premium": round(t_ctx_premium, 4),
        },
        "by_function": by_function,
    }


def _empty() -> dict:
    summary = {
        "total_entries": 0, "total_hits": 0, "hit_count": 0, "miss_count": 0,
        "hit_rate_pct": 0.0, "cache_write_tokens": 0, "cache_read_tokens": 0,
        "tokens_saved": 0, "resp_cache_usd_saved": 0.0, "context_cache_usd_saved": 0.0,
        "total_usd_saved": 0.0, "cost_usd_saved": 0.0, "context_cache_write_premium": 0.0,
    }
    return {"enabled": False, "backend": "unknown", "window_hours": 168,
            "summary": summary, "by_function": []}
