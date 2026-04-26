# FathomDesk

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## FathomDesk

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| OpenBB Gateway | tools/fathomdesk/openbb_gateway.py | OpenBB SDK wrapper; graceful fallback if not installed; 5 methods: price, fundamentals, options_chain, news, macro_indicator | --ticker SYM --method get_price --json | Method result or fallback response |
| Cost Optimizer Reflex | tools/genesis/reflexes/cost_optimizer.py | Weekly LLM token spend audit; Haiku-eligible task detection; bloated prompt flagging. Hard rule: never flags Risk or Execution agents. Genesis-registered: weekly/168h, GREEN tier | config dict | recommendations_generated count |
