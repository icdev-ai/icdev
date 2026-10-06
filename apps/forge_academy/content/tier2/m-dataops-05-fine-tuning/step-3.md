---
ontology_id: icdev:mission:m-dataops-05-fine-tuning:step:3
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Evaluate and Promote

Training a checkpoint is not the same as deploying a model. Promotion is gated — a model must pass quantitative thresholds before it can replace the active version. This step covers `evaluator.py`, `promotion_manager.py`, `model_registry.py`, staged rollout, and automatic retraining.

## evaluator.py metrics

`tools/finetune/evaluator.py` runs on the held-out test set. All metrics are implemented in pure Python, so they are safe to run air-gapped.

**BLEU**: n-gram precision against the reference output. It is cheap and works well for outputs with a canonical wording.

**ROUGE-L (Longest Common Subsequence)**: an overlap measure that is more tolerant of reordering than BLEU. It is still blind to semantic equivalence: two correct but differently worded summaries score poorly against each other.

**Perplexity estimate**: how "surprised" the model is by the reference outputs. Promotion looks for an *improvement* over the base model.

**LLM-as-judge (optional)**: set `evaluation.llm_judge_enabled: true` in `args/finetune_config.yaml` to have `qwen3-local` judge outputs. It costs an LLM call per test pair, but suits open-ended tasks.

To compare two versions head to head, `tools/finetune/ab_evaluator.py --compare --model-a mv-xxx --model-b mv-yyy --test-set test.jsonl` runs both on the same set and reports a paired t-test.

```bash
python tools/finetune/evaluator.py --evaluate \
    --model-version-id mv-xxx \
    --test-set data/finetune/test.jsonl \
    --json
```

## promotion_manager.py gates

The thresholds live under `promotion:` in `args/finetune_config.yaml`, and `require_all_thresholds: true` means every one must pass:

| Gate | Default threshold |
|---|---|
| BLEU | ≥ 0.30 (`min_bleu`) |
| ROUGE-L | ≥ 0.40 (`min_rouge_l`) |
| Perplexity improvement vs base | ≥ 10% (`min_perplexity_improvement_pct`) |

```bash
# Is this version eligible?
python tools/finetune/promotion_manager.py --check --model-version-id mv-xxx --function code_generation --json
# Promote it if every threshold passes
python tools/finetune/promotion_manager.py --auto-promote --model-version-id mv-xxx --function code_generation --json
# Human override (manual_override_allowed: true); always give a reason
python tools/finetune/promotion_manager.py --force-promote --model-version-id mv-xxx --function code_generation --reason "..." --json
```

Every transition is written to the append-only `ft_promotion_log`. The built-in gate has no "must beat the current production model" rule. If you want one, run `ab_evaluator.py` against the active version first; that is a policy you add.

## model_registry.py versioning

Each trained model is a **model version** with an `mv-...` id and its eval scores. Promotion is **per function**, and only one version is active per `(function_name, tenant_id, project_id)`:

```python
from tools.finetune.model_registry import get_active_model, promote_model, demote_model

promote_model(model_version_id="mv-xxx", function_name="stig_triage",
              activated_by="ml-lead", activation_reason="BLEU 0.41 / ROUGE-L 0.52")
get_active_model("stig_triage")       # {"success": True, "active_model": {...}}
demote_model("stig_triage", reason="regression in prod")   # back to default routing
```

## Staged rollout (canary) pattern

Never switch all traffic to a new fine-tuned model at once. ICDEV has no percentage traffic splitter for fine-tuned models. It does have **scoped promotion**: `promote_model(..., tenant_id=..., project_id=...)` activates a version for one tenant or project only, so you can canary on a single project first:

```
project "pilot"   → fine-tune mv-new (canary)
everyone else     → current active version / default routing
```

Monitor error rate, latency percentiles and user feedback for 24-48 hours. Widen the promotion only if the canary stays within bounds; otherwise `demote_model()` the canary scope.

## retrain_trigger.py: automatic retraining

`tools/finetune/retrain_trigger.py` watches each dataset for new approved examples. When a dataset gains at least `retrain.new_example_threshold` (default 50) new examples since its last training job, and the `cooldown_hours` (default 24) have passed, it queues a new training job. At most `max_concurrent_jobs` (2) run at once. It runs as a heartbeat check or on demand:

```bash
python tools/finetune/retrain_trigger.py --check --json
python tools/finetune/retrain_trigger.py --trigger --dataset-id ds-xxx --json
```

Quality-based triggers live elsewhere. `tools/finetune/quality_monitor.py` watches RAG evaluation metrics (NDCG, MRR, faithfulness) and can generate targeted pairs and trigger retraining. `tools/llm/model_monitor.py` detects production quality, latency and token drift (Mission SRE-AI-02).

## Configuration questions

1. Suppose you add your own gate: an `ab_evaluator.py` comparison against the **currently active** version, not a static reference. Why does that matter across several retraining cycles?
2. A new fine-tune scores BLEU 0.34, ROUGE-L 0.38, and improves perplexity by 15%. What does `--auto-promote` do, and when (if ever) is `--force-promote` justified?
3. You canary the new version on one pilot project. After 12 hours its error rate matches the current model, but P99 latency is 40% higher. Do you widen the promotion? What do you investigate?
4. `retrain_trigger.py` queues a job because 50 new examples were approved. Some of them came from production logs. What data hygiene step must happen before production logs become training examples?

---

**Your task:** Answer the configuration questions above.
