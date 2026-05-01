# CUI // SP-CTI
"""FathomDesk constants — BacktestResult dataclass and strategy type registry.

Strategy IDs mirror args/options_strategies.yaml (canonical source).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class BacktestResult:
    ticker: str
    strategy_id: str
    strategy_type: str
    sharpe_ratio: float
    calmar_ratio: float
    max_drawdown_pct: float
    win_rate: float
    ev_per_trade: float
    trade_count: int
    backtest_start: str
    backtest_end: str
    paper_default: bool = False
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


RATE_LIMIT_RETRY_MAX: int = 3

# Strategy IDs sourced from args/options_strategies.yaml.
STRATEGY_TYPES: list[str] = [
    "long_call",
    "long_put",
    "covered_call",
    "cash_secured_put",
    "bull_call_spread",
    "bear_put_spread",
    "iron_condor",
    "long_straddle",
    "long_call_butterfly",
    "iron_butterfly",
    "calendar_spread",
    "diagonal_spread",
    "calendar_butterfly",
    "risk_reversal_bull",
    "risk_reversal_bear",
    "broken_wing_butterfly_call",
    "broken_wing_butterfly_put",
    "jade_lizard",
    "call_backspread",
    "put_backspread",
]
