# Workflow Templates — Extended YAML Schema

This directory contains YAML workflow templates consumed by ICDEV™ Studio and the
FORGE orchestration engine.  Each template is a DAG of *step nodes* with optional
human-gate and approval-gate semantics layered on top of the base `tool` execution
model.

---

## Step node — base fields

Every step must declare `id`, `name`, and `tool`.  All other fields are optional.

```yaml
steps:
  - id: <string>           # unique within this template
    name: <string>         # human-readable label
    tool: <string>         # path to the Python tool script
    description: <string>  # one-line purpose
    depends_on:            # list (or single string) of step ids this step waits for
      - <step_id>
    args: {}               # key-value pairs forwarded to the tool
    inject_project_id: false
    required: false
```

---

## Extended fields — node type and routing

Seven optional fields extend each step node to support human-in-the-loop gates,
approval workflows, MCP tool dispatch, documentation hooks, and team-member
routing.

### `node_type`

```yaml
node_type: tool | human | approval | mcp | agent   # default: tool
```

| Value | Meaning |
|-------|---------|
| `tool` | (default) Automated step.  The engine executes `tool` directly.  No human interaction required. |
| `human` | A person must perform or confirm this step before the DAG may proceed. |
| `approval` | One or more named approvers must sign off before the DAG may proceed. |
| `mcp` | Automated step that dispatches a registered MCP tool.  The step names the tool in `mcp_tool`, not a script path in `tool`. |
| `agent` | Automated step that runs an **agent loop**.  The step names its task in `prompt` and bounds its tools with `agent_tools`, not a script path in `tool`. |

> Omitting `node_type` is identical to `node_type: tool`.

---

### `mcp_tool` / `mcp_params`

```yaml
- id: scan
  name: Scan Dependencies
  node_type: mcp
  mcp_tool: scan_dependencies        # a name in tools/mcp/tool_registry.py::TOOL_REGISTRY
  mcp_params:                        # forwarded to the tool's handler as its arguments
    path: tools/
```

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `mcp_tool` | string | **yes** (for `node_type: mcp`) | Name of the MCP tool to dispatch.  Must be a `tools/mcp/tool_registry.py::TOOL_REGISTRY` key **and** allowlisted in `mcp_workflow_tools` (see below). |
| `mcp_params` | dict | no | Arguments forwarded to the tool's handler.  Defaults to `{}` when omitted. |

Only meaningful when `node_type: mcp`.  The engine ignores the step's `tool`
field for these nodes and runs every one of them through the shared executor
`tools/studio/executors/mcp_executor.py`:

```
python tools/studio/executors/mcp_executor.py \
    --tool scan_dependencies --params '{"path": "tools/"}' \
    --step-id scan --project-id <id> --run-id <id> --json
```

`mcp_params` is normally a mapping and is serialized to JSON for `--params`.
A string is passed through verbatim, so a template may hand-author the JSON;
either way the executor rejects anything that is not a JSON object.  A step
with `node_type: mcp` and no `mcp_tool` is skipped rather than run.

The step's per-run `args` are **not** forwarded — an MCP tool takes its
arguments from `mcp_params` only.  This holds for the composer's per-step
argument overrides too, which apply to `args` and so never reach an mcp step.

Both engines build that invocation identically: Studio's
`tools/studio/workflow_runner.py` and the headless
`tools/orchestration/workflow_composer.py` (which takes the run ID as
`--run-id`).  A template with an mcp step therefore runs the same way from the
UI, from cron, and from an air-gapped shell —
`tests/test_dwo_mcp_composer_parity.py` asserts the two builders agree.

#### The workflow tool surface is default-deny

Naming a tool that exists in `TOOL_REGISTRY` is **not** sufficient.  The registry
exposes 500+ tools, many destructive or classification-sensitive, so the workflow
surface is default-deny: a step may dispatch a tool only when that tool is listed
in the `mcp_workflow_tools` block of `args/security_gates.yaml` (gate `MCP-WF-001`).

| List | Behaviour |
|------|-----------|
| `allowed` | Dispatched unattended.  Read-only / advisory tools only — e.g. `rag_search`, `stig_check`, `scan_dependencies`, `kanban_list_tasks`. |
| `requires_approval` | Dispatched **only** after an approved `node_type: human` gate in the same run.  State-changing, destructive, or egress-bearing — e.g. `terraform_apply`, `k8s_deploy`, `rollback`, `emass_sync`. |
| anything else | Refused before import/dispatch, and audited. |

