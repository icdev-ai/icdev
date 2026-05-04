# CUI // SP-CTI
"""FathomDesk backtester — signal scoring (precision / recall / F1) +
event-driven OHLCV replay with FIFO position tracking and realized P&L.

score(signals) — unchanged classification metrics helper.
BacktestSession.run() — bar-by-bar replay with FIFO lot accounting.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_risk_free_rate() -> float:
    """Load annual risk-free rate from fathomdesk_config.yaml; default 0.04."""
    try:
        import yaml
        config_path = BASE_DIR / "args" / "fathomdesk_config.yaml"
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return float(cfg.get("risk_free_rate", 0.04))
    except Exception:
        return 0.04


def _compute_backtest_metrics(
    equity_curve: list[float],
    base_capital: float,
    risk_free_rate: float,
) -> tuple[float, float, float]:
    """Return (sharpe_ratio, calmar_ratio, max_drawdown_pct).

    equity_curve: per-bar cumulative realized + unrealized P&L.
    base_capital: normalisation denominator (first bar close × qty_per_trade).
    risk_free_rate: annual rate, e.g. 0.04 for 4%.
    """
    n = len(equity_curve)
    if n < 2 or base_capital <= 0:
        return 0.0, 0.0, 0.0

    # Fractional daily returns relative to initial capital
    daily_returns = [
        (equity_curve[i] - equity_curve[i - 1]) / base_capital
        for i in range(1, n)
    ]

    nr = len(daily_returns)
    mean_r = sum(daily_returns) / nr
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / nr
    std_r = math.sqrt(variance) if variance > 0 else 0.0

    # Annualized Sharpe (252 trading days)
    daily_rf = risk_free_rate / 252.0
    sharpe = (mean_r - daily_rf) / std_r * math.sqrt(252.0) if std_r > 0 else 0.0

    # Max drawdown: peak-to-trough on equity curve, expressed as % of peak
    peak = equity_curve[0]
    max_dd_pct = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak * 100.0
            if dd > max_dd_pct:
                max_dd_pct = dd

    # Calmar: annualized return (decimal) / max drawdown (decimal)
    total_return = (equity_curve[-1] - equity_curve[0]) / base_capital
    ann_return = total_return * (252.0 / n)
    calmar = ann_return / (max_dd_pct / 100.0) if max_dd_pct > 0 else 0.0

    return round(sharpe, 4), round(calmar, 4), round(max_dd_pct, 4)


# ---------------------------------------------------------------------------
# FIFO lot helpers
# ---------------------------------------------------------------------------

@dataclass
class _Lot:
    qty: float
    cost_basis: float  # per-unit entry price
    opened_at: str     # ISO date string


def _close_fifo(
    lots: list[_Lot],
    qty: float,
    exit_price: float,
) -> tuple[float, float]:
    """Consume lots FIFO; return (realized_pnl, qty_actually_closed)."""
    remaining = qty
    pnl = 0.0
    while remaining > 0.0 and lots:
        lot = lots[0]
        take = min(lot.qty, remaining)
        pnl += take * (exit_price - lot.cost_basis)
        lot.qty -= take
        remaining -= take
        if lot.qty == 0.0:
            lots.pop(0)
    return round(pnl, 6), qty - remaining


def _sma_signal(closes: list[float], i: int, period: int = 20) -> str | None:
    """Return 'long' on golden cross, 'short' on death cross, else None.

    Golden cross: close moves from below SMA to at-or-above.
    Death cross:  close moves from at-or-above SMA to below.
    Requires at least period+1 bars of history.
    """
    if i < period:
        return None
    sma_now = sum(closes[i - period : i]) / period
    if i == period:
        return None  # no prior SMA to compare
    sma_prev = sum(closes[i - period - 1 : i - 1]) / period
    prev_close = closes[i - 1]
    curr_close = closes[i]
    if prev_close < sma_prev and curr_close >= sma_now:
        return "long"
    if prev_close >= sma_prev and curr_close < sma_now:
        return "short"
    return None


# ---------------------------------------------------------------------------
# BacktestSession
# ---------------------------------------------------------------------------

class BacktestSession:
    """Event-driven OHLCV replay with FIFO position tracking and realized P&L.

    Usage::

        session = BacktestSession()
        result = session.run("AAPL", "covered_call", "2023-01-01", "2023-12-31")
        print(result["realized_pnl"])
    """

    def __init__(self) -> None:
        from tools.fathomdesk.data_gateway import FathomDeskDataGateway
        self._gw = FathomDeskDataGateway()

    def run(
        self,
        ticker: str,
        strategy_id: str,
        start: str,
        end: str,
        signals: list[dict[str, Any]] | None = None,
        qty_per_trade: float = 1.0,
    ) -> dict[str, Any]:
        """Replay OHLCV bars bar-by-bar and compute realized P&L.

        Args:
            ticker: Equity symbol, e.g. ``"AAPL"``.
            strategy_id: Strategy tag (metadata; see ``STRATEGY_TYPES``).
            start: Window start date ``"YYYY-MM-DD"``.
            end: Window end date ``"YYYY-MM-DD"`` (passed as ``as_of_date``).
            signals: Optional pre-computed signal dicts.  Each must have
                ``date`` (``"YYYY-MM-DD"``) and ``direction``
                (``"long"``/``"short"``).  When omitted, an SMA-20 crossover
                rule is applied bar-by-bar.
            qty_per_trade: Units opened or closed per signal.

        Returns:
            Dict with keys: ``ticker``, ``strategy_id``, ``start``, ``end``,
            ``trades``, ``realized_pnl``, ``trade_count``, ``open_lots``.
            Each trade has ``date``, ``ticker``, ``direction``, ``qty``,
            ``exit_price``, and ``realized_pnl``.
        """
        bars = self._gw.historical_bars(ticker, period="max", as_of_date=end)
        bars = [b for b in bars if start <= b["date"][:10] <= end]
        bars.sort(key=lambda b: b["date"])

        if not bars:
            return self._empty_result(ticker, strategy_id, start, end)

        # Build date → direction lookup from caller-supplied signals
        sig_by_date: dict[str, str] = {}
        if signals:
            for s in signals:
                date = str(s.get("date") or s.get("signal_date") or "")[:10]
                if date:
                    sig_by_date[date] = str(s.get("direction", "long")).lower()

        lots: list[_Lot] = []
        trades: list[dict[str, Any]] = []
        closes = [b["close"] for b in bars]
        cum_realized_pnl = 0.0
        equity_curve: list[float] = []

        for i, bar in enumerate(bars):
            date = bar["date"][:10]
            close = bar["close"]

            if signals is not None:
                direction = sig_by_date.get(date)
            else:
                direction = _sma_signal(closes, i)

            if direction == "long":
                lots.append(_Lot(qty=qty_per_trade, cost_basis=close, opened_at=date))
            elif direction == "short" and lots:
                pnl, closed_qty = _close_fifo(lots, qty_per_trade, close)
                if closed_qty > 0.0:
                    cum_realized_pnl += pnl
                    trades.append(
                        {
                            "date": date,
                            "ticker": ticker,
                            "direction": "close_long",
                            "qty": closed_qty,
                            "exit_price": close,
                            "realized_pnl": pnl,
                        }
                    )

            unrealized = sum((close - l.cost_basis) * l.qty for l in lots)
            equity_curve.append(cum_realized_pnl + unrealized)

        realized_pnl = round(cum_realized_pnl, 6)
        open_lots_out = [
            {"qty": l.qty, "cost_basis": l.cost_basis, "opened_at": l.opened_at}
            for l in lots
        ]

        base_capital = max(bars[0]["close"] * qty_per_trade, 1e-9)
        risk_free_rate = _load_risk_free_rate()
        sharpe, calmar, max_dd_pct = _compute_backtest_metrics(
            equity_curve, base_capital, risk_free_rate
        )

        return {
            "ticker": ticker,
            "strategy_id": strategy_id,
            "start": start,
            "end": end,
            "trades": trades,
            "realized_pnl": realized_pnl,
            "trade_count": len(trades),
            "open_lots": open_lots_out,
            "sharpe_ratio": sharpe,
            "calmar_ratio": calmar,
            "max_drawdown_pct": max_dd_pct,
        }

    def _empty_result(
        self, ticker: str, strategy_id: str, start: str, end: str
    ) -> dict[str, Any]:
        return {
            "ticker": ticker,
            "strategy_id": strategy_id,
            "start": start,
            "end": end,
            "trades": [],
            "realized_pnl": 0.0,
            "trade_count": 0,
            "open_lots": [],
            "sharpe_ratio": 0.0,
            "calmar_ratio": 0.0,
            "max_drawdown_pct": 0.0,
        }


# ---------------------------------------------------------------------------
# Original signal-scoring helper — UNCHANGED
# ---------------------------------------------------------------------------

def score(signals: list[dict[str, Any]]) -> dict[str, float]:
    """Return precision, recall, and F1 for a list of directional signals.

    Each signal must have:
      direction  — 'long' or 'short'
      p1y        — actual 1-year return (float; > 0 means price rose)
      confidence — float 0-1 (informational; not used in classification)

    Positive class: direction == 'long'.
    Ground truth positive: p1y > 0.
    """
    tp = fp = fn = 0
    for sig in signals:
        predicted_long = str(sig.get("direction", "")).lower() == "long"
        actual_up = float(sig.get("p1y", 0.0)) > 0.0
        if predicted_long and actual_up:
            tp += 1
        elif predicted_long and not actual_up:
            fp += 1
        elif not predicted_long and actual_up:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return {"precision": precision, "recall": recall, "f1": f1}
