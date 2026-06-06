# Autonomous Recovery v2 — Deep Debugger, Log/Observability Integration, Calibrated Autonomy

> Design document. Status: **proposed** (no code yet). Author: design pass 2026-06-06.
> Scope: deepen the `failure_triage` / `self_debug` autonomous-recovery loop from
> infrastructure-pattern RCA into genuine code-logic debugging, wired into the
> existing logging + observability stack, with a calibration-gated path toward
> fuller autonomy (auto-merge for low-risk task types once precision is proven).

---

## 1. Current state (grounded)

The "Autonomous Recovery" panel on Home is the visible tip of a real pipeline:

```
Genesis daemon ─(every 30m, args/genesis_config.yaml:408)─▶ failure_triage reflex
   └─ tools/genesis/reflexes/failure_triage.py  (thin wrapper)
        └─ tools/workflow/failure_triage.py :: triage_once()            (786)
             ├─ find_recent_failures()    — kanban_tasks w/ last_failure_reason (105)
             ├─ diagnose_task()           — LLM 'failure_triage_diagnose' (306)
             │     └─ self_debug.snapshot()  — evidence (self_debug.py:159)
             │     └─ self_debug.diagnose()/_heuristic_diagnosis() fallback (216/270)
             ├─ should_auto_apply()       — gates (264); APPLY_CONFIDENCE=0.85 (64)
             ├─ generate_patch()          — LLM 'failure_triage_patch' (389)
             └─ apply_patch_in_worktree() — isolated .tmp/autofix/ + verify (616)
```

Dashboard surface:
- `/api/autonomy/status` — `tools/dashboard/app.py:10649`
- panel partial — `tools/dashboard/templates/_autonomy_status.html`
- the **"diagnosing"** tag is a *derived label* (`_autonomy_status.html:155`):
  `applied>0 → "fixing"`, else `failures>0 → "diagnosing"`, else `"watching"`.
  It is **not** a live-process indicator.

Current runtime posture (this environment):
- `ICDEV_AUTOFIX_ENABLED=true` → auto-apply path active.
- `ICDEV_AUTOFIX_AUTOMERGE` unset → patches land on `autofix/*` branches only; `main` protected.
- Tracer: `args/observability_tracing_config.yaml` backend `auto` (→ sqlite, air-gapped), `sampling_rate: 1.0`.

### What's good
- Conservative, well-gated apply path: task-type whitelist (`build/chore/fix/research/test`,
  `failure_triage.py:74`), deny-lists for signatures + files (`:79`–`:91`), allowlisted
  verification commands (`:471`), rollback on any non-zero verify, 5-applies/hour cap
  (`:66`), ff-only merge that refuses a diverged main (`:758`).
- Real structured RCA persisted as kanban "Oracle RCA" cards + memory `insight` lessons.

### The core defect — **evidence starvation**
`self_debug.snapshot()` feeds the LLM ~4 KB of git-plumbing JSON, a 20-line log tail, and
`git grep` of quoted error fragments. It **never** consumes the rich evidence that the
platform already captures:

| Already captured today | Where | Consumed by diagnoser? |
|---|---|---|
| Full pytest/playwright output | `.logs/build.ndjson` via `build_logger.capture_pytest` (called at `kanban.py:1281`) | ❌ |
| Per-gate verification results (codelens/coherence/e2e/ruff/bandit/git_commits) | `kanban_verifications` table (`kanban.py:2618`) | ❌ |
| Verification detail JSON | `.tmp/kanban/{task_id}.verification.json` (`kanban.py:4793`) | ❌ (only the ~2 KB capped `last_failure_reason`, `kanban.py:3385`) |
| Cross-component ERROR/CRITICAL logs | `centralized_logs` (migration 181) via `log_query.query_logs` | ❌ |
| Execution spans / timing | `otel_spans` via `@traced` (`tools/observability/instrumentation.py`) | ❌ |
| Static code metrics / smells | `tools/analysis/code_lens.py`, Code Intelligence engine | ❌ |
| The actual change under test | `git diff main..kanban/<task>` | ❌ |

**Consequence:** the heuristic fallback (`_heuristic_diagnosis`, `:270`) recognizes ~5
infrastructure failure modes (missing worktree, orphan worktree, "main passes/cwd fails",
dispatch timeout, phantom) and returns *"Unknown recurring failure; manual review
required"* (confidence 0.30) for everything else. Genuine code-logic bugs fall through.
Confidence is the LLM's uncalibrated self-report, yet it is load-bearing at the 0.85 gate.

