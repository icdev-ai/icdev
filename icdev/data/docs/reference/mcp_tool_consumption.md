# CUI // SP-CTI

# MCP dispatch tool consumption — classifying all 471 declared tools (`rem-cap-02`)

**Measured:** 2026-08-16 against the live PostgreSQL board (`icdev@localhost:5432`),
the SQLite copy at `data/icdev.db`, and 316 Claude Code session transcripts.
**Scope:** every key of `tools/mcp/tool_registry.py::TOOL_REGISTRY`.
**Status:** report only. This task changes no behaviour, no budget, and no gate.

---

## 1. TL;DR

`capability_consumption.py` reports 471 declared `mcp_dispatch_tool` units and 4
consumed. That number is **arithmetically correct and substantively misleading**,
and the card was right to demand the trap be checked first.

| Group | Count | Meaning |
|---|---:|---|
| **reachable** | **73** | An in-repo declaration can dispatch it by name |
| **external-only** | **21** | The only caller is out-of-repo, and something declares it so |
| **unused** | **377** | Neither. Declared, live, importable — and nothing anywhere names it |
| **TOTAL** | **471** | |

**The audit table is not a complete record of dispatch.** It is a complete record
of *one* of four dispatch surfaces, and on that surface it is default-denied to
439 of the 471 tools. Every row it holds — on both databases — is test or probe
traffic. Details in §2; that section is the answer the card asked to be
established before 467 is reported as dead.

So the headline number moves, but not in the reassuring direction: the inert
surface is **377**, not 467, and it is not "almost entirely the sanctioned
external-only state" — external-only accounts for 21 tools, 4.5% of the registry.

---

## 2. Is `studio_mcp_dispatch_audit` a complete record of dispatch? **No.**

Four independent findings, each verifiable on its own.

### 2.1 One writer, one surface

`grep -rn "record_dispatch_audit" tools/` returns exactly two call sites, both in
the Studio workflow executor family:

* `tools/studio/executors/mcp_executor.py::run()` — `node_type: mcp` workflow steps
* `tools/studio/executors/agent_tool_gate.py` — `node_type: agent` steps, which
  delegates to the same function (`agent_tool_gate.py:555`)

Three other surfaces dispatch registry tools and write **nothing** to it:

| Surface | File | Dispatches | Writes dispatch audit? |
|---|---|---|---|
| stdio MCP (the one `.mcp.json` launches) | `tools/mcp/unified_server.py` | all 471 | **No** — writes `runtime_invocations` (`surface='mcp'`) |
| SaaS MCP over HTTP / SSE | `tools/saas/mcp_http.py`, `mcp_sse.py` | its own 19 / 12-entry list (16 / 10 overlap the unified registry) | **No** |
| Agent runtime | `tools/agent_runtime/dispatch.py` | `args/agent_toolsets.yaml` bundles | **No** — writes `runtime_invocations` (`surface='agent'`) |
| A2A bridge | `tools/mcp/a2a_bridge_server.py` | reads the registry, publishes an agent card | **No** |

A tool dispatched by Claude Code, by a tenant's agent over HTTPS, or by the SAG
agent loop leaves no row in the table `capability_consumption.py` reads. It is
structurally incapable of observing them.

### 2.2 Even on its own surface, 439 tools cannot produce an `allowed` row

`mcp_executor.check_tool_allowed()` enforces gate MCP-WF-001 as **default-deny**
before the registry is even touched. `args/security_gates.yaml::mcp_workflow_tools`
names 32 tools (17 `allowed`, 15 `requires_approval`). The other **439 are refused
by policy**, and the Studio workflow editor only offers the allowlist for
authoring (`workflow_editor.py:1882`).

So for 439 of the 471, "zero rows" is not evidence of disuse. It is the gate
working. The only row those tools can ever earn is a `refused` one — and one
exists: `sbom_generate`, `refused`, `mcp_tool_not_allowlisted`.

### 2.3 Every row in the table is test or probe traffic

**PostgreSQL — 19 rows, 4 distinct tools:**

| tool | decision | n | window |
|---|---|---:|---|
| `nist_lookup` | allowed | 8 | 2026-07-29 01:16 → 04:02 |
| `health_check` | allowed | 7 | 2026-08-08 15:25 → 2026-08-09 12:00 |
| `studio_run_start` | allowed / pending / refused | 3 | 2026-08-09 12:00 |
| `studio_run_status` | allowed | 1 | 2026-08-09 11:59 |

Every row has `principal_id = ''` and `caller_source = 'default (no caller
declared)'`, and the `step_id`s are `lookup`, `probe`, `gateprobe`,
`mcp-health_check`. The timestamps sit inside the delivery windows of `dwo-mcp-02`
(late July) and `hgx-agent` (2026-08-08/09). This is the acceptance traffic of the
feature that created the table. There is no organic production dispatch in it.

**SQLite `data/icdev.db` — 51 rows, 15 distinct tools**, a *different* history
from the same table name. Its tool names settle the question:
`stub_echo`, `stub_il5`, `stub_boom`, `stub_missing`, `stub_denied`,
`definitely_not_a_registered_tool`, `definitely_not_allowlisted`,
`no_such_tool_anywhere`, `health_chek` (sic). That is a test suite's residue.

