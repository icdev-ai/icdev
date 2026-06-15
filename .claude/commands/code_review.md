---
allowed-tools:
- Read
- Grep
- Glob
context: fork
description: Thorough, structured code review covering correctness, security, style,
  and test coverage.
name: code-review
tags:
- code-quality
- security
- review
---
# Code Review

CUI // SP-CTI

## Overview

Thorough, structured code review covering correctness, security, style, and test coverage.

## Provenance

- **Source:** OpenClaw Community (SkillHub)
- **Author:** anthropic
- **Original URL:** local://official-seed/claude/claude-code-review
- **Import Date:** 2026-06-14T15:45:42.460754+00:00
- **SHA-256:** 58869a19b0696843f360a4aee8fd0e9d1e83acfaa4a0bf36e23ce62865f52827
- **Scan Status:** PASSED (all 10 gates)
- **Trust Score:** 0.3
- **Registration Required:** No
- **Renewal Required:** No

## Instructions

# Code Review

CUI // SP-CTI

## Overview

Thorough, structured code review covering correctness, security, style, and test coverage.

## Provenance

- **Enhanced by:** ICDEV™ (Innovation + Creative + Research engines)
- **Original Author:** anthropic
- **Source:** OpenClaw Community (SkillHub)
- **Author:** anthropic
- **Original Version:** 1.2.0
- **Compatibility Score:** 96/100
- **Auto-Adaptations:** 2

## Instructions

# Code Review

Perform a thorough code review of the provided code or diff.

## Instructions

Analyze the code across four dimensions:

**1. Correctness**
- Logic errors, off-by-one errors, null/undefined handling
- Edge cases that may cause unexpected behavior
- Algorithm correctness and complexity

**2. Security (OWASP Top 10)**
- Injection vulnerabilities (SQL, command, LDAP)
- Authentication and authorization gaps
- Sensitive data exposure (hardcoded secrets, logging PII)
- Input validation and output encoding

**3. Code Quality**
- Naming clarity and consistency
- Function/class cohesion and coupling
- Duplicated logic or missed abstractions
- Dead code or unused imports

**4. Test Coverage**
- Are happy paths tested?
- Are error/edge cases covered?
- Are mocks appropriate (not masking real behavior)?

For each finding: state the location, severity (critical/high/medium/low), and a concrete fix.

## Usage

```
/claude-code-review $ARGUMENTS
```

Provide: file path, code snippet, or PR diff as $ARGUMENTS.


## Enrichment (ICDEV™ Intelligence)

*Auto-generated on 2026-06-14 by Innovation + Creative + Research engines*

