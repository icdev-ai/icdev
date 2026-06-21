---
allowed-tools:
- Read
- Grep
- Glob
context: fork
description: Static analysis tool for Python, JavaScript, Go, and Rust using local
  linters. Air-gap safe.
name: code-analysis-tool
tags:
- static-analysis
- linting
- security
- air-gap
---
# Code Analysis Tool

CUI // SP-CTI

## Overview

Static analysis tool for Python, JavaScript, Go, and Rust using local linters. Air-gap safe.

## Provenance

- **Source:** OpenClaw Community (SkillHub)
- **Author:** langchain-ai
- **Original URL:** local://official-seed/langchain/langchain-code-analysis-tool
- **Import Date:** 2026-06-14T15:45:42.882752+00:00
- **SHA-256:** c179b6c46d03a4733456b44ec90e4764349c9ce3f2a25af94630dd792c8ea2b1
- **Scan Status:** PASSED (all 10 gates)
- **Trust Score:** 0.3
- **Registration Required:** No
- **Renewal Required:** No

## Instructions

# Code Analysis Tool

CUI // SP-CTI

## Overview

Static analysis tool for Python, JavaScript, Go, and Rust using local linters. Air-gap safe.

## Provenance

- **Enhanced by:** ICDEV™ (Innovation + Creative + Research engines)
- **Original Author:** langchain-ai
- **Source:** OpenClaw Community (SkillHub)
- **Author:** langchain-ai
- **Original Version:** 0.2.0
- **Compatibility Score:** 94/100
- **Auto-Adaptations:** 3

## Instructions

{
  "name": "analyze_code",
  "description": "Run static analysis on code using local tools (ruff, eslint-local, golangci-lint, clippy). Air-gap safe — no network calls. Returns structured findings with line numbers and fix suggestions.",
  "args_schema": {
    "type": "object",
    "properties": {
      "code": {
        "type": "string",
        "description": "Source code to analyze"
      },
      "language": {
        "type": "string",
        "enum": ["python", "javascript", "typescript", "go", "rust"],
        "description": "Programming language of the code"
      },
      "checks": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Specific checks to run: ['style', 'security', 'complexity', 'all']"
      }
    },
    "required": ["code", "language"]
  },
  "return_direct": false
}


## Enrichment (ICDEV™ Intelligence)

*Auto-generated on 2026-06-14 by Innovation + Creative + Research engines*

