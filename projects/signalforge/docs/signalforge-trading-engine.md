# SignalForge — Trading Signal Engine

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Project | SignalForge (ICDEV Child App) |
| Title | XGBoost/LightGBM Trading Signal Engine for ES/MES Futures |
| Status | Implemented |
| Priority | P1 |
| Dependencies | ICDEV Phase 19 (Agentic Generation) |
| Author | ICDEV Architect Agent |
| Date | 2026-03-02 |

---

## 1. Problem Statement

NT8-RL is an 84K LOC PPO-based reinforcement learning trading system for ES/MES futures via NinjaTrader 8. It has a critically low win rate (<35%) and inverted risk-reward ratio (0.71:1 vs 1.5:1 target). Root causes identified through analysis:

1. **917 noisy features** — 880 lookback-expansion features, most redundant or noise
2. **700K model parameters on 80K training samples** — 9:1 param/sample ratio causing massive overfitting
3. **Cascading reward penalties** — PPO reward function creates a death spiral where the model learns to avoid trading entirely
4. **5 AI layers adding noise** — Swarm agents, DeepSeek reasoning, forecasting, adaptive training, strategy validation — each layer adds uncertainty without proven value
5. **TP/SL not enforced at bar level** — Backtester checks SL too late, doesn't hard-enforce TP
6. **Strategy validator rejects 80%+ of model output** — Predefined rules override model predictions

**Decision**: Complete rewrite as ICDEV child app using XGBoost/LightGBM classifier with 25 engineered features, no predefined strategy, hard TP/SL enforcement, RTH only, full GOTCHA compliance.

---

## 2. Goals

1. Generate ES/MES futures trading signals via XGBoost 3-class classifier (LONG/SHORT/FLAT)
2. Compute 25 deterministic features from 1-minute OHLCV bars
3. Label historical data via TP/SL simulation (not return-based)
4. Train with walk-forward cross-validation (no data leakage)
5. Backtest with Monte Carlo confidence intervals and overfitting detection
6. Enforce hard TP/SL at bar level with SL checked before TP on same bar
7. ATR-based position sizing with circuit breakers and trailing stops
8. Append-only trade journal with SHA-256 hash chain (CFTC Reg AT compliant)
9. NinjaTrader 8 gRPC bridge for live execution
10. Flask dashboard with equity curve, trade history, model analysis, backtest results

---

## 3. Architecture

### Pipeline
```
Data → Features → Labels → Train → Backtest → Deploy → Signal → Journal
```

### GOTCHA Layers

| Layer | SignalForge Implementation |
|-------|---------------------------|
| Goals | `goals/trading_workflow.md` — 6-step end-to-end workflow |
| Orchestration | Claude orchestrates tool execution order |
| Tools | 9 trading tools in `tools/trading/` |
| Args | `args/trading_config.yaml` — all tunable parameters |
| Context | Feature reference, model comparison tables |
| Hard Prompts | Dashboard templates with inline documentation |

### XGBoost vs PPO (Architecture Decision)

| Aspect | NT8-RL (PPO) | SignalForge (XGBoost) |
|--------|-------------|----------------------|
| Model Type | Policy gradient RL | Gradient boosting classifier |
| Parameters | ~700,000 | ~100 (trees) |
| Features | 917 (mostly noise) | 25 (hand-selected) |
| Training | Episode-based, reward shaping | Supervised, walk-forward CV |
| Interpretability | Black box | Feature importance (SHAP) |
| Overfitting Risk | 9:1 param/sample ratio | Built-in regularization |
| TP/SL | Learned (not enforced) | Hard-enforced at bar level |
| Strategy Rules | 80%+ rejected by validator | None — model learns |

---

## 4. Feature Set (25 Features)