---

## 2. Goals / non-goals

**Goals**
1. Make RCA *deep* — diagnose code-logic bugs, not just plumbing — by feeding the
   evidence the platform already has and adding a static + iterative debugging layer.
2. Integrate recovery with the existing logging, observability, and visibility surfaces
   (`centralized_logs`, `/logs`, `otel_spans`, `/traces`, the Home panel) so the loop is
   observable end-to-end.
3. Close the feedback loop: measure whether applied patches actually hold, and use that
   to **calibrate confidence** — the precondition for fuller autonomy.
4. Reach **calibration-gated auto-merge** for low-risk task types once measured precision
   clears a threshold, without weakening the existing safety gates.

**Non-goals**
- Replacing the existing gate architecture (we extend it, not loosen it).
- Auto-merging high-blast-radius types (`deploy`, migrations, security, hooks, config) — these
  stay human-review forever.
- Turning on content-plaintext tracing of secrets (respect `content_tracing.enabled=false`).

---

## 3. Workstreams

### WS1 — Deep evidence collector (foundation)

New module `tools/workflow/evidence_collector.py` exposing
`collect_evidence(task) -> EvidenceBundle`. It joins existing sinks; `self_debug.snapshot()`
is refactored to delegate to it (back-compat: keep the old keys, add new ones).

Bundle contents:

| Field | Source | Notes |
|---|---|---|
| `verification` | `kanban_verifications` latest row (reuse `_fetch_verification_details`) | per-gate booleans + reason; tells us *which* gate failed |
| `verification_detail` | `.tmp/kanban/{task_id}.verification.json` | full, untruncated |
| `build_events` | parse `.logs/build.ndjson` for this task's window | pytest/playwright stdout, pass/fail counts |
| `traceback` | parse Python traceback out of build/verify output | → exception type + `file:line:function` frames (deterministic) |
| `correlated_logs` | `log_query.query_logs(component, level≥ERROR, since=window)` | runtime errors the code itself logged |
| `spans` | `otel_spans` for the run's trace_id (if present) | failing tool/function + timing (timeout detection) |
| `diff` | `git diff main..kanban/<task>` (bounded) | the code actually written |
| `suspect_functions` | full source of functions named in traceback frames | replaces grep snippets |
| existing plumbing keys | current `snapshot()` | unchanged |

Token budget: bundle is assembled then *prioritized + truncated* (traceback + failing gate +
diff first; plumbing last) to a configurable cap so the diagnose prompt stays bounded.

**Feasibility:** confirmed — `build.ndjson` and `kanban_verifications` are already populated
on the kanban path; no runner change strictly required for Phase 1. (Optional hardening:
tee full verify stdout to `.logs/` keyed by task_id for guaranteed correlation.)

### WS2 — Deep code-logic debugger

Escalating tiers, cheapest first; each tier is independently flag-gated.

1. **Traceback-driven localization (deterministic).** New `tools/workflow/traceback_analyzer.py`:
   parse exception class + frames → exact `file:line`, in-scope names, the offending source
   line. Feeds both the LLM prompt *and* upgrades `_heuristic_diagnosis` so the no-LLM floor
   returns a real location + exception class instead of conf-0.30 "manual review."
2. **Static analysis on the diff (deterministic, no LLM).** Run `code_lens.py` / Code
   Intelligence on the *changed functions only*: unhandled branch, None-deref risk,
   off-by-one, complexity spike, missing return. Findings become structured diagnosis inputs.
3. **Iterative hypothesis→repro loop (the actual debugger).** Replace one-shot
   `diagnose → patch` with a bounded ReAct loop inside the isolated worktree:
   `hypothesize → instrument (targeted asserts/log lines) OR re-run failing test → observe →
   refine`, capped at `max_iterations` (default 3) and a per-failure token budget. Each
   iteration's observation is appended to evidence. Terminates on: confident fix, budget
   exhaustion, or repeated no-progress.
4. **Post-mortem locals capture (opt-in, heavy).** `faulthandler` / `trace` (or `debugpy`
   post-mortem) on the reproduction to capture locals at the failing frame. Executes code →
   **strictly** behind a new `ICDEV_AUTOFIX_DEEP_DEBUG=true` flag, worktree-only.