A typo reads as "not allowlisted", so the step is refused at *runtime*, not at
edit time — the template still lints clean.  IL and role limits are not restated
in that block; they come from the tool's own registry metadata plus
`args/component_registry.yaml` and are evaluated per-dispatch alongside the
allowlist.  Refusals and approvals are both audited append-only.

---

### `prompt` / `agent_tools` — `node_type: agent`

```yaml
- id: document
  name: Document The Module
  node_type: agent
  prompt: "Read tools/foo.py and add a module docstring explaining what it does."
  agent_tools: [worktree_build]     # bundle names from args/agent_toolsets.yaml
  llm_function: code_generation     # a routing FUNCTION, never a model id
  work_dir: /path/to/worktree       # default: the repo root
```

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `prompt` | string | **yes** | The task the agent is asked to carry out. |
| `agent_tools` | list | **yes** | Toolset bundle names bounding what the agent may call. |
| `system_prompt` | string | no | Overrides the default "you are a workflow step" instruction. |
| `llm_function` | string | no | Router routing key (default `code_generation`).  **Never a model id** — which provider serves it is `args/llm_config.yaml`'s decision. |
| `work_dir` | string | no | Root the file tools are confined to (default: the repo root). |
| `max_iterations` / `max_tokens` / `effort` | — | no | Loop budget knobs. |
| `approval_mode` | string | no | `enforce` (default) / `dry_run` / `off` for the reversibility gate. |

Every one of these steps runs through the shared executor
`tools/studio/executors/agent_executor.py`, the way every mcp step runs through
`mcp_executor.py`:

```
python tools/studio/executors/agent_executor.py \
    --prompt "Read tools/foo.py and add a module docstring..." \
    --agent-tools worktree_build \
    --step-id document --project-id <id> --run-id <id> --json
```

A step with `node_type: agent` and no `prompt` is skipped rather than run.

**The agent tool surface is default-deny.**  `agent_tools` resolves through
`tools/agent_runtime/toolsets.py::resolve_bundles` against
`args/agent_toolsets.yaml`, and the offered toolset is the worktree toolset
(`tools/genesis/rubric_build_tools.py` — every path resolved and
traversal-guarded inside `work_dir`) **intersected** with what those bundles
name.  A step that declares no bundle is refused, not handed every tool.  Every
call then passes the reversibility gate in
`tools/agent_runtime/approval_gate.py`, whose tiers live in
`args/agent_approval_policy.yaml` and whose decisions are audited append-only —
the default approver denies on EOF, so an unattended run fails closed on
anything the policy calls irreversible or unknown.

**A provider that cannot do native tool use degrades the step, it does not fail
the run.**  The CLI-bridge provider flattens tools to text, and some local models
declare `supports_tools: false`; either way the loop cannot run.  The step then
records `skipped` with the reason as its stderr and the workflow continues, so a
template with an agent step is still deployable on a box whose model cannot serve
one.  Dependents can branch on it with
`when: {field: output.degraded, operator: equals, value: "True"}`.

Files the agent wrote or patched are declared as the step's `artifacts`, which
the runner lifts into run memory under this step's name like any other step's.

---

### `role`

```yaml
role: <string>   # role label from wf_team_members
```

Interpretation depends on `node_type`:

| `node_type` | `role` meaning |
|-------------|----------------|
| `tool` | Ignored at runtime. |
| `human` | The owner responsible for completing this step (looked up in `wf_team_members`). |
| `approval` | The approver group whose members are eligible to sign off. |

---

### `human_required`

```yaml
human_required: true | false   # only meaningful when node_type: human
```

When `true` the engine will not auto-advance even if the underlying tool exits 0.
A human acknowledgment event is mandatory.  When `false` the step behaves like a
`tool` node but is surfaced in the human-task inbox for visibility.

Only applies when `node_type: human`.  Ignored for all other node types.

---

### `approval_policy`

```yaml
approval_policy: any_one | all | majority   # only for node_type: approval
```

| Value | Quorum rule |
|-------|-------------|
| `any_one` | A single approval from any member of `role` unblocks the step. |
| `all` | Every member of `role` must approve before the step unblocks. |
| `majority` | More than half of `role` members must approve. |

Only applies when `node_type: approval`.  Ignored for all other node types.

---

### `doc_template`

