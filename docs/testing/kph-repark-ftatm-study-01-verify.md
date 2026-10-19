# CUI // SP-CTI

# Verification — kph-repark-ftatm-study-01-d4 (2026-10-19)

Follows `kph-repark-ftatm-study-01-triage.md` (d3). That note found the cause:
`ftatm-` was missing from `args/kanban_external_repos.yaml`, so adds ran against the
public ICDev checkout. #2503 fixed it. This note checks the fix through the dispatch
code path and records why the requeue step was **not** run.

## 1. Worktree creation through the dispatch path

Probe id `ftatm-probe-d4`. It routes by the same `ftatm-` prefix as the subject, so
the real `kanban/ftatm-study-01` branch is never touched. The probe calls the
runner's own `tools.genesis.reflexes.kanban._create_worktree`:

| check | result |
|---|---|
| `resolve_task_repo('ftatm-study-01')` | `icdev_ft`, root `C:/ai/icdev_ft`, `is_external=True` |
| `resolve_task_repo('ftatm-probe-d4')` | same target |
| `_create_worktree('ftatm-probe-d4')` | `%TEMP%\icdev-kanban\icdev_ft\ftatm-probe-d4` in **1.63s** (budget 30s) |
| returned path vs `BASE_DIR` (shared checkout) | different, so no fallback to the shared checkout |
| cleanup | `git worktree remove` + `git branch -d kanban/ftatm-probe-d4` (branch was at `origin/main` `2cc436d`, no commits); `worktree list` shows 0 probe entries |

## 2. Requeue deliberately NOT run

`python tools/kanban/cli.py --show ftatm-study-01` (before and after):

```
  id:       ftatm-study-01
  title:    STUDY: SMC Python port to v1.1 + parity with NT8
  status:   done
  updated:  2026-10-16 08:43:50.995061+00:00
```

The subject finished on the first dispatch after #2503, and icdev_ft PR #543
merged on 2026-10-16T08:43Z (see d3). This card's acceptance was
`--requeue ftatm-study-01 --requeue-status scheduled`. Running it would send a
merged task back to the runner and produce a duplicate PR, the same kind of
duplicate kpr-dup-07 had to clean up. Acceptance "status scheduled" was written
before anyone knew the subject was done, so it no longer applies. Leaving the
subject `done` is correct.

## 3. Stranded audit

`stranded_audit` only puts a row in `orphan_validating` when its `status ==
'validating'` and it has no branch (`tools/kanban/stranded_audit.py:351`). A `done`
row cannot appear there. See the live run result in the card comment on
`kph-repark-ftatm-study-01`.
