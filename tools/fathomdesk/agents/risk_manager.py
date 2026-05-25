# CUI // SP-CTI
"""FathomDesk risk_manager — portfolio-level arbitration + constraint enforcement.

Reads a DebateResult and applies three ordered gates before emitting a final
trading recommendation:

  Gate A — signal confidence floor (args/signal_thresholds.yaml min_confidence)
  Gate B — liquidity trap regime hard-block (ad_macro_regimes + ad_trap_events)
  Gate C — tax-lot wash-sale sizing penalty (ad_tax_wash_sale_flags)

Return shape::

    {
        "direction":     str,    # "BUY" | "SELL" | "HOLD"
        "confidence":    float,  # propagated from DebateResult (post-gate)
        "size_modifier": float,  # position size scalar [0.0–1.0]
        "reasoning":     str,    # narrative of all gates applied
    }
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from tools.db.storage import get_connection
from tools.fathomdesk.constants import STRATEGY_TYPES
from tools.fathomdesk.decision_memory import (
    compute_return_from_ohlcv,
    get_past_context,
    get_pending,
    store_decision,
    update_with_return,
)
from tools.fathomdesk.signal_generator import load_thresholds

if TYPE_CHECKING:
    from tools.fathomdesk.agents.debate_engine import DebateResult

logger = get_logger(__name__)

_UNIVERSE_YAML = Path(__file__).parent.parent.parent.parent / "args" / "fathomdesk_universe.yaml"


def _load_venue_map() -> dict[str, str]:
    """Build symbol→exchange-code map from fathomdesk_universe.yaml (fail-open)."""
    try:
        with _UNIVERSE_YAML.open(encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        result: dict[str, str] = {}
        for market in (cfg.get("international_tickers") or {}).values():
            exchange = market.get("exchange", "")
            for entry in market.get("tickers") or []:
                sym = (entry.get("symbol") or "").upper()
                if sym and exchange:
                    result[sym] = exchange
        return result
    except Exception:
        return {}


_VENUE_MAP: dict[str, str] = _load_venue_map()


def _resolve_venue(ticker: str) -> str:
    """Return exchange code for *ticker* using intl ticker config (e.g. TSX, TSE, HKEX, US)."""
    upper = ticker.upper()
    if upper in _VENUE_MAP:
        return _VENUE_MAP[upper]
    return ticker.rsplit(".", 1)[1] if "." in ticker else "US"


def _resolve_instrument_type(rec: dict[str, Any]) -> str:
    """Return 'option' if rec strategy is in STRATEGY_TYPES, else 'equity'."""
    return "option" if rec.get("strategy", "") in STRATEGY_TYPES else "equity"


_WASH_SALE_WINDOW_DAYS = 30
_LIQUIDITY_TRAP_REGIME = "liquidity_trap"
_MACRO_TRAP_PATTERN = "macro_liquidity_trap"


# ---------------------------------------------------------------------------
# DB helpers (all fail-open — never raise)
# ---------------------------------------------------------------------------

def _fetch_active_liquidity_trap() -> tuple[bool, float]:
    """Return (active, confidence) from ad_macro_regimes.

    Queries the most recent liquidity_trap row.  Fails open → (False, 0.0).
    """
    try:
        conn = get_connection()
        ph = "%s" if getattr(conn, "_dialect", "sqlite") == "postgresql" else "?"
        try:
            rows = conn.execute(
                f"SELECT active, confidence FROM ad_macro_regimes "  # nosec B608
                f"WHERE regime_type = {ph} "
                f"ORDER BY created_at DESC LIMIT 1",
                [_LIQUIDITY_TRAP_REGIME],
            ).fetchall()
            if rows:
                row = dict(rows[0])
                return bool(row.get("active")), float(row.get("confidence", 0.0))
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("RiskManager: ad_macro_regimes query failed: %s", exc)
    return False, 0.0


def _fetch_ticker_trap_pattern(ticker: str) -> bool:
    """Return True if ad_trap_events holds a macro_liquidity_trap for ticker."""
    try:
        conn = get_connection()
        ph = "%s" if getattr(conn, "_dialect", "sqlite") == "postgresql" else "?"
        try:
            rows = conn.execute(
                f"SELECT id FROM ad_trap_events "  # nosec B608
                f"WHERE ticker = {ph} AND pattern = {ph} "
                f"ORDER BY created_at DESC LIMIT 1",
                [ticker, _MACRO_TRAP_PATTERN],
            ).fetchall()
            return len(rows) > 0
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("RiskManager: ad_trap_events macro check failed: %s", exc)
    return False


def _count_wash_sale_flags(ticker: str, window_days: int = _WASH_SALE_WINDOW_DAYS) -> int:
    """Count wash-sale flags for *ticker* within the rolling *window_days* window.

    Returns 0 on any DB error (fail open).
    """
    try:
        conn = get_connection()
        ph = "%s" if getattr(conn, "_dialect", "sqlite") == "postgresql" else "?"
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        try:
            row = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM ad_tax_wash_sale_flags "  # nosec B608
                f"WHERE ticker = {ph} AND created_at >= {ph}",
                [ticker, cutoff],
            ).fetchone()
            return int((dict(row) if row else {}).get("cnt", 0))
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("RiskManager: ad_tax_wash_sale_flags query failed: %s", exc)
    return 0


def _write_decision_audit(
    ticker: str,
    as_of_date: str,
    debate_result: "DebateResult",
    rec: dict[str, Any],
) -> None:
    """Append one row to ad_decision_audit (fail-open, SEC Rule 17a-4 immutable)."""
    venue = _resolve_venue(ticker)
    instrument_type = _resolve_instrument_type(rec)
    mifid_ts = datetime.now(timezone.utc).isoformat()
    audit_id = f"ada-{uuid.uuid4().hex[:12]}"
    reports = debate_result.analyst_reports or {}
    try:
        conn = get_connection()
        ph = "%s" if getattr(conn, "_dialect", "sqlite") == "postgresql" else "?"
        try:
            conn.execute(
                f"INSERT INTO ad_decision_audit "  # nosec B608
                f"(id, ticker, as_of_date, "
                f"fundamentals_score, technical_score, sentiment_score, macro_score, "
                f"bull_confidence, bear_confidence, "
                f"final_direction, final_confidence, reasoning, "
                f"venue, instrument_type, mifid_timestamp) "
                f"VALUES ({','.join([ph] * 15)})",
                [
                    audit_id,
                    ticker,
                    as_of_date,
                    float(reports.get("fundamentals", {}).get("score", 0.0)),
                    float(reports.get("technical", {}).get("score", 0.0)),
                    float(reports.get("sentiment", {}).get("score", 0.0)),
                    float(reports.get("macro", {}).get("score", 0.0)),
                    float(debate_result.bull_score),
                    float(debate_result.bear_score),
                    rec["direction"],
                    float(rec["confidence"]),
                    rec.get("reasoning", ""),
                    venue,
                    instrument_type,
                    mifid_ts,
                ],
            )
            conn.commit()
            logger.debug("RiskManager: ad_decision_audit row written [%s]", audit_id)
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("RiskManager: ad_decision_audit write failed: %s", exc)


# ---------------------------------------------------------------------------
# RiskManager
# ---------------------------------------------------------------------------

class RiskManager:
    """Portfolio-level risk arbitration over a DebateResult.

    Instantiate once per ticker/session; call :meth:`arbitrate` for each result.
    """

    def __init__(self, ticker: str, as_of_date: str) -> None:
        self.ticker = ticker.upper().strip()
        self.as_of_date = as_of_date
        self._thresholds: dict[str, Any] = load_thresholds()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def _resolve_pending_decisions(self) -> None:
        """Resolve any pending same-ticker decisions from prior runs (fail-open).

        For each pending entry, fetches OHLCV to compute the holding-period
        return since the decision date, then persists the outcome + LLM
        reflection via update_with_return().
        """
        try:
            pending = get_pending(self.ticker)
            for entry in pending:
                raw, alpha = compute_return_from_ohlcv(
                    self.ticker, entry["date"], self.as_of_date
                )
                update_with_return(self.ticker, entry["date"], raw, alpha)
        except Exception as exc:
            logger.debug("RiskManager: pending resolution failed: %s", exc)

    def arbitrate(self, debate_result: "DebateResult") -> dict[str, Any]:
        """Apply all risk gates and return the final recommendation.

        Gates are applied in order; an early exit is taken as soon as
        size_modifier reaches 0.0 and direction is forced to HOLD.

        Args:
            debate_result: output of :class:`DebateEngine.run`.

        Returns:
            ``{"direction", "confidence", "size_modifier", "reasoning"}``
        """
        # Resolve outcomes for prior same-ticker decisions before gating
        self._resolve_pending_decisions()

        direction: str = debate_result.verdict      # "BUY" | "SELL" | "HOLD"
        confidence: float = debate_result.confidence
        size_modifier: float = 0.0 if direction == "HOLD" else 1.0
        reasons: list[str] = []

        # Inject past decision context into reasoning for downstream PM visibility
        past_ctx = get_past_context(self.ticker)
        if past_ctx:
            reasons.append(past_ctx)

        if debate_result.reasoning:
            reasons.append(debate_result.reasoning)

        # ---- Gate A: min_confidence floor --------------------------------
        min_conf = float(self._thresholds.get("min_confidence", 0.60))
        if confidence < min_conf:
            reasons.append(
                f"Gate A: confidence {confidence:.2f} below threshold "
                f"{min_conf:.2f} — verdict overridden to HOLD."
            )
            direction, size_modifier = "HOLD", 0.0

        # ---- Gate B: liquidity trap — hard-blocks SELL -------------------
        elif direction == "SELL":
            trap_active, trap_conf = _fetch_active_liquidity_trap()
            ticker_trap = _fetch_ticker_trap_pattern(self.ticker)
            if trap_active or ticker_trap:
                sources: list[str] = []
                if trap_active:
                    sources.append(f"ad_macro_regimes (conf={trap_conf:.0%})")
                if ticker_trap:
                    sources.append(f"ad_trap_events[{self.ticker}]")
                reasons.append(
                    f"Gate B: liquidity trap active [{', '.join(sources)}] — "
                    "SELL hard-blocked; direction overridden to HOLD."
                )
                direction, size_modifier = "HOLD", 0.0

        # ---- Gate C: wash-sale sizing penalty for SELL -------------------
        if direction == "SELL":
            flag_count = _count_wash_sale_flags(self.ticker, _WASH_SALE_WINDOW_DAYS)
            if flag_count > 0:
                # 0.25 reduction per flag, floored at 0.0
                penalty = min(1.0, flag_count * 0.25)
                size_modifier = max(0.0, size_modifier - penalty)
                reasons.append(
                    f"Gate C: {flag_count} wash-sale flag(s) within "
                    f"{_WASH_SALE_WINDOW_DAYS}d for {self.ticker} — "
                    f"size_modifier reduced by {penalty:.2f} → {size_modifier:.2f}."
                )
                if size_modifier == 0.0:
                    reasons.append(
                        "Gate C: size_modifier at 0.0 — effective HOLD "
                        "due to wash-sale constraint."
                    )
                    direction = "HOLD"

        rec = self._rec(direction, confidence, size_modifier, reasons)
        _write_decision_audit(self.ticker, self.as_of_date, debate_result, rec)
        try:
            store_decision(self.ticker, self.as_of_date, rec)
        except Exception as exc:
            logger.debug("RiskManager: decision_memory store failed: %s", exc)
        return rec

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rec(
        direction: str,
        confidence: float,
        size_modifier: float,
        reasons: list[str],
    ) -> dict[str, Any]:
        return {
            "direction": direction,
            "confidence": round(confidence, 4),
            "size_modifier": round(size_modifier, 4),
            "reasoning": " | ".join(r for r in reasons if r),
        }
