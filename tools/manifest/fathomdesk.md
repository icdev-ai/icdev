# FathomDesk

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## FathomDesk

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| OpenBB Gateway | tools/fathomdesk/openbb_gateway.py | OpenBB SDK wrapper; graceful fallback if not installed; 5 methods: price, fundamentals, options_chain, news, macro_indicator | --ticker SYM --method get_price --json | Method result or fallback response |
