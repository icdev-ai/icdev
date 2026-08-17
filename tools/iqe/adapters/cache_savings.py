# CUI // SP-CTI
"""IQE cache_savings collection adapters.

Registering this module exposes three IQE collections:
  cache.stats        — per-function hit rate, avoided calls, token savings, cost saved
  cache.entries      — raw llm_response_cache rows (non-expired)
  cache.by_provider  — per-provider PREFIX-cache effectiveness (cch-obs-01)

The first two describe the RESPONSE cache (an LLM call avoided entirely) and
read `llm_response_cache`. The third describes PREFIX caching (cached input
tokens on a call that still happened) and reads `ai_telemetry`. They are
different questions about different substrates; querying one for the other's
answer returns a confidently wrong number.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _stats_adapter(conn: Any) -> list[dict]:
    """Return cache stats aggregated by function."""
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                function,
                COUNT(*)                                    AS total_entries,
                SUM(hit_count)                              AS total_hits,
                ROUND(SUM(hit_count) * 100.0 / COUNT(*), 1) AS hit_rate_pct,
                MAX(0, SUM(hit_count) - COUNT(*))           AS avoided_calls,
                COALESCE(SUM(cache_read_input_tokens), 0)   AS cache_read_tokens,
                COALESCE(SUM(cache_creation_input_tokens),0) AS cache_write_tokens
            FROM llm_response_cache
            WHERE expires_at > datetime('now')
            GROUP BY function
            ORDER BY total_hits DESC
            """
        ).fetchall()
        cols = [d[0] for d in rows[0].description] if rows and hasattr(rows[0], "description") else None
        if cols is None and rows:
            cur = conn.execute(
                "SELECT function, COUNT(*) AS total_entries, SUM(hit_count) AS total_hits "
                "FROM llm_response_cache WHERE expires_at > datetime('now') GROUP BY function"
            )
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return [dict(zip([d[0] for d in conn.execute(
            "SELECT function,COUNT(*),SUM(hit_count),ROUND(SUM(hit_count)*100.0/COUNT(*),1),"
            "MAX(0,SUM(hit_count)-COUNT(*)),COALESCE(SUM(cache_read_input_tokens),0),"
            "COALESCE(SUM(cache_creation_input_tokens),0) "
            "FROM llm_response_cache WHERE expires_at>datetime('now') GROUP BY function LIMIT 0"
        ).description], row)) for row in rows]
    except Exception:
        from tools.cache_savings.savings import get_savings_stats
        return get_savings_stats(conn)["by_function"]


def _entries_adapter(conn: Any) -> list[dict]:
    """Return non-expired llm_response_cache rows (content truncated)."""
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT cache_key, function, model_id, provider,
                   input_tokens, output_tokens,
                   cache_creation_input_tokens, cache_read_input_tokens,
                   hit_count, created_at, expires_at
            FROM llm_response_cache
            WHERE expires_at > datetime('now')
            ORDER BY hit_count DESC
            LIMIT 200
            """
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def _get_stats_simple(conn: Any) -> list[dict]:
    """Simplified stats adapter that always works."""
    from tools.cache_savings.savings import get_savings_stats
    return get_savings_stats(conn)["by_function"]


def _by_provider_adapter(conn: Any) -> list[dict]:
    """Per-provider prefix-cache effectiveness rows (cch-obs-01).

    `cached_share_pct` and `usd_saved` are deliberately NULL rather than 0 for
    a provider with no traffic, no reported counters, or no bill — so an IQE
    query that filters or sorts on them cannot silently rank "we never
    measured this" alongside "this measured zero".
    """
    try:
        from tools.cache_savings.by_provider import get_provider_effectiveness
        return get_provider_effectiveness(conn=conn).get("providers", [])
    except Exception:
        return []


register_collection("cache.stats",       _get_stats_simple)
register_collection("cache.entries",     _entries_adapter)
register_collection("cache.by_provider", _by_provider_adapter)
