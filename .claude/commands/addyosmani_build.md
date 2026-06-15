---
name: addyosmani-build
description: "Incremental implementation: build via vertical slices with feature flags."
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

Invoke the addyosmani incremental-implementation skill.

## What This Does
Implements the thinnest possible vertical slice, gates it behind a feature flag, ships, then repeats.
Never implements everything at once.

## Steps
1. Identify the thinnest vertical slice that delivers user value
2. Add feature flag: ICDEV_<FEATURE>_ENABLED=false in .env
3. Implement the slice (backend + frontend + test)
4. Verify slice works end-to-end (Playwright smoke test)
5. Ship slice, monitor for regressions
6. Next slice — repeat until full feature complete
7. Remove feature flag when stable (ruff clean, tests green)

## Arguments
$ARGUMENTS — feature name or task ID to implement

## Source Skill
.agents/skills/addyosmani-incremental-implementation/SKILL.md
