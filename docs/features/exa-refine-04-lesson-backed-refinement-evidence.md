# CUI // SP-CTI

# Lesson-backed evidence on every proposed refinement (exa-refine-04)

## The gap

prime-agent's `/refine` applies only **evidence-backed** updates drawn from the
session trajectory. ICDEV had the better evidence source and did not connect it.

`tools/workflow/lesson_learned.py` is the one self-improvement loop here that
unambiguously works: a 24-pattern deterministic taxonomy
(`PHANTOM_COMPLETION`, `TOKEN_EXHAUSTION`, `VERIFICATION_FAIL`,
`MIGRATION_NUMBER_COLLISION`, …), recurrence scoring over a 7-day window,
systemic escalation at a 0.50 threshold, **14,765 rows**, and 13+ live call
sites across the kanban reflex, `stranded_audit`, `self_debug`, `pr_watcher`
and chat corrections.

Meanwhile `agent_improvement_artifacts.evidence_traces` — the column that is
supposed to say *why* a refinement was proposed — held one of three useless
things:

| Writer | What it wrote |
|--------|---------------|
| `tools/workflow/reflexion_agent.py` | `["trace-ca00728823e8", …]` — opaque ids |
| `tools/genesis/reflexes/evolution.py` (NOVA SELA) | the literal `'[]'` |
| `tools/nova/skill_generator.py` | a provenance dict, no lessons |

A human reviewing a proposal had nothing to review, and a proposal motivated by
nothing was indistinguishable from one motivated by a recurring systemic
failure. This is the EXA thesis in miniature — *declared but unconsumed*.

## What shipped

### 1. The join — `tools/workflow/refinement_evidence.py`

`collect_evidence()` takes the execution traces a proposal was derived from and
returns a versioned `refinement_evidence/v1` bundle:

* **lesson rows** — each trace's `task_id` resolved to its `lesson_learned`
  entry in `memory_entries`, carrying `pattern`, `category`, `outcome`,
  `last_failure_reason`, `recommendation`, `is_systemic` and the memory entry id;
* **recurrence** — per distinct pattern, scored by
  `lesson_learned.get_recurrence` (the existing scorer, reused, not
  reimplemented), plus the headline `recurrence_score` and `dominant_pattern`.

Matching is a loose `LIKE` on the JSON body narrowed to an exact `task_id`
comparison in Python — per the repo's PG-portability rule, structured filtering
of a JSON column belongs in Python, not in dialect-specific JSON SQL. The
narrowing is load-bearing: without it a lesson for `exa-refine-041` would be
counted as evidence for `exa-refine-04`.

### 2. The gate — rejection before a human sees it

`evaluate_evidence()` decides whether the bundle supports the refinement.
Both refinement writers now persist an unsupported proposal with
`status='rejected_no_evidence'` instead of `'pending'`.

`'pending'` is precisely what `gepa_optimizer._get_pending_artifacts`,
`reflexion_agent.get_latest_improvement` and
`skills_lifecycle.list_proposals` select on — so a non-`'pending'` status *is*
the rejection. The proposal is still persisted, so the rejection is auditable
rather than silent.

Config: `args/refinement_evidence.yaml` — `require_evidence` (default **on**),
`min_lessons`, `min_recurrence_score`, `window_days`. Turning the gate off is a
recorded decision: the bundle is still attached and `gate_passed` still reports
the truth, only the block is lifted.

`min_recurrence_score` defaults to `0.0` deliberately. Recurrence is
`similar / total_in_window`, so on a busy board a genuinely recurring pattern —
254 `sibling_file_conflict` lessons out of 7,564 in the window — still scores
`0.034`. A non-zero floor set by intuition would reject everything.

### 3. The review surfaces display it

| Surface | What it shows |
|---------|---------------|
| GEPA kanban review card (`_seed_review_card`) | full markdown evidence block: pattern/recurrence/systemic table + the lesson rows with their failure reasons |
| NOVA proposal queue (`skills_lifecycle.list_proposals`, MCP `nova_list_skill_queue`) | `evidence` bundle + one-line `evidence_summary` |
| ACE coworker card (`/api/ace/…/nova-state`, `coworker/instance.html`) | `evidence:` sub-line under each applied improvement |
| CLI | `python tools/workflow/refinement_evidence.py --task-type build --json` |

### 4. Backward compatibility

`parse_evidence()` reads all three legacy shapes — the bare trace-id list, the
NOVA provenance dict, and unparseable junk — and reports **zero lesson
evidence** with an explanatory `note` rather than raising or pretending. The
124 artifacts already in the table therefore read honestly instead of being
mass-rejected: the gate is a write-time decision, not a retroactive purge.

This also fixed a latent bug in GEPA: `n_traces` was `len(json.loads(...))`,
which on a dict-shaped `evidence_traces` would have counted **dict keys**.

## Verified

* `tests/test_refinement_evidence.py` — 20 tests: the join, the substring-collision
  guard, the gate (pass / reject / recurrence floor / gate-disabled), the three
  legacy shapes, both review surfaces, and end-to-end assertions on the
  **persisted row's** status and evidence.
* `tests/test_nova_sela_evolution.py` — added
  `test_run_evolution_rejects_a_mutation_with_no_lesson_evidence`; existing
  fixtures now seed lessons so they exercise the supported path.
* Live check against the production PostgreSQL board: 3 lesson rows joined for
  `exa-bench-04` / `exa-refine-03` / `exa-audit-04`, patterns
  `sibling_file_conflict` (254 similar in window, recurrence 0.034),
  `verification_fail`, `permission_blocked`.

## Known limitation

`agent_execution_traces` is only written when `ICDEV_HARNESS_COLEARN=true`, so
on a board where co-learning is off the refinement writers have no trajectory to
collect evidence from and will (correctly) reject their own proposals. The
evidence join itself is independent of that flag — `--task-ids` / the
`task_ids=` argument works against any kanban task id.
