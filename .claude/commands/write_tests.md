---
allowed-tools:
- Read
- Grep
- Glob
context: fork
description: Generate comprehensive unit and integration tests for any function, class,
  or module.
name: write-tests
tags:
- testing
- tdd
- quality
---
# Write Tests

CUI // SP-CTI

## Overview

Generate comprehensive unit and integration tests for any function, class, or module.

## Provenance

- **Source:** OpenClaw Community (SkillHub)
- **Author:** anthropic
- **Original URL:** local://official-seed/claude/claude-write-tests
- **Import Date:** 2026-06-14T15:45:42.498467+00:00
- **SHA-256:** 25e4496c7d4ced1213c8f15047b62253405fa1595d6affb3eb276c9d0a40fd99
- **Scan Status:** PASSED (all 10 gates)
- **Trust Score:** 0.3
- **Registration Required:** No
- **Renewal Required:** No

## Instructions

# Write Tests

CUI // SP-CTI

## Overview

Generate comprehensive unit and integration tests for any function, class, or module.

## Provenance

- **Enhanced by:** ICDEV™ (Innovation + Creative + Research engines)
- **Original Author:** anthropic
- **Source:** OpenClaw Community (SkillHub)
- **Author:** anthropic
- **Original Version:** 1.1.0
- **Compatibility Score:** 96/100
- **Auto-Adaptations:** 2

## Instructions

# Write Tests

Generate a comprehensive test suite for the provided code.

## Instructions

1. **Identify the public interface** — functions, methods, classes, API endpoints
2. **Map test categories**:
   - Happy path (expected inputs → expected outputs)
   - Boundary conditions (empty, zero, max, min, None)
   - Error conditions (invalid input, missing args, type errors)
   - Integration points (DB calls, HTTP calls — mock appropriately)
3. **Write tests** using the project's existing test framework (detect: pytest, Jest, JUnit, etc.)
4. **Add docstrings** explaining what each test validates
5. **Target ≥80% branch coverage** for the submitted code

Name tests `test_<function>_<scenario>` for clarity.
Do not test framework internals — only the code provided.

## Usage

```
/claude-write-tests $ARGUMENTS
```

Provide: file path or function definition as $ARGUMENTS.


## Enrichment (ICDEV™ Intelligence)

*Auto-generated on 2026-06-14 by Innovation + Creative + Research engines*

