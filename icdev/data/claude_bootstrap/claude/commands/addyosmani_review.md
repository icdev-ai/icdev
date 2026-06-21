---
name: addyosmani-review
description: "Five-axis code review: correctness, clarity, security, performance, testability."
source: addyosmani/agent-skills
allowed-tools:
- Read
- Write
- Edit
- Bash
- Grep
- Glob
tags:
- addyosmani
- engineering-discipline
---

Invoke the addyosmani code-review-and-quality skill (five-axis review).

## What This Does
Performs a five-axis code review on staged changes or a specified file.

## Axes
1. **CORRECTNESS**: Does it match the spec and acceptance criteria?
2. **CLARITY**: Will a newcomer understand it in 6 months?
3. **SECURITY**: OWASP Top 10, SIPA scan clean, no hardcoded secrets?
4. **PERFORMANCE**: No N+1 queries, no hot-path allocations, no blocking I/O?
5. **TESTABILITY**: Isolated, deterministic, fast, no side effects?

## Steps
1. Run: `ruff check $ARGUMENTS`
2. Run: `python -m bandit $ARGUMENTS --severity-level medium`
3. Review each axis against the code
4. Flag any axis with issues
5. Produce a review report with severity (BLOCK / WARN / NOTE) per finding

## Arguments
$ARGUMENTS — file path or PR diff to review

## Source Skill
.agents/skills/addyosmani-code-review-and-quality/SKILL.md
