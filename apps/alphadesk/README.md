# FathomDesk — AI-Powered Multi-Agent Trading Intelligence

FathomDesk is a commercial trading intelligence platform that uses multi-agent AI to analyze markets and generate trading signals. Built on the ICDEV™ FORGE framework.

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

# Run analysis
python tools/trading/runner.py --ticker AAPL --json

# Start dashboard
python tools/trading/dashboard/app.py --port 5100
```

## LLM Configuration

- **Primary**: Ollama qwen3.5 (local, free)
- **Secondary**: Claude Sonnet (API, for review)
- **Routing**: Scanner tier for analysts, Worker tier for debate, Planner tier for approval

## Market Data

- **Provider**: Alpaca Markets (paper trading)
- **Assets**: Equities + Crypto
- **Integration**: via ICDEV™ DataBridge SaaSBaseConnector

## Disclaimer

This is a paper trading research platform. Not financial advice.
Past performance does not guarantee future results.
