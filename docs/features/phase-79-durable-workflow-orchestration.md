# CUI // SP-CTI

# Phase 79 — DWO: Durable Workflow Orchestration

**Date:** 2026-07-28
**Card:** DWO — Durable Workflow Orchestration (`args/projects.yaml`, `task_prefix: dwo-`)
**Spike:** [docs/spikes/dwo-00-superplane-workflow-adaptation.md](../spikes/dwo-00-superplane-workflow-adaptation.md)
**Deep-dive (design rationale + rejected alternatives):** [docs/features/dwo-durable-workflow-orchestration.md](dwo-durable-workflow-orchestration.md)
**Primary modules:** `tools/studio/workflow_runner.py`, `tools/studio/run_memory.py`,
`tools/studio/gate_bridge.py`, `tools/studio/init_db.py`,
`tools/db/migrations/303_studio_run_memory.sql`, `tools/db/migrations/304_studio_event_tables.sql`,
`args/security_gates.yaml`, `tools/dashboard/api/studio.py`,
`tools/dashboard/static/js/workflow-studio-exec.js`

> **Phase numbering.** The running phase register is `docs/reference/adrs.md`, whose
> highest entry before this one is **Phase 78 — DVG (Divergent Ideation)**. DWO is
> therefore Phase 79. (The unnumbered companion doc
> `docs/features/dwo-durable-workflow-orchestration.md` predates this file and remains
> the long-form rationale; this document is the per-phase feature record.)

---

## Overview

SuperPlane (`github.com/superplanehq/superplane`, Apache-2.0, Go + React + PostgreSQL +
RabbitMQ) is an open-source control plane for agentic engineering. The DWO card asked one
question — *port it, run it as a sidecar, or adapt its concepts?* — and the spike answered
**adapt the concepts**. Roughly 90% of SuperPlane's primitives already exist in Python in
this tree; four capabilities were genuinely missing. This phase closes those four, each by
extending an existing surface rather than standing up a parallel one. No Go, no broker, no
npm, no new runtime dependency.

---

## 1. SuperPlane mapping

SuperPlane's primitives against what ICDEV™ already owned at the start of the phase:

| SuperPlane primitive | Meaning upstream | ICDEV™ equivalent | State before DWO |
|---|---|---|---|
| **Canvas** (`canvas.yaml`) | Graph of steps + dependencies | `args/workflow_templates/*.yaml` — 41 templates with `steps`, `depends_on`, `node_type`, `timeout`, `role` | Solid |
| DAG resolution | Dependency ordering | `tools/orchestration/workflow_composer.py` (`graphlib.TopologicalSorter`; D26, D40, D343) | Solid |
| **Run** | Durable execution, resumable steps | `tools/studio/workflow_runner.py` + `studio_workflow_runs` / `studio_workflow_run_steps` | Ran, but **not durable** → G1 |
| **Component** (action) | Integration-backed task | `tools/studio/executors/` — terraform plan/apply/destroy, ansible, aws-config, gns3, validation, migration reporter | Thin (9) → G4 |
| **Component** (trigger) / **Event** | Incoming event starts a run; payload becomes input | `tools/studio/automation_builder.py` (10 internal `TRIGGER_TYPES`), `tools/gateway/gateway_agent.py` (9 channels, HMAC envelope, 8-gate `security_chain.py`) | Both present, **not connected** → G2 |
| **Memory** | App-scoped JSON across runs | *(nothing)* — steps passed data by stdout-JSON scraping | Absent → G3 |
| **Console** | Declarative panel grid per app | `tools/studio/dashboard_builder.py`, `studio_dashboards.layout_json` | Already covered — out of scope |
| Visual graph editor | React canvas editor | `tools/studio/workflow_editor.py` (server-rendered) | Already covered — out of scope |
| *(no upstream equivalent)* | — | `tools/workflow_canvas/` — BPMN, process-as-code, SLA, conformance | Beyond SuperPlane |
| *(no upstream equivalent)* | — | `tools/workflow_hitl/` — `wf_external_steps`, webhook-token completion | Parallel to Studio gates → dwo-dur-04 |

### ⚠ Vocabulary collision

SuperPlane's **canvas** is a *workflow graph*. An ICDEV **canvas** is a *domain module*
(NDC, IDC, DSOC, …) registered in `args/component_registry.yaml`. Nothing adopted here
reuses "canvas" for a graph — the vocabulary throughout DWO is **workflow**, **run**,
**trigger**, **event source**.

### Options considered