Two consequences worth stating plainly. First, the "4 consumed" figure is
**4 tools that a test touched**, not 4 tools in use — the true production count on
this surface is **0**. Second, the answer depends on which database you ask:
`capability_consumption.py` reads whatever `get_connection()` resolves to, and the
two copies disagree (19 vs 51 rows, 4 vs 15 tools).

### 2.4 The one surface that *does* observe everything is silently dead on the DB it writes to

`unified_server._register_lazy_tool` wraps every dispatch in
`invocation_recorder.record(SURFACE_MCP, …)` — deliberately, as "the one place
that can observe all of them". It writes `runtime_invocations` (migration 341).

But `.mcp.json` launches the server with `ICDEV_DB_PATH=C:/AI/ICDev/data/icdev.db`,
and **that database has no `runtime_invocations` table**:

```
$ sqlite3 data/icdev.db "SELECT COUNT(*) FROM runtime_invocations"
ERR no such table: runtime_invocations
```

`invocation_recorder` degrades exactly as documented — first failed INSERT sets a
process-level flag, every later call short-circuits — so an MCP server started
against that path records nothing and reports nothing. On PostgreSQL the table
exists and holds **26 MCP invocations across 9 tools** since 2026-08-02.

Corroboration from a corpus with no relationship to either database: 316 Claude
Code transcripts contain **7 `mcp__icdev-unified__*` tool_use blocks across 5
tools** — `browser_navigate` (2), `kanban_update_task` (2), `kanban_get_task`,
`kanban_board_summary`, `studio_list_templates`. Same order of magnitude,
independently sourced, and a strict subset of the 9. The two agree.

**Net:** MCP consumption is genuinely tiny — but it is tiny at ~9 tools, not
4, and it is measured through `runtime_invocations`, a table
`capability_consumption.py::probe_mcp_dispatch_tool` does not read.

---

## 3. Method

Classification is by **declaration**, not by text search. An early pass grepping
all 471 names across the repo returned 21,356 hits and 208 "callers"; it was
discarded because tool names like `format`, `lint`, `scaffold`, `introspect`,
`remediate` and `rollback` are ordinary identifiers, so a word match cannot
distinguish a dispatch from a coincidence.

The question a classification must answer is narrower: **what can cause this tool
to run, by name?** That set is small and enumerable.

*In-repo dispatch declarations → `reachable`:*

| Source | Tools in registry | Dispatch path |
|---|---:|---|
| `args/security_gates.yaml::mcp_workflow_tools.allowed` | 17 | `mcp_executor` (audited) |
| `args/security_gates.yaml::mcp_workflow_tools.requires_approval` | 15 | `mcp_executor` (audited) |
| `args/agent_toolsets.yaml` bundles | 48 | `agent_runtime/dispatch.py` (not audited) |
| `args/workflow_templates/*.yaml` `mcp_tool:` | 4 (⊂ allowlist) | `mcp_executor` (audited) |

*Out-of-repo consumer declarations → `external-only`:*

| Source | Tools in registry | Consumer |
|---|---:|---|
| `args/mcp_toolset_profiles.yaml` | 29 | external MCP agent, bounded profile |
| `tools/mcp/cortex_server.py::CORTEX_TOOLS` | 8 | external / air-gapped MCP client |
| `tools/saas/mcp_http.py`, `mcp_sse.py` | 16 / 10 | SaaS tenant's agent over HTTPS |
| measured stdio call with no in-repo declaration | 1 | observed, not merely declared |

Two rules make the partition exact:

1. **`reachable` wins over `external-only`.** 26 tools are both allowlisted and
   in a toolset profile; an in-repo caller existing is the stronger factual claim,
   so they are counted once, as reachable.
2. **A measured external call outranks any declaration.** `studio_list_templates`
   has no in-repo declaration but a recorded stdio invocation in both
   `runtime_invocations` and the transcripts. Observation beats absence of
   paperwork, so it is external-only rather than unused.

Three things checked that are *not* dispatch surfaces, so as not to over-credit
reachability: `tools/saas/rest_api.py::call_tool(...)` passes function objects,
not registry names; `args/ace/roles/**` `icdev_tools` lists shell commands
(`python tools/testing/health_check.py --json`), not tool names — 0 of 10 are in
the registry; and `args/security_gates.yaml::agent_workflow_tools` names worktree
tools (`read_file`, `run_command`), 0 of 9 in the registry.

**All 471 handlers import and resolve.** An import sweep over every
`(module, handler)` pair: `resolved 471, failed 0`. Nothing here is a broken stub
— the unused group is live, maintained code with no caller. (The test that
asserts this, `tests/mcp/test_registry_handler_coverage.py`, is in
`args/ci_test_backlog.txt` and has never gated a merge; it passes today.)

---

## 4. Group 1 — external-only (21)

