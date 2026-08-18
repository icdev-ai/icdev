# `windows.d` — per-PR fragments for `windows.txt`

Add a test to CI by dropping **one new file here**, named for your task:

```
args/ci_test_files/windows.d/<task-id>.txt
```

One target per line; `#` comments and blank lines are ignored, exactly as in
`windows.txt`. Files are read after `windows.txt` in filename order, so the run
order is deterministic on every machine.

## Why a directory

`windows.txt` was the largest merge-collision surface in the repository. 82.8% of
merged kanban PRs touched `core.txt`, because CLAUDE.md requires every PR adding
a test file to append to it — and GitHub does **not** apply the
`.gitattributes merge=union` rule, so every one of those PRs went CONFLICTING as
soon as a sibling merged. 30.9% needed a rebase; 27.4% escalated to a human.

Two PRs writing two differently-named files cannot conflict at all.

`windows.txt` is unchanged and still authoritative for everything already in it —
this is additive, and nothing was migrated. Both are read as one list, so the
duplicate check, the floor and the census all see the combined set.
