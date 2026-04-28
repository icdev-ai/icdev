# FathomDesk

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## FathomDesk

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| OpenBB Gateway | tools/fathomdesk/openbb_gateway.py | OpenBB SDK wrapper; graceful fallback if not installed; 5 methods: price, fundamentals, options_chain, news, macro_indicator | --ticker SYM --method get_price --json | Method result or fallback response |
| Signal Generator | tools/fathomdesk/signal_generator.py | Threshold-gated signal filtering — loads `args/signal_thresholds.yaml` and filters candidate signals by min_confidence (default 0.60) and min_score (default 0.50); caps output at max_signals (default 20). Falls back to hardcoded defaults on missing/unreadable YAML. Entry point: `generate(signals, thresholds=None)`. No LLM — fully deterministic. | N/A (library; `from tools.fathomdesk.signal_generator import generate`) | Filtered list of signal dicts |
| Cost Optimizer Reflex | tools/genesis/reflexes/cost_optimizer.py | Weekly LLM token spend audit; Haiku-eligible task detection; bloated prompt flagging. Hard rule: never flags Risk or Execution agents. Genesis-registered: weekly/168h, GREEN tier | config dict | recommendations_generated count |