### WS3 — Observability & visibility of recovery itself

1. **Trace the triage.** Decorate `triage_once / diagnose_task / generate_patch /
   apply_patch_in_worktree` with `@traced()`. Each recovery becomes a span tree on `/traces`
   with timing + GenAI LLM attributes (token usage). Honor existing sampling/content policy.
2. **Structured events → `centralized_logs`.** Emit NDJSON events
   (`diagnosis_made`, `gate_decision`, `patch_generated`, `verify_result`, `apply_outcome`)
   via `get_logger` so they flow to `.logs/` and (when `log_ingest`/direct-write lands)
   `centralized_logs` → visible on `/logs`, queryable via IQE. Today outcomes live only in
   `.tmp/.../triaged/*.marker`.
3. **Richer Home panel.** Extend `/api/autonomy/status` + `_autonomy_status.html` to show
   per-diagnosis: `root_cause`, `suspect_files`, `confidence`, **diff preview**, verify-output
   tail, debugger-iteration count, and drill-through links to the RCA card + `autofix/*`
   branch. Add a recovery span link to `/traces`.
4. **Calibration table (feedback loop).** New table `triage_outcomes` (see §4): record each
   apply, then watch whether the source task subsequently reaches `done` (patch held) vs
   re-failed / was reverted. Compute rolling precision per `(task_type, signature_class)`.

### WS4 — Harden the decision

1. **Self-consistency diagnosis.** Sample N diagnoses (`temperature` spread); use cross-sample
   agreement as the *real* confidence, replacing the single self-reported number before any
   patch attempt. Disagreement → suggested-card path.
2. **Verification beyond the LLM's own command.** Today the LLM picks its own
   `verification_command` (self-grading risk). Also re-run the *originally failing* test,
   `ruff`, and the coherence gate; require all to pass before commit.

---

## 4. Data model changes

All additive. Append-only tables MUST be registered in `APPEND_ONLY_TABLES`
(`.claude/hooks/pre_tool_use.py`) and added to `MINIMAL_ICDEV_SCHEMA` in
`tests/conftest.py`; ship dual `SCHEMA_PG` / `SCHEMA_SQLITE`; default backend PostgreSQL.

- **`triage_runs`** (append-only) — one row per `triage_once` cycle: started/finished,
  scanned, applied, suggested, autofix_enabled, trace_id.
- **`triage_outcomes`** (append-only) — one row per diagnosis/apply: task_id, signature,
  signature_class, task_type, recommendation, confidence (raw + self-consistency),
  gate_decision, applied?, verify_rc, autofix_branch, autofix_commit, merged?, and a
  later-resolved `held` flag (set when source task reaches `done`/re-fails). Backs precision
  metrics. Append-only ⇒ `held` is recorded as a *new* resolution row, not an UPDATE.

(If a strictly mutable status is needed, keep it on `kanban_tasks`; never UPDATE audit rows.)

---

## 5. Autonomy roadmap — calibration-gated auto-merge

The user's chosen direction. We do **not** flip `ICDEV_AUTOFIX_AUTOMERGE` globally. Instead,
auto-merge becomes earned, per `(task_type, signature_class)`, and stays ff-only.

1. **Phase A — measure (no behavior change).** WS3 calibration table accumulates outcomes.
   Surface precision per cohort on the panel. Auto-merge still requires the env flag.
2. **Phase B — gated auto-merge.** New gate `should_auto_merge(task, diag, cohort_stats)`:
   allow ff-merge **only if** all current apply gates pass **AND** task_type ∈ low-risk set
   **AND** cohort has ≥ `MIN_SAMPLES` (e.g. 20) applied outcomes **AND** rolling precision ≥
   `AUTOMERGE_PRECISION` (e.g. 0.95) **AND** self-consistency ≥ threshold. Driven by
   `args/genesis_config.yaml`, not just an env bool.
3. **Phase C — adaptive thresholds + auto-rollback.** If a cohort's precision drops below a
   floor, automatically revoke its auto-merge privilege and notify. Add post-merge watch:
   if an auto-merged commit correlates with a new failure within a window, open a revert card.

Hard ceilings retained regardless of precision: `deploy`, `tools/db/migrations/`,
`tools/security/`, `.claude/hooks/`, `args/security_gates.yaml`, `args/llm_config.yaml`,
and any deny-token signature stay human-review.

---

