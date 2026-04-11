---
name: icdev-worktree
description: "Creates and manages isolated git worktrees for ICDEV™ tasks, ensuring each worktree is properly initialized with project context and CUI markings. Use when starting work on a new kanban task, creating an isolated branch environment, or managing parallel development streams for an ICDEV™ project."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-worktree

## What This Does
Manages git worktrees for isolated ICDEV™ task execution:
1. **Create** — scaffold a new worktree from a kanban task ID with a generated branch name
2. **Initialize** — load session context and CUI markings into the new worktree
3. **List** — show all active worktrees and their associated task IDs
4. **Cleanup** — remove completed worktrees and merge or discard branches

## Example
```
$icdev-worktree create --task-id task-abc123
$icdev-worktree list
$icdev-worktree cleanup --task-id task-abc123
```

## Error Handling
- If task ID not found: check kanban board at `http://localhost:5050`
- If branch already exists: append suffix or prompt to reuse
- If worktree directory exists: prompt before overwriting