| Option | Verdict |
|---|---|
| Full port (Go → Python) | **Rejected** — 2,695-commit project; would duplicate the composer, runner, editor and audit trail already owned. |
| Sidecar (run SuperPlane, call ICDEV over MCP) | **Rejected** — adds Go + RabbitMQ + a React/npm build; fails air-gap and IL5/IL6 deployment, and moves orchestration state outside the ICDEV audit trail. |
| **Concept adaptation into Studio/WFC** | **Selected** — four slices, all in existing Python modules. |

Apache-2.0 permits reusing the declarative schema shape; attribution belongs in `NOTICE`
if the trigger/event YAML ends up closely mirroring theirs.

---

## 2. The four gaps

### G1 — DUR: execution was not durable

`workflow_runner._cleanup_orphaned_gates()` ran at **import time** — i.e. on every dashboard
start — and force-failed every run and step sitting at `awaiting_approval`. Gates blocked on
an in-memory `threading.Event`, and `get_pending_approvals()` read that dict rather than the
database. A workflow parked on a human approver (templates use `timeout: 3600`) could not
survive a deploy, a restart or a crash. This was a correctness defect, not a missing feature.

**Delivered**

| Piece | Where |
|---|---|
| `get_pending_approvals()` reads the DB, not `_approval_events` | `workflow_runner.py:554` |
| `_await_gate()` waits on the in-process Event **and** polls the DB, so a cross-process decision releases the gate | `workflow_runner.py:576` |
| `reconcile_runs_on_boot()` — called once from app startup, **never at import** — resumes gates inside their window, expires those past it, and fails steps orphaned at `running` | `workflow_runner.py:958` |
| `_load_prior_steps()` — steps already `success`/`approved`/`skipped` are replayed, not re-executed (`terraform apply` is not idempotent) | `workflow_runner.py:625` |
| Per-step `retries` / `retry_backoff_seconds` (both default `0`; only `failed`/`timeout` retried; linear backoff capped at 300 s) | `_retry_policy()`, `_exec_step_with_retries()` |
| `POST /api/studio/runs/<run_id>/resume` (alias `/api/studio/workflows/runs/<run_id>/resume`) | `tools/dashboard/api/studio.py` |
| **▶ Resume Run** control on the run detail modal | `workflow-studio-exec.js` |

**Divergence from the spike.** The spike sketched a new `studio_workflow_gates` table. The
delivered design instead makes the existing append-only `studio_workflow_run_steps` rows the
durable source of truth — a gate *is* a step row at `awaiting_approval`. That needed no
migration, and it is what lets an approval issued before an interruption still resolve after
one, because the `step_run_id` never moves.

**Resume is in place, not forked.** `RESUME_MODE = "in_place"`: `resume_run()` re-attaches a
worker to the original run row rather than inserting a new run carrying
`resumed_from_run_id`. Forking would strand approvals already issued against the original
`step_run_id`. `RESUMABLE_RUN_STATUSES = (pending, running, awaiting_approval, failed)` —
`failed` is included deliberately, because a run that died part-way is the case resume exists
for. Full argument and the rejected fork alternative:
[dwo-durable-workflow-orchestration.md](dwo-durable-workflow-orchestration.md).

### G2 — EVT: events were received but never bound to workflows

The gateway ingested external events through a hardened path (HMAC envelope + 8-gate security
chain + IL-aware response filtering) and `workflow_composer` ran DAGs, but nothing connected
the two. There was no event-source registry, no payload match/filter, and no way to pass an
event payload into a run as input. `automation_builder.TRIGGER_TYPES` covered *internal*
events only.

**Delivered (schema, `dwo-evt-01-d1`)** — migration `304_studio_event_tables.sql`, also
registered in `init_db.STUDIO_TABLES` so a fresh install gets them without the migration:

| Table | Role |
|---|---|
| `studio_event_sources` | One row per source; `kind` CHECK-constrained to `gateway_channel｜canvas_bus｜schedule｜manual` |
| `studio_workflow_triggers` | "event X matching filter Y on source S starts workflow Z" — `filter_json` + `input_mapping_json` |
| `studio_trigger_events` | **APPEND-ONLY** (NIST AU) — one row per evaluated event *including non-matches* (`matched=0`, `run_id` NULL, with a `reason`), because a trigger that silently never fires is otherwise undiagnosable. This table answers "why did this run start". |

Design constraints held: routing goes **through** `tools/gateway/security_chain.py` — no new
unauthenticated ingress; the filter language reuses
`automation_builder.CONDITION_OPERATORS` (equals, not_equals, contains, greater_than,
less_than, in_list, is_empty, is_not_empty) — deliberately **no second condition DSL**; JSON
columns are TEXT parsed in Python, never with SQLite JSON functions (CLAUDE.md PG
portability rule). The table is registered in `init_db.APPEND_ONLY_TABLES` and the
`.claude/hooks/pre_tool_use.py` hook.