Sanctioned, per the precedent set in
[`ctx-reach-02`](../design/ctx-reach-02-cortex-client-external-only.md): zero
in-repo consumers is the *correct* state when the only available in-repo caller
would be a loopback into the process already holding the callee. Each entry's
written reason follows; the reasons are given per basis, and every tool below
carries at least one.

**`cortex_*` (8) — bounded external stdio surface.**
`tools/mcp/cortex_server.py` is a second, deliberately narrow stdio entry point
for an external or air-gapped MCP client that must see only the Cortex family and
not the full 471-tool registry. `.mcp.json` deliberately does **not** launch it
(decision `ctx-reach-03`, recorded in the module docstring). The in-repo path to
the same behaviour is `tools/cortex/api.py`, in-process and governed — an in-repo
MCP caller would be an HTTP round trip into the dashboard from the dashboard.
Drift is pinned: every `CORTEX_TOOLS` name must also be in `TOOL_REGISTRY`,
asserted by `tests/cortex/test_cortex_reach_decisions.py`.

**SaaS MCP surface (10) — the caller is a tenant, by construction.**
`tools/saas/mcp_http.py` and `mcp_sse.py` expose a compliance-shaped subset to a
paying tenant's own agent over authenticated HTTPS, gated per-tool by
`MCPToolAuthorizer` (D261, `exa-policy-05`). ICDEV has no in-repo tenant; a
platform-side caller would be ICDEV impersonating its own customer. These tools
are additionally reachable in-process through `tools/saas/rest_api.py`, which
calls the underlying functions directly rather than by registry name — so the
*function* is exercised in-repo while the *tool name* is genuinely tenant-only.

**Curated external-agent profiles (2) — the profile is the declaration.**
`args/mcp_toolset_profiles.yaml` exists so a small local model gets a bounded
tool list instead of 471, and each profile carries a reviewed `cui_egress`
decision enforced at server start (`toolset_profiles.enforce_cui_egress`).
`triage_cve` and `vex_generate` (profile `security`) and `news_aggregate`
(profile `research`) are named there and nowhere in-repo. Being on a profile is a
deliberate, reviewed offer to an out-of-repo consumer.

**Measured (1).** `studio_list_templates` — one recorded stdio invocation
(2026-08-11 22:20) in `runtime_invocations` and in a Claude Code transcript. No
declaration names it; the evidence that it is externally consumed is that it *was*.

| Tool | Category | External consumer | Recorded consumption |
|---|---|---|---|
| `ai_inventory_register` | compliance | SaaS MCP: `mcp_http.py` | — |
| `ai_transparency_audit` | compliance | SaaS MCP: `mcp_http.py` | — |
| `cortex_agent_launch` | cortex | `cortex_server.py::CORTEX_TOOLS` (bounded stdio, ctx-reach-03) | — |
| `cortex_ask` | cortex | `cortex_server.py::CORTEX_TOOLS` (bounded stdio, ctx-reach-03) | — |
| `cortex_classify` | cortex | `cortex_server.py::CORTEX_TOOLS` (bounded stdio, ctx-reach-03) | — |
| `cortex_complete` | cortex | `cortex_server.py::CORTEX_TOOLS` (bounded stdio, ctx-reach-03) | — |
| `cortex_extract` | cortex | `cortex_server.py::CORTEX_TOOLS` (bounded stdio, ctx-reach-03) | — |
| `cortex_govern` | cortex | `cortex_server.py::CORTEX_TOOLS` (bounded stdio, ctx-reach-03) | — |
| `cortex_reason` | cortex | `cortex_server.py::CORTEX_TOOLS` (bounded stdio, ctx-reach-03) | — |
| `cortex_search` | cortex | `cortex_server.py::CORTEX_TOOLS` (bounded stdio, ctx-reach-03) | — |
| `fairness_assess` | compliance | SaaS MCP: `mcp_http.py` | — |
| `fips199_categorize` | compliance | SaaS MCP: `mcp_http.py`, `mcp_sse.py` | — |
| `fips200_validate` | compliance | SaaS MCP: `mcp_http.py`, `mcp_sse.py` | — |
| `model_card_generate` | compliance | SaaS MCP: `mcp_http.py` | — |
| `news_aggregate` | fathomdesk_news | toolset profile `research` | — |
| `project_create` | core | SaaS MCP: `mcp_http.py`, `mcp_sse.py` | — |
| `project_list` | core | SaaS MCP: `mcp_http.py`, `mcp_sse.py` | — |
| `studio_list_templates` | studio | **MEASURED** stdio call | transcripts ×1, `runtime_invocations` ×1 |
| `system_card_generate` | compliance | SaaS MCP: `mcp_http.py` | — |
| `triage_cve` | supply_chain | toolset profile `security` | — |
| `vex_generate` | compliance | toolset profile `security` | — |

**A caveat this group must carry.** `ctx-reach-02` made external-only a *declared*
state with four enforced obligations (a decision doc named in the module
docstring, zero production importers checked bidirectionally, a vendor-parity
declaration, and a gated test). None of these 21 carries those obligations today,
because none is declared in `args/external_only_surfaces.yaml`. They are
classified external-only here on the strength of an existing artifact —
`CORTEX_TOOLS`, a SaaS registry entry, a reviewed toolset profile, a recorded
call — and that is weaker than the `ctx-reach-02` bar. See §7.

