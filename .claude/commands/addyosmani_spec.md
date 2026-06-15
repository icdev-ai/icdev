---
name: addyosmani-spec
description: "Spec-driven development: author a PRD before writing any code."
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

Invoke the addyosmani spec-driven-development skill.

## What This Does
Authors a PRD (Product Requirements Document) before writing any code.
Covers objectives, success criteria, out-of-scope, testing strategy, and implementation boundaries.

## Steps
1. Elicit requirements to 95% confidence via structured Q&A (use /addyosmani-interview-me if unclear)
2. Draft SPEC.md with:
   - Objectives and success criteria
   - User stories with acceptance criteria
   - Out-of-scope items
   - Testing strategy (unit / integration / E2E)
   - Implementation boundaries and constraints
3. Present SPEC.md for approval
4. Do NOT write implementation code until spec is approved

## Arguments
$ARGUMENTS — task or feature description (optional; will use interview-me if omitted)

## Source Skill
.agents/skills/addyosmani-spec-driven-development/SKILL.md
