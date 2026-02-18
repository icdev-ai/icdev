# CUI // SP-CTI
# Skill: icdev-worktree
# Git Worktree Task Isolation

## Description
Create and manage isolated git worktrees for parallel ICDEV task execution.

## Usage
/icdev-worktree create <task-id> <target-dir>
/icdev-worktree list
/icdev-worktree cleanup <worktree-name>
/icdev-worktree status <worktree-name>

## Examples
- /icdev-worktree create issue-42 src/
- /icdev-worktree list
- /icdev-worktree cleanup icdev-issue-42
- /icdev-worktree status icdev-issue-42

## Workflow
1. Create sparse-checkout worktree in trees/ directory
2. New branch icdev-<task-id> created automatically
3. CUI classification marker written
4. Agent identity tracked in database
5. Cleanup removes worktree and updates DB

## Tool
python tools/ci/modules/worktree.py
