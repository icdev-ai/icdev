#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed the DWO (Durable Workflow Orchestration) card onto the kanban board.

Backs ``docs/spikes/dwo-00-superplane-workflow-adaptation.md``.

DWO adapts the four capabilities SuperPlane (github.com/superplanehq/superplane,
Apache-2.0) has that the ICDEV Studio workflow engine lacks. It is deliberately
NOT a port: Studio already owns the DAG model, the composer, the editor, the SSE
runner and the append-only run audit trail. The rule for every task on this card
is *extend the existing surface, do not add a parallel one*.

This card is ungated — tasks dispatch normally. Dependencies are expressed with
``depends_on_task_id`` so each epic builds in order.

Usage::

    python tools/kanban/seed_dwo_kanban.py            # seed
    python tools/kanban/seed_dwo_kanban.py --json     # machine-readable report
    python tools/kanban/seed_dwo_kanban.py --dry-run  # print, insert nothing
"""

from __future__ import annotations

import argparse
import json
import sys

SPIKE = "docs/spikes/dwo-00-superplane-workflow-adaptation.md"


def _t(
    task_id: str,
    title: str,
    description: str,
    *,
    depends_on: str | None = None,
    priority: str = "medium",
    task_type: str = "build",
) -> dict:
    spec: dict = {
        "id": task_id,
        "title": title,
        "description": description.strip(),
        "task_type": task_type,
        "priority": priority,
        "status": "backlog",
    }
    if depends_on:
        spec["depends_on_task_id"] = depends_on
    return spec


_CONTEXT = f"""
Card: DWO — Durable Workflow Orchestration. Spike: {SPIKE}
Ground rule: extend the existing Studio/WFC surface. Do NOT create a second
workflow engine, a second approval surface, a second rules DSL, or a second
event ingress. Read the spike before starting.
"""


TASKS: list[dict] = [

    # ══════════════════════════════════════════════════════════════════
    # DUR — Durable Execution & Gate Persistence
    # ══════════════════════════════════════════════════════════════════
    _t(
        "dwo-dur-01",
        "Persist HITL gate state to the database",
        f"""{_CONTEXT}
PROBLEM
`tools/studio/workflow_runner.py` holds approval-gate state in three module-level
dicts — `_approval_events` (step_run_id -> threading.Event), `_approval_results`
and `_approval_reasons`. `get_pending_approvals()` reads that dict, not the DB.
Every gate therefore dies with the process.

BUILD
1. New table `studio_workflow_gates`, added to `STUDIO_TABLES` in
   `tools/studio/init_db.py` AND as a numbered migration under
   `tools/db/migrations/` (use the next free number; 223 is taken).
   Columns: gate_id PK, run_id, step_run_id, step_id, step_name, role,
   status (pending|approved|rejected|expired), requested_at, expires_at,
   decided_at, decided_by, reason, payload_json.
   Authored PG-first per CLAUDE.md; SQLite is init-fallback only.
2. `approve_step()` / `reject_step()` write the decision to the table FIRST,
   then set the in-memory Event if one exists in this process. The Event stays
   as a fast local wake-up, not as the source of truth.
3. `get_pending_approvals()` reads the table, not `_approval_events`.
4. The `_worker` wait loop polls the table on an interval in addition to
   waiting on the Event, so a decision recorded by a *different* process
   (dashboard worker, gateway, CLI) still releases the gate.

GEL
- `studio_workflow_gates` is an audit surface: add it to `APPEND_ONLY_TABLES`
  in `tools/studio/init_db.py` if append-only semantics fit, and to
  `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py` either way if it is
  audit-bearing. If the decision must mutate the row, keep the row mutable and
  add a separate append-only `studio_workflow_gate_events` log instead — decide
  and document which, do not do both silently.
- Add the schema to `MINIMAL_ICDEV_SCHEMA` in `tests/conftest.py`.
- Do not touch `wf_external_steps` yet — dwo-dur-04 unifies the two surfaces.

ACCEPTANCE
- A gate approved via `approve_step()` in one Python process releases a run
  blocked in another process.
