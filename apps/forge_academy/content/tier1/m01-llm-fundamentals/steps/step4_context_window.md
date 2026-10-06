---
ontology_id: icdev:mission:m01-llm-fundamentals:step:4
step_class: icdev:Lesson
---

# Context Window Limits

The context window is the working memory of an LLM. Everything the model knows during a single inference — system prompt, conversation history, retrieved documents, tool results — must fit inside it, together with the tokens the model generates in reply (including any reasoning tokens).

## How big is a window today?

As of September 2026 (source: Anthropic model documentation and ICDEV `args/llm_config.yaml`):

| Model | Context window | Max output per response |
|---|---|---|
| Claude Opus 5.5 / Claude Sonnet 5.5 | 1M tokens | 128K tokens |
| Claude Haiku 4.5 | 200K tokens | — |
| `qwen3-local` (Ollama, air-gap) | 32K tokens | — |

The spread is more than thirtyfold. A prompt that fits comfortably on a hosted frontier model can overflow the local model that serves the same request in air-gap mode. Windows also change between model generations, so treat any number here as dated and check the provider's model listing (Anthropic's Models API returns `max_input_tokens` per model) before you design against it.

## Why this matters operationally

In production ICDEV systems, you'll chain agents that pass context between them. A RAG pipeline might retrieve 20 documents × 500 tokens each = 10,000 tokens before the model even starts generating. If your context window is 8K, you're already at the limit before adding the system prompt or conversation history.

A bigger window is not a free pass either: every token you put in it is billed as input on every call, and long prompts are slower to process.

## Strategies for managing context

1. **Summarization** — Compress old conversation turns into a running summary
2. **Chunking** — Split documents, retrieve top-K chunks, not the full text
3. **Context distillation** — Use a cheap model to compress, expensive model to reason
4. **Sliding window** — Keep the last N tokens of conversation, drop earlier turns

## ICDEV approach: budget against the smallest window

ICDEV's budgeting seam is `tools/llm/context_budget.py`. It works out how many tokens are left for evidence before a request is sent, rather than letting the provider reject an oversized one:

- **`floor_window_for_function()`** takes the **minimum** context window across every model in the routing chain for a function. The model that actually serves the call can change at run time (a fallback, or a local model in air-gap mode), so a budget computed for a 1M-token model that is served by a 32K model would overflow.
- **`available_input_tokens()`** subtracts a reserve for the model's output and the system prompt and question, then keeps a 10% safety margin.
- **`estimate_tokens()`** deliberately **over-counts** (about 3.3 characters per token, not 4). Over-estimating costs you one fewer chunk; under-estimating overflows a real request that cannot be recovered.
- **`pack_evidence()`** fills the remaining budget in rank order instead of truncating each document blind.

You will implement a toy version of the same idea — count, reserve, drop the oldest turns — in the Step 6 lab.

**Test your understanding** — answer the questions below to continue.
