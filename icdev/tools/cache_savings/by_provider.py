# CUI // SP-CTI
"""Per-provider prompt-cache effectiveness (cch-obs-01).

Answers the question one aggregate hit rate cannot: IS prefix caching working,
on WHICH provider, and is it better or worse than last week.

Reads ``ai_telemetry`` — the per-call LLM ledger that cch-tel-01 taught to
record ``cache_creation_input_tokens`` / ``cache_read_input_tokens`` — NOT
``llm_response_cache``. Those are different questions:
``llm_response_cache`` answers "did we avoid an LLM call entirely", and it holds
a row only for responses that were themselves response-cached, so it can never
describe a provider that served cached PREFIX tokens on a live call. Prefix
caching lives in the per-call ledger or nowhere.

THREE HONESTY RULES, each of which exists because collapsing it produced a
false claim on this platform:

1. A provider with no traffic reads ``no_data``, never 0%. "Nobody called it"
   and "it cached nothing" are different claims and only one is a defect.
2. A provider whose transport never reports cache counters reads
   ``unreported``, never 0%. `claude-cli` carried 626 calls on the live board;
   Claude Code caches aggressively on the far side of it and returns no usage
   counters, so a 0% there would be a fabrication about a working cache.
3. A provider with no bill (Ollama and friends — ``usd_basis: local``) shows no
   dollars at all. ``$0.00`` renders identically to a provider that saved
   nothing, and self-hosted inference cannot save money it never spent. Its
   observed latency is reported instead.

And one correctness rule that the single aggregate got silently wrong:
providers disagree about whether ``input_tokens`` INCLUDES the cached tokens.
Anthropic and Bedrock report them disjointly (total prompt = input + read +
write); OpenAI and Azure report ``cached_tokens`` as a SUBSET of
``prompt_tokens``. Summing both shapes into one rate double-counts every
OpenAI cached token. ``token_accounting`` in ``args/cache_effectiveness.yaml``
is what keeps the two apart.

Usage::

    python tools/cache_savings/by_provider.py --json
    python tools/cache_savings/by_provider.py --window-days 30
    python tools/cache_savings/by_provider.py --provider anthropic --json
"""

from __future__ import annotations

import sys
from pathlib import Path

# Run by path (`python tools/cache_savings/by_provider.py`) and sys.path[0] is
# this file's own directory, so `from tools...` below raises ModuleNotFoundError
# and the usage lines above fail exactly as printed. parents[2] is whatever
# holds this file's `tools` package: the repo root in tools/, and <repo>/icdev
# in the icdev/ mirror.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

import argparse  # noqa: E402
import json  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from typing import Any, Optional  # noqa: E402

log = get_logger("icdev.cache_savings.by_provider")

CONFIG_PATH = _REPO_ROOT / "args" / "cache_effectiveness.yaml"

#: Per-provider cache status. Four values, because four things are true of a
#: provider and only one of them is "caching is broken".
STATUS_NO_DATA = "no_data"              # zero calls in the window
STATUS_UNREPORTED = "unreported"        # calls, but the transport reports no counters
STATUS_NO_CACHE_HITS = "no_cache_hits"  # calls, counters reported, all zero — a real 0%
STATUS_CACHING = "caching"              # cache tokens observed

STATUS_LABEL = {
    STATUS_NO_DATA: "no data",
    STATUS_UNREPORTED: "not reported",
    STATUS_NO_CACHE_HITS: "no cache hits",
    STATUS_CACHING: "caching",
}

STATUS_DETAIL = {
    STATUS_NO_DATA: "no calls to this provider in the window — this is not a zero hit rate",
    STATUS_UNREPORTED: (
        "this provider's transport returns no cache token counts, so the hit "
        "rate is unknown rather than zero — caching may well be working upstream"
    ),
    STATUS_NO_CACHE_HITS: "calls were made and cache counts were reported as zero — a measured 0%",
    STATUS_CACHING: "",
}