```yaml
doc_template: <string>   # wf_doc_templates.name  (optional SOP / checklist)
```

References a named document template from the `wf_doc_templates` table.  When set,
the engine attaches the rendered SOP or checklist to the step's task card so that
the human or approver has the relevant procedure available inline.

Applies to any `node_type`; most useful for `human` and `approval` nodes.

---

## Annotated 4-step example

The workflow below models a common release gate pattern:

1. An automated build step
2. A human QA review gate
3. A multi-person approval gate
4. An automated deploy step

```yaml
# CUI // SP-CTI
description: "Release gate: build → QA review → change-board approval → deploy"
category: build

steps:

  # ── Step 1 ── automated tool node (default) ─────────────────────────────
  - id: build
    name: "Build Artifacts"
    tool: "tools/builder/build.py"
    description: "Compile, lint, and package the application"
    node_type: tool        # explicit; could be omitted — same effect
    args:
      env: production

  # ── Step 2 ── human gate ─────────────────────────────────────────────────
  - id: qa_review
    name: "QA Smoke Review"
    tool: "tools/testing/smoke_check.py"
    description: "QA engineer validates smoke-test results before promotion"
    depends_on: [build]
    node_type: human
    role: qa_engineer      # looked up in wf_team_members; this person owns the task
    human_required: true   # engine will NOT auto-advance; explicit sign-off required
    doc_template: qa_smoke_checklist  # SOP attached to the task card

  # ── Step 3 ── approval gate ──────────────────────────────────────────────
  - id: change_board_approval
    name: "Change Board Sign-off"
    tool: "tools/workflow/approval_notifier.py"
    description: "Change Advisory Board must approve before production deploy"
    depends_on: [qa_review]
    node_type: approval
    role: change_advisory_board   # group defined in wf_team_members
    approval_policy: majority     # >50 % of board members must approve
    doc_template: cab_approval_form

  # ── Step 4 ── automated tool node ────────────────────────────────────────
  - id: deploy
    name: "Production Deploy"
    tool: "tools/deploy/deploy.py"
    description: "Push artifacts to production after approval"
    depends_on: [change_board_approval]
    node_type: tool
    args:
      target: production
      strategy: blue_green
```

---

## Field applicability matrix

| Field | `tool` | `human` | `approval` | `mcp` | `agent` |
|-------|--------|---------|------------|-------|---------|
| `node_type` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `tool` | required | required | required | ignored | ignored |
| `mcp_tool` | ignored | ignored | ignored | **required** | ignored |
| `mcp_params` | ignored | ignored | ignored | optional | ignored |
| `prompt` | ignored | ignored | ignored | ignored | **required** |
| `agent_tools` | ignored | ignored | ignored | ignored | **required** |
| `system_prompt` / `llm_function` / `work_dir` | ignored | ignored | ignored | ignored | optional |
| `max_iterations` / `max_tokens` / `effort` / `approval_mode` | ignored | ignored | ignored | ignored | optional |
| `args` | forwarded | forwarded | forwarded | **not forwarded** | **not forwarded** |
| `role` | ignored | owner | approver group | ignored | ignored |
| `human_required` | ignored | ✓ | ignored | ignored | ignored |
| `approval_policy` | ignored | ignored | ✓ | ignored | ignored |
| `doc_template` | optional | optional | optional | optional | optional |

---

## Validation

`tools/studio/template_linter.py` validates all templates in this directory.
It checks DAG connectivity (isolated nodes, disconnected subgraphs, dangling
`depends_on` references) **and** `node_type` values.

```bash
python tools/studio/template_linter.py --check        # report
python tools/studio/template_linter.py --check --gate # CI exit-1 on failure
python tools/studio/template_linter.py --fix          # auto-fix DAG issues
```

Allowed `node_type` values: `tool`, `human`, `approval`, `mcp`, `agent` (or
omitted).  Any other value is reported as a lint error.

An `agent` step is additionally linted for the two keys that fail quietly at run
time when they are missing: a step with no `prompt` is skipped by the runner, and
a step with no `agent_tools` is refused by the executor's default-deny allowlist.
Both are reported under `bad_agent`.

---

## `max_parallel` — top-level key (optional, default `1`)

`max_parallel` is an **optional top-level key** (sibling of `steps`) that sets how
many steps `tools/studio/workflow_runner.py` may execute concurrently.