---

## 5. Group 2 — reachable (73)

An in-repo declaration can dispatch each of these by name. 16 have a recorded
call somewhere; **57 have none**. For those 57 the card's question — never
dispatched, or dispatch not recorded? — splits cleanly in two, and the split
matters because the two need different fixes.

**36 tools: dispatch is NOT RECORDED.** These are reachable only through
`args/agent_toolsets.yaml` bundles, dispatched by
`tools/agent_runtime/dispatch.py`, which records to `runtime_invocations` with
`surface='agent'` and never touches `studio_mcp_dispatch_audit`. Worse, the agent
surface records `spec.name`, and the 313 distinct `surface='agent'` names on the
live board are **kanban task ids** (`agov-case-01`, `hgx-obs-02`, …), not tool
names — 0 of 313 are in `TOOL_REGISTRY`. So for the bundle path there is
currently no telemetry that can answer "was this tool called" at all. A zero here
is unmeasured, not empty.

**21 tools: NEVER DISPATCHED.** These are on MCP-WF-001, so the audit-writing
path is live for them, would record them, and has in fact recorded eight of their
neighbours. A zero here is a real zero on that surface. Context for why: only 4
of the 32 allowlisted tools are named by any shipped workflow template
(`mcp_posture_review.yaml`: `health_check`, `code_analyze`, `scan_dependencies`,
`stig_check`), so the other 28 are dispatchable but unreferenced by any authored
workflow.

Of the 73, 32 are reachable through the audited path, 41 only through the
unaudited bundle path, and 7 through both.

| Tool | Category | In-repo dispatch declaration | Recorded consumption |
|---|---|---|---|
| `ansible_run` | infra | MCP-WF-001 `requires_approval` | sqlite dispatch audit×2 |
| `browser_click` | browser | bundle `browser` | — |
| `browser_navigate` | browser | bundle `browser` | claude transcripts×2, runtime invocations mcp×2 |
| `browser_read_state` | browser | bundle `browser` | — |
| `browser_screenshot` | browser | bundle `browser` | — |
| `browser_type` | browser | bundle `browser` | — |
| `canvas_compliance_gate` | canvas | bundle `canvas` | — |
| `canvas_compliance_summary` | canvas | bundle `canvas` | — |
| `canvas_compute_readiness` | canvas | bundle `canvas` | — |
| `canvas_create_project` | canvas | bundle `canvas` | — |
| `canvas_link_design` | canvas | bundle `canvas` | — |
| `canvas_list_projects` | canvas | bundle `canvas` | — |
| `canvas_unlink_design` | canvas | bundle `canvas` | — |
| `check_mcp_authorization` | security_agentic | bundle `security` | — |
| `classification_check` | compliance | MCP-WF-001 `allowed`; bundle `compliance` | sqlite dispatch audit×6 |
| `cmmc_assess` | compliance | bundle `compliance` | — |
| `code_analyze` | builder | MCP-WF-001 `allowed`; template `mcp_posture_review.yaml` | — |
| `confabulation_check` | security | bundle `security` | — |
| `control_map` | compliance | bundle `compliance` | — |
| `crosswalk_query` | compliance | MCP-WF-001 `allowed`; bundle `compliance` | — |
| `cui_mark` | compliance | bundle `compliance` | — |
| `databridge_fetch` | databridge | bundle `external_data` | — |
| `databridge_sources` | databridge | bundle `external_data` | — |
| `decompose_requirements` | requirements | bundle `govcon` | — |
| `detect_gaps` | requirements | bundle `govcon` | — |
| `emass_sync` | compliance | MCP-WF-001 `requires_approval` | — |
| `fedramp_assess` | compliance | bundle `compliance` | — |
| `generate_ai_bom` | security_agentic | bundle `security` | — |
| `generate_bdd` | requirements | bundle `govcon` | — |
| `get_status` | innovation | MCP-WF-001 `allowed` | — |
| `guard_result` | security_agentic | bundle `security` | — |
| `health_check` | testing | MCP-WF-001 `allowed`; template `mcp_posture_review.yaml` | pg dispatch audit×7, sqlite dispatch audit×7 |
| `install_asset` | marketplace | MCP-WF-001 `requires_approval` | — |
| `k8s_deploy` | infra | MCP-WF-001 `requires_approval` | — |
| `kanban_board_summary` | kanban | bundle `kanban` | claude transcripts×1, runtime invocations mcp×1 |
| `kanban_create_task` | kanban | bundle `kanban` | runtime invocations mcp×1 |
| `kanban_delete_task` | kanban | MCP-WF-001 `requires_approval` | — |
| `kanban_get_task` | kanban | MCP-WF-001 `allowed`; bundle `kanban` | claude transcripts×1, runtime invocations mcp×12 |
| `kanban_list_tasks` | kanban | MCP-WF-001 `allowed`; bundle `kanban` | runtime invocations mcp×3 |
| `kanban_move_task` | kanban | bundle `kanban` | — |
| `kanban_queue_plan` | kanban | bundle `kanban` | — |
| `kanban_update_task` | kanban | bundle `kanban` | claude transcripts×2, runtime invocations mcp×4 |
| `kg_search` | knowledge_graph | MCP-WF-001 `allowed` | sqlite dispatch audit×1 |
| `nist_lookup` | compliance | MCP-WF-001 `allowed`; bundle `compliance` | pg dispatch audit×8 |
| `oscal_generate` | compliance | bundle `compliance` | — |
| `poam_generate` | compliance | bundle `compliance` | — |
| `project_status` | core | MCP-WF-001 `allowed` | — |
| `publish_asset` | marketplace | MCP-WF-001 `requires_approval` | — |
| `rag_delete_source` | rag | MCP-WF-001 `requires_approval` | — |
| `rag_search` | rag | MCP-WF-001 `allowed` | — |
| `rfi_demand_scan` | govcon | bundle `govcon` | — |
| `rollback` | infra | MCP-WF-001 `requires_approval` | — |
| `rtm_generate` | compliance | bundle `compliance` | — |
| `sandbox_execute` | security | MCP-WF-001 `requires_approval`; bundle `security` | — |
| `sbom_generate` | compliance | bundle `compliance` | sqlite dispatch audit×1 (refused: not allowlisted) |
| `scan_code_patterns` | security_agentic | bundle `security` | — |
| `scan_dependencies` | maintenance | MCP-WF-001 `allowed`; template `mcp_posture_review.yaml` | — |
| `score_readiness` | requirements | bundle `govcon` | — |
| `search_knowledge` | knowledge | MCP-WF-001 `allowed` | — |
| `self_heal` | knowledge | MCP-WF-001 `requires_approval` | — |
| `send_command` | gateway | MCP-WF-001 `requires_approval` | — |
| `ssp_generate` | compliance | bundle `compliance` | — |
| `stig_check` | compliance | MCP-WF-001 `allowed`; bundle `compliance`; template `mcp_posture_review.yaml` | — |
| `studio_list_workflows` | studio | MCP-WF-001 `allowed` | runtime invocations mcp×1 |
| `studio_run_resume` | studio | MCP-WF-001 `requires_approval` | — |
| `studio_run_start` | studio | MCP-WF-001 `requires_approval` | pg dispatch audit×3 |
| `studio_run_status` | studio | MCP-WF-001 `allowed` | pg dispatch audit×1, runtime invocations mcp×1 |
| `terraform_apply` | infra | MCP-WF-001 `requires_approval` | sqlite dispatch audit×9 |
| `trace_query` | observability | MCP-WF-001 `allowed` | — |
| `validate_agent_output` | security_agentic | bundle `security` | — |
| `xacta_sync` | compliance | MCP-WF-001 `requires_approval` | — |
| `zta_assess` | devsecops | bundle `security` | — |
| `zta_posture_check` | devsecops | bundle `security` | — |

