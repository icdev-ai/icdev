---
ontology_id: icdev:mission:m01-llm-fundamentals:step:3
step_class: icdev:Lesson
---

# Temperature & Sampling

Temperature is the single dial that controls how predictable or creative your LLM is. Get it wrong and your production agent either hallucinates freely or repeats the same answer forever.

## The Math (simplified)

At each generation step, the model outputs a probability distribution over its vocabulary (~100K tokens). **Temperature** scales this distribution:

- **Temperature = 0.0** → Always pick the highest-probability token. Deterministic. Boring. Reliable.
- **Temperature = 1.0** → Sample from the raw distribution. Creative. Varied. Risky.
- **Temperature > 1.5** → Amplifies low-probability tokens. Often incoherent.

```
Low temp:  "The capital of France is Paris."
High temp: "The capital of France is perhaps most famously Paris, though historically..."
```

## Top-P (nucleus sampling)

Rather than using all tokens in the distribution, top-p sampling cuts off the tail. At `top_p=0.9`, the model only samples from the top 90% of the cumulative probability mass. This prevents bizarre outliers without flattening the distribution like low temperature does.

## When to use which

| Use case | Temperature | Top-P |
|----------|------------|-------|
| Code generation | 0.1–0.3 | 0.95 |
| Classification / routing | 0.0 | 1.0 |
| Creative writing | 0.7–1.0 | 0.9 |
| Structured JSON output | 0.0–0.1 | 1.0 |
| Chat / conversation | 0.5–0.7 | 0.95 |

## Your task

Implement a function `sample_responses` that simulates how different temperatures produce different outputs. Call `simulate_temperature(prompt, temp)` with temperatures 0.0, 0.5, and 1.0, then print the results. Observe how the "diversity score" changes.
