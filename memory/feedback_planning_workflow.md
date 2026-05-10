---
name: Standard Planning and Execution Workflow
description: Every implementation task must follow plan→decompose→dependency-order→kanban→harmonize→V&V. Non-negotiable default behavior.
type: feedback
---

Every non-trivial implementation task follows this exact sequence without being asked:

**1. Plan and decompose with priority + dependency order**
- Decompose into phases, then tasks within each phase
- Assign priorities (critical / high / medium / low)
- Establish explicit dependency chains — no task starts until its blocker is done
- Phases that are independent of each other run in parallel (no artificial sequencing)
- Register multi-epic initiatives in `args/projects.yaml` with `key`, `name`, `task_prefix`, `briefs[]`, `epics[]`
- Task IDs MUST use form `<task_prefix><epic_key>-<N>` (e.g. `fdm-crd-01`)

**2. Add plan to Kanban**
- Insert all tasks into the Kanban board with correct `depends_on_task_id` chains
- Use the PostgreSQL-backed `get_connection()` with dotenv loaded — not raw sqlite3
- Each task description must be specific enough for autonomous execution (file paths, thresholds, API names, expected outputs)

**3. Each phase ends with a V&V gate task**
- V&V task = CodeLens + Coherence checker + Selenium E2E lifecycle (including regression)
- Exact commands: `python tools/workflow/coherence_checker.py --all --fix --gate` and `python tools/testing/e2e_runner.py --run-all`
- Next phase is blocked until the V&V task passes
- Never report a phase complete without running V&V

**4. Harmonization pass (built into final phase)**
- After all capability phases: review and harmonize the entire affected subsystem
- Ensure new signals/features are surfaced consistently across all related pages
- No page should show stale, missing, or contradictory signal state
- Companion sync: `python tools/dx/companion.py --sync --write --json`

**Why:** This sequence was codified 2026-04-21 after the FathomDesk Macro Intelligence Expansion planning session. The user explicitly confirmed this should be the default for all future work, not something that needs to be requested.

**How to apply:** Any time the user asks to plan, build, or implement anything non-trivial — apply this workflow automatically. Do not wait to be asked about kanban, V&V, or harmonization.