`sbom_generate` is the group's clearest single illustration: it is in the
`compliance` bundle, so it is reachable; it is **not** on MCP-WF-001, so a
workflow dispatch attempt was refused and the refusal was audited. One tool, two
surfaces, two different answers — which is the whole reason a single-table probe
cannot classify this registry.

---

## 6. Group 3 — unused (377)

Declared in `TOOL_REGISTRY`, handler imports and resolves, exposed over stdio by
`unified_server` — and named by **no** allowlist, **no** agent bundle, **no**
workflow template, **no** toolset profile, **no** SaaS surface, and **no**
recorded invocation on any surface, on either database, in 316 transcripts.

This is the real declared-but-unconsumed surface, and at 377 it is the largest
single instance of ICDEV's signature defect measured to date.

Two observations before the enumeration. First, the concentration is in
**compliance (42)** and **knowledge_graph (18)** — the two areas where "the
capability exists" is most likely to be counted as evidence, which is the exact
shape of the `file existence as compliance evidence` finding. `audit_chain_sweep`,
`fedramp_ksi_generate` and `sbom_validate_minimum_elements` sitting in this list
is worth its own look. Second, several entries are tools whose *underlying
function* is used constantly through other paths — `lint`, `format`, `run_tests`,
`generate_code` are the ANVIL loop's daily work, reached directly rather than
through MCP. For those, the MCP registration is the unused thing, not the
capability.

