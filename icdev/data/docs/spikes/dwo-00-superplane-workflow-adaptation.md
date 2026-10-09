# CUI // SP-CTI

# DWO-00 — SuperPlane Adaptation Spike

**Date:** 2026-07-28
**Source:** https://github.com/superplanehq/superplane (Apache-2.0, Go + React, PostgreSQL + RabbitMQ, beta)
**Question:** Port SuperPlane into ICDEV™, integrate it as a sidecar, or adapt its concepts?
**Answer:** **Adapt the concepts.** Neither a port nor a sidecar is viable; ~90% of the primitives
already exist in Python in this tree. Four capabilities are genuinely missing.

---

## 1. What SuperPlane is

An open-source control plane for "agentic engineering" — automation orchestration across
tools and services with human oversight and policy gates.

| Primitive | Meaning |
|-----------|---------|
| **App** | Deployable unit: workflow graph + console UI + app-scoped memory. Git-versioned as `canvas.yaml` + `console.yaml`. |
| **Canvas** | A graph of steps and their dependencies; one canvas can express several workflows and run them concurrently. |
| **Component** | A node — either a trigger (incoming event) or an action (integration-backed task). |
| **Event / Trigger** | Incoming events match triggers to start runs; the event payload becomes run input. |
| **Run** | Durable execution — runs, run items and payloads survive restarts; failed steps resume without custom retry logic. |
| **Memory** | App-scoped JSON storage persisting across runs. |
| **Console** | Per-app operational UI declared as a dynamic grid of panels (KPIs, charts, runbooks, live data). |

Roughly 50 integrations (AI/LLM, VCS/CI, cloud, observability, incident, chat, ticketing).

### ⚠ Vocabulary collision

SuperPlane's **canvas** is a *workflow graph*. ICDEV's **canvas** is a *domain module*
(NDC, IDC, DSOC, …) registered in `args/component_registry.yaml`. Nothing adopted from
SuperPlane may reuse the word "canvas" for a graph — this spike and the DWO card use
**workflow**, **run**, **trigger** and **event source** throughout.

---

## 2. Overlap — what ICDEV™ already has

| SuperPlane concept | ICDEV™ equivalent | State |
|---|---|---|
| `canvas.yaml` DAG | `args/workflow_templates/*.yaml` — 33 templates with `steps`, `depends_on`, `node_type: human\|tool`, `timeout`, `role` | Solid |
| DAG resolution | `tools/orchestration/workflow_composer.py` — `graphlib.TopologicalSorter` (D26, D40, D343) | Solid |
| Run engine + live progress | `tools/studio/workflow_runner.py` — per-run SSE queues, HITL approval gates | Solid |
| Run/run-item persistence | `studio_workflow_runs`, `studio_workflow_run_steps` (append-only) | Solid |
| Visual graph editor | `tools/studio/workflow_editor.py` | Solid |
| Trigger → condition → action | `tools/studio/automation_builder.py`, `studio_automations` — 10 `TRIGGER_TYPES`, 8 `CONDITION_OPERATORS` | Partial |
| Integration-backed actions | `tools/studio/executors/` — terraform plan/apply/destroy, ansible, aws-config, gns3, validation, migration reporter | Thin (9) |
| Consoles | `tools/studio/dashboard_builder.py`, `studio_dashboards.layout_json` | Partial |
| Event ingestion | `tools/gateway/gateway_agent.py` — 9 channels, HMAC `event_envelope.py`, 8-gate `security_chain.py` | Solid, not wired to workflows |
| Process-as-code / BPMN / SLA / conformance | `tools/workflow_canvas/` (WFC) | Beyond SuperPlane |
| External human steps | `tools/workflow_hitl/` — `wf_external_steps`, webhook-token completion | Solid, parallel to Studio gates |

**Conclusion:** the graph model, the editor, the runner, the audit trail, the event
receiver and the approval surfaces are all present. What is absent is *durability*,
*event-to-workflow binding*, *shared run state*, and *action breadth*.

---

## 3. The four gaps

### G1 — Execution is not durable (highest value)

`tools/studio/workflow_runner.py` is currently the **opposite** of durable:

- `_cleanup_orphaned_gates()` runs at *import time* (i.e. every dashboard start) and
  force-fails every run and step sitting in `awaiting_approval`.