## 6. Phasing & acceptance criteria

**Phase 1 — Evidence + visibility (highest ROI, low risk; no gate change)**
- WS1 evidence collector; WS2.1 traceback localization (incl. fallback upgrade);
  WS3.1 tracing + WS3.2 structured events; WS3.3 panel enrichment (read-only fields).
- *Done when:* a real code-logic failure produces a diagnosis citing the traceback frame +
  diff hunk; recovery appears as a span tree on `/traces`; outcomes are queryable on `/logs`;
  panel shows root_cause + diff preview. New tests green; no change to apply behavior.

**Phase 2 — Deeper diagnosis + decision hardening**
- WS2.2 static-on-diff; WS2.3 iterative repro loop; WS4.1 self-consistency; WS4.2 expanded
  verification.
- *Done when:* the loop fixes a seeded logic bug a one-shot diagnose misses; confidence
  reflects self-consistency; patches must pass the original failing test + ruff + coherence.

**Phase 3 — Calibrated autonomy**
- `triage_outcomes` precision tracking (Autonomy Phase A); `should_auto_merge` cohort gate
  (Phase B); adaptive revocation + post-merge revert watch (Phase C); WS2.4 deep-debug
  (opt-in).
- *Done when:* a low-risk cohort with proven precision auto-merges ff-only; a precision drop
  auto-revokes the privilege; dashboard shows per-cohort precision + auto-merge status.

---

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Token cost of iterative loop + self-consistency | Per-failure token budget + `max_iterations`; tiers individually flag-gated; reuse existing 5/hr apply cap |
| Code execution in repro / post-mortem has side effects | Worktree-only; deep-debug behind `ICDEV_AUTOFIX_DEEP_DEBUG`; verification-command allowlist already enforced (`:471`) |
| Over-trusting calibration → bad auto-merge | ff-only; min-sample + precision floor; permanent ceilings on risky types; auto-revoke + revert watch |
| `centralized_logs` not yet fully fed (log_ingest reflex absent on this branch) | Phase 1 reads `.logs/*.ndjson` + `kanban_verifications` directly; centralized_logs is additive enrichment, not a hard dependency |
| Append-only discipline | New audit tables registered in `APPEND_ONLY_TABLES`; resolutions recorded as new rows |
| Evidence prompt bloat | Prioritized truncation (traceback/gate/diff first); hard char cap |
| Concurrency with live kanban sessions | Recovery stays in `.tmp/autofix/` worktrees (separate from `.tmp/worktrees/`); no edits to files an active session owns |

---

## 8. Open questions

1. **Trace correlation:** is a `trace_id` currently threaded from kanban dispatch → verify so
   `otel_spans` can be joined to a task? If not, Phase 1 adds a task_id↔trace_id link (cheap).
2. **Self-consistency cost ceiling:** acceptable N and token budget per failure?
3. **Auto-merge thresholds:** confirm `MIN_SAMPLES` / `AUTOMERGE_PRECISION` and the low-risk
   cohort set (proposed: `chore`, `test` first; `fix`/`build` after a probation period).
4. **Panel placement:** keep enriched view inline on Home, or add a dedicated
   `/recovery` page (mirrors `/traces`) with the Home panel as a summary?

---

## 9. Registration checklist (per CLAUDE.md, when building)

- `tools/manifest/<topic>.md` (logging-system + a new "autonomous-recovery" shard or extend
  knowledge-self-healing) — register `evidence_collector`, `traceback_analyzer`, new gates.
- `docs/reference/commands.md` — any new CLI entry points.
- `args/genesis_config.yaml` — debugger iteration caps, automerge cohort thresholds.
- `args/security_gates.yaml` — auto-merge gate definition.
- `.claude/hooks/pre_tool_use.py` — `triage_runs`, `triage_outcomes` append-only.
- `tests/conftest.py` — new table schemas in `MINIMAL_ICDEV_SCHEMA`.
- `tools/mcp/tool_registry.py` + `gap_handlers.py` — expose new tools if MCP-visible.
- `python tools/dx/companion.py --sync --write --json` (foreground; never background).
- `python tools/workflow/coherence_checker.py --all --fix --gate`.
- Sandbox-coverage decision in `docs/security/sandbox-coverage.md` for any module ingesting
  LLM-generated patch/repro content (the deep-debug executor is the relevant one).

<!-- CUI // SP-CTI -->
