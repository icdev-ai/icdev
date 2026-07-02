# Hypothesis-First Debugging

> Root cause analysis pattern: generate ranked hypotheses before proposing any fix.
> Source: adapted from "Bug Hunter" prompt (50 Mega-Prompts, 2026).
> Use in: ai_developer role, debugging flows, incident post-mortems.

---

## The Core Problem

Junior engineers (and naive LLMs) jump directly to a fix for the most obvious symptom.
This produces:
- Patches that fix symptoms but not root causes
- Regression bugs when the real cause manifests differently
- No prevention — the same class of bug recurs

**Hypothesis-first debugging** forces explicit enumeration of possible causes, ranked by
likelihood, before any code is written.

---

## Pattern

### Step 1 — HYPOTHESIS LIST

Generate 5–7 ranked hypotheses for the root cause (most likely first).

For each hypothesis:
- **Hypothesis**: What the root cause might be
- **Evidence that would confirm it**: What to check / what log line to look for
- **Evidence that would eliminate it**: What rules it out

```
HYPOTHESIS LIST (ranked by likelihood):
1. [Most likely] — [what would confirm / what would eliminate]
2. ...
7. [Least likely but worth checking]
```

### Step 2 — ROOT CAUSE ANALYSIS

Once the most likely hypothesis is identified:

```
ROOT CAUSE:
  What triggers it: [specific condition]
  What state becomes corrupted: [what breaks internally]
  Why the symptom appears: [chain from trigger to visible failure]

Logic chain: [A] → because [B] → therefore [C]
```

Never say "the bug is caused by X" without the full chain.

### Step 3 — REPRODUCTION STEPS

Write exact steps a developer unfamiliar with the bug can follow to reproduce it.

```
REPRODUCTION:
  Environment: [OS, runtime, DB version, etc.]
  Setup: [seed data, config, env vars]
  Steps:
    1. [exact action]
    2. [exact action]
  Expected: [what should happen]
  Actual: [what happens instead]
```

### Step 4 — THE FIX

Show the current code and fixed code side-by-side.

```python
# BEFORE (the bug)
def foo(x):
    return x / x  # ZeroDivisionError when x=0

# AFTER (the fix)
def foo(x):
    if x == 0:          # guard: x=0 is valid input per caller contract
        return 0
    return x / x
```

Every changed line gets an inline comment explaining **WHY** — not what.

### Step 5 — REGRESSION TESTS

3–5 test cases that:
- Catch this exact bug if it returns
- Cover the boundary conditions that caused the failure
- Test adjacent code paths exposed by the same failure mode

### Step 6 — PREVENTION

2–3 systemic improvements that would prevent this *class* of bug:
- Linting rule / type annotation
- Monitoring alert
- Code review checklist item

---

## Prompt Template

```
[SYSTEM]
You are a senior software engineer specializing in debugging complex production issues.
You think systematically, isolate variables, and find root causes — not just symptoms.

Before proposing any fix, apply hypothesis-first debugging:
1. HYPOTHESIS LIST: Generate 5-7 ranked hypotheses (most likely first). For each,
   state what evidence would confirm or eliminate it.
2. ROOT CAUSE: Explain the full chain: trigger → corrupted state → visible symptom.
   Use "because → therefore" logic. Never skip a link in the chain.
3. REPRODUCTION STEPS: Write steps a developer unfamiliar with this bug can follow.
4. THE FIX: Show before/after code. Every changed line must have an inline comment
   explaining WHY the change fixes the issue.
5. REGRESSION TESTS: 3-5 tests that would catch this bug if it returns.
6. PREVENTION: 2-3 systemic improvements (lint rules, type safety, monitoring).

If the provided code/logs are insufficient to diagnose, specify exactly what to provide.
Never guess or fabricate a root cause when evidence is missing.
```

---

## RULES (from source)

- Never suggest "try restarting" as a fix. Root causes only.
- If you are not confident in the root cause, say so and list what additional
  information you need.
- If the code is insufficient to diagnose, specify exactly which files or logs to provide.
- A hypothesis is not a root cause — don't skip from hypothesis to fix.
