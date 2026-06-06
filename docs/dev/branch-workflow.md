<!-- CUI // SP-CTI -->
# Branch → Test → V&V → MR Workflow

**Best practice:** isolate work on a new branch, run the full validation gauntlet locally, and open the MR **only after everything is green**. A human merges to `main` — agents never self-merge.

This is the existing ANVIL 5-phase flow (`/feature`, `/bug`, `/chore`) condensed to one page. The slash commands automate every phase and write an audit trail; the commands below are also what you run if you drive it by hand.

---

## TL;DR (happy path, top to bottom)

```bash
# 1. Fresh, isolated branch off main
git checkout main && git pull
git checkout -b feat-issue-123-icdev-a1b2c3d4-add-auth   # see naming below

# 2. Implement (ANVIL auto-runs plan→implement→validate→commit→close)
#    In Claude Code:  /feature "add auth"
#    Headless:        python tools/anvil/feature.py --json -- "add auth"

# 3. Validation gauntlet — ALL must pass before commit
ruff check .                                                   # lint gatekeeper
python tools/workflow/coherence_checker.py --all --fix --gate  # run BEFORE pytest
pytest tests/ -v --tb=short                                    # unit (SQLite forced, >=80% cov)
behave features/                                               # BDD (if features/ touched)
python -m bandit -r tools/ --severity-level medium             # SAST
python tools/dx/companion.py --sync --write --json             # foreground, then commit

# 4. Open the MR (only when green) — pushes + creates PR/MR
#    /pull_request   (gh pr create / glab mr create under the hood)

# 5. Human reviews and merges to main.
```

---

## 1. Branch / worktree (isolation first — ADR D32)

- Always start from an up-to-date base: `git checkout main && git pull`.
- **Branch name** comes from `/generate_branch_name`:
  `<class>-issue-<#>-icdev-<runid>-<name>` — e.g. `feat-issue-123-icdev-a1b2c3d4-add-auth`
  (`class` = `feat` / `bug` / `chore`; `runid` = 8-char UUID hex).
- **Parallel / agent work → use a worktree** (sparse checkout, zero-conflict):
  ```bash
  python tools/ci/modules/worktree.py --create --task-id <id> --json   # branch icdev-<id>
  python tools/ci/modules/worktree.py --cleanup --worktree-name icdev-<id>
  ```
- Real examples in this repo: long-lived integration branch `irad/feature`; Kanban's `kanban/<task-id>` branches.

## 2. Implement via ANVIL

- Drive the work with `/feature "<desc>"` (or `/bug`, `/chore`). All 5 phases run automatically: **Plan → Implement → Validate → Commit → Close**, the plan is written to `specs/`, and progress is posted to the GitHub issue.
- Non-Claude shell: `python tools/anvil/feature.py --json -- "<desc>"`.

## 3. Validation gauntlet (Phase 3 — the "test + V&V")

Run in this order; **lint and coherence first** because they catch cheap failures before you spend a pytest cycle.

| Gate | Command | Pass condition |
|------|---------|----------------|
| Lint (gatekeeper) | `ruff check .` | 0 errors (F401/E block everything) |
| Coherence (before pytest) | `python tools/workflow/coherence_checker.py --all --fix --gate` | gate exits 0 (14 checks) |
| Unit tests | `pytest tests/ -v --tb=short` | 0 failed, coverage ≥ 80% |
| BDD | `behave features/` | 0 failed (if `features/` touched) |
| **V&V — Playwright E2E** *(MANDATORY if UI/dashboard/route changed)* | start `python tools/dashboard/app.py`; `python tools/testing/e2e_runner.py --run-all` | 0 failed, no 500/TemplateNotFound; screenshots → `playwright/screenshots/<name>.png`. Mark N/A w/ reason if no UI change |
| Acceptance V&V | `python tools/testing/acceptance_validator.py --plan specs/<plan> --json` | 0 failed criteria ("did we build what was asked?") |
| Security | `python -m bandit -r tools/ --severity-level medium` | 0 critical/high; 0 secrets; deps clean; CUI markings present |
| Canvas smoke *(if applicable)* | e.g. `python tools/testing/fathomdesk_smoke.py` | 0 import/schema failures |
| Companion sync (always) | `python tools/dx/companion.py --sync --write --json` | run **foreground**, then commit |

> Companion sync **must run in the foreground**. A background sync has previously deleted uncommitted work — commit first, sync second.

## 4. Commit & open the MR (only after green)

- ANVIL Phase 4 commits the change plus a validation report under `audit/`.
- `/pull_request` pushes the branch and creates the PR/MR:
  - Title: `<type>: #<issue> - <title>`
  - Body: links the `specs/` plan, `Closes #<n>`, the run-id, and `CUI // SP-CTI`.
  - GitHub → `gh pr create`; GitLab → `glab mr create`.
- **A human merges to `main`.** Agents do not self-merge (cf. PR #36).

---

## Windows / gotchas

- **CI-fix tasks:** run the **exact CI ruff command**, not bare `ruff check .` — the fast Ruff Lint job gates E2E, and "fix CI run X" tasks often point at a stale commit already fixed in HEAD.
- **Stale worktrees:** verify the implementation isn't already on `main` before re-implementing.
- **Scheduler:** after killing any `python.exe`, restart the scheduler immediately (killing python wedges it).
- **PowerShell:** use `Start-Sleep`, `2>$null`, `Stop-Process` (not `sleep`/`2>/dev/null`/`pkill`); set `$env:PYTHONPATH="C:\AI\ICDev"`.

<!-- CUI // SP-CTI -->
