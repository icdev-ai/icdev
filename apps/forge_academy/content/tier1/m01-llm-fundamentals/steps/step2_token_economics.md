---
ontology_id: icdev:mission:m01-llm-fundamentals:step:2
step_class: icdev:Lesson
---

# Token Economics

Every character you send costs something. Every response you receive costs something. In production AI systems, tokens are currency — and understanding how they're counted is the difference between a $10/month tool and a $10,000/month infrastructure bill.

## What is a token?

A token is roughly **4 characters** or **¾ of a word** in English. LLMs don't see raw text — they see sequences of token IDs. The tokenizer (usually byte-pair encoding or SentencePiece) converts your text into those IDs before the model ever sees it.

```
"Hello, world!" → ["Hello", ",", " world", "!"] → [15496, 11, 995, 0]
```

Different words tokenize differently:
- `"cat"` → 1 token
- `"categorically"` → 3 tokens
- `"CUI//SP-CTI"` → 4+ tokens (special chars fragment more)

## Why tokenizers differ across model families

A tokenizer is **trained**, not defined. Each model family learns its own vocabulary from its own training mix, so the same text produces a different token count on a different family — and sometimes on a new generation of the same family.

- A vocabulary trained mostly on English prose splits code, JSON, identifiers and non-Latin scripts into more, smaller pieces.
- Token IDs are not portable: ID `995` means one thing to one model and something else to another.
- **Example, as of September 2026** (source: Anthropic model migration notes): the tokenizer introduced with Claude Opus 4.7 — also used by Opus 5.5 and Sonnet 5.5 — produces about **1× to 1.35×** as many tokens as the earlier Claude tokenizer for the same text. The per-token price can stay the same while the bill for identical text goes up.

The rule that follows: **never reuse a token count across model families.** Re-count with the target model's own counter (Anthropic exposes a `count_tokens` endpoint for this) before you budget or quote a cost.

## Four kinds of token on the bill

| Type | What it is | Relative cost |
|------|-----------|---------------|
| **Input tokens** | Your prompt: system prompt, history, documents, tool results | Base rate (prefill phase, processed in parallel) |
| **Cached input tokens** | A repeated prompt *prefix* served from the provider's cache | A fraction of the input rate (reads ~0.1×; writing the cache costs ~1.25×) |
| **Output tokens** | The visible answer | About 5× the input rate (decode phase, generated one token at a time) |
| **Reasoning / thinking tokens** | Tokens the model generates while it reasons before answering | **Billed as output tokens** — even when the reasoning text is hidden from you |

Reasoning tokens are the one that surprises teams. On current Claude models (as of September 2026) thinking is on by default, and the API returns the reasoning as an empty or summarised block — but you pay for every token the model actually generated while thinking. A short visible answer can carry a large output bill.

## Current prices — as of September 2026

Source: Anthropic API pricing, first-party rates, checked 25 September 2026. Prices are per **million** tokens (MTok). Cloud marketplaces (Bedrock, Vertex AI) set their own rates — always check the provider you are actually billed by.

| Model | Input $/MTok | Cached input (read) $/MTok | Output $/MTok | Context window |
|---|---|---|---|---|
| Claude Opus 5.5 | $4.00 | $0.20 | $20.00 | 1M tokens |
| Claude Sonnet 5.5 | $2.00 | $0.20 | $10.00 | 1M tokens |
| Claude Haiku 4.5 | $1.00 | ~$0.10 (0.1× rule) | $5.00 | 200K tokens |
| Local model via Ollama (e.g. `qwen3-local`) | $0.00 | n/a | $0.00 | 32K tokens (ICDEV config) |

A local model has no per-token bill, but it is not free: you pay in hardware, and its smaller window limits what you can send.

## Working out what a request costs

```
cost = input_tokens        × input_rate
     + cached_input_tokens × cache_read_rate
     + (output_tokens + reasoning_tokens) × output_rate
     ÷ 1,000,000
```

Worked example on Claude Sonnet 5.5 (prices as of September 2026): 20,000 input tokens of which 15,000 are a cached system prompt, a 500-token answer, and 1,500 tokens of thinking.

- uncached input: 5,000 × $2.00 = $0.0100
- cached input: 15,000 × $0.20 = $0.0030
- output + thinking: 2,000 × $10.00 = $0.0200
- **total ≈ $0.033** — and 60% of it is output, mostly thinking the user never saw.

## Optimising the bill

1. Compress system prompts — every run pays the input cost again.
2. Put stable content first and cache the prefix — cached reads cost about a tenth of fresh input.
3. Cap output with `max_tokens` and lower reasoning effort where quality holds — output is the expensive direction.
4. Stream responses to cut *perceived* latency (streaming does not reduce cost).

## The context window

Every model has a **context window** — the maximum token count for a single inference. Input + output combined (including reasoning tokens) must fit within it. As of September 2026, frontier hosted models offer windows of up to 1M tokens, while the local models you run in air-gap mode are often 8K–32K. Step 4 covers what to do when your prompt does not fit, and Step 6 is the lab where you compute a cost and trim a prompt yourself.