| Category | Count | Tools |
|---|---:|---|
| compliance | 42 | `ai_accountability_audit`, `ai_appeal_file`, `ai_appeal_resolve`, `ai_caio_designate`, `ai_ethics_review_submit`, `ai_incident_log`, `ai_oversight_plan_create`, `ai_reassessment_schedule`, `audit_chain_sweep`, `cato_monitor`, `cmmc_report`, `cssp_assess`, `cssp_evidence`, `cssp_ir_plan`, `cssp_report`, `eu_ai_act_classify`, `fedramp_authorization_package`, `fedramp_ksi_generate`, `fedramp_report`, `gao_ai_assess`, `ironbank_generate`, `ironbank_validate`, `ivv_assess`, `ivv_report`, `nist_ai_600_1_assess`, `omb_m25_21_assess`, `omb_m26_04_assess`, `oscal_catalog_lookup`, `oscal_convert`, `oscal_detect_tools`, `oscal_resolve_profile`, `oscal_validate_deep`, `owasp_asi_assess`, `pi_compliance`, `sbd_assess`, `sbd_report`, `sbom_validate_minimum_elements`, `security_categorize`, `slsa_generate`, `slsa_verify`, `swft_bundle`, `xacta_export` |
| knowledge_graph | 18 | `kg_add_alias`, `kg_compliance_build`, `kg_compliance_coverage`, `kg_compliance_crosswalk`, `kg_create_view`, `kg_cross_project_coverage`, `kg_enrich`, `kg_federated_search`, `kg_find_duplicates`, `kg_generate_ft_pairs`, `kg_graph_evolution`, `kg_merge_entities`, `kg_recent_changes`, `kg_resolve_ambiguous`, `kg_shared_entities`, `kg_stale_entities`, `kg_temporal_diff`, `kg_time_range` |
| builder | 15 | `agentic_fitness`, `code_quality_report`, `dev_profile_create`, `dev_profile_detect`, `dev_profile_get`, `dev_profile_resolve`, `format`, `generate_blueprint`, `generate_child_app`, `generate_code`, `lint`, `run_tests`, `runtime_feedback_collect`, `scaffold`, `write_tests` |
| marketplace | 15 | `asset_scan`, `check_compat`, `get_asset`, `list_assets`, `list_pending`, `openclaw_export`, `openclaw_import`, `openclaw_list_exports`, `openclaw_list_quarantine`, `openclaw_promote`, `openclaw_reject`, `review_asset`, `search_assets`, `sync_status`, `uninstall_asset` |
| llmops | 14 | `compress_context`, `cost_intelligence_anomalies`, `cost_intelligence_dashboard`, `cost_intelligence_recommend`, `llm_gateway_check`, `llm_gateway_stats`, `model_monitor_drift`, `model_monitor_health`, `prompt_registry_activate`, `prompt_registry_list`, `prompt_registry_register`, `proxy_key_issue`, `proxy_key_list`, `proxy_key_show` |
| registry | 13 | `absorption_candidates`, `cross_pollination_candidates`, `egress_monitor_evaluate`, `evaluate_capability`, `evolution_daemon_status`, `get_genome`, `list_children`, `list_propagations`, `list_staging`, `propagation_verify`, `register_child`, `sandbox_score`, `unevaluated_behaviors` |
| security_agentic | 13 | `ai_telemetry_summary`, `blueprint_verify`, `credential_broker_request`, `credential_broker_status`, `detect_behavioral_drift`, `egress_policy_resolve`, `evaluate_aggregation_rules`, `finding_enforce_reproduction`, `finding_replay`, `finding_verify_discrimination`, `run_atlas_red_team`, `score_agent_trust`, `validate_tool_chain` |
| research | 12 | `last30days__parallel_multi_source_social`, `research_create_session`, `research_get_challenges`, `research_get_dossier`, `research_get_forecasts`, `research_get_status`, `research_list_sessions`, `research_list_verticals`, `research_review_dossier`, `research_run_pipeline`, `research_run_stage`, `research_trigger_fitness` |
| nova | 11 | `ace_ensure_sme`, `ace_persona_query`, `council_query`, `nova_analyze_patterns`, `nova_evolve_skill`, `nova_generate_skill`, `nova_get_dispatch_config`, `nova_get_trust_score`, `nova_list_skill_queue`, `nova_record_trust_event`, `nova_trust_summary` |
| devsecops | 10 | `attestation_verify`, `devsecops_maturity_assess`, `devsecops_profile_create`, `devsecops_profile_get`, `network_segmentation_generate`, `pdp_config_generate`, `pipeline_security_generate`, `policy_generate`, `service_mesh_generate`, `zta_maturity_score` |
| integration | 10 | `build_traceability`, `configure_gitlab`, `configure_jira`, `configure_servicenow`, `export_reqif`, `review_approval`, `submit_approval`, `sync_gitlab`, `sync_jira`, `sync_servicenow` |
| mbse | 10 | `des_assess`, `detect_drift`, `import_reqif`, `import_xmi`, `mbse_generate_code`, `model_snapshot`, `sync_model`, `thread_coverage`, `trace_backward`, `trace_forward` |
| modernization | 10 | `analyze_legacy`, `assess_seven_r`, `check_compliance_bridge`, `create_migration_plan`, `extract_architecture`, `generate_docs`, `generate_migration_code`, `migrate_version`, `register_legacy_app`, `track_migration` |
| rag | 10 | `crag_benchmark_run`, `quality_feedback_run`, `query_classify`, `rag_chunk_info`, `rag_ingest`, `rag_providers`, `rag_reindex`, `rag_retention_migrate`, `rag_retrieval_history`, `rag_status` |
| innovation | 9 | `competitive_scan`, `detect_trends`, `generate_solution`, `introspect`, `run_pipeline`, `scan_web`, `score_signals`, `standards_check`, `triage_signals` |
| supply_chain | 9 | `add_vendor`, `assess_boundary_impact`, `assess_scrm`, `build_dependency_graph`, `generate_red_alternative`, `manage_isa`, `propagate_impact`, `register_ato_system`, `watch_passive_cve` |
| translation | 9 | `assemble_project`, `check_types`, `extract_source_ir`, `map_dependencies`, `map_features`, `translate_code`, `translate_tests`, `translate_unit`, `validate_translation` |
| migration | 8 | `mc_net_ai_assist`, `mc_net_build_parallel_timeline`, `mc_net_get_inventory`, `mc_net_ingest_csv`, `mc_net_ingest_netbox`, `mc_net_ingest_topology`, `mc_net_plan_protocol_migration`, `mc_net_recommend_hardware` |
| misc | 8 | `analyze_legacy_ui`, `framework_migrate`, `generate_claude_md`, `generate_profile_md`, `nlq_query`, `register_external_patterns`, `version_migrate`, `worktree_manage` |
| simulation | 8 | `compare_coas`, `create_scenario`, `generate_alternative_coa`, `generate_coas`, `manage_scenarios`, `run_monte_carlo`, `run_simulation`, `select_coa` |
| sre | 8 | `incident_create`, `incident_dashboard`, `incident_update`, `runbook_execute`, `runbook_register`, `slo_dashboard`, `slo_define`, `slo_measure` |
| finetune | 7 | `ft_hp_create`, `ft_hp_list`, `ft_hp_record`, `ft_hp_run_next`, `ft_hp_status`, `ft_pipeline_run`, `ft_quality_check` |
| autoresearch | 6 | `autoresearch_create`, `autoresearch_evaluate`, `autoresearch_health`, `autoresearch_loop`, `autoresearch_select`, `autoresearch_status` |
| requirements | 6 | `create_intake_session`, `extract_document`, `get_session_status`, `process_intake_turn`, `resume_intake_session`, `upload_document` |
| cloud | 5 | `cloud_mode_status`, `csp_changelog`, `csp_health_check`, `csp_monitor_scan`, `validate_region` |
| context | 5 | `fetch_docs`, `get_agent_context`, `get_icdev_metadata`, `get_project_context`, `list_sections` |
| dx | 5 | `companion_setup`, `detect_ai_tools`, `generate_instructions`, `generate_mcp_configs`, `translate_skills` |
| fathomdesk_news | 5 | `news_classify`, `news_db_migrate`, `news_ingest_once`, `news_reason`, `news_scenario_match` |
| infra | 5 | `pdc_analyze`, `pdc_export`, `pdc_validate`, `pipeline_generate`, `terraform_plan` |
| observability | 5 | `prov_export`, `prov_lineage`, `shap_analyze`, `trace_summary`, `xai_assess` |
| oracle | 5 | `oracle_kanban_bridge_gate`, `oracle_kanban_bridge_sync`, `oracle_lens_status`, `oracle_predictions_list`, `sio_run` |
| testing | 5 | `production_audit`, `production_remediate`, `run_e2e_tests`, `validate_claude_dir`, `validate_screenshot` |
| dic | 4 | `dic_chat`, `dic_generate`, `dic_ingest`, `dic_search` |
| gateway | 4 | `bind_user`, `gateway_status`, `list_bindings`, `revoke_binding` |
| installer | 4 | `generate_platform_artifacts`, `install_modules`, `list_compliance_postures`, `validate_module_registry` |
| intelligence | 4 | `bayesian_optimal_order`, `bayesian_score_pairs`, `bayesian_smart_encode`, `bayesian_teaching_dim` |
| redaction | 4 | `redaction_anonymize`, `redaction_detect`, `redaction_sanitize_proposal`, `redaction_scan_db` |
| workflow | 4 | `workflow_loop_create`, `workflow_loop_status`, `workflow_next_action`, `workflow_reconcile` |
| ace | 3 | `ace_abort`, `ace_launch`, `ace_status` |
| agent_detection | 3 | `agent_detect_check_rules`, `agent_detect_list_rules`, `agent_detect_scan_session` |
| agent_topology | 3 | `topology_airgap`, `topology_build`, `topology_spof` |
| docmod | 3 | `docmod_findings`, `docmod_redline`, `docmod_scan` |
| knowledge | 3 | `add_pattern`, `analyze_failure`, `get_recommendations` |
| maintenance | 3 | `check_vulnerabilities`, `remediate`, `run_maintenance_audit` |
| analyzers | 2 | `analyzer_capabilities`, `analyzer_dispatch` |
| compass | 2 | `compass_lcat_lookup`, `compass_staffing_summary` |
| core | 2 | `agent_status`, `task_dispatch` |
| foundry | 2 | `foundry_run`, `foundry_status` |
| integrity | 2 | `integrity_assess`, `integrity_list_assessments` |
| studio | 2 | `studio_init_db`, `studio_tool_catalog` |
| canvas | 1 | `canvas_kg_rebuild` |
| pulse | 1 | `writeguard_analyze` |
| **TOTAL** | **377** | |