| # | Feature | Category | Normalization |
|---|---------|----------|--------------|
| 1-4 | close, high, low, volume | Price/Volume | Raw |
| 5-7 | sma_20, sma_50, sma_200 | Moving Averages | (close - SMA) / close |
| 8 | rsi_14 | Momentum | 0-1 (divided by 100) |
| 9-11 | macd_line, macd_signal, macd_hist | Trend | / close |
| 12-13 | bb_upper_dist, bb_lower_dist | Volatility | / close |
| 14 | atr_14 | Volatility | / close |
| 15 | adx_14 | Trend Strength | 0-1 (divided by 100) |
| 16-17 | stoch_k, stoch_d | Momentum | 0-1 (divided by 100) |
| 18 | volume_sma_ratio | Volume | volume / SMA(volume) |
| 19-20 | dist_to_support, dist_to_resistance | S/R Levels | / close |
| 21 | regime | Market Regime | -1, 0, 1 |
| 22 | hour_of_day | Time | 0-1 (RTH normalized) |
| 23 | day_of_week | Time | 0-1 (Mon=0, Fri=1) |
| 24 | close_vs_vwap | VWAP | (close - VWAP) / close |
| 25 | bar_range_vs_atr | Volatility | (high - low) / ATR |

---

## 5. Database Schema

### trade_journal
| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PK | Auto-increment |
| trade_id | TEXT UNIQUE | Format: `sf-{uuid12}` |
| timestamp | TEXT | ISO 8601 |
| direction | TEXT | LONG or SHORT |
| entry_price | REAL | Entry price |
| exit_price | REAL | Exit price (NULL if open) |
| take_profit | REAL | TP level |
| stop_loss | REAL | SL level |
| position_size | INTEGER | Contracts |
| pnl_points | REAL | Points gained/lost |
| pnl_dollars | REAL | Dollar PnL ($50/point) |
| bars_held | INTEGER | Duration |
| exit_reason | TEXT | take_profit, stop_loss, timeout, eod |
| confidence | REAL | Model prediction confidence (0-1) |
| model_version | TEXT | Model identifier |
| signal_features | TEXT | JSON top features |
| status | TEXT | open, closed, cancelled |
| prev_hash | TEXT | Previous record's SHA-256 |
| record_hash | TEXT | This record's SHA-256 |
| created_at | TEXT | Insertion timestamp |

### daily_performance
| Column | Type | Purpose |
|--------|------|---------|
| trade_date | TEXT UNIQUE | Date |
| total_trades | INTEGER | Count |
| winning_trades | INTEGER | Wins |
| losing_trades | INTEGER | Losses |
| total_pnl | REAL | Dollar PnL |
| max_drawdown | REAL | Max DD |
| win_rate | REAL | Win % |
| avg_rr | REAL | Average R:R |

---

## 6. Configuration

All parameters in `args/trading_config.yaml`:

| Section | Key Parameters |
|---------|---------------|
| instrument | ES (1-minute bars, RTH 09:30-16:00 ET) |
| features | SMA periods [20,50,200], RSI 14, MACD 12/26/9, BB 20/2, ATR 14, ADX 14, Stoch 14/3/3 |
| model | XGBoost, max_depth=6, n_estimators=500, lr=0.01, lookahead=20 bars |
| risk | TP=1.5%, SL=1.0%, max daily loss=3%, max positions=1, ATR sizing |
| backtest | Walk-forward 6mo train/1mo test, 1000 MC iterations, min 50 trades |
| bridge | localhost:50051, 5s heartbeat |

---

## 7. CLI Commands

```bash
# Full pipeline
python tools/trading/data_pipeline.py --input data/raw/ES_1min.csv --output data/processed/ --json

# Train model
python tools/trading/model_trainer.py --data data/processed/labeled.csv --output models/best_model.json --json

# Backtest
python tools/trading/backtester.py --model models/best_model.json --data data/processed/labeled.csv --json

# Risk check
python tools/trading/risk_engine.py --check --json
python tools/trading/risk_engine.py --size --entry 5200 --atr 12.5 --account 100000 --direction LONG --json

# Generate signal
python tools/trading/signal_generator.py --model models/best_model.json --bar data/raw/latest_bar.csv --json

# Trade journal
python tools/trading/trade_journal.py --summary --json
python tools/trading/trade_journal.py --verify --json
```

---

## 8. Dashboard Pages

| Route | Page | Content |
|-------|------|---------|
| `/signalforge` | Main Dashboard | Stat grid, equity curve SVG, recent trades, hash chain verify |
| `/signalforge/backtest` | Backtest Results | Target metrics comparison, risk limits, CLI instructions |
| `/signalforge/journal` | Trade Journal | Full history table with 13 columns |
| `/signalforge/model` | Model Analysis | 25-feature reference, model config, XGBoost vs PPO comparison |