- `get_pending_approvals()` returns gates after a fresh interpreter start.
- Existing Studio approval routes and the Telegram `/approve <step_run_id>`
  flow still work unchanged.
""",
        priority="high",
    ),
    _t(
        "dwo-dur-02",
        "Replace force-fail-on-boot with a resume reconciler",
        f"""{_CONTEXT}
PROBLEM
`workflow_runner._cleanup_orphaned_gates()` is called at *import time* — i.e.
on every dashboard start — and unconditionally does:
    UPDATE studio_workflow_run_steps SET status='timeout' WHERE status='awaiting_approval'
    UPDATE studio_workflow_runs      SET status='failed'  WHERE status='awaiting_approval'
So every in-flight approval is destroyed by a restart or a deploy. Templates
routinely set `timeout: 3600` on human steps, so this is a live defect.

BUILD
1. Rename to `reconcile_runs_on_boot()` and stop calling it at import time —
   call it explicitly from the Studio blueprint/app startup path so import
   stays side-effect free (this also fixes test-time surprises).
2. New behaviour, driven by `studio_workflow_gates` (dwo-dur-01):
   - gate past `expires_at`  -> expire it, fail the step and the run, audit it;
   - gate still within its deadline -> leave it `awaiting_approval` and register
     it as resumable; the run stays in `awaiting_approval`, not `failed`;
   - run with no gate but a `running` step whose process is gone -> mark the
     step `failed` with a clear stderr and leave the run resumable.
3. Emit an audit entry for every reconciliation decision.

GEL
- Reuse the existing storage helpers (`tools.db.storage.get_connection`).
- Do not add a background thread — reconciliation is a boot-time pass plus the
  poll loop added in dwo-dur-01.

ACCEPTANCE
- Start a run, park it on a human step, restart the dashboard: the run is still
  `awaiting_approval` and approving it completes the run.
- A gate whose `expires_at` has passed is expired (not silently resurrected).
- Importing `tools.studio.workflow_runner` performs no database writes.
""",
        depends_on="dwo-dur-01",
        priority="high",
    ),
    _t(
        "dwo-dur-03",
        "Step-level checkpoint and resume for failed runs",
        f"""{_CONTEXT}
PROBLEM
There is no way to resume a partially-completed run. A run that fails at step 7
of 9 must be restarted from step 1, re-running expensive/irreversible steps
(terraform apply, ansible). SuperPlane's durability claim is exactly this:
"failed steps can resume without custom retry logic".

BUILD
1. `resume_run(run_id) -> str` in `tools/studio/workflow_runner.py`:
   re-resolve the DAG for the run's workflow, treat steps already recorded
   `success` as satisfied, and execute only `failed` / `timeout` / `pending`
   steps in dependency order.