**Not yet in the tree:** the CRUD/`match_event()` layer (rest of `dwo-evt-01`) and the
gateway→run routing that consumes these tables (`dwo-evt-02`/`-03`). This slice is schema
only.

### G3 — MEM: no run-scoped shared state

Steps communicated only through stdout JSON scraped by the next executor.
`executors/_base.py::resolve_canvas()` reconstructed the canvas slug by sniffing artifact
paths, then falling back to the workflow-name prefix, then to a hardcoded default — a
four-strategy workaround for the absence of shared run state (and the source of a real bug:
an unknown canvas resolved to `ddc`).

**Delivered** — `tools/studio/run_memory.py` + migration `303_studio_run_memory.sql`:

- `studio_run_memory`, PK `(run_id, key)`, JSON parsed in Python.
- API: `get` / `set` / `all` / `delete` / `copy(src_run_id, dst_run_id)`. `copy` exists for a
  forked-resume model; the shipped path is in-place, so no runtime caller forks a run id today.
- The runner exports `ICDEV_RUN_ID` into every step's environment.
- Reserved keys: `_inputs` (run inputs), `canvas` (the template's `canvas:` slug, published
  before the first step), `artifacts` (`{step_name: [artifact, …]}`, published as each step
  completes).
- `executors/_base.py` `resolve_canvas()` / `get_iac_artifacts()` read run memory first
  (`dwo-mem-02`) and keep the old fallbacks, so existing templates keep working.

This is Studio run state — **not** `tools/memory/` (long-term session memory).

### G4 — MCP: nine executors

SuperPlane's leverage is ~50 integration adapters; ICDEV had 9, all infrastructure-facing.
The fix was **not** to hand-write 50 more: `tools/mcp/tool_registry.py` already declares
455 tools (`len(TOOL_REGISTRY)` at the close of this phase; the spike counted 444), each
with a `module` + `handler` pair that can be imported and called. One generic executor
turns the whole MCP surface into workflow actions.

**Delivered**

- **`node_type: mcp`** (`dwo-mcp-03`) — a step names a registry tool in `mcp_tool` (+ optional
  `mcp_params`) instead of a path in `tool`, and is compiled to
  `<MCP_EXECUTOR> --tool <name> --params <json>` plus `--step-id` / `--project-id` /
  `--run-id` / `--json`. A step's `args` are **not** forwarded — an MCP tool takes its
  arguments from `mcp_params` only. A step with no `mcp_tool` is skipped with that reason.
- **Every `node_type` enumeration taught about `mcp`** (`dwo-mcp-03-d4`) — the template linter
  `VALID_NODE_TYPES`, the schema README, and the run view (which had rendered `mcp` steps with
  no tool label).
- **Default-deny authorization** (`dwo-mcp-02-d1`) — `mcp_workflow_tools` in
  `args/security_gates.yaml`, gate `MCP-WF-001`: 16 read-only tools in `allowed`, 13
  state-changing/destructive/egress-bearing tools in `requires_approval` (dispatched only
  after an approved `node_type: human` gate in the same run). Anything unnamed is refused
  before import/dispatch and audited. IL and role limits are not restated in the list — they
  come from the tool's own registry metadata plus `args/component_registry.yaml`.

**Not yet in the tree:** the executor script `tools/studio/executors/mcp_executor.py`
(`dwo-mcp-01`). Until it merges, an `mcp` step degrades to the existing "Tool not found"
skip rather than failing a run.

---

## 3. Decision recorded in dwo-dur-04 — ONE reviewer inbox

Two human-approval surfaces existed that did not know about each other:

- **Studio gates** — `node_type: human` steps parked as `studio_workflow_run_steps` rows at
  `awaiting_approval`, released by an in-process `threading.Event` or a DB poll (Telegram
  `/approve`);
- **workflow_hitl external steps** — `wf_external_steps`, completed via the webhook-token
  path, with notifier / teams / templates behind them.

An approver had two inboxes and could not see one from the other.

**Direction (a) was taken: Studio is a *producer* of `wf_external_steps`; workflow_hitl
remains the single reviewer inbox.** workflow_hitl already owns notification routing, teams,
templates and webhook-token completion; Studio reimplements none of it. Direction (b) — a
foreign key from `wf_external_steps` back to the Studio gate, with the HITL blueprint
rendering both row shapes — was rejected because it leaves two inboxes and asks the blueprint
to special-case a second shape.