---

## 7. What this makes actionable

No change is made here. These are the decisions the classification now permits,
in the order the evidence supports them.

1. **`probe_mcp_dispatch_tool` measures one of four surfaces and should say so.**
   It reads `studio_mcp_dispatch_audit` only. Reading `runtime_invocations`
   (`surface='mcp'`) alongside it would raise the observed count from 4 to 12 and,
   more importantly, would observe the surface that actually carries traffic. Until
   then its output is a claim about Studio workflow dispatch, not about MCP.

2. **`runtime_invocations` does not exist on the database `.mcp.json` points the
   MCP server at.** Migration 341 has not run against `data/icdev.db`. Every MCP
   invocation made through that configuration is silently unrecorded. This is a
   one-migration fix and it is the highest-value item here, because it is the
   difference between having the telemetry and only believing you do.

3. **The agent-bundle path records task ids where tool names belong.** 48 tools
   are reachable only through it, and `runtime_invocations.surface='agent'`
   currently holds 313 kanban task ids and 0 tool names. No probe can classify
   those 48 until the recorded `name` is the tool.

4. **`mcp_dispatch_tool: 467` in `args/liveness_gate.yaml` is correct for what
   the probe measures and wrong about the platform.** The genuinely unused count
   is 377. **Do not lower the budget on the strength of this report** — the probe
   cannot yet see the 90 tools that would justify it, and a budget lowered ahead
   of the measurement is a number, not a control. Fix (1) and (2) first; the
   budget can then follow evidence.

