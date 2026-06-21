# Goal: Analyze Ticker

Run a full 5-layer multi-agent analysis cycle for a given ticker.

## Workflow
1. **Layer 1 (Parallel)**: Run 4 analysts — fundamental, sentiment, news, technical
2. **Layer 2 (Debate)**: Bull vs bear perspective scoring using ICDEV™'s INTaaS multiperspectivity
3. **Layer 3 (Signal)**: Generate composite trading signal with confidence
4. **Layer 4 (Risk)**: Pre-trade risk validation against portfolio limits
5. **Layer 5 (Approval)**: Portfolio manager approval gate

## Tools
- `tools/trading/analysts/fundamental.py`
- `tools/trading/analysts/sentiment.py`
- `tools/trading/analysts/news.py`
- `tools/trading/analysts/technical.py`
- `tools/trading/analysis/perspective_scorer.py`
- `tools/trading/analysis/signal_generator.py`
- `tools/trading/execution/risk_checker.py`

## Entry Point
```bash
python tools/trading/runner.py --ticker AAPL --json
```

## Expected Output
JSON with all layer results, final signal direction, and approval status.
