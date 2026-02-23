# [TEMPLATE: CUI // SP-CTI]
# Initialize Git Worktree for Task Isolation

Create an isolated git worktree with sparse checkout for parallel task execution.
This enables multiple ICDEV workflows to run simultaneously without conflicts.

## Parameters
- **task-id**: The task or issue identifier (e.g., issue number, ticket ID)
- **target-dir**: The directory to sparse-checkout (e.g., `src/`, `tools/`, `.`)

## Steps

1. **Create worktree** with sparse checkout:
   ```bash
   python tools/ci/modules/worktree.py --create --task-id $ARGUMENTS --target-dir . --json
   ```

2. **Verify** the worktree was created:
   ```bash
   python tools/ci/modules/worktree.py --list --json
   ```

3. **Report** the worktree details including path, branch, and classification.

## Notes
- Each worktree gets its own branch: `icdev-<task-id>`
- Classification marker written to `.classification` file
- Agent identity written to `.icdev-agent` file
- Worktree state tracked in `ci_worktrees` database table
- Cleanup with: `python tools/ci/modules/worktree.py --cleanup --worktree-name icdev-<task-id>`
