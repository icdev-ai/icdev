<!-- CUI // SP-CTI -->

# Evaluate and Promote

Training a checkpoint is not the same as deploying a model. Promotion is gated — a model must pass a set of quantitative and regression-based checks before it can replace the production version. This step covers `evaluator.py`, `promotion_manager.py`, `model_registry.py`, canary deployment, and drift-based retraining.

## evaluator.py metrics

The evaluator runs three classes of metrics on the held-out test set:

**ROUGE-L (Longest Common Subsequence recall)** — A token-level overlap measure. Fast to compute. Useful for generation tasks where the ideal output has a canonical surface form. Blind to semantic equivalence — two correct but differently-worded summaries score poorly against each other.

**Win-rate via LLM judge** — The evaluator submits each (input, candidate output, reference output) triple to `llm_judge.py` and asks which response is better. Aggregated win-rate over the test set is a more reliable quality signal than ROUGE-L for open-ended tasks. Requires an LLM call per test pair — budget accordingly.

**Task-specific accuracy** — For constrained output tasks (classification, schema-conformant JSON, exact extraction), programmatic matching provides the cleanest signal. `evaluator.py` supports custom accuracy functions via a `--accuracy-fn` plugin path.

```bash
python tools/finetune/evaluator.py \
    --model-id fine-tune-v1-2-0 \
    --test-set data/finetune/test.jsonl \
    --metrics rouge,winrate,accuracy \
    --output reports/eval_v1_2_0.json
```

## promotion_manager.py gates

Promotion requires:

| Gate | Default threshold | Notes |
|---|---|---|
| ROUGE-L recall | ≥ 0.72 | Override via `--min-rouge` |
| Win-rate vs baseline | ≥ 0.55 | Must beat production model, not just tied |
| Task accuracy | ≥ 0.90 | Task-specific; set in `args/finetune_config.yaml` |
| Regression guard | ≤ -0.02 delta | Fails if any metric drops >2% vs current prod |

If any gate fails, promotion is blocked and the failure is written to `model_registry.py` with `status = "rejected"`.

```bash
python tools/finetune/promotion_manager.py \
    --candidate fine-tune-v1-2-0 \
    --baseline prod \
    --eval-report reports/eval_v1_2_0.json \
    --promote-if-pass
```

## model_registry.py versioning

Model versions follow `major.minor.patch` semver semantics applied to model weights:

| Increment | Meaning |
|---|---|
| `patch` | Retrain on the same dataset (hyperparameter tweak, deduplication fix) |
| `minor` | Dataset update — new pairs added, same task definition |
| `major` | Task redefinition, base model change, or catastrophic forgetting recovery |

```python
from tools.finetune.model_registry import ModelRegistry
registry = ModelRegistry()
registry.register(
    model_id="fine-tune-v1.2.0",
    base_model="claude-haiku-4-5",
    checkpoint_path="data/checkpoints/v1_2_0.bin",
    metrics=eval_report,
    status="candidate",
)
```

## Canary deployment pattern

Never flip the full traffic share to a new fine-tuned model immediately. Route 5% of production traffic to the candidate:

```
10% traffic → fine-tune v1.2.0 (canary)
90% traffic → fine-tune v1.1.0 (stable)
```

Monitor error rate, latency percentiles, and user feedback scores for 24–48 hours. Promote to 100% only if canary metrics stay within acceptable bounds. The routing weight is stored in `args/finetune_config.yaml` and read by the LLM router at request time.

## retrain_trigger.py for drift-based retraining

Production drift (gradual change in the input distribution) degrades model accuracy over time without any code change. `retrain_trigger.py` monitors:

- Output length drift — median response length shifts by >15% over a rolling 7-day window
- Refusal rate increase — model declines to answer more than a configurable baseline
- Accuracy drop on a continuously-scored canary prompt set

When any threshold is crossed, `retrain_trigger.py` creates a Kanban task `status = 'scheduled'` to kick off the pipeline with the latest production data appended to the training set.

## Configuration questions

1. Win-rate is measured against the current production model, not a static reference. Why does this matter across multiple retraining cycles?
2. The regression guard blocks if any metric drops >2% vs production. A new fine-tune improves task accuracy by 8% but drops ROUGE-L by 3%. What happens at the gate?
3. You start canary at 5% traffic. After 12 hours, error rate is identical to stable but P99 latency is 40% higher. Do you promote? What do you investigate?
4. `retrain_trigger.py` fires a retraining job. The new model is trained on old training data + new production logs. What data hygiene step must happen before adding production logs to the training set?

---

**Your task:** Answer the configuration questions above.