#: Why a provider shows no dollars.
USD_PRICED = "priced"
USD_LOCAL = "local"
USD_UNPRICED = "unpriced"

USD_DETAIL = {
    USD_LOCAL: "self-hosted inference is not billed per token — latency is the effect to watch, not dollars",
    USD_UNPRICED: "no per-token price is declared for this provider in args/cache_effectiveness.yaml",
}

#: How input_tokens relates to cached tokens.
ACCT_DISJOINT = "disjoint"
ACCT_INCLUSIVE = "inclusive"
ACCT_NA = "n_a"

TREND_IMPROVED = "improved"
TREND_WORSENED = "worsened"
TREND_FLAT = "flat"
TREND_NO_BASELINE = "no_baseline"

#: Percentage points of change below which the trend reads flat rather than
#: inventing a direction out of sampling noise.
_TREND_EPSILON = 0.5

_DEFAULT_WINDOW_DAYS = 7
_DEFAULT_TOP_PROVIDERS = 20


def _load_config() -> dict:
    """Load args/cache_effectiveness.yaml. A missing file is not fatal."""
    try:
        import yaml

        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        # Degrade to "every provider is unknown", which reports `unreported`
        # rather than fabricating a 0% for providers we can no longer classify.
        log.warning("cache_effectiveness config unreadable (%s) — every provider reads unknown", exc)
        return {}


def _provider_claim(cfg: dict, provider: str) -> dict:
    """Merge the declared claim for one provider over the configured defaults."""
    defaults = {
        "capability": "unknown",
        "token_accounting": ACCT_NA,
        "reports_cache_tokens": None,
        "usd_basis": USD_UNPRICED,
    }
    defaults.update(cfg.get("defaults") or {})
    claim = dict(defaults)
    claim.update((cfg.get("providers") or {}).get(provider) or {})
    claim["declared"] = provider in (cfg.get("providers") or {})
    return claim


def _classify(calls: int, cache_read: int, cache_write: int, reports: Optional[bool]) -> str:
    """Decide which of the four states a provider is in.

    Measurement outranks declaration: a provider declared not to report cache
    tokens that nonetheless reports some is CACHING. The declaration only
    decides how to read a ZERO, which is the only ambiguous case.
    """
    if calls <= 0:
        return STATUS_NO_DATA
    if cache_read > 0 or cache_write > 0:
        return STATUS_CACHING
    # Zero. Only a provider KNOWN to report counters can be said to have
    # measured a real 0%; false and unknown both mean we never saw a number.
    return STATUS_NO_CACHE_HITS if reports is True else STATUS_UNREPORTED


def _split_tokens(accounting: str, input_tokens: int, cache_read: int, cache_write: int) -> tuple:
    """Return (prompt_tokens_total, uncached_input_tokens).

    The whole reason this function exists: `input_tokens` means two different
    things depending on the provider, and adding the two meanings together is
    how a cached OpenAI token gets counted twice.
    """
    if accounting == ACCT_INCLUSIVE:
        # cached_tokens is a SUBSET of prompt_tokens.
        total = input_tokens
        uncached = max(0, input_tokens - cache_read)
    elif accounting == ACCT_DISJOINT:
        # input_tokens excludes both cache reads and cache writes.
        total = input_tokens + cache_read + cache_write
        uncached = input_tokens
    else:
        # No cache tokens are reported for this provider, so there is nothing
        # to reconcile — the prompt is whatever input_tokens says.
        total = input_tokens
        uncached = input_tokens
    return total, uncached


def _usd_saved(claim: dict, cache_read: int, cache_write: int) -> Optional[float]:
    """Dollars saved by prefix caching, or None when there are none to claim.

    None is a first-class answer here. `local` has no bill and `unpriced` has
    no declared rate; rendering either as 0.0 would report a working cache as a
    failed one, which is the defect this module exists to remove.
    """
    if claim.get("usd_basis") != USD_PRICED:
        return None
    price = float(claim.get("input_usd_per_mtok") or 0.0) / 1_000_000
    if price <= 0:
        return None
    read_mult = float(claim.get("read_multiplier", 0.1))
    write_mult = float(claim.get("write_multiplier", 1.25))
    saved = cache_read * price * (1.0 - read_mult)
    premium = cache_write * price * (write_mult - 1.0)
    return round(max(0.0, saved - premium), 6)


