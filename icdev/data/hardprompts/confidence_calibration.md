# Confidence Calibration

> How to label epistemic confidence on every claim, data point, and recommendation.
> Source: adapted from "Market Research Analyst" (#41) and "Data Interpreter" (#42),
> 50 Mega-Prompts, 2026. Applies across all LLM-generated analysis in ICDEV.

---

## The Problem

Unqualified claims mix hard facts, estimates, and speculation without signaling which is
which. A reader acts on a "finding" that was actually a guess. Confidence labels make
uncertainty explicit so the reader can calibrate their response.

---

## Three-Tier Confidence System

| Tier | Label | Definition | Example |
|------|-------|-----------|---------|
| 1 | **HIGH** | Verified from authoritative sources; reproducible; directly observed | DB row counts, NIST control text, committed code |
| 2 | **MEDIUM** | Estimated from multiple converging signals; reasonable inference | "Based on 3 separate query paths, the bottleneck is likely the N+1 join" |
| 3 | **LOW** | Directional indicator only; single signal; speculation flagged | "Industry data suggests ~40% adoption, but no ICDEV-specific measurement exists" |

**UNKNOWN** is a fourth state — use it when key data is missing:
> "UNKNOWN — verify during discovery: the tenant_id column existence in this table
>  has not been confirmed; run `\d table_name` to check."

---

## Application by Output Type

### Findings / Insights

Every insight must carry a confidence label and its supporting evidence:

```
FINDING: [one-sentence claim]
EVIDENCE: [specific numbers, file:line, log excerpt, or query result]
CONFIDENCE: HIGH | MEDIUM | LOW
SOURCE: [where the evidence comes from]
SO WHAT: [why it matters for the current task]
NOW WHAT: [the specific recommended action]
```

### Recommendations

```
RECOMMENDATION: [what to do]
CONFIDENCE IN DIAGNOSIS: HIGH | MEDIUM | LOW
RATIONALE: [evidence chain]
RISK IF WRONG: [what breaks if the diagnosis is incorrect]
```

### Data / Metrics

Mark inline: `47% adoption [MEDIUM: extrapolated from 3 customers, N=47]`

---

## Confidence Anti-Patterns to Avoid

| Anti-pattern | Problem | Fix |
|---|---|---|
| "Research shows..." | No source, no confidence | "A 2024 NIST report (SP 800-218) states..." [HIGH] |
| "Significant improvement" | No quantification | "23% reduction in p99 latency" [HIGH from load test] |
| "Likely caused by..." | No evidence chain | Add hypothesis list; mark LOW until confirmed |
| Mixing HIGH/LOW in same sentence without labeling | Reader can't calibrate | Separate each claim |
| Presenting correlation as causation | Logical error | "X correlates with Y [MEDIUM]; causal mechanism unconfirmed" |

---

## Confidence Labels in Reports

Use inline notation for density, block format for key claims:

**Inline:** `Memory leak in worker.py:142 [HIGH — heap profiler confirms 2MB/req growth]`

**Block** (for executive summaries or high-stakes findings):
```
CLAIM: The N+1 query on tenant_permissions is causing >80% of page load time.
CONFIDENCE: MEDIUM
EVIDENCE: EXPLAIN ANALYZE shows 47 sequential scans on tenant_permissions per request
          (observed on 3 separate page loads). Full load test not yet run.
GAP: A targeted benchmark with and without the proposed index would raise this to HIGH.
```

---

## Prompt Template

```
[SYSTEM]
For every claim, data point, and recommendation in your response:
- Attach a confidence label: HIGH (verified/authoritative), MEDIUM (estimated from
  multiple signals), or LOW (directional/single-signal).
- State the evidence supporting each claim explicitly.
- Mark gaps as UNKNOWN — do not fabricate or silently omit missing data.
- Distinguish between correlation and causation. Never present one as the other
  without explicitly flagging it.
- If the data is insufficient to answer the question, say so and specify what
  additional data is needed.
```

---

## Integration with Other Hardprompts

- Combine with `hypothesis_first_debugging.md`: each hypothesis gets a confidence label.
- Combine with `so_what_now_what.md`: the "so what" and "now what" sections carry the
  confidence of the finding they derive from.
- Combine with `multi_scenario_analysis.md`: each scenario assumption gets a confidence label.
