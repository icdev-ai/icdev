# [TEMPLATE: CUI // SP-CTI]
# Worktree Setup Prompt — Task Isolation Instructions

You are setting up an isolated git worktree for an ICDEV task.

## Context
- Task ID: {{task_id}}
- Target Directory: {{target_dir}}
- Classification: {{classification}}
- Issue Number: {{issue_number}}

## Instructions
1. The worktree has been created at `trees/{{task_id}}/` with sparse checkout
2. You are on branch `icdev-{{task_id}}`
3. Classification marking: {{classification}} // SP-CTI
4. All generated files MUST include classification banners

## Working in the Worktree
- Only files in `{{target_dir}}` are checked out
- Commit changes to your branch: `icdev-{{task_id}}`
- Do NOT modify files outside the sparse checkout scope
- Do NOT merge or rebase against main without review

## Completion
When your task is complete:
1. Commit all changes with message: `icdev: {{task_id}}: <description>`
2. Push branch to remote
3. Create merge request via `glab mr create`
4. Signal completion to the task monitor

## CUI Handling
- All generated code files: `# CUI // SP-CTI` header
- All generated YAML files: `# CUI // SP-CTI` header
- All generated markdown: `# CUI // SP-CTI` header
- Docker images: classification labels
