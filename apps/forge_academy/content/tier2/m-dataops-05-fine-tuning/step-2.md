<!-- CUI // SP-CTI -->

# Design Your Training Dataset

The dataset is the training run. A poor dataset produces a poor model regardless of how much compute you throw at it. This step covers how `pair_generator.py` works, how to size your dataset correctly, the split strategy, and the JSONL format the pipeline expects.

## How pair_generator.py works

`pair_generator.py` operates in three modes, which can be combined:

**Template variation** — You define an input template with `{{slots}}` and a corresponding ideal output template. The generator fills the slots from a seed vocabulary (lists of entities, values, intents). This is the fastest way to generate volume for well-structured tasks.

**Templated variation with paraphrase** — Same as above, but an LLM (Ollama locally, or a cloud provider) generates 3–5 surface-form variations of each input. This prevents the model from memorising phrasing instead of learning the underlying task.

**LLM-assisted augmentation** — For tasks where you have a small set of hand-labelled seed pairs (50–100), the generator prompts an LLM to produce novel inputs that are semantically diverse from the seeds, then generates predicted outputs for human review. This is slower but produces the most realistic distribution coverage.

All three modes output JSONL. The generator strips PII and CUI-sensitive values before writing if `--redact-cui` is passed.

## Dataset size guidelines

There are no universal rules, but these baselines hold for most fine-tuning APIs:

| Stage | Minimum | Production target |
|---|---|---|
| Initial experiment | 100 pairs | — |
| Staging / preview | 500 pairs | — |
| Production fine-tune | 1 000 pairs | 5 000–20 000 pairs |

More is better until you hit diminishing returns at roughly the 10 000–20 000 range for most task types. Quality degrades this curve faster than quantity improves it — 500 clean pairs outperform 5 000 noisy ones.

## Train/val/test split

Always split **before** any augmentation to prevent data leakage:

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
| LLM judge (Prometheus-2 or `llm_judge.py`) | Medium | High for relative comparison |
| Programmatic accuracy | Low | High for constrained outputs (exact match, schema validation) |
| ROUGE-L | Very low | Moderate — misses semantic equivalence |

For government AI applications, maintain a human-labelled gold set (minimum 200 pairs) that remains fixed across training runs. LLM judge metrics are acceptable for development iterations; the gold set drives production gates.

## JSONL format

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
3. You have 50 hand-labelled seed pairs. Which `pair_generator.py` mode produces the most realistic distribution coverage, and why?
4. A colleague suggests using ROUGE-L as the sole promotion gate metric. What task type would make this inadequate, and what alternative would you add?

---

**Your task:** Answer the configuration questions above.
