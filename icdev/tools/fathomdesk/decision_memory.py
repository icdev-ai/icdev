# CUI // SP-CTI
"""FathomDesk decision_memory — deferred reflection feedback loop.

Append-only markdown log of trading decisions with deferred outcome reflection.
On each same-ticker run the module:
  1. Fetches actual return for any pending decisions via data_gateway.
  2. Calls LLM for a 2–4 sentence reflection on each resolved outcome.
  3. Injects N same-ticker + M cross-ticker past lessons into risk_manager context.

Log path: data/fathomdesk_decisions.md
All writes use atomic temp-file replace to prevent corruption.

Public API::

    store_decision(ticker, date, decision)           # immediately persist, no LLM
    get_pending(ticker) -> list[dict]                # fetch unresolved entries
    update_with_return(ticker, date, raw, alpha)     # resolve + LLM reflection
    get_past_context(ticker, n_same, n_cross) -> str # prompt-ready context string
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os
import re
import tempfile
from pathlib import Path
from typing import Any

logger = get_logger(__name__)

_LOG_PATH = Path(__file__).parent.parent.parent / "data" / "fathomdesk_decisions.md"
_PENDING = "pending"
_RESOLVED = "resolved"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _log_path() -> Path:
    """Resolve the decision log path, honouring ``FATHOMDESK_DECISIONS_PATH``.

    Resolved per call rather than once at import so a test can redirect the log
    to a tmp dir. Without the override, any test that reaches arbitrate() writes
    fabricated decisions straight into the tracked data/fathomdesk_decisions.md,
    which the session auto-commit hook then commits.
    """
    override = os.environ.get("FATHOMDESK_DECISIONS_PATH")
    return Path(override) if override else _LOG_PATH


def _read_all() -> str:
    path = _log_path()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _write_all(content: str) -> None:
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _field(block: str, key: str) -> str:
    m = re.search(rf"^- {re.escape(key)}: (.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _make_entry(
    ticker: str,
    entry_date: str,
    direction: str,
    confidence: float,
    reasoning_summary: str,
    status: str = _PENDING,
    raw_return: float | None = None,
    alpha_return: float | None = None,
    reflection: str | None = None,
) -> str:
    """Render one markdown H2 entry block."""
    lines = [
        f"## [{entry_date}|{ticker}|{direction}|{status}]",
        f"- date: {entry_date}",
        f"- ticker: {ticker}",
        f"- direction: {direction}",
        f"- confidence: {confidence:.4f}",
        f"- reasoning_summary: {reasoning_summary[:300]}",
    ]
    if raw_return is not None:
        lines.append(f"- raw_return: {raw_return:.6f}")
    if alpha_return is not None:
        lines.append(f"- alpha_return: {alpha_return:.6f}")
    if reflection:
        safe = reflection.replace('"', "'").replace("\n", " ")
        lines.append(f'- reflection: "{safe}"')
    return "\n".join(lines) + "\n\n"


def _parse_entries(content: str, status_filter: str | None = None) -> list[dict[str, Any]]:
    """Parse all entries from the log.  Optionally filter by status."""
    results: list[dict[str, Any]] = []
    # Split on H2 headers; re.split keeps headers when using a capture group.
    # The result is [preamble, header, body, header, body, ...] — headers sit at
    # ODD indices. Walking from 0 read the preamble as a header and every body as
    # the next one, so no header ever matched: get_pending() returned [] against
    # a log holding 564 entries, and the whole deferred-reflection loop was inert.
    parts = re.split(r"(## \[[^\]]+\]\n)", content)
    i = 1
    while i < len(parts):
        header = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        i += 2
        hm = re.match(r"## \[(\d{4}-\d{2}-\d{2})\|([^|]+)\|([^|]+)\|(\w+)\]", header)
        if not hm:
            continue
        entry_date, ticker, direction, status = hm.groups()
        if status_filter and status != status_filter:
            continue
        block = header + body
        results.append(
            {
                "date": entry_date,
                "ticker": ticker,
                "direction": direction,
                "status": status,
                "confidence": float(_field(block, "confidence") or "0"),
                "reasoning_summary": _field(block, "reasoning_summary"),
                "raw_return": float(_field(block, "raw_return") or "0") if _field(block, "raw_return") else None,
                "alpha_return": float(_field(block, "alpha_return") or "0") if _field(block, "alpha_return") else None,
                "reflection": _field(block, "reflection").strip('"'),
                "_header": header,
                "_body": body,
            }
        )
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def store_decision(ticker: str, entry_date: str, decision: dict[str, Any]) -> None:
    """Append a pending decision entry immediately — no LLM call.

    Args:
        ticker: Ticker symbol (e.g. "AAPL").
        entry_date: ISO date string "YYYY-MM-DD".
        decision: dict with keys ``direction``, ``confidence``, ``reasoning``.
    """
    ticker = ticker.upper().strip()
    direction = str(decision.get("direction", "HOLD")).upper()
    confidence = float(decision.get("confidence", 0.0))
    reasoning_summary = str(decision.get("reasoning", ""))

    entry = _make_entry(ticker, entry_date, direction, confidence, reasoning_summary)
    _write_all(_read_all() + entry)
    logger.debug("decision_memory: stored [%s|%s|%s|pending]", entry_date, ticker, direction)


def get_pending(ticker: str) -> list[dict[str, Any]]:
    """Return all pending entries for *ticker* (oldest first).

    Each dict contains: ``date``, ``ticker``, ``direction``, ``confidence``,
    ``reasoning_summary``.
    """
    ticker = ticker.upper().strip()
    return [e for e in _parse_entries(_read_all(), _PENDING) if e["ticker"] == ticker]


def update_with_return(
    ticker: str,
    entry_date: str,
    raw_return: float,
    alpha_return: float,
) -> None:
    """Resolve a pending decision with outcome data and an LLM reflection.

    Locates the pending entry for (*ticker*, *entry_date*), invokes LLM for a
    2–4 sentence reflection on the outcome, then rewrites the block as resolved.
    Fails open — any error is logged and the entry is left as pending.

    Args:
        ticker: Ticker symbol.
        entry_date: ISO date string matching the original decision.
        raw_return: Actual holding-period price return.
        alpha_return: Return minus benchmark return (alpha).
    """
    ticker = ticker.upper().strip()
    content = _read_all()
    if not content:
        return

    entries = _parse_entries(content, _PENDING)
    target = next(
        (e for e in entries if e["ticker"] == ticker and e["date"] == entry_date),
        None,
    )
    if target is None:
        logger.debug("decision_memory: no pending entry [%s|%s]", entry_date, ticker)
        return

    reflection = _generate_reflection(
        ticker, entry_date, target["direction"],
        raw_return, alpha_return, target["reasoning_summary"],
    )
    new_entry = _make_entry(
        ticker, entry_date, target["direction"], target["confidence"],
        target["reasoning_summary"],
        status=_RESOLVED,
        raw_return=raw_return,
        alpha_return=alpha_return,
        reflection=reflection,
    )
    old_block = target["_header"] + target["_body"]
    new_content = content.replace(old_block, new_entry, 1)
    _write_all(new_content)
    logger.debug(
        "decision_memory: resolved [%s|%s] raw=%.4f alpha=%.4f",
        entry_date, ticker, raw_return, alpha_return,
    )


def get_past_context(ticker: str, n_same: int = 5, n_cross: int = 3) -> str:
    """Build a prompt-ready context string of past resolved decisions.

    Returns up to *n_same* resolved same-ticker decisions and *n_cross*
    cross-ticker decisions (most recent first).  Returns empty string if no
    resolved decisions exist.

    Args:
        ticker: Current ticker being analyzed.
        n_same: Max same-ticker resolved decisions to include.
        n_cross: Max cross-ticker resolved decisions to include.

    Returns:
        Formatted string ready for LLM prompt injection, or "" if none.
    """
    ticker = ticker.upper().strip()
    resolved = _parse_entries(_read_all(), _RESOLVED)
    if not resolved:
        return ""

    same = [e for e in resolved if e["ticker"] == ticker]
    cross = [e for e in resolved if e["ticker"] != ticker]

    # Most-recent first (entries are appended chronologically so reverse)
    same = same[-n_same:][::-1]
    cross = cross[-n_cross:][::-1]

    if not same and not cross:
        return ""

    lines: list[str] = ["[Past Decision Memory]"]
    if same:
        lines.append(f"Same-ticker ({ticker}) outcomes:")
        for d in same:
            raw = d["raw_return"] or 0.0
            alpha = d["alpha_return"] or 0.0
            outcome = "✓" if alpha >= 0 else "✗"
            refl = d["reflection"] or "no reflection"
            lines.append(
                f"  {d['date']} {d['direction']} → raw={raw:+.2%} "
                f"alpha={alpha:+.2%} {outcome} | {refl}"
            )
    if cross:
        lines.append("Cross-ticker lessons:")
        for d in cross:
            raw = d["raw_return"] or 0.0
            alpha = d["alpha_return"] or 0.0
            outcome = "✓" if alpha >= 0 else "✗"
            refl = d["reflection"] or "no reflection"
            lines.append(
                f"  {d['date']} {d['ticker']} {d['direction']} → raw={raw:+.2%} "
                f"alpha={alpha:+.2%} {outcome} | {refl}"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Return computation from OHLCV (called from risk_manager)
# ---------------------------------------------------------------------------

def compute_return_from_ohlcv(
    ticker: str,
    entry_date: str,
    as_of_date: str,
) -> tuple[float, float]:
    """Compute raw and alpha returns between *entry_date* and *as_of_date*.

    Fetches daily bars for *ticker* and SPY via FathomDeskDataGateway.
    Alpha = ticker_return - spy_return; falls back to (raw, 0.0) on any error.

    Returns:
        ``(raw_return, alpha_return)`` — both floats.
    """
    try:
        from tools.fathomdesk.data_gateway import FathomDeskDataGateway
        gw = FathomDeskDataGateway()
        bars = gw.historical_bars(ticker, period="6mo", interval="1d", as_of_date=as_of_date)
        if not bars:
            return 0.0, 0.0

        # Sort bars by date
        bars_sorted = sorted(bars, key=lambda b: str(b.get("date", "")))
        # Find entry bar (on or after entry_date)
        entry_bar = next((b for b in bars_sorted if str(b.get("date", "")) >= entry_date), None)
        exit_bar = bars_sorted[-1] if bars_sorted else None
        if not entry_bar or not exit_bar or entry_bar is exit_bar:
            return 0.0, 0.0

        entry_close = float(entry_bar.get("close", 0) or 0)
        exit_close = float(exit_bar.get("close", 0) or 0)
        if entry_close == 0.0:
            return 0.0, 0.0
        raw_return = (exit_close - entry_close) / entry_close

        # Benchmark: SPY for the same dates
        try:
            spy_bars = gw.historical_bars("SPY", period="6mo", interval="1d", as_of_date=as_of_date)
            spy_sorted = sorted(spy_bars, key=lambda b: str(b.get("date", "")))
            spy_entry = next((b for b in spy_sorted if str(b.get("date", "")) >= entry_date), None)
            spy_exit = spy_sorted[-1] if spy_sorted else None
            if spy_entry and spy_exit and spy_entry is not spy_exit:
                spy_entry_close = float(spy_entry.get("close", 0) or 0)
                spy_exit_close = float(spy_exit.get("close", 0) or 0)
                if spy_entry_close > 0:
                    spy_return = (spy_exit_close - spy_entry_close) / spy_entry_close
                    return raw_return, raw_return - spy_return
        except Exception as exc:
            logger.debug("decision_memory: SPY benchmark fetch failed: %s", exc)

        return raw_return, 0.0
    except Exception as exc:
        logger.debug("decision_memory: compute_return_from_ohlcv failed: %s", exc)
        return 0.0, 0.0


# ---------------------------------------------------------------------------
# LLM reflection (fail-open)
# ---------------------------------------------------------------------------

def _generate_reflection(
    ticker: str,
    entry_date: str,
    direction: str,
    raw_return: float,
    alpha_return: float,
    reasoning_summary: str,
) -> str:
    try:
        from tools.fathomdesk.llm_factory import get_llm
        from tools.llm.provider import LLMRequest

        correct = (
            (direction == "BUY" and raw_return > 0)
            or (direction == "SELL" and raw_return < 0)
        )
        outcome_word = "correct" if correct else "incorrect"
        prompt = (
            f"Ticker: {ticker}  Date: {entry_date}  Decision: {direction}\n"
            f"Raw return: {raw_return:+.4f}  Alpha vs SPY: {alpha_return:+.4f}\n"
            f"Original reasoning: {reasoning_summary[:200]}\n\n"
            f"The {direction} call was {outcome_word}. "
            "Write 2–4 sentences reflecting on what the reasoning got right or wrong "
            "and what lesson a portfolio manager should draw from this outcome. "
            "Be specific to the numbers. No bullet points."
        )
        llm = get_llm("decision_memory")
        response = llm(
            LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.3,
                agent_id="decision_memory",
                skip_injection_scan=True,
            )
        )
        return response.content.strip().replace("\n", " ")
    except Exception as exc:
        logger.debug("decision_memory: LLM reflection failed: %s", exc)
        return _fallback_reflection(direction, raw_return, alpha_return)


def _fallback_reflection(direction: str, raw_return: float, alpha_return: float) -> str:
    correct = (direction == "BUY" and raw_return > 0) or (direction == "SELL" and raw_return < 0)
    return (
        f"The {direction} call was {'correct' if correct else 'incorrect'}: "
        f"raw={raw_return:+.4f}, alpha={alpha_return:+.4f}. No LLM reflection available."
    )
