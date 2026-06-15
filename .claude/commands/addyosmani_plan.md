---
name: addyosmani-plan
description: "Task breakdown: decompose approved spec into atomic Kanban tasks with dependency mapping."
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

Invoke the addyosmani planning-and-task-breakdown skill.

## What This Does
Decomposes an approved spec into atomic tasks (max 2h each) with dependency mapping.
Seeds tasks into Kanban via tools/kanban/task_factory.py.

## Steps
1. Read the approved spec (SPEC.md or $ARGUMENTS path)
2. Enumerate atomic tasks — each completable in < 2h
3. Map dependencies: Task B depends on Task A
4. Identify critical path
5. Seed tasks: python tools/kanban/task_factory.py --project $PROJECT --tasks [...]

## Arguments
$ARGUMENTS — path to SPEC.md or task prefix (e.g. "feat-01")

## Source Skill
.agents/skills/addyosmani-planning-and-task-breakdown/SKILL.md