5. **The 21 external-only tools are classified, not declared.** `ctx-reach-02`
   set the bar: a decision doc named in the module docstring, zero production
   importers checked bidirectionally, and a gated test. Declaring these in
   `args/external_only_surfaces.yaml` would put them under
   `check_external_only_surfaces` and make the classification enforceable rather
   than merely written down. That is a separate task and should not be done in
   bulk — 21 entries added at once to satisfy a count would be the same move as
   raising a budget.

6. **377 tools need an owner decision, not a gate.** Each is live, importable
   code with no caller. The three honest outcomes per tool are: wire it to a
   consumer, declare it external-only with a reason, or delete the registration.
   The compliance cluster (42) should go first — an inert tool counted as
   satisfied evidence is the failure mode this platform already has a name for.

---

## 8. Reproducing this

```bash
# Declared surface (471) and handler resolution (471/471)
python -c "from tools.mcp.tool_registry import TOOL_REGISTRY; print(len(TOOL_REGISTRY))"
python -m pytest tests/mcp/test_registry_handler_coverage.py -q

# What the current probe sees (one table, one surface)
python tools/awareness/capability_consumption.py --class mcp_dispatch_tool --json

# The two dispatch-audit histories that disagree
python -c "from tools.db.storage import get_connection; c=get_connection(); \
print(c.execute('SELECT tool, decision, COUNT(*) FROM studio_mcp_dispatch_audit GROUP BY tool, decision').fetchall())"
sqlite3 data/icdev.db "SELECT tool, decision, COUNT(*) FROM studio_mcp_dispatch_audit GROUP BY tool, decision"

# The surface that observes everything — and the table that is missing on SQLite
python -c "from tools.db.storage import get_connection; c=get_connection(); \
print(c.execute(\"SELECT name, COUNT(*) FROM runtime_invocations WHERE surface='mcp' GROUP BY name\").fetchall())"
sqlite3 data/icdev.db "SELECT COUNT(*) FROM runtime_invocations"   # no such table

# The declarations the partition is built from
python -c "import yaml; g=yaml.safe_load(open('args/security_gates.yaml'))['mcp_workflow_tools']; \
print(len(g['allowed']), len(g['requires_approval']))"
python -c "import yaml; print(sorted(yaml.safe_load(open('args/agent_toolsets.yaml'))['bundles']))"
python -c "import yaml; print(sorted(yaml.safe_load(open('args/mcp_toolset_profiles.yaml'))['profiles']))"
python -c "from tools.mcp.cortex_server import CORTEX_TOOLS; print(len(CORTEX_TOOLS))"
```

The transcript measurement scans `%USERPROFILE%\.claude\projects\C--AI-ICDev\*.jsonl`
for `tool_use` blocks named `mcp__icdev-unified__*` — 316 files, 7 calls, 5 tools.
The transcripts are used rather than `hook_events` because `hook_events` persists
tool-input KEY NAMES and never the operand, so it cannot say which tool was
called; the same reason `tools/hooks/fire_rate_survey.py` is driven from the
transcripts.

---

## 9. Related

* `args/liveness_gate.yaml` — `mcp_dispatch_tool: 467` grandfather budget
* `args/capability_consumption.yaml` — class definition and known-inert cases
* `tools/awareness/capability_consumption.py::probe_mcp_dispatch_tool`
* [`ctx-reach-02`](../design/ctx-reach-02-cortex-client-external-only.md) — the external-only precedent and its obligations
* `docs/security/mcp-tool-authorization.md` — D261 per-tool RBAC
* `docs/features/dwo-durable-workflow-orchestration.md` — MCP-WF-001 and the dispatch audit
