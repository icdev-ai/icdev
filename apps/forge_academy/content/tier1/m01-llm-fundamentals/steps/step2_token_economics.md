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

## Input tokens vs Output tokens

| Type | Direction | Cost driver |
|------|-----------|-------------|
| **Input/Prompt tokens** | You → Model | Cheaper (prefill phase) |
| **Output/Completion tokens** | Model → You | More expensive (decode phase, sequential) |

In production, you optimize by:
1. Compressing system prompts (every run pays input cost)
2. Using streaming to reduce perceived latency
3. Caching repeated context (prefix caching = near-zero repeat cost)

## The context window

Every model has a **context window** — the maximum token count for a single inference. Input + output combined must fit within it.

| Model family | Context window |
|---|---|
| GPT-3.5-turbo | 16K tokens |
| GPT-4o | 128K tokens |
| Claude 3.7 Sonnet | 200K tokens |
| Mistral 7B (Ollama) | 8K–32K tokens |

In ICDEV, the LLM router tracks token usage per call. Watch the demo output to see how context budgets are managed in practice.
