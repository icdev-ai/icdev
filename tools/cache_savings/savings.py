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


#: Why the cache has no live entries. A zero hit rate is reported identically
#: whether caching is broken, switched off, or simply cold, so the state is named
#: rather than left to the reader.
#:
#: "Cold" USED to be the common case for a bad reason: llm_response_cache was
#: created UNLOGGED on PostgreSQL, and PostgreSQL truncates unlogged tables on
#: crash recovery. Every number below comes `FROM llm_response_cache` and there
#: is no separate savings table — so an unclean shutdown did not merely drop
#: cached responses (fine, they regenerate), it reset a CUMULATIVE metric to
#: $0.0000 with no record it had ever been anything else.
#:
#: The old note here called that "a deliberate trade (no WAL, fast cache
#: writes)". Measured 2026-08-16 on this deployment, 400 inserts of a 2KB body:
#: UNLOGGED 0.482 ms/insert, LOGGED 0.446 ms/insert — LOGGED marginally FASTER,
#: the difference noise. There was no throughput being bought, because a write
#: here happens once per cache MISS, i.e. once per LLM API call taking seconds.
#: The table is LOGGED as of migration 20260816123233; cold now means genuinely
#: new or recently expired, not "the server restarted".
STATE_POPULATED   = "populated"
STATE_DISABLED    = "disabled"        # response_cache.enabled is false
STATE_UNREACHABLE = "unreachable"     # the query itself failed
STATE_COLD        = "cold"            # table exists, zero rows at all
STATE_EXPIRED     = "expired"         # rows exist but all past expires_at


def _cache_state(enabled: bool, live_entries: int, stored_entries: int) -> str:
    if not enabled:
        return STATE_DISABLED
    if live_entries > 0:
        return STATE_POPULATED
    return STATE_EXPIRED if stored_entries > 0 else STATE_COLD


_STATE_DETAIL = {
    STATE_POPULATED: "",
    STATE_DISABLED: "response_cache.enabled is false in args/llm_config.yaml",
    STATE_UNREACHABLE: "the cache table could not be queried",
    STATE_COLD: (
        "cache is cold — no entries at all. It fills from live router traffic "
        "(one entry per cache miss), or run "
        "`python tools/cache_savings/warmer.py --warm` to pre-populate it. "
        "The table is LOGGED as of migration 20260816123233, so entries and the "
        "savings they represent now survive a restart; if this reads cold "
        "shortly after a restart, that is new behaviour worth investigating "
        "rather than the expected reset it used to be."
    ),
    STATE_EXPIRED: (
        "every stored entry is past its TTL (response_cache.ttl_seconds); the "
        "cache is warming rather than broken."
    ),
}

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
        return _empty(state=STATE_UNREACHABLE)

    # Stored rows IGNORING expiry. Without this, "never written" and "written
    # and aged out" both present as zero, and only one of them is worth acting
    # on. Best-effort: a failure here must not lose the numbers above.
    stored_entries = 0
    try:
        _srow = conn.execute("SELECT COUNT(*) FROM llm_response_cache").fetchone()
        stored_entries = int((_srow[0] if _srow else 0) or 0)
    except Exception as exc:  # noqa: BLE001
        log.debug("cache savings stored-count failed: %s", exc)

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
    state = _cache_state(bool(enabled), t_entries, stored_entries)
    return {
        "enabled":      enabled,
        "backend":      backend,
        "window_hours": 168,
        # Why the numbers below are what they are. A caller rendering 0% without
        # this cannot tell a broken cache from a cold one.
        "state":         state,
        "state_detail":  _STATE_DETAIL.get(state, ""),
        "stored_entries": stored_entries,
        "unlogged":      str(backend).startswith("postgres"),
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


def _empty(state: str = STATE_UNREACHABLE) -> dict:
    summary = {
        "total_entries": 0, "total_hits": 0, "hit_count": 0, "miss_count": 0,
        "hit_rate_pct": 0.0, "cache_write_tokens": 0, "cache_read_tokens": 0,
        "tokens_saved": 0, "resp_cache_usd_saved": 0.0, "context_cache_usd_saved": 0.0,
        "total_usd_saved": 0.0, "cost_usd_saved": 0.0, "context_cache_write_premium": 0.0,
    }
    return {"enabled": False, "backend": "unknown", "window_hours": 168,
            "state": state, "state_detail": _STATE_DETAIL.get(state, ""),
            "stored_entries": 0, "unlogged": False,
            "summary": summary, "by_function": []}