`tools/studio/gate_bridge.py` is the whole of the seam:

- **No new schema.** `external_system='studio'` and `external_ref=<step_run_id>` give lookup
  in both directions off columns that already exist. `step_type` is CHECK-constrained, so a
  Studio gate rides the existing `'manual'` type. The `instance_id`/`template_id` FKs are
  satisfied by one shared system template (`wft-studio-gate`) and one shadow instance per run
  (`wfi-studio-<run_id>`), both created idempotently.
- **Exactly once, in either direction.** A decision taken in workflow_hitl, in the Studio
  Details modal, or via Telegram `/approve <step_run_id>` releases the other surface once.
  Two guards: a `_bridging` re-entrancy guard suppresses the callback into the originating
  surface, and terminal-status checks on both sides reject-and-audit a second decision on an
  already-decided gate. The Telegram path wrote its decision straight to the DB, bypassing
  `approve_step()`, so it now closes the mirrored external step itself — otherwise a Telegram
  approval left an orphan in the reviewer inbox.
- **One audit trail.** `studio_gate_opened`, `studio_gate_approved`, `studio_gate_rejected`,
  `studio_gate_duplicate_decision_rejected` go to the shared append-only audit via
  `tools.audit.audit_logger.log_event`, whichever surface decided. Audit failure never breaks
  the gate.
- **Graceful degrade.** If workflow_hitl is unavailable (tables absent, feature disabled),
  `open_gate()` returns `None` and Studio falls back to its own Details-modal gate, unchanged.

---

## 4. Delivery status

| Epic | Task | State |
|---|---|---|
| DUR | `dwo-dur-01/02` gate durability + boot reconciliation | Merged |
| DUR | `dwo-dur-03` retries, resume API, resume control | Merged |
| DUR | `dwo-dur-04` gate bridge — one reviewer inbox | Merged |
| EVT | `dwo-evt-01-d1` event source / trigger / trigger-event tables | Merged (schema only) |
| EVT | rest of `dwo-evt-01` (CRUD + `match_event()`), `dwo-evt-02/03` routing | Not in tree |
| MEM | `dwo-mem-01` run-scoped memory store | Merged |
| MEM | `dwo-mem-02` executors read canvas/artifacts from run memory | Merged |
| MCP | `dwo-mcp-02-d1` default-deny allowlist | Merged |
| MCP | `dwo-mcp-03` `node_type: mcp`; `-d4` enumeration sweep | Merged |
| MCP | `dwo-mcp-01` `executors/mcp_executor.py` | Not in tree |
| VV | `dwo-vv-01-d1/d2` manifest shard + `icdev/` mirror; `-d3` this document | Merged / this file |

---

## 5. Tests

| File | Covers |
|---|---|
| `tests/test_dwo_gate_durability.py` | Gates survive a restart; boot reconciliation resumes / expires / orphans correctly |
| `tests/test_dwo_dur_03_resume_surface.py` | Retry policy parsing and defaults, retry execution, resumable-status contract, API 202/404/409, UI control parity with the Python constant |
| `tests/test_dwo_gate_bridge.py` | One parked gate → exactly one reviewer-inbox row (re-parking does not duplicate); release in both directions; Telegram approval closes the external step; rejection propagates; a second decision on a decided gate is refused without overwriting the first decider |
| `tests/test_dwo_event_tables.py` | The three event tables, their CHECK constraints and append-only registration |
| `tests/test_dwo_run_memory.py` | `get`/`set`/`all`/`delete`/`copy`, and memory surviving a resume |
| `tests/test_dwo_executor_run_memory.py`, `tests/test_dwo_canvas_resolution.py` | Executors read canvas/artifacts from run memory; an unknown canvas no longer resolves to `ddc` |
| `tests/test_dwo_mcp_allowlist.py` | Default-deny; `allowed` vs `requires_approval` partition; unknown tool refused |
| `tests/test_dwo_mcp_node_type.py`, `tests/test_dwo_mcp_node_type_enumerations.py` | `mcp` step compilation, `mcp_params` handling, and every `node_type` enumeration knowing about `mcp` |

---

## 6. Out of scope

- Rewriting the workflow editor UI in React — ICDEV stays server-rendered.
- RabbitMQ or any new message broker — the canvas event bus (migration `039_canvas_events`)
  and the gateway remain the transport.
- SuperPlane's **Console** primitive — `studio_dashboards` already covers declarative panel
  grids; revisit only if run-driven panels are requested.
- Importing SuperPlane Go code in any form.

---

CUI // SP-CTI
