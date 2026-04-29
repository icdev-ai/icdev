# FathomDesk

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## FathomDesk

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Blueprint | tools/fathomdesk/blueprint.py | Flask blueprint for FathomDesk API: GET /api/bars (OHLCV bars via DataGateway, OpenBB→yfinance fallback) and GET /fathomdesk/api/traps (trap event history with ticker/type filters, max 200 rows). Registered via _mount_inline in tools/dashboard/api/__init__.py. | (library — registered in app factory) | JSON responses |
| OpenBB Gateway | tools/fathomdesk/openbb_gateway.py | OpenBB SDK wrapper; graceful fallback if not installed; 5 methods: price, fundamentals, options_chain, news, macro_indicator | --ticker SYM --method get_price --json | Method result or fallback response |
| Signal Generator | tools/fathomdesk/signal_generator.py | Threshold-gated signal filtering — loads `args/signal_thresholds.yaml` and filters candidate signals by min_confidence (default 0.60) and min_score (default 0.50); caps output at max_signals (default 20). Falls back to hardcoded defaults on missing/unreadable YAML. Entry point: `generate(signals, thresholds=None)`. No LLM — fully deterministic. | N/A (library; `from tools.fathomdesk.signal_generator import generate`) | Filtered list of signal dicts |
| Signal Tuner | tools/fathomdesk/signal_tuner.py | Signal tuning utility — threshold optimisation with dry-run support. `dry_run(signals)` previews the full tuning pipeline (load_thresholds → filter_signals → score_backtest → emit_report) without writing to the database. Returns step descriptors, current threshold values, and input signal count. No LLM — fully deterministic. | N/A (library; `from tools.fathomdesk.signal_tuner import dry_run`) | Dict with dry_run flag, steps list, thresholds, and input_signals count |
| Cost Optimizer Reflex | tools/genesis/reflexes/cost_optimizer.py | Weekly LLM token spend audit; Haiku-eligible task detection; bloated prompt flagging. Hard rule: never flags Risk or Execution agents. Genesis-registered: weekly/168h, GREEN tier | config dict | recommendations_generated count |
