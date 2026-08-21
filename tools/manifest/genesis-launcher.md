# Genesis Launcher

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Genesis Launcher
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Genesis Launcher | tools/genesis/launcher.py | Genesis daemon launcher and control | (library) | Daemon lifecycle |

| Supervisor Status | tools/genesis/supervisor_status.py | Is the launcher supervisor up, and what is it supervising? Reads `.tmp/genesis/launcher.pid` plus the process table, and the code identity each child booted with. `--ensure` starts THE SUPERVISOR when none is running and DEFERS when one is (or when its state is unknown -- starting on uncertainty is how duplicates begin); it never starts an individual child and it kills nothing. Replaces the `/start` steps that raced the supervisor and lost silently. (autonomy-id-03) | --json, --ensure, --dry-run, --root PATH | Supervisor state (up/down/unknown) + per-service pids, logs and recorded code version |
