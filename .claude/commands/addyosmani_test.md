---
name: addyosmani-test
description: "TDD Red-Green-Refactor cycle. Write failing test before implementation."
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

Invoke the addyosmani test-driven-development skill.

## What This Does
Enforces Red-Green-Refactor TDD cycle. Write the failing test FIRST, then the implementation.

## Steps
1. Identify the behavior to test (from spec acceptance criteria)
2. Write failing test (RED) — run pytest to confirm it fails
3. Implement minimum code to make test pass (GREEN)
4. Refactor: clean up without breaking tests
5. Repeat for next behavior
6. Target: 80% unit / 15% integration / 5% E2E coverage

## Arguments
$ARGUMENTS — function, class, or module to test

## Commands
```bash
pytest tests/test_$ARGUMENTS.py -v --tb=short
python tools/testing/api_surface_extractor.py --file $ARGUMENTS --json
```

## Source Skill
.agents/skills/addyosmani-test-driven-development/SKILL.md
