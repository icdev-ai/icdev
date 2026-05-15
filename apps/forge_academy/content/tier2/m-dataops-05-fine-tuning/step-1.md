---
ontology_id: icdev:mission:m-dataops-05-fine-tuning:step:1
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Fine-Tuning Pipeline Overview

Fine-tuning is not a magic amplifier for any model problem. It is a targeted tool for one specific situation: you have a well-defined task, you have high-quality examples, and prompt engineering alone cannot reach the required accuracy or cost ceiling. Used correctly, it produces models that are faster, cheaper, and more consistent on their target task. Used incorrectly, it produces expensive, brittle models that overfit a narrow distribution.

## When to fine-tune vs prompt engineer

Apply the 80/20 rule: spend 80% of your effort on prompt engineering and retrieval before touching fine-tuning. Fine-tuning is warranted when:

- Prompt engineering is maxed out (you have a strong system prompt + few-shot examples) and accuracy is still below threshold
- The target output format is highly specialised (structured data, domain jargon, classified ontologies) and few-shot examples consume too many tokens to be practical
- Latency or cost is the primary driver and you need to use a smaller base model

Fine-tuning is **not** warranted for: tasks where you lack ground-truth data, tasks that change frequently, general-purpose capability improvements, or as a substitute for RAG when the knowledge is factual and updatable.

## The 4-stage pipeline

```
generate pairs → train → evaluate → promote
```

Each stage is a discrete, reversible step. A failure at any stage does not require restarting from the beginning.

| Stage | Tool | Output |
|---|---|---|
| Generate pairs | `pair_generator.py` | JSONL training file |
| Train | `training_engine.py` | Model checkpoint |
| Evaluate | `evaluator.py` | Metric report |
| Promote | `promotion_manager.py` | Registry entry + deployment |

`model_registry.py` underpins the last two stages — it stores checkpoint paths, metrics, versioning metadata, and the lineage chain from base model to fine-tuned variant.

## Tools in the ICDEV stack

**`pair_generator.py`** — Produces input/output pairs from templates, variation seeds, and optional LLM-assisted augmentation. Outputs JSONL. Supports CUI-redacted output for IL4+ datasets.

**`training_engine.py`** — Wraps the provider's fine-tuning API (Anthropic, Azure OpenAI, Vertex AI) or a local LoRA trainer (for Ollama-served models). Accepts a config YAML for hyperparameters.

**`evaluator.py`** — Runs ROUGE-L, win-rate (via LLM judge), and task-specific accuracy metrics on a held-out test set. Outputs a structured JSON report.

**`promotion_manager.py`** — Gates promotion based on minimum metric thresholds and regression guards against the current production model.

**`model_registry.py`** — Stores model metadata in `icdev.db`. Every checkpoint is versioned with `major.minor.patch` semver semantics.

## Dataset quality principles

A training dataset is the most important artefact in the fine-tuning pipeline. Quantity is secondary to quality:

- **Diversity** — Cover the full input distribution your model will encounter in production. A model trained on narrow examples fails silently on edge cases.
- **Coverage** — Map your dataset against a taxonomy of sub-tasks. Gaps in coverage become gaps in model capability.
- **10:1 negative-to-edge-case ratio** — For every edge-case pair, include ~10 mainstream pairs. Edge cases trained at equal weight cause the model to treat them as normal.
- **Clean labels** — Mislabelled examples are more damaging than missing examples. An incorrect "correct" output actively degrades the model's calibration.

---

**Your task:** In the next step, design your training dataset.
