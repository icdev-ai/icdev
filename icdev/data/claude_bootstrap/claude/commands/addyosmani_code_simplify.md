---
name: addyosmani-simplify
description: "Simplify code. Three similar lines beats one clever abstraction."
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

Invoke the addyosmani code-simplification skill.

## What This Does
Identifies and removes unnecessary complexity. Applies YAGNI and Chesterton's Fence principles.

## Simplification Rules
- Three similar lines > one premature abstraction
- Explicit > implicit
- Inline before extracting
- Names describe WHAT a thing IS, not what it does
- Remove dead code, unused imports, commented-out code
- No speculative generality

## Steps
1. Read $ARGUMENTS
2. Identify complexity: long functions (> 30 lines), deep nesting (> 3 levels), unclear names
3. Apply targeted simplification (one at a time)
4. Run tests after each simplification
5. Report: before/after line count, complexity score delta

## Arguments
$ARGUMENTS — file or function to simplify

## Source Skill
.agents/skills/addyosmani-code-simplification/SKILL.md