2. Resumption creates a NEW run row linked to the original via a
   `resumed_from_run_id` column (append-only audit is preserved — never rewrite
   the original run's rows).
3. Per-step `retries` / `retry_backoff_seconds` honoured from the template YAML
   if present; default 0 so existing templates are unchanged.
4. Expose it: a `Resume` control on the Studio run detail page and a
   `POST /studio/api/runs/<run_id>/resume` route on the existing blueprint.

GEL
- Reuse `_resolve_dag()` and `_exec_step()`; do not fork the execution path.
- SSE: resumed runs get their own run queue exactly like a fresh run
  (per-run SSE isolation is Architecture Decision D343+ — preserve it).
- `tools/studio/executors/*` must stay unmodified by this task.

ACCEPTANCE
- A run failing at step N, resumed, executes only step N onward.
- The original run's rows are untouched; the new run references it.
- Resuming a fully-successful run is a no-op that reports as such.
""",
        depends_on="dwo-dur-02",
        priority="high",
    ),
    _t(
        "dwo-dur-04",
        "Unify Studio gates with workflow_hitl external steps",
        f"""{_CONTEXT}
PROBLEM
There are two human-approval surfaces that do not know about each other:
- `tools/studio/workflow_runner.py` gates (`node_type: human` steps, Telegram
  /approve, in-process Events -> now `studio_workflow_gates`);
- `tools/workflow_hitl/` external steps (`wf_external_steps`, webhook-token
  completion via `external_steps.verify_webhook_token`, notifier, teams).
An approver has two inboxes and reviewers cannot see one from the other.

BUILD
Pick ONE direction and document it in the feature doc:
(a) Studio gates become a *producer* of `wf_external_steps` rows so
    workflow_hitl remains the single reviewer inbox; or
(b) `wf_external_steps` records a foreign key to `studio_workflow_gates` and
    the HITL blueprint renders both.
Recommended: (a) — workflow_hitl already owns notification routing, teams,
templates and the webhook-token completion path; Studio should not reimplement
any of it.

Whichever direction: completing the step in one surface must release the gate in
the other, exactly once, with one audit trail.

GEL
- Reuse `tools/workflow_hitl/external_steps.py` and `notifier.py` as-is.
- Keep the Telegram `/approve <step_run_id>` command working — it routes through
  `tools/gateway/`, so verify the command router still resolves the id.
- Update `tools/manifest/workflow-hitl.md` and
  `tools/manifest/icdev-studio-low-code-no-code-platform.md`.

ACCEPTANCE
- One pending approval appears in exactly one reviewer inbox.
- Approving from workflow_hitl releases a Studio run gate.
- Approving from Studio marks the corresponding external step complete.
- No double-approval: a second decision on a decided gate is rejected and audited.
""",
        depends_on="dwo-dur-03",
        priority="high",
    ),

    # ══════════════════════════════════════════════════════════════════
    # EVT — Event Sources & Workflow Triggers
    # ══════════════════════════════════════════════════════════════════
    _t(
        "dwo-evt-01",
        "Event source and workflow trigger registry",
        f"""{_CONTEXT}
PROBLEM
ICDEV receives external events (`tools/gateway/gateway_agent.py`, 9 channels)
and runs DAGs (`tools/orchestration/workflow_composer.py`), but nothing binds
the two. There is no registry of event sources and no way to say "when event X
matching filter Y arrives, start workflow Z with the payload as input".

BUILD
1. Tables + migration (next free number under `tools/db/migrations/`), also
   added to `STUDIO_TABLES` in `tools/studio/init_db.py`:
   - `studio_event_sources`: source_id PK, name, kind
     (gateway_channel|canvas_bus|schedule|manual), config_json, enabled,
     created_by, created_at.
   - `studio_workflow_triggers`: trigger_id PK, source_id FK, workflow_id FK,
     event_type, filter_json, input_mapping_json, enabled, created_at.
   - `studio_trigger_events` (APPEND-ONLY): every event evaluated, matched or
     not, with the resulting run_id — this is the audit trail for "why did this
     run start". Register it in `.claude/hooks/pre_tool_use.py::APPEND_ONLY_TABLES`.
2. `tools/studio/event_sources.py` — CRUD + `match_event(event) -> list[trigger]`.
   The filter language is `automation_builder.CONDITION_OPERATORS` (equals,
   not_equals, contains, greater_than, less_than, in_list, is_empty,
   is_not_empty) evaluated by `automation_builder._evaluate_condition`.
   DO NOT invent a second condition DSL — refactor `_evaluate_condition` to a
   shared helper if it needs to be importable.
3. Add schemas to `MINIMAL_ICDEV_SCHEMA` in `tests/conftest.py`.

GEL
- `canvas_bus` sources subscribe through the existing canvas event bus
  (migration `039_canvas_events`, the `tools/*/bus_subscriber.py` pattern).
- Nothing in this task opens a network listener — see dwo-evt-02.

ACCEPTANCE
- A trigger with a filter matches/rejects sample payloads per the operators.
- Every evaluated event lands in `studio_trigger_events` whether or not it matched.
""",
        priority="high",
    ),
    _t(
        "dwo-evt-02",
        "Route gateway events into workflow triggers",
        f"""{_CONTEXT}
PROBLEM
The gateway already does the hard part: `tools/gateway/gateway_agent.py`
registers per-channel webhook routes, `adapters/*.parse_webhook()` normalises
payloads into a `CommandEnvelope`, `event_envelope.py` HMAC-signs, and
`security_chain.py` runs an 8-gate security chain with IL-aware response
filtering. None of it reaches the workflow engine.

BUILD
1. After an envelope clears the security chain, hand it to
   `tools.studio.event_sources.match_event()` and start a run for each match
   via `workflow_runner.start_run()`, passing the payload as run input
   (depends on the input plumbing in dwo-evt-04).
2. Dispatch is asynchronous relative to the webhook response: the HTTP handler
   must not block on workflow execution.
3. Idempotency: an event carrying a delivery id must not start two runs.
   `task_factory` already models this with `idempotency_key` — mirror that
   pattern for runs.
4. Respect the channel's IL/classification: a run started from an event inherits
   the envelope's classification, and a trigger targeting a workflow above the
   source's IL is refused and audited.

GEL — NON-NEGOTIABLE
- Do NOT add a new unauthenticated webhook route. Every external event enters
  through the existing gateway security chain. If a new route is unavoidable,
  it registers through `gateway_agent._register_webhook_route` so the 8 gates
  apply automatically.
- `command_allowlist` semantics in the gateway must not be weakened.

ACCEPTANCE
- A signed test event on a gateway channel starts the bound workflow run.
- The same event replayed with the same delivery id starts exactly one run.
- An unsigned/failed-chain event starts nothing and is audited.
- An event whose classification exceeds the workflow's IL is refused.
""",
        depends_on="dwo-evt-01",
        priority="high",
    ),
    _t(
        "dwo-evt-03",
        "Extend automation_builder with external_event trigger and run_workflow action",
        f"""{_CONTEXT}
PROBLEM
`tools/studio/automation_builder.py` already models TRIGGER -> CONDITION ->
ACTION with 10 `TRIGGER_TYPES` (finding_detected, sla_breach, scan_complete,
deployment, form_submitted, case_state_change, poam_overdue, sam_opportunity,
schedule, manual). All are internal. There is no external-event trigger, and
the action list must be able to start a workflow.

BUILD
1. Add `external_event` to `TRIGGER_TYPES` (category "system"), whose config
   names a `studio_event_sources` row.
2. Ensure `ACTION_TYPES` contains `run_workflow` (workflow_id + input mapping);
   add it if absent, wired to `workflow_runner.start_run()`.
3. `simulate_automation()` must support the new trigger — feed a sample external
   payload and show which conditions passed. This is the dry-run surface users
   will rely on; do not leave it stubbed.
4. Surface both in the automation builder UI alongside the existing types
   (icon/colour/category fields are already part of the schema).

GEL
- One trigger vocabulary. `studio_workflow_triggers` (dwo-evt-01) and
  `studio_automations` must share `TRIGGER_TYPES` and `CONDITION_OPERATORS`
  rather than drifting into two catalogues.
- Existing automations keep working untouched.

ACCEPTANCE
- `get_trigger_types()` includes `external_event`; `get_action_types()`
  includes `run_workflow`.
- `simulate_automation()` on an external_event automation returns a
  per-condition pass/fail trace.
- An automation with a run_workflow action starts a real run when triggered.
""",
        depends_on="dwo-evt-02",
        priority="medium",
    ),
    _t(
        "dwo-evt-04",
        "Trigger payload as run input, plus trigger UI in the workflow editor",
        f"""{_CONTEXT}
PROBLEM
`workflow_runner.start_run(workflow_id, project_id)` takes no input payload, and
`_build_command()` builds a step command with no way to inject event data. A
triggered run cannot see what triggered it.

BUILD
1. `start_run(workflow_id, project_id, inputs: dict | None = None)` — keyword
   arg with a default, so every existing caller keeps working.
2. Persist inputs on the run row (`inputs_json`) and expose them to steps.
   Prefer the run-memory channel from dwo-mem-01 if that task has merged;
   otherwise a documented env var / JSON file contract that dwo-mem-02 then
   folds in. Do not invent a third mechanism.
3. `input_mapping_json` on the trigger maps event payload fields to run inputs.
4. Editor: a Triggers panel in `tools/studio/workflow_editor.py` to bind an
   event source + filter + mapping to the open workflow, and a run-detail badge
   showing which trigger/event started a run (link to `studio_trigger_events`).

GEL
- Templates in `args/workflow_templates/` are unchanged by this task; inputs are
  additive and optional.
- Follow the Jinja2 rule in CLAUDE.md (`value|round(0)|int`, never `'%%.0f'|format`).

ACCEPTANCE
- A run started by a trigger exposes the event payload to its steps.
- A manually started run with no inputs behaves exactly as today.
- The editor can create, test (via simulate) and disable a trigger.
""",
        depends_on="dwo-evt-03",
        priority="medium",
    ),

    # ══════════════════════════════════════════════════════════════════
    # MEM — Run-Scoped Memory
    # ══════════════════════════════════════════════════════════════════
    _t(
        "dwo-mem-01",
        "Run-scoped memory store for workflow steps",
        f"""{_CONTEXT}
PROBLEM
Steps communicate only through stdout JSON scraped by the next executor. There
is no shared state for a run, which is why `executors/_base.py` has to guess
(see dwo-mem-02). SuperPlane's equivalent is app-scoped JSON memory persisting
across runs.

BUILD
1. Table + migration `studio_run_memory`: run_id, key, value_json, updated_at,
   PK (run_id, key). Also add to `STUDIO_TABLES` and to `MINIMAL_ICDEV_SCHEMA`
   in `tests/conftest.py`.
2. `tools/studio/run_memory.py`: `get(run_id, key, default=None)`,
   `set(run_id, key, value)`, `all(run_id) -> dict`, `delete(run_id, key)`.
   PG-first SQL; read JSON columns raw and parse with `json.loads()` in Python
   rather than using SQLite JSON functions (CLAUDE.md PG portability rule —
   the `pg_portability_linter` gates this).
3. Contract for steps: the runner passes `ICDEV_RUN_ID` in the step environment;
   a step reads/writes memory through `run_memory` (Python steps) or via a
   documented CLI (`python tools/studio/run_memory.py --run-id ... --get/--set`)
   for non-Python executors.
4. Scope + retention: memory is per-run. Document whether a resumed run
   (dwo-dur-03) inherits its parent's memory — recommended yes, copied at
   resume time so the original run's record stays immutable.

GEL
- This is the single mechanism for inter-step data. dwo-evt-04 run inputs land
  here under a reserved `_inputs` key rather than in a parallel store.
- Do not use `tools/memory/` (that is the long-term session memory system,
  different concern entirely).

ACCEPTANCE
- Step A writes a key, step B reads it, across separate subprocesses.
- Memory survives a resume and does not leak between runs.
- Register the module in `tools/manifest/icdev-studio-low-code-no-code-platform.md`.
""",
        priority="medium",
    ),
    _t(
        "dwo-mem-02",
        "Refactor executors/_base.py to read canvas and artifacts from run memory",
        f"""{_CONTEXT}
PROBLEM
`tools/studio/executors/_base.py::resolve_canvas()` determines which canvas a
run belongs to with four fallback strategies in priority order: an explicit
--canvas arg, sniffing `data/studio_artifacts/<canvas>/` out of artifact paths,
a workflow-name prefix match, then a hardcoded default of 'ddc'. `get_iac_artifacts()`
similarly re-queries the 'Generate IaC' step and re-parses its stdout. All of
this exists because there is no shared run state — the guessing IS the bug.

BUILD
1. The runner (or the IaC-generating step) writes `canvas` and `artifacts` into
   run memory (dwo-mem-01) as the authoritative record.
2. `resolve_canvas()` and `get_iac_artifacts()` read run memory FIRST, then fall
   back to today's strategies unchanged.
3. Remove the silent `'ddc'` default: if nothing resolves, raise/exit with a
   clear error rather than writing another canvas's artifacts. Verify no
   existing template depends on the implicit default before removing it — if one
   does, fix the template in the same change.

GEL
- All 9 executors (terraform plan/apply/destroy, ansible, aws_config, gns3_sim,
  validation_runner, migration_reporter) inherit the fix through `_base.py`;
  none of them should need editing.
- The 'Generate IaC' stdout JSON contract documented at the top of `_base.py`
  stays valid — this task adds a faster/authoritative path, it does not break
  the contract. Update that docstring.

ACCEPTANCE
- A run whose workflow name does not encode the canvas still resolves correctly.
- An unresolvable canvas fails loudly instead of defaulting to 'ddc'.
- Existing canvas workflow templates (`*_workflow.yaml`, `*_teardown.yaml`)
  still execute end to end.
""",
        depends_on="dwo-mem-01",
        priority="medium",
    ),

    # ══════════════════════════════════════════════════════════════════
    # MCP — Universal MCP Tool Executor
    # ══════════════════════════════════════════════════════════════════
    _t(
        "dwo-mcp-01",
        "Generic MCP tool executor for workflow steps",
        f"""{_CONTEXT}
PROBLEM
Studio has 9 executors, all infrastructure-facing. SuperPlane's leverage is ~50
integration adapters. Hand-writing 50 executors is the wrong answer:
`tools/mcp/tool_registry.py::TOOL_REGISTRY` already declares 444 tools, each with
a `module` + `handler` + `input_schema`, and `list_tools()` enumerates them.

BUILD
1. `tools/studio/executors/mcp_executor.py`:
   `--tool <name> --params '<json>' [--run-id <id>]`. Looks the tool up in
   TOOL_REGISTRY, `importlib.import_module(entry["module"])`,
   `getattr(mod, entry["handler"])(params)`, prints the result as the stdout
   JSON contract the runner already expects.
2. Validate `params` against the entry's `input_schema` before dispatch and fail
   with a readable error listing the missing/invalid fields.
3. Results write to run memory (dwo-mem-01) under the step id so downstream
   steps can consume them.
4. Unknown tool name -> non-zero exit with the closest matches suggested.

GEL
- Follow the existing executor conventions in `tools/studio/executors/_base.py`
  (argparse, stdout JSON, exit codes) so the runner needs no special-casing.
- Do not import the MCP *servers* or start a stdio transport — this dispatches
  handlers in-process from the registry.
- Security gating is dwo-mcp-02; land these two together before enabling the
  node type in templates.

ACCEPTANCE
- `python tools/studio/executors/mcp_executor.py --tool health_check --params '{{}}'`
  returns the same result as the MCP tool.
- A bad tool name and a schema-invalid params object both fail with clear errors.
""",
        priority="medium",
    ),
    _t(
        "dwo-mcp-02",
        "Allowlist and IL/RBAC gating for MCP tool steps",
        f"""{_CONTEXT}
PROBLEM
A generic dispatcher over 444 tools is an authorization surface. Without gating,
a workflow step could call destructive or classification-sensitive tools
(terraform destroy, delete_run, redaction bypass, kanban delete) with no policy.

BUILD
1. Gate definition in `args/security_gates.yaml` (per the CLAUDE.md new-tool
   registration checklist) declaring which MCP tools are callable from a
   workflow step: default-deny with an explicit allowlist, plus a
   `requires_approval` set that forces a `node_type: human` gate before dispatch.
2. Enforce in `mcp_executor.py` before any import/dispatch.
3. Honour the caller's IL and role: a step running in an IL4 workflow cannot
   invoke a tool whose registry metadata requires higher. Reuse the existing
   RBAC/IL metadata rather than adding a new one
   (`args/component_registry.yaml` min_il/default_roles,
   `tools/security/canvas_access.py`).
4. Every dispatch — allowed or refused — is audited with tool name, params
   digest, run id, step id, actor.

GEL
- Reuse the classification helpers (`classification_manager.py`); do not
  hardcode CUI banners.
- If a refused dispatch needs an override, it goes through the existing HITL
  force/override + audit pattern, not a new flag.

ACCEPTANCE
- A non-allowlisted tool is refused and audited, and the run fails cleanly.
- A `requires_approval` tool blocks on a human gate before dispatching.
- The audit entry is append-only and includes actor + params digest.
""",
        depends_on="dwo-mcp-01",
        priority="medium",
    ),
    _t(
        "dwo-mcp-03",
        "node_type: mcp in templates and the editor palette",
        f"""{_CONTEXT}
PROBLEM
Templates currently support `node_type: human` and `node_type: tool` (a script
path). There is no way to declare an MCP tool step, and the editor has no
palette entry for one.

BUILD
1. `node_type: mcp` in the template schema, with `mcp_tool` and `mcp_params`
   keys. `workflow_runner._build_command()` maps it to
   `tools/studio/executors/mcp_executor.py --tool ... --params ...`.
2. `tools/orchestration/workflow_composer.py` accepts the same node type so
   headless template runs behave identically to Studio runs — the two must not
   diverge.
3. Editor palette in `tools/studio/workflow_editor.py` populated from
   `tool_registry.list_tools()`, filtered by the dwo-mcp-02 allowlist, with the
   registry `description` as help text and a params form driven by `input_schema`.
4. Document the node type in `args/workflow_templates/README.md`.
5. Ship one worked example template exercising an allowlisted MCP tool.

GEL
- `node_type` remains a closed vocabulary — update every place that switches on
  it (grep for `node_type` across `tools/` and `args/workflow_templates/`).
- Existing `human`/`tool` steps are untouched.

ACCEPTANCE
- A template with an `mcp` step runs identically under Studio and under
  `workflow_composer.py --template ...`.
- The editor palette lists only allowlisted tools.
- `ruff check .` clean.
""",
        depends_on="dwo-mcp-02",
        priority="medium",
    ),

    # ══════════════════════════════════════════════════════════════════
    # VV — Integration, Docs & Verification
    # ══════════════════════════════════════════════════════════════════
    _t(
        "dwo-vv-01",
        "Manifests, feature doc, companion sync and coherence gate",
        f"""{_CONTEXT}
Do not start until the dur / evt / mem / mcp chains have merged — this task
documents the finished shape of all four, not one of them.

BUILD
1. Manifest shards (index stays thin — edit the shards):
   `tools/manifest/icdev-studio-low-code-no-code-platform.md` (run_memory,
   event_sources, mcp_executor, resume), `tools/manifest/workflow-hitl.md`
   (unified approval surface), `tools/manifest/remote-command-gateway.md`
   (event -> trigger routing).
2. `docs/features/phase-{{N}}-durable-workflow-orchestration.md` — the required
   per-phase feature doc. Cover the SuperPlane mapping, the four gaps, and the
   decision recorded in dwo-dur-04.
3. Commands: add only CLIs that actually exist to
   `docs/reference/commands.md`. CLAUDE.md forbids documenting a command whose
   file is not committed (`coherence_checker.py:check_doc_command_paths`).
   Libraries get an import example, not a fake CLI.
4. `python tools/dx/companion.py --sync --write --json` (foreground, never
   background).
5. `python tools/workflow/coherence_checker.py --all --fix --gate` — must pass.
   Open any flagged line before "fixing" it; this checker has known false
   positives.
6. Confirm `.claude/hooks/pre_tool_use.py::APPEND_ONLY_TABLES` lists every new
   append-only table, and `tests/conftest.py::MINIMAL_ICDEV_SCHEMA` every new
   table.

ACCEPTANCE
- Coherence gate green; companion sync committed; no documented command without
  a committed file.
""",
        depends_on="dwo-dur-04",
        priority="high",
        task_type="chore",
    ),
    _t(
        "dwo-vv-02",
        "Test suite for durability, triggers, run memory and MCP dispatch",
        f"""{_CONTEXT}
BUILD — `tests/test_dwo_*.py`, standardised on the shared conftest schema
(`tests/conftest.py` injects the repo root and forces
`ICDEV_STORAGE_BACKEND=sqlite`). Use `get_connection()`, never raw `sqlite3` —
raw connections bypass the %s -> ? translator. Run
`python tools/testing/api_surface_extractor.py --file <module> --json` before
writing tests against a module.

COVERAGE
- durability: gate persisted; reconciler leaves a live gate `awaiting_approval`
  and expires a stale one; cross-process approval releases a run;
  `resume_run()` skips successful steps and does not mutate the original run.
- triggers: filter operators match/reject; unsigned event starts nothing;
  duplicate delivery id starts exactly one run; classification refusal.
- run memory: cross-subprocess read/write; no leakage between run ids;
  `resolve_canvas()` prefers memory and fails loudly with nothing to resolve.
- mcp: schema validation rejects bad params; non-allowlisted tool refused and
  audited; `requires_approval` tool blocks on a gate.
- regression: importing `tools.studio.workflow_runner` writes nothing to the DB.

GEL
- Patch shims correctly: `importlib.import_module("tools.x")` + `setattr`, not
  string-form patching of `tools.xxx` (canonical namespace is `icdev.tools.*`).
- Run from the repo root with an absolute PYTHONPATH.

ACCEPTANCE
- `pytest tests/test_dwo_*.py -v` green from the repo root.
- `pytest tests/ -v --tb=short` shows no new failures.
""",
        depends_on="dwo-vv-01",
        priority="high",
        task_type="test",
    ),
    _t(
        "dwo-vv-03",
        "Playwright V&V including a live restart-durability proof",
        f"""{_CONTEXT}
CLAUDE.md requires Playwright verification after dashboard changes. The headline
claim of this card — "a run survives a restart" — must be proven in the running
app, not only in pytest.

BUILD
1. E2E spec covering: create workflow -> start run -> park on a human step ->
   observe the SSE stream and the awaiting-approval state -> **restart the
   dashboard** -> reload and confirm the run is still awaiting approval ->
   approve -> run completes.
2. Second spec: bind a trigger to an event source, fire a signed test event
   through the gateway, confirm a run starts and the run-detail badge links back
   to the trigger event.
3. Third spec: a workflow with an `mcp` step executes and its result is visible.
4. Screenshots to `playwright/screenshots/<name>.png` (repo convention).
   Note `outputDir` is wiped each run — copy anything that must persist.

GEL
- Register the spec as an `e2e:` skill card alongside the existing ones
  (`e2e:kanban_pipeline`, `e2e:observability`, …) so it is discoverable.
- Verify visually AND in the DOM — a screenshot alone has previously missed
  visual regressions on canvas pages.

ACCEPTANCE
- All three specs pass against a live dashboard.
- The restart proof is captured in screenshots before and after the restart.
- Findings are recorded on the card, not left in `.tmp/` (invisible to the board).
""",
        depends_on="dwo-vv-02",
        priority="high",
        task_type="test",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the DWO kanban card")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--dry-run", action="store_true", help="print, insert nothing")
    args = parser.parse_args()

    if args.dry_run:
        payload = [
            {
                "id": t["id"],
                "title": t["title"],
                "priority": t["priority"],
                "type": t["task_type"],
                "depends_on": t.get("depends_on_task_id"),
                "description_chars": len(t["description"]),
            }
            for t in TASKS
        ]
        if args.json:
            print(json.dumps({"tasks": payload, "count": len(TASKS)}, indent=2))
        else:
            for t in payload:
                dep = f"  <- {t['depends_on']}" if t["depends_on"] else ""
                print(f"{t['id']:<14} [{t['priority']:<6}] {t['title']}{dep}")
            print(f"\n{len(TASKS)} tasks (dry run — nothing inserted)")
        return 0

    from tools.kanban.task_factory import create_tasks

    created = create_tasks(TASKS)
    report = {
        "created": created,
        "created_count": len(created),
        "submitted_count": len(TASKS),
        "skipped_existing": [t["id"] for t in TASKS if t["id"] not in created],
        "spike": SPIKE,
        "gated": False,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Seeded {len(created)}/{len(TASKS)} DWO tasks")
        for tid in created:
            print(f"  + {tid}")
        if report["skipped_existing"]:
            print("  (already present: " + ", ".join(report["skipped_existing"]) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
