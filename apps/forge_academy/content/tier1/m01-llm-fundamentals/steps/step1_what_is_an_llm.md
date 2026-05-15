---
ontology_id: icdev:mission:m01-llm-fundamentals:step:1
step_class: icdev:Lesson
---

# What Is a Large Language Model?

A **Large Language Model (LLM)** is a neural network trained on massive text datasets to predict the next token in a sequence. That's it. Everything else — the chat interface, the code generation, the "reasoning" — emerges from that single objective.

## The Core Mechanic

```
Input tokens → Attention layers → Probability distribution → Next token
```

The model doesn't "understand" text the way you do. It computes statistical relationships between billions of tokens and uses those relationships to generate plausible continuations.

## Why This Matters for You

As an AI engineer, you need to think in terms of:

1. **Context window** — How many tokens the model can "see" at once
2. **Temperature** — How random/creative the outputs are (0 = deterministic, 1 = chaotic)
3. **Token count** — Directly affects cost and latency

## Your First LLM Call

In ICDEV, all LLM calls go through `tools/llm/router.py` — a unified interface that routes to Bedrock, Azure OpenAI, or Ollama based on your environment config.

```python
from tools.llm.router import LLMRouter

router = LLMRouter()
provider = router.get_provider_for_function("chat")

response = provider.chat(messages=[
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Explain what a token is in one sentence."}
])
```

**Your task:** Run a basic LLM call and observe the output. Focus on the structure — what comes back, how it's formatted, what the token count looks like.
