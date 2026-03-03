# Tools Manifest — SignalForge

CUI // SP-CTI

## Trading Tools

| Tool | File | Purpose |
|------|------|---------|
| Feature Engineer | `tools/trading/feature_engineer.py` | Compute 25 deterministic features from OHLCV |
| Label Generator | `tools/trading/label_generator.py` | Create LONG/SHORT/FLAT labels via TP/SL simulation |
| Model Trainer | `tools/trading/model_trainer.py` | XGBoost/LightGBM training with walk-forward CV |
| Backtester | `tools/trading/backtester.py` | Walk-forward validation + Monte Carlo simulation |
| Risk Engine | `tools/trading/risk_engine.py` | Hard TP/SL, position sizing, circuit breakers |
| Signal Generator | `tools/trading/signal_generator.py` | Main prediction loop — features → model → signal |
| Trade Journal | `tools/trading/trade_journal.py` | Append-only SQLite trade log with SHA-256 hash chain |
| NT8 Bridge | `tools/trading/nt8_bridge.py` | gRPC client for NinjaTrader 8 market data + orders |
| Data Pipeline | `tools/trading/data_pipeline.py` | End-to-end orchestrator: load → filter → features → labels |

## Core ICDEV Tools (inherited)

| Tool | File | Purpose |
|------|------|---------|
| DB Init | `tools/db/init_signalforge_db.py` | Initialize SignalForge database |
| Dashboard | `tools/dashboard/app.py` | Flask web dashboard |
| Memory Read | `tools/memory/memory_read.py` | Load long-term memory |
| Memory Write | `tools/memory/memory_write.py` | Write to daily log + DB |
| Health Check | `tools/testing/health_check.py` | System health verification |

## CLI Quick Reference

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

# NT8 bridge
python tools/trading/nt8_bridge.py --connect --json
```
