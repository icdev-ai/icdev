# Per-provider prompt-cache effectiveness — not one aggregate number (cch-obs-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/cache_savings/by_provider.py --json
python tools/cache_savings/by_provider.py --window-days 30
python tools/cache_savings/by_provider.py --provider anthropic --json
```

Reads ai_telemetry (cch-tel-01's per-call ledger), NOT llm_response_cache — that table
answers "was a call avoided outright" and only holds response-cached results, so it can
never describe cached INPUT tokens on a call that still happened.
FOUR zeroes the old single hit rate merged into one, of which only the third is a defect:
no_data (nobody called it) | unreported (transport reports no counters — claude-cli
carried 626 such calls) | no_cache_hits (a real measured 0%) | caching. cached_share_pct
is None, never 0.0, for the first two. usd_basis: local (Ollama) has no bill, so
usd_saved is None and latency is reported — $0.00 there reads as "caching failed" for a
cache that works and simply is not billed.
Token accounting is per provider and NEVER summed across shapes: Anthropic/Bedrock report
input_tokens DISJOINT from cache tokens, OpenAI/Azure report cached tokens as a SUBSET.
The same raw numbers give 28.57% vs 40.00%; averaging them double-counts every OpenAI
cached token — which is exactly what the one aggregate did. No blended rate is emitted.
A database with no operating history reports UNMEASURABLE, never a wall of no_data.
Claims are provider-keyed, NEVER model-keyed: args/cache_effectiveness.yaml
UI: /cache-savings -> "Prefix Cache by Provider"   API: /api/cache-savings/by-provider