def _trend(current: Optional[float], previous: Optional[float]) -> dict:
    """Compare this window's cached share against the previous equal window."""
    if current is None or previous is None:
        return {
            "direction": TREND_NO_BASELINE,
            "previous_cached_share_pct": previous,
            "delta_pct_points": None,
        }
    delta = round(current - previous, 2)
    if abs(delta) < _TREND_EPSILON:
        direction = TREND_FLAT
    else:
        direction = TREND_IMPROVED if delta > 0 else TREND_WORSENED
    return {
        "direction": direction,
        "previous_cached_share_pct": previous,
        "delta_pct_points": delta,
    }


def _query_window(conn: Any, start: str, end: str) -> dict:
    """Aggregate ai_telemetry per provider between two ISO timestamps.

    Authored PG-native (``%s``); ``translate_sql`` rewrites the placeholders for
    the SQLite init-fallback path. A bare ``?`` works but logs a warning on
    every call, and the house rule is that runtime SQL targets PostgreSQL.
    """
    rows = conn.execute(
        """
        SELECT
            provider,
            COUNT(*)                                        AS calls,
            COALESCE(SUM(input_tokens), 0)                  AS input_tokens,
            COALESCE(SUM(output_tokens), 0)                 AS output_tokens,
            COALESCE(SUM(cache_read_input_tokens), 0)       AS cache_read,
            COALESCE(SUM(cache_creation_input_tokens), 0)   AS cache_write,
            COALESCE(AVG(latency_ms), 0)                    AS avg_latency_ms
        FROM ai_telemetry
        WHERE created_at >= %s AND created_at < %s
        GROUP BY provider
        """,
        (start, end),
    ).fetchall()

    out = {}
    for row in rows:
        provider = row[0] or "unknown"
        out[provider] = {
            "calls": int(row[1] or 0),
            "input_tokens": int(row[2] or 0),
            "output_tokens": int(row[3] or 0),
            "cache_read": int(row[4] or 0),
            "cache_write": int(row[5] or 0),
            "avg_latency_ms": round(float(row[6] or 0.0), 1),
        }
    return out


def _cached_share(status: str, prompt_total: int, cache_read: int) -> Optional[float]:
    """Cached share of prompt tokens, or None when the question is unanswerable.

    None for `no_data` and `unreported` is the point of the whole card: those
    two states have no rate, and printing 0.0 for them is the conflation the
    acceptance criteria forbid.
    """
    if status in (STATUS_NO_DATA, STATUS_UNREPORTED):
        return None
    if prompt_total <= 0:
        return 0.0
    return round(cache_read / prompt_total * 100, 2)


