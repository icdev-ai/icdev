---
allowed-tools:
- Read
- Grep
- Glob
context: fork
description: 'Code analysis functions: explain, review, generate tests, and detect
  vulnerabilities.'
name: code-intelligence
tags:
- code-analysis
- security
- testing
---
# Code Intelligence

CUI // SP-CTI

## Overview

Code analysis functions: explain, review, generate tests, and detect vulnerabilities.

## Provenance

- **Source:** OpenClaw Community (SkillHub)
- **Author:** google-deepmind
- **Original URL:** local://official-seed/gemini/gemini-code-intelligence
- **Import Date:** 2026-06-14T15:45:42.741332+00:00
- **SHA-256:** b44af04a409f08050a8ce55047df4b23dc4d625fb3cab44f37508b2bd00124c8
- **Scan Status:** PASSED (all 10 gates)
- **Trust Score:** 0.3
- **Registration Required:** No
- **Renewal Required:** No

## Instructions

# Code Intelligence

CUI // SP-CTI

## Overview

Code analysis functions: explain, review, generate tests, and detect vulnerabilities.

## Provenance

- **Enhanced by:** ICDEV™ (Innovation + Creative + Research engines)
- **Original Author:** google-deepmind
- **Source:** OpenClaw Community (SkillHub)
- **Author:** google-deepmind
- **Original Version:** 1.0.0
- **Compatibility Score:** 94/100
- **Auto-Adaptations:** 3

## Instructions

{
  "functionDeclarations": [
    {
      "name": "analyze_code_security",
      "description": "Analyze code for security vulnerabilities. Returns findings with CWE mappings and remediation guidance.",
      "parameters": {
        "type": "object",
        "properties": {
          "code": {"type": "string", "description": "Source code to analyze"},
          "language": {"type": "string", "description": "Programming language (python, javascript, go, java, rust, etc.)"},
          "severity_threshold": {"type": "string", "enum": ["info", "low", "medium", "high", "critical"], "description": "Minimum severity to report"}
        },
        "required": ["code", "language"]
      }
    },
    {
      "name": "generate_test_suite",
      "description": "Generate a comprehensive test suite for the provided code.",
      "parameters": {
        "type": "object",
        "properties": {
          "code": {"type": "string", "description": "Code to generate tests for"},
          "test_framework": {"type": "string", "description": "Test framework: pytest, jest, junit, go-test, rspec"},
          "coverage_target": {"type": "number", "description": "Target branch coverage percentage (0-100, default 80)"},
          "include_mocks": {"type": "boolean", "description": "Whether to generate mock objects for dependencies"}
        },
        "required": ["code"]
      }
    },
    {
      "name": "explain_architecture",
      "description": "Explain the architecture of a codebase from provided file contents or descriptions.",
      "parameters": {
        "type": "object",
        "properties": {
          "files": {"type": "array", "items": {"type": "string"}, "description": "List of file contents or summaries"},
          "audience": {"type": "string", "enum": ["executive", "developer", "architect", "junior"], "description": "Target audience for the explanation"},
          "output_format": {"type": "string", "enum": ["markdown", "mermaid", "json"], "description": "Output format"}
        },
        "required": ["files"]
      }
    }
  ]
}


## Enrichment (ICDEV™ Intelligence)

*Auto-generated on 2026-06-14 by Innovation + Creative + Research engines*