```yaml
max_parallel: 3     # up to three steps in flight at once
steps:
  - {id: gap_analysis, name: Gap Analysis, tool: tools/x.py}
  - {id: roi_model,    name: ROI Model,    tool: tools/y.py}
  - {id: coa_a, name: "COA-A", tool: tools/z.py, depends_on: [gap_analysis, roi_model]}
  - {id: coa_b, name: "COA-B", tool: tools/z.py, depends_on: [gap_analysis, roi_model]}
  - {id: coa_c, name: "COA-C", tool: tools/z.py, depends_on: [gap_analysis, roi_model]}
  - {id: brief, name: Brief,   tool: tools/w.py, depends_on: [coa_a, coa_b, coa_c]}
```

* **Omitting it means `1`** — one step at a time, in the order a flattened
  topological sort produces. Every template that predates this key therefore
  executes exactly as it always has.
* The runner walks the DAG with `graphlib.TopologicalSorter.get_ready()` /
  `done()` inside a bounded thread pool (decisions D40 and D36). A step is
  dispatched as soon as every one of its `depends_on` entries has finished.
* **A join needs no barrier field.** `brief` above waits for all three COAs
  purely because it names all three in `depends_on`.
* A `node_type: human` or `approval` gate parks **its own branch only** —
  sibling branches keep running in the remaining slots.
* Values are clamped to `1..16`; an unparseable value degrades to `1`.

---

## `narrative_context` — top-level block (optional)

`narrative_context` is an **optional top-level key** (sibling of `steps`) that
provides human-readable framing for a workflow.  The WNE narrative engine uses
it to generate stakeholder-facing reports and executive summaries.  All fields
are optional — WNE degrades gracefully when the block or any individual field
is omitted.

```yaml
narrative_context:
  audience: leadership          # leadership | technical | compliance | board | customer
  org_name: "Acme Corp"         # string — organization name
  program_name: "Project Atlas" # string — program or initiative name
  classification: CUI           # string — default CUI; use SECRET for IL6
  purpose: "Modernize legacy data pipelines to reduce cycle time by 40%."
  timeframe_months: 18          # int — planned duration in months
  parameters:
    workforce_size: 500                  # total headcount in scope
    developers_targeted: 80             # engineers receiving training/tooling
    avg_annual_salary_usd: 130000       # used for ROI calculations
    contract_value_usd: 4200000         # total contract or program value
    ai_productivity_gain_pct: 25        # expected productivity uplift (0–100)
    training_cost_per_person_usd: 1200  # blended per-seat training cost
    lab_standup_cost_usd: 85000         # one-time lab / environment setup cost
    free_resources_budget_usd: 0        # budget allocated to free resources
    paid_courses_per_person_usd: 600    # paid course spend per developer
    ilt_cost_per_person_usd: 400        # instructor-led training cost per seat
```

### Field reference

| Field | Type | Allowed values / notes |
|-------|------|------------------------|
| `audience` | string | `leadership`, `technical`, `compliance`, `board`, `customer` |
| `org_name` | string | Free text — name of the organization |
| `program_name` | string | Free text — name of the program or initiative |
| `classification` | string | Any valid marking (default `CUI`; use `SECRET` for IL6) |
| `purpose` | string | One sentence describing the program goal |
| `timeframe_months` | int | Planned duration in whole months |
| `parameters.workforce_size` | numeric | Total headcount in scope |
| `parameters.developers_targeted` | numeric | Developers receiving tooling or training |
| `parameters.avg_annual_salary_usd` | numeric | Blended annual salary for ROI model |
| `parameters.contract_value_usd` | numeric | Total contract or program value in USD |
| `parameters.ai_productivity_gain_pct` | numeric | Expected AI productivity uplift (0–100) |
| `parameters.training_cost_per_person_usd` | numeric | Blended per-seat training cost |
| `parameters.lab_standup_cost_usd` | numeric | One-time lab / environment setup cost |
| `parameters.free_resources_budget_usd` | numeric | Budget allocated to free resources |
| `parameters.paid_courses_per_person_usd` | numeric | Paid course spend per developer |
| `parameters.ilt_cost_per_person_usd` | numeric | Instructor-led training cost per seat |

### Validation rules (enforced by `template_linter.py`)

- If `narrative_context` is present and `audience` is set, it must be one of:
  `leadership`, `technical`, `compliance`, `board`, `customer`.
- If `parameters` is present, every supplied value must be numeric (int or float).
  Non-numeric values (strings, booleans, nulls) are reported as lint errors.
