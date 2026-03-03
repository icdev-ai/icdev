# Trading Workflow — SignalForge

CUI // SP-CTI

## Purpose
End-to-end workflow for ES/MES futures signal generation using XGBoost/LightGBM
classification on 25 engineered features with hard TP/SL enforcement.

## Pipeline

```
Data → Features → Labels → Train → Backtest → Deploy → Signal → Journal
```

### Step 1: Data Pipeline
**Tool:** `tools/trading/data_pipeline.py`
**Input:** Raw OHLCV CSV from NinjaTrader 8 export
**Output:** `data/processed/labeled.csv`, `data/processed/features.csv`

```bash
python tools/trading/data_pipeline.py --input data/raw/ES_1min.csv --output data/processed/ --json
```

**Checks:**
- RTH filter applied (09:30-16:00 ET only)
- 25 features computed, NaN warmup rows dropped
- Labels generated via TP/SL simulation
- Label distribution balanced (watch for >70% FLAT)

### Step 2: Model Training
**Tool:** `tools/trading/model_trainer.py`
**Input:** `data/processed/labeled.csv`
**Output:** `models/best_model.json`

```bash
python tools/trading/model_trainer.py --data data/processed/labeled.csv --output models/best_model.json --json
```

**Checks:**
- Walk-forward CV (6 months train, 1 month test)
- Avg accuracy > 40% (better than random 33% for 3-class)
- No single class dominating predictions
- Top features make intuitive sense

### Step 3: Backtesting
**Tool:** `tools/trading/backtester.py`
**Input:** Model + features CSV
**Output:** Performance metrics, Monte Carlo results

```bash
python tools/trading/backtester.py --model models/best_model.json --data data/processed/labeled.csv --json
```

**Required Metrics:**
- Win rate > 45%
- R:R > 1.2 (NOT inverted like NT8-RL's 0.71)
- Profit factor > 1.3
- Sharpe > 0.5
- Max drawdown < 15% of equity
- Min 50 trades for statistical validity
- Monte Carlo P5 > 0 (survives worst-case shuffle)

**Red Flags:**
- Win rate > 70% = likely overfitting
- In-sample Sharpe >> out-of-sample Sharpe = overfitting
- All trades same direction = directional bias bug

### Step 4: Risk Validation
**Tool:** `tools/trading/risk_engine.py`
**Verify:** Hard TP/SL levels, position sizing, circuit breakers

```bash
python tools/trading/risk_engine.py --check --json
python tools/trading/risk_engine.py --size --entry 5200 --atr 12.5 --account 100000 --direction LONG --json
```

### Step 5: Live Deployment
**Tool:** `tools/trading/nt8_bridge.py` + `tools/trading/signal_generator.py`
**Prerequisite:** NT8 running with gRPC strategy loaded

```bash
python tools/trading/nt8_bridge.py --connect --json
python tools/trading/signal_generator.py --model models/best_model.json --bar data/raw/latest_bar.csv --json
```

### Step 6: Trade Journal
**Tool:** `tools/trading/trade_journal.py`
**Monitor:** Daily performance, hash chain integrity

```bash
python tools/trading/trade_journal.py --summary --json
python tools/trading/trade_journal.py --verify --json
```

## Configuration
All parameters in `args/trading_config.yaml`. Key settings:
- `model.lookahead_bars`: 20 (trade horizon)
- `risk.take_profit_pct`: 0.015 (1.5%)
- `risk.stop_loss_pct`: 0.010 (1.0%)
- `risk.max_daily_loss_pct`: 0.03 (3% circuit breaker)
- `backtest.walk_forward_train_months`: 6
- `backtest.monte_carlo_iterations`: 1000

## Critical Fixes from NT8-RL
1. **Hard TP/SL** — Enforced at bar level in backtester. SL checked BEFORE TP on same bar.
2. **25 features** — Down from 917. All deterministic, no forecasts.
3. **XGBoost** — Replaces 700K-param PPO. ~100 params on same data.
4. **No reward function** — Classification, not RL. No cascading penalties.
5. **No strategy validator** — Model learns what works, no predefined rules.
6. **Walk-forward only** — No random train/test split. No data leakage.

## Edge Cases
- If label distribution is >70% FLAT: lower `min_move_threshold` or increase `lookahead_bars`
- If win rate is <35%: check feature normalization, try LightGBM, tune hyperparameters
- If Sharpe decays >50% out-of-sample: reduce `n_estimators`, increase `min_child_weight`
- If no trades generated: check RTH filter, verify data timestamps are ET
