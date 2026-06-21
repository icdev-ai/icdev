---
allowed-tools:
- Read
- Grep
- Glob
context: fork
description: 'Systematic debugging: reproduce → isolate → root cause → fix → verify.'
name: debug-issue
tags:
- debugging
- troubleshooting
---
# Debug Issue

CUI // SP-CTI

## Overview

Systematic debugging: reproduce → isolate → root cause → fix → verify.

## Provenance

- **Source:** OpenClaw Community (SkillHub)
- **Author:** anthropic
- **Original URL:** local://official-seed/claude/claude-debug-issue
- **Import Date:** 2026-06-14T15:45:42.531476+00:00
- **SHA-256:** 36b0667238e21414a54212b77bf7a637631c84276bb0f159ea48efd67af36a35
- **Scan Status:** PASSED (all 10 gates)
- **Trust Score:** 0.3
- **Registration Required:** No
- **Renewal Required:** No

## Instructions

# Debug Issue

CUI // SP-CTI

## Overview

Systematic debugging: reproduce → isolate → root cause → fix → verify.

## Provenance

- **Enhanced by:** ICDEV™ (Innovation + Creative + Research engines)
- **Original Author:** anthropic
- **Source:** OpenClaw Community (SkillHub)
- **Author:** anthropic
- **Original Version:** 1.0.0
- **Compatibility Score:** 96/100
- **Auto-Adaptations:** 2

## Instructions

# Debug Issue

Systematically debug the provided error or unexpected behavior.

## Instructions

Follow the DEBUG protocol:

**D — Describe**: Restate the observed behavior vs expected behavior in concrete terms.

**E — Evidence**: List all available evidence (stack trace, logs, test output, reproduction steps).

**B — Bisect**: Identify the smallest reproducing case. Which line/function is the first wrong?

**U — Understand**: Explain WHY the bug occurs — the root cause, not just the symptom.

**G — Generate fix**: Propose the minimal, targeted fix. Do not refactor beyond the bug scope.

Output:
1. Root cause (1-2 sentences)
2. Affected code location
3. Minimal fix (code diff)
4. Verification steps (how to confirm it's fixed)

## Usage

```
/claude-debug-issue $ARGUMENTS
```

Provide: error message, stack trace, or failing test output as $ARGUMENTS.


## Enrichment (ICDEV™ Intelligence)

*Auto-generated on 2026-06-14 by Innovation + Creative + Research engines*

