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
build dataset → train → evaluate → promote
```

Each stage is a separate, re-runnable step with its own record in the ICDEV database, so a failure at one stage does not force you to start over. All of it lives in `tools/finetune/` and is configured by `args/finetune_config.yaml`.

| Stage | Tool | Output |
|---|---|---|
| Build dataset | `dataset_manager.py`, `pair_generator.py`, `labeler.py` | Approved examples, exported as JSONL |
| Train | `training_engine.py` | Training job, then a model version (`mv-...`) |
| Evaluate | `evaluator.py` (and `ab_evaluator.py`) | BLEU / ROUGE-L / perplexity report |
| Promote | `promotion_manager.py` + `model_registry.py` | The active model for a function |

`model_registry.py` underpins the last two stages. It stores each model version and its eval scores, and records which version is active for which function, with an append-only promotion history.

## Tools in the ICDEV stack

**`dataset_manager.py`**: creates datasets, adds examples, and exports **approved** examples as chat-format JSONL.

**`pair_generator.py`**: generates question/answer pairs from document chunks or RAG content using the local `qwen3` model. Generated pairs land as **unapproved** examples, so a human must review them before they can be trained on.

**`labeler.py`**: the human review step. It scores quality, compliance and relevance, and can batch-approve or batch-reject by quality score.

**`training_engine.py`**: runs the full pipeline (dataset export → provider training → GGUF export → Ollama registration → evaluation). Providers are pluggable through `provider_factory.py`: local LoRA with `unsloth_local`, plus `openai`, `bedrock` and `azure_openai`.

**`evaluator.py`**: scores a model version on a held-out test set with BLEU, ROUGE-L and a perplexity estimate. An LLM-as-judge is optional (`llm_judge_enabled` in config, off by default). **`ab_evaluator.py`** compares two versions on the same test set with a paired t-test.

**`promotion_manager.py`**: auto-promotes a version that clears every threshold in `args/finetune_config.yaml`, or force-promotes it with an audited reason.

**`model_registry.py`**: model versions (`mv-...` ids), the active model per function, and promotion/demotion history.

## Dataset quality principles

A training dataset is the most important artefact in the fine-tuning pipeline. Quantity is secondary to quality:

- **Diversity** — Cover the full input distribution your model will encounter in production. A model trained on narrow examples fails silently on edge cases.
- **Coverage** — Map your dataset against a taxonomy of sub-tasks. Gaps in coverage become gaps in model capability.
- **10:1 negative-to-edge-case ratio** — For every edge-case pair, include ~10 mainstream pairs. Edge cases trained at equal weight cause the model to treat them as normal.
- **Clean labels** — Mislabelled examples are more damaging than missing examples. An incorrect "correct" output actively degrades the model's calibration.

---

**Your task:** In the next step, design your training dataset.
