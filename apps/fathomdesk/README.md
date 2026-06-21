# FathomDesk — Market Intelligence Terminal

> **DISCLAIMER: Informational only. Not investment advice. No order routing.
> Data may be delayed. Past performance does not predict future results.**

## What is FathomDesk?

FathomDesk is a **read-only market-intelligence research terminal** built on the ICDEV™ FORGE
framework. It ingests market data, runs multi-agent AI analysis (fundamental, sentiment, news,
and technical), and surfaces structured research commentary. It does **not** route, place,
or simulate any orders.

## What FathomDesk does NOT do

- Execute, simulate, or route trades of any kind
- Connect to live brokerage accounts
- Provide investment advice or recommendations
- Guarantee data accuracy or timeliness

## Architecture

9 logical agents in a 5-layer DAG:

1. **Analysts** (4 parallel): Fundamental, Sentiment, News, Technical
2. **Debate**: Bull vs Bear multiperspectivity analysis
3. **Trader**: Signal generation with composite scoring
4. **Risk Manager**: Pre-trade validation
5. **Portfolio Manager**: Final approval gate

## Quick Start

```bash
# Initialize database
python tools/trading/db_init.py --json

# Run analysis on a ticker
python tools/trading/runner.py --ticker AAPL --json

# Start dashboard
python tools/trading/dashboard/app.py --port 5100
```

## LLM Configuration

- **Primary**: Ollama qwen3.5 (local, free, air-gap safe)
- **Secondary**: Claude Sonnet (API, for review)
- **Routing**: Scanner tier for analysts, Worker tier for debate, Planner tier for approval

## Data Sources & Limitations

- Market data provider configurable via `args/trading_config.yaml`
- All data carries a `source` + `as_of` (UTC) timestamp — see Phase 1 of the hardening plan
- Delayed or cached data is flagged visibly in the UI
- No hardcoded price tables; all numeric values are deterministic and traceable

## Package

FathomDesk ships as a marketplace add-on to the ICDEV™ platform (`apps/fathomdesk/`).
The underlying trading engine lives in `tools/trading/`.
