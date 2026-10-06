---
ontology_id: icdev:mission:m-dataops-05-fine-tuning:step:4
step_class: icdev:Assessment
---

<!-- CUI // SP-CTI -->

# Fine-Tuning Retrospective

Shipping a fine-tuned model to production is not the end of the story. Models degrade, drift, and fail in ways that are often silent until a user notices. This final step covers the failure modes that appear most frequently in production, the drift indicators worth monitoring, and the feedback loop that closes the training cycle.

## Common failure modes

**Dataset too narrow** — The most frequent cause of production failure. A model trained on 10 template variations of a STIG classification task learns to recognise those templates, not the underlying task. It handles the training distribution well and fails on any real-world input that deviates even slightly. Prevention: check template diversity before training. `pair_generator.py --stats --dataset-id ds-xxx` shows what was generated. Count how many inputs share a template, and treat more than ~30% from one template as a red flag.

**Label noise** — Incorrect ground-truth outputs in the training set actively teach the model wrong behaviour. At 5% noise, most models are resilient. At 15%+, accuracy degradation is measurable. Prevention: have a second reviewer re-label a random 10% sample in `labeler.py` and measure the disagreement rate before training.

**Train/test leakage** — If any test pair appears in the training set (even paraphrased), your evaluation metrics are inflated. The model has effectively memorised those examples. Prevention: split before augmentation (covered in step 2). `dataset_manager.py` rejects exact duplicates by content hash, but only within one dataset, and paraphrases slip through. Compare your train and test exports for near-duplicates yourself.

**Catastrophic forgetting** — Fine-tuning on a narrow task can degrade the model's general capabilities. This is especially visible when the fine-tuned model is asked a question outside its training distribution — it responds with task-specific output regardless. Mitigation: use a small learning rate, limit training epochs, and include a small percentage of general-capability pairs (5–10%) in the training mix.

## Production drift indicators

| Indicator | What it signals | Tool |
|---|---|---|
| Quality-score drop | Genuine accuracy degradation | `tools/llm/model_monitor.py --detect-drift` (`quality_degradation`) |
| Token count rise | Output verbosity drift, prompt growth | `model_monitor.py` (`token_inflation`) |
| Latency change | Model version mismatch or infrastructure issue | `model_monitor.py` (`latency_increase`, P95) |
| Retrieval quality drop | RAG-side regression feeding the model | `tools/finetune/quality_monitor.py --check` |
| Score decay vs held-out gold set | Accuracy degradation | Scheduled `evaluator.py --evaluate` on the gold set |

`model_monitor.py` drift thresholds are fixed in code (quality warning above a 10% drop, critical above 25%), and each event is written to the append-only `model_drift_events` table. Refusal-rate tracking is not built in. If it matters for your task, score refusals in your evaluator and record them as quality scores.

## Monitoring commands

```bash
# Production drift for the function your fine-tune serves
python tools/llm/model_monitor.py --detect-drift --model <model_id> --function <function> --window-days 7 --json

# RAG evaluation health + retrain signal
python tools/finetune/quality_monitor.py --check --json

# Re-score the active version on your fixed gold set
python tools/finetune/evaluator.py --evaluate --model-version-id mv-xxx --test-set data/finetune/gold_set.jsonl --json
```

The fine-tuning pages of the dashboard (`/finetune`, with `/finetune/models` and `/finetune/evaluate`) show model versions and evaluation results.

## Feedback loop: corrections back to training pairs

User corrections are the highest-value training signal available — they represent real-world cases where the model failed and a human provided the correct output.

A feedback loop you can build on ICDEV's pieces:

1. Users flag incorrect outputs in your application and supply the corrected output.
2. Each correction becomes a dataset example through `dataset_manager.py --add-example`, tagged with its `--classification` and left **unapproved**.
3. A reviewer approves it in `labeler.py`, and only approved examples are exported.
4. `retrain_trigger.py` queues a new training job once enough new approved examples accumulate (default 50).
5. The new version goes through `evaluator.py` and the `promotion_manager.py` gates before it can serve traffic.

This loop requires that corrections be reviewed for label quality before inclusion. A correction submitted by a user is not automatically correct — particularly in government domain tasks with specialised terminology. A second human reviewer, or LLM judge agreement with a rubric, is the minimum quality gate.

## Reflection questions

1. A model trained on 3 000 pairs scores 94% accuracy on the test set but 71% in production. What are the two most likely explanations?
2. `model_monitor.py` reports `token_inflation` of +25% over 7 days, but quality scores are stable. What is the most probable cause, and is retraining warranted?
3. A user correction dataset contains 400 pairs but 180 of them correct the same recurring phrasing error. How would you weight these before adding them to training?
4. Catastrophic forgetting is detected: the fine-tuned model fails on basic reasoning tasks it handled correctly before training. You cannot retrain immediately. What can you do today to mitigate user impact?
5. Your team wants to close the feedback loop fully automatically (corrections → training → deployment with no human review). What is the failure mode this introduces, and what is the minimum safeguard?

---

**Your task:** Answer the reflection questions to complete this mission.
