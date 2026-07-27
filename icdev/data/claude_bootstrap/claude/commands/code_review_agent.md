---
allowed-tools:
- Read
- Grep
- Glob
context: fork
description: AutoGen conversational agent for iterative code review with human-in-the-loop
  approval.
name: code-review-agent
tags:
- code-review
- autogen
- hitl
- iterative
---
# Code Review Agent

CUI // SP-CTI

> **⚠️ Reference seed — NOT an executable capability (oss2-fix-03 / D3).**
> This card embeds an AutoGen agent definition, but **AutoGen is not an ICDEV
> dependency and nothing executes it** — `autogen` is imported nowhere in `tools/`.
> It is retained as a design reference from the SkillHub seed, not a wired agent.
> For the capability it describes, use ICDEV's actual implementation: the `/review`
> command (`.claude/commands/review.md`) or the review-until-green loop
> (`tools/quality/review_loop.py`).

## Overview

AutoGen conversational agent for iterative code review with human-in-the-loop approval.

## Provenance

- **Source:** OpenClaw Community (SkillHub)
- **Author:** microsoft
- **Original URL:** local://official-seed/autogen/autogen-code-review-agent
- **Import Date:** 2026-06-14T15:45:42.983277+00:00
- **SHA-256:** e49f9bae69ca19490de9745bafcbc6cf7e417d16f32cc7f663ce3efe77232180
- **Scan Status:** PASSED (all 10 gates)
- **Trust Score:** 0.3
- **Registration Required:** No
- **Renewal Required:** No

## Instructions

# Code Review Agent

CUI // SP-CTI

## Overview

AutoGen conversational agent for iterative code review with human-in-the-loop approval.

## Provenance

- **Enhanced by:** ICDEV™ (Innovation + Creative + Research engines)
- **Original Author:** microsoft
- **Source:** OpenClaw Community (SkillHub)
- **Author:** microsoft
- **Original Version:** 0.4.0
- **Compatibility Score:** 94/100
- **Auto-Adaptations:** 3

## Instructions

{
  "name": "CodeReviewAgent",
  "description": "Conversational code review agent that performs iterative review cycles with human-in-the-loop approval gates. Stops after MAX_REVIEW_ROUNDS or when human approves.",
  "system_message": "You are an expert code reviewer participating in a review conversation.\n\nIn each turn:\n1. Analyze the code or the author's response to your previous feedback\n2. List specific findings with file:line references and severity (CRITICAL/HIGH/MEDIUM/LOW)\n3. For CRITICAL/HIGH findings: require the author to address them before approving\n4. For MEDIUM/LOW findings: note them but do not block approval\n5. When all CRITICAL/HIGH findings are resolved, output exactly: REVIEW_APPROVED\n\nBe constructive. Cite the specific line. Suggest the exact fix.\nDo not repeat already-resolved findings.",
  "human_input_mode": "TERMINATE",
  "max_consecutive_auto_reply": 8,
  "code_execution_config": false,
  "llm_config": {
    "temperature": 0.1,
    "functions": [
      {
        "name": "flag_critical_finding",
        "description": "Flag a critical security or correctness issue that blocks approval",
        "parameters": {
          "type": "object",
          "properties": {
            "file": {"type": "string"},
            "line": {"type": "integer"},
            "description": {"type": "string"},
            "suggested_fix": {"type": "string"}
          },
          "required": ["file", "description"]
        }
      }
    ]
  }
}


## Enrichment (ICDEV™ Intelligence)

*Auto-generated on 2026-06-14 by Innovation + Creative + Research engines*