def get_provider_effectiveness(
    conn: Any = None,
    window_days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Per-provider prompt-cache effectiveness over a window, with a trend.

    Returns::

        {
          window_days, window_start, window_end, measurable (bool),
          unmeasurable_reason (str),
          providers: [ { provider, capability, status, status_label,
                         status_detail, calls, prompt_tokens, cached_input_tokens,
                         uncached_input_tokens, cache_write_tokens,
                         cached_share_pct (float|None), usd_saved (float|None),
                         usd_basis, usd_detail, avg_latency_ms,
                         token_accounting, declared, trend {...} } ],
          totals: { providers_caching, providers_no_cache_hits,
                    providers_unreported, providers_no_data,
                    usd_saved_total, usd_saved_basis }
        }
    """
    if conn is None:
        from tools.db.storage import get_connection

        conn = get_connection()

    cfg = _load_config()
    if window_days is None:
        window_days = int(cfg.get("window_days") or _DEFAULT_WINDOW_DAYS)
    top_n = int(cfg.get("top_providers") or _DEFAULT_TOP_PROVIDERS)

    end_dt = now or datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=window_days)
    prev_start_dt = start_dt - timedelta(days=window_days)

    def _fmt(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    try:
        current = _query_window(conn, _fmt(start_dt), _fmt(end_dt))
        previous = _query_window(conn, _fmt(prev_start_dt), _fmt(start_dt))
    except Exception as exc:  # noqa: BLE001
        log.debug("per-provider cache query failed: %s", exc)
        return _unmeasurable(
            window_days,
            _fmt(start_dt),
            _fmt(end_dt),
            f"ai_telemetry could not be queried: {exc}",
        )

    # A database with no operating history makes every provider look inert. A
    # fresh worktree or an ephemeral CI database must report UNMEASURABLE
    # rather than fabricating a wall of `no_data` findings.
    if not current and not previous:
        return _unmeasurable(
            window_days,
            _fmt(start_dt),
            _fmt(end_dt),
            "ai_telemetry holds no rows in this window or the previous one — "
            "this database has no operating history to measure",
        )

    # Every provider seen in EITHER window, plus every provider the config
    # declares. A declared provider with no traffic is exactly the `no_data`
    # case the card must show, so it cannot be omitted for having no rows.
    names = set(current) | set(previous) | set((cfg.get("providers") or {}).keys())

    providers = []
    for name in sorted(names):
        cur = current.get(name) or {
            "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read": 0, "cache_write": 0, "avg_latency_ms": 0.0,
        }
        claim = _provider_claim(cfg, name)
        accounting = claim.get("token_accounting") or ACCT_NA
        reports = claim.get("reports_cache_tokens")

        status = _classify(cur["calls"], cur["cache_read"], cur["cache_write"], reports)
        prompt_total, uncached = _split_tokens(
            accounting, cur["input_tokens"], cur["cache_read"], cur["cache_write"]
        )
        share = _cached_share(status, prompt_total, cur["cache_read"])

        prev = previous.get(name)
        prev_share = None
        if prev:
            prev_status = _classify(prev["calls"], prev["cache_read"], prev["cache_write"], reports)
            prev_total, _ = _split_tokens(
                accounting, prev["input_tokens"], prev["cache_read"], prev["cache_write"]
            )
            prev_share = _cached_share(prev_status, prev_total, prev["cache_read"])

        usd_basis = claim.get("usd_basis") or USD_UNPRICED
        usd = _usd_saved(claim, cur["cache_read"], cur["cache_write"]) if status == STATUS_CACHING else (
            0.0 if (status == STATUS_NO_CACHE_HITS and usd_basis == USD_PRICED) else None
        )

        providers.append({
            "provider": name,
            "capability": claim.get("capability") or "unknown",
            "declared": bool(claim.get("declared")),
            "token_accounting": accounting,
            "status": status,
            "status_label": STATUS_LABEL[status],
            "status_detail": STATUS_DETAIL[status],
            "calls": cur["calls"],
            "prompt_tokens": prompt_total,
            "cached_input_tokens": cur["cache_read"],
            "uncached_input_tokens": uncached,
            "cache_write_tokens": cur["cache_write"],
            "cached_share_pct": share,
            "usd_saved": usd,
            "usd_basis": usd_basis,
            "usd_detail": USD_DETAIL.get(usd_basis, ""),
            # Reported for every provider, but it is the ONLY effect a `local`
            # provider has to show. It is an observation, not a cache-attributed
            # saving — there is no uncached control run to compare against.
            "avg_latency_ms": cur["avg_latency_ms"],
            "trend": _trend(share, prev_share),
        })

    # Busiest first, but a provider with traffic always outranks one without,
    # so the `no_data` rows sink to the bottom instead of interleaving.
    providers.sort(key=lambda p: (p["calls"], p["cached_input_tokens"]), reverse=True)
    providers = providers[:top_n]

    counts = {s: 0 for s in (STATUS_CACHING, STATUS_NO_CACHE_HITS, STATUS_UNREPORTED, STATUS_NO_DATA)}
    for p in providers:
        counts[p["status"]] += 1

    usd_total = round(sum(p["usd_saved"] or 0.0 for p in providers), 6)

    return {
        "window_days": window_days,
        "window_start": _fmt(start_dt),
        "window_end": _fmt(end_dt),
        "measurable": True,
        "unmeasurable_reason": "",
        "providers": providers,
        "totals": {
            "providers_caching": counts[STATUS_CACHING],
            "providers_no_cache_hits": counts[STATUS_NO_CACHE_HITS],
            "providers_unreported": counts[STATUS_UNREPORTED],
            "providers_no_data": counts[STATUS_NO_DATA],
            "usd_saved_total": usd_total,
            # Deliberately NOT a platform-wide hit rate. Averaging a rate over
            # providers with different token accounting, different caching
            # mechanisms and different billing is the blur this card replaces.
            "usd_saved_basis": "priced providers only; local and unpriced providers contribute no dollars",
        },
    }


def _unmeasurable(window_days: int, start: str, end: str, reason: str) -> dict:
    """A shape that says 'we could not measure', never 'we measured zero'."""
    return {
        "window_days": window_days,
        "window_start": start,
        "window_end": end,
        "measurable": False,
        "unmeasurable_reason": reason,
        "providers": [],
        "totals": {
            "providers_caching": 0,
            "providers_no_cache_hits": 0,
            "providers_unreported": 0,
            "providers_no_data": 0,
            "usd_saved_total": None,
            "usd_saved_basis": "unmeasurable",
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Per-provider prompt-cache effectiveness")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--window-days", type=int, default=None, help="reporting window (default: config)")
    parser.add_argument("--provider", default=None, help="restrict output to one provider")
    args = parser.parse_args(argv)

    result = get_provider_effectiveness(window_days=args.window_days)
    if args.provider:
        result["providers"] = [p for p in result["providers"] if p["provider"] == args.provider]

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    # ASCII only below: this prints to a Windows console under cp1252 as often
    # as to a UTF-8 terminal, and an em dash renders there as a replacement
    # glyph in the exact column that is supposed to read "no answer".
    print("CUI // SP-CTI")
    if not result["measurable"]:
        print(f"UNMEASURABLE - {result['unmeasurable_reason']}")
        return 0

    print(
        f"Per-provider prompt cache - {result['window_days']}d window "
        f"({result['window_start']} to {result['window_end']})"
    )
    print()
    header = f"{'provider':<20} {'capability':<11} {'status':<14} {'calls':>7} {'cached':>10} {'share':>8} {'saved':>12} {'trend':<12}"
    print(header)
    print("-" * len(header))
    for p in result["providers"]:
        share = "n/a" if p["cached_share_pct"] is None else f"{p['cached_share_pct']:.2f}%"
        if p["usd_saved"] is None:
            saved = "n/a (local)" if p["usd_basis"] == USD_LOCAL else "n/a"
        else:
            saved = f"${p['usd_saved']:.4f}"
        trend = p["trend"]["direction"]
        if p["trend"]["delta_pct_points"] is not None:
            trend = f"{trend} {p['trend']['delta_pct_points']:+.1f}"
        print(
            f"{p['provider']:<20} {p['capability']:<11} {p['status_label']:<14} "
            f"{p['calls']:>7} {p['cached_input_tokens']:>10,} {share:>8} {saved:>12} {trend:<12}"
        )
    t = result["totals"]
    print()
    print(
        f"caching: {t['providers_caching']}   no cache hits: {t['providers_no_cache_hits']}   "
        f"not reported: {t['providers_unreported']}   no data: {t['providers_no_data']}"
    )
    print(f"saved: ${t['usd_saved_total']:.4f} ({t['usd_saved_basis']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