- Approval gates block on an in-memory `threading.Event` held in `_approval_events`;
  `get_pending_approvals()` reads that dict, not the database.

A workflow parked on a human approver (templates use `timeout: 3600`) cannot survive a
deploy, a restart, or a crash. This is a correctness defect, not just a missing feature.

### G2 — Events are received but never bound to workflows

The gateway ingests external events through a hardened path (HMAC envelope + 8-gate
security chain + IL-aware response filtering) and `workflow_composer` runs DAGs, but
nothing connects the two. There is no event-source registry, no payload match/filter,
and no way to pass an event payload into a run as input.

`automation_builder.TRIGGER_TYPES` covers *internal* events only (findings, scans,
SLA breach, form submitted, schedule, manual). No `external_event` type exists.

### G3 — No run-scoped shared state

Steps communicate only through stdout JSON scraped by the next executor.
`tools/studio/executors/_base.py::resolve_canvas()` reconstructs the canvas slug by
sniffing artifact paths, then falls back to the workflow-name prefix, then to a hardcoded
default — a four-strategy workaround for the absence of shared run state.

### G4 — Nine executors

SuperPlane's leverage is ~50 integration adapters. ICDEV has 9 executors, all
infrastructure-facing. The fix is **not** to hand-write 50 more: `tools/mcp/tool_registry.py`
already declares 444 tools, each with a `module` + `handler` pair that can be imported and
called. One generic executor turns the whole MCP surface into workflow actions.

---

## 4. Options considered

| Option | Verdict |
|---|---|
| **Full port** (Go → Python rewrite) | **Rejected.** 2,695-commit project; would duplicate the composer, runner, editor and audit trail we already own. |
| **Sidecar** (run SuperPlane, call ICDEV over MCP) | **Rejected.** Adds Go + RabbitMQ + a React/npm build. Fails air-gap and IL5/IL6 deployment, conflicts with the no-npm constraint, and moves orchestration state outside the ICDEV audit trail. |
| **Concept adaptation** into Studio/WFC | **Selected.** Four slices, all in existing Python modules, no new runtime dependency. |

Apache-2.0 permits reusing the declarative schema shape; attribution belongs in `NOTICE`
if the trigger/event YAML ends up closely mirroring theirs.

---

## 5. Selected design — how it gels with what exists

The rule for every slice: **extend the existing surface, do not add a parallel one.**

1. **Durable runs (`dwo-dur-*`)** — gate state moves to a `studio_workflow_gates` table;
   `_cleanup_orphaned_gates` becomes a *reconciler* that re-attaches live gates and expires
   only those past their deadline. Studio gates and `wf_external_steps` (workflow_hitl)
   converge on one approval surface rather than two.
2. **Event sources & triggers (`dwo-evt-*`)** — new `studio_event_sources` /
   `studio_workflow_triggers` tables fed **through** `tools/gateway/security_chain.py`.
   No new unauthenticated ingress. The filter language reuses
   `automation_builder.CONDITION_OPERATORS`; the trigger vocabulary extends
   `TRIGGER_TYPES` with `external_event`. No second rules DSL.
3. **Run memory (`dwo-mem-*`)** — a `studio_run_memory` KV scoped to `run_id`, exposed to
   steps; `executors/_base.py` reads canvas/artifacts from it, keeping today's fallbacks
   so existing templates keep working.
4. **Universal MCP executor (`dwo-mcp-*`)** — one executor dispatching any
   `TOOL_REGISTRY` entry via its `module`/`handler`, gated by an allowlist in
   `args/security_gates.yaml` and the existing IL/RBAC metadata. 444 actions from one file.

Everything lands inside Studio + WFC, is registered in `args/component_registry.yaml`
where a route or adapter is added, and is validated by
`tools/workflow/coherence_checker.py`.

---

## 6. Out of scope

- Rewriting the workflow editor UI (React) — ICDEV stays server-rendered.
- RabbitMQ or any new message broker — the canvas event bus (migration `039_canvas_events`)
  and gateway remain the transport.
- SuperPlane's "Console" primitive — `studio_dashboards` already covers declarative
  panel grids; revisit only if run-driven panels are requested.
- Importing SuperPlane Go code in any form.
