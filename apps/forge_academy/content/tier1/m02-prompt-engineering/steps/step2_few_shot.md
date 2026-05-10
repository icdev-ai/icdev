# Few-Shot Prompting

Zero-shot asks the model to figure out the pattern from instructions alone. Few-shot *shows* it the pattern. For classification, extraction, and formatting tasks, few-shot consistently outperforms zero-shot — sometimes by 30-40% accuracy.

## Why few-shot works

LLMs are exceptional pattern matchers. When you provide examples, the model uses them to calibrate:
- Output format (JSON, markdown, plain text)
- Tone and verbosity
- Edge case handling
- What "correct" looks like for your specific task

## Example: STIG Finding Classifier

```
SYSTEM: Classify STIG findings by severity. Use exactly this format:
FINDING: <text> → SEVERITY: <CAT1|CAT2|CAT3> | RISK: <brief reason>

EXAMPLE 1:
FINDING: Default password 'admin' in use on all servers.
→ SEVERITY: CAT1 | RISK: Immediate exploit vector, no authentication barrier.

EXAMPLE 2:
FINDING: Audit logs not reviewed within 72-hour window.
→ SEVERITY: CAT2 | RISK: Delayed detection of intrusion, NIST AU-6 non-compliant.

EXAMPLE 3:
FINDING: Screen lock timeout set to 16 minutes instead of 15.
→ SEVERITY: CAT3 | RISK: Minor policy deviation, low exploit probability.

NOW CLASSIFY:
FINDING: SSH root login permitted on bastion host.
```

The model has learned from 3 examples exactly what CAT1 vs CAT3 looks like in your context.

## Your task

Complete `classify_finding()` to build a few-shot prompt for STIG severity classification. Use at least 2 examples, then classify the provided finding and return the result.
