<!-- CUI // SP-CTI -->

# Fine-Tuning Retrospective

Shipping a fine-tuned model to production is not the end of the story. Models degrade, drift, and fail in ways that are often silent until a user notices. This final step covers the failure modes that appear most frequently in production, the drift indicators worth monitoring, and the feedback loop that closes the training cycle.

## Common failure modes

**Dataset too narrow** — The most frequent cause of production failure. A model trained on 10 template variations of a STIG classification task learns to recognise those templates, not the underlying task. It handles the training distribution well and fails on any real-world input that deviates even slightly. Prevention: use the diversity check in `pair_generator.py --check-diversity` before training; it flags if >30% of pairs share the same input template.

**Label noise** — Incorrect ground-truth outputs in the training set actively teach the model wrong behaviour. At 5% noise, most models are resilient. At 15%+, accuracy degradation is measurable. Prevention: run `evaluator.py --audit-labels` on a random 10% sample before training to surface likely mislabels via LLM judge disagreement.

**Train/test leakage** — If any test pair appears in the training set (even paraphrased), your evaluation metrics are inflated. The model has effectively memorised those examples. Prevention: split before augmentation (covered in step 2) and run a similarity hash check: `pair_generator.py --dedup-across train.jsonl test.jsonl`.

**Catastrophic forgetting** — Fine-tuning on a narrow task can degrade the model's general capabilities. This is especially visible when the fine-tuned model is asked a question outside its training distribution — it responds with task-specific output regardless. Mitigation: use a small learning rate, limit training epochs, and include a small percentage of general-capability pairs (5–10%) in the training mix.

## Production drift indicators

| Indicator | What it signals | Tool |
|---|---|---|
| Output length drift | Distribution shift or prompt-following degradation | `model_monitor.py --metric output_length` |
| Refusal rate increase | Prompt injection in user inputs or safety policy conflict | `model_monitor.py --metric refusal_rate` |
| Latency change | Model version mismatch or infrastructure issue | `model_monitor.py --metric p99_latency` |
| Win-rate decay vs. held-out gold | Genuine accuracy degradation | Weekly `evaluator.py` run on gold set |

A 15% drift in any indicator over a 7-day rolling window triggers a `retrain_trigger.py` Kanban task automatically. The threshold is configurable in `args/finetune_config.yaml`.

## Monitoring with model_monitor.py

```bash
# Run drift analysis for the last 7 days
python tools/finetune/model_monitor.py \
    --model-id prod \
    --window-days 7 \
    --metrics output_length,refusal_rate,p99_latency \
    --alert-on-drift \
    --json

# Weekly gold-set evaluation (cron-scheduled)
python tools/finetune/evaluator.py \
    --model-id prod \
    --test-set data/finetune/gold_set.jsonl \
    --metrics winrate,accuracy \
    --compare-baseline archived_baseline.json
```

`model_monitor.py` writes results to `icdev.db` in the `model_drift_events` table (append-only). The FathomDesk-style monitoring dashboard at `/finetune/monitor` visualises these time series.

## Feedback loop: corrections back to training pairs

User corrections are the highest-value training signal available — they represent real-world cases where the model failed and a human provided the correct output.

The feedback loop:

1. Users flag incorrect model outputs in the UI (thumbs down + optional correction text).
2. Corrections are stored in `model_corrections` table with the original input, model output, and human-corrected output.
3. `pair_generator.py --from-corrections` pulls flagged corrections, applies CUI redaction if needed, and formats them as JSONL pairs.
4. These pairs are added to the training set at the next scheduled retraining cycle (weighted 2x to compensate for volume imbalance).
5. The corrected pair IDs are marked `included_in_training = true` in `model_corrections` to prevent duplicate inclusion.

This loop requires that corrections be reviewed for label quality before inclusion. A correction submitted by a user is not automatically correct — particularly in government domain tasks with specialised terminology. A second human reviewer, or LLM judge agreement with a rubric, is the minimum quality gate.

## Reflection questions

1. A model trained on 3 000 pairs scores 94% accuracy on the test set but 71% in production. What are the two most likely explanations?
2. Your `model_monitor.py` report shows output length drift of +25% over 7 days but win-rate and accuracy are stable. What is the most probable cause, and is retraining warranted?
3. A user correction dataset contains 400 pairs but 180 of them correct the same recurring phrasing error. How would you weight these before adding them to training?
4. Catastrophic forgetting is detected: the fine-tuned model fails on basic reasoning tasks it handled correctly before training. You cannot retrain immediately. What can you do today to mitigate user impact?
5. Your team wants to close the feedback loop fully automatically (corrections → training → deployment with no human review). What is the failure mode this introduces, and what is the minimum safeguard?

---

**Your task:** Answer the reflection questions to complete this mission.