### API Endpoints
- `GET /api/signalforge/summary` — Performance summary JSON
- `GET /api/signalforge/equity-curve` — Equity curve data points
- `GET /api/signalforge/verify` — Hash chain verification
- `GET /api/signalforge/risk` — Current risk limits

---

## 9. Tools Manifest

| Tool | File | Purpose |
|------|------|---------|
| Feature Engineer | `tools/trading/feature_engineer.py` | Compute 25 features from OHLCV |
| Label Generator | `tools/trading/label_generator.py` | TP/SL simulation labeling |
| Model Trainer | `tools/trading/model_trainer.py` | XGBoost training + walk-forward CV |
| Backtester | `tools/trading/backtester.py` | Walk-forward + Monte Carlo |
| Risk Engine | `tools/trading/risk_engine.py` | TP/SL, sizing, circuit breakers |
| Signal Generator | `tools/trading/signal_generator.py` | Features → model → signal |
| Trade Journal | `tools/trading/trade_journal.py` | Append-only SQLite + hash chain |
| NT8 Bridge | `tools/trading/nt8_bridge.py` | gRPC client for NinjaTrader 8 |
| Data Pipeline | `tools/trading/data_pipeline.py` | End-to-end orchestrator |

---

## 10. Architecture Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| SF-1 | XGBoost over PPO/RL | ~100 params vs 700K; 3-class classifier; interpretable via SHAP |
| SF-2 | 25 features (down from 917) | All deterministic; no forecasts, no swarm guidance |
| SF-3 | No predefined strategy rules | Model learns from data; no validator rejecting 80%+ of output |
| SF-4 | Hard TP/SL at bar level | SL checked BEFORE TP on same bar; critical NT8-RL fix |
| SF-5 | Walk-forward only (no random split) | Prevents data leakage; realistic time-series evaluation |
| SF-6 | TP/SL simulation labeling | Labels reflect actual tradeable outcomes, not raw returns |
| SF-7 | RTH only (09:30-16:00 ET) | Reduces noise from extended hours; ES liquidity concentrated in RTH |
| SF-8 | SHA-256 hash chain journal | CFTC Reg AT compliant; tamper-evident append-only log |
| SF-9 | ATR-based position sizing | Adapts to current volatility; no static contract count |
| SF-10 | ICDEV child app (GOTCHA) | Full compliance scaffold; deterministic tools; LLM orchestration only |

---

## 11. Testing

### Unit Tests (116 tests, 6 files)

| File | Tests | Coverage |
|------|-------|----------|
| `test_feature_engineer.py` | 17 | SMA, EMA, RSI, MACD, BB, ATR, compute_features, load_ohlcv, filter_rth |
| `test_label_generator.py` | 12 | TP/SL labeling, return labeling, trend detection, threshold effects |
| `test_risk_engine.py` | 25 | Limits, sizing, trade risk, circuit breaker, trailing stop, EOD, validation |
| `test_trade_journal.py` | 16 | Hash chain, record/exit, summary, history, equity curve, verification |
| `test_backtester.py` | 18 | Trade simulation, metrics, Monte Carlo, overfitting score |
| `test_signal_generator.py` | 10 | Signal generation, TP/SL levels, class probs, edge cases |

### Required Backtest Metrics
| Metric | Threshold | NT8-RL Actual |
|--------|-----------|---------------|
| Win rate | > 45% | < 35% |
| R:R | > 1.2 | 0.71 |
| Profit factor | > 1.3 | < 1.0 |
| Sharpe | > 0.5 | Negative |
| Max drawdown | < 15% | > 20% |
| Min trades | >= 50 | Insufficient |
| Monte Carlo P5 | > 0 | Not computed |

### Red Flags
- Win rate > 70% = likely overfitting
- In-sample Sharpe >> out-of-sample = overfitting
- All trades same direction = directional bias bug

---

## 12. Security Considerations

- **Classification**: CUI // SP-CTI applied to all source files
- **Hash Chain**: SHA-256 tamper detection on trade journal (CFTC Reg AT)
- **Append-Only**: Trade journal uses append-only pattern (NIST AU compliant)
- **No Secrets in Config**: API keys, gRPC creds stored outside repository
- **Input Validation**: OHLCV data validated for missing columns, numeric types
- **Risk Limits**: Circuit breaker prevents runaway losses (3% daily cap)
- **EOD Flatten**: All positions closed at 15:55 ET (no overnight exposure)
