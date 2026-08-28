# CUI // SP-CTI
"""The durable record of calls the response cache avoided (cch-obs-07).

WHY THIS EXISTS. Every savings number on /cache-savings was derived live `FROM
llm_response_cache`. There is no savings table, so the figure was CUMULATIVE in intent and
VOLATILE in fact — a row's contribution vanished when the row did, and rows vanish routinely
(`ttl_seconds: 3600`, LRU eviction past `max_entries`, `invalidate()`).

`savings.py`'s own header names this shape for a different cause: the table was UNLOGGED and
PostgreSQL truncates unlogged tables on crash recovery, which "did not merely drop cached
responses (fine, they regenerate), it reset a CUMULATIVE metric to $0.0000 with no record it
had ever been anything else." That fixed the RESTART half. Expiry and eviction do the same
thing on an ordinary day.

WHAT A ROW MEANS: one LLM call that did not happen. Appended at hit time, so it outlives the
cache entry that caused it.

DOLLARS ARE PER PROVIDER, AND OFTEN CORRECTLY ABSENT. `savings.py` prices every provider at
Anthropic's list rate (`_IN = 3.00/1M`), which turns a free local Ollama call into a dollar
saving. This module asks `cache_effectiveness.yaml` instead — the same declared claims
`by_provider` uses — so:

    priced    a metered API: a real bill was avoided, usd_saved is a number
    local     Ollama: no bill exists, usd_saved is None
    unpriced  the Claude subscription: no per-token price, usd_saved is None

None, never 0.0, for the last two: a 0.0 reports a working cache as a failed one, which is
the defect cch-obs-01 removed from the per-provider view and which must not be reintroduced
here. `tokens_saved_*` are always real, whoever served the call — and for an unpriced
provider they are the whole of the answer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

log = get_logger("icdev.cache_savings.ledger")

TABLE = "llm_cache_savings_ledger"


def _claim(provider: str) -> dict:
    """The declared pricing claim for *provider*, from args/cache_effectiveness.yaml."""
    try:
        from tools.cache_savings.by_provider import _load_config, _provider_claim

        return _provider_claim(_load_config(), provider or "")
    except Exception as exc:  # noqa: BLE001 — an unreadable claim must not lose the row
        log.debug("provider claim unreadable for %r (%s); treating as unpriced", provider, exc)
        return {"usd_basis": "unpriced"}


def price_saving(
    provider: str, input_tokens: int, output_tokens: int
) -> tuple[Optional[float], str]:
    """Dollars avoided by not making this call, and the basis for that answer.

    Returns ``(usd_saved, usd_basis)``. ``usd_saved`` is None whenever no bill exists to
    avoid — that is a first-class answer, not a zero.
    """
    claim = _claim(provider)
    basis = str(claim.get("usd_basis") or "unpriced")
    if basis != "priced":
        return None, basis

    in_rate = claim.get("input_usd_per_mtok")
    out_rate = claim.get("output_usd_per_mtok", in_rate)
    if in_rate is None:
        # Declared priced with no rate is a config error, not a zero saving.
        log.warning("provider %r is declared priced but carries no input rate", provider)
        return None, "unpriced"
    usd = (int(input_tokens or 0) * float(in_rate)
           + int(output_tokens or 0) * float(out_rate or in_rate)) / 1_000_000
    return round(usd, 6), basis


def record_avoided_call(
    *,
    function: str,
    model_id: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    conn: Any = None,
) -> bool:
    """Append one row for a call the cache avoided. Never raises.

    Best-effort ON PURPOSE: a serving cache hit must not fail because bookkeeping did. A lost
    row understates the saving, which is the safe direction — the opposite would inflate a
    number the whole card exists to make trustworthy.
    """
    usd, basis = price_saving(provider, input_tokens, output_tokens)
    now = datetime.now(timezone.utc).isoformat()
    owns = conn is None
    try:
        if owns:
            from tools.db.storage import get_connection

            conn = get_connection()
        conn.execute(
            f"""INSERT INTO {TABLE}
                (occurred_at, function, model_id, provider,
                 tokens_saved_input, tokens_saved_output, usd_saved, usd_basis)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (now, function or "", model_id or "", provider or "",
             int(input_tokens or 0), int(output_tokens or 0), usd, basis),
        )
        conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001 — see the docstring
        log.debug("savings ledger append failed (%s); the cache hit still served", exc)
        return False
    finally:
        if owns and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def cumulative(conn: Any = None, since: Optional[str] = None) -> dict:
    """What the cache has saved, for all time or since *since*.

    THE FOUR NUMBERS ARE KEPT APART, because merging them is how a $0.00 gets reported for a
    cache that is working:

        avoided_calls     rows — always real
        tokens_saved_*    always real
        usd_saved         summed over PRICED rows only; None when there are none
        unpriced_calls    avoided calls that had no bill to avoid

    `usd_saved` is None rather than 0.0 when nothing priced was avoided. A deployment that
    runs entirely on Ollama and a Claude subscription has genuinely saved no dollars, and
    saying "$0.00" implies the cache did nothing — it avoided the calls, they just were not
    billed.
    """
    owns = conn is None
    out = {
        "avoided_calls": 0,
        "tokens_saved_input": 0,
        "tokens_saved_output": 0,
        "usd_saved": None,
        "priced_calls": 0,
        "unpriced_calls": 0,
        "measurable": False,
    }
    try:
        if owns:
            from tools.db.storage import get_connection

            conn = get_connection()
        where, params = "", ()
        if since:
            where, params = " WHERE occurred_at >= %s", (since,)
        row = conn.execute(
            f"""SELECT COUNT(*) AS n,
                       COALESCE(SUM(tokens_saved_input), 0)  AS ti,
                       COALESCE(SUM(tokens_saved_output), 0) AS to_,
                       SUM(CASE WHEN usd_basis = 'priced' THEN 1 ELSE 0 END) AS priced,
                       SUM(CASE WHEN usd_basis = 'priced' THEN usd_saved ELSE 0 END) AS usd
                FROM {TABLE}{where}""",
            params,
        ).fetchone()
        if row is None:
            return out
        d = dict(row) if not isinstance(row, dict) else row
        n = int(d.get("n") or 0)
        priced = int(d.get("priced") or 0)
        out.update(
            avoided_calls=n,
            tokens_saved_input=int(d.get("ti") or 0),
            tokens_saved_output=int(d.get("to_") or 0),
            priced_calls=priced,
            unpriced_calls=n - priced,
            # None, never 0.0, when nothing PRICED was avoided.
            usd_saved=round(float(d.get("usd") or 0.0), 4) if priced else None,
            measurable=True,
        )
        return out
    except Exception as exc:  # noqa: BLE001
        log.debug("savings ledger unreadable (%s)", exc)
        # measurable stays False: "could not read" must never render as "saved nothing".
        return out
    finally:
        if owns and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
