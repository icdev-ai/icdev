---
ontology_id: icdev:mission:m-dataops-05-fine-tuning:step:2
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Design Your Training Dataset

The dataset is the training run. A poor dataset produces a poor model regardless of how much compute you throw at it. This step covers how ICDEV builds a dataset, how to size it, the split strategy, and the JSONL format the pipeline expects.

## How ICDEV builds a dataset

There are three ways in, and they can be combined in one dataset:

**Hand-authored examples.** You add `(user_input, expected_output)` pairs directly:
`python tools/finetune/dataset_manager.py --add-example --dataset-id ds-xxx --user-input "..." --expected-output "..."`. This is the slowest route, but it gives the highest-quality seed set.

**Generated from documents.** `pair_generator.py --generate --dataset-id ds-xxx --document-id ftdoc-xxx` chunks an ingested document. It asks the local `qwen3` model for `questions_per_chunk` (default 3) Q&A pairs per chunk.

**Generated from RAG content.** `pair_generator.py --generate-from-rag --dataset-id ds-xxx --source-table research_signals` does the same over content already in the RAG store.

Generated pairs are stored **unapproved**. Before anything is exported for training, a person reviews them with `labeler.py` (`--unlabeled` to list them, `--label` to score one, or `--batch-approve --min-quality 3`). `dataset_manager.py` rejects exact duplicates (a SHA-256 content hash per example) and tags each example with a `--classification` marking (default `CUI`).

The idea behind template-and-paraphrase generation still applies when you write your own seeds: vary the surface form of the inputs, so the model learns the task rather than memorizing phrasing.

## Dataset size guidelines

There are no universal rules, but these baselines hold for most fine-tuning APIs:

| Stage | Minimum | Production target |
|---|---|---|
| Initial experiment | 100 pairs | — |
| Staging / preview | 500 pairs | — |
| Production fine-tune | 1 000 pairs | 5 000–20 000 pairs |

More is better until you hit diminishing returns at roughly the 10 000–20 000 range for most task types. Quality degrades this curve faster than quantity improves it — 500 clean pairs outperform 5 000 noisy ones.

## Train/val/test split

Always split **before** any augmentation, to prevent data leakage. (ICDEV's own `evaluation.test_set_split` defaults to 0.15; the 80/10/10 below is a common hand-built alternative.)

| Split | Ratio | Purpose |
|---|---|---|
| Train | 80% | Model training |
| Validation | 10% | Hyperparameter tuning, early stopping |
| Test | 10% | Final held-out evaluation — never touched until promotion gate |

The test set is the single source of truth for promotion decisions. Reusing test pairs as training examples invalidates your evaluation.

## Quality signals

| Signal | Cost | Reliability |
|---|---|---|
| Human labels | High | Highest |
| LLM judge (`evaluator.py` with `llm_judge_enabled: true`) | Medium | High for relative comparison |
| Programmatic accuracy | Low | High for constrained outputs (exact match, schema validation) |
| ROUGE-L | Very low | Moderate — misses semantic equivalence |

For government AI applications, maintain a human-labelled gold set (minimum 200 pairs) that remains fixed across training runs. LLM judge metrics are acceptable for development iterations; the gold set drives production gates.

## JSONL format

`dataset_manager.py --export` writes this chat format: one object per line, with an optional `system` message first.


```jsonl
{"messages": [{"role": "user", "content": "Classify the severity of this STIG finding: CAT II finding V-230234 — SSH is enabled on a non-management interface."}, {"role": "assistant", "content": "{\"severity\": \"medium\", \"cat\": \"CAT II\", \"remediation\": \"Disable SSH on non-management interfaces or restrict access via host-based firewall.\"}"}]}
{"messages": [{"role": "user", "content": "Classify the severity of this STIG finding: CAT I finding V-230265 — Root login is permitted over SSH."}, {"role": "assistant", "content": "{\"severity\": \"high\", \"cat\": \"CAT I\", \"remediation\": \"Set PermitRootLogin no in /etc/ssh/sshd_config and restart the SSH service.\"}"}]}
```

Key rules:
- Each line is a complete JSON object — no multi-line JSON
- The `messages` array follows the conversation format (user/assistant pairs)
- Assistant content is the exact ideal output, including any formatting the model should learn
- For structured output tasks, the assistant content should be valid JSON with no markdown fences

## Configuration questions

1. Why is the test set split performed before augmentation, not after?
2. Your dataset has 2 000 pairs but 1 800 come from a single template. What problem does this create?
3. You have 50 hand-labelled seed pairs and a 300-page policy manual. How would you combine hand-authored examples with `pair_generator.py --generate`, and what must happen in `labeler.py` before training?
4. A colleague suggests using ROUGE-L as the sole promotion gate metric. What task type would make this inadequate, and what alternative would you add?

---

**Your task:** Answer the configuration questions above.
