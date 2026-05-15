---
ontology_id: icdev:mission:m01-llm-fundamentals:step:4
step_class: icdev:Lesson
---

# Context Window Limits

The context window is the working memory of an LLM. Everything the model knows during a single inference — system prompt, conversation history, retrieved documents, tool results — must fit inside it.

## Why this matters operationally

In production ICDEV systems, you'll chain agents that pass context between them. A RAG pipeline might retrieve 20 documents × 500 tokens each = 10,000 tokens before the model even starts generating. If your context window is 8K, you're already at the limit before adding the system prompt or conversation history.

## Strategies for managing context

1. **Summarization** — Compress old conversation turns into a running summary
2. **Chunking** — Split documents, retrieve top-K chunks, not the full text
3. **Context distillation** — Use a cheap model to compress, expensive model to reason
4. **Sliding window** — Keep the last N tokens of conversation, drop earlier turns

## ICDEV approach

The `tools/llm/router.py` tracks `input_tokens + output_tokens` per call. When a session approaches 80% of the model's context limit, the router automatically triggers a summarization pass using the cheaper model tier. This prevents mid-conversation failures without requiring manual intervention.

**Test your understanding** — answer the question below to continue.
